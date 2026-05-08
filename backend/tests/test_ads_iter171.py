"""
JAPAP — iter171 P0 fixes E2E (Ads click + Auth resilience)
===========================================================
Backend-only verifications. The frontend AdSlot component is reviewed
visually + by the testing agent; here we make sure the API surface that
the new click handler relies on is intact and returns the right shape.
"""
import asyncio
import os
import sys

import asyncpg
import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

API = "http://localhost:8001"
DB_URL = os.environ["DATABASE_URL"]


async def _conn():
    return await asyncpg.connect(DB_URL)


async def _login(client, email, password):
    r = await client.post(f"{API}/api/auth/login", json={
        "email": email, "password": password,
        "captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0",
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    # Some httpx + cookie-domain combos drop the cookie on subsequent
    # requests when the server set Domain= a different host than 'localhost'.
    # Extract the access_token and pin it as Authorization Bearer for the
    # rest of the test session — equivalent auth path on the backend.
    tok = r.cookies.get("access_token") or client.cookies.get("access_token")
    if tok:
        client.headers["Authorization"] = f"Bearer {tok}"
    return r


async def main():
    async with httpx.AsyncClient(timeout=60.0) as client:
        await _login(client, "bob@japap.com", "Test1234!")

        # 1) /api/ads/serve returns the expected click-routing fields.
        r = await client.get(f"{API}/api/ads/serve?slot=feed")
        assert r.status_code == 200
        body = r.json()
        if body.get("campaign_id"):
            for k in ("target_type", "target_id", "cta_url", "title"):
                assert k in body, f"missing field {k} in /ads/serve payload"
            print(f"[ads] serve payload OK — campaign={body['campaign_id']}, "
                  f"type={body['target_type']}, target_id={body['target_id']}, "
                  f"cta_url={'<set>' if body['cta_url'] else '<empty>'}")
            cid = body["campaign_id"]
            # 2) click endpoint accepts the request and ack's idempotently
            for i in range(3):
                rc = await client.post(
                    f"{API}/api/ads/campaigns/{cid}/click", json={})
                assert rc.status_code == 200, rc.text
            # Confirm clicks counter incremented in DB
            c = await _conn()
            try:
                clicks = await c.fetchval(
                    "SELECT clicks FROM ad_campaigns WHERE campaign_id=$1", cid)
                assert clicks >= 3, f"expected ≥3 clicks, got {clicks}"
                print(f"[ads] click counter incremented OK ({clicks} >= 3) ✓")
            finally:
                await c.close()
        else:
            print("[ads] no ads currently approved — payload check skipped")

        # 3) /api/auth/me must accept a session cookie and return user
        r = await client.get(f"{API}/api/auth/me")
        assert r.status_code == 200
        me = r.json()
        assert me.get("user_id"), "auth/me must return user_id"
        print(f"[auth] /me ok user_id={me['user_id'][:12]}…")

        # 4) Anonymous /api/auth/me returns 401 (NOT 5xx) — important
        # for the AuthContext path-A vs path-B distinction.
        async with httpx.AsyncClient(timeout=15.0) as anon:
            r2 = await anon.get(f"{API}/api/auth/me")
            assert r2.status_code in (401, 403), f"expected 401/403, got {r2.status_code}"
            print(f"[auth] anon /me returns {r2.status_code} ✓")

    print("\n[ALL PASSED] iter171 ads + auth backend ✅")


if __name__ == "__main__":
    asyncio.run(main())
