"""iter224 - Quiz AI question pool admin widget tests."""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
BYPASS = "JAPAP_E2E_BYPASS_2026"

ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
USER_EMAIL = "bob@japap.com"
USER_PASSWORD = "Test1234!"


TIMEOUT = 30


def _retry_request(method, url, max_retries=3, **kwargs):
    """Wrapper that retries on transient read timeouts (preview CDN flake)."""
    last_err = None
    for i in range(max_retries):
        try:
            return requests.request(method, url, **kwargs)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            time.sleep(1 + i)
    raise last_err


def _retry_session(session, method, url, max_retries=3, **kwargs):
    last_err = None
    for i in range(max_retries):
        try:
            return session.request(method, url, **kwargs)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            time.sleep(1 + i)
    raise last_err


def _login(session: requests.Session, email: str, password: str) -> int:
    r = session.post(
        f"{BASE_URL}/api/auth/login",
        json={
            "email": email,
            "password": password,
            "captcha_id": BYPASS,
            "captcha_answer": "0",
        },
        timeout=TIMEOUT,
    )
    return r.status_code


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    code = _login(s, ADMIN_EMAIL, ADMIN_PASSWORD)
    if code != 200:
        pytest.skip(f"Admin login failed ({code})")
    return s


@pytest.fixture(scope="module")
def user_session():
    s = requests.Session()
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    code = _login(s, USER_EMAIL, USER_PASSWORD)
    if code != 200:
        pytest.skip(f"User login failed ({code})")
    return s


# ────────────────────────────────────────────────────────────
# TEST 1 — pool-status auth + payload shape
# ────────────────────────────────────────────────────────────
class TestPoolStatus:
    def test_pool_status_unauth_rejected(self):
        r = _retry_request("GET", f"{BASE_URL}/api/admin/games/quiz/pool-status", timeout=30)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}: {r.text[:200]}"

    def test_pool_status_admin_ok(self, admin_session):
        r = _retry_session(admin_session, "GET", f"{BASE_URL}/api/admin/games/quiz/pool-status", timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()

        required = {
            "active_count", "ai_total", "health", "health_min",
            "last_refresh_at", "last_batch_size", "next_refresh_at",
            "seconds_until_next", "refresh_interval_hours", "batch_size",
            "refresh_in_flight",
        }
        missing = required - set(data.keys())
        assert not missing, f"missing keys: {missing}; got keys={list(data.keys())}"

        assert isinstance(data["active_count"], int) and data["active_count"] >= 0
        assert data["health"] in ("ok", "warning", "critical")
        assert data["refresh_interval_hours"] == 48
        assert data["batch_size"] == 100
        assert data["health_min"] == 30
        assert isinstance(data["refresh_in_flight"], bool)

        # With current ~888 questions, health should be ok
        if data["active_count"] >= data["health_min"]:
            assert data["health"] == "ok"

    # TEST 2 - non-admin user gets 403
    def test_pool_status_user_forbidden(self, user_session):
        r = _retry_session(user_session, "GET", f"{BASE_URL}/api/admin/games/quiz/pool-status", timeout=30)
        assert r.status_code == 403, f"expected 403 for user, got {r.status_code}: {r.text[:200]}"


# ────────────────────────────────────────────────────────────
# TEST 3, 4 — pool-refresh accept + single-flight 409
# ────────────────────────────────────────────────────────────
class TestPoolRefresh:
    def test_pool_refresh_unauth_rejected(self):
        r = _retry_request("POST", f"{BASE_URL}/api/admin/games/quiz/pool-refresh", json={}, timeout=30)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"

    def test_pool_refresh_admin_async_then_409(self, admin_session):
        # First call should be 202 (or 409 if a refresh is still in flight from a previous test run)
        t0 = time.time()
        r1 = _retry_session(admin_session, "POST", f"{BASE_URL}/api/admin/games/quiz/pool-refresh", json={}, timeout=30)
        elapsed = time.time() - t0
        assert r1.status_code in (200, 202, 409), f"unexpected first status {r1.status_code}: {r1.text[:200]}"

        if r1.status_code in (200, 202):
            data = r1.json()
            assert data.get("status") == "accepted"
            assert data.get("batch_size") == 100
            assert "message" in data
            # Must return immediately (background task) — Claude calls take ~30-60s
            assert elapsed < 10, f"Endpoint blocked for {elapsed:.1f}s; expected <10s (background task)"

            # Immediate 2nd call should be 409 single-flight
            r2 = _retry_session(admin_session, "POST", f"{BASE_URL}/api/admin/games/quiz/pool-refresh", json={}, timeout=30)
            # Per spec: accept either 409 or 200/202 (if first task already finished, very unlikely)
            assert r2.status_code in (200, 202, 409), f"unexpected second status {r2.status_code}"
            if r2.status_code == 409:
                # Acceptable (expected): single-flight enforcement
                assert "renouvellement" in r2.text.lower() or "déjà" in r2.text.lower() or "already" in r2.text.lower()
        else:
            # First was 409 — still try to confirm in_flight semantics
            status = _retry_session(admin_session, "GET", f"{BASE_URL}/api/admin/games/quiz/pool-status", timeout=30).json()
            assert status.get("refresh_in_flight") is True

    def test_pool_status_reflects_in_flight(self, admin_session):
        # After a refresh has been triggered, in_flight should briefly be true
        r = _retry_session(admin_session, "GET", f"{BASE_URL}/api/admin/games/quiz/pool-status", timeout=30)
        assert r.status_code == 200
        # Not strictly required to still be in flight (may have finished),
        # but the field must exist and be a bool
        assert isinstance(r.json().get("refresh_in_flight"), bool)
