"""EligibilityChecker — does this organization qualify for this call?

Runs at the moment a user picks a call, before they sink time into a
brief. Catches the obvious disqualifiers (wrong country, expired
deadline, consortium required but the org is going solo) and surfaces
the soft mismatches (TRL band drift, tight deadline) as warnings rather
than hard blocks.

V1 is **rule-based**: every check is a deterministic comparison of the
org profile against the call's structured fields (``geo_scope``,
``eligibility_tags``, ``trl_min/max``, ``deadline``,
``partner_consortium_required``). It needs no LLM call — fast, free,
reproducible.

The ``router`` argument is accepted now so the V2 LLM-refine pass —
parsing nuanced rules out of ``call_text`` for CONDITIONAL verdicts —
can slot in without changing the call sites. V1 leaves it unused.

Verdict rollup:
  - any check ``fail``  → NOT_ELIGIBLE
  - else any ``warn``   → CONDITIONAL
  - else                → ELIGIBLE
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

import asyncpg

from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)


Verdict = Literal["ELIGIBLE", "CONDITIONAL", "NOT_ELIGIBLE"]
CheckStatus = Literal["pass", "warn", "fail"]

CLOSING_SOON_DAYS = 14
MODEL_VERSION = "eligibility_checker-v1"

# Region tokens in calls.geo_scope that mean "broader than a single
# country" — an org from any country passes the geo check when the
# call carries one of these.
_BROAD_GEO_TOKENS = frozenset({"eu27", "assoc", "global"})


# ── Result dataclasses ──────────────────────────────────────────────────


@dataclass(frozen=True)
class EligibilityCheck:
    """One rule's outcome, with a bilingual user-facing message."""

    rule: str
    status: CheckStatus
    message_tr: str
    message_en: str


@dataclass(frozen=True)
class EligibilityReport:
    verdict: Verdict
    checks: list[EligibilityCheck]
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 1.0
    model_version: str = MODEL_VERSION
    checked_at: str = ""


@dataclass(frozen=True)
class _OrgSnapshot:
    entity_type: str | None
    country: str | None
    trl_current: int | None


@dataclass(frozen=True)
class _CallSnapshot:
    call_id: UUID
    title: str
    status: str
    deadline: date | None
    geo_scope: list[str]
    eligibility_tags: list[str]
    trl_min: int | None
    trl_max: int | None
    partner_consortium_required: bool | None


# ── Checker ──────────────────────────────────────────────────────────────


class EligibilityChecker:
    """Rule-based eligibility assessment for one (org, call) pair."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        router: LLMRouter | None = None,
        tenant_id: UUID | None = None,
    ) -> None:
        self._pool = pool
        # Accepted for the V2 LLM-refine seam; unused in V1.
        self._router = router
        self._tenant_id = tenant_id

    async def check(
        self,
        *,
        org_tenant_id: UUID,
        call_id: UUID,
        today: date | None = None,
    ) -> EligibilityReport:
        """Assess whether ``org_tenant_id`` qualifies for ``call_id``.

        Raises ``ValueError`` when the call doesn't exist. A missing
        org profile is NOT an error — it just means most checks come
        back as warnings ('we can't confirm X without your profile').
        """

        org = await self._load_org(org_tenant_id)
        call = await self._load_call(call_id)
        today = today or datetime.now(UTC).date()

        checks: list[EligibilityCheck] = [
            _check_call_open(call),
            _check_deadline(call, today),
            _check_geo(org, call),
            _check_entity_type(org, call),
            _check_trl(org, call),
            _check_consortium(call),
        ]

        return _rollup(checks)

    # ── Loaders ──────────────────────────────────────────────────────

    async def _load_org(self, tenant_id: UUID) -> _OrgSnapshot:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT entity_type, country, trl_current
                  FROM organization_profiles
                 WHERE tenant_id = $1
                """,
                tenant_id,
            )
        if row is None:
            return _OrgSnapshot(entity_type=None, country=None, trl_current=None)
        return _OrgSnapshot(
            entity_type=row["entity_type"],
            country=(row["country"] or None),
            trl_current=row["trl_current"],
        )

    async def _load_call(self, call_id: UUID) -> _CallSnapshot:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, title, status, deadline, geo_scope,
                       eligibility_tags, trl_min, trl_max,
                       partner_consortium_required
                  FROM calls
                 WHERE id = $1
                """,
                call_id,
            )
        if row is None:
            raise ValueError(f"call {call_id} not found")
        return _CallSnapshot(
            call_id=UUID(str(row["id"])),
            title=str(row["title"]),
            status=str(row["status"]),
            deadline=row["deadline"],
            geo_scope=list(row["geo_scope"] or []),
            eligibility_tags=list(row["eligibility_tags"] or []),
            trl_min=row["trl_min"],
            trl_max=row["trl_max"],
            partner_consortium_required=row["partner_consortium_required"],
        )


# ── Individual rules ─────────────────────────────────────────────────────


def _check_call_open(call: _CallSnapshot) -> EligibilityCheck:
    if call.status == "closed":
        return EligibilityCheck(
            rule="call_open",
            status="fail",
            message_tr="Bu çağrı kapanmış.",
            message_en="This call is closed.",
        )
    if call.status == "draft":
        return EligibilityCheck(
            rule="call_open",
            status="warn",
            message_tr="Bu çağrı henüz taslak durumda — şartlar değişebilir.",
            message_en="This call is still in draft — terms may change.",
        )
    return EligibilityCheck(
        rule="call_open",
        status="pass",
        message_tr="Çağrı başvuruya açık.",
        message_en="The call is open for applications.",
    )


def _check_deadline(call: _CallSnapshot, today: date) -> EligibilityCheck:
    if call.deadline is None:
        return EligibilityCheck(
            rule="deadline",
            status="pass",
            message_tr="Çağrı sürekli açık (son tarih belirtilmemiş).",
            message_en="Rolling call — no fixed deadline.",
        )
    if call.deadline < today:
        return EligibilityCheck(
            rule="deadline",
            status="fail",
            message_tr=f"Son başvuru tarihi geçmiş ({call.deadline.isoformat()}).",
            message_en=f"The deadline has passed ({call.deadline.isoformat()}).",
        )
    days_left = (call.deadline - today).days
    if days_left <= CLOSING_SOON_DAYS:
        return EligibilityCheck(
            rule="deadline",
            status="warn",
            message_tr=(
                f"Son tarihe yalnızca {days_left} gün kaldı — kaliteli bir "
                "başvuru için süre dar."
            ),
            message_en=(
                f"Only {days_left} days to the deadline — tight for a "
                "competitive application."
            ),
        )
    return EligibilityCheck(
        rule="deadline",
        status="pass",
        message_tr=f"Son tarihe {days_left} gün var.",
        message_en=f"{days_left} days until the deadline.",
    )


def _check_geo(org: _OrgSnapshot, call: _CallSnapshot) -> EligibilityCheck:
    if not call.geo_scope:
        return EligibilityCheck(
            rule="geo",
            status="pass",
            message_tr="Çağrıda coğrafi kısıtlama belirtilmemiş.",
            message_en="No geographic restriction published for this call.",
        )
    if org.country is None:
        return EligibilityCheck(
            rule="geo",
            status="warn",
            message_tr=(
                "Organizasyon ülkeniz profilde belirtilmemiş — coğrafi "
                "uygunluk doğrulanamadı."
            ),
            message_en=(
                "Your organisation's country isn't set in the profile — "
                "geographic eligibility couldn't be confirmed."
            ),
        )
    scope = {token.lower() for token in call.geo_scope}
    country = org.country.lower()
    if country in scope or scope & _BROAD_GEO_TOKENS:
        return EligibilityCheck(
            rule="geo",
            status="pass",
            message_tr="Organizasyonunuz çağrının coğrafi kapsamında.",
            message_en="Your organisation is within the call's geographic scope.",
        )
    return EligibilityCheck(
        rule="geo",
        status="fail",
        message_tr=(
            f"Çağrı coğrafi kapsamı ({', '.join(call.geo_scope)}) "
            f"ülkenizi ({org.country}) içermiyor."
        ),
        message_en=(
            f"The call's geographic scope ({', '.join(call.geo_scope)}) "
            f"doesn't include your country ({org.country})."
        ),
    )


def _check_entity_type(org: _OrgSnapshot, call: _CallSnapshot) -> EligibilityCheck:
    # eligibility_tags carries both entity types and structural flags;
    # filter to the entity-type subset for this check.
    entity_tags = {
        t
        for t in call.eligibility_tags
        if t in {
            "individual", "sme", "university", "research_org",
            "large_corp", "ngo",
        }
    }
    if not entity_tags:
        return EligibilityCheck(
            rule="entity_type",
            status="pass",
            message_tr="Çağrıda kuruluş türü kısıtlaması belirtilmemiş.",
            message_en="No entity-type restriction published for this call.",
        )
    if org.entity_type is None:
        return EligibilityCheck(
            rule="entity_type",
            status="warn",
            message_tr=(
                "Kuruluş türünüz profilde belirtilmemiş — uygunluk "
                "doğrulanamadı."
            ),
            message_en=(
                "Your entity type isn't set in the profile — eligibility "
                "couldn't be confirmed."
            ),
        )
    if org.entity_type in entity_tags:
        return EligibilityCheck(
            rule="entity_type",
            status="pass",
            message_tr=f"Kuruluş türünüz ({org.entity_type}) çağrıya uygun.",
            message_en=f"Your entity type ({org.entity_type}) is eligible.",
        )
    return EligibilityCheck(
        rule="entity_type",
        status="fail",
        message_tr=(
            f"Çağrı yalnızca şu kuruluş türlerini kabul ediyor: "
            f"{', '.join(sorted(entity_tags))}. Sizin türünüz: {org.entity_type}."
        ),
        message_en=(
            f"The call only accepts: {', '.join(sorted(entity_tags))}. "
            f"Your entity type: {org.entity_type}."
        ),
    )


def _check_trl(org: _OrgSnapshot, call: _CallSnapshot) -> EligibilityCheck:
    if call.trl_min is None and call.trl_max is None:
        return EligibilityCheck(
            rule="trl",
            status="pass",
            message_tr="Çağrıda TRL bandı belirtilmemiş.",
            message_en="No TRL band published for this call.",
        )
    if org.trl_current is None:
        return EligibilityCheck(
            rule="trl",
            status="warn",
            message_tr=(
                "Mevcut TRL seviyeniz profilde belirtilmemiş — TRL uyumu "
                "değerlendirilemedi."
            ),
            message_en=(
                "Your current TRL isn't set in the profile — TRL fit "
                "couldn't be assessed."
            ),
        )
    lo = call.trl_min if call.trl_min is not None else 1
    hi = call.trl_max if call.trl_max is not None else 9
    if lo <= org.trl_current <= hi:
        return EligibilityCheck(
            rule="trl",
            status="pass",
            message_tr=f"TRL seviyeniz ({org.trl_current}) çağrı bandında ({lo}-{hi}).",
            message_en=f"Your TRL ({org.trl_current}) is within the call band ({lo}-{hi}).",
        )
    return EligibilityCheck(
        rule="trl",
        status="warn",
        message_tr=(
            f"TRL seviyeniz ({org.trl_current}) çağrı bandının ({lo}-{hi}) "
            "dışında — projeyi bu banda taşıyacak bir gerekçe sunmanız gerekir."
        ),
        message_en=(
            f"Your TRL ({org.trl_current}) sits outside the call band "
            f"({lo}-{hi}) — you'll need to justify moving the project into "
            "that band."
        ),
    )


def _check_consortium(call: _CallSnapshot) -> EligibilityCheck:
    if not call.partner_consortium_required:
        return EligibilityCheck(
            rule="consortium",
            status="pass",
            message_tr="Çağrı konsorsiyum zorunluluğu içermiyor.",
            message_en="The call doesn't require a consortium.",
        )
    return EligibilityCheck(
        rule="consortium",
        status="warn",
        message_tr=(
            "Bu çağrı konsorsiyum gerektiriyor — uygun ortakları (ülke / "
            "kuruluş türü) önceden belirlemeniz gerekir."
        ),
        message_en=(
            "This call requires a consortium — you'll need to line up "
            "eligible partners (country / entity type) before applying."
        ),
    )


# ── Rollup ───────────────────────────────────────────────────────────────


def _rollup(checks: list[EligibilityCheck]) -> EligibilityReport:
    blockers = [c.message_en for c in checks if c.status == "fail"]
    warnings = [c.message_en for c in checks if c.status == "warn"]

    if blockers:
        verdict: Verdict = "NOT_ELIGIBLE"
    elif warnings:
        verdict = "CONDITIONAL"
    else:
        verdict = "ELIGIBLE"

    # Confidence reflects how much of the assessment relied on present
    # data: each 'warn' caused by a missing profile field lowers it.
    missing_data_warns = sum(
        1
        for c in checks
        if c.status == "warn"
        and "couldn't be confirmed" in c.message_en
        or "couldn't be assessed" in c.message_en
    )
    confidence = max(0.4, 1.0 - 0.15 * missing_data_warns)

    return EligibilityReport(
        verdict=verdict,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        confidence=round(confidence, 2),
        checked_at=datetime.now(UTC).isoformat(),
    )


__all__ = [
    "CheckStatus",
    "EligibilityCheck",
    "EligibilityChecker",
    "EligibilityReport",
    "Verdict",
]
