"""E2E tests for the iter142 Crowdfunding viral module.

Run via:
    cd /app/backend && python3 -m pytest tests/test_iter142_crowdfunding.py -v -s

Tests cover the CEO P0 rules:
  - 1 user → 1 active project per cycle
  - Votes blocked until threshold reached
  - Atomic winner detection + wallet credit
  - Admin-only cycle reset (no auto-restart)
  - Self-vote blocked / double-vote blocked
"""
import os
import asyncio
import asyncpg
import pytest
import requests
import uuid
from dotenv import load_dotenv
from decimal import Decimal

load_dotenv("/app/backend/.env")
DB_URL = os.environ["DATABASE_URL"]
API = os.environ.get("PUBLIC_API_URL", "http://localhost:8001")
BYPASS = os.environ.get("MATH_CAPTCHA_TEST_BYPASS_TOKEN") or os.environ.get(
    "TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026"
)


# ─── Helpers ───────────────────────────────────────────────────────────────
async def _reset_state():
    c = await asyncpg.connect(DB_URL)
    try:
        await c.execute("TRUNCATE crowdfunding_votes CASCADE")
        await c.execute("TRUNCATE crowdfunding_projects CASCADE")
        await c.execute("TRUNCATE crowdfunding_cycles RESTART IDENTITY CASCADE")
        # Lower thresholds for testing
        for k, v in [
            ("crowdfunding_threshold_projects", "2"),
            ("crowdfunding_votes_to_win", "3"),
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
    finally:
        await c.close()


def _login(email: str, password: str) -> str:
    """Return Bearer token. Uses captcha bypass."""
    r = requests.post(
        f"{API}/api/auth/login",
        json={
            "email": email,
            "password": password,
            "captcha_id": BYPASS,
            "captcha_answer": "x",
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"login {email} failed: {r.status_code} {r.text[:200]}")
    body = r.json()
    tok = body.get("access_token") or body.get("token")
    if not tok:
        raise RuntimeError(f"login {email}: no access_token in {body}")
    return tok


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ─── Fixture ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="module", autouse=True)
def setup_module():
    asyncio.get_event_loop().run_until_complete(_reset_state())


@pytest.fixture(scope="module")
def tokens():
    return {
        "alice": _login("alice@japap.com", "Alice2026!"),
        "bob": _login("bob@japap.com", "Test1234!"),
        "admin": _login("admin@japap.com", "JapapAdmin2024!"),
    }


# ─── Tests ─────────────────────────────────────────────────────────────────
def test_01_state_initial(tokens):
    r = requests.get(f"{API}/api/crowdfunding/state").json()
    assert r["projects_count"] == 0
    assert r["cycle"]["votes_open"] is False
    assert r["cycle"]["threshold_projects"] == 2
    assert r["cycle"]["votes_to_win"] == 3


def test_02_create_project_alice(tokens):
    payload = {
        "title": "Restaurant communautaire à Yaoundé",
        "description": (
            "Nous voulons ouvrir un restaurant qui emploie 10 jeunes du "
            "quartier et propose des repas accessibles à tous. Plan complet"
            " avec emplacement déjà repéré."
        ),
        "objective": "Créer 10 emplois et nourrir 200 personnes/jour.",
        "category": "business",
        "country_code": "CM",
        "duration_days": 30,
    }
    r = requests.post(f"{API}/api/crowdfunding/projects",
                      json=payload, headers=_h(tokens["alice"]))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"]
    assert body["votes_open"] is False  # only 1 project, threshold is 2


def test_03_alice_cannot_create_second(tokens):
    payload = {
        "title": "Deuxième projet d'Alice",
        "description": "Cette tentative doit échouer car Alice a déjà un projet actif." * 2,
        "category": "tech",
    }
    r = requests.post(f"{API}/api/crowdfunding/projects",
                      json=payload, headers=_h(tokens["alice"]))
    assert r.status_code == 409, r.text


def test_04_create_project_bob_opens_votes(tokens):
    payload = {
        "title": "Application mobile pour agriculteurs",
        "description": "Une app simple pour que les agriculteurs accèdent aux "
                       "prix marché en temps réel. Prototype testé en zone rurale.",
        "category": "tech",
        "country_code": "CM",
        "duration_days": 60,
    }
    r = requests.post(f"{API}/api/crowdfunding/projects",
                      json=payload, headers=_h(tokens["bob"]))
    assert r.status_code == 201, r.text
    body = r.json()
    # Threshold reached (2/2) → votes auto-opened
    assert body["votes_open"] is True


def test_05_state_threshold_reached(tokens):
    r = requests.get(f"{API}/api/crowdfunding/state").json()
    assert r["projects_count"] == 2
    assert r["cycle"]["votes_open"] is True
    assert r["projects_remaining_to_open"] == 0


def test_06_list_projects_returns_two(tokens):
    r = requests.get(f"{API}/api/crowdfunding/projects?sort=recent").json()
    assert len(r) == 2
    slugs = [p["slug"] for p in r]
    assert all(s for s in slugs)


def test_07_alice_cannot_vote_self(tokens):
    # Get alice's own project slug
    r = requests.get(f"{API}/api/crowdfunding/projects?sort=recent").json()
    alice_slug = next(p["slug"] for p in r if "Restaurant" in p["title"])
    res = requests.post(f"{API}/api/crowdfunding/projects/{alice_slug}/vote",
                        headers=_h(tokens["alice"]))
    assert res.status_code == 400
    assert "propre" in res.json()["detail"].lower() or "yourself" in res.json()["detail"].lower()


def test_08_bob_votes_alice(tokens):
    r = requests.get(f"{API}/api/crowdfunding/projects?sort=recent").json()
    alice_slug = next(p["slug"] for p in r if "Restaurant" in p["title"])
    res = requests.post(f"{API}/api/crowdfunding/projects/{alice_slug}/vote",
                        headers=_h(tokens["bob"]))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["votes_count"] == 1
    assert body["won"] is False


def test_09_bob_double_vote_blocked(tokens):
    r = requests.get(f"{API}/api/crowdfunding/projects?sort=recent").json()
    alice_slug = next(p["slug"] for p in r if "Restaurant" in p["title"])
    res = requests.post(f"{API}/api/crowdfunding/projects/{alice_slug}/vote",
                        headers=_h(tokens["bob"]))
    assert res.status_code == 409, res.text


def test_10_winner_detection_and_reward(tokens):
    """Lower votes_to_win to 2 via admin, then have an admin user vote
    (he is not the project owner) to push count from 1 → 2 = winner.
    Tests atomic winner detection + wallet credit + cycle close."""
    # Lower bar: votes_to_win = 2 (Alice has 1 vote already from Bob)
    res = requests.put(
        f"{API}/api/crowdfunding/admin/cycles/active",
        json={"votes_to_win": 2},
        headers=_h(tokens["admin"]),
    )
    assert res.status_code == 200, res.text

    r = requests.get(f"{API}/api/crowdfunding/projects?sort=recent").json()
    alice_slug = next(p["slug"] for p in r if "Restaurant" in p["title"])

    # Capture Alice wallet pre-reward
    loop = asyncio.new_event_loop()
    async def _bal_before():
        c = await asyncpg.connect(DB_URL)
        u = await c.fetchrow("SELECT user_id FROM users WHERE email='alice@japap.com'")
        bal = await c.fetchval("SELECT balance FROM wallets WHERE user_id=$1", u["user_id"])
        await c.close()
        return float(bal or 0)
    bal_before = loop.run_until_complete(_bal_before())

    # Admin votes — winning vote!
    res = requests.post(f"{API}/api/crowdfunding/projects/{alice_slug}/vote",
                        headers=_h(tokens["admin"]))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["votes_count"] == 2
    assert body["won"] is True
    assert body["reward_tx_id"] and body["reward_tx_id"].startswith("tx_cf_")

    # Verify wallet credited 50000 (default reward)
    async def _bal_after():
        c = await asyncpg.connect(DB_URL)
        u = await c.fetchrow("SELECT user_id FROM users WHERE email='alice@japap.com'")
        bal = await c.fetchval("SELECT balance FROM wallets WHERE user_id=$1", u["user_id"])
        cy = await c.fetchrow("SELECT status, winner_user_id FROM crowdfunding_cycles WHERE cycle_number=1")
        proj_status = await c.fetchval(
            "SELECT status FROM crowdfunding_projects WHERE slug=$1", alice_slug)
        await c.close()
        return float(bal or 0), dict(cy), proj_status
    bal_after, cycle, proj_status = loop.run_until_complete(_bal_after())
    assert bal_after - bal_before == 50000, f"Expected +50000, got {bal_after - bal_before}"
    assert cycle["status"] == "completed"
    assert proj_status == "winner"


def test_11_between_cycles_no_more_votes(tokens):
    """After winner declared, cycle becomes 'completed' and no auto-restart.
    State endpoint must return between_cycles=True."""
    s = requests.get(f"{API}/api/crowdfunding/state").json()
    assert s["between_cycles"] is True, s
    assert s["cycle"] is None
    assert s["last_cycle"] and s["last_cycle"]["status"] == "completed"
    assert s["last_cycle"]["winner_user_id"] is not None

    # Project listing returns []
    r = requests.get(f"{API}/api/crowdfunding/projects").json()
    assert r == []

    # Trying to create a project returns 409 between cycles
    r = requests.post(
        f"{API}/api/crowdfunding/projects",
        json={"title": "Should fail",
              "description": "Aucun cycle actif " * 5,
              "category": "art"},
        headers=_h(tokens["bob"]),
    )
    assert r.status_code == 409, r.text


def test_12_admin_starts_new_cycle(tokens):
    res = requests.post(
        f"{API}/api/crowdfunding/admin/cycles",
        json={"threshold_projects": 5, "votes_to_win": 10,
              "reward_amount": 25000, "reward_currency": "XAF",
              "notes": "Cycle 2 — test"},
        headers=_h(tokens["admin"]),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["cycle_number"] == 2
    assert body["status"] == "active"

    # State now reflects cycle 2
    s = requests.get(f"{API}/api/crowdfunding/state").json()
    assert s["cycle"]["cycle_number"] == 2
    assert s["cycle"]["threshold_projects"] == 5
    assert s["projects_count"] == 0  # fresh cycle


def test_13_non_admin_cannot_start_cycle(tokens):
    res = requests.post(
        f"{API}/api/crowdfunding/admin/cycles",
        json={"threshold_projects": 5, "votes_to_win": 10,
              "reward_amount": 1000, "reward_currency": "XAF"},
        headers=_h(tokens["bob"]),
    )
    assert res.status_code == 403, res.text


def test_14_admin_update_active_cycle(tokens):
    res = requests.put(
        f"{API}/api/crowdfunding/admin/cycles/active",
        json={"votes_to_win": 20, "notes": "Bumped"},
        headers=_h(tokens["admin"]),
    )
    assert res.status_code == 200, res.text
    s = requests.get(f"{API}/api/crowdfunding/state").json()
    assert s["cycle"]["votes_to_win"] == 20


def test_15_admin_settings_get_put(tokens):
    g = requests.get(f"{API}/api/crowdfunding/admin/settings",
                     headers=_h(tokens["admin"]))
    assert g.status_code == 200, g.text
    p = requests.put(
        f"{API}/api/crowdfunding/admin/settings",
        json={"min_account_age_days": 14, "min_activity_score": 100},
        headers=_h(tokens["admin"]),
    )
    assert p.status_code == 200, p.text
    assert p.json()["min_account_age_days"] == 14


def test_16_alice_can_create_in_new_cycle(tokens):
    """1 user = 1 project per CYCLE — Alice (winner of cycle 1) should be
    able to create in cycle 2."""
    # First reset eligibility settings
    requests.put(
        f"{API}/api/crowdfunding/admin/settings",
        json={"min_account_age_days": 0, "min_activity_score": 0,
              "required_actions": {"posts": 0, "likes": 0, "transactions": 0}},
        headers=_h(tokens["admin"]),
    )
    r = requests.post(
        f"{API}/api/crowdfunding/projects",
        json={"title": "Nouveau projet d'Alice cycle 2",
              "description": "Alice peut tenter sa chance dans le cycle suivant. " * 2,
              "category": "art"},
        headers=_h(tokens["alice"]),
    )
    assert r.status_code == 201, r.text
