"""Tests for Settings.cors_origins parsing.

cors_origins is the load-bearing CORS allow-list. Pydantic-settings v2
JSON-decodes ``list[str]`` env vars by default, which is brittle: a
comma-separated value raises at boot and a malformed JSON array silently
yields ``[]`` (CORS middleware then never mounts → every browser request
from the frontend is blocked, with no server-side error to point at).

We disable that decoding via ``NoDecode`` and own the parsing. These
tests pin the contract: JSON array, comma-separated, single origin, and
empty all produce the right list.
"""

from __future__ import annotations

import pytest
from src.core.config import Settings


def _settings_with_cors(value: str) -> Settings:
    """Build a Settings reading CORS_ORIGINS from the env, no .env file."""

    return Settings(cors_origins=value, _env_file=None)  # type: ignore[arg-type]


def test_cors_json_array() -> None:
    s = _settings_with_cors('["https://a.com","https://b.com"]')
    assert s.cors_origins == ["https://a.com", "https://b.com"]


def test_cors_comma_separated() -> None:
    s = _settings_with_cors("https://a.com,https://b.com,https://c.com")
    assert s.cors_origins == ["https://a.com", "https://b.com", "https://c.com"]


def test_cors_comma_separated_with_spaces() -> None:
    s = _settings_with_cors("https://a.com , https://b.com")
    assert s.cors_origins == ["https://a.com", "https://b.com"]


def test_cors_single_origin() -> None:
    s = _settings_with_cors("https://a.com")
    assert s.cors_origins == ["https://a.com"]


def test_cors_empty_string() -> None:
    s = _settings_with_cors("")
    assert s.cors_origins == []


def test_cors_malformed_json_falls_back_to_csv() -> None:
    # Missing closing bracket: not valid JSON. We fall back to the
    # comma split rather than silently dropping everything. The leading
    # '[' is preserved on the first token — acceptable; the point is we
    # don't return [] and kill CORS entirely.
    s = _settings_with_cors('["https://a.com", "https://b.com"')
    assert s.cors_origins  # non-empty
    assert any("b.com" in origin for origin in s.cors_origins)


def test_cors_default_is_empty_list() -> None:
    s = Settings(_env_file=None)
    assert s.cors_origins == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://app.vercel.app", ["https://app.vercel.app"]),
        ("a,b,c", ["a", "b", "c"]),
        ("  a  ,  b  ", ["a", "b"]),
        ("a,,b", ["a", "b"]),  # empty members dropped
        ('["x"]', ["x"]),
    ],
)
def test_cors_parametrised(raw: str, expected: list[str]) -> None:
    assert _settings_with_cors(raw).cors_origins == expected
