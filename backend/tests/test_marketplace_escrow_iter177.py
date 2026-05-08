"""
iter177 — Marketplace Escrow Email Notifications + Disputes Admin queue
=======================================================================
Tests:
  [1] GET /api/marketplace/admin/orders/disputes — admin-only (200 admin / 403 non-admin)
  [2] order create → email_logs row kind='marketplace_order_received' for SELLER
  [3] dispute open → email_logs rows kind='marketplace_dispute_opened' (seller)
                    + kind='marketplace_dispute_admin' (admin)
  [4] admin /resolve refund_buyer → email_logs rows kind='marketplace_dispute_resolved'
      for BOTH buyer AND seller (2 rows)
  [5] auto-release sweep → email_logs row kind='marketplace_order_released' for SELLER
  [6] All flows still return 200 with full ledger updates even when Resend mocked
      (no RESEND_API_KEY ⇒ event='sent' + provider='mock' rows still appear)
"""
import asyncio
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, "/app/backend")
import asyncpg  # noqa: E402
import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

API = "http://localhost:8001"
DB_URL = os.environ["DATABASE_URL"]
BYPASS = "JAPAP_E2E_BYPASS_2026"

PASS, FAIL = [], []


def ok(name):
    PASS.append(name)
    print(f"  ✅ {name}")


def ko(name, err):
    FAIL.append((name, str(err)))
    print(f"  ❌ {name}: {err}")


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
    try:
        await c.execute("""
            INSERT INTO wallets (user_id, balance, currency, created_at)
            VALUES ($1, $2, 'USD', NOW())
            ON CONFLICT (user_id) DO UPDATE SET balance = EXCLUDED.balance
        """, uid, Decimal(str(amount)))
    finally:
        await c.close()


async def email_logs_count(email: str, kind: str, since) -> int:
    """Count email_logs rows for a given recipient + metadata.kind since timestamp."""
    c = await asyncpg.connect(DB_URL)
    try:
        n = await c.fetchval("""
            SELECT COUNT(*) FROM email_logs
            WHERE email = $1
              AND created_at >= $2
              AND event = 'sent'
              AND metadata::jsonb @> $3::jsonb
        """, email.lower(), since, json.dumps({"kind": kind}))
        return int(n or 0)
    finally:
        await c.close()


async def now_iso():
    c = await asyncpg.connect(DB_URL)
    try:
        return await c.fetchval("SELECT NOW()")
    finally:
        await c.close()


async def main():
    # Wipe login attempts
    c = await asyncpg.connect(DB_URL)
    for e in ["bob@japap.com", "alice@japap.com", "admin@japap.com"]:
        await c.execute("DELETE FROM login_attempts WHERE identifier=$1", e)
    await c.close()

    print("=" * 60)
    print("iter177 — Email side-effects + admin disputes queue")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=60) as cli:
        bob_t = await login(cli, "bob@japap.com", "Test1234!")
        alice_t = await login(cli, "alice@japap.com", "Alice2026!")
        admin_t = await login(cli, "admin@japap.com", "JapapAdmin2024!")
        bob = (await cli.get(f"{API}/api/auth/me", headers=H(bob_t))).json()
        alice = (await cli.get(f"{API}/api/auth/me", headers=H(alice_t))).json()
        admin = (await cli.get(f"{API}/api/auth/me", headers=H(admin_t))).json()
        bob_id, alice_id = bob["user_id"], alice["user_id"]
        admin_email = admin.get("email") or "admin@japap.com"
        print(f"[setup] bob={bob_id} alice={alice_id} admin={admin_email}")

        # ── [1] admin-only disputes endpoint
        try:
            r = await cli.get(f"{API}/api/marketplace/admin/orders/disputes", headers=H(admin_t))
            assert r.status_code == 200, f"admin got {r.status_code}"
            data = r.json()
            assert "items" in data and "total" in data, f"missing keys: {data}"
            assert isinstance(data["items"], list)
            r2 = await cli.get(f"{API}/api/marketplace/admin/orders/disputes", headers=H(alice_t))
            assert r2.status_code == 403, f"non-admin got {r2.status_code}"
            ok("[1] /admin/orders/disputes admin=200, non-admin=403")
        except Exception as e:
            ko("[1] disputes endpoint auth", e)

        await set_balance(alice_id, 200.0)

        # Create product
        r = await cli.post(f"{API}/api/marketplace/products",
            json={"title": "iter177 email-test product", "description": "x",
                  "price": 10.0, "category": "general",
                  "images": [{"thumb": "/x.jpg", "full": "/x.jpg", "hd": "/x.jpg"}]},
            headers=H(bob_t))
        r.raise_for_status()
        pid = r.json()["product_id"]
        print(f"[setup] product = {pid}")

        # ── [2] order create → email_logs (seller) kind=marketplace_order_received
        t0 = await now_iso()
        try:
            r = await cli.post(f"{API}/api/marketplace/orders",
                json={"product_id": pid, "notes": "email test"}, headers=H(alice_t))
            assert r.status_code == 200, f"order create {r.status_code}: {r.text}"
            oid = r.json()["order_id"]
            assert r.json()["escrow_status"] == "held"
            await asyncio.sleep(1.0)  # let post-commit email task complete
            n = await email_logs_count("bob@japap.com", "marketplace_order_received", t0)
            assert n >= 1, f"expected >=1 email_logs row for seller, got {n}"
            ok(f"[2] order create → seller email_logs row (n={n}, kind=marketplace_order_received)")
        except Exception as e:
            ko("[2] email on order create", e)

        # ── [3] dispute → email_logs seller + admin
        t1 = await now_iso()
        try:
            r = await cli.post(f"{API}/api/marketplace/orders/{oid}/dispute",
                json={"reason": "Produit jamais reçu après 5 jours"}, headers=H(alice_t))
            assert r.status_code == 200, f"dispute {r.status_code}: {r.text}"
            assert r.json()["escrow_status"] == "disputed"
            await asyncio.sleep(1.0)
            n_seller = await email_logs_count("bob@japap.com", "marketplace_dispute_opened", t1)
            n_admin = await email_logs_count(admin_email, "marketplace_dispute_admin", t1)
            assert n_seller >= 1, f"seller dispute email missing (n={n_seller})"
            assert n_admin >= 1, f"admin dispute email missing (n={n_admin})"
            ok(f"[3] dispute → seller={n_seller} (opened) + admin={n_admin} (admin) email_logs rows")
        except Exception as e:
            ko("[3] email on dispute open", e)

        # ── [4] admin resolve refund_buyer → email_logs both buyer & seller
        t2 = await now_iso()
        try:
            bal_a_pre = await asyncpg.connect(DB_URL)
            v_pre = await bal_a_pre.fetchval(
                "SELECT balance FROM wallets WHERE user_id=$1", alice_id)
            await bal_a_pre.close()
            r = await cli.post(f"{API}/api/marketplace/admin/orders/{oid}/resolve",
                json={"decision": "refund_buyer", "notes": "Vendeur défaillant — refund full"},
                headers=H(admin_t))
            assert r.status_code == 200, f"resolve {r.status_code}: {r.text}"
            # Wallet movement (financial flow not blocked by emails)
            bal_a_post = await asyncpg.connect(DB_URL)
            v_post = await bal_a_post.fetchval(
                "SELECT balance FROM wallets WHERE user_id=$1", alice_id)
            await bal_a_post.close()
            assert Decimal(str(v_post)) == Decimal(str(v_pre)) + Decimal("10.00"), \
                f"refund delta wrong: {v_pre} → {v_post}"
            await asyncio.sleep(1.5)
            n_buyer = await email_logs_count("alice@japap.com", "marketplace_dispute_resolved", t2)
            n_seller = await email_logs_count("bob@japap.com", "marketplace_dispute_resolved", t2)
            assert n_buyer >= 1, f"buyer resolved email missing (n={n_buyer})"
            assert n_seller >= 1, f"seller resolved email missing (n={n_seller})"
            ok(f"[4] resolve refund → buyer={n_buyer}+seller={n_seller} resolved emails, refund={v_post-v_pre} USD")
        except Exception as e:
            ko("[4] email + financial flow on resolve", e)

        # ── [5] auto-release sweep → email_logs marketplace_order_released
        await set_balance(alice_id, 200.0)
        t3 = await now_iso()
        try:
            r = await cli.post(f"{API}/api/marketplace/orders",
                json={"product_id": pid, "notes": "auto-release email test"}, headers=H(alice_t))
            assert r.status_code == 200, f"new order {r.status_code}: {r.text}"
            oid_ar = r.json()["order_id"]
            # backdate
            c2 = await asyncpg.connect(DB_URL)
            await c2.execute(
                "UPDATE orders SET auto_release_at = NOW() - INTERVAL '1 hour' WHERE order_id=$1", oid_ar)
            await c2.close()
            r = await cli.post(f"{API}/api/marketplace/admin/orders/auto-release/run", headers=H(admin_t))
            assert r.status_code == 200, f"sweep {r.status_code}: {r.text}"
            assert r.json()["released"] >= 1
            await asyncio.sleep(1.5)
            n_rel = await email_logs_count("bob@japap.com", "marketplace_order_released", t3)
            assert n_rel >= 1, f"auto-release email missing (n={n_rel})"
            ok(f"[5] auto-release → seller email_logs row (n={n_rel}, kind=marketplace_order_released)")
        except Exception as e:
            ko("[5] email on auto-release", e)

        # ── [6] disputes queue contains row with product+emails+reason fields
        t4 = await now_iso()
        try:
            await set_balance(alice_id, 200.0)
            r = await cli.post(f"{API}/api/marketplace/orders",
                json={"product_id": pid, "notes": "queue test"}, headers=H(alice_t))
            r.raise_for_status()
            oid_q = r.json()["order_id"]
            r = await cli.post(f"{API}/api/marketplace/orders/{oid_q}/dispute",
                json={"reason": "Test queue iter177 — produit cassé à la livraison"},
                headers=H(alice_t))
            r.raise_for_status()
            r = await cli.get(f"{API}/api/marketplace/admin/orders/disputes", headers=H(admin_t))
            r.raise_for_status()
            items = r.json()["items"]
            row = next((x for x in items if x["order_id"] == oid_q), None)
            assert row is not None, f"order {oid_q} not in disputes queue"
            assert row["buyer_email"] == "alice@japap.com"
            assert row["seller_email"] == "bob@japap.com"
            assert row["product_title"] == "iter177 email-test product"
            assert "iter177" in row["dispute_reason"]
            assert row["escrow_status"] == "disputed"
            assert row["amount"] == "10.00"
            ok(f"[6] disputes queue exposes product_title/buyer_email/seller_email/dispute_reason ({len(items)} rows total)")

            # Cleanup: split-resolve to clear the dispute and re-check it disappears
            r = await cli.post(f"{API}/api/marketplace/admin/orders/{oid_q}/resolve",
                json={"decision": "split", "seller_share_usd": 6.0,
                      "notes": "iter177 split test"}, headers=H(admin_t))
            assert r.status_code == 200
            rb = r.json()
            # split=6 USD, commission 2% → 0.12, seller_net=5.88, buyer_refund=4.00
            assert rb["seller_net"] == "5.88", f"got {rb['seller_net']}"
            assert rb["buyer_refund"] == "4.00", f"got {rb['buyer_refund']}"
            assert rb["commission"] == "0.12", f"got {rb['commission']}"
            await asyncio.sleep(1.0)
            r = await cli.get(f"{API}/api/marketplace/admin/orders/disputes", headers=H(admin_t))
            still_there = any(x["order_id"] == oid_q for x in r.json()["items"])
            assert not still_there, "resolved dispute still in queue"
            ok("[6b] split breakdown 5.88/4.00/0.12 correct + queue auto-removes resolved")
        except Exception as e:
            ko("[6] disputes queue + split breakdown", e)

        # cleanup
        c = await asyncpg.connect(DB_URL)
        await c.execute("DELETE FROM products WHERE product_id=$1", pid)
        await c.close()

    print("\n" + "=" * 60)
    print(f"PASS = {len(PASS)} | FAIL = {len(FAIL)}")
    for name, err in FAIL:
        print(f"  ✗ {name}: {err}")
    print("=" * 60)
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
