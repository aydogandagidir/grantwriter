"""One-shot loader: download CORDIS Horizon projects, embed abstracts,
bulk-insert into `cordis_funded_projects`.

Usage (after `make dev` brings up Postgres):

    OPENAI_API_KEY=sk-... \\
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/bluedev \\
        poetry run python scripts/load_cordis.py --concurrency 10

Source dataset: https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip
~150 MB, ~35K projects across HORIZON + H2020. After filtering to the last
N years (default 3) we expect ~10K-14K rows. Embeddings cost ~$0.40-$2 at
text-embedding-3-large pricing.

The script is idempotent: re-running skips rows whose `cordis_id` already
exists. Use `--limit N` for smoke tests; `--input-zip PATH` to reuse a
downloaded zip; `--dry-run` to parse and filter without embedding/inserting.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pandas as pd
from tqdm import tqdm

# Make the FastAPI src tree importable when running this script from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))

from src.core.config import get_settings  # noqa: E402
from src.llm.embeddings import embed_batch  # noqa: E402

CORDIS_ZIP_URL = "https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip"
CORDIS_PROJECT_CSV_NAMES = ("project.csv", "csv/project.csv", "horizon/project.csv")

REQUIRED_COLUMNS = {"id", "title", "objective", "topics", "startDate"}
OPTIONAL_COLUMNS = {"acronym", "frameworkProgramme", "totalCost", "ecMaxContribution", "endDate"}

logger = logging.getLogger("load_cordis")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--concurrency", type=int, default=10, help="Parallel embedding requests (default: 10).")
    p.add_argument("--batch-size", type=int, default=100, help="Inputs per OpenAI request (default: 100).")
    p.add_argument("--limit", type=int, default=None, help="Max rows to process (smoke test).")
    p.add_argument("--years", type=int, default=3, help="Filter to projects with startDate within the last N years.")
    p.add_argument("--input-zip", type=Path, default=None, help="Path to a pre-downloaded CORDIS zip (skips download).")
    p.add_argument("--input-csv", type=Path, default=None, help="Path to project.csv (skips download and unzip).")
    p.add_argument("--dry-run", action="store_true", help="Parse and filter, but don't embed or insert.")
    p.add_argument("--database-url", type=str, default=None, help="Override DATABASE_URL.")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Data acquisition + parsing
# ---------------------------------------------------------------------------


async def download_zip(url: str, dest: Path) -> Path:
    logger.info("downloading %s → %s", url, dest)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            with dest.open("wb") as fh, tqdm(
                total=total, unit="B", unit_scale=True, desc="cordis.zip"
            ) as bar:
                async for chunk in response.aiter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
                    bar.update(len(chunk))
    return dest


def extract_project_csv(zip_path: Path, dest_dir: Path) -> Path:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        match = next(
            (n for n in names if Path(n).name.lower() == "project.csv"),
            None,
        )
        if match is None:
            raise FileNotFoundError(
                f"project.csv not found in {zip_path}. Archive contents: {names[:20]}"
            )
        zf.extract(match, dest_dir)
        return dest_dir / match


def read_cordis_csv(csv_path: Path) -> pd.DataFrame:
    """Read CORDIS project.csv, handling both `,` and `;` separators."""
    for sep in (";", ","):
        try:
            df = pd.read_csv(
                csv_path,
                sep=sep,
                dtype=str,
                keep_default_na=False,
                na_values=[""],
                engine="python",
                quoting=0,
                on_bad_lines="warn",
            )
        except Exception:  # noqa: BLE001 — try next sep
            continue
        if REQUIRED_COLUMNS.issubset(df.columns):
            logger.info("parsed %d rows from %s (sep=%r)", len(df), csv_path.name, sep)
            return df
    raise ValueError(
        f"could not parse {csv_path} with required columns {REQUIRED_COLUMNS}; "
        f"got cols={list(df.columns) if 'df' in locals() else 'none'}"
    )


def filter_recent(df: pd.DataFrame, years: int, today: date | None = None) -> pd.DataFrame:
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=365 * years)
    parsed = pd.to_datetime(df["startDate"], errors="coerce", utc=True)
    mask = (parsed.dt.date >= cutoff) & df["objective"].notna() & df["id"].notna()
    out = df[mask].copy()
    out["startDate_parsed"] = parsed[mask].dt.date
    if "endDate" in out.columns:
        out["endDate_parsed"] = pd.to_datetime(out["endDate"], errors="coerce", utc=True).dt.date
    else:
        out["endDate_parsed"] = None
    out = out[out["objective"].str.len() > 100]
    out = out.drop_duplicates(subset=["id"])
    logger.info("filtered to %d rows (startDate ≥ %s, abstract length > 100)", len(out), cutoff)
    return out.reset_index(drop=True)


def split_topics(value: str | float | None) -> list[str]:
    """CORDIS `topics` is `;`-separated; trim and drop empties."""
    if value is None or (isinstance(value, float) and math.isnan(value)) or value == "":
        return []
    parts = [t.strip() for t in str(value).split(";")]
    return [p for p in parts if p]


def parse_budget(row: pd.Series) -> float | None:
    for col in ("totalCost", "ecMaxContribution"):
        if col in row and row[col]:
            try:
                # CORDIS uses comma as decimal in some rows ("1.234.567,89")
                cleaned = str(row[col]).replace(".", "").replace(",", ".") if "," in str(row[col]) else str(row[col])
                return float(cleaned)
            except (TypeError, ValueError):
                continue
    return None


def normalize_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, raw in df.iterrows():
        rows.append(
            {
                "cordis_id": str(raw["id"]),
                "title": str(raw["title"])[:1000] if raw.get("title") else "",
                "acronym": (str(raw["acronym"])[:200] if raw.get("acronym") else None),
                "topic_ids": split_topics(raw.get("topics")),
                "programme": str(raw["frameworkProgramme"]) if raw.get("frameworkProgramme") else None,
                "budget_eur": parse_budget(raw),
                "start_date": raw.get("startDate_parsed"),
                "end_date": raw.get("endDate_parsed"),
                "abstract": str(raw["objective"]),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Embedding + insert
# ---------------------------------------------------------------------------


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


async def insert_rows(
    pool: asyncpg.Pool,
    rows: list[dict[str, Any]],
    embeddings: list[list[float]],
) -> int:
    if len(rows) != len(embeddings):
        raise ValueError(
            f"row/embedding length mismatch: {len(rows)} vs {len(embeddings)}"
        )
    records = [
        (
            r["cordis_id"],
            r["title"],
            r["acronym"],
            r["topic_ids"],
            r["programme"],
            r["budget_eur"],
            r["start_date"],
            r["end_date"],
            r["abstract"],
            vector_literal(emb),
        )
        for r, emb in zip(rows, embeddings, strict=True)
    ]

    sql = """
        insert into cordis_funded_projects
          (cordis_id, title, acronym, topic_ids, programme, budget_eur,
           start_date, end_date, abstract, abstract_embedding)
        values
          ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::halfvec(3072))
        on conflict (cordis_id) do nothing
    """
    inserted = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for record in records:
                result = await conn.execute(sql, *record)
                if result.endswith(" 1"):
                    inserted += 1
    return inserted


def chunked(seq: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def acquire_csv(args: argparse.Namespace, workdir: Path) -> Path:
    if args.input_csv:
        if not args.input_csv.exists():
            raise FileNotFoundError(args.input_csv)
        return args.input_csv
    zip_path = args.input_zip
    if zip_path is None:
        zip_path = workdir / "cordis.zip"
        await download_zip(CORDIS_ZIP_URL, zip_path)
    elif not zip_path.exists():
        raise FileNotFoundError(zip_path)
    return extract_project_csv(zip_path, workdir)


async def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    settings = get_settings()
    db_url = args.database_url or settings.database_url

    with tempfile.TemporaryDirectory(prefix="cordis-") as tmpdir:
        workdir = Path(tmpdir)
        csv_path = await acquire_csv(args, workdir)
        df = read_cordis_csv(csv_path)
        df = filter_recent(df, args.years)
        if args.limit is not None:
            df = df.head(args.limit)
            logger.info("limit applied: %d rows", len(df))

        rows = normalize_rows(df)
        if not rows:
            logger.warning("no rows to insert; exiting")
            return 0

        if args.dry_run:
            logger.info("dry-run: would embed and insert %d rows", len(rows))
            return 0

        if not settings.openai_api_key:
            logger.error("OPENAI_API_KEY is not set; aborting")
            return 2

        # Embed in chunks so a single failure doesn't lose all work and so
        # progress is visible at coarse intervals.
        super_chunk = 1000
        pool = await asyncpg.create_pool(dsn=db_url, min_size=2, max_size=5)
        if pool is None:
            raise RuntimeError("failed to create asyncpg pool")
        try:
            inserted_total = 0
            for chunk in tqdm(list(chunked(rows, super_chunk)), desc="chunks"):
                texts = [r["abstract"] for r in chunk]
                embeddings = await embed_batch(
                    texts,
                    batch_size=args.batch_size,
                    concurrency=args.concurrency,
                )
                inserted_total += await insert_rows(pool, chunk, embeddings)
                logger.info(
                    "inserted %d (cumulative %d / %d)",
                    len(chunk),
                    inserted_total,
                    len(rows),
                )
        finally:
            await pool.close()

        logger.info(
            "DONE — processed=%d inserted=%d skipped(conflict)=%d",
            len(rows),
            inserted_total,
            len(rows) - inserted_total,
        )
        return 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
