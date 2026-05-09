-- Migration 005: Proposals + per-sentence provenance
-- Source of truth: docs/03-database-schema.md §2.3
--
-- proposal_provenance feeds the EU AI Act / Horizon Europe AI disclosure
-- (every sentence tagged human / ai-generated / ai-edited / imported / rag-retrieved).

create table if not exists proposals (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  created_by uuid not null references public.users(id),
  call_id uuid references calls(id),
  programme_id text not null references programmes(id),
  title text,
  acronym text,
  status text not null default 'draft' check (status in (
    'draft', 'brief_complete', 'generating', 'draft_complete',
    'in_review', 'validated', 'exported', 'submitted',
    'funded', 'rejected', 'archived'
  )),
  language text not null check (language in ('tr','en')),
  brief jsonb not null default '{}'::jsonb,
  draft jsonb not null default '{}'::jsonb,
  budget jsonb default '{}'::jsonb,
  bibliography jsonb default '[]'::jsonb,
  compliance_report jsonb default '{}'::jsonb,
  distinctiveness_score numeric(5,4),
  word_count int default 0,
  page_count int default 0,
  ai_disclosure_text text,
  generation_started_at timestamptz,
  generation_completed_at timestamptz,
  llm_cost_usd numeric(10,4) default 0,
  exported_at timestamptz,
  submitted_at timestamptz,
  result_status text check (result_status in ('pending','funded','rejected','withdrawn')),
  result_score numeric(5,2),
  result_feedback text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_proposals_tenant on proposals(tenant_id);
create index if not exists idx_proposals_status on proposals(status);
create index if not exists idx_proposals_call on proposals(call_id);
create index if not exists idx_proposals_created on proposals(created_at desc);

create table if not exists proposal_provenance (
  id uuid primary key default gen_random_uuid(),
  proposal_id uuid not null references proposals(id) on delete cascade,
  section text not null,
  sentence_id text not null,
  content text not null,
  source text not null check (source in (
    'human','ai-generated','ai-edited','imported','rag-retrieved'
  )),
  agent_id text,
  llm_model text,
  llm_tokens int,
  source_citations text[],
  created_at timestamptz not null default now(),
  unique(proposal_id, sentence_id)
);
create index if not exists idx_provenance_proposal on proposal_provenance(proposal_id);
