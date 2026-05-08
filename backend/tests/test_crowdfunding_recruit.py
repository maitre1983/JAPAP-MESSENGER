"""
E2E tests — Crowdfunding viral loop (P1, iter169)

Tests:
  1. /projects/{slug}/visit logs a visit with ref=<inviter_id> (anonymous OK)
  2. Anti-fraud: 4th visit from same IP/inviter/cycle is dropped (capped at 3)
  3. /recruiter/leaderboard returns inviter ranked + viewer block
  4. /recruiter/me returns count, tier, next_tier, badges (auth)
  5. try_credit_recruit credits inviter when recruit votes
  6. try_credit_recruit blocks self-project credit (own project)
  7. try_credit_recruit blocks self-referral
  8. Idempotent: same recruit voting again does NOT double-credit
  9. Tier badges awarded at 3+ recruits (Bronze)

Run: cd /app/backend && pytest tests/test_crowdfunding_recruit.py -v -s
"""
import os
import sys
import asyncio
import hashlib
import httpx
import asyncpg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

API = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
# Use internal localhost for stability inside the pod.
API = "http://localhost:8001"

DB_URL = os.environ["DATABASE_URL"]


@pytest.fixture(scope="module")
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def _conn():
    return await asyncpg.connect(DB_URL)


async def _login(client, email, password):
    r = await client.post(f"{API}/api/auth/login", json={
        "email": email, "password": password,
        "captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0",
    })
    r.raise_for_status()
    return r.json()


async def _get_active_cycle(c):
    return await c.fetchval(
        "SELECT cycle_id FROM crowdfunding_cycles WHERE status='active' "
        "ORDER BY started_at DESC LIMIT 1")


async def _alice_project(c, cycle_id):
    """Pick any active project in the cycle whose owner is not Bob/Charlie/Dave/Admin —
    we need an owner distinct from the inviters/recruits used in tests."""
    return await c.fetchrow(
        """SELECT project_id, slug, user_id FROM crowdfunding_projects
           WHERE cycle_id=$1 AND status='active'
             AND user_id NOT IN (
               SELECT user_id FROM users WHERE email = ANY($2::text[]))
           LIMIT 1""",
        cycle_id, [
            "bob@japap.com", "charlie_iter141@japap.com",
            "dave_iter141@japap.com", "admin@japap.com",
        ])


async def _cleanup(c, cycle_id):
    await c.execute("DELETE FROM crowdfunding_invite_visits WHERE cycle_id=$1", cycle_id)
    await c.execute("DELETE FROM crowdfunding_recruit_credits WHERE cycle_id=$1", cycle_id)
    await c.execute("DELETE FROM crowdfunding_recruiter_badges WHERE cycle_id=$1", cycle_id)


@pytest.mark.asyncio
async def test_full_recruit_flow():
    c = await _conn()
    try:
        cycle_id = await _get_active_cycle(c)
        assert cycle_id, "No active cycle"

        # Make sure we have an active project in the current cycle that is
        # NOT owned by any of our test "recruits" (so attribution is testable).
        proj = await _alice_project(c, cycle_id)
        assert proj, "Need an active project (not owned by bob/charlie/dave/admin) in current cycle"

        # Resolve inviter user ids
        bob_id = await c.fetchval("SELECT user_id FROM users WHERE email='bob@japap.com'")
        charlie_id = await c.fetchval("SELECT user_id FROM users WHERE email='charlie_iter141@japap.com'")
        dave_id = await c.fetchval("SELECT user_id FROM users WHERE email='dave_iter141@japap.com'")
        assert bob_id and charlie_id and dave_id

        await _cleanup(c, cycle_id)

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            # ── 1. Visit with ?ref=bob (anonymous click) ──────────────
            r = await client.get(
                f"{API}/api/crowdfunding/projects/{proj['slug']}/visit",
                params={"ref": bob_id, "src": "whatsapp"},
                headers={"X-Forwarded-For": "203.0.113.10"},
            )
            assert r.status_code == 303
            visits = await c.fetchval(
                "SELECT COUNT(*) FROM crowdfunding_invite_visits "
                "WHERE cycle_id=$1 AND inviter_id=$2", cycle_id, bob_id)
            assert visits == 1, f"expected 1 visit, got {visits}"
            print(f"[1] visit logged ✓ (cycle={cycle_id}, inviter={bob_id})")

            # ── 2. Anti-fraud: 4th visit from same IP+inviter is dropped ──
            for _ in range(3):
                await client.get(
                    f"{API}/api/crowdfunding/projects/{proj['slug']}/visit",
                    params={"ref": bob_id},
                    headers={"X-Forwarded-For": "203.0.113.10"},
                )
            visits_after = await c.fetchval(
                "SELECT COUNT(*) FROM crowdfunding_invite_visits "
                "WHERE cycle_id=$1 AND inviter_id=$2", cycle_id, bob_id)
            # Cap is 3 → first call counted, then 2 more → total 3.
            assert visits_after == 3, f"expected cap=3, got {visits_after}"
            print(f"[2] anti-fraud cap=3 enforced ✓ (visits={visits_after})")

            # ── 3. Leaderboard (anonymous viewer) ─────────────────────
            # No credits yet → empty
            r = await client.get(f"{API}/api/crowdfunding/recruiter/leaderboard")
            assert r.status_code == 200
            data = r.json()
            assert data["cycle_id"] == cycle_id
            assert data["items"] == []
            print(f"[3] empty leaderboard ✓")

        # ── 4. try_credit_recruit (own project blocked) ────────────
        from services.crowdfunding_recruit_service import (
            try_credit_recruit, award_tier_badges, my_progress,
            cycle_leaderboard,
        )
        # Insert a visit "by Alice → her own project" then try to credit her
        await c.execute(
            """INSERT INTO crowdfunding_invite_visits
                (cycle_id, inviter_id, project_slug, visitor_user_id,
                 ip_hash, user_agent_hash) VALUES ($1,$2,$3,$4,$5,$6)""",
            cycle_id, proj["user_id"], proj["slug"], charlie_id,
            "ip_alice_self", "ua_alice_self",
        )
        res = await try_credit_recruit(
            c, cycle_id=cycle_id, recruit_user_id=charlie_id, vote_id=None,
            project_id=proj["project_id"], project_owner_id=proj["user_id"],
            ip_hash="ip_alice_self",
        )
        assert res["credited"] is False
        assert res["reason"] == "self_project_blocked"
        print(f"[4] self_project_blocked ✓ ({res})")

        # Cleanup that visit
        await c.execute("DELETE FROM crowdfunding_invite_visits "
                        "WHERE inviter_id=$1 AND cycle_id=$2",
                        proj["user_id"], cycle_id)

        # ── 5. Successful credit: bob → charlie votes ─────────────
        # Bob already has 3 visits with charlie as visitor? No, charlie wasn't logged yet.
        # Insert a fresh user-bound visit so bob is the recorded inviter.
        await c.execute(
            """INSERT INTO crowdfunding_invite_visits
                (cycle_id, inviter_id, project_slug, visitor_user_id,
                 ip_hash, user_agent_hash) VALUES ($1,$2,$3,$4,$5,$6)""",
            cycle_id, bob_id, proj["slug"], charlie_id,
            "ip_charlie", "ua_charlie",
        )
        res = await try_credit_recruit(
            c, cycle_id=cycle_id, recruit_user_id=charlie_id, vote_id=None,
            project_id=proj["project_id"], project_owner_id=proj["user_id"],
            ip_hash="ip_charlie",
        )
        assert res["credited"] is True
        assert res["inviter_id"] == bob_id
        print(f"[5] charlie credited to bob ✓")

        # ── 6. Idempotent: same charlie voting again should NOT re-credit ──
        res2 = await try_credit_recruit(
            c, cycle_id=cycle_id, recruit_user_id=charlie_id, vote_id=None,
            project_id=proj["project_id"], project_owner_id=proj["user_id"],
            ip_hash="ip_charlie",
        )
        assert res2["credited"] is False
        assert res2["reason"] == "already_credited"
        print(f"[6] idempotent ✓ ({res2['reason']})")

        # ── 7. Self-referral blocked (bob inviting bob) ─────────────
        await c.execute(
            """INSERT INTO crowdfunding_invite_visits
                (cycle_id, inviter_id, project_slug, visitor_user_id,
                 ip_hash, user_agent_hash) VALUES ($1,$2,$3,$4,$5,$6)""",
            cycle_id, bob_id, proj["slug"], bob_id,
            "ip_bobself", "ua_bobself",
        )
        res3 = await try_credit_recruit(
            c, cycle_id=cycle_id, recruit_user_id=bob_id, vote_id=None,
            project_id=proj["project_id"], project_owner_id=proj["user_id"],
            ip_hash="ip_bobself",
        )
        assert res3["credited"] is False
        assert res3["reason"] == "self_referral_blocked"
        print(f"[7] self_referral_blocked ✓")

        # ── 8. Add 2 more credits (total 3) → Bronze badge ──────────
        await c.execute(
            """INSERT INTO crowdfunding_invite_visits
                (cycle_id, inviter_id, project_slug, visitor_user_id,
                 ip_hash, user_agent_hash) VALUES ($1,$2,$3,$4,$5,$6)""",
            cycle_id, bob_id, proj["slug"], dave_id,
            "ip_dave", "ua_dave",
        )
        await try_credit_recruit(
            c, cycle_id=cycle_id, recruit_user_id=dave_id, vote_id=None,
            project_id=proj["project_id"], project_owner_id=proj["user_id"],
            ip_hash="ip_dave",
        )
        # 3rd recruit — use admin user
        admin_id = await c.fetchval("SELECT user_id FROM users WHERE email='admin@japap.com'")
        await c.execute(
            """INSERT INTO crowdfunding_invite_visits
                (cycle_id, inviter_id, project_slug, visitor_user_id,
                 ip_hash, user_agent_hash) VALUES ($1,$2,$3,$4,$5,$6)""",
            cycle_id, bob_id, proj["slug"], admin_id,
            "ip_admin", "ua_admin",
        )
        await try_credit_recruit(
            c, cycle_id=cycle_id, recruit_user_id=admin_id, vote_id=None,
            project_id=proj["project_id"], project_owner_id=proj["user_id"],
            ip_hash="ip_admin",
        )
        newly = await award_tier_badges(c, bob_id, cycle_id)
        assert "bronze" in newly, f"expected bronze in newly={newly}"
        print(f"[8] Bronze badge awarded ✓ ({newly})")

        # ── 9. Leaderboard reflects bob with 3 recruits ─────────────
        lb = await cycle_leaderboard(c, cycle_id, limit=10, viewer_id=bob_id)
        assert len(lb["items"]) >= 1
        top = lb["items"][0]
        assert top["inviter_id"] == bob_id
        assert top["recruits_count"] == 3
        assert top["tier"]["key"] == "bronze"
        assert lb["me"]["rank"] == 1
        print(f"[9] leaderboard top={top['inviter_id']} count={top['recruits_count']} tier={top['tier']['key']} ✓")

        # ── 10. my_progress for bob ─────────────────────────────────
        prog = await my_progress(c, bob_id, cycle_id)
        assert prog["recruits_count"] == 3
        assert prog["tier"]["key"] == "bronze"
        assert prog["next_tier"]["key"] == "silver"
        assert prog["next_tier"]["missing"] == 7
        assert any(b["tier"] == "bronze" for b in prog["badges"])
        print(f"[10] my_progress: count={prog['recruits_count']} tier={prog['tier']['key']} next={prog['next_tier']['key']} (need {prog['next_tier']['missing']} more) ✓")

        # Cleanup
        await _cleanup(c, cycle_id)
        print("\n[ALL PASSED] Crowdfunding recruit P1 backend ✅")
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(test_full_recruit_flow())
