"""iter240k — Tests for Admin User Detail endpoints.

Tests the 7 admin endpoints under /api/admin/users/{user_id}/* for the
"User Detail" admin panel.
"""
import os
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
# Prefer localhost for backend tests when ingress is slow; fallback to public URL
BASE_URL = os.environ.get("TEST_BASE_URL") or "http://localhost:8001"
PUBLIC_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "BASE_URL must be set"

ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
BOB_EMAIL = "bob@japap.com"
BOB_PASSWORD = "Test1234!"
BOB_USER_ID = "user_a1b203440a53"
CAPTCHA = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}


def _login(email: str, password: str) -> requests.Session:
    """Login and return a session with auth cookies set."""
    s = requests.Session()
    # SPA convention bypasses CSRF middleware for cookie-authed requests
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    payload = {"email": email, "password": password, **CAPTCHA}
    r = s.post(f"{BASE_URL}/api/auth/login", json=payload, timeout=60)
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text[:300]}"
    data = r.json()
    # If JWT bearer token returned, also set Authorization header
    token = data.get("access_token") or data.get("token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    # Forward CSRF cookie → header (double-submit)
    csrf = s.cookies.get("csrf_token") or s.cookies.get("XSRF-TOKEN") or s.cookies.get("csrftoken")
    if csrf:
        s.headers.update({"X-CSRF-Token": csrf, "X-XSRF-TOKEN": csrf})
    return s


@pytest.fixture(scope="module")
def admin_session():
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def bob_session():
    return _login(BOB_EMAIL, BOB_PASSWORD)


# ──────────────── Auth / RBAC ────────────────
class TestAuthGuards:
    def test_unauthenticated_returns_401_or_403(self):
        r = requests.get(f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/detail", timeout=60)
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"

    def test_non_admin_user_returns_403(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/detail", timeout=15)
        assert r.status_code == 403, f"Bob (non-admin) should get 403, got {r.status_code}: {r.text[:200]}"


# ──────────────── GET /detail ────────────────
class TestUserDetail:
    def test_get_detail_admin_returns_full_dossier(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/detail", timeout=30)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:400]}"
        data = r.json()
        # Validate top-level keys
        required_keys = {
            "user", "kyc", "wallet", "transactions", "game_activity",
            "restrictions", "posts", "crowdfunding", "login_history",
            "flags", "referrals", "notes",
        }
        missing = required_keys - set(data.keys())
        assert not missing, f"Missing keys: {missing}"
        # Sensitive fields stripped
        assert "password_hash" not in data["user"], "password_hash leaked!"
        assert "totp_secret" not in data["user"], "totp_secret leaked!"
        # Game activity subkeys
        ga = data["game_activity"]
        for sub in ("quiz", "fortune_wheel", "mini_spin", "staking"):
            assert sub in ga, f"game_activity missing {sub}"
        # Wallet shape
        for k in ("balance", "currency", "is_locked"):
            assert k in data["wallet"], f"wallet missing {k}"
        # Lists are lists
        for k in ("transactions", "restrictions", "login_history", "flags", "notes"):
            assert isinstance(data[k], list), f"{k} must be list"

    def test_get_detail_unknown_user_returns_404(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/users/user_does_not_exist_xyz/detail", timeout=15)
        assert r.status_code == 404


# ──────────────── Notes ────────────────
class TestAdminNotes:
    def test_post_then_get_note(self, admin_session):
        note_text = "TEST_iter240k automated note"
        r = admin_session.post(
            f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/notes",
            json={"note": note_text}, timeout=15,
        )
        assert r.status_code == 200, f"POST notes: {r.status_code} {r.text[:300]}"
        body = r.json()
        assert body.get("ok") is True
        assert "id" in body

        # GET notes — should include the new note with admin join
        r2 = admin_session.get(f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/notes", timeout=15)
        assert r2.status_code == 200
        notes = r2.json()
        assert isinstance(notes, list)
        assert any(n.get("note") == note_text for n in notes), "Newly inserted note not found"
        # Verify admin join fields present in at least one note
        for n in notes:
            if n.get("note") == note_text:
                assert "admin_first_name" in n
                assert "admin_last_name" in n
                break

    def test_empty_note_rejected(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/notes",
            json={"note": ""}, timeout=15,
        )
        assert r.status_code in (400, 422)


# ──────────────── Restrictions ────────────────
class TestRestrictions:
    def test_restrict_then_unrestrict(self, admin_session):
        # Add a "games" restriction
        r = admin_session.post(
            f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/restrict",
            json={"type": "games", "reason": "TEST_iter240k", "duration_days": 1},
            timeout=15,
        )
        assert r.status_code == 200, f"restrict: {r.status_code} {r.text[:300]}"
        body = r.json()
        assert body.get("ok") is True
        assert body.get("id") is not None
        assert body.get("expires_at") is not None

        # Verify visible in detail.restrictions[]
        d = admin_session.get(f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/detail", timeout=20).json()
        active = [r for r in d.get("restrictions", []) if r.get("restriction_type") == "games" and not r.get("lifted_at")]
        assert active, "Restriction not visible in detail.restrictions[]"

        # Lift
        r2 = admin_session.post(
            f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/unrestrict",
            json={"type": "games", "reason": "TEST_iter240k_lift"},
            timeout=15,
        )
        assert r2.status_code == 200, f"unrestrict: {r2.status_code} {r2.text[:300]}"
        assert r2.json().get("ok") is True

        # Verify all games restrictions are lifted now
        d2 = admin_session.get(f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/detail", timeout=20).json()
        still_active = [r for r in d2.get("restrictions", []) if r.get("restriction_type") == "games" and not r.get("lifted_at")]
        assert not still_active, f"Restrictions still active after unrestrict: {still_active}"


# ──────────────── Reset game limits ────────────────
class TestResetGameLimits:
    def test_reset_returns_ok_even_when_tables_missing(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/reset-game-limits",
            timeout=15,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        assert r.json().get("ok") is True


# ──────────────── Send notification ────────────────
class TestSendNotification:
    def test_send_notification_inserts_row(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/send-notification",
            json={"message": "TEST_iter240k hello bob", "type": "info"},
            timeout=15,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body.get("ok") is True
        assert body.get("notif_id", "").startswith("notif_")


# ──────────────── RBAC negative on mutations ────────────────
class TestNonAdminMutationsForbidden:
    def test_bob_cannot_add_note(self, bob_session):
        r = bob_session.post(
            f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/notes",
            json={"note": "should fail"}, timeout=15,
        )
        assert r.status_code in (401, 403)

    def test_bob_cannot_restrict(self, bob_session):
        r = bob_session.post(
            f"{BASE_URL}/api/admin/users/{BOB_USER_ID}/restrict",
            json={"type": "games", "reason": "x", "duration_days": 1}, timeout=15,
        )
        assert r.status_code in (401, 403)


# ──────────────── SW version ────────────────
class TestSwVersion:
    def test_sw_version_v25_iter240k(self):
        # SW served by frontend not backend — use public URL
        url = PUBLIC_URL if PUBLIC_URL else BASE_URL
        r = requests.get(f"{url}/sw.js", timeout=30)
        assert r.status_code == 200
        assert "v25-iter240k" in r.text, "SW_VERSION not updated to v25-iter240k"
