"""Unit tests for :mod:`src.agents.eligibility_checker`.

Every rule is a pure comparison of an _OrgSnapshot against a
_CallSnapshot, so the whole rule layer + the verdict rollup are
testable without a DB. The thin async ``check()`` wrapper (which only
loads the two snapshots and delegates) gets covered in the integration
suite.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from src.agents.eligibility_checker import (
    _CallSnapshot,
    _check_call_open,
    _check_consortium,
    _check_deadline,
    _check_entity_type,
    _check_geo,
    _check_trl,
    _OrgSnapshot,
    _rollup,
)


def _call(
    *,
    status: str = "open",
    deadline: date | None = date(2026, 12, 1),
    geo_scope: list[str] | None = None,
    eligibility_tags: list[str] | None = None,
    trl_min: int | None = None,
    trl_max: int | None = None,
    consortium: bool | None = False,
) -> _CallSnapshot:
    return _CallSnapshot(
        call_id=uuid4(),
        title="Test call",
        status=status,
        deadline=deadline,
        geo_scope=geo_scope or [],
        eligibility_tags=eligibility_tags or [],
        trl_min=trl_min,
        trl_max=trl_max,
        partner_consortium_required=consortium,
    )


def _org(
    *,
    entity_type: str | None = "sme",
    country: str | None = "tr",
    trl_current: int | None = 5,
) -> _OrgSnapshot:
    return _OrgSnapshot(
        entity_type=entity_type, country=country, trl_current=trl_current
    )


_TODAY = date(2026, 5, 14)


# ── _check_call_open ─────────────────────────────────────────────────────


def test_call_open_passes_for_open_status() -> None:
    assert _check_call_open(_call(status="open")).status == "pass"
    assert _check_call_open(_call(status="closing_soon")).status == "pass"


def test_call_open_fails_for_closed() -> None:
    assert _check_call_open(_call(status="closed")).status == "fail"


def test_call_open_warns_for_draft() -> None:
    assert _check_call_open(_call(status="draft")).status == "warn"


# ── _check_deadline ──────────────────────────────────────────────────────


def test_deadline_passes_when_far() -> None:
    check = _check_deadline(_call(deadline=date(2026, 12, 1)), _TODAY)
    assert check.status == "pass"


def test_deadline_fails_when_past() -> None:
    check = _check_deadline(_call(deadline=date(2026, 1, 1)), _TODAY)
    assert check.status == "fail"


def test_deadline_warns_when_within_two_weeks() -> None:
    check = _check_deadline(_call(deadline=date(2026, 5, 20)), _TODAY)
    assert check.status == "warn"
    assert "6 days" in check.message_en


def test_deadline_passes_when_rolling() -> None:
    check = _check_deadline(_call(deadline=None), _TODAY)
    assert check.status == "pass"


# ── _check_geo ───────────────────────────────────────────────────────────


def test_geo_passes_when_country_in_scope() -> None:
    check = _check_geo(_org(country="tr"), _call(geo_scope=["tr", "de"]))
    assert check.status == "pass"


def test_geo_passes_on_broad_token_even_when_country_absent() -> None:
    # Org from Turkey vs an EU-wide call: the 'assoc' token covers TR.
    check = _check_geo(_org(country="tr"), _call(geo_scope=["eu27", "assoc"]))
    assert check.status == "pass"


def test_geo_fails_when_country_not_in_scope() -> None:
    check = _check_geo(_org(country="us"), _call(geo_scope=["tr"]))
    assert check.status == "fail"


def test_geo_warns_when_org_country_missing() -> None:
    check = _check_geo(_org(country=None), _call(geo_scope=["tr"]))
    assert check.status == "warn"


def test_geo_passes_when_call_has_no_scope() -> None:
    check = _check_geo(_org(country="us"), _call(geo_scope=[]))
    assert check.status == "pass"


# ── _check_entity_type ───────────────────────────────────────────────────


def test_entity_type_passes_when_org_type_eligible() -> None:
    check = _check_entity_type(
        _org(entity_type="sme"), _call(eligibility_tags=["sme", "university"])
    )
    assert check.status == "pass"


def test_entity_type_fails_when_org_type_not_eligible() -> None:
    check = _check_entity_type(
        _org(entity_type="large_corp"), _call(eligibility_tags=["sme"])
    )
    assert check.status == "fail"


def test_entity_type_ignores_non_entity_tags() -> None:
    # eligibility_tags = ['consortium_required'] only — no entity-type
    # constraint, so the check passes regardless of org type.
    check = _check_entity_type(
        _org(entity_type="large_corp"),
        _call(eligibility_tags=["consortium_required"]),
    )
    assert check.status == "pass"


def test_entity_type_warns_when_org_type_missing() -> None:
    check = _check_entity_type(
        _org(entity_type=None), _call(eligibility_tags=["sme"])
    )
    assert check.status == "warn"


# ── _check_trl ───────────────────────────────────────────────────────────


def test_trl_passes_inside_band() -> None:
    check = _check_trl(_org(trl_current=5), _call(trl_min=4, trl_max=7))
    assert check.status == "pass"


def test_trl_warns_outside_band() -> None:
    check = _check_trl(_org(trl_current=2), _call(trl_min=5, trl_max=8))
    assert check.status == "warn"


def test_trl_passes_when_call_has_no_band() -> None:
    check = _check_trl(_org(trl_current=2), _call(trl_min=None, trl_max=None))
    assert check.status == "pass"


def test_trl_warns_when_org_trl_missing() -> None:
    check = _check_trl(_org(trl_current=None), _call(trl_min=4, trl_max=7))
    assert check.status == "warn"


# ── _check_consortium ────────────────────────────────────────────────────


def test_consortium_passes_when_not_required() -> None:
    assert _check_consortium(_call(consortium=False)).status == "pass"


def test_consortium_warns_when_required() -> None:
    assert _check_consortium(_call(consortium=True)).status == "warn"


# ── _rollup verdict logic ────────────────────────────────────────────────


def test_rollup_eligible_when_all_pass() -> None:
    checks = [
        _check_call_open(_call(status="open")),
        _check_consortium(_call(consortium=False)),
    ]
    report = _rollup(checks)
    assert report.verdict == "ELIGIBLE"
    assert report.blockers == []
    assert report.confidence == 1.0


def test_rollup_conditional_when_any_warn() -> None:
    checks = [
        _check_call_open(_call(status="open")),
        _check_consortium(_call(consortium=True)),  # warn
    ]
    report = _rollup(checks)
    assert report.verdict == "CONDITIONAL"
    assert len(report.warnings) == 1


def test_rollup_not_eligible_when_any_fail() -> None:
    checks = [
        _check_call_open(_call(status="closed")),  # fail
        _check_consortium(_call(consortium=True)),  # warn
    ]
    report = _rollup(checks)
    # A single fail overrides any number of warns.
    assert report.verdict == "NOT_ELIGIBLE"
    assert len(report.blockers) == 1
    assert len(report.warnings) == 1


def test_rollup_confidence_drops_with_missing_data_warns() -> None:
    # Two checks that warn specifically because profile fields are
    # missing → confidence dips below 1.0.
    checks = [
        _check_geo(_org(country=None), _call(geo_scope=["tr"])),
        _check_trl(_org(trl_current=None), _call(trl_min=4, trl_max=7)),
    ]
    report = _rollup(checks)
    assert report.verdict == "CONDITIONAL"
    assert report.confidence < 1.0
