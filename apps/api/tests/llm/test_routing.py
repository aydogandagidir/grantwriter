"""Routing + fallback + retry behaviour using FakeProvider — no SDK calls."""

from __future__ import annotations

from src.llm.base import (
    TASK_ROUTES,
    LLMRetryableError,
    LLMUnrecoverableError,
    calculate_cost,
    route_for,
)

from tests.llm.conftest import (
    FakeProvider,
    build_router,
    make_request,
    make_response,
)

# ── Task → model mapping ────────────────────────────────────────────────


def test_task_routes_match_doc_06_table() -> None:
    """docs/06-agent-architecture.md §7.2 is the source of truth."""

    expected: dict[str, tuple[str, str, str, str]] = {
        "call_analyst": ("claude", "claude-opus-4-7", "openai", "gpt-4o"),
        "excellence_writer": ("claude", "claude-opus-4-7", "openai", "gpt-4o"),
        "impact_writer": ("claude", "claude-opus-4-7", "openai", "gpt-4o"),
        "implementation_writer": ("claude", "claude-opus-4-7", "openai", "gpt-4o"),
        "compliance_reviewer": (
            "claude",
            "claude-sonnet-4-6",
            "openai",
            "gpt-4o-mini",
        ),
        "hallucination_hunter": (
            "claude",
            "claude-sonnet-4-6",
            "openai",
            "gpt-4o-mini",
        ),
    }
    for task, (pp, pm, fp, fm) in expected.items():
        entry = TASK_ROUTES[task]  # type: ignore[index]
        assert (
            entry.primary_provider == pp
            and entry.primary_model == pm
            and entry.fallback_provider == fp
            and entry.fallback_model == fm
        ), task


# ── Happy path ──────────────────────────────────────────────────────────


async def test_complete_routes_to_primary_provider() -> None:
    primary = FakeProvider(
        "claude",
        [make_response(text="from claude", model="claude-opus-4-7", provider="claude")],
    )
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    req = make_request(task="call_analyst")
    response = await router.complete(req)

    assert response.text == "from claude"
    assert response.provider == "claude"
    assert response.model == "claude-opus-4-7"
    assert response.used_byok is False  # platform key path
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 0


async def test_complete_uses_correct_model_per_task() -> None:
    primary = FakeProvider(
        "claude",
        [make_response(model="claude-sonnet-4-6", provider="claude")],
    )
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    response = await router.complete(make_request(task="compliance_reviewer"))

    assert response.model == "claude-sonnet-4-6"
    _, model, _ = primary.calls[0]
    assert model == "claude-sonnet-4-6"


# ── Retry logic ─────────────────────────────────────────────────────────


async def test_retries_transient_errors_then_succeeds() -> None:
    primary = FakeProvider(
        "claude",
        [
            LLMRetryableError("429 rate limit"),
            LLMRetryableError("503 transient"),
            make_response(text="finally", model="claude-opus-4-7", provider="claude"),
        ],
    )
    fallback = FakeProvider("openai", [])
    router = build_router(
        providers={"claude": primary, "openai": fallback},
        max_retries=3,
    )

    response = await router.complete(make_request())

    assert response.text == "finally"
    assert len(primary.calls) == 3
    assert len(fallback.calls) == 0


async def test_retry_budget_exhausted_falls_back_to_secondary() -> None:
    primary = FakeProvider(
        "claude",
        [
            LLMRetryableError("first"),
            LLMRetryableError("second"),
            LLMRetryableError("third"),
        ],
    )
    fallback = FakeProvider(
        "openai",
        [make_response(text="rescued", model="gpt-4o", provider="openai")],
    )
    router = build_router(
        providers={"claude": primary, "openai": fallback},
        max_retries=3,
    )

    response = await router.complete(make_request(task="call_analyst"))

    assert response.text == "rescued"
    assert response.provider == "openai"
    assert len(primary.calls) == 3
    assert len(fallback.calls) == 1
    _, fallback_model, _ = fallback.calls[0]
    assert fallback_model == "gpt-4o"


async def test_unrecoverable_skips_remaining_retries() -> None:
    primary = FakeProvider(
        "claude",
        [LLMUnrecoverableError("400 bad request")],
    )
    fallback = FakeProvider(
        "openai",
        [make_response(text="rescued", model="gpt-4o", provider="openai")],
    )
    router = build_router(
        providers={"claude": primary, "openai": fallback},
        max_retries=3,
    )

    response = await router.complete(make_request())

    # Primary exits after the first call (no retries on permanent error).
    assert len(primary.calls) == 1
    assert response.provider == "openai"


# ── Fallback path with both providers permanently failing ───────────────


async def test_both_providers_fail_raises() -> None:
    primary = FakeProvider("claude", [LLMUnrecoverableError("permanent")])
    fallback = FakeProvider("openai", [LLMUnrecoverableError("also permanent")])
    router = build_router(providers={"claude": primary, "openai": fallback})

    import pytest

    with pytest.raises(LLMUnrecoverableError):
        await router.complete(make_request())


# ── Cache hit detection / cost accounting ───────────────────────────────


async def test_cache_hit_response_carries_through() -> None:
    primary = FakeProvider(
        "claude",
        [
            make_response(
                model="claude-opus-4-7",
                provider="claude",
                input_tokens=200,
                cached_tokens=4_800,  # large system block read from cache
                output_tokens=300,
            )
        ],
    )
    router = build_router(
        providers={"claude": primary, "openai": FakeProvider("openai", [])},
    )

    response = await router.complete(make_request(cache_system=True))

    assert response.usage.has_cache_hit
    assert response.usage.cached_tokens == 4_800


def test_cost_calculation_includes_cache_split() -> None:
    """Cache reads are billed at the discounted rate."""

    from src.llm.base import LLMUsage

    full_input = LLMUsage(input_tokens=10_000, output_tokens=2_000)
    cached_split = LLMUsage(input_tokens=2_000, cached_tokens=8_000, output_tokens=2_000)

    full_cost = calculate_cost("claude-opus-4-7", full_input)
    cached_cost = calculate_cost("claude-opus-4-7", cached_split)

    # 8000 cached tokens cost 1.50/M instead of 15/M → meaningful saving.
    assert cached_cost < full_cost
    # And the difference must equal exactly (8000 / 1M) * (15.00 - 1.50).
    expected_saving = (8_000 / 1_000_000) * (15.00 - 1.50)
    assert abs((full_cost - cached_cost) - expected_saving) < 1e-9


def test_unknown_model_costs_zero_not_crash() -> None:
    from src.llm.base import LLMUsage

    cost = calculate_cost("gpt-future-9000", LLMUsage(input_tokens=100, output_tokens=50))
    assert cost == 0.0


# ── Sanity: route_for matches TASK_ROUTES ───────────────────────────────


def test_route_for_returns_table_entry() -> None:
    entry = route_for("excellence_writer")
    assert entry == TASK_ROUTES["excellence_writer"]
