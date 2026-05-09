"""Server-Sent Events publisher for the orchestrator saga.

Channel convention: ``proposal:{uuid}``. The :class:`SSEPublisher` is a
single class with an optional Redis client — when omitted (tests, smoke
deploys without Redis), events are recorded on ``self.events`` so the
saga can still publish without crashing and tests can assert against the
recorded list directly.

Production wiring: the Celery worker passes a ``redis.asyncio.Redis``
client into the publisher at saga-start time. The SSE consumer endpoint
(``GET /proposals/{id}/stream``) subscribes to the same channel via the
same Redis instance.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    import redis.asyncio as redis_asyncio

logger = logging.getLogger(__name__)


class SSEPublisher:
    """Publishes saga progress events to Redis Pub/Sub.

    A single instance is bound to one ``proposal_id`` for its lifetime —
    construct a fresh publisher per saga run. The publisher is safe to
    pass into the orchestrator from a Celery task; it does not hold any
    server-side state beyond the Redis client reference.
    """

    def __init__(
        self,
        proposal_id: UUID,
        redis_client: redis_asyncio.Redis | None = None,
    ) -> None:
        self.proposal_id = proposal_id
        self.channel = channel_for(proposal_id)
        self._redis = redis_client
        self.events: list[dict[str, Any]] = []
        """Recorded events — populated unconditionally so tests can assert
        what the saga emitted, regardless of whether Redis is wired."""

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish one event.

        The envelope shape matches the SSE consumer's expectation:
        ``{"event": <type>, "data": <dict>, "id": <uuid>}``. The id is
        a freshly minted uuid4 per call — used by EventSource clients to
        request resume via the ``Last-Event-ID`` header (resume itself
        is best-effort and lands with the proposal_events table in
        Sprint 3).
        """

        envelope: dict[str, Any] = {
            "event": event_type,
            "data": data,
            "id": str(uuid.uuid4()),
        }
        self.events.append(envelope)

        if self._redis is None:
            return

        try:
            await self._redis.publish(self.channel, json.dumps(envelope))
        except Exception:
            # Pub/sub failures must not abort the saga — the saga's
            # primary contract is to drive the agents and persist their
            # outputs to Postgres. SSE is observability sugar; if Redis
            # is unhealthy the consumer just won't see live events,
            # they can poll the proposal row instead.
            logger.exception(
                "sse_publisher_publish_failed",
                extra={
                    "proposal_id": str(self.proposal_id),
                    "event_type": event_type,
                },
            )


def channel_for(proposal_id: UUID) -> str:
    """Canonical channel name for a proposal — single source of truth.

    Both the publisher (``DraftGenerator``) and the subscriber (the SSE
    endpoint) call into this so they can never drift on the format.
    """

    return f"proposal:{proposal_id}"


__all__ = ["SSEPublisher", "channel_for"]
