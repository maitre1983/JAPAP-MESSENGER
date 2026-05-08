"""
iter194 — Hubtel new credentials + /api/payments alias tests
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


async def main():
    async with httpx.AsyncClient(base_url=API, timeout=60.0) as c:
        # CASE 1 — new merchant creds are applied
        admin_tok = (await c.post("/api/auth/login", json={
            "email": "admin@japap.com", "password": "JapapAdmin2024!",
            "captcha_id": BYPASS, "captcha_answer": "0",
        })).json()["access_token"]

        r = await c.post("/api/wallet/admin/hubtel/debug-initiate",
                          headers={"Authorization": f"Bearer {admin_tok}"},
                          json={"amount_usd": 0.05})
        assert r.status_code == 200, f"debug-initiate: {r.status_code}"
        d = r.json()
        assert d["raw_response"]["responseCode"] == "0000"
        assert d["raw_response"]["status"] == "Success"
        assert d["checkout_url"].startswith("https://pay.hubtel.com/")
        print(f"  CASE 1 OK — new creds (merchant 2029069) accepted by Hubtel")
        print(f"             checkout_url = {d['checkout_url']}")

        # CASE 2 — callbackUrl sent to Hubtel matches CEO spec
        logs = (await c.get("/api/wallet/admin/hubtel/logs?limit=1",
                              headers={"Authorization": f"Bearer {admin_tok}"})).json()["logs"]
        req = logs[0]["request"]
        assert req["callbackUrl"] == "https://japapamessenger.com/api/payments/hubtel/callback", \
            f"unexpected callback: {req['callbackUrl']}"
        assert req["returnUrl"].startswith("https://japapmessenger.com/wallet?status=success"), \
            f"unexpected return: {req['returnUrl']}"
        assert req["merchantAccountNumber"] == "2029069"
        print(f"  CASE 2 OK — callbackUrl matches CEO spec:")
        print(f"             callback: {req['callbackUrl']}")
        print(f"             return:   {req['returnUrl']}")

        # CASE 3 — /api/payments/hubtel/callback route is mounted and
        # responds with the same logic as the legacy /api/wallet path
        probe = {"ResponseCode": "0000", "Status": "Success",
                  "Data": {"CheckoutId": "p", "ClientReference": "nonexistent",
                            "Status": "Success", "Amount": 0.05}}
        r1 = await c.post("/api/payments/hubtel/callback", json=probe)
        r2 = await c.post("/api/wallet/hubtel/webhook", json=probe)
        assert r1.status_code == r2.status_code == 404, \
            f"{r1.status_code} / {r2.status_code}"
        print(f"  CASE 3 OK — new /api/payments/hubtel/callback = legacy behavior (404 unknown tx)")

        # CASE 4 — webhook call logs rows (kind=webhook) accumulate for audit
        r = await c.get("/api/wallet/admin/hubtel/logs?kind=webhook&limit=5",
                         headers={"Authorization": f"Bearer {admin_tok}"})
        assert r.status_code == 200
        print(f"  CASE 4 OK — webhook logs endpoint reachable (count={r.json()['count']})")

    print("\n✅ iter194 — all 4 scenarios passed")


if __name__ == "__main__":
    asyncio.run(main())
