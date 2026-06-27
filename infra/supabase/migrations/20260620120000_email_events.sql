-- Migration: email delivery / bounce / complaint events from Resend.
--
-- The notifications module ships email on the saga's success path +
-- the invitations flow; until now we had no way to see whether
-- Resend actually delivered them. This table is the read-side log
-- the operator monitors for reputation issues + the per-recipient
-- bounce suppression list that ``send_invitation_email`` consults
-- before queuing another delivery.
--
-- We persist the full payload as ``jsonb`` so backfilling new event
-- types (e.g. ``email.failed`` once Resend ships it) doesn't need a
-- migration — query the ``payload`` column for the new key.
--
-- Idempotency: ``provider_event_id`` is UNIQUE. Resend retries on
-- transient 5xx, the receiver does ``ON CONFLICT DO NOTHING`` so a
-- replayed event becomes a no-op.

create table if not exists email_events (
  id uuid primary key default gen_random_uuid(),
  provider text not null default 'resend' check (provider in ('resend')),
  provider_event_id text not null unique,
  event_type text not null,
  recipient text,
  -- Resend's ``message_id`` ties the event back to the send. Nullable
  -- because some event types (e.g. ``email.opened``) re-send with a
  -- different correlation key.
  message_id text,
  payload jsonb not null default '{}'::jsonb,
  received_at timestamptz not null default now()
);

create index if not exists idx_email_events_recipient
  on email_events(recipient, received_at desc);
create index if not exists idx_email_events_message_id
  on email_events(message_id);
create index if not exists idx_email_events_event_type
  on email_events(event_type, received_at desc);

comment on table email_events is
  'Resend webhook receipts — see src/api/routes/notifications.py + '
  'docs/09 §3.1.2 for the suppression-list use case.';
