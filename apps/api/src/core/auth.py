"""JWT validation against Supabase-signed access tokens.

Supabase migrated to asymmetric JWT signing keys (ES256 via JWKS) for new
projects in 2025; the local CLI emits ES256 tokens by default. Legacy
HS256 (shared-secret) mode is still supported for projects that did not
migrate or for self-hosted setups without a JWKS endpoint.

The validator is intentionally narrow: signature + audience + expiry. We
do not introspect the ``role`` claim here — that's enforced downstream by
Postgres RLS policies via the JWT-claim GUC.
"""

from __future__ import annotations

import threading
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWKClientError

from src.core.config import SettingsDep

_BEARER = HTTPBearer(auto_error=True, description="Supabase access token")
_DEFAULT_AUDIENCE = "authenticated"

# Algorithms Supabase may use for asymmetric signing. Anything not in this
# set + not equal to HS256 is rejected at the front door.
_ASYMMETRIC_ALGOS: tuple[str, ...] = (
    "ES256",
    "ES256K",
    "EdDSA",
    "RS256",
    "RS384",
    "RS512",
)

# Per-URL JWKS client cache. PyJWKClient does its own key caching, so we
# only need to dedupe the client instances themselves. Guarded by a lock
# because FastAPI sync deps run in a threadpool.
_jwks_clients: dict[str, PyJWKClient] = {}
_jwks_lock = threading.Lock()


class JWTValidationError(HTTPException):
    """Raised when a JWT fails any signature, audience, or expiry check."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def _get_jwks_client(jwks_url: str) -> PyJWKClient:
    """Return (and lazily build + cache) a PyJWKClient for ``jwks_url``."""

    with _jwks_lock:
        client = _jwks_clients.get(jwks_url)
        if client is None:
            client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
            _jwks_clients[jwks_url] = client
        return client


def verify_supabase_jwt(  # noqa: PLR0912 - one branch per PyJWT exception class is the point
    token: str,
    secret: str | None = None,
    *,
    audience: str = _DEFAULT_AUDIENCE,
    jwks_url: str | None = None,
) -> dict[str, Any]:
    """Verify a Supabase JWT and return the decoded claims.

    Routes by the token's ``alg`` header:

    * **HS256** — verified against ``secret``. Raises if ``secret`` is
      ``None``.
    * **ES256 / RS256 / EdDSA / etc.** — verified against the JWK fetched
      from ``jwks_url``. Raises if ``jwks_url`` is ``None``.

    Raises :class:`JWTValidationError` (HTTP 401) on any failure mode —
    bad signature, expired token, wrong audience, malformed token, unknown
    algorithm. The distinction does not matter to the caller; it should
    always surface as 401. The detail string is intentionally explicit so
    ops can diagnose without exposing the token itself.
    """

    # Inspect the header first so we know which verification path to take.
    # Malformed tokens fail here before we touch any network or secret.
    try:
        header = jwt.get_unverified_header(token)
    except jwt.DecodeError as exc:
        raise JWTValidationError(f"Malformed token: {exc}") from exc

    alg = header.get("alg")

    try:
        if alg == "HS256":
            if not secret:
                raise JWTValidationError(
                    "Token uses HS256 but no shared secret is configured"
                )
            return jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=audience,
                options={"require": ["exp", "sub", "aud"]},
            )
        if alg in _ASYMMETRIC_ALGOS:
            if not jwks_url:
                raise JWTValidationError(
                    f"Token uses {alg} but no JWKS URL is configured "
                    "(set SUPABASE_URL so we can derive it)"
                )
            client = _get_jwks_client(jwks_url)
            signing_key = client.get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=list(_ASYMMETRIC_ALGOS),
                audience=audience,
                options={"require": ["exp", "sub", "aud"]},
            )
        raise JWTValidationError(f"Unsupported JWT algorithm: {alg!r}")
    except PyJWKClientError as exc:
        # Network failure, missing kid, malformed JWKS, etc.
        raise JWTValidationError(f"JWKS error: {exc}") from exc
    except jwt.ExpiredSignatureError as exc:
        raise JWTValidationError("Token expired") from exc
    except jwt.InvalidSignatureError as exc:
        raise JWTValidationError(f"Invalid signature: {exc}") from exc
    except jwt.InvalidAudienceError as exc:
        raise JWTValidationError(f"Invalid audience: {exc}") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise JWTValidationError(f"Missing required claim: {exc.claim}") from exc
    except jwt.InvalidAlgorithmError as exc:
        raise JWTValidationError(f"Invalid algorithm: {exc}") from exc
    except jwt.DecodeError as exc:
        raise JWTValidationError(f"Decode error: {exc}") from exc
    except jwt.InvalidTokenError as exc:
        raise JWTValidationError(
            f"Invalid token ({type(exc).__name__}): {exc}"
        ) from exc


def _derive_jwks_url(supabase_url: str | None) -> str | None:
    """Compose the canonical JWKS endpoint from a Supabase project URL."""

    if not supabase_url:
        return None
    return f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_BEARER)],
    settings: SettingsDep,
) -> UUID:
    """FastAPI dependency: validate the bearer token, return its ``sub`` claim."""

    secret = (
        settings.supabase_jwt_secret.get_secret_value()
        if settings.supabase_jwt_secret is not None
        else None
    )
    jwks_url = _derive_jwks_url(settings.supabase_url)

    if not secret and not jwks_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT verification not configured "
            "(set SUPABASE_URL for asymmetric or SUPABASE_JWT_SECRET for HS256)",
        )

    claims = verify_supabase_jwt(
        credentials.credentials,
        secret,
        audience=settings.supabase_jwt_audience,
        jwks_url=jwks_url,
    )
    try:
        return UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise JWTValidationError("Invalid sub claim") from exc


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
"""Dependency alias — inject as ``user_id: CurrentUserId``."""
