"""
iter116 — Tests for payment redirect fix + AI Error Monitor on IPN.

Verifies:
  1. Hubtel `initiate_checkout` builds the FRONTEND-URL-based returnUrl
     (not the backend) when a separate `public_frontend_url` is provided.
  2. NowPayments `create_invoice` does the same for success_url/cancel_url.
  3. Both keep `callbackUrl`/`ipn_callback_url` on the backend URL.

These are unit-level: they mock out the HTTP call and inspect the payload
that would be sent to the provider, without touching the network/DB.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path
import pytest

# Ensure backend on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _StubResp:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _StubClient:
    """Records the last request and returns a canned response."""
    last_payload = None
    last_url = None

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, *, json=None, headers=None):
        _StubClient.last_url = url
        _StubClient.last_payload = json
        # Mimic Hubtel & NowPayments success shape on the same stub.
        if "hubtel" in url or "POS-Sales" in url:
            return _StubResp(200, {
                "status": "Success", "responseCode": "0000",
                "data": {
                    "checkoutUrl": "https://payment.hubtel.com/abc",
                    "checkoutDirectUrl": "https://payment.hubtel.com/abc/direct",
                    "checkoutTransactionId": "ck_test_123",
                },
            })
        return _StubResp(200, {
            "id": "inv_123",
            "invoice_url": "https://nowpayments.io/payment/inv_123",
        })

    async def get(self, url, *, headers=None):
        _StubClient.last_url = url
        return _StubResp(200, {})


@pytest.mark.asyncio
async def test_hubtel_uses_frontend_url_for_return_redirect(monkeypatch):
    """Even when public_base_url is the BACKEND, returnUrl must point at
    the FRONTEND host so users land back in the JAPAP wallet UI."""
    from services import hubtel_service

    async def _fake_cfg():
        return {
            "client_id": "cid",
            "client_secret": "secret",
            "merchant_account": "12345",
        }
    monkeypatch.setattr(hubtel_service, "_get_config", _fake_cfg)
    monkeypatch.setattr(hubtel_service.httpx, "AsyncClient", _StubClient)

    backend = "https://api.japapmessenger.com"
    frontend = "https://japapmessenger.com"
    await hubtel_service.initiate_checkout(
        tx_id="dep_test_abc",
        amount=10.0,
        description="Test",
        public_base_url=backend,
        public_frontend_url=frontend,
    )
    p = _StubClient.last_payload
    assert p is not None, "Payload not captured"
    # Backend webhook (callbackUrl) — hits the BACKEND
    assert p["callbackUrl"] == f"{backend}/api/wallet/hubtel/webhook"
    # User-facing redirects MUST land on the FRONTEND wallet page
    assert p["returnUrl"].startswith(f"{frontend}/wallet?deposit=success")
    assert p["cancellationUrl"].startswith(f"{frontend}/wallet?deposit=cancelled")
    assert "tx=dep_test_abc" in p["returnUrl"]


@pytest.mark.asyncio
async def test_hubtel_falls_back_to_base_url_when_no_frontend(monkeypatch):
    """Backwards-compat: if no frontend URL is provided, returnUrl falls
    back to the base URL (preview env behaviour)."""
    from services import hubtel_service

    async def _fake_cfg():
        return {"client_id": "x", "client_secret": "y", "merchant_account": "1"}
    monkeypatch.setattr(hubtel_service, "_get_config", _fake_cfg)
    monkeypatch.setattr(hubtel_service.httpx, "AsyncClient", _StubClient)

    base = "https://preview.example.com"
    await hubtel_service.initiate_checkout(
        tx_id="dep_compat_1",
        amount=5.0,
        description="X",
        public_base_url=base,
    )
    p = _StubClient.last_payload
    assert p["returnUrl"].startswith(f"{base}/wallet?deposit=success")
    assert p["callbackUrl"] == f"{base}/api/wallet/hubtel/webhook"


@pytest.mark.asyncio
async def test_nowpayments_invoice_uses_frontend_url_for_redirect(monkeypatch):
    """Same fix on NowPayments hosted invoice."""
    from services import nowpayments_service

    async def _fake_cfg():
        return {"api_key": "k", "base_url": "https://api.nowpayments.io/v1"}
    monkeypatch.setattr(nowpayments_service, "_get_config", _fake_cfg)
    monkeypatch.setattr(nowpayments_service.httpx, "AsyncClient", _StubClient)

    backend = "https://api.japapmessenger.com"
    frontend = "https://japapmessenger.com"
    await nowpayments_service.create_invoice(
        tx_id="dep_npn_1",
        amount_usd=20.0,
        pay_currency="usdttrc20",
        public_base_url=backend,
        public_frontend_url=frontend,
    )
    p = _StubClient.last_payload
    assert p["ipn_callback_url"] == f"{backend}/api/wallet/nowpayments/webhook"
    assert p["success_url"].startswith(f"{frontend}/wallet?deposit=success")
    assert p["cancel_url"].startswith(f"{frontend}/wallet?deposit=cancelled")
    assert "tx=dep_npn_1" in p["success_url"]


@pytest.mark.asyncio
async def test_nowpayments_invoice_fallback_to_base(monkeypatch):
    """Without a frontend URL, falls back to the base URL."""
    from services import nowpayments_service

    async def _fake_cfg():
        return {"api_key": "k", "base_url": "https://api.nowpayments.io/v1"}
    monkeypatch.setattr(nowpayments_service, "_get_config", _fake_cfg)
    monkeypatch.setattr(nowpayments_service.httpx, "AsyncClient", _StubClient)

    base = "https://preview.example.com"
    await nowpayments_service.create_invoice(
        tx_id="dep_npn_compat",
        amount_usd=5.0,
        pay_currency="usdtbsc",
        public_base_url=base,
    )
    p = _StubClient.last_payload
    assert p["success_url"].startswith(f"{base}/wallet?deposit=success")
    assert p["cancel_url"].startswith(f"{base}/wallet?deposit=cancelled")
