"""Supabase JWT auth for API endpoints.

Decodes the bearer token, validates signature and audience, and returns a
minimal `User` model. The `tenant_id` is left unresolved here — endpoints that
need it should query `public.users` themselves (when the migration lands), or
rely on RLS via `auth.uid()`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Header, HTTPException, status
from jose import JWTError, jwt
from pydantic import BaseModel

from src.core.config import get_settings


class User(BaseModel):
    id: UUID
    email: str | None = None


async def get_current_user(authorization: Annotated[str, Header()]) -> User:
    settings = get_settings()
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=[settings.supabase_jwt_algorithm],
            audience=settings.supabase_jwt_audience,
        )
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token missing sub claim")
    try:
        user_id = UUID(str(sub))
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="sub is not a UUID") from exc

    return User(id=user_id, email=payload.get("email"))
