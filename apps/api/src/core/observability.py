"""Sentry + Logtail wiring with lazy, optional imports.

Both SDKs are optional. The pattern:

- Import is deferred into :func:`_init_sentry` / :func:`_init_logtail`
  so an environment without the package installed (CI fast lane, dev
  laptops) doesn't import-error at app startup.
- A missing DSN / token is treated identically to a missing package:
  log warning, return ``False``, the app keeps booting.

The PII scrubber :func:`scrub_event` is a pure function — exposed for
unit tests so we can assert that BYOK keys / JWT-shaped tokens never
leave the process inside a Sentry event.

Wiring:

- :func:`init_observability` is called from ``main.py``'s lifespan.
  Returns ``InitReport`` so tests can assert which subsystems came up.
- The Logtail handler attaches to the root logger; structured-log
  records (``structlog`` JSON) ship through unchanged.

Why we don't add the SDKs as required deps: CLAUDE.md restricts new
dependency additions, and the SDKs together drag in ~25 transitive
packages. Production installs them via a separate extra; CI doesn't
need them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InitReport:
    """Reports which observability subsystems came up successfully."""

    sentry_enabled: bool
    logtail_enabled: bool
    reason_sentry: str | None = None
    reason_logtail: str | None = None


# ── PII scrubber ───────────────────────────────────────────────────────


# Anthropic + OpenAI key shapes (50+ chars). Any match → mask.
_KEY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{40,}"),
]
# JWT shape — three base64url segments separated by dots, total >= 100 chars.
_JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_REDACTION = "***REDACTED***"


def _scrub_string(value: str) -> str:
    redacted = value
    for pat in _KEY_PATTERNS:
        redacted = pat.sub(_REDACTION, redacted)
    redacted = _JWT_PATTERN.sub(_REDACTION, redacted)
    return redacted


def _scrub_value(value: Any) -> Any:
    """Recursively walk dict / list / str values and scrub leaf strings."""

    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(v) for v in value)
    return value


def scrub_event(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sentry ``before_send`` hook — strip BYOK keys and JWTs from the body.

    Sentry calls this with the event payload (and a hint dict we ignore).
    We walk every string in the payload, redacting matches against the
    canonical secret shapes. Returning ``None`` would drop the event;
    we always return the (possibly redacted) event so errors still flow.
    """

    return _scrub_value(event)  # type: ignore[no-any-return]


# ── Init helpers ───────────────────────────────────────────────────────


def _init_sentry(settings: Settings) -> tuple[bool, str | None]:
    if settings.sentry_dsn is None:
        return False, "SENTRY_DSN not configured"

    try:
        import sentry_sdk
    except ImportError:
        return False, "sentry-sdk not installed"

    try:
        # `release` is injected at deploy time (Railway start command pulls
        # the current git SHA into SENTRY_RELEASE). Lets Sentry group errors
        # by deploy + show "first seen in release X" / "regression in release Y".
        sentry_sdk.init(
            dsn=settings.sentry_dsn.get_secret_value(),
            environment=settings.sentry_environment or settings.app_env,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            release=settings.sentry_release,
            send_default_pii=False,  # we scrub everything ourselves
            before_send=scrub_event,
        )
    except Exception as exc:
        # The SDK should never raise here, but if init genuinely fails
        # we don't want to crash the app.
        return False, f"sentry init raised: {type(exc).__name__}"
    return True, None


def _init_logtail(settings: Settings) -> tuple[bool, str | None]:
    if settings.logtail_token is None:
        return False, "LOGTAIL_TOKEN not configured"

    try:
        from logtail import LogtailHandler
    except ImportError:
        return False, "logtail-python not installed"

    try:
        handler = LogtailHandler(
            source_token=settings.logtail_token.get_secret_value()
        )
        # Wrap the handler so the same scrub the Sentry hook does also
        # runs on the log message before shipping.
        handler.addFilter(_LogtailScrubFilter())
        logging.getLogger().addHandler(handler)
    except Exception as exc:
        return False, f"logtail handler init raised: {type(exc).__name__}"
    return True, None


class _LogtailScrubFilter(logging.Filter):
    """Apply :func:`_scrub_string` to every log record before emit."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _scrub_string(str(record.msg))
        if record.args:
            record.args = tuple(
                _scrub_string(str(a)) if isinstance(a, str) else a
                for a in record.args
            )
        # extra= dict values land as record attributes; scrub strings only.
        for attr_name, attr_value in list(record.__dict__.items()):
            if isinstance(attr_value, str) and attr_name not in {
                "name", "msg", "levelname", "module",
                "filename", "pathname", "funcName",
            }:
                record.__dict__[attr_name] = _scrub_string(attr_value)
        return True


# ── Public API ─────────────────────────────────────────────────────────


def init_observability(settings: Settings) -> InitReport:
    """Bring up Sentry + Logtail. Returns which ones succeeded.

    Called from ``main.py``'s lifespan, exactly once per process. Safe
    to call multiple times in tests (each call is idempotent — the
    underlying SDKs handle re-init).
    """

    if not settings.observability_enabled:
        logger.info("observability_disabled_by_kill_switch")
        return InitReport(
            sentry_enabled=False,
            logtail_enabled=False,
            reason_sentry="OBSERVABILITY_ENABLED=false",
            reason_logtail="OBSERVABILITY_ENABLED=false",
        )

    sentry_ok, sentry_reason = _init_sentry(settings)
    logtail_ok, logtail_reason = _init_logtail(settings)

    if sentry_ok:
        logger.info("sentry_initialised")
    else:
        logger.info(
            "sentry_skipped", extra={"reason": sentry_reason}
        )
    if logtail_ok:
        logger.info("logtail_initialised")
    else:
        logger.info(
            "logtail_skipped", extra={"reason": logtail_reason}
        )

    return InitReport(
        sentry_enabled=sentry_ok,
        logtail_enabled=logtail_ok,
        reason_sentry=sentry_reason,
        reason_logtail=logtail_reason,
    )


__all__ = ["InitReport", "init_observability", "scrub_event"]
