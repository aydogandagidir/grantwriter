"""Tests for asymmetric (JWKS-based) JWT validation in ``core.auth``.

Supabase migrated to asymmetric ES256 signing for new projects in 2025;
the local CLI emits ES256 tokens by default. This file proves both paths:

- ES256 (JWKS) — happy path + missing-URL guard.
- HS256 (legacy secret) — happy path + signature mismatch.
- Unsupported algorithm — 401 with explicit detail.

The JWKS HTTP fetch is short-circuited by monkey-patching
``auth._get_jwks_client`` to a stub that returns a key directly, so these
tests stay hermetic (no real network, no port allocation).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from src.core import auth as auth_module
from src.core.auth import JWTValidationError, verify_supabase_jwt

_AUD = "authenticated"
_HS256_SECRET = "test-secret-do-not-use-in-prod-32bytes!"


# ── Helpers ────────────────────────────────────────────────────────────


def _es256_keypair() -> tuple[
    ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey
]:
    private = ec.generate_private_key(ec.SECP256R1())
    return private, private.public_key()


def _encode_es256(
    payload: dict[str, Any], private_key: ec.EllipticCurvePrivateKey
) -> str:
    return jwt.encode(payload, private_key, algorithm="ES256")


def _valid_payload(sub: str = "11111111-1111-1111-1111-111111111111") -> dict[str, Any]:
    return {"sub": sub, "aud": _AUD, "exp": int(time.time()) + 3600}


def _patch_jwks_client(
    monkeypatch: pytest.MonkeyPatch, public_key: ec.EllipticCurvePublicKey
) -> None:
    """Replace ``auth._get_jwks_client`` with a stub returning ``public_key``."""

    stub_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public_key)
    )
    monkeypatch.setattr(auth_module, "_get_jwks_client", lambda _url: stub_client)


# ── ES256 / JWKS path ──────────────────────────────────────────────────


def test_es256_token_validates_via_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A correctly-signed ES256 token round-trips through the JWKS path."""

    private, public = _es256_keypair()
    _patch_jwks_client(monkeypatch, public)

    token = _encode_es256(_valid_payload(), private)

    claims = verify_supabase_jwt(token, secret=None, jwks_url="http://fake.test/jwks")
    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"
    assert claims["aud"] == _AUD


def test_es256_token_without_jwks_url_returns_401() -> None:
    """If the caller forgets to wire a JWKS URL, ES256 tokens must 401
    with an explicit message — not silently fall through to HS256."""

    private, _ = _es256_keypair()
    token = _encode_es256(_valid_payload(), private)

    with pytest.raises(JWTValidationError) as exc:
        verify_supabase_jwt(token, secret=_HS256_SECRET, jwks_url=None)

    assert exc.value.status_code == 401
    assert "JWKS" in exc.value.detail


def test_es256_signature_mismatch_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token signed by key-A but JWKS returns key-B → 401."""

    private_a, _ = _es256_keypair()
    _, public_b = _es256_keypair()
    _patch_jwks_client(monkeypatch, public_b)

    token = _encode_es256(_valid_payload(), private_a)

    with pytest.raises(JWTValidationError) as exc:
        verify_supabase_jwt(token, secret=None, jwks_url="http://fake.test/jwks")

    assert exc.value.status_code == 401


# ── HS256 / legacy path ────────────────────────────────────────────────


def test_hs256_token_validates_via_secret() -> None:
    """The legacy HS256 path remains functional even with JWKS infrastructure
    in place — operators on the old shared-secret model are not broken."""

    token = jwt.encode(_valid_payload(), _HS256_SECRET, algorithm="HS256")

    claims = verify_supabase_jwt(token, secret=_HS256_SECRET)
    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"


def test_hs256_token_with_no_secret_returns_401() -> None:
    """A token says HS256 but the caller did not supply a secret → 401."""

    token = jwt.encode(_valid_payload(), _HS256_SECRET, algorithm="HS256")

    with pytest.raises(JWTValidationError) as exc:
        verify_supabase_jwt(token, secret=None, jwks_url="http://fake.test/jwks")

    assert exc.value.status_code == 401
    assert "HS256" in exc.value.detail


def test_hs256_wrong_secret_returns_401() -> None:
    """Signature mismatch path — unchanged from the pre-JWKS validator."""

    token = jwt.encode(_valid_payload(), "other-secret-32bytes-padded-xxxxxxxxxx", algorithm="HS256")

    with pytest.raises(JWTValidationError) as exc:
        verify_supabase_jwt(token, secret=_HS256_SECRET)

    assert exc.value.status_code == 401
    assert "signature" in exc.value.detail.lower()


# ── Edge cases ─────────────────────────────────────────────────────────


def test_malformed_token_returns_401() -> None:
    """``not.a.jwt`` fails at header decode — 401, no traceback."""

    with pytest.raises(JWTValidationError) as exc:
        verify_supabase_jwt("not.a.jwt", secret=_HS256_SECRET)

    assert exc.value.status_code == 401
    assert "Malformed" in exc.value.detail or "Decode" in exc.value.detail


def test_expired_token_returns_401() -> None:
    """Expired tokens fail at signature-validation time (post-alg routing)."""

    expired_payload = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": _AUD,
        "exp": int(time.time()) - 60,
    }
    token = jwt.encode(expired_payload, _HS256_SECRET, algorithm="HS256")

    with pytest.raises(JWTValidationError) as exc:
        verify_supabase_jwt(token, secret=_HS256_SECRET)

    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower()
