-- LOCAL-ONLY auth.users stub.
--
-- In production we use Supabase Auth, which provisions the `auth` schema
-- and `auth.users` table automatically. When validating migrations against
-- a raw Postgres + pgvector container (e.g. `pgvector/pgvector:pg16`),
-- run this file FIRST so `public.users.id references auth.users(id)` resolves.
--
-- Do NOT apply this file when running `supabase db reset` against the
-- Supabase CLI's local stack — it owns the auth schema there.

create schema if not exists auth;

create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  email text unique,
  created_at timestamptz not null default now()
);
