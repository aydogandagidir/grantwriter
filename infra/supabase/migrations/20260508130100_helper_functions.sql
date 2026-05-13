-- Migration 009: RLS helper functions + soft-delete column + grants
-- Source of truth: docs/03-database-schema.md §4
--
-- This migration MUST land before the RLS policies migration — every policy
-- in the next migration calls public.tenant_id() and / or public.is_tenant_admin().
-- We give them an earlier timestamp than 20260508130200_rls_policies.sql so
-- Supabase applies them in dependency order.
--
-- Schema choice — public, not auth:
--   Supabase CLI's local stack and (almost certainly) production Supabase
--   itself keep the `auth` schema owned by `supabase_auth_admin`. The
--   `postgres` migration runner can't CREATE inside that schema. So our
--   custom helpers live in `public` and the RLS policies reference them
--   as `public.tenant_id()` / `public.is_tenant_admin()`.
--
--   The helpers still read Supabase's `auth.uid()` — that one IS provided
--   by GoTrue and lives in the auth schema by design. Calling it from
--   public is allowed (it's a SECURITY DEFINER function with execute
--   granted to authenticated).
--
-- Why SECURITY DEFINER:
--   The helpers query public.users. If RLS were enabled on public.users
--   (which the next migration does), an INVOKER-mode function would trigger
--   the users policy "tenant_id = public.tenant_id()" — calling itself
--   recursively. SECURITY DEFINER runs the function as its owner (postgres
--   on Supabase / locally), bypassing RLS on public.users.
--
--   Locking down the search_path is mandatory for SECURITY DEFINER functions
--   per CWE-426; without it a hostile schema can shadow `users`.
--
-- Why the soft-delete column:
--   The helpers exclude rows where deleted_at is set, so a soft-deleted user
--   gets public.tenant_id() = NULL and is denied by every policy that compares
--   tenant_id = public.tenant_id().

alter table public.users
  add column if not exists deleted_at timestamptz;

create index if not exists idx_users_active
  on public.users(id)
  where deleted_at is null;

create or replace function public.tenant_id() returns uuid
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

create or replace function public.is_tenant_admin() returns boolean
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
revoke all on function public.tenant_id() from public, anon;
revoke all on function public.is_tenant_admin() from public, anon;
grant execute on function public.tenant_id() to authenticated, service_role;
grant execute on function public.is_tenant_admin() to authenticated, service_role;

-- Backwards-compat shims: if an earlier deploy created auth.tenant_id() /
-- auth.is_tenant_admin() before this rename, drop them so policies refer
-- to a single implementation. Silently no-op on first install.
do $$ begin
  drop function if exists auth.tenant_id();
  drop function if exists auth.is_tenant_admin();
exception
  when insufficient_privilege then null;   -- can't drop in auth — fine, never existed there
  when invalid_schema_name then null;      -- no auth schema at all
end $$;
