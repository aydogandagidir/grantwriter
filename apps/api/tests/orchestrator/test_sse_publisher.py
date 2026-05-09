"""SSEPublisher unit tests.

Two concerns: the channel-name convention (load-bearing — both the
saga publisher and the endpoint subscriber compute it from the same
helper) and the recording behavior under both no-redis and
redis-failing paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from src.orchestrator.sse_publisher import SSEPublisher, channel_for


def test_channel_for_format() -> None:
    pid = UUID("12345678-1234-5678-1234-567812345678")
    assert channel_for(pid) == "proposal:12345678-1234-5678-1234-567812345678"


async def test_publish_records_event_without_redis() -> None:
    """Without a redis client, events still record in self.events.

    Tests assert against this list directly. The non-redis mode also
    keeps the saga functional in test environments without a broker.
    """

    pub = SSEPublisher(uuid4())
    await pub.publish("agent_started", {"agent": "call_analyst"})
    await pub.publish("agent_completed", {"agent": "call_analyst", "duration_ms": 42})

    assert len(pub.events) == 2
    assert pub.events[0]["event"] == "agent_started"
    assert pub.events[0]["data"] == {"agent": "call_analyst"}
    assert "id" in pub.events[0]
    assert pub.events[1]["event"] == "agent_completed"


async def test_publish_calls_redis_when_provided() -> None:
    """With a redis client, publish() pushes a JSON envelope to the channel."""

    redis_client = AsyncMock()
    pid = uuid4()
    pub = SSEPublisher(pid, redis_client=redis_client)

    await pub.publish("test_event", {"foo": "bar"})

    assert redis_client.publish.call_count == 1
    args = redis_client.publish.await_args
    assert args is not None
    assert args.args[0] == f"proposal:{pid}"
    payload_json = args.args[1]
    assert "test_event" in payload_json
    assert "foo" in payload_json


async def test_publish_swallows_redis_failure() -> None:
    """A failing Redis publish must not abort the saga.

    Pub/sub is observability sugar — the saga's primary contract is to
    persist agent outputs to Postgres. Redis hiccups should be logged
    and swallowed.
    """

    redis_client = AsyncMock()
    redis_client.publish = AsyncMock(side_effect=ConnectionError("redis down"))

    pub = SSEPublisher(uuid4(), redis_client=redis_client)
    # Should NOT raise.
    await pub.publish("ok", {})

    assert len(pub.events) == 1  # event still recorded in memory
