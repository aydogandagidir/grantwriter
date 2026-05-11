"""Sprint 4 Day 16 — preflight contract test.

Three concerns:

A. **Drift between `apps/api/.env.production.example` and `Settings`.**
   When ``Settings`` gains a new production-required field (anything with
   no default OR ``SecretStr | None = None``), the example file MUST
   document it. The first test parses the example and compares against
   the model fields the user-side operator is expected to populate.

B. **Preflight script accepts a fully-populated env.** Run
   ``scripts/preflight-check.sh`` with every required var set to a
   placeholder; assert exit code 0 + the success message on stdout.

C. **Preflight script rejects a missing var.** Strip one required var
   from the otherwise-complete env; assert exit code 1 + the missing
   key surfaces in stderr.

Skips on Windows hosts where bash isn't on PATH — the script is the
runtime Railway uses, not something the dev laptop needs to validate.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import get_args, get_origin

import pytest
from pydantic import SecretStr
from src.core.config import Settings


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent


def _example_path() -> Path:
    return _repo_root() / "apps" / "api" / ".env.production.example"


def _script_path() -> Path:
    return _repo_root() / "scripts" / "preflight-check.sh"


# ── Parser (kept duplicated from preflight-check.sh deliberately) ──────


_REQUIRED_HEADER = re.compile(r"^\s*#\s*──+\s*REQUIRED", re.IGNORECASE)
_OPTIONAL_HEADER = re.compile(r"^\s*#\s*──+\s*OPTIONAL", re.IGNORECASE)
_KV_LINE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=")


def _parse_required_keys(example: Path) -> list[str]:
    """Replicate the bash parser in Python — single source of truth lives
    in the example file itself, both parsers must agree on its meaning."""

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


# ── Tests ──────────────────────────────────────────────────────────────


# Fields that have safe defaults in Settings and intentionally DON'T need
# to be set in production — listed here so test A doesn't false-positive
# when a future commit adds another defaulted Settings field.
_OPTIONAL_BY_DESIGN = {
    "app_env",  # required but lives in the same Required block; covered there
    "log_level",
    "app_version",
    "cors_origins",
    "db_pool_min_size",
    "db_pool_max_size",
    "embedding_model",
    "embedding_dim",
    "supabase_storage_bucket",
    "supabase_jwt_audience",
    "supabase_jwt_algorithm",
    "celery_broker_url",
    "celery_result_backend",
    "iyzico_base_url",  # has default but prod MUST override; example flags it
    "iyzico_callback_url",  # default OK for v1
    "email_from",  # default OK for v1
    "app_url",  # default OK for v1
    "sentry_environment",  # falls back to app_env
    "sentry_traces_sample_rate",
    "sentry_release",  # injected by Railway at deploy time
    "observability_enabled",
    "email_enabled",
}


def _required_settings_fields() -> set[str]:
    """Return UPPER_SNAKE_CASE names of Settings fields that production
    cannot run without."""

    required: set[str] = set()
    for field_name, field_info in Settings.model_fields.items():
        if field_name in _OPTIONAL_BY_DESIGN:
            continue

        annotation = field_info.annotation
        default = field_info.default
        default_factory = field_info.default_factory

        # No real default → required.
        if default is None and default_factory is None:
            required.add(field_name.upper())
            continue

        # Default exists but is a "soft None" (SecretStr | None = None or
        # similar). These are the BYOK-shaped optional types — they MUST be
        # populated in production even though pydantic accepts None.
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is not None and type(None) in args and SecretStr in args:
            required.add(field_name.upper())

    return required


def test_env_example_lists_every_required_settings_field() -> None:
    """The example file MUST cover every required Settings field — when
    Settings gains a new BYOK / DSN-style field, the example must be
    updated in the same commit."""

    example_keys = set(_parse_required_keys(_example_path()))
    settings_required = _required_settings_fields()

    missing_from_example = settings_required - example_keys
    extra_in_example = example_keys - settings_required - {"APP_ENV"}

    # APP_ENV is allowlisted in the example as required (production deploy
    # MUST set it to "production") even though Settings has a default of
    # "development" — the default is dev-friendly, the example reminds
    # operators to flip it.

    assert not missing_from_example, (
        "Settings has required fields the example file doesn't document: "
        f"{sorted(missing_from_example)}. Update apps/api/.env.production.example."
    )
    assert not extra_in_example, (
        "Example file lists keys that aren't required Settings fields: "
        f"{sorted(extra_in_example)}. Either remove them or add them to Settings."
    )


# ── Bash script invocation tests ───────────────────────────────────────


def _bash_available() -> bool:
    """Skip the bash-script tests on hosts that don't ship bash on PATH —
    Windows CI without WSL, etc. Railway runs Linux, so the prod path
    always has bash."""

    return shutil.which("bash") is not None


@pytest.fixture
def full_required_env() -> dict[str, str]:
    """Build a placeholder env that covers every required key."""

    keys = _parse_required_keys(_example_path())
    return {key: "placeholder" for key in keys}


def _run_preflight_with_env(env_vars: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the preflight script with ``env_vars`` set in bash's process.

    We inline the env exports into ``bash -c`` because Git Bash on
    Windows ignores values passed via ``subprocess.run(env=...)`` for a
    chunk of its MSYS-layer translation; ``export FOO=...; ./script`` in
    the bash command string survives both Linux CI and Win32 Git Bash.

    Values are passed through ``shlex.quote`` so a stray space / quote in
    a placeholder doesn't break the export."""

    exports = " ".join(
        f"export {key}={shlex.quote(value)};" for key, value in env_vars.items()
    )
    command = f"{exports} ./scripts/preflight-check.sh"
    return subprocess.run(
        ["bash", "-c", command],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(not _bash_available(), reason="bash not on PATH")
@pytest.mark.skipif(
    sys.platform == "win32" and shutil.which("bash") is not None
    and "git" not in (shutil.which("bash") or "").lower(),
    reason="non-Git-Bash on Windows can't run POSIX scripts",
)
def test_preflight_check_passes_with_full_env(
    full_required_env: dict[str, str],
) -> None:
    """Every required key set → exit 0 + success message."""

    result = _run_preflight_with_env(full_required_env)
    assert result.returncode == 0, (
        f"preflight exited {result.returncode}; stderr:\n{result.stderr}"
    )
    assert "all" in result.stdout.lower()
    assert "required production env vars set" in result.stdout


@pytest.mark.skipif(not _bash_available(), reason="bash not on PATH")
@pytest.mark.skipif(
    sys.platform == "win32" and shutil.which("bash") is not None
    and "git" not in (shutil.which("bash") or "").lower(),
    reason="non-Git-Bash on Windows can't run POSIX scripts",
)
def test_preflight_check_fails_when_one_required_var_missing(
    full_required_env: dict[str, str],
) -> None:
    """Dropping a single required key MUST surface its name in stderr +
    return exit 1 (Railway treats that as a deploy failure)."""

    dropped_key = sorted(full_required_env)[0]
    partial_env = {k: v for k, v in full_required_env.items() if k != dropped_key}

    result = _run_preflight_with_env(partial_env)
    assert result.returncode == 1, (
        f"expected exit 1 with one missing var; got {result.returncode}; "
        f"stderr:\n{result.stderr}"
    )
    assert dropped_key in result.stderr, (
        f"missing key {dropped_key} should appear in stderr:\n{result.stderr}"
    )
