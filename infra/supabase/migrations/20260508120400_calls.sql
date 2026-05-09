-- Migration 004: Calls + RAG-ready call_chunks
-- Source of truth: docs/03-database-schema.md §2.2
--
-- Note on the embedding HNSW index:
--   text-embedding-3-large produces 3072-dim vectors. pgvector's HNSW
--   indexes only support up to 2,000 dims for the `vector` type, but
--   support up to 4,000 dims for `halfvec` (half-precision floats).
--   We store as `vector(3072)` (no precision loss) and create the HNSW
--   index over an inline `halfvec` cast so query-time recall stays high
--   while the index fits within the dimensional limit.

create table if not exists calls (
  id uuid primary key default gen_random_uuid(),
  programme_id text not null references programmes(id),
  source text not null check (source in (
    'eu_ft_portal','nlnet','cascade','tubitak','kosgeb','manual'
  )),
  external_id text not null,
  title text not null,
  call_text text,
  call_url text,
  call_pdf_url text,
  deadline date,
  budget_total_eur numeric(15,2),
  budget_per_project_min_eur numeric(15,2),
  budget_per_project_max_eur numeric(15,2),
  trl_min int,
  trl_max int,
  topic_keywords text[],
  eligibility_summary jsonb,
  raw_metadata jsonb not null default '{}'::jsonb,
  scraped_at timestamptz not null default now(),
  status text not null default 'open'
    check (status in ('open','closing_soon','closed','draft')),
  language text not null,
  unique(source, external_id)
);
create index if not exists idx_calls_programme on calls(programme_id);
create index if not exists idx_calls_deadline on calls(deadline);
create index if not exists idx_calls_status on calls(status) where status = 'open';
create index if not exists idx_calls_topic on calls using gin(topic_keywords);
create index if not exists idx_calls_text_trgm on calls using gin(title gin_trgm_ops);

create table if not exists call_chunks (
  id uuid primary key default gen_random_uuid(),
  call_id uuid not null references calls(id) on delete cascade,
  chunk_index int not null,
  section text,
  content text not null,
  embedding vector(3072),
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists idx_call_chunks_call on call_chunks(call_id);
create index if not exists idx_call_chunks_embedding
  on call_chunks
  using hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);
