"""
iter175 — Extended coverage: standard_7d plan, ledger, product_boosts row, currency=USD
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


async def login_token(client, email, password):
    r = await client.post(f"{API}/api/auth/login", json={
        "email": email, "password": password,
        "captcha_id": BYPASS, "captcha_answer": "0",
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    return r.cookies.get("access_token")


def H(token):
    return {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}


async def set_balance(user_id, amount):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("""
            INSERT INTO wallets (user_id, balance, currency, created_at)
            VALUES ($1, $2, 'USD', NOW())
            ON CONFLICT (user_id) DO UPDATE SET balance = EXCLUDED.balance
        """, user_id, Decimal(str(amount)))
    finally:
        await conn.close()


async def reset_attempts(emails):
    c = await asyncpg.connect(DATABASE_URL)
    try:
        for e in emails:
            await c.execute("DELETE FROM login_attempts WHERE identifier=$1", e)
    finally:
        await c.close()


async def main():
    await reset_attempts(["bob@japap.com", "alice@japap.com"])
    async with httpx.AsyncClient(timeout=30) as c:
        bob_t = await login_token(c, "bob@japap.com", "Test1234!")
        bob = (await c.get(f"{API}/api/auth/me", headers=H(bob_t))).json()
        bob_id = bob["user_id"]

        await set_balance(bob_id, 100.0)

        r = await c.post(f"{API}/api/marketplace/products",
            json={"title": "iter175-ext test", "description": "ext",
                  "price": 9.99, "category": "general",
                  "images": [{"thumb": "/x.jpg", "full": "/x.jpg", "hd": "/x.jpg"}]},
            headers=H(bob_t))
        r.raise_for_status()
        pid = r.json()["product_id"]
        print(f"[setup] product={pid}")

        # ── Test standard_7d plan ($5, 7 days, NOT homepage)
        r = await c.post(f"{API}/api/marketplace/products/{pid}/boost-wallet",
                         json={"plan": "standard_7d"}, headers=H(bob_t))
        r.raise_for_status()
        body = r.json()
        assert body["plan"] == "standard_7d"
        assert body["price_usd"] == "5.00"
        assert body["is_homepage"] is False, "standard_7d should NOT set homepage"
        print(f"[1] standard_7d $5 7d boost OK, expires={body['expires_at']}")

        # Verify DB rows: transactions + product_boosts
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            tx = await conn.fetchrow("""
                SELECT type, amount, currency, status FROM transactions
                WHERE from_user_id=$1 AND type='product_boost'
                ORDER BY created_at DESC LIMIT 1
            """, bob_id)
            assert tx is not None, "no product_boost transaction"
            assert tx["type"] == "product_boost"
            assert tx["currency"] == "USD"
            assert tx["status"] == "completed"
            assert Decimal(str(tx["amount"])) == Decimal("5.00")
            print(f"[2] transactions row: type={tx['type']} amt={tx['amount']} curr={tx['currency']} ✓")

            boost = await conn.fetchrow("""
                SELECT plan, price_usd, status, expires_at FROM product_boosts
                WHERE product_id=$1 ORDER BY created_at DESC LIMIT 1
            """, pid)
            assert boost is not None, "no product_boosts row"
            assert boost["plan"] == "standard_7d"
            assert Decimal(str(boost["price_usd"])) == Decimal("5.00")
            assert boost["status"] == "active"
            print(f"[3] product_boosts row: plan={boost['plan']} status={boost['status']} ✓")

            # 7d expiry roughly
            from datetime import datetime, timezone, timedelta
            exp = boost["expires_at"]
            delta = exp - datetime.now(timezone.utc)
            assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1), f"delta={delta}"
            print(f"[4] expiry ~7d: {delta} ✓")

            # Verify product flags
            prod = await conn.fetchrow(
                "SELECT is_boosted, is_homepage_featured, last_boost_plan, boost_expires_at "
                "FROM products WHERE product_id=$1", pid)
            assert prod["is_boosted"] is True
            assert prod["is_homepage_featured"] is False
            assert prod["last_boost_plan"] == "standard_7d"
            print(f"[5] product flags: is_boosted=True, is_homepage_featured=False ✓")
        finally:
            await conn.close()

        # ── /featured should NOT include standard_7d (only homepage)
        r = await c.get(f"{API}/api/marketplace/featured", headers=H(bob_t))
        r.raise_for_status()
        items = r.json()["items"]
        in_featured = any(it["product_id"] == pid for it in items)
        assert not in_featured, "standard_7d should NOT appear in /featured (homepage only)"
        print(f"[6] standard_7d not in /featured ✓")

        # ── GET product returns active_boost
        r = await c.get(f"{API}/api/marketplace/products/{pid}", headers=H(bob_t))
        body = r.json()
        ab = body.get("active_boost")
        assert ab is not None, "active_boost missing"
        assert ab["plan"] == "standard_7d"
        print(f"[7] GET product → active_boost={ab} ✓")

        # cleanup
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute("DELETE FROM products WHERE product_id=$1", pid)
        await conn.close()

    print("\n✅ ALL 7 EXTENDED TESTS PASS")


if __name__ == "__main__":
    asyncio.run(main())
