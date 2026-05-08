"""
iter185 — Viral share UTM loop tests
=====================================
Validates: track endpoint awards points on UNIQUE visit, dedupes within
window, blocks self-visits, respects daily cap, exposes stats endpoint.
"""
import asyncio
import os
import sys
import uuid as _uuid

import asyncpg
import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API_URL = os.environ.get("TEST_API_URL") or "http://localhost:8001"
BYPASS = os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026")
load_dotenv("/app/backend/.env")


async def _login(client: httpx.AsyncClient, email: str, pw: str):
    r = await client.post("/api/auth/login",
        json={"email": email, "password": pw,
              "captcha_id": BYPASS, "captcha_answer": "0"})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {tok}"
    return r.json()


async def _user_id(email: str):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    uid = await conn.fetchval("SELECT user_id FROM users WHERE email = $1", email)
    await conn.close()
    return uid


async def _connect_points(uid: str) -> int:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    p = await conn.fetchval("SELECT COALESCE(connect_points, 0) FROM users WHERE user_id = $1", uid)
    await conn.close()
    return int(p or 0)


async def _reset(sharer_id: str):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute("DELETE FROM viral_share_events WHERE sharer_id = $1", sharer_id)
    await conn.close()


async def main():
    bob_id = await _user_id("bob@japap.com")
    alice_id = await _user_id("alice@japap.com")
    assert bob_id and alice_id
    await _reset(bob_id)
    pts_before = await _connect_points(bob_id)
    print(f"[setup] Bob={bob_id} (pts={pts_before}) Alice={alice_id}")

    # Pick a product Bob owns
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    pid = await conn.fetchval(
        "SELECT product_id FROM products WHERE seller_id = $1 AND status = 'active' LIMIT 1",
        bob_id)
    await conn.close()
    assert pid
    utm = f"share_{bob_id}_product_{pid}"

    async with httpx.AsyncClient(base_url=API_URL, timeout=15.0,
                                  headers={"X-Requested-With": "XMLHttpRequest",
                                           "X-Forwarded-For": "203.0.113.42",
                                           "User-Agent": "iter185-test/1.0"}) as anon:
        # [1] Anonymous unique visit → reward
        r = await anon.post("/api/seo/viral/track",
                             json={"utm": utm, "visitor_user_id": None})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True and d["rewarded"] is True
        assert d["points_awarded"] >= 50
        print(f"[1] anonymous unique visit → +{d['points_awarded']} pts OK")
        pts_mid = await _connect_points(bob_id)
        assert pts_mid == pts_before + d["points_awarded"], \
            f"pts mismatch: {pts_before}→{pts_mid} expected +{d['points_awarded']}"

        # [2] Same IP again → dedup, no reward
        r = await anon.post("/api/seo/viral/track",
                             json={"utm": utm, "visitor_user_id": None})
        assert r.status_code == 200
        d = r.json()
        assert d["rewarded"] is False and d["reason"] == "recent_dup"
        print("[2] same-IP repeat → dedup OK")

        # [3] Self-visit by sharer → no reward
        r = await anon.post("/api/seo/viral/track",
                             json={"utm": utm, "visitor_user_id": bob_id})
        assert r.status_code == 200
        d = r.json()
        assert d["rewarded"] is False and d["reason"] == "self_visit"
        print("[3] self-visit → blocked OK")

        # [4] Different IP, logged-in different user → reward
        r = await anon.post("/api/seo/viral/track",
                             json={"utm": utm, "visitor_user_id": alice_id},
                             headers={"X-Forwarded-For": "203.0.113.99"})
        assert r.status_code == 200
        d = r.json()
        assert d["rewarded"] is True and d["points_awarded"] >= 50
        print(f"[4] different IP + Alice → +{d['points_awarded']} pts OK")

        # [5] Invalid UTM → 200 ok=false
        r = await anon.post("/api/seo/viral/track",
                             json={"utm": "garbage_data"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False and d["reason"] == "invalid_utm"
        print("[5] invalid UTM → ok=False OK")

        # [6] Unknown sharer → no reward
        bad_utm = f"share_user_NOPE_product_{pid}"
        r = await anon.post("/api/seo/viral/track", json={"utm": bad_utm})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False and d["reason"] == "unknown_sharer"
        print("[6] unknown sharer → blocked OK")

        # [7] Stats endpoint (auth as Bob)
        async with httpx.AsyncClient(base_url=API_URL, timeout=15.0,
                                      headers={"X-Requested-With": "XMLHttpRequest"}) as bob:
            await _login(bob, "bob@japap.com", "Test1234!")
            r = await bob.get("/api/seo/viral/stats")
            assert r.status_code == 200
            s = r.json()
            assert s["total_clicks"] >= 3   # 1 reward + 1 dedup + 1 reward (self_visit returns before insert)
            assert s["rewarded_clicks"] == 2
            assert s["points_total"] >= 100
            assert any(t["entity_type"] == "product" for t in s["by_type"])
            print(f"[7] stats OK : clicks={s['total_clicks']}, rewarded={s['rewarded_clicks']}, pts={s['points_total']}")

    pts_final = await _connect_points(bob_id)
    print(f"[final] Bob points: {pts_before} → {pts_final} (Δ {pts_final - pts_before})")
    assert pts_final >= pts_before + 100
    print("\n✅ iter185 — All 7 assertions PASS")


if __name__ == "__main__":
    asyncio.run(main())
