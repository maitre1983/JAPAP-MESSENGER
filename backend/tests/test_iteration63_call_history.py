"""Iteration 63 — Call History Sidebar backend tests.

Covers the unified GET /api/calls/history endpoint shape + reuse of the
existing POST /api/calls/{session_id}/summary/share for re-sharing.

Scope (per user brief):
  • past calls list (legacy 1-1 + LiveKit sessions + group)
  • status (completed / missed / rejected)
  • duration / participants / timestamp
  • has_summary + summary_id when available
  • re-share through the existing /summary/share endpoint
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
BOB = {"email": "bob@japap.com", "password": "Test1234!"}
CAROL = {"email": "carol@japap.com", "password": "Test1234!"}


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, r.text
    return s, r.json()["user"]["user_id"]


@pytest.fixture(scope="module")
def bob():
    return _login(BOB)


@pytest.fixture(scope="module")
def carol():
    return _login(CAROL)


# ─── 1. Legacy 1-1 call → history shape ────────────────────────────────────

def test_history_legacy_missed_call_included(bob, carol):
    """Bob initiates an audio call to Carol, marks it missed, then both
    users should see it in /history with direction + status correctly set."""
    s_bob, bob_uid = bob
    s_carol, carol_uid = carol
    r = s_bob.post(f"{BASE_URL}/api/calls/initiate",
                   json={"callee_id": carol_uid, "type": "audio"}, timeout=10)
    assert r.status_code == 200, r.text
    call_id = r.json()["call_id"]
    r2 = s_bob.post(f"{BASE_URL}/api/calls/end",
                    json={"call_id": call_id, "duration": 0, "status": "missed"}, timeout=10)
    assert r2.status_code == 200

    # Bob perspective: outgoing missed
    h_bob = s_bob.get(f"{BASE_URL}/api/calls/history?limit=10", timeout=10).json()
    bob_item = next((x for x in h_bob if x["call_id"] == call_id), None)
    assert bob_item, f"Missing call in Bob's history: {h_bob}"
    assert bob_item["kind"] == "p2p"
    assert bob_item["type"] == "audio"
    assert bob_item["direction"] == "outgoing"
    assert bob_item["status"] == "missed"
    assert bob_item["duration"] == 0
    assert bob_item["other"]["user_id"] == carol_uid
    assert bob_item["has_summary"] is False
    assert bob_item["has_recording"] is False
    assert bob_item["session_id"] is None
    assert bob_item["started_at"]

    # Carol perspective: incoming missed
    h_carol = s_carol.get(f"{BASE_URL}/api/calls/history?limit=10", timeout=10).json()
    carol_item = next((x for x in h_carol if x["call_id"] == call_id), None)
    assert carol_item, f"Missing call in Carol's history: {h_carol}"
    assert carol_item["direction"] == "incoming"
    assert carol_item["status"] == "missed"
    assert carol_item["other"]["user_id"] == bob_uid


def test_history_video_ended_call_with_duration(bob, carol):
    s_bob, _ = bob
    _, carol_uid = carol
    r = s_bob.post(f"{BASE_URL}/api/calls/initiate",
                   json={"callee_id": carol_uid, "type": "video"}, timeout=10)
    call_id = r.json()["call_id"]
    s_bob.post(f"{BASE_URL}/api/calls/end",
               json={"call_id": call_id, "duration": 125, "status": "ended"}, timeout=10)

    h = s_bob.get(f"{BASE_URL}/api/calls/history?limit=20", timeout=10).json()
    it = next(x for x in h if x["call_id"] == call_id)
    assert it["type"] == "video"
    assert it["status"] == "ended"
    assert it["duration"] == 125


# ─── 2. Ordering + pagination ──────────────────────────────────────────────

def test_history_order_desc_by_started_at(bob, carol):
    s_bob, _ = bob
    _, carol_uid = carol
    ids = []
    for _ in range(3):
        r = s_bob.post(f"{BASE_URL}/api/calls/initiate",
                       json={"callee_id": carol_uid, "type": "audio"}, timeout=10)
        cid = r.json()["call_id"]
        s_bob.post(f"{BASE_URL}/api/calls/end",
                   json={"call_id": cid, "duration": 10, "status": "ended"}, timeout=10)
        ids.append(cid)

    h = s_bob.get(f"{BASE_URL}/api/calls/history?limit=50", timeout=10).json()
    # The 3 most recent entries with these ids must appear in reverse-insert order
    seen = [x["call_id"] for x in h if x["call_id"] in ids]
    assert seen == list(reversed(ids)), f"Unexpected order: {seen}"


def test_history_limit_param_respected(bob):
    s_bob, _ = bob
    h = s_bob.get(f"{BASE_URL}/api/calls/history?limit=2", timeout=10).json()
    assert isinstance(h, list)
    assert len(h) <= 2


# ─── 3. Unauthenticated → 401 ──────────────────────────────────────────────

def test_history_requires_auth():
    r = requests.get(f"{BASE_URL}/api/calls/history", timeout=10)
    assert r.status_code == 401


# ─── 4. Re-share endpoint exists + participant gating ──────────────────────

def test_reshare_summary_unknown_session_404(bob):
    s_bob, _ = bob
    r = s_bob.post(f"{BASE_URL}/api/calls/ses_notexist_xxxxxxxxx/summary/share",
                   json={}, timeout=10)
    assert r.status_code == 404


def test_reshare_summary_non_participant_forbidden(bob):
    """Create a dummy call_session for a third user (admin as host) and make
    sure Bob can't re-share it since he wasn't a participant."""
    import asyncio
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    DATABASE_URL = os.environ.get("DATABASE_URL", "")

    async def _seed():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            # Find a user_id that's not Bob (admin user)
            admin = await conn.fetchrow(
                "SELECT user_id FROM users WHERE email='admin@japap.com'")
            if not admin:
                return None
            sid = f"ses_{uuid.uuid4().hex[:14]}"
            await conn.execute("""
                INSERT INTO call_sessions (session_id, room_name, mode, kind, host_user_id, status)
                VALUES ($1, $1, 'audio', 'p2p', $2, 'ended')
            """, sid, admin['user_id'])
            return sid
        finally:
            await conn.close()

    sid = asyncio.get_event_loop().run_until_complete(_seed())
    if not sid:
        pytest.skip("DATABASE_URL not configured for direct seed")
    s_bob, _ = bob
    r = s_bob.post(f"{BASE_URL}/api/calls/{sid}/summary/share",
                   json={}, timeout=10)
    assert r.status_code == 403, r.text
