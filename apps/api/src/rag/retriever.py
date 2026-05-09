"""CorpusRetriever — pgvector HNSW search + LLM re-rank.

Per docs/04 §2.4–2.5:

  1. Embed the query.
  2. pgvector HNSW search filtered by programme + section → ``candidate_pool``
     candidates (default 20). The HNSW index on the corpus chunks is built
     over ``(embedding::halfvec(3072)) halfvec_cosine_ops`` (S1.D2.T1
     workaround for pgvector's 2 000-dim HNSW cap on plain vectors), so
     queries cast to ``halfvec(3072)`` to actually use the index.
  3. Optionally re-rank the candidates with a Sonnet-class LLM via the
     :class:`~src.llm.router.LLMRouter` (task=``rerank``). If the router
     is not provided, return the top-k by cosine score directly.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

import asyncpg

from src.llm.base import LLMMessage, LLMRequest
from src.llm.router import LLMRouter
from src.rag.base import Embedder, RetrievedChunk
from src.rag.corpus_manager import _vector_literal

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
DEFAULT_CANDIDATE_POOL = 20

_RERANK_SYSTEM = """\
You rank candidate text chunks from successful past grant proposals by their
relevance to the user's project query. Return ONLY a JSON object of the form
{"ranked_ids": ["<chunk_id_1>", "<chunk_id_2>", ...]} where ranked_ids is the
candidate ids in best-to-worst order. Do not include chunks not in the input.
Do not include prose, code fences, or commentary.
"""


class CorpusRetriever:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        embedder: Embedder,
        router: LLMRouter | None = None,
        tenant_id: UUID | None = None,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._router = router
        # Re-rank requests need a tenant for cost logging; production
        # always passes one, tests skip re-rank or pass a synthetic uuid.
        self._tenant_id = tenant_id

    async def retrieve(
        self,
        query: str,
        *,
        programme_id: str,
        section: str,
        top_k: int = DEFAULT_TOP_K,
        candidate_pool: int = DEFAULT_CANDIDATE_POOL,
        rerank: bool = True,
    ) -> list[RetrievedChunk]:
        """Embed → ANN search → optional LLM re-rank → top-k."""

        if top_k <= 0:
            return []
        candidate_pool = max(candidate_pool, top_k)

        embedding = await self._embedder.embed(query)
        candidates = await self._fetch_candidates(
            embedding=embedding,
            programme_id=programme_id,
            section=section,
            limit=candidate_pool,
        )
        if not candidates:
            return []

        if not rerank or self._router is None or len(candidates) <= top_k:
            return candidates[:top_k]

        try:
            return await self._llm_rerank(query, candidates, top_k)
        except Exception:
            logger.exception("rerank_failed_falling_back_to_ann_order")
            return candidates[:top_k]

    async def _fetch_candidates(
        self,
        *,
        embedding: list[float],
        programme_id: str,
        section: str,
        limit: int,
    ) -> list[RetrievedChunk]:
        vec_literal = _vector_literal(embedding)
        query = """
            SELECT
              spc.id,
              spc.corpus_id,
              spc.section,
              spc.content,
              spc.metadata,
              sp.title,
              sp.topic_id,
              1 - (spc.embedding::halfvec(3072) <=> $1::halfvec(3072)) AS score
            FROM successful_proposal_chunks spc
            JOIN successful_proposals_corpus sp ON sp.id = spc.corpus_id
            WHERE sp.programme_id = $2
              AND spc.section = $3
            ORDER BY spc.embedding::halfvec(3072) <=> $1::halfvec(3072)
            LIMIT $4
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, vec_literal, programme_id, section, limit)

        out: list[RetrievedChunk] = []
        for row in rows:
            raw_meta = row["metadata"]
            meta: dict[str, Any]
            if isinstance(raw_meta, str):
                meta = json.loads(raw_meta) if raw_meta else {}
            elif isinstance(raw_meta, dict):
                meta = raw_meta
            else:
                meta = {}
            out.append(
                RetrievedChunk(
                    id=row["id"],
                    corpus_id=row["corpus_id"],
                    section=row["section"],
                    content=row["content"],
                    score=float(row["score"]),
                    title=row["title"],
                    topic_id=row["topic_id"],
                    metadata=meta,
                )
            )
        return out

    async def _llm_rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        if self._router is None:  # narrow for type-checker
            return candidates[:top_k]
        if self._tenant_id is None:
            logger.warning(
                "rerank_skipped_no_tenant",
                extra={"candidate_count": len(candidates)},
            )
            return candidates[:top_k]

        formatted = "\n\n".join(
            f"id: {c.id}\nsection: {c.section}\ntitle: {c.title or '-'}\n"
            f"score: {c.score:.4f}\ncontent: {c.content[:600].strip()}"
            for c in candidates
        )
        request = LLMRequest(
            task="rerank",
            tenant_id=self._tenant_id,
            system=_RERANK_SYSTEM,
            messages=[
                LLMMessage(
                    role="user",
                    content=f"Query: {query}\n\nCandidates:\n{formatted}",
                )
            ],
            temperature=0.0,
            max_tokens=512,
        )
        response = await self._router.complete(request)
        ordered_ids = _parse_ranked_ids(response.text)
        if not ordered_ids:
            return candidates[:top_k]
        by_id = {str(c.id): c for c in candidates}
        ranked: list[RetrievedChunk] = []
        for cid in ordered_ids:
            chunk = by_id.get(cid)
            if chunk and chunk not in ranked:
                ranked.append(chunk)
                if len(ranked) >= top_k:
                    break
        # Backfill any survivors the LLM dropped.
        if len(ranked) < top_k:
            for c in candidates:
                if c not in ranked:
                    ranked.append(c)
                    if len(ranked) >= top_k:
                        break
        return ranked


_RANKED_IDS_RE = re.compile(r'"ranked_ids"\s*:\s*\[([^\]]*)\]')


def _parse_ranked_ids(text: str) -> list[str]:
    """Extract ``ranked_ids`` from the LLM response, lenient on prose."""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[: -len("```")]
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            ids = data.get("ranked_ids")
            if isinstance(ids, list):
                return [str(x).strip() for x in ids if x]
    except json.JSONDecodeError:
        pass

    # Fallback: regex scan for the array, in case the LLM wrapped extra prose.
    match = _RANKED_IDS_RE.search(text)
    if not match:
        return []
    raw = match.group(1)
    items = [s.strip().strip('"').strip("'") for s in raw.split(",")]
    return [s for s in items if s]


__all__ = ["CorpusRetriever", "DEFAULT_CANDIDATE_POOL", "DEFAULT_TOP_K"]
