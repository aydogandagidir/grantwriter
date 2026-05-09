-- Migration 006: Citations + version snapshots + comments
-- Source of truth: docs/03-database-schema.md §2.3

create table if not exists citations (
  id uuid primary key default gen_random_uuid(),
  proposal_id uuid not null references proposals(id) on delete cascade,
  raw_text text not null,
  doi text,
  title text,
  authors text[],
  year int,
  journal text,
  url text,
  status text not null default 'unverified' check (status in (
    'unverified', 'verifying', 'verified', 'fabricated', 'partial_match'
  )),
  verification_source text check (verification_source in (
    'crossref','openalex','manual','doi_direct'
  )),
  verified_at timestamptz,
  match_score numeric(4,3),
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists idx_citations_proposal on citations(proposal_id);
create index if not exists idx_citations_status on citations(status);
create index if not exists idx_citations_doi on citations(doi);

create table if not exists proposal_versions (
  id uuid primary key default gen_random_uuid(),
  proposal_id uuid not null references proposals(id) on delete cascade,
  version_number int not null,
  draft_snapshot jsonb not null,
  created_by uuid references public.users(id),
  comment text,
  created_at timestamptz not null default now(),
  unique(proposal_id, version_number)
);
create index if not exists idx_versions_proposal
  on proposal_versions(proposal_id, version_number desc);

create table if not exists proposal_comments (
  id uuid primary key default gen_random_uuid(),
  proposal_id uuid not null references proposals(id) on delete cascade,
  author_id uuid not null references public.users(id),
  section text,
  anchor text,
  content text not null,
  resolved boolean default false,
  parent_id uuid references proposal_comments(id),
  created_at timestamptz not null default now()
);
create index if not exists idx_comments_proposal on proposal_comments(proposal_id);
