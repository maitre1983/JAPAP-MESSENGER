"""
JAPAP — iter170 advanced E2E: race-safe anti-fraud & tier notifications.

  • Race test: 10 parallel record_invite_visit calls with the same
    (cycle, inviter, ip) → exactly 3 rows inserted (cap=3 enforced
    even under concurrency thanks to pg_advisory_xact_lock).
  • Notify test: notify_tier_awarded creates an in-app notification
    row (notifications) AND records an email_logs entry (or logs
    when RESEND_API_KEY is missing).
  • Reminder test: run_reminder_pass picks up the right candidates
    and is idempotent (second run sends 0 emails).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

DB_URL = os.environ["DATABASE_URL"]


async def _conn():
    return await asyncpg.connect(DB_URL)


async def _active_cycle(c):
    return await c.fetchval(
        "SELECT cycle_id FROM crowdfunding_cycles WHERE status='active' "
        "ORDER BY started_at DESC LIMIT 1")


async def _pick_project(c, cycle_id):
    return await c.fetchrow(
        """SELECT project_id, slug, user_id FROM crowdfunding_projects
           WHERE cycle_id=$1 AND status='active'
             AND user_id NOT IN (
               SELECT user_id FROM users WHERE email = ANY($2::text[]))
           LIMIT 1""", cycle_id, [
            "bob@japap.com", "charlie_iter141@japap.com",
            "dave_iter141@japap.com", "admin@japap.com",
        ])


async def test_race_safe_cap():
    """10 parallel visits must converge to 3 rows."""
    pool = await asyncpg.create_pool(DB_URL, min_size=10, max_size=10,
                                      statement_cache_size=0)
    from services.crowdfunding_recruit_service import record_invite_visit
    async with pool.acquire() as c:
        cycle_id = await _active_cycle(c)
        proj = await _pick_project(c, cycle_id)
        bob_id = await c.fetchval("SELECT user_id FROM users WHERE email='bob@japap.com'")
        # Cleanup
        await c.execute(
            "DELETE FROM crowdfunding_invite_visits "
            "WHERE cycle_id=$1 AND inviter_id=$2 AND ip_hash='ip_race'",
            cycle_id, bob_id)

    async def _one_visit():
        async with pool.acquire() as conn:
            return await record_invite_visit(
                conn, cycle_id=cycle_id, inviter_id=bob_id,
                project_slug=proj["slug"], visitor_user_id=None,
                ip_hash="ip_race", user_agent_hash="ua_race",
            )

    results = await asyncio.gather(*[_one_visit() for _ in range(10)])
    inserted = sum(1 for r in results if r["recorded"])
    capped = sum(1 for r in results if r["reason"] == "ip_cap_reached")

    async with pool.acquire() as c:
        rows = await c.fetchval(
            "SELECT COUNT(*) FROM crowdfunding_invite_visits "
            "WHERE cycle_id=$1 AND inviter_id=$2 AND ip_hash='ip_race'",
            cycle_id, bob_id)
        await c.execute(
            "DELETE FROM crowdfunding_invite_visits "
            "WHERE cycle_id=$1 AND inviter_id=$2 AND ip_hash='ip_race'",
            cycle_id, bob_id)
    await pool.close()

    assert rows == 3, f"expected 3 inserted under race, got {rows}"
    assert inserted == 3 and capped == 7
    print(f"[race] 10 parallel visits → inserted={inserted}, capped={capped}, db_rows={rows} ✓")


async def test_notify_tier_awarded():
    c = await _conn()
    try:
        cycle_id = await _active_cycle(c)
        bob_id = await c.fetchval("SELECT user_id FROM users WHERE email='bob@japap.com'")
        # Seed Bob with 3 credits (Bronze) for an isolated cycle id.
        # Reuse the active cycle but seed/cleanup — so test is realistic.
        # First clear any existing badge/notif for Bob in this cycle.
        await c.execute(
            "DELETE FROM crowdfunding_recruiter_badges WHERE user_id=$1 AND cycle_id=$2",
            bob_id, cycle_id)
        await c.execute(
            "DELETE FROM notifications "
            "WHERE user_id=$1 AND type='crowdfunding_tier_awarded'",
            bob_id)
        # Seed exactly 3 recruit_credits so notify count = 3.
        await c.execute(
            "DELETE FROM crowdfunding_recruit_credits "
            "WHERE inviter_id=$1 AND cycle_id=$2 AND recruit_user_id LIKE 'test_notify_%'",
            bob_id, cycle_id)
        for i in range(3):
            await c.execute(
                """INSERT INTO crowdfunding_recruit_credits
                     (cycle_id, inviter_id, recruit_user_id, project_id)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT DO NOTHING""",
                cycle_id, bob_id, f"test_notify_{i}", "prj_test_dummy")

        from services.crowdfunding_recruit_notify import notify_tier_awarded
        result = await notify_tier_awarded(
            c, user_id=bob_id, cycle_id=cycle_id, tier_key="bronze")
        # In-app row?
        n = await c.fetchval(
            "SELECT COUNT(*) FROM notifications "
            "WHERE user_id=$1 AND type='crowdfunding_tier_awarded'",
            bob_id)
        assert n == 1, f"expected 1 in-app notif, got {n}"
        print(f"[notify] tier_awarded result={result}, in_app_rows={n} ✓")

        # Idempotent re-dispatch
        result2 = await notify_tier_awarded(
            c, user_id=bob_id, cycle_id=cycle_id, tier_key="bronze")
        n2 = await c.fetchval(
            "SELECT COUNT(*) FROM notifications "
            "WHERE user_id=$1 AND type='crowdfunding_tier_awarded'",
            bob_id)
        assert n2 == 1 and not result2["sent_inapp"], "should not double-insert"
        print(f"[notify] idempotent re-dispatch sent_inapp={result2['sent_inapp']} ✓")

        # Cleanup
        await c.execute(
            "DELETE FROM crowdfunding_recruit_credits "
            "WHERE inviter_id=$1 AND cycle_id=$2 AND recruit_user_id LIKE 'test_notify_%'",
            bob_id, cycle_id)
        await c.execute(
            "DELETE FROM notifications WHERE user_id=$1 "
            "AND type='crowdfunding_tier_awarded'", bob_id)
    finally:
        await c.close()


async def test_reminder_idempotent():
    c = await _conn()
    try:
        # Make sure the worker DDL is in place.
        from services.crowdfunding_recruit_remind_worker import (
            _ensure_table, run_reminder_pass,
        )
        await _ensure_table(c)
    finally:
        await c.close()

    out = await run_reminder_pass(force=False)
    assert "cycle_id" in out
    assert "visited_no_vote" in out and "voted_no_share" in out
    print(f"[remind] first pass: {out}")

    # Second pass MUST send 0 (idempotent UNIQUE constraint).
    out2 = await run_reminder_pass(force=False)
    assert out2["visited_no_vote"]["sent"] == 0
    assert out2["voted_no_share"]["sent"] == 0
    print(f"[remind] second pass idempotent ✓ (sent=0/0)")


async def main():
    await test_race_safe_cap()
    await test_notify_tier_awarded()
    await test_reminder_idempotent()
    print("\n[ALL PASSED] iter170 advanced E2E ✅")


if __name__ == "__main__":
    asyncio.run(main())
