-- 006 — CORDIS funded projects corpus (for distinctiveness scoring)
-- Source: docs/03-database-schema.md §2.4 (with deviations D1, D2 documented in
-- the implementation plan).
--
-- Deviations from spec:
--   D1: abstract_embedding is halfvec(3072), not vector(3072). pgvector's HNSW
--       caps `vector` at 2000 dims; halfvec extends to 4000 with negligible
--       cosine-ranking impact for this use case.
--   D2: topic_ids is text[], not topic_id text. CORDIS projects can be funded
--       under multiple topics; we preserve the full set so distinctiveness
--       comparisons in cross-cutting topics aren't silently dropped.

create table if not exists cordis_funded_projects (
  id uuid primary key default gen_random_uuid(),
  cordis_id text unique not null,
  title text not null,
  acronym text,
  topic_ids text[] not null default '{}',
  programme text,                          -- 'HORIZON', 'H2020', 'FP7'
  budget_eur numeric,
  start_date date,
  end_date date,
  abstract text,
  abstract_embedding halfvec(3072),
  metadata jsonb default '{}'::jsonb,
  scraped_at timestamptz not null default now()
);

create index if not exists idx_cordis_topics on cordis_funded_projects using gin (topic_ids);
create index if not exists idx_cordis_start_date on cordis_funded_projects (start_date desc);
create index if not exists idx_cordis_embedding
  on cordis_funded_projects
  using hnsw (abstract_embedding halfvec_cosine_ops);
