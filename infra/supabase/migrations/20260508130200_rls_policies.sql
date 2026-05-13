-- Migration 010: Row Level Security policies (multi-tenant isolation)
-- Source of truth: docs/03-database-schema.md §4
--
-- Depends on the helper functions migration (public.tenant_id /
-- public.is_tenant_admin must exist) and on the soft-delete column added
-- there. Apply ordering enforced by the timestamp prefix.
--
-- Tables WITHOUT RLS (public reference data — same view for everyone):
--   programmes, calls, call_chunks, successful_proposals_corpus,
--   successful_proposal_chunks, funder_guidelines, cordis_funded_projects.
--
-- Tables WITH RLS but NO explicit authenticated policies (the doc leaves
-- their policies to later migrations — they stay deny-by-default for
-- authenticated users; only service_role can touch them):
--   tenant_invitations, tenant_llm_config, proposal_versions,
--   proposal_comments, billing_events.

-- ── Enable RLS on every tenant-scoped table ───────────────────────────
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

-- ── public.users: see own tenant; update only own row ─────────────────
drop policy if exists users_own_tenant on public.users;
create policy users_own_tenant on public.users
  for select to authenticated
  using (tenant_id = public.tenant_id());

drop policy if exists users_self_update on public.users;
create policy users_self_update on public.users
  for update to authenticated
  using (id = auth.uid())
  with check (id = auth.uid());

-- ── tenants: see own; admins update own ───────────────────────────────
drop policy if exists tenants_own on tenants;
create policy tenants_own on tenants
  for select to authenticated
  using (id = public.tenant_id());

drop policy if exists tenants_admin_update on tenants;
create policy tenants_admin_update on tenants
  for update to authenticated
  using (id = public.tenant_id() and public.is_tenant_admin())
  with check (id = public.tenant_id() and public.is_tenant_admin());

-- ── proposals: tenant-scoped read; author or admin write ──────────────
drop policy if exists proposals_tenant_select on proposals;
create policy proposals_tenant_select on proposals
  for select to authenticated
  using (tenant_id = public.tenant_id());

drop policy if exists proposals_tenant_insert on proposals;
create policy proposals_tenant_insert on proposals
  for insert to authenticated
  with check (tenant_id = public.tenant_id() and created_by = auth.uid());

drop policy if exists proposals_tenant_update on proposals;
create policy proposals_tenant_update on proposals
  for update to authenticated
  using (
    tenant_id = public.tenant_id()
    and (created_by = auth.uid() or public.is_tenant_admin())
  )
  with check (tenant_id = public.tenant_id());

drop policy if exists proposals_tenant_delete on proposals;
create policy proposals_tenant_delete on proposals
  for delete to authenticated
  using (tenant_id = public.tenant_id() and public.is_tenant_admin());

-- ── proposal_provenance: follow proposal access ───────────────────────
drop policy if exists provenance_via_proposal on proposal_provenance;
create policy provenance_via_proposal on proposal_provenance
  for all to authenticated
  using (
    proposal_id in (select id from proposals where tenant_id = public.tenant_id())
  )
  with check (
    proposal_id in (select id from proposals where tenant_id = public.tenant_id())
  );

-- ── citations: follow proposal access ─────────────────────────────────
drop policy if exists citations_via_proposal on citations;
create policy citations_via_proposal on citations
  for all to authenticated
  using (
    proposal_id in (select id from proposals where tenant_id = public.tenant_id())
  )
  with check (
    proposal_id in (select id from proposals where tenant_id = public.tenant_id())
  );

-- ── tenant_usage_log: admin-only read ─────────────────────────────────
drop policy if exists usage_admin_select on tenant_usage_log;
create policy usage_admin_select on tenant_usage_log
  for select to authenticated
  using (tenant_id = public.tenant_id() and public.is_tenant_admin());

-- ── audit_log: admin-only read ────────────────────────────────────────
drop policy if exists audit_admin_select on audit_log;
create policy audit_admin_select on audit_log
  for select to authenticated
  using (tenant_id = public.tenant_id() and public.is_tenant_admin());
