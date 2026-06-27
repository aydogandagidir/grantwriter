# Bluedev GrantWriter — Geliştirici komutları
# Kaynak: docs/10-deployment-devops.md §3

.PHONY: help dev test lint migrate seed

help:
	@echo "Bluedev GrantWriter — Geliştirici komutları"
	@echo ""
	@echo "  make dev      — Postgres + Redis + Mailhog (Docker), API, Worker, Web ayağa kalkar"
	@echo "  make test     — apps/api (pytest) ve apps/web (pnpm test)"
	@echo "  make lint     — ruff + mypy (api), eslint + tsc (web)"
	@echo "  make migrate  — Supabase migration'larını uygular"
	@echo "  make seed     — Geliştirme verisi ekler"

dev:
	docker compose -f infra/docker-compose.yml up -d
	cd apps/api && poetry run uvicorn src.main:app --reload --port 8000 &
	cd apps/api && poetry run celery -A src.tasks.celery_app worker --loglevel=info &
	cd apps/web && pnpm dev

test:
	cd apps/api && poetry run pytest
	cd apps/web && pnpm test

lint:
	cd apps/api && poetry run ruff check . && poetry run mypy src
	cd apps/web && pnpm lint && pnpm typecheck

migrate:
	supabase db reset --linked

seed:
	cd apps/api && poetry run python scripts/seed_dev_data.py
