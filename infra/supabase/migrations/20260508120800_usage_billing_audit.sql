-- Migration 008: Usage tracking + billing events + audit log
-- Source of truth: docs/03-database-schema.md §2.5, §2.6
--
-- tenant_usage_log feeds Stripe metered billing and BYOK budget alerts.
-- billing_events stores raw provider webhooks for reconciliation.
-- audit_log records every privileged write (proposal updates, citation
-- verification, compliance reviews, BYOK changes).

create table if not exists tenant_usage_log (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  user_id uuid references public.users(id),
  proposal_id uuid references proposals(id),
  event_type text not null,
  resource text,
  input_tokens int,
  output_tokens int,
  cached_tokens int,
  cost_usd numeric(10,6),
  used_byok boolean default false,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists idx_usage_tenant_time
  on tenant_usage_log(tenant_id, created_at desc);
create index if not exists idx_usage_proposal on tenant_usage_log(proposal_id);

create table if not exists billing_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  event_type text not null,
  provider text not null check (provider in ('stripe','iyzico')),
  provider_event_id text unique not null,
  amount_eur numeric(10,2),
  payload jsonb not null,
  processed_at timestamptz not null default now()
);

create table if not exists audit_log (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references tenants(id),
  user_id uuid references public.users(id),
  action text not null,
  resource_type text,
  resource_id uuid,
  diff jsonb,
  ip_address inet,
  user_agent text,
  created_at timestamptz not null default now()
);
create index if not exists idx_audit_tenant_time
  on audit_log(tenant_id, created_at desc);
create index if not exists idx_audit_action on audit_log(action);
