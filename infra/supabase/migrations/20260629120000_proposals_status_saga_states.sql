-- Migration: allow the saga's terminal proposal statuses.
--
-- BUG (surfaced by tests/integration/test_saga_persistence_db.py):
-- DraftGenerator persists three statuses the original
-- proposals_status_check (migration 20260508120500) never allowed —
--   * draft_complete_with_issues  (Hallucination Hunter blocked export)
--   * failed_recoverable          (a writer / hunter step failed)
--   * failed                      (unrecoverable error)
-- See the DraftStatus literal in src/orchestrator/draft_generator.py.
--
-- Because every existing saga test used an AsyncMock connection, the
-- CHECK never fired in CI. Against a real Postgres, ANY blocking-hunter
-- or failed saga run raised CheckViolationError at _update_status — i.e.
-- the saga crashes in production the moment the hunter recommends
-- block_export or any step fails. This migration extends the constraint
-- to cover the saga-managed terminal states.
--
-- Idempotent: drops the existing constraint by name (if present) and
-- recreates it with the full status vocabulary.

alter table proposals drop constraint if exists proposals_status_check;

alter table proposals
  add constraint proposals_status_check check (
    status in (
      'draft',
      'brief_complete',
      'generating',
      'draft_complete',
      'draft_complete_with_issues',
      'failed_recoverable',
      'failed',
      'in_review',
      'validated',
      'exported',
      'submitted',
      'funded',
      'rejected',
      'archived'
    )
  );
