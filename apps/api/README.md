# Bluedev GrantWriter — API

FastAPI tabanlı backend. 7 ajanlı orchestration, RAG, citation grounding, doküman üretimi.

## Durum

Placeholder — gerçek bağımlılıklar ve `src/main.py` **S1.D1.T2** görevinde gelecek.

## Ön Koşullar

- Python 3.11+
- Poetry 1.8+
- Docker (lokal Postgres + Redis için)

## Kurulum (S1.D1.T2 sonrası)

```bash
poetry install
poetry run uvicorn src.main:app --reload --port 8000
```

## Test

```bash
poetry run pytest
```

## Lint / Tipler

```bash
poetry run ruff check .
poetry run ruff format .
poetry run mypy src
```

Ayrıntı için kök dizindeki `CLAUDE.md` ve `docs/02-architecture.md` (Komponent Detayları).
