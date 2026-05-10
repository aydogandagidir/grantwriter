"""Iyzico outbound API client — pure-httpx, no SDK.

Companion module to :mod:`src.billing.iyzico` (the inbound webhook
receiver). This one talks the other direction: hosted-checkout session
initialisation + subscription cancel. Everything else operators run by
hand from the Iyzico merchant panel today.

Why no SDK: the official ``iyzipay`` Python package adds ~12
transitive deps (see CLAUDE.md "no new deps without discussion"), and
we already implement HMAC for the inbound side. Going SDK-less keeps
the dependency footprint flat and means tests can drive the wire with
``httpx.MockTransport`` without monkey-patching a third-party.

Authentication uses Iyzico's V2 PKI scheme:

    random_key  = monotonic per-request string (timestamp ms)
    body        = canonical JSON of the request payload
    auth_data   = random_key || uri_path || body
    signature   = hex(HMAC-SHA256(secret_key, auth_data))
    auth_string = "apiKey:{api_key}&randomKey:{random_key}&signature:{signature}"
    Authorization: IYZWSv2 <base64(auth_string)>
    x-iyzi-rnd:    <random_key>

The signature is over the EXACT JSON body bytes shipped on the wire —
re-serialising with different separator/indent settings would yield
a different signature, so the body is built once, used for both signing
and the request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

AUTH_SCHEME = "IYZWSv2"
RANDOM_HEADER = "x-iyzi-rnd"
_HTTP_BAD_REQUEST = 400  # any status >= 400 is a non-success


class IyzicoOutboundError(Exception):
    """A non-2xx response from the Iyzico API.

    Carries the HTTP status + the raw response body so the caller can
    audit-log without re-fetching. Body is small (JSON dict) and we
    accept the leak for diagnostic value — never contains the API key.
    """

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Iyzico API returned {status_code}: {body[:300]}")
        self.status_code = status_code
        self.body = body


# ── Result models ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckoutSession:
    """Hosted-checkout payload returned by ``initialize``.

    ``payment_page_url`` is the URL the FE redirects the user to —
    Iyzico hosts the card form, then bounces the user back to our
    ``callback_url`` with a token the webhook receiver later validates.
    ``conversation_id`` is our correlation id; we generate it so we can
    join the eventual webhook event back to this session.
    """

    token: str
    payment_page_url: str
    conversation_id: str


@dataclass(frozen=True)
class CancelResult:
    """Result of ``POST /subscription/{ref}/cancel``."""

    subscription_reference_code: str
    status: str  # Iyzico's status string — usually "success"


# ── Auth ──────────────────────────────────────────────────────────────


def _canonical_body(payload: dict[str, Any]) -> str:
    """Stable JSON serialisation: no whitespace, sorted keys.

    Two callers serialise the same dict and must produce byte-for-byte
    identical output (signing + transport). Sorted keys defends against
    Python's dict ordering quirks across versions.
    """

    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def build_auth_header(
    *,
    api_key: str,
    secret_key: str,
    uri_path: str,
    body: str,
    random_key: str | None = None,
) -> tuple[str, str]:
    """Build the ``Authorization`` + ``x-iyzi-rnd`` header pair.

    ``random_key`` is exposed for deterministic tests; production
    callers leave it ``None`` and a millisecond timestamp + uuid is
    generated. Returns ``(authorization_value, random_key)``.
    """

    if random_key is None:
        random_key = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:12]}"

    auth_data = (random_key + uri_path + body).encode("utf-8")
    signature = hmac.new(
        secret_key.encode("utf-8"), auth_data, hashlib.sha256
    ).hexdigest()
    auth_string = (
        f"apiKey:{api_key}&randomKey:{random_key}&signature:{signature}"
    )
    encoded = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")
    return f"{AUTH_SCHEME} {encoded}", random_key


# ── Client ────────────────────────────────────────────────────────────


class IyzicoClient:
    """Thin async wrapper around two Iyzico endpoints we actually use.

    Tests drive it via ``http=httpx.AsyncClient(transport=mock)``; the
    rest of the app uses the default constructor which spins up its
    own client. Pass an explicit ``base_url`` to override the sandbox
    default for live testing.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        base_url: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._http = http
        self._owns_http = http is None

    async def __aenter__(self) -> IyzicoClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── Hosted checkout ───────────────────────────────────────────────

    async def create_subscription_checkout(
        self,
        *,
        plan_reference_code: str,
        tenant_id: UUID,
        customer_email: str,
        customer_first_name: str,
        customer_last_name: str,
        callback_url: str,
        locale: str = "tr",
    ) -> CheckoutSession:
        """Initialise a hosted-checkout session.

        Iyzico returns a ``paymentPageUrl`` we redirect the user to;
        upon completion the merchant panel posts a webhook our inbound
        receiver picks up. ``tenant_id`` flows into ``conversationId``
        so the eventual webhook can be tied back to the originating
        tenant even if Iyzico's customerReferenceCode mapping is stale.
        """

        uri_path = "/v2/subscription/checkoutform/initialize"
        conversation_id = f"tenant:{tenant_id}:{uuid.uuid4().hex[:8]}"
        payload: dict[str, Any] = {
            "locale": locale,
            "conversationId": conversation_id,
            "callbackUrl": callback_url,
            "pricingPlanReferenceCode": plan_reference_code,
            "subscriptionInitialStatus": "ACTIVE",
            "customer": {
                "name": customer_first_name,
                "surname": customer_last_name,
                "identityNumber": "11111111111",  # placeholder until KYC
                "email": customer_email,
                "gsmNumber": "+905555555555",  # placeholder until profile
                "billingAddress": {
                    "contactName": (
                        f"{customer_first_name} {customer_last_name}".strip()
                    ),
                    "city": "Istanbul",
                    "country": "Turkey",
                    "address": "N/A",
                    "zipCode": "34000",
                },
                "shippingAddress": {
                    "contactName": (
                        f"{customer_first_name} {customer_last_name}".strip()
                    ),
                    "city": "Istanbul",
                    "country": "Turkey",
                    "address": "N/A",
                    "zipCode": "34000",
                },
            },
        }
        response = await self._post(uri_path, payload)
        token = str(response.get("token") or "")
        url = str(response.get("checkoutFormContent") or response.get("paymentPageUrl") or "")
        if not token or not url:
            raise IyzicoOutboundError(
                status_code=200,
                body=f"missing token/url in response: {response}",
            )
        return CheckoutSession(
            token=token, payment_page_url=url, conversation_id=conversation_id
        )

    # ── Subscription cancel ───────────────────────────────────────────

    async def cancel_subscription(
        self, *, subscription_reference_code: str
    ) -> CancelResult:
        """Cancel an active Iyzico subscription.

        Iyzico's API uses a path-parameter for the reference code; the
        body is a small JSON object so the signature still works the
        same way (auth_data covers uri_path + body).
        """

        uri_path = (
            f"/v2/subscription/subscriptions/{subscription_reference_code}/cancel"
        )
        payload: dict[str, Any] = {
            "locale": "tr",
            "conversationId": uuid.uuid4().hex,
            "subscriptionReferenceCode": subscription_reference_code,
        }
        response = await self._post(uri_path, payload)
        return CancelResult(
            subscription_reference_code=subscription_reference_code,
            status=str(response.get("status") or "success"),
        )

    # ── Internal ──────────────────────────────────────────────────────

    async def _post(
        self, uri_path: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Sign + POST + decode. Raises on non-2xx."""

        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
            self._owns_http = True

        body = _canonical_body(payload)
        authorization, random_key = build_auth_header(
            api_key=self._api_key,
            secret_key=self._secret_key,
            uri_path=uri_path,
            body=body,
        )
        url = f"{self._base_url}{uri_path}"

        try:
            response = await self._http.post(
                url,
                content=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json",
                    "Authorization": authorization,
                    RANDOM_HEADER: random_key,
                },
            )
        except httpx.HTTPError as exc:
            raise IyzicoOutboundError(
                status_code=0, body=f"transport error: {type(exc).__name__}"
            ) from exc

        if response.status_code >= _HTTP_BAD_REQUEST:
            raise IyzicoOutboundError(
                status_code=response.status_code, body=response.text
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise IyzicoOutboundError(
                status_code=response.status_code,
                body=f"non-JSON response: {response.text[:200]}",
            ) from exc

        if not isinstance(data, dict):
            raise IyzicoOutboundError(
                status_code=response.status_code,
                body=f"expected JSON object, got {type(data).__name__}",
            )
        return data


__all__ = [
    "AUTH_SCHEME",
    "CancelResult",
    "CheckoutSession",
    "IyzicoClient",
    "IyzicoOutboundError",
    "RANDOM_HEADER",
    "build_auth_header",
]
