"""Pre-startup validation that catches misconfigured production deploys.

Runs at FastAPI lifespan startup BEFORE any other resource (DB pool,
Sentry, etc.) is opened. Three layered checks per REQUIRED var:

1. **Set + non-empty.** Empty / missing env => deploy aborts immediately.
2. **No ``<placeholder>`` substring.** The single most common
   copy-paste artifact (cost us a deploy cycle when literal
   ``postgres.<ref>`` reached the Supabase pooler).
3. **DSN-shape sanity** for ``DATABASE_URL``: host not wrapped in
   ``[...]`` unless the inner text is a real IPv6 literal. Python's
   stricter ``urlsplit`` raises ``ValueError`` on bracketed-hostname
   DSNs, crashing ``asyncpg.create_pool`` mid-boot. ``db.py``
   already normalises this defensively at the DSN layer; catching it
   here gives the operator a one-line message instead of a stack
   trace deep inside ``urllib.parse``.

Two operator-controlled outcomes on failure:

* **Warn-only (default).** Errors are logged to stderr with a clear
  ``WARN`` prefix; the function returns and the lifespan continues.
  This is the safe default during a phased rollout where the secrets
  matrix lands in waves (a strict gate during that window boot-loops
  a partially-configured container — exactly what happened when PR #33
  first shipped against the still-partial production env).
* **Strict (``PREFLIGHT_STRICT={true,1,yes,on}``).** Errors raise
  ``SystemExit(1)`` and the host (Render / Railway / Fly) surfaces a
  one-line actionable failure in the deploy log — no more digging
  through asyncpg or jwt tracebacks. Flip this on once every
  ``# ── REQUIRED ──`` var in ``.env.production.example`` is wired.

Why a Python preflight in the lifespan (and not just the bash script):
the bash script at ``scripts/preflight-check.sh`` requires the host's
start command to be wired through it — fragile, depends on the
operator's dashboard config. The lifespan path is part of the app
itself: it always runs, regardless of how the operator configures the
host. The bash script remains useful for CI / pre-push checks.
"""

from __future__ import annotations

import ipaddress
import os
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

# ── Parser patterns (also implemented in scripts/preflight-check.sh) ──

_REQUIRED_HEADER = re.compile(r"^\s*#\s*──+\s*REQUIRED", re.IGNORECASE)
_OPTIONAL_HEADER = re.compile(r"^\s*#\s*──+\s*OPTIONAL", re.IGNORECASE)
_KV_LINE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=")

# ── Value-quality patterns ────────────────────────────────────────────

# Permissive — matches every placeholder shape the example file ships
# (``<project-ref>``, ``<openssl rand -base64 32>``, ``<rotate-via-...>``).
_PLACEHOLDER = re.compile(r"<[^>]+>")

# Captures a netloc segment of the form ``@[...]``. The inner text MUST
# be a valid IPv6 literal — anything else (a hostname accidentally
# left in IPv6-template brackets) crashes urlsplit on Python 3.11+.
_BRACKETED_HOST = re.compile(r"@\[([^\]]+)\]")


@dataclass(frozen=True)
class PreflightError:
    """One actionable problem found during preflight.

    Surfaced to stderr so the host's deploy log shows exactly which
    variable is wrong and why — no need for the operator to trace a
    runtime stack frame.
    """

    key: str
    message: str

    def format_line(self) -> str:
        return f"  - {self.key}: {self.message}"


def _example_path() -> Path:
    """Resolve ``apps/api/.env.production.example`` regardless of CWD.

    ``preflight.py`` lives at ``apps/api/src/core/preflight.py``, so
    ``parents[2]`` lands on ``apps/api/`` both in dev (running from
    repo root) and inside the Docker image (where the file is copied
    to ``/app/.env.production.example`` and this module lives at
    ``/app/src/core/preflight.py``).
    """

    return Path(__file__).resolve().parents[2] / ".env.production.example"


def parse_required_keys(example: Path) -> list[str]:
    """Extract REQUIRED keys from an ``.env.production.example`` file.

    Parser contract (documented at the top of the example file,
    enforced by tests):

    * ``# ── REQUIRED ──`` opens the required block.
    * ``# ── OPTIONAL`` (any continuation) closes it.
    * Inside the block, every ``KEY=...`` line registers ``KEY``.
    * Comments and blank lines are skipped.
    """

    keys: list[str] = []
    in_required = False
    for line in example.read_text(encoding="utf-8").splitlines():
        if _REQUIRED_HEADER.search(line):
            in_required = True
            continue
        if _OPTIONAL_HEADER.search(line):
            in_required = False
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if in_required:
            match = _KV_LINE.match(line)
            if match:
                keys.append(match.group(1))
    return keys


def check_env(
    required_keys: Iterable[str],
    env: Mapping[str, str] | None = None,
) -> list[PreflightError]:
    """Validate the environment against the required-key list.

    Layered checks per key (first failure for a key short-circuits the
    rest — a missing var can't simultaneously contain a placeholder).
    Pure: no IO, no globals; ``env`` defaults to a snapshot of
    ``os.environ`` so callers can pass a fixture dict from tests.
    """

    snapshot: Mapping[str, str] = os.environ if env is None else env
    errors: list[PreflightError] = []

    for key in required_keys:
        value = snapshot.get(key, "")
        if not value:
            errors.append(PreflightError(key, "required but not set"))
            continue

        placeholder = _PLACEHOLDER.search(value)
        if placeholder:
            errors.append(
                PreflightError(
                    key,
                    f"contains a placeholder `{placeholder.group(0)}` — "
                    "substitute the real value from the secret matrix",
                )
            )
            continue

        if key == "DATABASE_URL":
            dsn_error = _check_database_url_shape(value)
            if dsn_error is not None:
                errors.append(dsn_error)
                continue

    return errors


def _check_database_url_shape(dsn: str) -> PreflightError | None:
    """Catch the bracketed-non-IPv6-host DSN that crashes ``urlsplit``.

    A real IPv6 DSN looks like ``postgresql://u:p@[2406:da18::1]:5432/x``
    — Python's ``urlsplit`` accepts that. Replacing the IPv6 with a
    hostname (``@[aws-1-...pooler.supabase.com]:5432``) — a common
    mistake when adapting the IPv6 template to the Supabase pooler URL
    — makes ``urlsplit`` raise ``ValueError`` and aborts startup
    before ``db.py`` ever sees the DSN.
    """

    match = _BRACKETED_HOST.search(dsn)
    if match is None:
        return None
    host = match.group(1)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return PreflightError(
            "DATABASE_URL",
            f"host wrapped in [...] but `{host}` is not an IPv6 literal — "
            "remove the brackets (the IPv6 template syntax doesn't apply "
            "to hostname pooler URLs)",
        )
    return None


_STRICT_ENV_VAR = "PREFLIGHT_STRICT"
_STRICT_TRUTHY = frozenset({"true", "1", "yes", "on"})


def _strict_mode_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Operator opts into hard-fail behaviour via PREFLIGHT_STRICT={true,1,yes,on}.

    Default (unset / anything else) is warn-only — see ``run_preflight``.
    """

    snapshot: Mapping[str, str] = os.environ if env is None else env
    return snapshot.get(_STRICT_ENV_VAR, "").strip().lower() in _STRICT_TRUTHY


def run_preflight() -> None:
    """Top-level entry — parse the example, check the env, log or exit.

    Called from the FastAPI lifespan when ``APP_ENV == "production"``.

    Two operator-controlled modes:

    * **Warn-only (default).** Errors are logged to stderr with a clear
      "WARN" prefix, then the function RETURNS — the lifespan continues
      to ``init_observability`` / ``create_pool``. This is the safe
      default during a phased Sprint 4 rollout where the secrets matrix
      lands in waves (Supabase first, Iyzico/Resend/Sentry later); a
      strict gate during that window would boot-loop a partially-
      configured container and take the deploy down.
    * **Strict (opt-in via ``PREFLIGHT_STRICT={true,1,yes,on}``).**
      Errors trigger ``SystemExit(1)``, exiting the container with an
      actionable list in the deploy log. Flip this on once every
      ``# ── REQUIRED ──`` var in ``.env.production.example`` is wired.

    Raises:
      * ``SystemExit(1)`` — only in strict mode, on any check failure.
      * ``SystemExit(2)`` — packaging bug (example file missing /
        malformed). Distinct exit code so ops can tell the two apart;
        always raised regardless of strict mode because the app can't
        boot without the example file.

    Returns silently on success or in warn-only mode after logging.
    """

    example = _example_path()
    if not example.is_file():
        print(
            f"preflight: cannot find env example at {example} — packaging bug "
            "(Dockerfile must COPY .env.production.example into /app/)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    required = parse_required_keys(example)
    if not required:
        print(
            f"preflight: parsed 0 required keys from {example} — "
            "example file malformed (no '# ── REQUIRED ──' header?)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    errors = check_env(required)
    if not errors:
        # stdout (not stderr) so it shows in the normal deploy log alongside
        # uvicorn's startup banner, not flagged as an error.
        print(
            f"preflight: all {len(required)} required production env vars OK",
            file=sys.stdout,
        )
        return

    strict = _strict_mode_enabled()
    severity = "ERROR" if strict else "WARN"
    print(
        f"preflight: {len(errors)} env {severity}(s) of {len(required)} required vars:",
        file=sys.stderr,
    )
    for err in errors:
        print(err.format_line(), file=sys.stderr)

    if strict:
        print(
            "\npreflight: fix the env in your host's dashboard and redeploy.\n"
            "preflight: required vars documented in apps/api/.env.production.example",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(
        "\npreflight: continuing boot in warn-only mode "
        f"({_STRICT_ENV_VAR} is unset or not truthy).\n"
        f"preflight: set {_STRICT_ENV_VAR}=true once every required var is wired.",
        file=sys.stderr,
    )


if __name__ == "__main__":  # pragma: no cover
    run_preflight()
