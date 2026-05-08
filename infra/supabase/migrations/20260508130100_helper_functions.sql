-- Migration 009: RLS helper functions + soft-delete column + grants
-- Source of truth: docs/03-database-schema.md §4
--
-- This migration MUST land before the RLS policies migration — every policy
-- in the next migration calls auth.tenant_id() and / or auth.is_tenant_admin().
-- We give them an earlier timestamp than 20260508130200_rls_policies.sql so
-- Supabase applies them in dependency order.
--
-- Why SECURITY DEFINER:
--   The helpers query public.users. If RLS were enabled on public.users
--   (which the next migration does), an INVOKER-mode function would trigger
--   the users policy "tenant_id = auth.tenant_id()" — calling itself
--   recursively. SECURITY DEFINER runs the function as its owner (postgres
--   on Supabase / locally), bypassing RLS on public.users.
--
--   Locking down the search_path is mandatory for SECURITY DEFINER functions
--   per CWE-426; without it a hostile schema can shadow `users`.
--
-- Why the soft-delete column:
--   The helpers exclude rows where deleted_at is set, so a soft-deleted user
--   gets auth.tenant_id() = NULL and is denied by every policy that compares
--   tenant_id = auth.tenant_id().

alter table public.users
  add column if not exists deleted_at timestamptz;

create index if not exists idx_users_active
  on public.users(id)
  where deleted_at is null;

create or replace function auth.tenant_id() returns uuid
language sql
security definer
stable
set search_path = public, pg_catalog
as $$
  select tenant_id
    from public.users
   where id = auth.uid()
     and deleted_at is null
$$;

create or replace function auth.is_tenant_admin() returns boolean
language sql
security definer
stable
set search_path = public, pg_catalog
as $$
  select role in ('owner', 'admin')
    from public.users
   where id = auth.uid()
     and deleted_at is null
$$;

-- Grants for the helpers — anon must NOT call them (they leak which user is
-- which tenant), but authenticated and service_role need execution rights.
revoke all on function auth.tenant_id() from public, anon;
revoke all on function auth.is_tenant_admin() from public, anon;
grant execute on function auth.tenant_id() to authenticated, service_role;
grant execute on function auth.is_tenant_admin() to authenticated, service_role;
