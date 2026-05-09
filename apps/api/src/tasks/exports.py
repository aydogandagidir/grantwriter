"""Export Celery tasks.

The HTTP layer enqueues ``generate_proposal_docx_task``; the worker
calls into the programme module, uploads the bytes to Supabase Storage,
and returns the signed URL. The body is split into a sync Celery
``@app.task`` thin wrapper and an async ``_render_and_upload`` callable
that does the real work — easier to test and to call from a non-Celery
context (e.g., the FastAPI request handler in dev).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from src.programs import get_module
from src.storage.supabase_storage import SupabaseStorage, UploadResult
from src.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _StorageLike(Protocol):
    async def store_and_sign(
        self, *, path: str, data: bytes, content_type: str, expires_in: int = ...
    ) -> UploadResult: ...


def _docx_storage_path(*, tenant_id: str, proposal_id: str) -> str:
    """Per-tenant + per-proposal + timestamped — easy to RLS-scope and audit.

    Bucket policy locks reads to the owning tenant. Timestamp suffix
    avoids overwriting prior exports when the user re-runs. The path
    deliberately includes the original proposal_id so manual debugging
    via the Supabase dashboard is straightforward.
    """

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"tenant/{tenant_id}/proposal/{proposal_id}/agy100-{ts}.docx"


async def _render_and_upload(
    *, proposal: dict[str, Any], storage: _StorageLike, expires_in: int = 3600
) -> UploadResult:
    """Pure async core — render the DOCX through the programme module
    and ship the bytes to storage. Independent of Celery so tests can
    drive it directly with a fake storage backend."""

    programme_id = str(proposal.get("programme_id") or "")
    module = get_module(programme_id)
    blob = module.export_docx(proposal)

    tenant_id = str(proposal.get("tenant_id") or "")
    proposal_id = str(proposal.get("id") or "")
    if not (tenant_id and proposal_id):
        raise ValueError("proposal must carry tenant_id + id for storage path")
    path = _docx_storage_path(tenant_id=tenant_id, proposal_id=proposal_id)

    result = await storage.store_and_sign(
        path=path, data=blob, content_type=DOCX_MIME, expires_in=expires_in
    )
    logger.info(
        "proposal_docx_uploaded",
        extra={
            "proposal_id": proposal_id,
            "tenant_id": tenant_id,
            "bucket": result.bucket,
            "path": result.path,
            "size_bytes": len(blob),
        },
    )
    return result


@celery_app.task(name="exports.generate_proposal_docx", bind=True, max_retries=3)  # type: ignore[untyped-decorator]
def generate_proposal_docx_task(
    self: Any, proposal: dict[str, Any], *, expires_in: int = 3600
) -> dict[str, Any]:
    """Celery wrapper around :func:`_render_and_upload`.

    Reads Supabase Storage settings from process settings; the broker /
    redis URL was already wired by :mod:`src.tasks.celery_app`. Returns
    a JSON-safe dict with bucket, path, expires_in, and signed_url so
    the HTTP layer can poll for it via the result backend.
    """

    from src.core.config import get_settings

    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_service_role_key):
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured for exports."
        )

    storage = SupabaseStorage(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key.get_secret_value(),
        bucket=settings.supabase_storage_bucket,
    )

    async def _go() -> UploadResult:
        try:
            return await _render_and_upload(
                proposal=proposal, storage=storage, expires_in=expires_in
            )
        finally:
            await storage.aclose()

    try:
        result = asyncio.run(_go())
    except Exception as exc:
        logger.exception("export_task_failed", extra={"proposal_id": proposal.get("id")})
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc

    return {
        "bucket": result.bucket,
        "path": result.path,
        "signed_url": result.signed_url,
        "expires_in": result.expires_in,
    }


def proposal_id_to_uuid(proposal_id: str) -> UUID:
    return UUID(proposal_id)


# ── XLSX export ────────────────────────────────────────────────────────


def _xlsx_storage_path(*, tenant_id: str, proposal_id: str) -> str:
    """Mirror of :func:`_docx_storage_path` for the budget XLSX.

    Different filename suffix (``budget`` vs ``agy100``) so a tenant
    can have both a DOCX and an XLSX export co-existing without
    overwriting each other.
    """

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"tenant/{tenant_id}/proposal/{proposal_id}/budget-{ts}.xlsx"


async def _render_and_upload_xlsx(
    *, proposal: dict[str, Any], storage: _StorageLike, expires_in: int = 3600
) -> UploadResult | None:
    """Async core for the XLSX export.

    Returns ``None`` when the programme module's ``export_xlsx_budget``
    returns ``None`` (e.g., TÜBİTAK uses the in-DOCX budget table, not
    a standalone XLSX). The Celery wrapper translates that into a
    no-op response so the orchestrator doesn't fail the task.
    """

    programme_id = str(proposal.get("programme_id") or "")
    module = get_module(programme_id)
    blob = module.export_xlsx_budget(proposal)
    if blob is None:
        return None

    tenant_id = str(proposal.get("tenant_id") or "")
    proposal_id = str(proposal.get("id") or "")
    if not (tenant_id and proposal_id):
        raise ValueError("proposal must carry tenant_id + id for storage path")
    path = _xlsx_storage_path(tenant_id=tenant_id, proposal_id=proposal_id)

    result = await storage.store_and_sign(
        path=path, data=blob, content_type=XLSX_MIME, expires_in=expires_in
    )
    logger.info(
        "proposal_xlsx_uploaded",
        extra={
            "proposal_id": proposal_id,
            "tenant_id": tenant_id,
            "bucket": result.bucket,
            "path": result.path,
            "size_bytes": len(blob),
        },
    )
    return result


@celery_app.task(name="exports.generate_proposal_xlsx", bind=True, max_retries=3)  # type: ignore[untyped-decorator]
def generate_proposal_xlsx_task(
    self: Any, proposal: dict[str, Any], *, expires_in: int = 3600
) -> dict[str, Any]:
    """Celery wrapper around :func:`_render_and_upload_xlsx`.

    Returns ``{"skipped": True, "reason": "no_xlsx_for_programme"}``
    if the module produces no XLSX (e.g., TÜBİTAK). Tests + the HTTP
    layer treat that as a success — the absence is meaningful.
    """

    from src.core.config import get_settings

    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_service_role_key):
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured for exports."
        )

    storage = SupabaseStorage(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key.get_secret_value(),
        bucket=settings.supabase_storage_bucket,
    )

    async def _go() -> UploadResult | None:
        try:
            return await _render_and_upload_xlsx(
                proposal=proposal, storage=storage, expires_in=expires_in
            )
        finally:
            await storage.aclose()

    try:
        result = asyncio.run(_go())
    except Exception as exc:
        logger.exception(
            "xlsx_export_task_failed", extra={"proposal_id": proposal.get("id")}
        )
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc

    if result is None:
        return {"skipped": True, "reason": "no_xlsx_for_programme"}

    return {
        "bucket": result.bucket,
        "path": result.path,
        "signed_url": result.signed_url,
        "expires_in": result.expires_in,
    }


__all__ = [
    "DOCX_MIME",
    "XLSX_MIME",
    "_docx_storage_path",
    "_render_and_upload",
    "_render_and_upload_xlsx",
    "_xlsx_storage_path",
    "generate_proposal_docx_task",
    "generate_proposal_xlsx_task",
]
