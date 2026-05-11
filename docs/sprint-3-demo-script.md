# Sprint 3 Demo Script

**Duration:** 12–15 minutes
**Audience:** Internal review (Aydoğan + Sn + Md) + pilot stakeholders if invited
**Goal:** Prove every Sprint 3 deliverable works end-to-end against two real tenants with no cross-leak.

Run order matches the multi-tenant E2E test (`tests/integration/test_multi_tenant_isolation.py`) so the demo doubles as a live re-verification of CI behaviour.

---

## Pre-flight (T-30 min)

- [ ] Local Postgres + Redis up (`docker compose up -d`)
- [ ] `bluedev_demo` database fresh (`bash scripts/apply_migrations.sh "postgresql://postgres:postgres@localhost:5432/bluedev_demo" --strict`)
- [ ] Backend running on `http://localhost:8000` with:
  - `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/bluedev_demo`
  - `REDIS_URL=redis://localhost:6379/0`
  - `LLM_MASTER_ENCRYPTION_KEY=demo-master-key-32-bytes-padding!`
  - `IYZICO_API_KEY` + `IYZICO_SECRET_KEY` + `IYZICO_WEBHOOK_SECRET=wh_demo`
  - `IYZICO_BASE_URL=https://sandbox-api.iyzipay.com`
  - `RESEND_API_KEY` set OR `EMAIL_ENABLED=false`
- [ ] Frontend running on `http://localhost:3000` with matching `NEXT_PUBLIC_*` envs
- [ ] Seed two tenants + users via psql (script below)
- [ ] ngrok or similar tunnel pointed at `:8000` so Iyzico can hit the webhook
- [ ] Two browser profiles open (incognito + main) so you can log in as different users simultaneously

### Seed snippet

```sql
-- Tenant A (Bluedev)
insert into tenants (id, name, slug)
  values ('aaaa0000-0000-0000-0000-000000000001', 'Bluedev', 'bluedev');
insert into auth.users (id, email)
  values ('aaaa0000-0000-0000-0000-000000000010', 'aydogan@bluedev.dev'),
         ('aaaa0000-0000-0000-0000-000000000011', 'sn@bluedev.dev'),
         ('aaaa0000-0000-0000-0000-000000000012', 'md@bluedev.dev');
insert into public.users (id, tenant_id, role, display_name) values
  ('aaaa0000-0000-0000-0000-000000000010', 'aaaa0000-0000-0000-0000-000000000001', 'owner', 'Aydoğan'),
  ('aaaa0000-0000-0000-0000-000000000011', 'aaaa0000-0000-0000-0000-000000000001', 'admin', 'Sn'),
  ('aaaa0000-0000-0000-0000-000000000012', 'aaaa0000-0000-0000-0000-000000000001', 'member', 'Md');

-- Tenant B (Test Müşteri)
insert into tenants (id, name, slug)
  values ('bbbb0000-0000-0000-0000-000000000001', 'Test Müşteri', 'test-musteri');
insert into auth.users (id, email)
  values ('bbbb0000-0000-0000-0000-000000000010', 'owner@test.example'),
         ('bbbb0000-0000-0000-0000-000000000011', 'member@test.example');
insert into public.users (id, tenant_id, role, display_name) values
  ('bbbb0000-0000-0000-0000-000000000010', 'bbbb0000-0000-0000-0000-000000000001', 'owner', 'Test Owner'),
  ('bbbb0000-0000-0000-0000-000000000011', 'bbbb0000-0000-0000-0000-000000000001', 'member', 'Test Member');
```

(Skip Supabase Auth dance; the demo uses dependency-overridden JWTs via `gh repl scripts/demo_jwt.py` — or sign in via the real Supabase Auth UI if `SUPABASE_URL` points at a working project.)

---

## The 10-step flow

### 1. Setup (2 min) — slide 1: "Two tenants"
- Open both browsers; one logged in as `aydogan@bluedev.dev` (Bluedev owner), the other as `owner@test.example` (Test Müşteri owner).
- Point out the tenant name in the topbar — different.
- Switch locale on Bluedev side: `tr` → `en`. Show the UI flips.

### 2. BYOK isolation (2 min)
- **Bluedev /settings/llm-config:** Paste `sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaa`, click Save → "Anahtarlar kaydedildi" toast. Click Test Connection → mock response, success toast.
- **Test Müşteri /settings/llm-config:** Both badges show "Not configured". **Bluedev's key never leaked.** Audit-relevant.

### 3. Invitations (2 min)
- **Bluedev /settings/invitations:** Email = `newhire@bluedev.dev`, role = Member. Submit → token modal pops with the one-shot secret. Copy.
- **Test Müşteri /settings/invitations:** Empty list. (Cross-tenant leak check.)
- New incognito tab → `/invitations/{token}` → preview shows "You've been invited to join Bluedev as Member". Accept → redirects to `/dashboard`.

### 4. Members (1 min)
- Bluedev /settings/members: 4 rows now (owner + admin + member + newly accepted member).
- Try Promote → Demote: works.
- Try Remove on the **last owner** (yourself): API rejects with `last_owner` guard message. Toast in red.

### 5. Audit (1 min)
- Bluedev /settings/audit: stream shows the recent actions: `tenant.llm_config_updated`, `tenant.invitation_sent`, `tenant.invitation_accepted`. Each row has actor + resource id snippet.
- Test Müşteri /settings/audit: only their own events. No leak.

### 6. Usage (1 min)
- Run a quick `INSERT INTO tenant_usage_log ...` for Bluedev with `cost_usd=10.50`.
- Insert another for Test Müşteri with `cost_usd=999.00`.
- Bluedev /settings/usage: KPI shows `$10.50`. Test Müşteri's `$999` never appears.

### 7. Quota + Iyzico checkout (3 min) — the headline step
- Bluedev plan is Starter, monthly_limit=3.
- Create 3 proposals via API (script). 4th attempt → **402 Payment Required** with `quotaExceeded` error code.
- /settings/billing → "Pro plana geç ($49/ay)" button. Click → backend hits Iyzico `subscription/checkoutform/initialize` → browser bounces to Iyzico sandbox payment page.
- Pay with sandbox card. Iyzico fires webhook to ngrok URL → signature verified → `billing_events` row + `tenant.plan_changed` audit + `tenants.plan = 'pro'`.
- Reload /settings/billing — "Active" badge, monthly_limit = 50.
- Try the 4th proposal again — 201 Created.

### 8. Multi-tenant cross-check (1 min)
- During the upgrade, Test Müşteri's plan stays `starter`. Show it.

### 9. Versions (1 min)
- /proposals/{id} (any proposal): Versions tab → "Anlık görüntü al" with comment "before edit".
- Edit the proposal in DB (UPDATE statement injecting a different excellence_md).
- Take v2 snapshot.
- Hit Restore on v1 → toast "v1 geri yüklendi (yeni v3 oluştu)". Snapshot panel shows v1, v2, v3 (where v3.comment = "restored from v1").

### 10. Comments (1 min)
- /proposals/{id} Comments tab → "Bu bölüm zayıf — daha somut metrik gerek." (Bluedev admin posts).
- Reply with another account: "Katılıyorum, KPI 1.2 ekleyebiliriz." (Bluedev member).
- Resolve the root → strike-through + Resolved badge.
- Toggle "Show resolved" → reveals the thread again.

---

## Q&A talking points

- **"What blocks export?"** Hallucination Hunter's `recommendation=block_export` when either fabricated/not_found citations > 0 OR claim_check_pass_rate < 0.6.
- **"How do we know BYOK keys are safe?"** pgcrypto envelope encryption, master key in Railway secrets only (`docs/sprint-3-known-issues.md` TICKET-003 has the rotation runbook), audit log records "set"/"cleared" sentinels, never the key value (`_validate_diff` rejects strings > 36 chars).
- **"What's the cost ceiling per draft?"** Hallucination Hunter LLM is capped at 10 sample claims × ~$0.001 ≈ $0.01/draft. Compliance Reviewer LLM is one Sonnet call ≈ $0.02/draft. Writers are the bulk.
- **"What happens if Iyzico webhook fails?"** Idempotent persistence: same `provider_event_id` ON CONFLICT DO NOTHING. Iyzico retries; we replay safely. Failures are logged at WARN.
- **"What's the next sprint?"** Production deploy (Railway + Vercel + Supabase prod) + 2 pilot customers + first paying customer (Bluedev itself).

---

## After the demo

- Note in `docs/sprint-3-retro.md` anything that broke or surprised the audience.
- File any new bug as a GitHub issue with the `sprint-3-bug` label.
- Schedule the Sprint 4 kickoff for Monday 09:00.
