-- Migration: funder-guideline chunked corpus (Faz 3).
--
-- Migration 007 (`20260508120700_rag_corpus.sql`) created
-- `funder_guidelines` as a flat single-row-per-document table with one
-- embedding for the entire guideline. That's useless for RAG retrieval:
-- a 100-page Horizon Europe work-programme PDF doesn't fit in one vector,
-- and chunk-level retrieval is the whole point.
--
-- This migration:
--   1. Extends `funder_guidelines` with the columns the ingestion
--      pipeline actually writes (call_id, file_hash, page_count, etc.)
--      and the idempotency key (call_id, file_hash).
--   2. Introduces `funder_guideline_chunks` parallel to
--      `successful_proposal_chunks` — same shape so the retriever can
--      share the cosine-similarity machinery from
--      `src/rag/retriever.py`.
--   3. Drops the now-stale per-document HNSW index on
--      `funder_guidelines.embedding`. The new index lives on the chunks
--      table.
--   4. Leaves the legacy `content` and `embedding` columns on
--      `funder_guidelines` in place (NULL-able, no consumer) for ABI
--      safety in case any out-of-tree tooling read them; the ingestor
--      stops populating them.
--
-- RLS: guidelines + chunks are world-readable to authenticated users
-- (the funder published them — same logic as `calls`). Writes go through
-- the service-role (Celery worker), which bypasses RLS.

-- ── funder_guidelines: new columns ────────────────────────────────────

alter table funder_guidelines
  add column if not exists call_id uuid references calls(id) on delete cascade,
  add column if not exists file_hash text,
  add column if not exists page_count int,
  add column if not exists language text,
  add column if not exists byte_size bigint,
  add column if not exists ingested_at timestamptz not null default now(),
  add column if not exists metadata jsonb not null default '{}'::jsonb;

-- Idempotency: re-running ingestion with an unchanged PDF must short-
-- circuit. Partial index because call_id can be NULL (a guideline can
-- live at the programme level without a specific call), in which case
-- the uniqueness is enforced by source_url + file_hash via a different
-- partial.

create unique index if not exists uq_funder_guidelines_call_hash
  on funder_guidelines (call_id, file_hash)
  where call_id is not null and file_hash is not null;

create unique index if not exists uq_funder_guidelines_url_hash
  on funder_guidelines (source_url, file_hash)
  where call_id is null and file_hash is not null;

create index if not exists idx_funder_guidelines_call on funder_guidelines(call_id);
create index if not exists idx_funder_guidelines_programme on funder_guidelines(programme_id);
create index if not exists idx_funder_guidelines_doctype on funder_guidelines(document_type);

-- Replace the per-doc embedding index with a chunk-level one. We keep
-- the column itself in case future code revives the per-doc summary
-- embedding (cheap retrieval pre-filter), but the index is stale.

drop index if exists idx_guidelines_embedding;

-- ── funder_guideline_chunks ───────────────────────────────────────────

create table if not exists funder_guideline_chunks (
  id uuid primary key default gen_random_uuid(),
  guideline_id uuid not null references funder_guidelines(id) on delete cascade,
  section text not null,
  chunk_index int not null,
  content text not null,
  embedding vector(3072),
  metadata jsonb not null default '{}'::jsonb,
  token_count int,
  created_at timestamptz not null default now()
);

-- (guideline_id, section, chunk_index) is unique per guideline. Lets us
-- upsert chunks during re-ingest without leaving stragglers.
create unique index if not exists uq_funder_guideline_chunks_index
  on funder_guideline_chunks (guideline_id, section, chunk_index);

create index if not exists idx_funder_guideline_chunks_guideline
  on funder_guideline_chunks(guideline_id);

create index if not exists idx_funder_guideline_chunks_section
  on funder_guideline_chunks(section);

-- HNSW index on halfvec cast — same pattern as
-- successful_proposal_chunks (S1.D2.T1 workaround for pgvector's
-- 2000-dim cap on plain vectors).
create index if not exists idx_funder_guideline_chunks_embedding
  on funder_guideline_chunks
  using hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);

-- ── RLS ───────────────────────────────────────────────────────────────
-- Both tables hold funder-published material — world-readable to any
-- authenticated user inside the platform. Service-role (Celery worker,
-- migrations) bypasses RLS for writes.

alter table funder_guidelines enable row level security;
alter table funder_guideline_chunks enable row level security;

drop policy if exists funder_guidelines_authenticated_read on funder_guidelines;
create policy funder_guidelines_authenticated_read
  on funder_guidelines
  for select
  using (auth.uid() is not null);

drop policy if exists funder_guideline_chunks_authenticated_read on funder_guideline_chunks;
create policy funder_guideline_chunks_authenticated_read
  on funder_guideline_chunks
  for select
  using (auth.uid() is not null);

-- ── Comments ──────────────────────────────────────────────────────────

comment on table funder_guidelines is
  'Parent doc for one funder-published guideline (work programme, '
  'application form, terms-of-reference, etc.). One row per (call_id, '
  'file_hash). Chunks live in funder_guideline_chunks.';

comment on table funder_guideline_chunks is
  'Chunked, embedded body of a funder guideline. Consumed by '
  'CorpusRetriever.retrieve_guideline() to give writer agents '
  '(ExcellenceWriter, ImpactWriter, ImplementationWriter) the actual '
  'evaluation criteria + scope text rather than relying on the model''s '
  'pretrained knowledge of the funder.';

comment on column funder_guidelines.file_hash is
  'sha256 of the source PDF bytes. Lets the ingestor short-circuit when '
  'the funder re-publishes an unchanged file.';

comment on column funder_guidelines.call_id is
  'Optional FK back to the call this guideline belongs to. NULL for '
  'programme-level docs (e.g. the Horizon Europe Programme Guide) that '
  'apply to every call under a programme.';
