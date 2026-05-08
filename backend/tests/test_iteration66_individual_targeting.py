"""Iter 66-IndivTarget — individual targeting (manual + search) tests."""
import os
import uuid
import asyncio
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
BOB = {"email": "bob@japap.com", "password": "Test1234!"}


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, r.text
    return s, r.json()["user"]["user_id"]


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def bob():
    return _login(BOB)


# ── User search ─────────────────────────────────────────────────────────────

def test_user_search_admin_only(bob):
    s, _ = bob
    r = s.get(f"{BASE_URL}/api/admin/messaging/users/search?q=bob", timeout=10)
    assert r.status_code == 403


def test_user_search_by_email(admin):
    s, _ = admin
    r = s.get(f"{BASE_URL}/api/admin/messaging/users/search?q=bob@japap", timeout=10)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    top = items[0]
    assert top["email"] == "bob@japap.com"
    for k in ("user_id", "email", "name", "is_pro", "is_active", "email_subscribed"):
        assert k in top


def test_user_search_empty_query_returns_empty(admin):
    s, _ = admin
    r = s.get(f"{BASE_URL}/api/admin/messaging/users/search?q=", timeout=10)
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_user_search_limit_respected(admin):
    s, _ = admin
    r = s.get(f"{BASE_URL}/api/admin/messaging/users/search?q=a&limit=3", timeout=10)
    assert r.status_code == 200
    assert len(r.json()["items"]) <= 3


# ── Campaign with individual targets (users + external emails) ──────────────

def test_campaign_with_mixed_targets(admin, bob):
    s, _ = admin
    _, bob_uid = bob
    r = s.post(f"{BASE_URL}/api/admin/messaging/campaigns", json={
        "name": f"indiv_mix_{uuid.uuid4().hex[:6]}",
        "subject": "Hi {{first_name}}",
        "body_html": "<p>Hello {{first_name}}!</p>",
        "individual_user_ids": [bob_uid],
        "individual_emails": ["stranger_e2e@example.com", "BAD", "bob@japap.com"],  # BAD dropped, bob dedup
    }, timeout=10)
    assert r.status_code == 200
    cid = r.json()["campaign_id"]

    # Detail should carry merged targets (1 uid + 1 external; duplicate + invalid removed)
    d = s.get(f"{BASE_URL}/api/admin/messaging/campaigns/{cid}", timeout=10).json()
    assert d["segment_id"] in (None, "")
    raw = d["individual_user_ids"]
    assert isinstance(raw, list)
    uids = [x for x in raw if isinstance(x, str)]
    ext = [x["email"] for x in raw if isinstance(x, dict)]
    assert uids == [bob_uid]
    assert ext == ["stranger_e2e@example.com"]


def test_campaign_send_individual_expands_correctly(admin, bob):
    s, _ = admin
    _, bob_uid = bob
    r = s.post(f"{BASE_URL}/api/admin/messaging/campaigns", json={
        "name": f"indiv_send_{uuid.uuid4().hex[:6]}",
        "subject": "Test {{first_name}}",
        "body_html": "<p>Hello {{first_name}}</p>",
        "individual_user_ids": [bob_uid],
        "individual_emails": ["external_test@example.com"],
    }, timeout=10)
    cid = r.json()["campaign_id"]

    rs = s.post(f"{BASE_URL}/api/admin/messaging/campaigns/{cid}/send",
                json={"confirm": True}, timeout=20)
    assert rs.status_code == 200, rs.text
    body = rs.json()
    assert body["status"] == "sending"
    # Audience = Bob + 1 external = 2
    assert body["audience_size"] == 2
    assert body["enqueued"] == 2

    # Inspect queue rows directly
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    db = os.environ["DATABASE_URL"]

    async def _inspect():
        c = await asyncpg.connect(db)
        try:
            return await c.fetch(
                "SELECT recipient_user_id, recipient_email, rendered_subject "
                "FROM email_send_queue WHERE campaign_id = $1 ORDER BY id",
                cid,
            )
        finally:
            await c.close()
    rows = asyncio.get_event_loop().run_until_complete(_inspect())
    subjects = {r["recipient_email"]: r["rendered_subject"] for r in rows}
    assert "bob@japap.com" in subjects
    assert "Bob" in subjects["bob@japap.com"]
    assert "external_test@example.com" in subjects
    # External recipient → first_name fallback "Utilisateur"
    assert "Utilisateur" in subjects["external_test@example.com"]
    # And the external row has recipient_user_id = NULL
    ext_row = next(r for r in rows if r["recipient_email"] == "external_test@example.com")
    assert ext_row["recipient_user_id"] is None


def test_campaign_send_no_audience_rejected(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/admin/messaging/campaigns", json={
        "name": f"no_audience_{uuid.uuid4().hex[:6]}",
        "subject": "Nope", "body_html": "<p>Nope</p>",
    }, timeout=10)
    cid = r.json()["campaign_id"]
    rs = s.post(f"{BASE_URL}/api/admin/messaging/campaigns/{cid}/send",
                json={"confirm": True}, timeout=10)
    assert rs.status_code == 400
    assert "Audience requise" in rs.json()["detail"]
