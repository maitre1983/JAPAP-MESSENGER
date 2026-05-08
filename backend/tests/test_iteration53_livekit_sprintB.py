"""Iteration 53 — Sprint B LiveKit Audio 1-1 backend tests.

Covers the LiveKit-backed call endpoints for 1-to-1 audio calls:
    POST /api/calls/session         — create a session + pre-create room
    POST /api/calls/token           — mint JWT (host + callee allowed, others 403)
    POST /api/calls/{id}/join       — mark participants
    POST /api/calls/{id}/leave      — ends session when host leaves
    GET  /api/calls/test-livekit    — admin health check

Requires LiveKit credentials configured in admin_settings (or LIVEKIT_* env).
Test users: bob, carol (see /app/memory/test_credentials.md).

Schema contract (verified iter53 after fix):
    /token returns { token, ws_url, room, identity, expires_at }
    Previously crashed with TypeError (with_ttl expected timedelta, not int).
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")

BOB = {"email": "bob@japap.com", "password": "Test1234!"}
CAROL = {"email": "carol@japap.com", "password": "Test1234!"}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, f"login {creds['email']} -> {r.status_code}: {r.text[:200]}"
    uid = r.json()["user"]["user_id"]
    return s, uid


@pytest.fixture(scope="module")
def bob():
    return _login(BOB)


@pytest.fixture(scope="module")
def carol():
    return _login(CAROL)


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


# ─── 1. Admin health check ──────────────────────────────────────────────────

def test_livekit_is_configured(admin):
    s, _ = admin
    r = s.get(f"{BASE_URL}/api/calls/test-livekit", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True, f"LiveKit not configured or unreachable: {body}"
    assert body.get("ws_url", "").startswith("wss://")


def test_livekit_non_admin_forbidden(bob):
    s, _ = bob
    r = s.get(f"{BASE_URL}/api/calls/test-livekit", timeout=10)
    assert r.status_code in (401, 403), r.text


# ─── 2. /session creation ───────────────────────────────────────────────────

def test_create_p2p_session(bob, carol):
    s_bob, _ = bob
    _, carol_uid = carol
    r = s_bob.post(
        f"{BASE_URL}/api/calls/session",
        json={"mode": "audio", "kind": "p2p", "callee_id": carol_uid},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["session_id"].startswith("sess_")
    assert d["room_name"].startswith("japap_sess_")
    assert d["mode"] == "audio"
    assert d["kind"] == "p2p"
    assert d["max_participants"] == 2


def test_create_session_self_not_allowed_p2p(bob):
    s_bob, _ = bob
    # Missing callee -> 400
    r = s_bob.post(
        f"{BASE_URL}/api/calls/session",
        json={"mode": "audio", "kind": "p2p"},
        timeout=10,
    )
    assert r.status_code == 400


def test_create_session_invalid_mode(bob, carol):
    s_bob, _ = bob
    _, carol_uid = carol
    r = s_bob.post(
        f"{BASE_URL}/api/calls/session",
        json={"mode": "hologram", "kind": "p2p", "callee_id": carol_uid},
        timeout=10,
    )
    assert r.status_code == 400


# ─── 3. /token minting ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def p2p_session(bob, carol):
    s_bob, _ = bob
    _, carol_uid = carol
    r = s_bob.post(
        f"{BASE_URL}/api/calls/session",
        json={"mode": "audio", "kind": "p2p", "callee_id": carol_uid},
        timeout=20,
    )
    assert r.status_code == 200
    return r.json()


def test_token_for_host_returns_jwt(bob, p2p_session):
    s_bob, _ = bob
    r = s_bob.post(
        f"{BASE_URL}/api/calls/token",
        json={"session_id": p2p_session["session_id"]},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    # Strict schema contract (frontend reads exactly these keys)
    assert set(["token", "ws_url", "room", "identity", "expires_at"]).issubset(d.keys())
    assert d["token"].count(".") == 2, "Not a valid JWT"
    assert d["ws_url"].startswith("wss://")
    assert d["room"] == p2p_session["room_name"]


def test_token_for_callee_allowed(carol, p2p_session):
    s_carol, carol_uid = carol
    r = s_carol.post(
        f"{BASE_URL}/api/calls/token",
        json={"session_id": p2p_session["session_id"]},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["identity"] == carol_uid


def test_token_for_stranger_forbidden(admin, p2p_session):
    # Admin isn't host nor callee for this p2p session -> 403
    s_admin, _ = admin
    r = s_admin.post(
        f"{BASE_URL}/api/calls/token",
        json={"session_id": p2p_session["session_id"]},
        timeout=10,
    )
    assert r.status_code == 403, r.text


def test_token_unknown_session(bob):
    s_bob, _ = bob
    r = s_bob.post(
        f"{BASE_URL}/api/calls/token",
        json={"session_id": f"sess_{uuid.uuid4().hex[:16]}"},
        timeout=10,
    )
    assert r.status_code == 404


# ─── 4. Join / Leave lifecycle ──────────────────────────────────────────────

def test_join_leave_ends_session_when_host_leaves(bob, carol):
    s_bob, _ = bob
    s_carol, carol_uid = carol
    # New session (the shared fixture one might already be ended)
    r = s_bob.post(
        f"{BASE_URL}/api/calls/session",
        json={"mode": "audio", "kind": "p2p", "callee_id": carol_uid},
        timeout=20,
    )
    assert r.status_code == 200
    sid = r.json()["session_id"]

    assert s_bob.post(f"{BASE_URL}/api/calls/{sid}/join", timeout=10).status_code == 200
    assert s_carol.post(f"{BASE_URL}/api/calls/{sid}/join", timeout=10).status_code == 200

    # Host leaves -> session ends
    leave = s_bob.post(f"{BASE_URL}/api/calls/{sid}/leave", timeout=10)
    assert leave.status_code == 200

    # Minting a token on an ended session should return 410 Gone
    tok = s_bob.post(
        f"{BASE_URL}/api/calls/token", json={"session_id": sid}, timeout=10,
    )
    assert tok.status_code == 410, tok.text
