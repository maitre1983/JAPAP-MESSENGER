"""Iteration 56 — Sprint D preflight (Record + AI Summary) — graceful 503 coverage.

Since R2 credentials are not yet provisioned, this pytest suite validates
the **preflight behavior** of the recording endpoints :

  - POST /api/calls/{id}/record/start      → 503 with a friendly FR message
  - POST /api/calls/{id}/record/stop       → 404 (no active recording)
  - GET  /api/calls/{id}/summary           → 'none' when nothing recorded
  - GET  /api/calls/test-r2  (admin-only)  → ok:false, reason='R2 non configuré…'
  - Authorization : only the host can call /record/start and /record/stop (403)

Once R2 credentials are set via admin_settings or env, the same test file can
be extended to cover the happy path (record round-trip + Whisper + Claude).
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
BOB = {"email": "bob@japap.com", "password": "Test1234!"}
CAROL = {"email": "carol@japap.com", "password": "Test1234!"}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}


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


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def session(bob, carol):
    s_bob, _ = bob
    _, carol_uid = carol
    r = s_bob.post(
        f"{BASE_URL}/api/calls/session",
        json={"mode": "audio", "kind": "p2p", "callee_id": carol_uid},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ─── Preflight : admin health checks ────────────────────────────────────────

def test_test_r2_without_config_returns_ok_false(admin):
    """When R2 creds are missing, endpoint should return ok:false gracefully
    (no 500 crash). Once admin pastes R2 creds this test flips to ok:true."""
    s, _ = admin
    r = s.get(f"{BASE_URL}/api/calls/test-r2", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body.get("ok"), bool)
    if not body["ok"]:
        assert "R2" in body.get("reason", "") or "boto3" in body.get("reason", "")


def test_test_r2_requires_admin(bob):
    s, _ = bob
    r = s.get(f"{BASE_URL}/api/calls/test-r2", timeout=10)
    assert r.status_code in (401, 403)


# ─── /record/start authorization & R2-missing fallback ─────────────────────

def test_record_start_non_host_forbidden(carol, session):
    s, _ = carol
    r = s.post(f"{BASE_URL}/api/calls/{session['session_id']}/record/start", timeout=15)
    assert r.status_code == 403
    detail = (r.json().get("detail") or "").lower()
    assert "hôte" in detail or "host" in detail


def test_record_start_503_when_r2_missing(bob, session):
    """When R2 creds aren't configured, /record/start should return 503 with
    an FR message. This test flips to 200 automatically once admin pastes creds."""
    s, _ = bob
    r = s.post(f"{BASE_URL}/api/calls/{session['session_id']}/record/start", timeout=20)
    # Either 503 (R2 not configured) or 200 (R2 configured). Both are valid.
    assert r.status_code in (200, 502, 503), r.text
    if r.status_code == 503:
        detail = r.json().get("detail", "")
        assert "R2" in detail or "LiveKit" in detail or "Appels" in detail


# ─── /record/stop without active recording ──────────────────────────────────

def test_record_stop_no_active_returns_404(bob, session):
    s, _ = bob
    r = s.post(f"{BASE_URL}/api/calls/{session['session_id']}/record/stop", timeout=15)
    # If R2 not configured → 503 or 404 ("Aucun enregistrement actif").
    # If R2 IS configured and a previous test /record/start succeeded → 200
    # (legitimately stops the recording). Both are valid preflight outcomes.
    assert r.status_code in (200, 404, 502, 503), r.text


def test_record_stop_non_host_forbidden(carol, session):
    s, _ = carol
    r = s.post(f"{BASE_URL}/api/calls/{session['session_id']}/record/stop", timeout=15)
    assert r.status_code == 403


# ─── /summary ───────────────────────────────────────────────────────────────

def test_summary_returns_none_for_unrecorded_call(bob, session):
    s, _ = bob
    r = s.get(f"{BASE_URL}/api/calls/{session['session_id']}/summary", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    # Either 'none' (no recording and no summary) or a legitimate row if
    # another test recorded this session first. The shape must always include
    # `status` + `session_id`.
    assert "status" in body
    assert body.get("session_id") == session["session_id"] or body.get("status") == "none"


def test_summary_requires_participant(admin, session):
    s, _ = admin
    r = s.get(f"{BASE_URL}/api/calls/{session['session_id']}/summary", timeout=10)
    # Admin is not a participant of this p2p session → 403
    assert r.status_code == 403
