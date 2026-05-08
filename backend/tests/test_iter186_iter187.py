"""
iter186 + iter187 — Viral milestones + Contact seller marketplace tests
========================================================================
"""
import asyncio
import os
import sys

import asyncpg
import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API_URL = os.environ.get("TEST_API_URL") or "http://localhost:8001"
BYPASS = os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026")
load_dotenv("/app/backend/.env")


async def _login(c, email, pw):
    r = await c.post("/api/auth/login",
                      json={"email": email, "password": pw,
                            "captcha_id": BYPASS, "captcha_answer": "0"})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    bob = await conn.fetchval("SELECT user_id FROM users WHERE email='bob@japap.com'")
    alice = await conn.fetchval("SELECT user_id FROM users WHERE email='alice@japap.com'")
    pid = await conn.fetchval(
        "SELECT product_id FROM products WHERE seller_id=$1 AND status='active' LIMIT 1", bob)
    # Reset milestones + viral events
    await conn.execute("DELETE FROM viral_share_events WHERE sharer_id=$1", bob)
    await conn.execute("DELETE FROM viral_milestones_reached WHERE user_id=$1", bob)
    # Pre-seed 4 rewarded events so a 5th will cross threshold 5
    for i in range(4):
        await conn.execute("""
            INSERT INTO viral_share_events
              (sharer_id, entity_type, entity_id, ip_hash, ua_hash, day_key, rewarded, points_awarded)
            VALUES ($1,'product',$2,$3,$4,'2026-04-30',TRUE,50)
        """, bob, pid, f"ip_seed_{i}", f"ua_seed_{i}")
    await conn.close()

    # ───────── iter186 — Milestone push ─────────
    async with httpx.AsyncClient(base_url=API_URL, timeout=15.0,
                                  headers={"X-Requested-With": "XMLHttpRequest",
                                           "X-Forwarded-For": "203.0.113.221"}) as anon:
        r = await anon.post("/api/seo/viral/track",
                             json={"utm": f"share_{bob}_product_{pid}"})
        assert r.status_code == 200
        d = r.json()
        assert d["rewarded"] is True
        print(f"[i186-1] 5th visit triggered milestone — pts +{d['points_awarded']}")

    # Check that thresholds 1 and 5 are recorded
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    ms = await conn.fetch(
        "SELECT threshold FROM viral_milestones_reached WHERE user_id=$1 ORDER BY threshold", bob)
    thresholds = [int(r["threshold"]) for r in ms]
    assert 1 in thresholds and 5 in thresholds, f"expected 1 & 5 in {thresholds}"
    print(f"[i186-2] thresholds recorded: {thresholds}")

    # Check notification was emitted (highest pending → 5)
    notif = await conn.fetchrow("""
        SELECT title, message FROM notifications
        WHERE user_id=$1 AND type='viral_milestone'
        ORDER BY created_at DESC LIMIT 1
    """, bob)
    assert notif and "5 visiteurs" in (notif["title"] or "")
    print(f"[i186-3] notification emitted: {notif['title']!r}")

    # 2nd call from same IP (dedup) → no new milestone notif spam
    notif_count_before = await conn.fetchval(
        "SELECT COUNT(*) FROM notifications WHERE user_id=$1 AND type='viral_milestone'", bob)
    await conn.close()
    async with httpx.AsyncClient(base_url=API_URL, timeout=15.0,
                                  headers={"X-Requested-With": "XMLHttpRequest",
                                           "X-Forwarded-For": "203.0.113.221"}) as anon:
        await anon.post("/api/seo/viral/track",
                         json={"utm": f"share_{bob}_product_{pid}"})
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    after = await conn.fetchval(
        "SELECT COUNT(*) FROM notifications WHERE user_id=$1 AND type='viral_milestone'", bob)
    assert after == notif_count_before, f"milestone push should not duplicate (was {notif_count_before}, now {after})"
    print("[i186-4] no spam on dedup OK")
    await conn.close()

    # ───────── iter187 — Contact seller ─────────
    async with httpx.AsyncClient(base_url=API_URL, timeout=15.0,
                                  headers={"X-Requested-With": "XMLHttpRequest"}) as alice_c:
        await _login(alice_c, "alice@japap.com", "Alice2026!")
        r = await alice_c.post("/api/marketplace/products/contact-seller",
                                 json={"product_id": pid})
        assert r.status_code == 200, r.text
        d1 = r.json()
        assert d1["ok"] is True
        assert d1["seller_id"] == bob
        assert "Bonjour, je suis intéressé" in d1["preview"]
        assert "Prix" in d1["preview"]
        assert "/marketplace/p/" in d1["preview"]
        conv_id_1 = d1["conv_id"]
        print(f"[i187-1] contact-seller OK conv={conv_id_1}")

        # Same Alice clicks again → SAME conv_id (no duplicate)
        r = await alice_c.post("/api/marketplace/products/contact-seller",
                                 json={"product_id": pid})
        assert r.status_code == 200
        d2 = r.json()
        assert d2["conv_id"] == conv_id_1, "anti-doublon failed"
        print("[i187-2] dedup conversation OK")

        # Bad product → 404
        r = await alice_c.post("/api/marketplace/products/contact-seller",
                                 json={"product_id": "prod_NONEXISTENT"})
        assert r.status_code == 404
        print("[i187-3] bad product → 404 OK")

    # Bob → his own product → 400 self-contact
    async with httpx.AsyncClient(base_url=API_URL, timeout=15.0,
                                  headers={"X-Requested-With": "XMLHttpRequest"}) as bob_c:
        await _login(bob_c, "bob@japap.com", "Test1234!")
        r = await bob_c.post("/api/marketplace/products/contact-seller",
                               json={"product_id": pid})
        assert r.status_code == 400
        assert "toi-même" in r.json()["detail"]
        print("[i187-4] self-contact blocked OK")

    # Verify auto-message exists in DB
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    msgs = await conn.fetch("""
        SELECT text FROM messages WHERE conv_id=$1 ORDER BY created_at DESC LIMIT 3
    """, conv_id_1)
    await conn.close()
    assert any("Bonjour" in (m["text"] or "") for m in msgs)
    print(f"[i187-5] auto-message in conv ({len(msgs)} messages) OK")

    print("\n✅ iter186+iter187 — All 9 assertions PASS")


if __name__ == "__main__":
    asyncio.run(main())
