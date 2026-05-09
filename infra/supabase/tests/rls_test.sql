-- RLS test suite — runs in CI on every migration.
-- Source: docs/03-database-schema.md §5.
--
-- Run via:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f infra/supabase/tests/rls_test.sql
--
-- Output ends with the literal string "RLS tests PASSED" on success. Any
-- assertion failure raises and aborts before that final SELECT.
--
-- This file is destructive (BEGIN ... ROLLBACK), so it leaves the database
-- exactly as it found it. Safe to run against any environment with the
-- migrations applied.

\set ON_ERROR_STOP on
\echo === RLS test suite starting ===

begin;

-- ── Setup: 2 tenants, 2 users, 2 proposals ─────────────────────────────
insert into tenants (id, name, slug) values
  ('aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'Tenant A', 'tenant-a'),
  ('bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'Tenant B', 'tenant-b');

insert into auth.users (id, email) values
  ('11111111-1111-1111-1111-111111111111', 'a@test.com'),
  ('22222222-2222-2222-2222-222222222222', 'b@test.com');

insert into public.users (id, tenant_id, role) values
  ('11111111-1111-1111-1111-111111111111',
   'aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'owner'),
  ('22222222-2222-2222-2222-222222222222',
   'bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'owner');

insert into proposals (tenant_id, created_by, programme_id, language) values
  ('aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
   '11111111-1111-1111-1111-111111111111', 'tubitak_1501', 'tr'),
  ('bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
   '22222222-2222-2222-2222-222222222222', 'horizon_eu_ria', 'en');

-- Sanity: as superuser/postgres we should still see both rows.
do $$
declare cnt int;
begin
  select count(*) into cnt from proposals;
  if cnt < 2 then
    raise exception 'SETUP FAILED: postgres sees only % proposals (need 2)', cnt;
  end if;
end $$;

-- ── Switch to authenticated role + impersonate user A (Tenant A owner) ─
set local role authenticated;
set local "request.jwt.claims" =
  '{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}';

-- Test 1: User A sees exactly 1 proposal (Tenant A's).
do $$
declare cnt int;
begin
  select count(*) into cnt from proposals;
  if cnt != 1 then
    raise exception 'RLS LEAK: user A sees % proposals, expected 1', cnt;
  end if;
end $$;

-- Test 2: User A cannot see Tenant B proposals via explicit filter.
do $$
declare cnt int;
begin
  select count(*) into cnt from proposals
   where tenant_id = 'bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
  if cnt != 0 then
    raise exception 'RLS LEAK: user A sees Tenant B proposals via filter';
  end if;
end $$;

-- Test 3: User A's auth.tenant_id() returns Tenant A's id.
do $$
declare tid uuid;
begin
  select auth.tenant_id() into tid;
  if tid != 'aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa' then
    raise exception 'auth.tenant_id() mismatch for user A: %', tid;
  end if;
end $$;

-- Test 4: User A is owner → auth.is_tenant_admin() = true.
do $$
declare ok boolean;
begin
  select auth.is_tenant_admin() into ok;
  if ok is not true then
    raise exception 'auth.is_tenant_admin() false for user A (owner)';
  end if;
end $$;

-- Test 5: User A cannot insert into Tenant B (RLS WITH CHECK rejects).
do $$
begin
  begin
    insert into proposals (tenant_id, created_by, programme_id, language)
    values ('bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            '11111111-1111-1111-1111-111111111111', 'tubitak_1501', 'tr');
    raise exception 'RLS LEAK: user A inserted a row into Tenant B';
  exception
    when insufficient_privilege then null;  -- expected — RLS denied INSERT
    when check_violation then null;          -- expected — WITH CHECK denied
  end;
end $$;

-- Test 6: User A cannot UPDATE Tenant B's proposals (zero rows affected).
do $$
declare affected int;
begin
  update proposals set title = 'tampered'
   where tenant_id = 'bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
  get diagnostics affected = row_count;
  if affected != 0 then
    raise exception 'RLS LEAK: user A updated % Tenant B rows', affected;
  end if;
end $$;

-- ── Switch to user B (Tenant B owner) — symmetric checks ──────────────
set local "request.jwt.claims" =
  '{"sub":"22222222-2222-2222-2222-222222222222","role":"authenticated"}';

-- Test 7: User B sees exactly 1 proposal.
do $$
declare cnt int;
begin
  select count(*) into cnt from proposals;
  if cnt != 1 then
    raise exception 'RLS LEAK: user B sees % proposals, expected 1', cnt;
  end if;
end $$;

-- Test 8: User B's auth.tenant_id() returns Tenant B's id.
do $$
declare tid uuid;
begin
  select auth.tenant_id() into tid;
  if tid != 'bbbb1111-bbbb-bbbb-bbbb-bbbbbbbbbbbb' then
    raise exception 'auth.tenant_id() mismatch for user B: %', tid;
  end if;
end $$;

-- ── Anonymous (no JWT) — must see nothing ─────────────────────────────
set local role anon;
set local "request.jwt.claims" = '';

-- Test 9: anon role sees zero proposals (no policy grants it access).
do $$
declare cnt int;
begin
  select count(*) into cnt from proposals;
  if cnt != 0 then
    raise exception 'RLS LEAK: anon sees % proposals, expected 0', cnt;
  end if;
end $$;

-- ── service_role — bypasses RLS, sees everything ──────────────────────
set local role service_role;

-- Test 10: service_role sees both proposals (BYPASSRLS).
do $$
declare cnt int;
begin
  select count(*) into cnt from proposals;
  if cnt != 2 then
    raise exception
      'service_role bypass broken: sees % proposals, expected 2', cnt;
  end if;
end $$;

reset role;
rollback;

select 'RLS tests PASSED' as result;
