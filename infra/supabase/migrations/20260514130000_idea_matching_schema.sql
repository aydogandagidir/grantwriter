-- Migration: idea-matching schema (Faz 2 foundation).
--
-- Adds four tables that turn the call catalogue into a matchmaking
-- engine:
--
--   * organization_profiles — one row per tenant. Drives the eligibility
--     checker (entity_type vs call.eligibility_tags, country vs
--     call.geo_scope, TRL fit) and serves as the priors input to
--     IdeaGenerator (suggest ideas that match the org's expertise).
--   * project_ideas — a user's project idea, with its embedding. Owned
--     by the tenant. The same idea can match many calls, so matching
--     lives in its own table.
--   * idea_call_matches — match score cache, composite (idea, call) key.
--     Recomputing the LLM re-rank is expensive (~$0.02/idea), so we
--     cache for 24h via ``computed_at`` and invalidate on idea/call edit.
--   * call_idea_suggestions — cache of LLM-generated idea cards per
--     call. Shared across tenants (no PII in suggestions). Cuts the
--     IdeaGenerator cost from ~$0.15/call → near-zero on cache hit.
--
-- Also extends ``proposals``:
--   * idea_id FK → project_ideas (nullable, since pre-Faz-2 proposals
--     were created without an idea).
--   * status CHECK adds the five matching-flow states:
--     idea_draft, idea_matched, call_selected, eligibility_verified,
--     brief_in_progress.

-- ── organization_profiles ───────────────────────────────────────────────

create table if not exists organization_profiles (
  tenant_id uuid primary key references tenants(id) on delete cascade,
  legal_name text,
  entity_type text check (entity_type in (
    'individual','sme','university','large_corp','ngo','research_org'
  )),
  country text,
  nuts_region text,
  nace_codes text[] not null default array[]::text[],
  sectors text[] not null default array[]::text[],
  team_size int,
  annual_revenue_eur numeric(15,2),
  founded_year int,
  technology_areas text[] not null default array[]::text[],
  trl_current int check (trl_current between 1 and 9),
  trl_target int check (trl_target between 1 and 9),
  expertise_keywords text[] not null default array[]::text[],
  past_projects jsonb not null default '[]'::jsonb,
  funding_history jsonb not null default '[]'::jsonb,
  preferred_languages text[] not null default array[]::text[],
  embedding vector(3072),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_org_profiles_country
  on organization_profiles(country);
create index if not exists idx_org_profiles_sectors
  on organization_profiles using gin(sectors);
create index if not exists idx_org_profiles_embedding
  on organization_profiles
  using hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);

-- updated_at is bumped by application code (no project-wide trigger
-- helper exists yet); see ``ScraperRunner.persist`` for the analogous
-- pattern on calls.last_seen_at.

-- ── project_ideas ───────────────────────────────────────────────────────

create table if not exists project_ideas (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  created_by uuid references users(id),
  source text not null default 'user_input'
    check (source in ('user_input','generated_from_call','imported')),
  seed_call_id uuid references calls(id) on delete set null,
  title text not null,
  abstract text not null,
  technology_angle text,
  target_market text,
  trl_estimate int check (trl_estimate between 1 and 9),
  budget_estimate_eur_min numeric(15,2),
  budget_estimate_eur_max numeric(15,2),
  team_size_estimate int,
  sectors text[] not null default array[]::text[],
  keywords text[] not null default array[]::text[],
  embedding vector(3072),
  distinctiveness_score numeric(5,4),
  status text not null default 'draft'
    check (status in ('draft','active','archived')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_project_ideas_tenant
  on project_ideas(tenant_id);
create index if not exists idx_project_ideas_status
  on project_ideas(tenant_id, status) where status = 'active';
create index if not exists idx_project_ideas_embedding
  on project_ideas
  using hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);
create index if not exists idx_project_ideas_sectors
  on project_ideas using gin(sectors);

-- updated_at on project_ideas: bumped by application code on UPDATE.

-- ── idea_call_matches ───────────────────────────────────────────────────
--
-- Composite primary key (idea_id, call_id) gives us idempotent upsert
-- on re-rerank. Score columns let the UI render a per-call breakdown
-- (semantic / keyword / sector / TRL / budget) without re-computation.

create table if not exists idea_call_matches (
  idea_id uuid not null references project_ideas(id) on delete cascade,
  call_id uuid not null references calls(id) on delete cascade,
  total_score numeric(5,4),
  semantic_score numeric(5,4),
  keyword_overlap_score numeric(5,4),
  sector_score numeric(5,4),
  trl_fit_score numeric(5,4),
  budget_fit_score numeric(5,4),
  rationale_tr text,
  rationale_en text,
  identified_gaps text[] not null default array[]::text[],
  eligibility_verdict text check (eligibility_verdict in (
    'ELIGIBLE','CONDITIONAL','NOT_ELIGIBLE'
  )),
  computed_at timestamptz not null default now(),
  model_version text,
  primary key (idea_id, call_id)
);

create index if not exists idx_idea_call_matches_idea_score
  on idea_call_matches(idea_id, total_score desc nulls last);
create index if not exists idx_idea_call_matches_call
  on idea_call_matches(call_id);
create index if not exists idx_idea_call_matches_computed
  on idea_call_matches(computed_at);

-- ── call_idea_suggestions ───────────────────────────────────────────────
--
-- LLM-generated ideation cache per call. Shared across tenants because
-- the seed material (call text + funded-project corpus) is public —
-- there's no tenant data to leak. Saves on the ~$0.15/call generation
-- cost on the second user that browses the same call.

create table if not exists call_idea_suggestions (
  call_id uuid not null references calls(id) on delete cascade,
  suggestion_index int not null,
  title text not null,
  abstract text not null,
  technology_angle text,
  impact_thesis text,
  est_budget_eur_min numeric(15,2),
  est_budget_eur_max numeric(15,2),
  est_trl int check (est_trl between 1 and 9),
  suggested_consortium_type text,
  alignment_score numeric(5,4),
  distinctiveness_score numeric(5,4),
  generated_at timestamptz not null default now(),
  generator_version text,
  primary key (call_id, suggestion_index)
);

create index if not exists idx_call_idea_suggestions_generated
  on call_idea_suggestions(generated_at);

-- ── proposals: idea_id + new statuses ───────────────────────────────────

alter table proposals
  add column if not exists idea_id uuid references project_ideas(id) on delete set null;

create index if not exists idx_proposals_idea on proposals(idea_id);

-- Extend the status CHECK. The original was defined inline in
-- migration 005, so Postgres auto-named it; rather than hard-coding a
-- guess (Supabase + plain Postgres pick slightly different names),
-- drop whichever constraint currently enforces the status enum.
do $$
declare
  conname_to_drop text;
begin
  select conname into conname_to_drop
    from pg_constraint
   where conrelid = 'proposals'::regclass
     and contype = 'c'
     and pg_get_constraintdef(oid) ilike '%status%draft%brief_complete%';
  if conname_to_drop is not null then
    execute format('alter table proposals drop constraint %I', conname_to_drop);
  end if;
end $$;

alter table proposals add constraint proposals_status_check check (
  status in (
    'idea_draft',
    'idea_matched',
    'call_selected',
    'eligibility_verified',
    'brief_in_progress',
    'draft',
    'brief_complete',
    'generating',
    'draft_complete',
    'in_review',
    'validated',
    'exported',
    'submitted',
    'funded',
    'rejected',
    'archived'
  )
);

-- ── RLS ─────────────────────────────────────────────────────────────────
--
-- Tenant-scoped: organization_profiles, project_ideas, idea_call_matches.
-- Globally-readable: call_idea_suggestions (no tenant data; the
-- IdeaGenerator output is funder-derived).

alter table organization_profiles enable row level security;
alter table project_ideas enable row level security;
alter table idea_call_matches enable row level security;
alter table call_idea_suggestions enable row level security;

-- organization_profiles --------------------------------------------------

drop policy if exists organization_profiles_select on organization_profiles;
create policy organization_profiles_select
  on organization_profiles
  for select
  using (tenant_id = public.tenant_id());

drop policy if exists organization_profiles_insert on organization_profiles;
create policy organization_profiles_insert
  on organization_profiles
  for insert
  with check (tenant_id = public.tenant_id());

drop policy if exists organization_profiles_update on organization_profiles;
create policy organization_profiles_update
  on organization_profiles
  for update
  using (tenant_id = public.tenant_id())
  with check (tenant_id = public.tenant_id());

drop policy if exists organization_profiles_delete on organization_profiles;
create policy organization_profiles_delete
  on organization_profiles
  for delete
  using (tenant_id = public.tenant_id());

-- project_ideas ----------------------------------------------------------

drop policy if exists project_ideas_select on project_ideas;
create policy project_ideas_select
  on project_ideas
  for select
  using (tenant_id = public.tenant_id());

drop policy if exists project_ideas_insert on project_ideas;
create policy project_ideas_insert
  on project_ideas
  for insert
  with check (tenant_id = public.tenant_id());

drop policy if exists project_ideas_update on project_ideas;
create policy project_ideas_update
  on project_ideas
  for update
  using (tenant_id = public.tenant_id())
  with check (tenant_id = public.tenant_id());

drop policy if exists project_ideas_delete on project_ideas;
create policy project_ideas_delete
  on project_ideas
  for delete
  using (tenant_id = public.tenant_id());

-- idea_call_matches: scoped through the parent idea's tenant ------------

drop policy if exists idea_call_matches_select on idea_call_matches;
create policy idea_call_matches_select
  on idea_call_matches
  for select
  using (
    idea_id in (
      select id from project_ideas where tenant_id = public.tenant_id()
    )
  );

drop policy if exists idea_call_matches_insert on idea_call_matches;
create policy idea_call_matches_insert
  on idea_call_matches
  for insert
  with check (
    idea_id in (
      select id from project_ideas where tenant_id = public.tenant_id()
    )
  );

drop policy if exists idea_call_matches_update on idea_call_matches;
create policy idea_call_matches_update
  on idea_call_matches
  for update
  using (
    idea_id in (
      select id from project_ideas where tenant_id = public.tenant_id()
    )
  );

drop policy if exists idea_call_matches_delete on idea_call_matches;
create policy idea_call_matches_delete
  on idea_call_matches
  for delete
  using (
    idea_id in (
      select id from project_ideas where tenant_id = public.tenant_id()
    )
  );

-- call_idea_suggestions: world-readable to authenticated users; only
-- the service role (Celery worker) inserts. RLS keeps the
-- WITH-CHECK gate so a malicious JWT can't seed garbage suggestions.

drop policy if exists call_idea_suggestions_select on call_idea_suggestions;
create policy call_idea_suggestions_select
  on call_idea_suggestions
  for select
  using (true);

-- Insert/update/delete: no policy → no row-level access → service role
-- (which bypasses RLS) is the only writer. That matches the
-- IdeaGenerator's flow: Celery task writes; tenant users only read.

-- ── Comments ────────────────────────────────────────────────────────────

comment on table organization_profiles is
  'Per-tenant profile used by EligibilityChecker (entity_type, country, TRL fit) and as priors for IdeaGenerator.';
comment on table project_ideas is
  'User project ideas. Same idea can match many calls via idea_call_matches.';
comment on table idea_call_matches is
  'Match-score cache (idea, call). Recomputed by IdeaMatcher with 24h TTL via computed_at.';
comment on table call_idea_suggestions is
  'LLM-generated idea cards per call. Shared cache — no tenant data.';
comment on column proposals.idea_id is
  'Optional FK to project_ideas(id); pre-Faz-2 proposals were not idea-rooted, so null is allowed.';
