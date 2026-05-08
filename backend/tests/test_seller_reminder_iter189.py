"""
iter189 — Seller reminder worker tests
========================================
Tests the two cascading reminders emitted by
`services.seller_reminder_worker._tick`.

Scenarios covered :
    1.  Intent older than push threshold + no seller reply
        → push_15min row inserted in buyer_intents_reminders_sent
    2.  Seller has replied in conv → no reminder
    3.  Reminder already sent → no duplicate
    4.  Intent older than email threshold → email_24h row inserted

The test fully resets worker state between sub-cases and uses
`alice@japap.com` (buyer) / `bob@japap.com` (seller) from the canonical
seed. The actual push/email providers are monkey-patched to avoid
hitting OneSignal / Resend during CI.
"""
import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")


async def _seed(conn, buyer_id, seller_id, product_id, conv_id, created_at):
    """Insert a fresh marketplace_buyer_intent with custom created_at."""
    row = await conn.fetchrow("""
        INSERT INTO marketplace_buyer_intents
          (buyer_id, seller_id, product_id, conv_id, created_at)
        VALUES ($1,$2,$3,$4,$5) RETURNING id
    """, buyer_id, seller_id, product_id, conv_id, created_at)
    return row["id"]


async def _ensure_conv(conn, conv_id, creator):
    await conn.execute("""
        INSERT INTO conversations (conv_id, type, created_by)
        VALUES ($1,'direct',$2) ON CONFLICT (conv_id) DO NOTHING
    """, conv_id, creator)


async def _add_message(conn, conv_id, sender_id, created_at, text="hi"):
    msg_id = f"msg_test_{uuid.uuid4().hex[:10]}"
    await conn.execute("""
        INSERT INTO messages (msg_id, conv_id, sender_id, text, created_at, status)
        VALUES ($1,$2,$3,$4,$5,'sent')
    """, msg_id, conv_id, sender_id, text, created_at)


async def _reset_intent(conn, intent_id):
    await conn.execute(
        "DELETE FROM buyer_intents_reminders_sent WHERE intent_id=$1", intent_id)


async def _was_reminded(conn, intent_id, kind):
    return await conn.fetchval("""
        SELECT 1 FROM buyer_intents_reminders_sent
        WHERE intent_id=$1 AND kind=$2
    """, intent_id, kind)


async def main():
    # Force worker to be enabled
    os.environ.setdefault("PUBLIC_APP_URL", "https://japapmessenger.com")

    # Monkey-patch push + email so the test doesn't hit external services
    from services import seller_reminder_worker as smr

    push_calls, email_calls = [], []

    async def fake_push(conn, intent):
        push_calls.append(int(intent["id"]))
        return True

    async def fake_email(conn, seller_id, intents):
        email_calls.append((seller_id, [int(i["id"]) for i in intents]))
        return True

    smr._send_push_reminder = fake_push
    smr._send_email_digest = fake_email

    from database import get_pool
    pool = await get_pool()

    async with pool.acquire() as conn:
        buyer = await conn.fetchval(
            "SELECT user_id FROM users WHERE email='alice@japap.com'")
        seller = await conn.fetchval(
            "SELECT user_id FROM users WHERE email='bob@japap.com'")
        pid = await conn.fetchval("""
            SELECT product_id FROM products WHERE seller_id=$1 AND status='active'
            LIMIT 1
        """, seller)
        assert buyer and seller and pid, "Canonical seed missing"

        # Dedicated conversation for this test (avoids cross-test contamination)
        conv_id = f"conv_test_iter189_{uuid.uuid4().hex[:8]}"
        await _ensure_conv(conn, conv_id, seller)

        # Clean any lingering test intents on this conv
        await conn.execute(
            "DELETE FROM marketplace_buyer_intents WHERE conv_id=$1", conv_id)

        # ───────────────────────────────────────────────────────────
        # CASE 1 — intent older than 15 min, no reply → push must fire
        # ───────────────────────────────────────────────────────────
        now = datetime.now(timezone.utc)
        intent1 = await _seed(conn, buyer, seller, pid, conv_id,
                               now - timedelta(minutes=30))

    push_calls.clear(); email_calls.clear()
    await smr._tick(pool)

    async with pool.acquire() as conn:
        assert intent1 in push_calls, f"CASE1 push not fired: {push_calls}"
        assert await _was_reminded(conn, intent1, "push_15min"), \
            "CASE1 push_15min row missing"
    print("CASE1 OK — push fired after 15min")

    # ───────────────────────────────────────────────────────────
    # CASE 2 — seller has replied → no reminder fired
    # ───────────────────────────────────────────────────────────
    async with pool.acquire() as conn:
        await _reset_intent(conn, intent1)
        # Add a seller reply AFTER intent creation
        await _add_message(
            conn, conv_id, seller,
            now - timedelta(minutes=10), "Réponse vendeur")

    push_calls.clear()
    await smr._tick(pool)

    async with pool.acquire() as conn:
        assert intent1 not in push_calls, \
            "CASE2 push should NOT fire when seller replied"
        assert not await _was_reminded(conn, intent1, "push_15min"), \
            "CASE2 row should NOT exist"
        # Cleanup seller reply to isolate next cases
        await conn.execute(
            "DELETE FROM messages WHERE conv_id=$1 AND sender_id=$2",
            conv_id, seller)
    print("CASE2 OK — push suppressed when seller replied")

    # ───────────────────────────────────────────────────────────
    # CASE 3 — idempotency: once push_15min row exists, no re-fire
    # ───────────────────────────────────────────────────────────
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO buyer_intents_reminders_sent (intent_id, kind)
            VALUES ($1, 'push_15min')
        """, intent1)
    push_calls.clear()
    await smr._tick(pool)
    assert intent1 not in push_calls, "CASE3 push re-fired (idempotency bug)"
    print("CASE3 OK — idempotent (no double push)")

    # ───────────────────────────────────────────────────────────
    # CASE 4 — intent older than 24h → email digest fires
    # ───────────────────────────────────────────────────────────
    async with pool.acquire() as conn:
        intent2 = await _seed(conn, buyer, seller, pid, conv_id,
                               now - timedelta(hours=26))

    email_calls.clear()
    await smr._tick(pool)

    async with pool.acquire() as conn:
        flat = [i for _, ids in email_calls for i in ids]
        assert intent2 in flat, f"CASE4 email not fired: {email_calls}"
        assert await _was_reminded(conn, intent2, "email_24h"), \
            "CASE4 email_24h row missing"
        # Cleanup
        await conn.execute(
            "DELETE FROM buyer_intents_reminders_sent WHERE intent_id=ANY($1)",
            [intent1, intent2])
        await conn.execute(
            "DELETE FROM marketplace_buyer_intents WHERE id=ANY($1)",
            [intent1, intent2])
        await conn.execute(
            "DELETE FROM conversations WHERE conv_id=$1", conv_id)
    print("CASE4 OK — email digest fired after 24h")

    # ───────────────────────────────────────────────────────────
    # CASE 5 — admin toggle off → _tick returns early, no reminders
    # ───────────────────────────────────────────────────────────
    from services.settings_service import set_setting
    # Seed a fresh ageing intent on a brand new conv
    conv_id2 = f"conv_test_iter189b_{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await _ensure_conv(conn, conv_id2, seller)
        intent3 = await _seed(conn, buyer, seller, pid, conv_id2,
                               datetime.now(timezone.utc) - timedelta(minutes=30))

    await set_setting("seller_reminders_enabled", "false")
    push_calls.clear(); email_calls.clear()
    await smr._tick(pool)
    assert not push_calls and not email_calls, \
        f"CASE5 reminders fired with toggle OFF: push={push_calls} email={email_calls}"
    async with pool.acquire() as conn:
        assert not await _was_reminded(conn, intent3, "push_15min"), \
            "CASE5 push_15min row should NOT exist"
    # Re-enable and confirm it fires
    await set_setting("seller_reminders_enabled", "true")
    await smr._tick(pool)
    assert intent3 in push_calls, "CASE5 push must fire once toggle re-enabled"
    print("CASE5 OK — admin toggle gates reminders correctly")

    # ───────────────────────────────────────────────────────────
    # CASE 6 — strong idempotency: duplicate ON CONFLICT DO NOTHING
    # ───────────────────────────────────────────────────────────
    async with pool.acquire() as conn:
        # Count current rows for intent3 / push_15min
        cnt_before = await conn.fetchval("""
            SELECT COUNT(*) FROM buyer_intents_reminders_sent
            WHERE intent_id=$1 AND kind='push_15min'
        """, intent3)
        # Attempt duplicate insert — must not raise, must not duplicate
        await conn.execute("""
            INSERT INTO buyer_intents_reminders_sent (intent_id, kind)
            VALUES ($1, 'push_15min') ON CONFLICT DO NOTHING
        """, intent3)
        cnt_after = await conn.fetchval("""
            SELECT COUNT(*) FROM buyer_intents_reminders_sent
            WHERE intent_id=$1 AND kind='push_15min'
        """, intent3)
        assert cnt_before == cnt_after == 1, \
            f"CASE6 idempotency broken: before={cnt_before} after={cnt_after}"
        # Run _tick again → must NOT re-call push for intent3
        push_calls.clear()
        await smr._tick(pool)
        assert intent3 not in push_calls, "CASE6 push re-fired after duplicate insert"
        # Final cleanup
        await conn.execute(
            "DELETE FROM buyer_intents_reminders_sent WHERE intent_id=$1", intent3)
        await conn.execute(
            "DELETE FROM marketplace_buyer_intents WHERE id=$1", intent3)
        await conn.execute(
            "DELETE FROM conversations WHERE conv_id=$1", conv_id2)
    print("CASE6 OK — strong idempotency (no duplicates, no re-push)")

    print("\n✅ iter189 — all 6 scenarios passed")


if __name__ == "__main__":
    asyncio.run(main())
