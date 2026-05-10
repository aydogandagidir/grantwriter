"""Unit tests for the Iyzico outbound client.

Three concerns:

A. **Auth header** — the V2 PKI scheme produces deterministic output
   for a known random_key + payload + secret. One known-good vector
   guards against accidental regression in the hash chain.
B. **Happy paths** — ``create_subscription_checkout`` and
   ``cancel_subscription`` both round-trip via ``httpx.MockTransport``
   and decode the JSON response into the typed dataclass.
C. **Error paths** — non-2xx, missing fields, transport errors all
   raise :class:`IyzicoOutboundError` with the status code preserved.

Tests run pure-Python — no network, no DB.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from typing import Any

import httpx
import pytest
from src.billing.iyzico_client import (
    AUTH_SCHEME,
    RANDOM_HEADER,
    CheckoutSession,
    IyzicoClient,
    IyzicoOutboundError,
    _canonical_body,
    build_auth_header,
)

# ── Auth header (A) ────────────────────────────────────────────────────


def test_canonical_body_is_stable_across_dict_orderings() -> None:
    a = {"a": 1, "b": 2}
    b = {"b": 2, "a": 1}
    assert _canonical_body(a) == _canonical_body(b)


def test_build_auth_header_deterministic_with_explicit_random() -> None:
    """A known input yields a known output — guards the hash chain."""

    api_key = "api_xxx"
    secret_key = "secret_yyy"
    uri_path = "/v2/subscription/checkoutform/initialize"
    body = '{"k":"v"}'
    random_key = "fixed-random-1234"

    auth_header, returned_random = build_auth_header(
        api_key=api_key,
        secret_key=secret_key,
        uri_path=uri_path,
        body=body,
        random_key=random_key,
    )

    # Recompute by hand and compare.
    auth_data = (random_key + uri_path + body).encode("utf-8")
    expected_sig = hmac.new(
        secret_key.encode("utf-8"), auth_data, hashlib.sha256
    ).hexdigest()
    expected_auth_string = (
        f"apiKey:{api_key}&randomKey:{random_key}&signature:{expected_sig}"
    )
    expected_encoded = base64.b64encode(
        expected_auth_string.encode("utf-8")
    ).decode("ascii")
    expected_header = f"{AUTH_SCHEME} {expected_encoded}"

    assert auth_header == expected_header
    assert returned_random == random_key


def test_build_auth_header_generates_unique_random_per_call() -> None:
    """Without an explicit random_key, two calls produce different signatures."""

    h1, r1 = build_auth_header(
        api_key="k", secret_key="s", uri_path="/x", body="{}"
    )
    h2, r2 = build_auth_header(
        api_key="k", secret_key="s", uri_path="/x", body="{}"
    )
    assert r1 != r2
    assert h1 != h2


# ── Test transports ───────────────────────────────────────────────────


def _checkout_success_response(token: str, url: str) -> dict[str, Any]:
    return {
        "status": "success",
        "token": token,
        "checkoutFormContent": url,
        "tokenExpireTime": 1800,
    }


def _build_recording_transport(
    *,
    response: httpx.Response,
) -> tuple[httpx.MockTransport, dict[str, httpx.Request]]:
    """Return a transport that records the inbound request for asserts."""

    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return response

    return httpx.MockTransport(handler), captured


# ── Happy paths (B) ───────────────────────────────────────────────────


async def test_create_subscription_checkout_happy_path() -> None:
    response = httpx.Response(
        200,
        json=_checkout_success_response(
            token="tok_abc",
            url="https://sandbox.iyzipay.com/checkout?token=tok_abc",
        ),
    )
    transport, captured = _build_recording_transport(response=response)
    http = httpx.AsyncClient(transport=transport)
    tenant_id = uuid.uuid4()

    async with IyzicoClient(
        api_key="api_xxx",
        secret_key="secret_yyy",
        base_url="https://sandbox-api.iyzipay.com",
        http=http,
    ) as client:
        session = await client.create_subscription_checkout(
            plan_reference_code="iyz_pro_monthly",
            tenant_id=tenant_id,
            customer_email="alice@example.com",
            customer_first_name="Alice",
            customer_last_name="Doe",
            callback_url="https://app.bluedev.dev/billing/return",
        )

    assert isinstance(session, CheckoutSession)
    assert session.token == "tok_abc"
    assert session.payment_page_url == (
        "https://sandbox.iyzipay.com/checkout?token=tok_abc"
    )
    assert session.conversation_id.startswith(f"tenant:{tenant_id}:")

    request = captured["request"]
    assert request.method == "POST"
    assert request.url.path == "/v2/subscription/checkoutform/initialize"
    assert request.headers["Authorization"].startswith(AUTH_SCHEME + " ")
    assert request.headers[RANDOM_HEADER]
    body = json.loads(request.content.decode("utf-8"))
    assert body["pricingPlanReferenceCode"] == "iyz_pro_monthly"
    assert body["customer"]["email"] == "alice@example.com"
    assert body["callbackUrl"] == "https://app.bluedev.dev/billing/return"

    await http.aclose()


async def test_cancel_subscription_happy_path() -> None:
    response = httpx.Response(200, json={"status": "success"})
    transport, captured = _build_recording_transport(response=response)
    http = httpx.AsyncClient(transport=transport)

    async with IyzicoClient(
        api_key="api",
        secret_key="secret",
        base_url="https://sandbox-api.iyzipay.com",
        http=http,
    ) as client:
        result = await client.cancel_subscription(
            subscription_reference_code="sub_ref_123"
        )

    assert result.status == "success"
    assert result.subscription_reference_code == "sub_ref_123"
    request = captured["request"]
    assert request.url.path == (
        "/v2/subscription/subscriptions/sub_ref_123/cancel"
    )
    body = json.loads(request.content.decode("utf-8"))
    assert body["subscriptionReferenceCode"] == "sub_ref_123"

    await http.aclose()


# ── Error paths (C) ───────────────────────────────────────────────────


async def test_non_2xx_raises_iyzico_outbound_error() -> None:
    response = httpx.Response(401, json={"errorMessage": "invalid signature"})
    transport, _ = _build_recording_transport(response=response)
    http = httpx.AsyncClient(transport=transport)

    with pytest.raises(IyzicoOutboundError) as exc_info:
        async with IyzicoClient(
            api_key="api",
            secret_key="secret",
            base_url="https://sandbox-api.iyzipay.com",
            http=http,
        ) as client:
            await client.cancel_subscription(
                subscription_reference_code="sub_ref_123"
            )

    assert exc_info.value.status_code == 401
    assert "invalid signature" in exc_info.value.body
    await http.aclose()


async def test_missing_token_in_checkout_response_raises() -> None:
    """Iyzico returned 200 but no ``token`` field — treat as failure."""

    response = httpx.Response(200, json={"status": "success"})  # no token
    transport, _ = _build_recording_transport(response=response)
    http = httpx.AsyncClient(transport=transport)

    with pytest.raises(IyzicoOutboundError):
        async with IyzicoClient(
            api_key="api",
            secret_key="secret",
            base_url="https://sandbox-api.iyzipay.com",
            http=http,
        ) as client:
            await client.create_subscription_checkout(
                plan_reference_code="iyz_pro_monthly",
                tenant_id=uuid.uuid4(),
                customer_email="alice@example.com",
                customer_first_name="Alice",
                customer_last_name="Doe",
                callback_url="https://app.bluedev.dev/billing/return",
            )

    await http.aclose()


async def test_transport_error_raises_iyzico_outbound_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    transport = httpx.MockTransport(boom)
    http = httpx.AsyncClient(transport=transport)

    with pytest.raises(IyzicoOutboundError) as exc_info:
        async with IyzicoClient(
            api_key="api",
            secret_key="secret",
            base_url="https://sandbox-api.iyzipay.com",
            http=http,
        ) as client:
            await client.cancel_subscription(
                subscription_reference_code="sub_ref_123"
            )

    assert exc_info.value.status_code == 0
    assert "ConnectError" in exc_info.value.body
    await http.aclose()
