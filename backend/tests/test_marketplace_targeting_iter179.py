"""
iter179 — Marketplace Boost Audience Targeting E2E
==================================================
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
DB_URL = os.environ["DATABASE_URL"]
BYPASS = "JAPAP_E2E_BYPASS_2026"


async def login(c, email, password):
    r = await c.post(f"{API}/api/auth/login", json={
        "email": email, "password": password,
        "captcha_id": BYPASS, "captcha_answer": "0",
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    return r.cookies.get("access_token")


def H(t):
    return {"Authorization": f"Bearer {t}", "X-Requested-With": "XMLHttpRequest"}


async def set_balance(uid, amount):
    c = await asyncpg.connect(DB_URL)
    await c.execute("""
        INSERT INTO wallets (user_id, balance, currency, created_at)
        VALUES ($1, $2, 'USD', NOW())
        ON CONFLICT (user_id) DO UPDATE SET balance = EXCLUDED.balance
    """, uid, Decimal(str(amount)))
    await c.close()


async def main():
    c = await asyncpg.connect(DB_URL)
    for e in ["bob@japap.com", "alice@japap.com"]:
        await c.execute("DELETE FROM login_attempts WHERE identifier=$1", e)
    await c.close()

    async with httpx.AsyncClient(timeout=30) as cli:
        bob_t = await login(cli, "bob@japap.com", "Test1234!")
        alice_t = await login(cli, "alice@japap.com", "Alice2026!")
        bob = (await cli.get(f"{API}/api/auth/me", headers=H(bob_t))).json()
        alice = (await cli.get(f"{API}/api/auth/me", headers=H(alice_t))).json()
        bob_id, alice_id = bob["user_id"], alice["user_id"]
        await set_balance(bob_id, 100.0)

        # Set Alice country to FR for filtering test
        c = await asyncpg.connect(DB_URL)
        await c.execute("UPDATE users SET country_code='FR' WHERE user_id=$1", alice_id)
        # Set Alice birthday to make her 30 years old
        await c.execute("UPDATE users SET birthday=CURRENT_DATE - INTERVAL '30 years' WHERE user_id=$1", alice_id)
        await c.close()

        # Create product
        r = await cli.post(f"{API}/api/marketplace/products",
            json={"title": "iter179 targeting test", "description": "x",
                  "price": 5.0, "category": "general",
                  "images": [{"thumb": "/x.jpg", "full": "/x.jpg", "hd": "/x.jpg"}]},
            headers=H(bob_t))
        r.raise_for_status()
        pid = r.json()["product_id"]
        print(f"[setup] product = {pid}, alice country=FR age=30")

        # ── [1] Homepage boost with COUNTRY targeting CM only
        r = await cli.post(f"{API}/api/marketplace/products/{pid}/boost-wallet",
            json={"plan": "homepage_30d", "is_global": False,
                  "target_countries": ["CM"]},
            headers=H(bob_t))
        r.raise_for_status()
        # Alice (FR) should NOT see this in /featured
        r = await cli.get(f"{API}/api/marketplace/featured", headers=H(alice_t))
        items = r.json()["items"]
        assert not any(it["product_id"] == pid for it in items), \
            f"Alice (FR) saw a CM-targeted boost — got {[i['product_id'] for i in items]}"
        print(f"[1] CM-targeted boost hidden from FR user ✓")

        # ── [2] Same product re-boosted with FR included → visible
        r = await cli.post(f"{API}/api/marketplace/products/{pid}/boost-wallet",
            json={"plan": "homepage_30d", "is_global": False,
                  "target_countries": ["CM", "FR"]},
            headers=H(bob_t))
        r.raise_for_status()
        # Force product to use NEW boost: deactivate the old CM-only boost row
        c = await asyncpg.connect(DB_URL)
        await c.execute("""
            UPDATE product_boosts SET status='expired'
            WHERE product_id=$1 AND status='active'
              AND target_countries = ARRAY['CM']::text[]
        """, pid)
        await c.close()
        r = await cli.get(f"{API}/api/marketplace/featured", headers=H(alice_t))
        items = r.json()["items"]
        assert any(it["product_id"] == pid for it in items), \
            f"Alice (FR) didn't see CM+FR boost — got {[i['product_id'] for i in items]}"
        print(f"[2] CM+FR boost visible to FR user ✓")

        # ── [3] AGE filter — boost only 18-25 (Alice is 30 → should be hidden)
        r = await cli.post(f"{API}/api/marketplace/products/{pid}/boost-wallet",
            json={"plan": "homepage_30d", "is_global": True,
                  "age_min": 18, "age_max": 25},
            headers=H(bob_t))
        r.raise_for_status()
        # Expire the old broader boosts
        c = await asyncpg.connect(DB_URL)
        await c.execute("""
            UPDATE product_boosts SET status='expired'
            WHERE product_id=$1 AND age_min IS NULL
        """, pid)
        await c.close()
        r = await cli.get(f"{API}/api/marketplace/featured", headers=H(alice_t))
        items = r.json()["items"]
        assert not any(it["product_id"] == pid for it in items), \
            f"Alice (30) saw 18-25 boost — got {[i['product_id'] for i in items]}"
        print(f"[3] age 18-25 boost hidden from age=30 user ✓")

        # ── [4] AGE filter 25-40 → Alice (30) sees it
        r = await cli.post(f"{API}/api/marketplace/products/{pid}/boost-wallet",
            json={"plan": "homepage_30d", "is_global": True,
                  "age_min": 25, "age_max": 40},
            headers=H(bob_t))
        r.raise_for_status()
        c = await asyncpg.connect(DB_URL)
        await c.execute("""
            UPDATE product_boosts SET status='expired'
            WHERE product_id=$1 AND age_max=25
        """, pid)
        await c.close()
        r = await cli.get(f"{API}/api/marketplace/featured", headers=H(alice_t))
        items = r.json()["items"]
        assert any(it["product_id"] == pid for it in items), \
            f"Alice (30) didn't see 25-40 boost — got {[i['product_id'] for i in items]}"
        print(f"[4] age 25-40 boost visible to age=30 user ✓")

        # ── [5] No-country fallback — set Alice country to NULL → still sees a CM boost
        c = await asyncpg.connect(DB_URL)
        await c.execute("UPDATE users SET country_code=NULL, country=NULL WHERE user_id=$1", alice_id)
        await c.close()
        # Make a CM-only boost
        r = await cli.post(f"{API}/api/marketplace/products/{pid}/boost-wallet",
            json={"plan": "homepage_30d", "is_global": False,
                  "target_countries": ["CM"]},
            headers=H(bob_t))
        r.raise_for_status()
        c = await asyncpg.connect(DB_URL)
        await c.execute(
            "UPDATE product_boosts SET status='expired' WHERE product_id=$1 AND age_min=25",
            pid)
        await c.close()
        r = await cli.get(f"{API}/api/marketplace/featured", headers=H(alice_t))
        items = r.json()["items"]
        assert any(it["product_id"] == pid for it in items), \
            "country-NULL user should see all (graceful fallback)"
        print(f"[5] NULL country → graceful fallback (visible) ✓")

        # ── [6] /products/{id} returns audience targeting fields
        r = await cli.get(f"{API}/api/marketplace/products/{pid}", headers=H(alice_t))
        r.raise_for_status()
        ab = r.json().get("active_boost")
        assert ab is not None
        for k in ("is_global", "target_countries", "age_min", "age_max"):
            assert k in ab, f"missing {k} in active_boost"
        assert ab["target_countries"] == ["CM"]
        print(f"[6] product detail exposes targeting: {ab} ✓")

        # cleanup
        c = await asyncpg.connect(DB_URL)
        await c.execute("DELETE FROM products WHERE product_id=$1", pid)
        await c.execute("UPDATE users SET country_code='CM', birthday=NULL WHERE user_id=$1", alice_id)
        await c.close()
    print("\n✅ ALL 6 ITER179 TARGETING TESTS PASS")


if __name__ == "__main__":
    asyncio.run(main())
