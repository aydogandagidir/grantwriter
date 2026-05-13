-- Migration: extend `calls` to support automated scraping + idea matching.
-- Plan: docs/plans (Faz 0 → Faz 2)
--
-- Adds:
--   * agency_id  — granular sub-programme inside one funder/source
--                  (e.g. `nlnet_ngi0_core`, `tubitak_1507`, `he_ria_cl4`).
--   * embedding (vector 3072, HNSW halfvec) — call-level semantic vector
--     for the Faz 2 idea-matcher hot path.
--   * sectors / geo_scope / eligibility_tags — array facets the call
--     browser filters on.
--   * opening_at / application_form_url / work_programme_pdf_url
--   * historical_acceptance_rate / partner_consortium_required
--   * source_url_canonical / last_seen_at — dedup + freshness.
-- Extends `source` CHECK to include `eurostars`, `schumann`.
-- Creates `scraper_runs` table for Faz 1 run history + admin dashboard.

-- ── calls: new columns ──────────────────────────────────────────────────

alter table calls
  add column if not exists agency_id text,
  add column if not exists embedding vector(3072),
  add column if not exists sectors text[] not null default array[]::text[],
  add column if not exists geo_scope text[] not null default array[]::text[],
  add column if not exists eligibility_tags text[] not null default array[]::text[],
  add column if not exists opening_at date,
  add column if not exists application_form_url text,
  add column if not exists work_programme_pdf_url text,
  add column if not exists historical_acceptance_rate numeric(5,4),
  add column if not exists partner_consortium_required boolean,
  add column if not exists source_url_canonical text,
  add column if not exists last_seen_at timestamptz not null default now(),
  add column if not exists scope_summary text,
  add column if not exists funding_rate_pct int;

-- ── source CHECK: extend to new funders ────────────────────────────────
-- Drop & recreate the CHECK rather than ADD because Postgres doesn't
-- support "extend an existing CHECK"; safe because we own the constraint.

alter table calls drop constraint if exists calls_source_check;
alter table calls add constraint calls_source_check check (
  source in (
    'eu_ft_portal','nlnet','cascade','tubitak','kosgeb',
    'eurostars','schumann','manual'
  )
);

-- ── Indexes ────────────────────────────────────────────────────────────

-- Call-level semantic similarity for idea matching (Faz 2). halfvec cast
-- keeps the 3072-dim vector within pgvector's HNSW dimensional cap, same
-- trick the call_chunks index uses.
create index if not exists idx_calls_embedding
  on calls
  using hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);

create index if not exists idx_calls_sectors on calls using gin(sectors);
create index if not exists idx_calls_geo_scope on calls using gin(geo_scope);
create index if not exists idx_calls_eligibility_tags on calls using gin(eligibility_tags);
create index if not exists idx_calls_agency on calls(agency_id) where agency_id is not null;
create index if not exists idx_calls_opening on calls(opening_at);
create index if not exists idx_calls_last_seen on calls(last_seen_at);

-- ── scraper_runs: per-invocation history ───────────────────────────────

create table if not exists scraper_runs (
  id uuid primary key default gen_random_uuid(),
  source text not null check (source in (
    'eu_ft_portal','nlnet','cascade','tubitak','kosgeb',
    'eurostars','schumann','manual'
  )),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  calls_discovered int not null default 0,
  calls_persisted int not null default 0,
  calls_updated int not null default 0,
  calls_failed int not null default 0,
  duration_seconds numeric(10,3),
  errors jsonb not null default '[]'::jsonb,
  triggered_by text not null default 'beat'
    check (triggered_by in ('beat','manual','test')),
  triggered_by_user_id uuid references users(id),
  created_at timestamptz not null default now()
);
create index if not exists idx_scraper_runs_source on scraper_runs(source);
create index if not exists idx_scraper_runs_started on scraper_runs(started_at desc);

-- ── RLS: scraper_runs is admin-only ────────────────────────────────────
-- Calls themselves remain world-readable to authenticated users (funder-
-- published data). scraper_runs exposes operational data and stays
-- scoped to platform admins via a session-set claim.

alter table scraper_runs enable row level security;

-- DROP-then-CREATE makes the policy block idempotent. Without this,
-- re-running migrations (the integration test fixture replays every
-- *.sql per pool) raises duplicate-object on the second pass and
-- aborts the fixture before any test gets to run.
drop policy if exists scraper_runs_admin_select on scraper_runs;
create policy scraper_runs_admin_select
  on scraper_runs
  for select
  using (
    coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'is_platform_admin', 'false')::boolean
  );

drop policy if exists scraper_runs_admin_insert on scraper_runs;
create policy scraper_runs_admin_insert
  on scraper_runs
  for insert
  with check (
    coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'is_platform_admin', 'false')::boolean
  );

drop policy if exists scraper_runs_admin_update on scraper_runs;
create policy scraper_runs_admin_update
  on scraper_runs
  for update
  using (
    coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'is_platform_admin', 'false')::boolean
  );

-- Service-role (Celery worker, backend) bypasses RLS — that's the row
-- writer in practice. The policies above gate the admin dashboard read.

comment on table scraper_runs is
  'Per-invocation summary written by src/scrapers/runner.py. Used by '
  'the admin scraper-health dashboard and Celery beat monitoring.';
comment on column calls.embedding is
  'Call-level semantic embedding (text-embedding-3-large 3072d). Filled '
  'by the embed_open_calls Celery task; consumed by IdeaMatcher in Faz 2.';
comment on column calls.agency_id is
  'Granular sub-programme handle inside one funder/source. NULL when the '
  'source maps 1:1 to a programme.';
comment on column calls.source_url_canonical is
  'Cleaned canonical URL (no UTM/tracking, sorted query). Used for cross-'
  'source dedup; falls back to call_url at write time.';
