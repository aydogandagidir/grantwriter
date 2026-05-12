# Local Dev Test Guide

> End-to-end browser test for the Sprint 1–4 flow **without** a production Supabase project. Uses the official Supabase CLI to run Auth + Postgres + Storage locally; the FastAPI backend + Next.js frontend connect to those local services exactly like they will in production.
>
> Tested on Windows 11 + Docker Desktop; Linux/macOS instructions are identical except for path separators.

---

## What you'll have running

| Service | Port | Source |
|---|---|---|
| Supabase Postgres | 54322 | `supabase start` |
| Supabase Auth (GoTrue) | 54321 | `supabase start` |
| Supabase Studio | 54323 | `supabase start` |
| FastAPI backend | 8000 | `poetry run uvicorn` |
| Celery worker | n/a | `poetry run celery -A src.tasks.celery_app worker` |
| Next.js frontend | 3000 | `pnpm dev` |
| Redis (sse/queue/cache) | 6379 | `docker compose up redis` (or existing `bluedev-redis` container) |

After setup you click through `http://localhost:3000` exactly like a pilot user would, with real Supabase Auth + real RLS in play.

---

## One-time setup (~15 min)

### 1. Install Supabase CLI

```powershell
# Windows (Scoop):
scoop bucket add supabase https://github.com/supabase/scoop-bucket.git
scoop install supabase

# macOS:
brew install supabase/tap/supabase

# Linux: see https://supabase.com/docs/guides/local-development/cli/getting-started
```

Verify:

```powershell
supabase --version  # >= 1.x
```

### 2. Initialize + start the local stack

From the repo root:

```powershell
cd infra
supabase init     # creates supabase/config.toml if it doesn't exist already
supabase start    # downloads images on first run (~2 min)
```

`supabase start` prints a credentials block when it's done — **copy the four lines below**, you'll paste them into `.env.local` files:

```
API URL:        http://127.0.0.1:54321
DB URL:         postgresql://postgres:postgres@127.0.0.1:54322/postgres
Studio URL:     http://127.0.0.1:54323
anon key:       eyJh…<long JWT>
service_role:   eyJh…<long JWT>
JWT secret:     super-secret-jwt-token-with-at-least-32-characters
```

> The JWT secret is hard-coded by the CLI in dev mode — same string for every dev laptop. Don't memorize it; it's *only* the local-test value.

### 3. Apply migrations to the local Supabase DB

The Supabase CLI symlinks `infra/supabase/migrations/` to its own migration folder, so:

```powershell
supabase db reset    # drops + recreates + applies every migration in lexical order
```

(Or, if you'd rather keep your local DB:)

```powershell
supabase db push
```

When this finishes, the local DB has every table the production schema has, including the 5-row `programmes` seed.

### 4. Wire the backend

Copy `apps/api/.env.example` to `apps/api/.env` (gitignored), then paste:

```
APP_ENV=development
LOG_LEVEL=INFO

DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
REDIS_URL=redis://localhost:6379/0

SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_SERVICE_ROLE_KEY=<service_role from supabase start>
SUPABASE_JWT_SECRET=super-secret-jwt-token-with-at-least-32-characters
SUPABASE_JWT_AUDIENCE=authenticated
SUPABASE_JWT_ALGORITHM=HS256

# Test mode for the LLM stack — leave platform keys blank; BYOK or
# whatever the user pastes in `/settings/llm-config` will be used. Set
# placeholders so Settings boots without raising.
ANTHROPIC_API_KEY=sk-ant-dev-placeholder
OPENAI_API_KEY=sk-proj-dev-placeholder
LLM_MASTER_ENCRYPTION_KEY=local-dev-master-key-32-bytes-pad

# Optional services — leave unset to disable.
# IYZICO_*=...
# RESEND_API_KEY=...
# SENTRY_DSN=...
```

### 5. Wire the frontend

Copy or create `apps/web/.env.local`:

```
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key from supabase start>
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 6. Make sure Redis is up

The `make dev` target boots Redis via `docker compose`. If you already have `bluedev-redis` running from earlier work (e.g. `docker ps` shows it healthy), skip this.

```powershell
docker compose -f infra/docker-compose.yml up -d redis
```

---

## Daily startup (3 terminals)

```powershell
# Terminal A — backend
cd apps/api
poetry install        # only on first run
poetry run uvicorn src.main:app --reload --port 8000

# Terminal B — Celery worker (only needed when you exercise /generate)
cd apps/api
poetry run celery -A src.tasks.celery_app worker --loglevel=info

# Terminal C — frontend
cd apps/web
pnpm install          # only on first run
pnpm dev
```

Open `http://localhost:3000`.

---

## First-time user flow

1. Click **"Sign up"** at the top right.
2. Enter any email + password. Local Supabase Auth doesn't send a verification email by default — you'll be logged in immediately.
3. The app redirects to `/onboarding` because the `public.users` row hasn't been written yet (that's the Sprint 4 onboarding-wizard backlog item). **Switch to a terminal and run:**

   ```powershell
   cd apps/api
   poetry run python scripts/seed_dev_user.py
   ```

   This picks up the newest `auth.users` row (the one you just signed up with), creates a fresh tenant, sets your role to `owner`, and links `public.users` to your Supabase auth user.

4. Reload `http://localhost:3000` — the layout now finds the `public.users` row and routes you to the dashboard.

5. From here you can:
   - Open **Settings → LLM Keys** and paste a real Anthropic / OpenAI key (your own — BYOK). Without one the `/generate` route fails because no key is wired.
   - Open **Settings → Members / Invitations** and verify the admin flows.
   - Click **"New proposal"** → pick a programme + language → land on `/proposals/[id]/brief` → save → click "Generate draft" → watch the SSE event timeline → read the markdown sections that come back → click "Export DOCX".

> If `/generate` returns 503 about the LLM router, check that `ANTHROPIC_API_KEY` is set in `apps/api/.env`, OR that `Settings → LLM Keys` shows a saved BYOK pair. The router needs at least one provider key.

---

## Inviting a second user (multi-tenant test)

1. As the owner, go to **Settings → Invitations** and create an invite for a second email (e.g. `bob@example.com`). Copy the token shown.
2. Open a **private browser window** (so cookies don't collide) and visit `http://localhost:3000/invitations/<paste token here>`.
3. Sign up at the prompt with `bob@example.com`.
4. The invitation accept flow links Bob's Supabase user to the owner's tenant — no `seed_dev_user.py` run needed for the invitee.

---

## Resetting state

```powershell
# Drop everything in the local Supabase DB + reapply migrations from scratch.
cd infra
supabase db reset

# Then re-seed the first user:
cd ../apps/api
poetry run python scripts/seed_dev_user.py
```

---

## Common gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| FE redirects to `/onboarding` after every login | `public.users` row never landed | Re-run `scripts/seed_dev_user.py` |
| Backend 401 on every request | `SUPABASE_JWT_SECRET` mismatch between `.env` and the CLI's JWT secret | Re-copy the JWT secret from `supabase start`'s output |
| `/generate` returns "LLM_MASTER_ENCRYPTION_KEY not configured" | Backend boot used a stale `.env` | Restart `uvicorn` |
| SSE stream never opens, `/proposals/[id]/stream` returns 503 | Redis isn't running | `docker compose up -d redis` |
| Pop-up "Site can't be reached" on `127.0.0.1:54321/auth/v1/token` | Supabase CLI stopped | `supabase start` again |
| Celery task never runs | Worker terminal closed | Re-launch Celery (Terminal B) |

---

## When to graduate off this guide

Once you have a real Supabase production project (Sprint 4 Day 16 Aşama B), you point `SUPABASE_*` envs at it, drop `supabase start`, and the FE talks to the same backend without any code change. The seed script's job is taken over by the onboarding wizard (Sprint 5).
