"""Minimal Supabase Storage client.

The official ``supabase-py`` SDK is not yet a project dep; we use the
narrow REST surface this codebase actually needs (upload + signed URL)
via httpx. Stays in line with CLAUDE.md "no new deps without
discussion".

Tests inject a fake :class:`_StorageLike` (see
``src/tasks/exports.py``) so this concrete client is never reached
without real Supabase credentials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Anything from 3xx upwards is treated as a failure for our PUT/POST flows.
_HTTP_REDIRECT_FLOOR = 300


class UploadResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: str
    path: str
    signed_url: str
    expires_in: int


@dataclass(frozen=True)
class StorageError(Exception):
    """Raised on a non-2xx response from the Storage REST API."""

    status_code: int
    message: str

    def __str__(self) -> str:  # pragma: no cover — trivial
        return f"Supabase Storage error {self.status_code}: {self.message}"


class SupabaseStorage:
    """Thin async wrapper around the Supabase Storage REST surface."""

    def __init__(self, *, url: str, service_role_key: str, bucket: str) -> None:
        if not url:
            raise ValueError("supabase url required")
        if not service_role_key:
            raise ValueError("supabase service_role_key required")
        self._base = url.rstrip("/")
        self._bucket = bucket
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {service_role_key}",
                "apikey": service_role_key,
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def bucket(self) -> str:
        return self._bucket

    async def upload(
        self, *, path: str, data: bytes, content_type: str = DOCX_MIME, upsert: bool = True
    ) -> None:
        """PUT bytes to ``<bucket>/<path>``. ``upsert=True`` overwrites
        if the path already exists; safer for re-runs of the same
        proposal export."""

        resp = await self._client.post(
            f"{self._base}/storage/v1/object/{self._bucket}/{path}",
            content=data,
            headers={
                "Content-Type": content_type,
                "x-upsert": "true" if upsert else "false",
            },
        )
        if resp.status_code >= _HTTP_REDIRECT_FLOOR:
            raise StorageError(status_code=resp.status_code, message=resp.text)

    async def signed_url(self, *, path: str, expires_in: int = 3600) -> str:
        """Create a time-limited download URL. Bucket can be private."""

        resp = await self._client.post(
            f"{self._base}/storage/v1/object/sign/{self._bucket}/{path}",
            json={"expiresIn": expires_in},
        )
        if resp.status_code >= _HTTP_REDIRECT_FLOOR:
            raise StorageError(status_code=resp.status_code, message=resp.text)
        payload: dict[str, Any] = resp.json()
        signed_path = str(payload.get("signedURL") or payload.get("signedUrl") or "")
        if not signed_path:
            raise StorageError(
                status_code=resp.status_code,
                message="signed URL missing in Storage response",
            )
        return f"{self._base}/storage/v1{signed_path}"

    async def store_and_sign(
        self, *, path: str, data: bytes, content_type: str, expires_in: int = 3600
    ) -> UploadResult:
        await self.upload(path=path, data=data, content_type=content_type)
        signed = await self.signed_url(path=path, expires_in=expires_in)
        return UploadResult(
            bucket=self._bucket, path=path, signed_url=signed, expires_in=expires_in
        )


__all__ = ["DOCX_MIME", "StorageError", "SupabaseStorage", "UploadResult"]
