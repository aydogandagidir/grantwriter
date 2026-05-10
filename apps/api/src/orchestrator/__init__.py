"""Saga orchestrator for the 7-agent draft generation flow.

Per docs/06-agent-architecture.md §5. The orchestrator coordinates the
agents, publishes progress to Redis Pub/Sub for the SSE stream, and
persists each step's outputs to the proposals row.

Entry points:
- :class:`DraftGenerator` — the saga itself, async-first.
- :class:`SSEPublisher` — the progress event publisher used by the saga
  and consumed by the ``/proposals/{id}/stream`` endpoint.
- :func:`generate_draft_task` — the Celery wrapper (lives in
  :mod:`src.tasks.orchestrator` to keep Celery imports out of the API
  process when it's not needed).
"""

from __future__ import annotations

from src.orchestrator.draft_generator import (
    DraftGenerator,
    DraftGeneratorResult,
    DraftStatus,
    RecoverableError,
    UnrecoverableError,
    build_default_agents,
)
from src.orchestrator.sse_publisher import SSEPublisher

__all__ = [
    "DraftGenerator",
    "DraftGeneratorResult",
    "DraftStatus",
    "RecoverableError",
    "SSEPublisher",
    "UnrecoverableError",
    "build_default_agents",
]
