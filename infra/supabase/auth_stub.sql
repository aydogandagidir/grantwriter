-- LOCAL-ONLY Supabase Auth surface stub.
--
-- Production runs on Supabase, which provisions the `auth` schema, the
-- `auth.users` table, the `auth.uid()` function, and the `anon` /
-- `authenticated` / `service_role` roles automatically.
--
-- For raw Postgres + pgvector containers (e.g. CI fast lane,
-- pgvector/pgvector:pg16) we recreate just enough of that surface so
-- migrations apply and the RLS test suite executes.
--
-- DO NOT apply this file when running `supabase db reset` against the
-- Supabase CLI's local stack — Supabase owns those objects there and
-- this stub would clash.

create schema if not exists auth;

create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  email text unique,
  -- Supabase's auth.users carries raw_user_meta_data (the JWT
  -- user_metadata claim — display name, avatar, etc.). The onboarding
  -- endpoint reads it; without the column the CI fast lane 500s with
  -- ``UndefinedColumnError``. Mirror the production shape.
  raw_user_meta_data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- Idempotency: CI databases created before this column was added need
-- it backfilled — ``create table if not exists`` won't alter an
-- existing table.
alter table auth.users
  add column if not exists raw_user_meta_data jsonb not null default '{}'::jsonb;

-- auth.uid() — Supabase's standard implementation. Reads the JWT subject
-- claim from the per-transaction GUC `request.jwt.claims`.
create or replace function auth.uid() returns uuid
language sql stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
  )::uuid
$$;

-- Roles — exact names match Supabase Cloud so the same SQL works locally.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    -- BYPASSRLS lets Celery workers / scrapers / migrations connect with this
    -- role and see the world ignoring all RLS policies. Production gives this
    -- role to its service-role JWT.
    create role service_role nologin bypassrls;
  end if;
end $$;

grant usage on schema public to anon, authenticated, service_role;
grant usage on schema auth to anon, authenticated, service_role;

-- Tables exist already (migrations 002-008 have run by the time the test
-- suite hits this); GRANT them now so authenticated and anon can query.
-- service_role inherits via BYPASSRLS so explicit grants are belt+suspenders.
grant select, insert, update, delete on all tables in schema public
  to anon, authenticated, service_role;
grant select on all tables in schema auth to anon, authenticated, service_role;
grant usage, select on all sequences in schema public
  to anon, authenticated, service_role;

-- Future tables created by migrations get the same default privileges.
alter default privileges in schema public
  grant select, insert, update, delete on tables to anon, authenticated, service_role;
alter default privileges in schema public
  grant usage, select on sequences to anon, authenticated, service_role;
