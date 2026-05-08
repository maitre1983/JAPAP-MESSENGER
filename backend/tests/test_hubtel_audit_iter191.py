"""
iter191 — Hubtel audit, debug endpoints & callbackUrl safeguard
================================================================
Validates the diagnostic + safeguard layer added to the Hubtel pipeline.

Cases:
  1. POST /api/wallet/admin/hubtel/debug-initiate  → 200, real checkoutUrl
     on pay.hubtel.com, response from Hubtel = Success/0000.
  2. GET  /api/wallet/admin/hubtel/logs?limit=5    → list with persisted
     request/response/status/took_ms for our debug call.
  3. The persisted callbackUrl MUST be public HTTPS (no localhost / IP).
  4. _normalize_msisdn_gh handles +233 / 233 / 0XX / 9 digits.
  5. initiate_checkout raises HubtelConfigError when PUBLIC_BASE_URL is
     localhost (safeguard against silent webhook drops).
  6. Non-admin gets 403 on both debug endpoints.
"""
import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

API = os.environ.get("TEST_API_URL", "http://localhost:8001")
BYPASS = os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026")


async def _login(c: httpx.AsyncClient, email: str, pw: str) -> str:
    r = await c.post("/api/auth/login", json={
        "email": email, "password": pw,
        "captcha_id": BYPASS, "captcha_answer": "0",
    })
    assert r.status_code == 200, f"login failed: {r.text[:200]}"
    return r.json()["access_token"]


async def main():
    # ── Pure unit: msisdn normalizer ────────────────────────────────────
    from services.hubtel_service import _normalize_msisdn_gh
    assert _normalize_msisdn_gh("+233241234567") == "233241234567"
    assert _normalize_msisdn_gh("233241234567") == "233241234567"
    assert _normalize_msisdn_gh("0241234567") == "233241234567"
    assert _normalize_msisdn_gh("241234567") == "233241234567"
    assert _normalize_msisdn_gh("") == ""
    print("  CASE 4 OK — _normalize_msisdn_gh covers +233/233/0XX/9-digit forms")

    # ── Pure unit: localhost callbackUrl safeguard ──────────────────────
    from services.hubtel_service import initiate_checkout, HubtelConfigError
    try:
        await initiate_checkout(
            tx_id="local_test",
            amount=0.05,
            description="t",
            public_base_url="http://localhost:8001",
        )
        raise AssertionError("CASE 5 FAILED — localhost callbackUrl was accepted!")
    except HubtelConfigError as e:
        assert "PUBLIC_BASE_URL" in str(e) or "localhost" in str(e).lower()
        print("  CASE 5 OK — localhost callbackUrl rejected (HubtelConfigError)")

    # Same with HTTPS but private LAN
    try:
        await initiate_checkout(
            tx_id="lan_test", amount=0.05, description="t",
            public_base_url="https://10.20.0.5",
        )
        raise AssertionError("CASE 5b FAILED — private IP accepted!")
    except HubtelConfigError:
        print("  CASE 5b OK — private LAN address rejected")

    # ── Live debug endpoints ────────────────────────────────────────────
    async with httpx.AsyncClient(base_url=API, timeout=60.0) as c:
        admin_tok = await _login(c, "admin@japap.com", "JapapAdmin2024!")
        bob_tok = await _login(c, "bob@japap.com", "Test1234!")

        # CASE 6 — non-admin gets 403
        r = await c.post("/api/wallet/admin/hubtel/debug-initiate",
                          headers={"Authorization": f"Bearer {bob_tok}"},
                          json={"amount_usd": 0.05})
        assert r.status_code == 403, f"non-admin should 403, got {r.status_code}"
        r = await c.get("/api/wallet/admin/hubtel/logs",
                         headers={"Authorization": f"Bearer {bob_tok}"})
        assert r.status_code == 403
        print("  CASE 6 OK — non-admin gets 403 on /admin/hubtel endpoints")

        # CASE 1 — live debug initiate
        r = await c.post("/api/wallet/admin/hubtel/debug-initiate",
                          headers={"Authorization": f"Bearer {admin_tok}"},
                          json={"amount_usd": 0.05})
        assert r.status_code == 200, f"debug-initiate failed: {r.text[:200]}"
        d = r.json()
        assert d["ok"] is True
        assert d["checkout_url"].startswith("https://pay.hubtel.com/"), \
            f"unexpected checkout host: {d['checkout_url']}"
        assert d["checkout_tx_id"], "checkout_tx_id (Hubtel checkoutId) missing"
        raw = d["raw_response"]
        assert raw.get("responseCode") == "0000"
        assert raw.get("status") == "Success"
        tx_id = d["tx_id"]
        print(f"  CASE 1 OK — live initiate → {d['checkout_url']}")

        # CASE 2 — logs endpoint reflects the debug call
        r = await c.get("/api/wallet/admin/hubtel/logs?limit=5",
                         headers={"Authorization": f"Bearer {admin_tok}"})
        assert r.status_code == 200, r.text[:200]
        logs = r.json()["logs"]
        assert any(log["tx_id"] == tx_id for log in logs), \
            f"debug tx_id {tx_id} not found in logs"
        log = next(log for log in logs if log["tx_id"] == tx_id)
        assert log["kind"] == "initiate"
        assert log["response_status"] == 200
        assert log["took_ms"] is not None and log["took_ms"] > 0
        print(f"  CASE 2 OK — call log persisted (id={log['id']}, took={log['took_ms']}ms)")

        # CASE 3 — callbackUrl is public HTTPS
        cb = log["request"]["callbackUrl"]
        assert cb.startswith("https://"), f"callbackUrl not HTTPS: {cb}"
        for bad in ("localhost", "127.0.0.1", "10.", "192.168.", "172.16."):
            assert bad not in cb, f"callbackUrl contains private host {bad}: {cb}"
        print(f"  CASE 3 OK — callbackUrl is public HTTPS: {cb}")

    print("\n✅ iter191 — all 6+ scenarios passed")


if __name__ == "__main__":
    asyncio.run(main())
