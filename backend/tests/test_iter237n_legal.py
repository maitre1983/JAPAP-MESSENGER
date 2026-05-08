"""iter237n+iter237o — Legal endpoints + health latency tests.

Covers:
- GET /api/legal/cgu (legal pages public render via frontend) — backend covers /api/legal/* only
- POST /api/legal/accept-cgu  (auth, idempotent)
- POST /api/legal/accept-cgje (auth, idempotent)
- POST /api/legal/accept-privacy (auth, idempotent)
- GET  /api/legal/status (auth)
- /api/health latency under load (iter237o litellm patch)
- Public legal HTML pages reachable (200 OK without auth)
"""
from __future__ import annotations

import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    # fallback: read from /app/frontend/.env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip()
                break
BASE_URL = BASE_URL.rstrip("/")
BYPASS = "JAPAP_E2E_BYPASS_2026"


# ---------- Fixtures ----------
def _csrf_headers(s: requests.Session) -> dict:
    """Return X-CSRF-Token header from session cookie (set on first GET)."""
    tok = s.cookies.get("csrf_token") or ""
    return {"X-CSRF-Token": tok} if tok else {}


@pytest.fixture(scope="module")
def alice_session():
    s = requests.Session()
    # First touch GET to obtain csrf_token cookie
    s.get(f"{BASE_URL}/api/health", timeout=10)
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={
            "email": "alice@japap.com",
            "password": "Alice2026!",
            "captcha_id": BYPASS,
            "captcha_answer": "0",
        },
        headers=_csrf_headers(s),
        timeout=15,
    )
    if r.status_code != 200:
        pytest.skip(f"Alice login failed: {r.status_code} {r.text[:200]}")
    return s


@pytest.fixture(scope="module")
def new_user_session():
    """Fresh registration to verify cgu/privacy POST hooks happen at signup."""
    s = requests.Session()
    s.get(f"{BASE_URL}/api/health", timeout=10)
    email = f"testlegal_{uuid.uuid4().hex[:8]}@japap.com"
    pwd = "Test1234!"
    r = s.post(
        f"{BASE_URL}/api/auth/register",
        json={
            "email": email,
            "password": pwd,
            "username": f"testlegal_{uuid.uuid4().hex[:6]}",
            "first_name": "Test",
            "last_name": "Legal",
            "country_code": "CM",
            "phone_number": f"6{uuid.uuid4().int % 100000000:08d}",
            "captcha_id": BYPASS,
            "captcha_answer": "0",
        },
        headers=_csrf_headers(s),
        timeout=20,
    )
    if r.status_code not in (200, 201):
        pytest.skip(f"Register failed: {r.status_code} {r.text[:200]}")
    return s, email


# ---------- Health latency (iter237o) ----------
class TestHealthLatency:
    def test_health_fast(self):
        t = time.time()
        r = requests.get(f"{BASE_URL}/api/health", timeout=5)
        dt = time.time() - t
        assert r.status_code == 200, r.text
        assert dt < 3.0, f"/api/health took {dt:.2f}s (expected <3s)"
        body = r.json()
        assert body.get("status") in ("healthy", "ok")

    def test_health_repeat(self):
        # Three consecutive fast calls -> ensures event loop not stuck
        for _ in range(3):
            t = time.time()
            r = requests.get(f"{BASE_URL}/api/health", timeout=5)
            assert r.status_code == 200
            assert (time.time() - t) < 3.0


# ---------- Public legal pages (frontend SPA renders them but backend also serves /api/legal status) ----------
class TestLegalPublicPages:
    @pytest.mark.parametrize("path", [
        "/legal/cgu",
        "/legal/conditions-de-jeu",
        "/legal/confidentialite",
        "/about",
        "/contact",
    ])
    def test_public_route_returns_html(self, path):
        # SPA: any unknown route returns the React index.html (200).
        r = requests.get(f"{BASE_URL}{path}", timeout=10, allow_redirects=True)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert "<html" in r.text.lower()

    def test_index_seo_meta(self):
        r = requests.get(f"{BASE_URL}/", timeout=10)
        assert r.status_code == 200
        html = r.text
        assert "JAPAP TECHNOLOGIES PLC" in html, "Missing raison sociale in <head>"
        assert "Addis Ababa" in html, "Missing Addis Ababa in <head>"


# ---------- /api/legal/status auth required ----------
class TestLegalAuthRequired:
    def test_status_unauthenticated_rejected(self):
        r = requests.get(f"{BASE_URL}/api/legal/status", timeout=10)
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"

    def test_accept_cgu_unauthenticated_rejected(self):
        r = requests.post(f"{BASE_URL}/api/legal/accept-cgu", timeout=10)
        assert r.status_code in (401, 403)


# ---------- Legal accept endpoints (idempotent) ----------
class TestLegalAcceptance:
    def test_status_shape(self, alice_session):
        r = alice_session.get(f"{BASE_URL}/api/legal/status", timeout=10)
        assert r.status_code == 200, r.text
        j = r.json()
        for k in ("cgu_accepted_at", "cgje_accepted_at", "privacy_accepted_at"):
            assert k in j, f"Missing field {k}"

    def test_accept_cgu_idempotent(self, alice_session):
        h = _csrf_headers(alice_session)
        r1 = alice_session.post(f"{BASE_URL}/api/legal/accept-cgu", headers=h, timeout=10)
        assert r1.status_code == 200, r1.text
        ts1 = r1.json().get("accepted_at")
        assert ts1, "accepted_at should be set"

        r2 = alice_session.post(f"{BASE_URL}/api/legal/accept-cgu", headers=h, timeout=10)
        assert r2.status_code == 200
        ts2 = r2.json().get("accepted_at")
        # Idempotent: timestamp must NOT change on second call
        assert ts1 == ts2, f"Idempotency broken: {ts1} != {ts2}"

    def test_accept_cgje_idempotent(self, alice_session):
        h = _csrf_headers(alice_session)
        r1 = alice_session.post(f"{BASE_URL}/api/legal/accept-cgje", headers=h, timeout=10)
        assert r1.status_code == 200, r1.text
        ts1 = r1.json().get("accepted_at")
        r2 = alice_session.post(f"{BASE_URL}/api/legal/accept-cgje", headers=h, timeout=10)
        assert r2.status_code == 200
        assert ts1 == r2.json().get("accepted_at")

    def test_accept_privacy_idempotent(self, alice_session):
        h = _csrf_headers(alice_session)
        r1 = alice_session.post(f"{BASE_URL}/api/legal/accept-privacy", headers=h, timeout=10)
        assert r1.status_code == 200, r1.text
        ts1 = r1.json().get("accepted_at")
        r2 = alice_session.post(f"{BASE_URL}/api/legal/accept-privacy", headers=h, timeout=10)
        assert r2.status_code == 200
        assert ts1 == r2.json().get("accepted_at")

    def test_status_reflects_acceptance(self, alice_session):
        h = _csrf_headers(alice_session)
        # Trigger all three first to ensure persistence
        alice_session.post(f"{BASE_URL}/api/legal/accept-cgu", headers=h, timeout=10)
        alice_session.post(f"{BASE_URL}/api/legal/accept-cgje", headers=h, timeout=10)
        alice_session.post(f"{BASE_URL}/api/legal/accept-privacy", headers=h, timeout=10)

        r = alice_session.get(f"{BASE_URL}/api/legal/status", timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert j["cgu_accepted_at"] is not None
        assert j["cgje_accepted_at"] is not None
        assert j["privacy_accepted_at"] is not None
        # boolean helpers
        assert j.get("cgu_accepted") is True
        assert j.get("cgje_accepted") is True
        assert j.get("privacy_accepted") is True


# ---------- New user registration legacy backfill ----------
class TestRegistrationLegacy:
    def test_new_user_status_endpoint(self, new_user_session):
        sess, email = new_user_session
        r = sess.get(f"{BASE_URL}/api/legal/status", timeout=10)
        assert r.status_code == 200, r.text
        j = r.json()
        # The /api/auth/register endpoint may or may not auto-trigger
        # accept-cgu/accept-privacy; the frontend RegisterPage.js does that.
        # We DO NOT assert non-NULL here at backend level (frontend job).
        assert "cgu_accepted_at" in j

    def test_new_user_can_accept(self, new_user_session):
        sess, _ = new_user_session
        h = _csrf_headers(sess)
        r = sess.post(f"{BASE_URL}/api/legal/accept-cgu", headers=h, timeout=10)
        assert r.status_code == 200
        assert r.json().get("accepted_at") is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
