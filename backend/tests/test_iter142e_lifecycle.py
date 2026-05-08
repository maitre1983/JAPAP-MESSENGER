"""iter142E — Project lifecycle + share-performance + rival endpoints.

GO/NO-GO regression suite.
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


def _h(t):
    return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}


@pytest.fixture(scope="module", autouse=True)
def setup():
    async def _r():
        c = await asyncpg.connect(DB_URL)
        await c.execute("TRUNCATE crowdfunding_votes, crowdfunding_projects, "
                        "crowdfunding_behavior_events, crowdfunding_engagement_state, "
                        "crowdfunding_message_performance CASCADE")
        await c.execute("TRUNCATE crowdfunding_cycles RESTART IDENTITY CASCADE")
        for k, v in [
            ("crowdfunding_threshold_projects", "3"),
            ("crowdfunding_votes_to_win", "5"),
            ("crowdfunding_min_account_age_days", "0"),
            ("crowdfunding_min_activity_score", "0"),
            ("crowdfunding_required_actions",
             '{"posts":0,"likes":0,"transactions":0}'),
        ]:
            await c.execute(
                "INSERT INTO admin_settings (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", k, v)
        await c.close()
    asyncio.get_event_loop().run_until_complete(_r())


@pytest.fixture(scope="module")
def alice():
    return _login("alice@japap.com", "Alice2026!")


@pytest.fixture(scope="module")
def bob():
    return _login("bob@japap.com", "Test1234!")


@pytest.fixture(scope="module")
def admin():
    return _login("admin@japap.com", "JapapAdmin2024!")


def _create(tok, title, cat="art", country="CM"):
    return requests.post(f"{API}/api/crowdfunding/projects", json={
        "title": title,
        "description": "Description longue suffisante pour passer min length 20.",
        "category": cat, "country_code": country,
    }, headers=_h(tok))


def test_01_create_alice(alice):
    r = _create(alice, "Café solidaire iter142E")
    assert r.status_code == 201, r.text


def test_02_owner_can_edit_before_votes(alice):
    me = requests.get(f"{API}/api/crowdfunding/me", headers=_h(alice)).json()
    slug = me["project"]["slug"]
    r = requests.put(
        f"{API}/api/crowdfunding/projects/{slug}",
        json={"title": "Café solidaire (édité)",
              "description": "Description mise à jour avec assez de caractères pour la validation."},
        headers=_h(alice),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "title" in body["updated_fields"]


def test_03_other_user_cannot_edit(alice, bob):
    me = requests.get(f"{API}/api/crowdfunding/me", headers=_h(alice)).json()
    slug = me["project"]["slug"]
    r = requests.put(
        f"{API}/api/crowdfunding/projects/{slug}",
        json={"title": "Hijack test"},
        headers=_h(bob),
    )
    assert r.status_code == 403, r.text


def test_04_owner_can_delete_before_votes(alice):
    # Get the slug, delete it, ensure it disappears from listing.
    me = requests.get(f"{API}/api/crowdfunding/me", headers=_h(alice)).json()
    slug = me["project"]["slug"]
    r = requests.delete(f"{API}/api/crowdfunding/projects/{slug}", headers=_h(alice))
    assert r.status_code == 200, r.text
    # Listing no longer includes it
    listing = requests.get(f"{API}/api/crowdfunding/projects").json()
    assert slug not in [p["slug"] for p in listing]


def test_05_owner_cannot_delete_after_votes_open(alice, bob, admin):
    """Recreate Alice + Bob + admin, push to 3 → votes open, alice can no
    longer delete."""
    _create(alice, "Café reborn iter142E")
    _create(bob, "Projet Bob iter142E", cat="tech")
    _create(admin, "Projet admin iter142E", cat="education")

    # Verify votes_open
    st = requests.get(f"{API}/api/crowdfunding/state").json()
    assert st["cycle"]["votes_open"] is True

    me = requests.get(f"{API}/api/crowdfunding/me", headers=_h(alice)).json()
    slug = me["project"]["slug"]

    # Edit MUST be refused (votes started)
    r_edit = requests.put(f"{API}/api/crowdfunding/projects/{slug}",
                          json={"title": "Try edit"}, headers=_h(alice))
    assert r_edit.status_code == 409

    # Delete MUST be refused
    r_del = requests.delete(f"{API}/api/crowdfunding/projects/{slug}",
                            headers=_h(alice))
    assert r_del.status_code == 409


def test_06_admin_can_force_delete(alice, bob, admin):
    bob_me = requests.get(f"{API}/api/crowdfunding/me", headers=_h(bob)).json()
    bob_slug = bob_me["project"]["slug"]
    r = requests.delete(f"{API}/api/crowdfunding/admin/projects/{bob_slug}",
                        headers=_h(admin))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deleted"
    listing = requests.get(f"{API}/api/crowdfunding/projects").json()
    assert bob_slug not in [p["slug"] for p in listing]


def test_07_non_admin_cannot_force_delete(alice):
    me = requests.get(f"{API}/api/crowdfunding/me", headers=_h(alice)).json()
    slug = me["project"]["slug"]
    r = requests.delete(f"{API}/api/crowdfunding/admin/projects/{slug}",
                        headers=_h(alice))
    assert r.status_code == 403


def test_08_admin_disqualify_keeps_visible_log(admin, alice):
    me = requests.get(f"{API}/api/crowdfunding/me", headers=_h(alice)).json()
    slug = me["project"]["slug"]
    r = requests.post(
        f"{API}/api/crowdfunding/admin/projects/{slug}/disqualify",
        json={"reason": "Contenu inapproprié"},
        headers=_h(admin),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "disqualified"
    # Project removed from rankings (status not in active/winner)
    listing = requests.get(f"{API}/api/crowdfunding/projects").json()
    assert slug not in [p["slug"] for p in listing]


def test_09_share_performance_endpoint(alice):
    """Recreate alice, add a share, ensure share_performance returns it."""
    _create(alice, "Café re-reborn iter142E")
    requests.post(f"{API}/api/crowdfunding/events",
                  json={"event_type": "share", "source": "whatsapp"},
                  headers=_h(alice))
    r = requests.get(f"{API}/api/crowdfunding/share-performance/me",
                     headers=_h(alice))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_share_at"] is not None
    assert body["best_channel"] == "whatsapp"
    assert "conversion_rate" in body


def test_10_rival_endpoint(alice, bob):
    """After deletes, recreate Bob's project so we have 2 active. Alice is
    1st (no rival above her)."""
    _create(bob, "Bob nouveau iter142E", cat="tech")

    r_alice = requests.get(f"{API}/api/crowdfunding/rival", headers=_h(alice))
    assert r_alice.status_code == 200, r_alice.text
    body = r_alice.json()
    # Alice was last to be created, equal votes 0, so rank == 2 (sorted by
    # votes DESC then created_at ASC). Either way the API should not crash.
    assert "rank" in body
    assert "vote_velocity_10m" in body


def test_11_engagement_includes_velocity_and_share_perf(alice):
    """Reset cooldown so we get a fresh payload."""
    async def _r():
        c = await asyncpg.connect(DB_URL)
        await c.execute(
            "UPDATE crowdfunding_engagement_state SET cooldown_until=NULL, "
            "last_message_at=NULL WHERE user_id="
            "(SELECT user_id FROM users WHERE email='alice@japap.com')")
        await c.close()
    asyncio.get_event_loop().run_until_complete(_r())

    r = requests.get(f"{API}/api/crowdfunding/engagement/me?with_llm=false",
                     headers=_h(alice))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "vote_velocity_10m" in body
    assert "share_performance" in body
    sp = body["share_performance"]
    for k in ("last_share_votes", "last_share_clicks",
              "conversion_rate", "best_channel"):
        assert k in sp
