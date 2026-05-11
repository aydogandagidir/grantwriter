# Sprint 3 Known Issues

Tracked items inherited from Sprint 3. Each entry maps to a GitHub issue (opened during closure) and a Sprint 4 backlog slot.

---

## TICKET-001 — `test_llm_rerank_reorders_candidates` non-determinism

**Surface:** `apps/api/tests/rag/test_retriever.py::test_llm_rerank_reorders_candidates`
**Marked:** `@pytest.mark.flaky_pre_s3` (CI skips via `-m "not flaky_pre_s3"`)
**First seen:** S2.D6 (test was added) — not introduced by Sprint 3

### Symptom
On a Postgres+pgvector instance the test executes `retrieve()` twice with the same query and the same seeded corpus. The first call's returned ANN order is used to compute a reversed `ranked_ids` payload; the second call's reranked output is then asserted equal to that reversed list (capped at top_k).

Reality: the second `retrieve()` returns a slightly different ANN ordering. Pgvector ties on cosine distance between near-identical sentence embeddings, and tie-break is implementation-defined (heap order). The canned LLM verdict computed from call #1's order no longer maps cleanly onto call #2's candidate set, so the rerank lookup builds a different `top_k` slice.

### Repro

```bash
cd apps/api
poetry run pytest tests/rag/test_retriever.py::test_llm_rerank_reorders_candidates
# Fails 3/3 runs against bluedev_test
```

### Root-cause hypotheses (ranked)

1. **Tied cosine distances** — fixture's 3 proposals have nearly identical excellence sections; pgvector returns ties in non-deterministic order. **Most likely.**
2. **Hash collision in the deterministic embedder** — `DeterministicEmbedder` hashes content to a unit vector; two distinct strings might land at exactly the same vector, making cosine distance 1.0 across pairs. Unlikely but possible.
3. **Retriever pulls candidates in two different code paths** — code review says no, but worth a second look.

### Proposed fix (Sprint 4)

- Replace the deterministic embedder with a fixed Numpy seed + distinct vectors per proposal (no ties).
- OR: add a deterministic tiebreaker (`ORDER BY similarity DESC, id ASC`) to `_fetch_candidates` so ANN order is reproducible across calls.

### Severity
**P3 — low.** Code under test is correct (`_llm_rerank` honours `ranked_ids` properly); only the test fixture is fragile. Production behaviour unaffected.

---

## TICKET-002 — `test_distinctiveness_integration.py` stale-schema flake

**Surface:** All 5 cases in `apps/api/tests/compliance/test_distinctiveness_integration.py`
**Marked:** `@pytest.mark.integration` + `@pytest.mark.flaky_pre_s3`
**First seen:** S2.D7 (suite was added)

### Symptom
The `live_db_pool` fixture in `tests/conftest.py` opens a real Postgres connection (default DSN: `postgresql://postgres:postgres@localhost:5432/bluedev`) and applies every migration in lexical order. On a developer laptop where the `bluedev` database has been mutated by other suites (different column sets, dropped tables), one of the later migrations references `programme_id` on a table whose schema has drifted — boom, `UndefinedColumnError`.

CI's fresh `bluedev_test` database is fine; the symptom is laptop-only state.

### Repro

```bash
docker exec bluedev-postgres psql -U postgres -d bluedev -c "DROP TABLE rag_corpus CASCADE;"
poetry run pytest tests/compliance/test_distinctiveness_integration.py
# ERROR: column "programme_id" does not exist
```

### Proposed fix (Sprint 4)

- `live_db_pool` should target a **dedicated** `bluedev_test_integration` database (drop + create each session) so it never inherits state from `bluedev`.
- OR: detect the drift and skip with a clear message ("run `bash scripts/apply_migrations.sh --reset` first").

### Severity
**P3 — low.** Production isolation is unchanged; the suite still runs cleanly on CI. Only impacts developer ergonomics.

---

## TICKET-003 — Sentry / Logtail DSNs not configured in prod

**Surface:** `apps/api/src/core/observability.py` — `init_observability()` returns `(False, "SENTRY_DSN not configured")`
**Severity:** **P2 — must land before Sprint 4 pilot launch.**

### What's done
- Lazy-init pattern with PII scrubber (`scrub_event`, JWT + Anthropic/OpenAI key regex)
- `_LogtailScrubFilter` for log records
- `tests/core/test_observability.py` covers the scrubber + init noop paths

### What's missing
- Real `SENTRY_DSN` + `LOGTAIL_TOKEN` values in Railway production secrets
- Source tags + release tracking (`SENTRY_RELEASE=git-sha` injection at deploy time)
- PostHog `NEXT_PUBLIC_POSTHOG_KEY` for the frontend (not yet built — Sprint 4)

### Action (Sprint 4 Day 16)

1. Provision Sentry org `bluedev` + project `grantwriter-api`
2. Provision Logtail source `grantwriter-api-production`
3. Inject DSN/token into Railway as `SENTRY_DSN` + `LOGTAIL_TOKEN` secrets
4. Smoke test: trigger a deliberate exception from `/health/sentry-test` → confirm event lands in Sentry + scrubbed of BYOK key shapes

---

## Out-of-scope items (Faz 2 backlog, not Sprint 4)

- **Resend delivery + bounce webhook** — Mailbox provider reputation tracking. Reopen when we see > 5% bounce rate or need automated suppression lists.
- **Comments WebSocket / real-time presence** — Polling refresh works for pilot scale. Real-time is a nice-to-have once user base > 50.
- **Stripe rejoining the matrix** — User decision was Iyzico-only for Phase 1; revisit at international rollout.
- **Comments edit/delete audit codes** — Currently undocumented in the audit stream (over-instrumentation tradeoff). Add if a customer needs the trail.
- **Saga auto-snapshot on generate complete** — Manual snapshot is the only path today; auto-snapshot is a 1-commit follow-up.
