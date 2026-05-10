"""GET /proposals/{id}/prodis-fields endpoint tests.

Two layers:
1. Pure-function tests on ``TUBITAKBaseModule.get_prodis_fields`` —
   markdown→plain-text round-trip, ordering, missing subsections.
2. HTTP route tests — DB row → endpoint → 11 fields, programme guard.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from src.core.auth import get_current_user_id
from src.core.db import get_db
from src.programs import get_module
from src.programs._tubitak_base import (
    PRODIS_FIELD_LABELS,
    ProdisField,
    _markdown_to_plain_text,
)

# ── _markdown_to_plain_text helper ─────────────────────────────────────


def test_markdown_to_plain_text_strips_headings() -> None:
    md = "# Big heading\n\nBody.\n\n## Sub\n\nMore body."
    text = _markdown_to_plain_text(md)
    assert "#" not in text
    assert "Big heading" in text
    assert "Sub" in text


def test_markdown_to_plain_text_strips_bold_and_italic() -> None:
    md = "This is **bold** and *italic* and ***both***."
    text = _markdown_to_plain_text(md)
    assert "**" not in text
    assert "bold" in text
    assert "italic" in text


def test_markdown_to_plain_text_keeps_link_label_drops_url() -> None:
    md = "See [the docs](https://example.com/path) for details."
    text = _markdown_to_plain_text(md)
    assert "the docs" in text
    assert "https://example.com" not in text


def test_markdown_to_plain_text_normalises_list_bullets() -> None:
    md = "- first\n- second\n* third\n1. fourth\n2. fifth"
    text = _markdown_to_plain_text(md)
    # All bullets normalise to "- "; ordered list numbers also become "- ".
    assert text.count("- ") == 5


def test_markdown_to_plain_text_strips_inline_code() -> None:
    md = "Use `pytest -v` to run tests."
    text = _markdown_to_plain_text(md)
    assert "`" not in text
    assert "pytest -v" in text


def test_markdown_to_plain_text_handles_empty_input() -> None:
    assert _markdown_to_plain_text("") == ""
    assert _markdown_to_plain_text("   \n\n  ") == ""


# ── get_prodis_fields ──────────────────────────────────────────────────


def _full_tubitak_draft() -> dict[str, Any]:
    return {
        "excellence_md": (
            "## B1 Proje Konusu ve Amaçları\n\nProje konusu açıklaması.\n\n"
            "## B2 Yenilikçi Yönler\n\n**Çok yenilikçi**.\n\n"
            "## B3 Yöntem ve Teknik\n\n- Adım 1\n- Adım 2\n\n"
            "## B4 Literatür Taraması\n\nReferans var.\n"
        ),
        "impact_md": (
            "## C1 Ekonomik ve Ulusal Kazanım\n\nİhracat artışı.\n\n"
            "## C2 Yaygın Etki\n\nSektörel etki.\n\n"
            "## C3 Pazar Analizi\n\nPazar 1B USD.\n"
        ),
        "implementation_md": (
            "## D1 İş Paketleri\n\nWP1, WP2.\n\n"
            "## D2 Zaman Planlaması\n\nGantt.\n\n"
            "## D3 Bütçe\n\n1.5M TL.\n\n"
            "## D4 Proje Yönetimi ve Riskler\n\nRisk listesi.\n"
        ),
    }


def test_prodis_fields_returns_eleven_fields_for_full_draft() -> None:
    module = get_module("tubitak_1501")
    fields = module.get_prodis_fields({"draft": _full_tubitak_draft()})

    assert len(fields) == 11
    keys = [f.key for f in fields]
    assert keys == [label[0] for label in PRODIS_FIELD_LABELS]


def test_prodis_fields_extract_subsection_bodies() -> None:
    module = get_module("tubitak_1501")
    fields = module.get_prodis_fields({"draft": _full_tubitak_draft()})

    by_key = {f.key: f for f in fields}
    assert "Proje konusu açıklaması" in by_key["B1_proje_konusu_ve_amaclari"].value
    # Bold markers stripped.
    assert "Çok yenilikçi" in by_key["B2_yenilikci_yonleri"].value
    assert "**" not in by_key["B2_yenilikci_yonleri"].value
    # List bullets normalised.
    b3_value = by_key["B3_yontem_ve_teknik"].value
    assert "- Adım 1" in b3_value
    assert "- Adım 2" in b3_value


def test_prodis_fields_handles_missing_subsections() -> None:
    """A subsection that the writer didn't produce → empty value, NOT a crash."""

    module = get_module("tubitak_1501")
    sparse_draft = {
        "excellence_md": "## B1 Proje Konusu\n\nOnly B1 here.\n",
        # No impact / implementation
    }
    fields = module.get_prodis_fields({"draft": sparse_draft})

    assert len(fields) == 11  # always 11; missing fields just have empty value
    by_key = {f.key: f for f in fields}
    assert by_key["B1_proje_konusu_ve_amaclari"].value != ""
    assert by_key["B2_yenilikci_yonleri"].value == ""
    assert by_key["C1_ekonomik_ve_ulusal_kazanim"].value == ""
    assert by_key["D1_is_paketleri"].value == ""


def test_prodis_fields_labels_are_bilingual() -> None:
    module = get_module("tubitak_1501")
    fields = module.get_prodis_fields({"draft": _full_tubitak_draft()})

    for f in fields:
        assert f.label_tr
        assert f.label_en
        assert f.label_tr != f.label_en  # genuinely translated


def test_prodis_fields_works_for_tubitak_1507() -> None:
    """1507 inherits from TUBITAKBase — same form, same fields."""

    module = get_module("tubitak_1507")
    fields = module.get_prodis_fields({"draft": _full_tubitak_draft()})
    assert len(fields) == 11
    assert all(isinstance(f, ProdisField) for f in fields)


# ── HTTP endpoint ──────────────────────────────────────────────────────


@pytest.fixture
def overridden_app(app: FastAPI) -> AsyncIterator[FastAPI]:
    fake_user_id = uuid.uuid4()
    fake_conn = AsyncMock()
    # Default: TÜBİTAK 1501 with full draft.
    fake_conn.fetchrow = AsyncMock(
        return_value={
            "programme_id": "tubitak_1501",
            "draft": json.dumps(_full_tubitak_draft()),
            "brief": "{}",
        }
    )

    async def _user_override() -> uuid.UUID:
        return fake_user_id

    async def _db_override() -> AsyncIterator[Any]:
        yield fake_conn

    app.dependency_overrides[get_current_user_id] = _user_override
    app.dependency_overrides[get_db] = _db_override
    app.state.test_conn = fake_conn  # type: ignore[attr-defined]
    yield app
    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(get_db, None)


async def test_endpoint_returns_eleven_fields(
    overridden_app: FastAPI,
    client: AsyncClient,
) -> None:
    response = await client.get(f"/api/v1/proposals/{uuid.uuid4()}/prodis-fields")

    assert response.status_code == 200
    body = response.json()
    assert body["programme_id"] == "tubitak_1501"
    assert len(body["fields"]) == 11
    keys = [f["key"] for f in body["fields"]]
    assert "B2_yenilikci_yonleri" in keys
    assert "D4_proje_yonetimi_ve_riskler" in keys


async def test_endpoint_returns_404_when_proposal_missing(
    overridden_app: FastAPI,
    client: AsyncClient,
) -> None:
    overridden_app.state.test_conn.fetchrow = AsyncMock(return_value=None)

    response = await client.get(f"/api/v1/proposals/{uuid.uuid4()}/prodis-fields")
    assert response.status_code == 404


async def test_endpoint_returns_422_for_non_tubitak_programme(
    overridden_app: FastAPI,
    client: AsyncClient,
) -> None:
    overridden_app.state.test_conn.fetchrow = AsyncMock(
        return_value={
            "programme_id": "horizon_eu_ria",
            "draft": "{}",
            "brief": "{}",
        }
    )
    response = await client.get(f"/api/v1/proposals/{uuid.uuid4()}/prodis-fields")
    assert response.status_code == 422
    assert "tübi̇tak" in response.json()["detail"].lower() or "tubitak" in response.json()["detail"].lower()


async def test_endpoint_returns_422_for_unknown_programme(
    overridden_app: FastAPI,
    client: AsyncClient,
) -> None:
    overridden_app.state.test_conn.fetchrow = AsyncMock(
        return_value={
            "programme_id": "not_a_real_programme",
            "draft": "{}",
            "brief": "{}",
        }
    )
    response = await client.get(f"/api/v1/proposals/{uuid.uuid4()}/prodis-fields")
    assert response.status_code == 422
