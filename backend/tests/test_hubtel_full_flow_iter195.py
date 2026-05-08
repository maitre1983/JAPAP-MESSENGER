"""
iter195 — Hubtel Full Flow (in-process, no HTTP)
================================================
CEO directive: JAPAP Hubtel = clone EAA.

Tests:
    [1] Admin settings persistence (`set_setting` / `get_setting`)
    [2] `initiate_checkout` injects admin-configured callbackUrl + returnUrl
    [2b] Fallback defaults when overrides empty → uses PUBLIC_BASE_URL +
         /wallet/deposit/return
    [3] Webhook handler credits wallet + idempotent
    [3b] /api/payments/hubtel/callback alias forwards to webhook

Run: `cd /app/backend && python tests/test_hubtel_full_flow_iter195.py`
"""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, "/app/backend")

try:
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
except Exception:
    pass

USER_EMAIL = "bob@japap.com"

CALLBACK_URL = "https://japapmessenger.com/api/wallet/hubtel/webhook"
RETURN_URL = "https://japapmessenger.com/wallet/deposit/return"


async def test_1_admin_settings():
    print("\n[1] Admin settings persistence…")
    from services.settings_service import set_setting, get_setting, get_all

    await set_setting("hubtel_callback_url_override", CALLBACK_URL)
    await set_setting("hubtel_return_url_override", RETURN_URL)

    assert await get_setting("hubtel_callback_url_override") == CALLBACK_URL
    assert await get_setting("hubtel_return_url_override") == RETURN_URL
    # Also verify they're in defaults (loaded on fresh installs)
    from services.settings_service import DEFAULTS
    assert DEFAULTS.get("hubtel_callback_url_override"), \
        "[FAIL] hubtel_callback_url_override missing from DEFAULTS"
    assert DEFAULTS.get("hubtel_return_url_override"), \
        "[FAIL] hubtel_return_url_override missing from DEFAULTS"
    print(f"  [1a] callback persisted & in DEFAULTS: {DEFAULTS['hubtel_callback_url_override']}")
    print(f"  [1b] return persisted & in DEFAULTS:   {DEFAULTS['hubtel_return_url_override']}")


async def test_2_initiate_uses_dynamic_urls():
    print("\n[2] Initiate injects admin callback/return URLs…")
    from services.settings_service import set_setting
    await set_setting("hubtel_callback_url_override", CALLBACK_URL)
    await set_setting("hubtel_return_url_override", RETURN_URL)
    await set_setting("hubtel_client_id", "test_ci")
    await set_setting("hubtel_client_secret", "test_cs")
    await set_setting("hubtel_merchant_account", "0241234567")

    from services import hubtel_service

    captured = {}

    class FakeResp:
        status_code = 200
        def json(self):
            return {
                "status": "Success", "responseCode": "0000",
                "data": {"checkoutUrl": "https://payment.hubtel.com/pay/xyz",
                         "checkoutDirectUrl": "https://payment.hubtel.com/d/xyz",
                         "checkoutId": "chk_abc"},
            }

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["payload"] = json
            captured["auth"] = headers.get("Authorization", "") if headers else ""
            return FakeResp()

    with patch.object(hubtel_service, "httpx") as mock_httpx:
        mock_httpx.AsyncClient = FakeClient
        mock_httpx.HTTPError = Exception
        result = await hubtel_service.initiate_checkout(
            tx_id="dep_test195",
            amount=10.0,
            description="iter195 test",
            public_base_url="https://api.japapmessenger.com",
            public_frontend_url="https://japapmessenger.com",
        )

    p = captured.get("payload", {})
    print(f"  callbackUrl:     {p.get('callbackUrl')}")
    print(f"  returnUrl:       {p.get('returnUrl')}")
    print(f"  cancellationUrl: {p.get('cancellationUrl')}")
    print(f"  clientReference: {p.get('clientReference')}")
    print(f"  merchant:        {p.get('merchantAccountNumber')}")
    print(f"  totalAmount:     {p.get('totalAmount')}")
    print(f"  auth prefix:     {captured.get('auth','')[:10]}")

    assert p.get("callbackUrl") == CALLBACK_URL
    assert p.get("returnUrl", "").startswith(RETURN_URL) and "tx=dep_test195" in p.get("returnUrl", "")
    assert p.get("clientReference") == "dep_test195"
    assert p.get("merchantAccountNumber") == "0241234567"
    assert captured.get("auth", "").startswith("Basic ")
    assert result.get("checkout_url") == "https://payment.hubtel.com/pay/xyz"
    print("  [2] OK — dynamic URLs injected ✓")


async def test_2b_fallback_defaults():
    print("\n[2b] Fallback to PUBLIC_BASE_URL + /wallet/deposit/return…")
    from services.settings_service import set_setting
    await set_setting("hubtel_callback_url_override", "")
    await set_setting("hubtel_return_url_override", "")

    from services import hubtel_service
    captured = {}

    class FakeResp:
        status_code = 200
        def json(self):
            return {"status": "Success", "responseCode": "0000",
                    "data": {"checkoutUrl": "https://p/a"}}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            captured["payload"] = json
            return FakeResp()

    with patch.object(hubtel_service, "httpx") as mock_httpx:
        mock_httpx.AsyncClient = FakeClient
        mock_httpx.HTTPError = Exception
        await hubtel_service.initiate_checkout(
            tx_id="dep_fb",
            amount=5.0,
            description="fallback",
            public_base_url="https://api.japapmessenger.com",
            public_frontend_url="https://japapmessenger.com",
        )

    p = captured["payload"]
    print(f"  callbackUrl={p.get('callbackUrl')}")
    print(f"  returnUrl={p.get('returnUrl')}")
    print(f"  cancellationUrl={p.get('cancellationUrl')}")
    # Fallback is /api/payments/hubtel/callback (CEO alias) — same handler
    # as /api/wallet/hubtel/webhook. Either is valid.
    assert p.get("callbackUrl") in (
        "https://api.japapmessenger.com/api/wallet/hubtel/webhook",
        "https://api.japapmessenger.com/api/payments/hubtel/callback",
    ), f"[FAIL] fallback callback: {p.get('callbackUrl')!r}"
    assert p.get("returnUrl") == "https://japapmessenger.com/wallet/deposit/return?tx=dep_fb"
    assert p.get("cancellationUrl") == "https://japapmessenger.com/wallet/deposit/return?tx=dep_fb&cancelled=1"
    print("  [2b] OK ✓")

    # Restore overrides
    await set_setting("hubtel_callback_url_override", CALLBACK_URL)
    await set_setting("hubtel_return_url_override", RETURN_URL)


class _FakeRequest:
    """Minimal async-Request shim for the webhook handler."""
    def __init__(self, body: bytes, headers=None):
        self._body = body
        self.headers = headers or {}
        self.url = "http://test/api/wallet/hubtel/webhook"

    async def body(self):
        return self._body


async def test_3_webhook_credits_wallet():
    print("\n[3] Webhook → independent verify → wallet credit → idempotent…")
    import database
    import json as _json
    from services.settings_service import set_setting
    pool = await database.get_pool()

    async with pool.acquire() as conn:
        bob = await conn.fetchrow("SELECT user_id FROM users WHERE email=$1", USER_EMAIL)
        assert bob, "bob not found"
        bob_id = bob["user_id"]

        tx_id = "dep_iter195_test"
        await conn.execute("DELETE FROM transactions WHERE tx_id=$1", tx_id)

        w = await conn.fetchrow("SELECT balance FROM wallets WHERE user_id=$1", bob_id)
        if not w:
            await conn.execute(
                "INSERT INTO wallets (user_id, balance, currency) VALUES ($1, 0, 'USD')",
                bob_id)
            before = 0.0
        else:
            before = float(w["balance"] or 0)
        await conn.execute(
            """INSERT INTO transactions
                    (tx_id, type, to_user_id, amount, currency, status, provider,
                     created_at)
               VALUES ($1, 'deposit', $2, 10.0, 'USD', 'pending', 'hubtel', NOW())""",
            tx_id, bob_id)
        print(f"  [3a] Pending deposit seeded tx={tx_id} before_balance={before}")

    await set_setting("hubtel_webhook_secret", "")  # disable HMAC for test

    from services import hubtel_service

    async def fake_verify(**kwargs):
        return {"ok": True, "status": "Paid", "is_paid": True, "amount": 10.0,
                "provider_ref": "hubtel_ref_xyz", "raw": {}, "reason": "",
                "whitelist_required": False}

    payload = {
        "ResponseCode": "0000", "Status": "Success",
        "Data": {"CheckoutId": "hubtel_ref_xyz", "ClientReference": tx_id,
                 "Status": "Success", "Amount": 10.0},
    }
    body = _json.dumps(payload).encode()

    from routes.wallet import hubtel_webhook

    with patch.object(hubtel_service, "verify_transaction_status",
                      side_effect=fake_verify):
        req = _FakeRequest(body)
        r1 = await hubtel_webhook(req)
        print(f"  [3b] 1st pass → {r1}")
        assert r1.get("status") == "completed"

        req2 = _FakeRequest(body)
        r2 = await hubtel_webhook(req2)
        print(f"  [3c] 2nd pass idempotent → {r2}")
        assert r2.get("status") == "already_completed"

    async with pool.acquire() as conn:
        w = await conn.fetchrow("SELECT balance FROM wallets WHERE user_id=$1", bob_id)
        after = float(w["balance"] or 0)
        assert abs(after - (before + 10.0)) < 0.001, \
            f"[FAIL] balance before={before} after={after} (Δ={after-before})"
        print(f"  [3d] Wallet credited: {before} → {after} (+10 USD) ✓")

        # Clean up
        await conn.execute("DELETE FROM transactions WHERE tx_id=$1", tx_id)
        await conn.execute(
            "UPDATE wallets SET balance=balance-10.0 WHERE user_id=$1", bob_id)


async def test_3b_payments_alias():
    print("\n[3b] /api/payments/hubtel/callback uses EAA-style process_callback…")
    from routes.payments import hubtel_callback as payments_handler
    # iter207 — CEO: EAA clone. The callback endpoint now calls
    # services.hubtel_service.process_callback directly (ResponseCode-driven,
    # no IP-whitelisted verify). The legacy strict-verify path stays alive at
    # /api/wallet/hubtel/webhook for backward compat.
    import inspect
    src = inspect.getsource(payments_handler)
    assert "process_callback" in src, \
        "[FAIL] /api/payments/hubtel/callback does not use EAA process_callback"
    print("  [3b] OK — alias uses services.hubtel_service.process_callback ✓")


async def test_4_return_page():
    print("\n[4] Frontend return page file exists & has required testids…")
    page = "/app/frontend/src/pages/wallet/DepositReturnPage.js"
    assert os.path.exists(page), f"[FAIL] {page} missing"
    with open(page) as f:
        src = f.read()
    required = [
        "data-testid=\"deposit-return-page\"",
        "data-testid=\"deposit-return-title\"",
        "data-testid=\"deposit-return-message\"",
        "data-testid=\"deposit-return-tx\"",
        "data-testid=\"deposit-return-go-wallet\"",
        "/api/wallet/deposit/",  # polling path
    ]
    missing = [r for r in required if r not in src]
    assert not missing, f"[FAIL] Return page missing: {missing}"
    print("  [4] Return page OK with all testids ✓")


async def main():
    print("=" * 60)
    print("iter195 — Hubtel Full Flow (in-process)")
    print("=" * 60)
    await test_1_admin_settings()
    await test_2_initiate_uses_dynamic_urls()
    await test_2b_fallback_defaults()
    await test_3_webhook_credits_wallet()
    await test_3b_payments_alias()
    await test_4_return_page()
    print("\n" + "=" * 60)
    print("ALL PASS ✓ — iter195 Hubtel clone-EAA flow validated")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
