"""JWT validation against Supabase's HS256-signed access tokens.

Lives in ``src/core`` because every authenticated route depends on it. The
validator is intentionally narrow: signature + audience + expiry only. We
do not introspect Supabase Auth's ``role`` claim here — that's enforced
downstream by Postgres RLS policies via the JWT-claim GUC.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.config import SettingsDep

_BEARER = HTTPBearer(auto_error=True, description="Supabase access token")
_DEFAULT_AUDIENCE = "authenticated"


class JWTValidationError(HTTPException):
    """Raised when a JWT fails any signature, audience, or expiry check."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_supabase_jwt(
    token: str,
    secret: str,
    *,
    audience: str = _DEFAULT_AUDIENCE,
) -> dict[str, Any]:
    """Verify a Supabase HS256 JWT and return the decoded claims.

    Raises :class:`JWTValidationError` (HTTP 401) on any failure mode —
    bad signature, expired token, wrong audience, malformed token. The
    distinction does not matter to the caller; it should always 401.
    """

    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=audience,
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise JWTValidationError("Token expired") from exc
    except jwt.InvalidSignatureError as exc:
        raise JWTValidationError("Invalid signature") from exc
    except jwt.InvalidAudienceError as exc:
        raise JWTValidationError("Invalid audience") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise JWTValidationError(f"Missing required claim: {exc.claim}") from exc
    except jwt.InvalidTokenError as exc:
        raise JWTValidationError("Invalid token") from exc


def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_BEARER)],
    settings: SettingsDep,
) -> UUID:
    """FastAPI dependency: validate the bearer token, return its ``sub`` claim."""

    if settings.supabase_jwt_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT secret not configured",
        )
    claims = verify_supabase_jwt(
        credentials.credentials,
        settings.supabase_jwt_secret.get_secret_value(),
    )
    try:
        return UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise JWTValidationError("Invalid sub claim") from exc


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
"""Dependency alias — inject as ``user_id: CurrentUserId``."""
