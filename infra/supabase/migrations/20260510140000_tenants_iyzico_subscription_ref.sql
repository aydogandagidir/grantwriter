-- Migration 013: tenants.iyzico_subscription_reference
-- Source of truth: docs/03 §1.1 + sprint-roadmap S3.D11
--
-- Persisting the active Iyzico subscriptionReferenceCode lets us cancel
-- a subscription out-of-band without re-fetching it from Iyzico (which
-- would require an extra outbound + an admin lookup per cancel call).
--
-- The column is set when the inbound webhook receiver sees an
-- ``subscription.activated`` / ``SUBSCRIPTION_ORDER_SUCCESS`` event
-- carrying ``subscriptionReferenceCode``, and cleared on
-- ``subscription.cancelled``. The cancel endpoint reads the column,
-- calls Iyzico, and clears it on success.
--
-- Idempotent — running twice is a no-op.

alter table tenants
  add column if not exists iyzico_subscription_reference text;

comment on column tenants.iyzico_subscription_reference is
  'Iyzico subscriptionReferenceCode populated when checkout completes; '
  'cleared on cancel webhook. NULL means tenant has no active subscription.';
