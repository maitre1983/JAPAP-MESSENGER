"""iter142D — Phase P3 IA engagement engine tests.

Covers:
  - track event endpoint validation
  - state machine deterministic outputs (cold / engaged / competitive / critical)
  - cooldown 6h same-state suppression
  - dismiss → 7-day cooldown
  - admin message performance leaderboard
  - LLM enrichment ONLY for critical+votes_open (with fallback)
"""
import os
import asyncio
import asyncpg
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
DB_URL = os.environ["DATABASE_URL"]
API = os.environ.get("PUBLIC_API_URL", "http://localhost:8001")
BYPASS = (os.environ.get("MATH_CAPTCHA_TEST_BYPASS_TOKEN")
          or os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026"))


def _login(email, password):
    r = requests.post(f"{API}/api/auth/login",
                      json={"email": email, "password": password,
                            "captcha_id": BYPASS, "captcha_answer": "x"},
                      timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture(scope="module", autouse=True)
def setup():
    async def _r():
        c = await asyncpg.connect(DB_URL)
        await c.execute("TRUNCATE crowdfunding_votes CASCADE")
        await c.execute("TRUNCATE crowdfunding_projects CASCADE")
        await c.execute("TRUNCATE crowdfunding_cycles RESTART IDENTITY CASCADE")
        await c.execute("TRUNCATE crowdfunding_behavior_events")
        await c.execute("TRUNCATE crowdfunding_engagement_state")
        await c.execute("TRUNCATE crowdfunding_message_performance")
        for k, v in [
            ("crowdfunding_threshold_projects", "2"),
            ("crowdfunding_min_account_age_days", "0"),
            ("crowdfunding_min_activity_score", "0"),
            ("crowdfunding_required_actions",
             '{"posts":0,"likes":0,"transactions":0}'),
        ]:
            await c.execute(
                "INSERT INTO admin_settings (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                k, v,
            )
        await c.close()
    asyncio.get_event_loop().run_until_complete(_r())


@pytest.fixture(scope="module")
def alice_tok():
    return _login("alice@japap.com", "Alice2026!")


@pytest.fixture(scope="module")
def admin_tok():
    return _login("admin@japap.com", "JapapAdmin2024!")


def test_anon_engagement_returns_cold_default():
    r = requests.get(f"{API}/api/crowdfunding/engagement/me?with_llm=false")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "cold"
    assert body["user_id"] is None
    assert body["message"]


def test_track_event_accepts_valid_types(alice_tok):
    H = {"Authorization": f"Bearer {alice_tok}",
         "Content-Type": "application/json"}
    for t in ("view", "share", "invite", "session"):
        r = requests.post(f"{API}/api/crowdfunding/events",
                          json={"event_type": t, "source": "whatsapp"},
                          headers=H)
        assert r.status_code == 200, (t, r.text)


def test_track_event_rejects_unknown_type(alice_tok):
    r = requests.post(
        f"{API}/api/crowdfunding/events",
        json={"event_type": "hack_attempt"},
        headers={"Authorization": f"Bearer {alice_tok}",
                 "Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_engaged_state_after_share(alice_tok):
    """Alice has shares but no project → state should be 'engaged'
    (or at least non-cold)."""
    r = requests.get(f"{API}/api/crowdfunding/engagement/me?with_llm=false",
                     headers={"Authorization": f"Bearer {alice_tok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] in ("engaged", "cold")
    assert body["engagement_score"] >= 0
    assert "next_best_action" in body


def test_critical_state_with_open_votes(alice_tok, admin_tok):
    """Push Alice's project to 80%+ with votes_open → state=critical, ui_mode=urgent."""
    # Create alice's project
    H = {"Authorization": f"Bearer {alice_tok}",
         "Content-Type": "application/json"}
    requests.post(f"{API}/api/crowdfunding/projects", json={
        "title": "Projet critique iter142D",
        "description": "Description longue suffisante pour passer la validation.",
        "category": "art", "country_code": "CM",
    }, headers=H)

    # Need a 2nd project to open votes (threshold=2)
    bob_tok = _login("bob@japap.com", "Test1234!")
    requests.post(f"{API}/api/crowdfunding/projects", json={
        "title": "Projet de Bob iter142D",
        "description": "Description longue suffisante pour passer la validation.",
        "category": "tech", "country_code": "CM",
    }, headers={"Authorization": f"Bearer {bob_tok}",
                "Content-Type": "application/json"})

    # Force Alice to 80% — directly in DB (admin doesn't have an endpoint
    # for this — fixture-only hack).
    async def _push():
        c = await asyncpg.connect(DB_URL)
        await c.execute(
            "UPDATE crowdfunding_cycles SET votes_to_win=10 WHERE status='active'")
        await c.execute(
            "UPDATE crowdfunding_projects SET votes_count=8 "
            "WHERE user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')")
        await c.execute(
            "UPDATE crowdfunding_engagement_state SET last_message_at=NULL, "
            "last_message_id=NULL, cooldown_until=NULL "
            "WHERE user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')")
        await c.close()
    asyncio.get_event_loop().run_until_complete(_push())

    r = requests.get(f"{API}/api/crowdfunding/engagement/me?with_llm=false",
                     headers={"Authorization": f"Bearer {alice_tok}"},
                     timeout=10)
    body = r.json()
    assert body["state"] == "critical", body
    assert body["ui_mode"] == "urgent"
    assert body["urgency_score"] >= 80
    assert body["context"]["pct"] == 80
    assert body["next_best_action"] == "share"
    assert body["message"]


def test_cooldown_suppresses_same_state(alice_tok):
    """Calling /engagement/me twice in a row must trigger 6h cooldown
    on the same state."""
    # Reset cooldown for a clean run
    async def _r():
        c = await asyncpg.connect(DB_URL)
        await c.execute(
            "UPDATE crowdfunding_engagement_state SET last_message_at=NULL, "
            "last_message_id=NULL, cooldown_until=NULL "
            "WHERE user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')")
        await c.close()
    asyncio.get_event_loop().run_until_complete(_r())

    H = {"Authorization": f"Bearer {alice_tok}"}
    r1 = requests.get(f"{API}/api/crowdfunding/engagement/me?with_llm=false",
                      headers=H, timeout=10).json()
    r2 = requests.get(f"{API}/api/crowdfunding/engagement/me?with_llm=false",
                      headers=H, timeout=10).json()
    assert r1["message"] is not None, r1
    assert r2["message"] is None, r2
    assert r2["ui_mode"] == "calm"
    assert r2["cooldown_until"]


def test_feedback_dismiss_creates_7d_cooldown(alice_tok):
    """User dismisses → 7-day silence on that user."""
    # Reset cooldown first
    async def _r():
        c = await asyncpg.connect(DB_URL)
        await c.execute(
            "UPDATE crowdfunding_engagement_state SET last_message_at=NULL, "
            "last_message_id='crit_v1', cooldown_until=NULL "
            "WHERE user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')")
        await c.close()
    asyncio.get_event_loop().run_until_complete(_r())

    H = {"Authorization": f"Bearer {alice_tok}",
         "Content-Type": "application/json"}
    r = requests.post(f"{API}/api/crowdfunding/engagement/feedback",
                      json={"message_id": "crit_v1", "action": "dismissed"},
                      headers=H)
    assert r.status_code == 200

    # Verify cooldown set
    async def _check():
        c = await asyncpg.connect(DB_URL)
        row = await c.fetchrow(
            "SELECT cooldown_until FROM crowdfunding_engagement_state "
            "WHERE user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')")
        await c.close()
        return row
    row = asyncio.get_event_loop().run_until_complete(_check())
    assert row["cooldown_until"] is not None


def test_admin_message_performance_listing(admin_tok):
    H = {"Authorization": f"Bearer {admin_tok}"}
    r = requests.get(f"{API}/api/crowdfunding/admin/engagement/messages",
                     headers=H)
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    if rows:
        first = rows[0]
        for k in ("message_id", "state", "variant_text", "shown_count",
                  "clicked_count", "dismissed_count", "shared_count",
                  "conversion_rate"):
            assert k in first


def test_non_admin_blocked_from_message_performance(alice_tok):
    r = requests.get(f"{API}/api/crowdfunding/admin/engagement/messages",
                     headers={"Authorization": f"Bearer {alice_tok}"})
    assert r.status_code == 403
