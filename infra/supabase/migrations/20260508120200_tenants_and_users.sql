-- Migration 002: Tenants, users, invitations, BYOK config
-- Source of truth: docs/03-database-schema.md §2.1
--
-- public.users.id references auth.users(id) — auth.users is provided by
-- Supabase Auth (auth schema) and exists before user migrations run.
-- For RAW pgvector containers without Supabase Auth, see infra/supabase/auth_stub.sql.

create table if not exists tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique not null,
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
create index if not exists idx_tenants_slug on tenants(slug);
create index if not exists idx_tenants_stripe on tenants(stripe_customer_id);

create table if not exists public.users (
  id uuid primary key references auth.users(id) on delete cascade,
  tenant_id uuid not null references tenants(id) on delete cascade,
  role text not null default 'member' check (role in ('owner','admin','member','viewer')),
  display_name text,
  avatar_url text,
  preferred_language text default 'tr' check (preferred_language in ('tr','en')),
  notification_preferences jsonb not null default
    '{"email_new_calls": true, "email_draft_complete": true}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_users_tenant on public.users(tenant_id);

create table if not exists tenant_invitations (
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
create index if not exists idx_invitations_email on tenant_invitations(email);
create index if not exists idx_invitations_token on tenant_invitations(token);

-- BYOK (Bring Your Own Key) — encrypted with pgcrypto, master key in env (not DB).
create table if not exists tenant_llm_config (
  tenant_id uuid primary key references tenants(id) on delete cascade,
  anthropic_api_key_encrypted bytea,
  openai_api_key_encrypted bytea,
  preferred_provider text default 'claude'
    check (preferred_provider in ('claude','openai','auto')),
  monthly_budget_usd numeric(10,2),
  alert_threshold_usd numeric(10,2),
  use_managed_keys boolean default true,
  updated_at timestamptz not null default now()
);
