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

create table if not exists cordis_funded_projects (
  id uuid primary key default gen_random_uuid(),
  cordis_id text unique not null,
  title text not null,
  acronym text,
  topic_id text,
  programme text,
  budget_eur numeric,
  start_date date,
  end_date date,
  abstract text,
  abstract_embedding vector(3072),
  metadata jsonb default '{}'::jsonb,
  scraped_at timestamptz not null default now()
);
create index if not exists idx_cordis_topic on cordis_funded_projects(topic_id);
create index if not exists idx_cordis_embedding
  on cordis_funded_projects
  using hnsw ((abstract_embedding::halfvec(3072)) halfvec_cosine_ops);
