# 03 — Database Schema

PostgreSQL 16 + pgvector extension. Tüm tablolar Supabase üzerinde, RLS politikaları zorunlu.

## 1. Extensions

```sql
create extension if not exists "uuid-ossp";
create extension if not exists "pgcrypto";
create extension if not exists "vector";       -- pgvector
create extension if not exists "pg_trgm";      -- fuzzy text search
create extension if not exists "btree_gin";    -- composite indexes
```

---

## 2. Core Tables

### 2.1 Tenants & Users

```sql
-- Tenants (Bluedev müşterileri — KOBİ, TTO, accelerator)
create table tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique not null,         -- URL-friendly, e.g. "acme-corp"
  plan text not null default 'starter' check (plan in ('starter','pro','agency','enterprise')),
  billing_email text,
  stripe_customer_id text unique,
  iyzico_customer_id text unique,
  monthly_proposal_limit int not null default 3,
  monthly_proposals_used int not null default 0,
  billing_period_start date,
  status text not null default 'active' check (status in ('active','suspended','cancelled')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index idx_tenants_slug on tenants(slug);
create index idx_tenants_stripe on tenants(stripe_customer_id);

-- Users (Supabase Auth ile bağlı)
create table public.users (
  id uuid primary key references auth.users(id) on delete cascade,
  tenant_id uuid not null references tenants(id) on delete cascade,
  role text not null default 'member' check (role in ('owner','admin','member','viewer')),
  display_name text,
  avatar_url text,
  preferred_language text default 'tr' check (preferred_language in ('tr','en')),
  notification_preferences jsonb not null default '{"email_new_calls": true, "email_draft_complete": true}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index idx_users_tenant on public.users(tenant_id);

-- Tenant invitations
create table tenant_invitations (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  email text not null,
  role text not null default 'member',
  invited_by uuid references public.users(id),
  token text unique not null,
  expires_at timestamptz not null default (now() + interval '7 days'),
  accepted_at timestamptz,
  created_at timestamptz not null default now()
);
create index idx_invitations_email on tenant_invitations(email);
create index idx_invitations_token on tenant_invitations(token);

-- Tenant LLM config (BYOK)
create table tenant_llm_config (
  tenant_id uuid primary key references tenants(id) on delete cascade,
  anthropic_api_key_encrypted bytea,    -- pgcrypto encrypted
  openai_api_key_encrypted bytea,
  preferred_provider text default 'claude' check (preferred_provider in ('claude','openai','auto')),
  monthly_budget_usd numeric(10,2),
  alert_threshold_usd numeric(10,2),
  use_managed_keys boolean default true,  -- Bluedev's keys (Pro+ only)
  updated_at timestamptz not null default now()
);
```

### 2.2 Calls (Hibe Çağrıları)

```sql
create table programmes (
  id text primary key,                   -- 'tubitak_1501', 'horizon_eu_ria', vs.
  name_tr text not null,
  name_en text not null,
  funder text not null,                  -- 'TÜBİTAK', 'European Commission', 'NLnet'
  language text not null check (language in ('tr','en','both')),
  description_tr text,
  description_en text,
  active boolean default true,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- Seed data (5 Faz 1 program):
insert into programmes (id, name_tr, name_en, funder, language) values
  ('tubitak_1501', 'TÜBİTAK 1501 Sanayi AR-GE', 'TÜBİTAK 1501 Industrial R&D', 'TÜBİTAK', 'tr'),
  ('tubitak_1507', 'TÜBİTAK 1507 KOBİ AR-GE Başlangıç', 'TÜBİTAK 1507 SME R&D Start', 'TÜBİTAK', 'tr'),
  ('kosgeb_arge', 'KOSGEB AR-GE ve Yenilik', 'KOSGEB R&D and Innovation', 'KOSGEB', 'tr'),
  ('horizon_eu_ria', 'Horizon Europe RIA/IA', 'Horizon Europe RIA/IA', 'European Commission', 'en'),
  ('cascade_funding', 'Cascade Funding & NLnet', 'Cascade Funding & NLnet', 'NGI / FSTP', 'en');

create table calls (
  id uuid primary key default gen_random_uuid(),
  programme_id text not null references programmes(id),
  source text not null check (source in ('eu_ft_portal','nlnet','cascade','tubitak','kosgeb','manual')),
  external_id text not null,             -- e.g. HORIZON-CL4-2026-DIGITAL-EMERGING-01
  title text not null,
  call_text text,                        -- full text of call document
  call_url text,
  call_pdf_url text,
  deadline date,
  budget_total_eur numeric(15,2),
  budget_per_project_min_eur numeric(15,2),
  budget_per_project_max_eur numeric(15,2),
  trl_min int,
  trl_max int,
  topic_keywords text[],
  eligibility_summary jsonb,             -- {countries: [], entity_types: [], ...}
  raw_metadata jsonb not null default '{}'::jsonb,
  scraped_at timestamptz not null default now(),
  status text not null default 'open' check (status in ('open','closing_soon','closed','draft')),
  language text not null,
  unique(source, external_id)
);
create index idx_calls_programme on calls(programme_id);
create index idx_calls_deadline on calls(deadline);
create index idx_calls_status on calls(status) where status = 'open';
create index idx_calls_topic on calls using gin(topic_keywords);
create index idx_calls_text_trgm on calls using gin(title gin_trgm_ops);

-- Call chunks for RAG
create table call_chunks (
  id uuid primary key default gen_random_uuid(),
  call_id uuid not null references calls(id) on delete cascade,
  chunk_index int not null,
  section text,                          -- 'scope', 'expected_outcomes', 'eligibility', etc.
  content text not null,
  embedding vector(3072),
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index idx_call_chunks_embedding on call_chunks using hnsw (embedding vector_cosine_ops);
create index idx_call_chunks_call on call_chunks(call_id);
```

### 2.3 Proposals (Başvurular)

```sql
create table proposals (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  created_by uuid not null references public.users(id),
  call_id uuid references calls(id),
  programme_id text not null references programmes(id),
  title text,
  acronym text,                          -- e.g., "GREENMOBILITY"
  status text not null default 'draft' check (status in (
    'draft', 'brief_complete', 'generating', 'draft_complete',
    'in_review', 'validated', 'exported', 'submitted',
    'funded', 'rejected', 'archived'
  )),
  language text not null check (language in ('tr','en')),
  brief jsonb not null default '{}'::jsonb,    -- user input (program-specific schema)
  draft jsonb not null default '{}'::jsonb,    -- {excellence_md, impact_md, implementation_md, ...}
  budget jsonb default '{}'::jsonb,            -- structured budget
  bibliography jsonb default '[]'::jsonb,      -- verified citations
  compliance_report jsonb default '{}'::jsonb, -- AI disclosure, DNSH, page limits
  distinctiveness_score numeric(5,4),          -- 0.0000 - 1.0000
  word_count int default 0,
  page_count int default 0,
  ai_disclosure_text text,                     -- HE page 32 auto-generated
  generation_started_at timestamptz,
  generation_completed_at timestamptz,
  llm_cost_usd numeric(10,4) default 0,
  exported_at timestamptz,
  submitted_at timestamptz,
  result_status text check (result_status in ('pending','funded','rejected','withdrawn')),
  result_score numeric(5,2),                   -- evaluator score if known
  result_feedback text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index idx_proposals_tenant on proposals(tenant_id);
create index idx_proposals_status on proposals(status);
create index idx_proposals_call on proposals(call_id);
create index idx_proposals_created on proposals(created_at desc);

-- Provenance tracking (every sentence in draft)
create table proposal_provenance (
  id uuid primary key default gen_random_uuid(),
  proposal_id uuid not null references proposals(id) on delete cascade,
  section text not null,                  -- 'excellence', 'impact', etc.
  sentence_id text not null,              -- frontend-generated UUID
  content text not null,
  source text not null check (source in ('human','ai-generated','ai-edited','imported','rag-retrieved')),
  agent_id text,                          -- which agent generated this (if AI)
  llm_model text,
  llm_tokens int,
  source_citations text[],                -- citation IDs from bibliography
  created_at timestamptz not null default now(),
  unique(proposal_id, sentence_id)
);
create index idx_provenance_proposal on proposal_provenance(proposal_id);

-- Citations
create table citations (
  id uuid primary key default gen_random_uuid(),
  proposal_id uuid not null references proposals(id) on delete cascade,
  raw_text text not null,                 -- user-typed citation
  doi text,
  title text,
  authors text[],
  year int,
  journal text,
  url text,
  status text not null default 'unverified' check (status in (
    'unverified', 'verifying', 'verified', 'fabricated', 'partial_match'
  )),
  verification_source text check (verification_source in ('crossref','openalex','manual','doi_direct')),
  verified_at timestamptz,
  match_score numeric(4,3),               -- 0.000-1.000 fuzzy match
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index idx_citations_proposal on citations(proposal_id);
create index idx_citations_status on citations(status);
create index idx_citations_doi on citations(doi);

-- Proposal versions (snapshot history)
create table proposal_versions (
  id uuid primary key default gen_random_uuid(),
  proposal_id uuid not null references proposals(id) on delete cascade,
  version_number int not null,
  draft_snapshot jsonb not null,
  created_by uuid references public.users(id),
  comment text,
  created_at timestamptz not null default now(),
  unique(proposal_id, version_number)
);
create index idx_versions_proposal on proposal_versions(proposal_id, version_number desc);

-- Comments (collaboration)
create table proposal_comments (
  id uuid primary key default gen_random_uuid(),
  proposal_id uuid not null references proposals(id) on delete cascade,
  author_id uuid not null references public.users(id),
  section text,                           -- which section
  anchor text,                            -- text anchor or sentence_id
  content text not null,
  resolved boolean default false,
  parent_id uuid references proposal_comments(id),
  created_at timestamptz not null default now()
);
create index idx_comments_proposal on proposal_comments(proposal_id);
```

### 2.4 RAG Corpus

```sql
-- Successful proposals corpus (anonymized)
create table successful_proposals_corpus (
  id uuid primary key default gen_random_uuid(),
  programme_id text not null references programmes(id),
  source text not null,                   -- 'EC_publications', 'cordis', 'manual'
  external_id text,                       -- CORDIS project ID etc.
  title text,
  topic_id text,                          -- e.g. HORIZON-CL4-2024-...
  funded_year int,
  budget_eur numeric,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table successful_proposal_chunks (
  id uuid primary key default gen_random_uuid(),
  corpus_id uuid not null references successful_proposals_corpus(id) on delete cascade,
  section text not null,                  -- 'excellence', 'impact', 'implementation'
  chunk_index int not null,
  content text not null,
  embedding vector(3072),
  metadata jsonb default '{}'::jsonb
);
create index idx_corpus_chunks_embedding on successful_proposal_chunks using hnsw (embedding vector_cosine_ops);
create index idx_corpus_chunks_section on successful_proposal_chunks(section);

-- Funder guidelines (work programmes, evaluation guides, AI rules)
create table funder_guidelines (
  id uuid primary key default gen_random_uuid(),
  programme_id text references programmes(id),
  document_type text not null,            -- 'work_programme', 'evaluation_guide', 'ai_disclosure_rules'
  title text not null,
  content text not null,
  source_url text,
  effective_date date,
  embedding vector(3072),
  created_at timestamptz not null default now()
);
create index idx_guidelines_embedding on funder_guidelines using hnsw (embedding vector_cosine_ops);

-- CORDIS funded projects (for distinctiveness scoring)
create table cordis_funded_projects (
  id uuid primary key default gen_random_uuid(),
  cordis_id text unique not null,
  title text not null,
  acronym text,
  topic_id text,
  programme text,                         -- 'HORIZON', 'H2020', 'FP7'
  budget_eur numeric,
  start_date date,
  end_date date,
  abstract text,
  abstract_embedding vector(3072),
  metadata jsonb default '{}'::jsonb,
  scraped_at timestamptz not null default now()
);
create index idx_cordis_topic on cordis_funded_projects(topic_id);
create index idx_cordis_embedding on cordis_funded_projects using hnsw (abstract_embedding vector_cosine_ops);
```

### 2.5 Usage & Billing

```sql
create table tenant_usage_log (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  user_id uuid references public.users(id),
  proposal_id uuid references proposals(id),
  event_type text not null,               -- 'llm_call', 'citation_verify', 'docx_export', 'draft_generated'
  resource text,                          -- 'claude-opus-4-7', 'crossref', etc.
  input_tokens int,
  output_tokens int,
  cached_tokens int,
  cost_usd numeric(10,6),
  used_byok boolean default false,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index idx_usage_tenant_time on tenant_usage_log(tenant_id, created_at desc);
create index idx_usage_proposal on tenant_usage_log(proposal_id);

create table billing_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  event_type text not null,               -- 'subscription_created', 'invoice_paid', 'subscription_cancelled'
  provider text not null check (provider in ('stripe','iyzico')),
  provider_event_id text unique not null,
  amount_eur numeric(10,2),
  payload jsonb not null,
  processed_at timestamptz not null default now()
);
```

### 2.6 Audit Log

```sql
create table audit_log (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references tenants(id),
  user_id uuid references public.users(id),
  action text not null,                   -- 'proposal.created', 'citation.verified', etc.
  resource_type text,
  resource_id uuid,
  diff jsonb,
  ip_address inet,
  user_agent text,
  created_at timestamptz not null default now()
);
create index idx_audit_tenant_time on audit_log(tenant_id, created_at desc);
create index idx_audit_action on audit_log(action);
```

---

## 3. Triggers & Functions

### 3.1 Auto-update timestamps

```sql
create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger tenants_updated_at before update on tenants
  for each row execute function update_updated_at();
create trigger users_updated_at before update on public.users
  for each row execute function update_updated_at();
create trigger proposals_updated_at before update on proposals
  for each row execute function update_updated_at();
-- ... apply to all tables with updated_at
```

### 3.2 Proposal monthly limit enforcement

```sql
create or replace function check_proposal_limit()
returns trigger as $$
declare
  current_count int;
  limit_val int;
begin
  -- Reset counter if new billing period
  update tenants
    set monthly_proposals_used = 0,
        billing_period_start = current_date
    where id = new.tenant_id
      and (billing_period_start is null
           or billing_period_start < (current_date - interval '1 month'));

  -- Check limit
  select monthly_proposals_used, monthly_proposal_limit
    into current_count, limit_val
    from tenants where id = new.tenant_id;

  if current_count >= limit_val then
    raise exception 'Monthly proposal limit reached (%). Upgrade plan to continue.', limit_val
      using errcode = 'LIMIT';
  end if;

  -- Increment counter only when transitioning to 'generating'
  if new.status = 'generating' and (old.status is null or old.status != 'generating') then
    update tenants set monthly_proposals_used = monthly_proposals_used + 1
      where id = new.tenant_id;
  end if;

  return new;
end;
$$ language plpgsql;

create trigger enforce_proposal_limit
  before insert or update on proposals
  for each row execute function check_proposal_limit();
```

### 3.3 Audit log auto-insert

```sql
create or replace function audit_proposal_changes()
returns trigger as $$
begin
  insert into audit_log (tenant_id, user_id, action, resource_type, resource_id, diff)
  values (
    new.tenant_id,
    auth.uid(),
    case
      when tg_op = 'INSERT' then 'proposal.created'
      when tg_op = 'UPDATE' then 'proposal.updated'
      when tg_op = 'DELETE' then 'proposal.deleted'
    end,
    'proposal',
    new.id,
    case when tg_op = 'UPDATE' then jsonb_build_object('old', to_jsonb(old), 'new', to_jsonb(new)) else null end
  );
  return new;
end;
$$ language plpgsql;

create trigger audit_proposals
  after insert or update or delete on proposals
  for each row execute function audit_proposal_changes();
```

---

## 4. RLS Policies (CRITICAL — Tenant Isolation)

```sql
-- Enable RLS on all tenant-scoped tables
alter table tenants enable row level security;
alter table public.users enable row level security;
alter table tenant_invitations enable row level security;
alter table tenant_llm_config enable row level security;
alter table proposals enable row level security;
alter table proposal_provenance enable row level security;
alter table citations enable row level security;
alter table proposal_versions enable row level security;
alter table proposal_comments enable row level security;
alter table tenant_usage_log enable row level security;
alter table billing_events enable row level security;
alter table audit_log enable row level security;

-- Helper function: get current user's tenant_id
create or replace function auth.tenant_id() returns uuid
language sql stable as $$
  select tenant_id from public.users where id = auth.uid()
$$;

-- Helper function: check if current user is tenant admin/owner
create or replace function auth.is_tenant_admin() returns boolean
language sql stable as $$
  select role in ('owner', 'admin') from public.users where id = auth.uid()
$$;

-- USERS table: see own tenant members
create policy "users_own_tenant" on public.users
  for select using (tenant_id = auth.tenant_id());
create policy "users_self_update" on public.users
  for update using (id = auth.uid());

-- TENANTS table: see own tenant
create policy "tenants_own" on tenants
  for select using (id = auth.tenant_id());
create policy "tenants_admin_update" on tenants
  for update using (id = auth.tenant_id() and auth.is_tenant_admin());

-- PROPOSALS: tenant-scoped, all members can read, only authors+admins can write
create policy "proposals_tenant_select" on proposals
  for select using (tenant_id = auth.tenant_id());
create policy "proposals_tenant_insert" on proposals
  for insert with check (tenant_id = auth.tenant_id() and created_by = auth.uid());
create policy "proposals_tenant_update" on proposals
  for update using (
    tenant_id = auth.tenant_id()
    and (created_by = auth.uid() or auth.is_tenant_admin())
  );
create policy "proposals_tenant_delete" on proposals
  for delete using (tenant_id = auth.tenant_id() and auth.is_tenant_admin());

-- PROPOSAL_PROVENANCE: follow proposal access
create policy "provenance_via_proposal" on proposal_provenance
  for all using (
    proposal_id in (select id from proposals where tenant_id = auth.tenant_id())
  );

-- CITATIONS: follow proposal access
create policy "citations_via_proposal" on citations
  for all using (
    proposal_id in (select id from proposals where tenant_id = auth.tenant_id())
  );

-- USAGE LOG: tenant admins only
create policy "usage_admin_select" on tenant_usage_log
  for select using (tenant_id = auth.tenant_id() and auth.is_tenant_admin());

-- AUDIT LOG: tenant admins only, read-only
create policy "audit_admin_select" on audit_log
  for select using (tenant_id = auth.tenant_id() and auth.is_tenant_admin());

-- Public tables: no RLS
-- programmes, calls, call_chunks, successful_proposals_corpus, etc. — read-only public

-- Service role bypass (for Celery workers, scrapers)
-- These connect with service_role key, bypassing RLS
```

---

## 5. RLS Test Suite (CRITICAL — Run on every migration)

```sql
-- File: infra/supabase/tests/rls_test.sql
-- Run via: psql -f rls_test.sql

-- Setup: 2 tenants, 2 users, 2 proposals
begin;

-- Tenant A
insert into tenants (id, name, slug) values
  ('aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'Tenant A', 'tenant-a');

-- Tenant B
insert into tenants (id, name, slug) values
  ('bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'Tenant B', 'tenant-b');

-- Users
insert into auth.users (id, email) values
  ('11111111-1111-1111-1111-111111111111', 'a@test.com'),
  ('22222222-2222-2222-2222-222222222222', 'b@test.com');

insert into public.users (id, tenant_id, role) values
  ('11111111-1111-1111-1111-111111111111', 'aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'owner'),
  ('22222222-2222-2222-2222-222222222222', 'bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'owner');

-- Proposals
insert into proposals (tenant_id, created_by, programme_id, language) values
  ('aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'tubitak_1501', 'tr'),
  ('bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222', 'horizon_eu_ria', 'en');

-- Test 1: User A should see only Tenant A proposals
set local "request.jwt.claims" = '{"sub":"11111111-1111-1111-1111-111111111111"}';
do $$
declare
  cnt int;
begin
  select count(*) into cnt from proposals;
  if cnt != 1 then raise exception 'RLS LEAK: user A sees % proposals, expected 1', cnt; end if;
end $$;

-- Test 2: User A should NOT see Tenant B proposals
do $$
declare
  cnt int;
begin
  select count(*) into cnt from proposals where tenant_id = 'bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
  if cnt != 0 then raise exception 'RLS LEAK: user A sees Tenant B proposals'; end if;
end $$;

-- Test 3: User A cannot insert into Tenant B
do $$
begin
  begin
    insert into proposals (tenant_id, created_by, programme_id, language)
    values ('bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '11111111-1111-1111-1111-111111111111', 'tubitak_1501', 'tr');
    raise exception 'RLS LEAK: user A inserted into Tenant B';
  exception when others then
    if sqlerrm not like '%violates%' and sqlerrm not like '%policy%' then raise; end if;
  end;
end $$;

rollback;

-- All tests passed
select 'RLS tests PASSED' as result;
```

**Bu test her CI run'da çalışacak.** Fail ederse merge bloklanır.

---

## 6. Migration Strategy

- **Tool:** Supabase CLI (`supabase migration new <name>`)
- **Naming:** `YYYYMMDDHHMMSS_descriptive_name.sql`
- **Order:** `infra/supabase/migrations/`
- **CI:** her PR'da `supabase db reset --linked` + RLS test çalışır
- **Production deploy:** `supabase db push` (staging önce, sonra prod)

### İlk migration'lar (Sprint 1):
1. `001_extensions_and_tenants.sql`
2. `002_users_and_auth.sql`
3. `003_programmes_and_calls.sql`
4. `004_proposals_and_provenance.sql`
5. `005_citations.sql`
6. `006_rag_corpus.sql`
7. `007_usage_and_billing.sql`
8. `008_audit_log.sql`
9. `009_rls_policies.sql`
10. `010_triggers_and_functions.sql`

---

## 7. Indexes & Performance

### Hot queries (production benchmark targets)

| Query | Index | p95 target |
|---|---|---|
| List proposals for tenant | `idx_proposals_tenant`, `idx_proposals_created` | <50ms |
| Search calls by topic | `idx_calls_topic` (GIN) | <100ms |
| RAG retrieval (top-5 similar chunks) | `idx_corpus_chunks_embedding` (HNSW) | <200ms |
| Citation lookup by DOI | `idx_citations_doi` | <10ms |
| Tenant usage report (1 month) | `idx_usage_tenant_time` | <300ms |

### Maintenance
- `VACUUM ANALYZE` weekly (auto on Supabase)
- `REINDEX CONCURRENTLY` if HNSW degrades (>1M chunks)
- Monitor `pg_stat_statements` for slow queries

---

## 8. Backup & Recovery

- **Supabase Pro:** Daily backup, 7-day retention, point-in-time recovery
- **Manual:** weekly `pg_dump` to S3-compatible storage
- **Testing:** monthly restore drill (in staging)

---

## 9. Data Retention

| Table | Retention | Action |
|---|---|---|
| `proposals` | indefinite (user-controlled) | Soft delete (status='archived') |
| `proposal_versions` | last 50 per proposal | Hard delete oldest |
| `audit_log` | 90 days | Hard delete |
| `tenant_usage_log` | 365 days | Aggregate then delete |
| `billing_events` | 7 years (legal) | Cold storage |

---

**Sonraki dosya:** `04-rag-strategy.md` — RAG ve halüsinasyon kontrolü.