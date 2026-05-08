"""
iter180 — Ads Console tests
===========================
Tests for the internal JAPAP Ads regime (Wallet USD only, audience targeting,
CPM/CPC consumption). Covers happy path + edge cases.

Usage: cd /app/backend && python3 tests/test_ads_console_iter180.py
"""
import asyncio
import os
import sys
from decimal import Decimal

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API_URL = os.environ.get("TEST_API_URL") or os.environ.get("FRONTEND_URL") or "http://localhost:8001"
if not API_URL.startswith("http"):
    API_URL = "http://localhost:8001"
BYPASS = os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026")

CRED_BOB = {"email": "bob@japap.com", "password": "Test1234!"}
CRED_ALICE = {"email": "alice@japap.com", "password": "Alice2026!"}


async def _login(client: httpx.AsyncClient, cred: dict) -> str:
    """Login and return the access_token for Authorization header.
    Using Bearer auth avoids cookie Secure/HTTP mismatch issues in tests."""
    r = await client.post(
        "/api/auth/login",
        json={**cred, "captcha_id": BYPASS, "captcha_answer": "0"},
    )
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text}"
    tok = r.json().get("access_token")
    assert tok, "no access_token in login response"
    client.headers["Authorization"] = f"Bearer {tok}"
    return tok


async def _product_id_of(client: httpx.AsyncClient, seller_id: str) -> str:
    r = await client.get("/api/marketplace/products?limit=50")
    assert r.status_code == 200
    for p in r.json().get("products", []):
        if p.get("seller_id") == seller_id and p.get("status") == "active":
            return p["product_id"]
    raise RuntimeError(f"no active product for seller {seller_id}")


async def main():
    async with httpx.AsyncClient(base_url=API_URL, timeout=30.0,
                                  headers={"X-Requested-With": "XMLHttpRequest"}) as bob:
        await _login(bob, CRED_BOB)
        me = (await bob.get("/api/auth/me")).json()
        bob_id = me.get("user_id") or (me.get("user") or {}).get("user_id")
        assert bob_id
        pid = await _product_id_of(bob, bob_id)
        print(f"[setup] Bob={bob_id} product={pid}")

        # [1] Create campaign — happy path
        r = await bob.post("/api/ads_console/campaigns", json={
            "product_id": pid, "budget_usd": 5.5, "duration_days": 3, "is_global": True,
        })
        assert r.status_code == 200, f"[1] create 200 expected, got {r.status_code}: {r.text}"
        camp = r.json()
        cid = camp["campaign_id"]
        print(f"[1] create OK: {cid} budget={camp['budget_usd']}")

        # [2] List mine
        r = await bob.get("/api/ads_console/campaigns/mine")
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(c["campaign_id"] == cid for c in items), "new campaign not in list"
        print(f"[2] list mine OK ({len(items)} camps)")

        # [3] Below min budget
        r = await bob.post("/api/ads_console/campaigns", json={
            "product_id": pid, "budget_usd": 1.0, "duration_days": 3, "is_global": True,
        })
        assert r.status_code == 400, f"[3] expected 400, got {r.status_code}"
        assert "minimum" in r.text.lower()
        print("[3] below min budget → 400 OK")

        # [4] Insufficient balance
        r = await bob.post("/api/ads_console/campaigns", json={
            "product_id": pid, "budget_usd": 9_999_999.0, "duration_days": 3, "is_global": True,
        })
        assert r.status_code == 400, f"[4] expected 400, got {r.status_code}"
        assert "INSUFFICIENT" in r.text
        print("[4] insufficient balance → 400 OK")

        # [5] Product not owned (try with a non-Bob product)
        r0 = await bob.get("/api/marketplace/products?limit=50")
        other = next((p for p in r0.json().get("products", [])
                      if p.get("seller_id") != bob_id and p.get("status") == "active"), None)
        if other:
            r = await bob.post("/api/ads_console/campaigns", json={
                "product_id": other["product_id"], "budget_usd": 6, "duration_days": 3, "is_global": True,
            })
            assert r.status_code == 404
            print("[5] non-owned product → 404 OK")
        else:
            print("[5] skipped (no other seller product)")

    async with httpx.AsyncClient(base_url=API_URL, timeout=30.0,
                                  headers={"X-Requested-With": "XMLHttpRequest"}) as alice:
        await _login(alice, CRED_ALICE)

        # [6] Feed shows Bob's campaign to Alice
        r = await alice.get("/api/ads_console/feed?limit=10")
        assert r.status_code == 200
        items = r.json()["items"]
        # cid should be visible
        assert any(c["campaign_id"] == cid for c in items), \
            f"Bob's campaign {cid} not shown to Alice in feed"
        print(f"[6] feed injection OK (alice sees {len(items)} slots incl {cid})")

        # [7] Impression CPM debit
        async with httpx.AsyncClient(base_url=API_URL, timeout=30.0,
                                      headers={"X-Requested-With": "XMLHttpRequest"}) as bob:
            await _login(bob, CRED_BOB)
            r_before = (await bob.get("/api/ads_console/campaigns/mine")).json()
            before = next(c for c in r_before["items"] if c["campaign_id"] == cid)
            spent_before = Decimal(before["spent_usd"])
            imp_before = before["impressions"]

            r = await alice.post("/api/ads_console/impress",
                                  json={"campaign_ids": [cid]})
            assert r.status_code == 200 and r.json()["logged"] == 1

            r_after = (await bob.get("/api/ads_console/campaigns/mine")).json()
            after = next(c for c in r_after["items"] if c["campaign_id"] == cid)
            assert after["impressions"] == imp_before + 1, \
                f"imp expected {imp_before+1}, got {after['impressions']}"
            assert Decimal(after["spent_usd"]) > spent_before, \
                f"spent should grow after impression"
            print(f"[7] impression CPM debit OK (spent {spent_before}→{after['spent_usd']})")

            # [8] Click CPC debit
            spent_mid = Decimal(after["spent_usd"])
            r = await alice.post("/api/ads_console/click", json={"campaign_id": cid})
            assert r.status_code == 200 and r.json()["ok"] is True

            r_final = (await bob.get("/api/ads_console/campaigns/mine")).json()
            final = next(c for c in r_final["items"] if c["campaign_id"] == cid)
            spent_click = Decimal(final["spent_usd"])
            cpc = Decimal(final["cpc_rate"])
            assert (spent_click - spent_mid) >= cpc * Decimal("0.99"), \
                f"click should add ~cpc {cpc}, got delta {spent_click - spent_mid}"
            print(f"[8] click CPC debit OK (spent {spent_mid}→{spent_click}, cpc={cpc})")

            # [9] Pause / Resume
            r = await bob.patch(f"/api/ads_console/campaigns/{cid}",
                                 json={"status": "paused"})
            assert r.status_code == 200
            # Feed should NOT show paused
            r_paused = (await alice.get("/api/ads_console/feed?limit=10")).json()
            assert not any(c["campaign_id"] == cid for c in r_paused["items"]), \
                "paused campaign must not be served"
            # Resume
            r = await bob.patch(f"/api/ads_console/campaigns/{cid}",
                                 json={"status": "active"})
            assert r.status_code == 200
            print("[9] pause/resume OK")

            # [10] Marketplace feed returns sponsored_slots for Alice
            r = await alice.get("/api/marketplace/products?limit=20")
            assert r.status_code == 200
            data = r.json()
            assert "sponsored_slots" in data
            # Bob's campaign may or may not show depending on product count (slot_every=5),
            # but key must be present.
            print(f"[10] marketplace sponsored_slots field present ({len(data['sponsored_slots'])} slots)")

            # [11] Legacy /api/ads/serve must still respond (no conflict)
            r = await alice.get("/api/ads/serve?slot=marketplace")
            assert r.status_code == 200, f"[11] legacy /ads/serve broken: {r.status_code}"
            print("[11] legacy /api/ads/serve still works OK")

    print("\n✅ iter180 — All 11 assertions PASS")


if __name__ == "__main__":
    asyncio.run(main())
