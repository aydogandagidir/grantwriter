"""Export Celery task tests.

Drives the async core (:func:`_render_and_upload`) directly with a
fake storage backend — no Celery worker, no real Supabase. The
Celery wrapper itself is exercised via the HTTP endpoint test.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from src.storage.supabase_storage import UploadResult
from src.tasks.exports import _docx_storage_path, _render_and_upload


class _FakeStorage:
    """In-memory stand-in for :class:`SupabaseStorage`."""

    def __init__(self) -> None:
        self.captured: list[tuple[str, bytes, str, int]] = []

    async def store_and_sign(
        self, *, path: str, data: bytes, content_type: str, expires_in: int = 3600
    ) -> UploadResult:
        self.captured.append((path, data, content_type, expires_in))
        return UploadResult(
            bucket="exports",
            path=path,
            signed_url=f"https://supabase.test/storage/v1/object/sign/exports/{path}?token=fake",
            expires_in=expires_in,
        )


def _proposal() -> dict[str, Any]:
    return {
        "id": "11111111-2222-3333-4444-555555555555",
        "tenant_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "programme_id": "tubitak_1501",
        "title": "Smoke proposal",
        "draft": {
            "excellence_md": "## B1 Title\n\nBody.\n",
            "impact_md": "## C1 Title\n\nBody.\n",
            "implementation_md": "## D1 Title\n\nBody.\n",
        },
        "budget": {"by_category": {}},
    }


# ── Path naming ─────────────────────────────────────────────────────────


def test_docx_storage_path_includes_tenant_proposal_and_timestamp() -> None:
    tenant_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    proposal_id = "11111111-2222-3333-4444-555555555555"
    path = _docx_storage_path(tenant_id=tenant_id, proposal_id=proposal_id)
    assert path.startswith(f"tenant/{tenant_id}/proposal/{proposal_id}/agy100-")
    assert path.endswith(".docx")


# ── _render_and_upload ──────────────────────────────────────────────────


async def test_render_and_upload_produces_docx_and_signed_url() -> None:
    storage = _FakeStorage()
    result = await _render_and_upload(proposal=_proposal(), storage=storage)

    assert len(storage.captured) == 1
    path, data, content_type, expires_in = storage.captured[0]
    assert path.endswith(".docx")
    # First 4 bytes of any zip / docx file: PK\x03\x04
    assert data[:4] == b"PK\x03\x04"
    assert content_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert expires_in == 3600
    assert result.signed_url.startswith("https://supabase.test/storage/v1/object/sign/")


async def test_render_and_upload_requires_proposal_ids() -> None:
    storage = _FakeStorage()
    bad = _proposal()
    bad["tenant_id"] = ""
    with pytest.raises(ValueError, match="tenant_id"):
        await _render_and_upload(proposal=bad, storage=storage)


async def test_render_and_upload_unknown_programme_raises() -> None:
    storage = _FakeStorage()
    bad = _proposal()
    bad["programme_id"] = "kosgeb_arge"  # not yet registered (S2.D7)
    with pytest.raises(KeyError):
        await _render_and_upload(proposal=bad, storage=storage)


# ── Round-trip: stored bytes are openable with python-docx ─────────────


async def test_round_trip_open_with_python_docx() -> None:
    """Per task spec — "downloads the file, opens with python-docx,
    asserts sections present". The fake storage captures bytes; we
    open them and verify the rendered headings."""

    from io import BytesIO

    from docx import Document

    storage = _FakeStorage()
    await _render_and_upload(proposal=_proposal(), storage=storage)
    _, data, _, _ = storage.captured[0]

    doc = Document(BytesIO(data))
    headings = [
        p.text
        for p in doc.paragraphs  # type: ignore[attr-defined]
        if p.style.name.startswith("Heading")
    ]
    assert "B. Bilimsel ve Teknolojik Detaylar" in headings
    assert "B1 Title" in headings
    assert "C1 Title" in headings
    assert "D1 Title" in headings
    assert "Bütçe Tablosu" in headings


def test_uuid_parsing_is_lenient() -> None:
    """A real Celery task call from the HTTP layer passes the proposal
    id as a UUID stringified into the dict — sanity check it round-trips.
    """

    pid = "11111111-2222-3333-4444-555555555555"
    assert str(UUID(pid)) == pid
