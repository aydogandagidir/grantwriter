-- Migration 007: RAG corpus
-- Source of truth: docs/03-database-schema.md §2.4
--
-- All embedding columns are vector(3072) (text-embedding-3-large native).
-- HNSW indexes use halfvec cast — see rationale in migration 004.

create table if not exists successful_proposals_corpus (
  id uuid primary key default gen_random_uuid(),
  programme_id text not null references programmes(id),
  source text not null,
  external_id text,
  title text,
  topic_id text,
  funded_year int,
  budget_eur numeric,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists successful_proposal_chunks (
  id uuid primary key default gen_random_uuid(),
  corpus_id uuid not null references successful_proposals_corpus(id) on delete cascade,
  section text not null,
  chunk_index int not null,
  content text not null,
  embedding vector(3072),
  metadata jsonb default '{}'::jsonb
);
create index if not exists idx_corpus_chunks_section
  on successful_proposal_chunks(section);
create index if not exists idx_corpus_chunks_embedding
  on successful_proposal_chunks
  using hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);

create table if not exists funder_guidelines (
  id uuid primary key default gen_random_uuid(),
  programme_id text references programmes(id),
  document_type text not null,
  title text not null,
  content text not null,
  source_url text,
  effective_date date,
  embedding vector(3072),
  created_at timestamptz not null default now()
);
create index if not exists idx_guidelines_embedding
  on funder_guidelines
  using hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);

-- cordis_funded_projects: backs the distinctiveness scorer (docs/04 §4).
-- Two deviations from the docs/03 §2.4 spec, both load-bearing:
--   D1: abstract_embedding is halfvec(3072) directly. pgvector HNSW caps the
--       `vector` type at 2000 dims; halfvec extends to 4000 with negligible
--       cosine-ranking impact. Using halfvec at the column level (not as an
--       index expression) lets the scorer query without a per-row cast.
--   D2: topic_ids is text[] (not text). CORDIS projects are funded under one
--       or more topics; preserving the full set lets the scorer match any of
--       them via `WHERE $topic = ANY(topic_ids)` + GIN index. The single-
--       column `topic_id` would silently lose the multi-topic projects.

create table if not exists cordis_funded_projects (
  id uuid primary key default gen_random_uuid(),
  cordis_id text unique not null,
  title text not null,
  acronym text,
  topic_ids text[] not null default '{}',
  programme text,
  budget_eur numeric,
  start_date date,
  end_date date,
  abstract text,
  abstract_embedding halfvec(3072),
  metadata jsonb default '{}'::jsonb,
  scraped_at timestamptz not null default now()
);
create index if not exists idx_cordis_topics
  on cordis_funded_projects using gin (topic_ids);
create index if not exists idx_cordis_start_date
  on cordis_funded_projects (start_date desc);
create index if not exists idx_cordis_embedding
  on cordis_funded_projects
  using hnsw (abstract_embedding halfvec_cosine_ops);
