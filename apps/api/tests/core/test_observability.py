"""Tests for the observability module.

Three concerns:

A. **Init contract** — with no DSN/token, both subsystems return
   ``False`` cleanly. The kill-switch trumps any DSN.
B. **PII scrubber** — the most important assertion of the file: the
   :func:`scrub_event` hook redacts BYOK keys and JWT-shaped tokens
   wherever they appear (top-level message, nested dict, list item).
C. **Lazy import** — when the SDK isn't installed but the DSN IS set,
   init reports ``False`` with a "not installed" reason instead of
   crashing.

The init tests don't actually need the SDKs to be present (they use
the no-DSN path or simulate import failure via monkeypatch). The
scrubber tests are pure-Python.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr
from src.core.config import Settings
from src.core.observability import (
    InitReport,
    init_observability,
    scrub_event,
)


def _make_settings(**overrides: Any) -> Settings:
    return Settings(**overrides)


# ── PII scrubber ───────────────────────────────────────────────────────


def test_scrub_event_redacts_anthropic_key_in_message() -> None:
    event = {"message": "user gave key sk-ant-CANARY-do-not-leak-7b3e1c-aaaaaa"}
    out = scrub_event(event)
    assert "sk-ant-CANARY" not in out["message"]
    assert "***REDACTED***" in out["message"]


def test_scrub_event_redacts_openai_key_in_extra_dict() -> None:
    event = {
        "extra": {
            "context": "loaded key sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA into router"
        }
    }
    out = scrub_event(event)
    assert "sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in out["extra"]["context"]


def test_scrub_event_redacts_jwt_shaped_token() -> None:
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NSJ9.signature_part_here_padding"
    event = {"breadcrumbs": [{"message": f"got token {jwt}"}]}
    out = scrub_event(event)
    assert jwt not in out["breadcrumbs"][0]["message"]


def test_scrub_event_walks_nested_lists() -> None:
    event = {
        "data": {
            "items": [
                "ok value",
                "leaked sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA here",
            ]
        }
    }
    out = scrub_event(event)
    assert "sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in out["data"]["items"][1]
    assert out["data"]["items"][0] == "ok value"


def test_scrub_event_passes_non_string_scalars_unchanged() -> None:
    event = {"count": 42, "ratio": 1.5, "ok": True, "nothing": None}
    out = scrub_event(event)
    assert out == {"count": 42, "ratio": 1.5, "ok": True, "nothing": None}


# ── Init: no DSN / token ───────────────────────────────────────────────


def test_init_returns_disabled_when_kill_switch_off() -> None:
    settings = _make_settings(observability_enabled=False)
    report = init_observability(settings)
    assert isinstance(report, InitReport)
    assert report.sentry_enabled is False
    assert report.logtail_enabled is False
    assert "OBSERVABILITY_ENABLED" in (report.reason_sentry or "")
    assert "OBSERVABILITY_ENABLED" in (report.reason_logtail or "")


def test_init_skips_sentry_when_dsn_unset() -> None:
    settings = _make_settings(sentry_dsn=None)
    report = init_observability(settings)
    assert report.sentry_enabled is False
    assert report.reason_sentry == "SENTRY_DSN not configured"


def test_init_skips_logtail_when_token_unset() -> None:
    settings = _make_settings(logtail_token=None)
    report = init_observability(settings)
    assert report.logtail_enabled is False
    assert report.reason_logtail == "LOGTAIL_TOKEN not configured"


# ── Init: SDK not installed ────────────────────────────────────────────


def test_sentry_init_reports_missing_sdk_when_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate ``import sentry_sdk`` failing — init must NOT crash."""

    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sentry_sdk":
            raise ImportError("no sentry-sdk in this env")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    settings = _make_settings(sentry_dsn=SecretStr("https://x@sentry.io/1"))
    report = init_observability(settings)
    assert report.sentry_enabled is False
    assert report.reason_sentry == "sentry-sdk not installed"


def test_logtail_init_reports_missing_sdk_when_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "logtail":
            raise ImportError("no logtail-python in this env")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    settings = _make_settings(logtail_token=SecretStr("source-token-xyz"))
    report = init_observability(settings)
    assert report.logtail_enabled is False
    assert report.reason_logtail == "logtail-python not installed"


# ── Init: SDK present (mock the module so we don't need real packages) ──


def test_sentry_init_calls_sdk_init_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject a fake sentry_sdk module — verify init() is called with the
    right args + the before_send hook is our scrubber."""

    import sys
    import types

    captured: dict[str, Any] = {}

    fake_module = types.ModuleType("sentry_sdk")

    def fake_init(**kwargs: Any) -> None:
        captured.update(kwargs)

    fake_module.init = fake_init  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_module)

    settings = _make_settings(
        sentry_dsn=SecretStr("https://abc@sentry.io/1"),
        sentry_environment="staging",
        sentry_traces_sample_rate=0.1,
    )
    report = init_observability(settings)
    assert report.sentry_enabled is True
    assert captured["dsn"] == "https://abc@sentry.io/1"
    assert captured["environment"] == "staging"
    assert captured["traces_sample_rate"] == 0.1
    assert captured["send_default_pii"] is False
    # The before_send hook is our scrubber — verify it actually scrubs.
    redacted = captured["before_send"](
        {"message": "leak sk-ant-XYZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
        None,
    )
    assert "sk-ant-XYZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in redacted["message"]
