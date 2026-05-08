"""
iter175 — Marketplace Sponsored Boost (Wallet USD) + 24h View Counter
=====================================================================
"""
import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, "/app/backend")
import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

API = "http://localhost:8001"
DATABASE_URL = os.environ["DATABASE_URL"]
BYPASS = "JAPAP_E2E_BYPASS_2026"


async def _reset(emails):
    c = await asyncpg.connect(DATABASE_URL)
    try:
        for e in emails:
            await c.execute("DELETE FROM login_attempts WHERE identifier=$1", e)
    finally:
        await c.close()


async def login_token(client, email, password):
    r = await client.post(f"{API}/api/auth/login", json={
        "email": email, "password": password,
        "captcha_id": BYPASS, "captcha_answer": "0",
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    return r.cookies.get("access_token")


def H(token):
    return {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}


async def fund_wallet(user_id: str, amount_usd: float):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("""
            INSERT INTO wallets (user_id, balance, currency, created_at)
            VALUES ($1, $2, 'USD', NOW())
            ON CONFLICT (user_id) DO UPDATE SET balance = wallets.balance + EXCLUDED.balance
        """, user_id, Decimal(str(amount_usd)))
    finally:
        await conn.close()


async def set_balance(user_id: str, amount_usd: float):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "UPDATE wallets SET balance=$2 WHERE user_id=$1",
            user_id, Decimal(str(amount_usd)))
    finally:
        await conn.close()


async def get_balance(user_id: str) -> Decimal:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        v = await conn.fetchval("SELECT balance FROM wallets WHERE user_id=$1", user_id)
        return Decimal(str(v or 0))
    finally:
        await conn.close()


async def main():
    await _reset(["bob@japap.com", "alice@japap.com", "admin@japap.com"])

    async with httpx.AsyncClient(timeout=30) as c:
        bob_t = await login_token(c, "bob@japap.com", "Test1234!")
        alice_t = await login_token(c, "alice@japap.com", "Alice2026!")
        bob = (await c.get(f"{API}/api/auth/me", headers=H(bob_t))).json()
        alice = (await c.get(f"{API}/api/auth/me", headers=H(alice_t))).json()
        bob_id, alice_id = bob["user_id"], alice["user_id"]
        print(f"[setup] bob={bob_id} alice={alice_id}")

        await set_balance(bob_id, 50.0)
        bal0 = await get_balance(bob_id)
        print(f"[setup] bob initial balance = {bal0} USD")

        # Create product (Bob)
        r = await c.post(f"{API}/api/marketplace/products",
            json={"title": "iter175 boost test product", "description": "boost test",
                  "price": 9.99, "category": "general",
                  "images": [{"thumb": "/x.jpg", "full": "/x.jpg", "hd": "/x.jpg"}]},
            headers=H(bob_t))
        r.raise_for_status()
        pid = r.json()["product_id"]
        print(f"[setup] product_id = {pid}")

        # ── [1] view counter — alice views once
        r = await c.get(f"{API}/api/marketplace/products/{pid}", headers=H(alice_t))
        r.raise_for_status()
        v1 = r.json()
        assert v1["views_24h"] >= 1, f"got {v1['views_24h']}"
        print(f"[1] alice 1st view: views_24h={v1['views_24h']} ✓")

        # alice repeat → dedup
        r = await c.get(f"{API}/api/marketplace/products/{pid}", headers=H(alice_t))
        v2 = r.json()
        assert v2["views_24h"] == v1["views_24h"]
        print(f"[1] alice repeat dedup: {v2['views_24h']} ✓")

        # owner Bob views → no count
        r = await c.get(f"{API}/api/marketplace/products/{pid}", headers=H(bob_t))
        v3 = r.json()
        assert v3["views_24h"] == v2["views_24h"], "owner view should not count"
        print(f"[2] owner view does not increment: {v3['views_24h']} ✓")

        # ── [3] catalogue
        r = await c.get(f"{API}/api/marketplace/boost/plans", headers=H(bob_t))
        r.raise_for_status()
        plans = {p["plan"]: p for p in r.json()["plans"]}
        assert plans["basic_24h"]["price_usd"] == "1.00"
        assert plans["standard_7d"]["price_usd"] == "5.00"
        assert plans["homepage_30d"]["price_usd"] == "10.00"
        assert plans["homepage_30d"]["is_homepage"] is True
        print(f"[3] plan catalogue 1$/5$/10$ ✓")

        # ── [4] insufficient balance — Alice creates her own product, drained wallet
        r = await c.post(f"{API}/api/marketplace/products",
            json={"title": "Alice produit", "description": "x",
                  "price": 5.0, "category": "general",
                  "images": [{"thumb": "/y.jpg", "full": "/y.jpg", "hd": "/y.jpg"}]},
            headers=H(alice_t))
        r.raise_for_status()
        alice_pid = r.json()["product_id"]
        await set_balance(alice_id, 0.0)
        r = await c.post(f"{API}/api/marketplace/products/{alice_pid}/boost-wallet",
                         json={"plan": "basic_24h"}, headers=H(alice_t))
        assert r.status_code == 400, f"got {r.status_code}"
        assert "INSUFFICIENT" in r.json()["detail"]
        print(f"[4] insufficient → 400 ({r.json()['detail']}) ✓")

        # ── [5] basic_24h boost
        r = await c.post(f"{API}/api/marketplace/products/{pid}/boost-wallet",
                         json={"plan": "basic_24h"}, headers=H(bob_t))
        r.raise_for_status()
        body = r.json()
        assert body["plan"] == "basic_24h"
        assert body["price_usd"] == "1.00"
        assert body["is_homepage"] is False
        bal1 = await get_balance(bob_id)
        assert bal1 == bal0 - Decimal("1.00"), f"{bal1} != {bal0}-1"
        print(f"[5] 24h boost active: bal {bal0}→{bal1} expires={body['expires_at']} ✓")

        # ── [6] homepage_30d
        r = await c.post(f"{API}/api/marketplace/products/{pid}/boost-wallet",
                         json={"plan": "homepage_30d"}, headers=H(bob_t))
        r.raise_for_status()
        body = r.json()
        assert body["is_homepage"] is True
        bal2 = await get_balance(bob_id)
        assert bal2 == bal1 - Decimal("10.00"), f"{bal2}"
        print(f"[6] homepage boost active: bal {bal1}→{bal2} ✓")

        # ── [7] /featured
        r = await c.get(f"{API}/api/marketplace/featured", headers=H(alice_t))
        r.raise_for_status()
        items = r.json()["items"]
        assert any(it["product_id"] == pid for it in items), \
            f"missing pid; got {[i['product_id'] for i in items]}"
        print(f"[7] /featured returns product (total={len(items)}) ✓")

        # ── [8] non-owner cannot boost
        r = await c.post(f"{API}/api/marketplace/products/{pid}/boost-wallet",
                         json={"plan": "basic_24h"}, headers=H(alice_t))
        assert r.status_code == 403, f"got {r.status_code}"
        print(f"[8] non-owner blocked (403) ✓")

        # ── [9] sweep
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute(
            "UPDATE products SET boost_expires_at = NOW() - INTERVAL '1 hour', "
            "homepage_expires_at = NOW() - INTERVAL '1 hour' WHERE product_id=$1", pid)
        await conn.close()
        admin_t = await login_token(c, "admin@japap.com", "JapapAdmin2024!")
        r = await c.post(f"{API}/api/marketplace/admin/boosts/sweep-expired",
                         headers=H(admin_t))
        r.raise_for_status()
        sweep = r.json()
        assert sweep["products_unboosted"] >= 1
        assert sweep["homepage_cleared"] >= 1
        print(f"[9] sweep cleared expired: {sweep} ✓")

        r = await c.get(f"{API}/api/marketplace/featured", headers=H(alice_t))
        items = r.json()["items"]
        assert not any(it["product_id"] == pid for it in items)
        print(f"[9] expired removed from /featured ✓")

        # cleanup
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute("DELETE FROM products WHERE product_id IN ($1, $2)", pid, alice_pid)
        await conn.close()

    print("\n✅ ALL 9 TESTS PASS — iter175 marketplace boost+views OK")


if __name__ == "__main__":
    asyncio.run(main())
