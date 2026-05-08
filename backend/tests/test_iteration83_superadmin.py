"""Tests for Iteration 83 — Superadmin dynamic URL + 2FA + Admin management.

Covers:
- Registration + OTP verification + resend cooldown
- Regular user login (no 2FA)
- Admin login (no 2FA)
- Superadmin login — 2FA required
- verify-2fa sets cookies + returns role=superadmin
- Superadmin admin CRUD (list / create / patch roles / reset-password / delete)
- Audit log
- URL token + url-check
- require_superadmin 403 for non-superadmins
- CSRF X-Requested-With enforcement
"""

import os
import re
import time
import uuid
import asyncio
import pytest
import requests
import asyncpg
from datetime import datetime, timezone

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
DATABASE_URL = (
    "postgresql://neondb_owner:npg_YFaoTc01dJkx@ep-still-boat-algu2h2h-pooler.c-3.eu-central-1.aws.neon.tech/"
    "neondb?sslmode=require"
)

SUPERADMIN_EMAIL = "emileparfait2003@gmail.com"
SUPERADMIN_PWD = "Gerard0103@"
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PWD = "JapapAdmin2024!"
USER_EMAIL = "bob@japap.com"
USER_PWD = "Test1234!"

CSRF_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"}


# ---------- helpers ----------
def _session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


async def _fetch_otp(email: str, purpose: str) -> str | None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow(
            "SELECT code FROM email_otps WHERE LOWER(email)=LOWER($1) AND purpose=$2 AND used=FALSE "
            "ORDER BY created_at DESC LIMIT 1",
            email, purpose,
        )
        return row["code"] if row else None
    finally:
        await conn.close()


def fetch_otp(email: str, purpose: str) -> str | None:
    return asyncio.run(_fetch_otp(email, purpose))


# ================= Registration OTP =================
class TestRegistrationOtp:
    unique_email = f"TEST_iter83_{uuid.uuid4().hex[:8]}@japap.com"

    def test_register_returns_otp_sent(self):
        s = _session()
        s.headers.update(CSRF_HEADERS)
        r = s.post(f"{API}/auth/register", json={
            "email": self.unique_email,
            "password": "StrongPass1!",
            "first_name": "Iter",
            "last_name": "Eighty3",
            "country": "CM",
            "phone": "+237670000000",
            "terms_accepted": True,
        })
        assert r.status_code in (200, 201), f"register {r.status_code}: {r.text}"
        data = r.json()
        assert data.get("status") == "otp_sent", f"Expected otp_sent, got {data}"

    def test_verify_otp_activates_user(self):
        time.sleep(2)
        otp = fetch_otp(self.unique_email, "register")
        assert otp, "No registration OTP in DB"
        s = _session()
        s.headers.update(CSRF_HEADERS)
        r = s.post(f"{API}/auth/verify-otp", json={"email": self.unique_email, "code": otp})
        assert r.status_code == 200, f"verify-otp {r.status_code}: {r.text}"
        data = r.json()
        assert "user" in data or "access_token" in data or data.get("status") == "verified"
        # cookies should be set now
        assert any(c.name.startswith("access") or "token" in c.name.lower() for c in s.cookies), \
            f"No auth cookie set, cookies={[c.name for c in s.cookies]}"

    def test_resend_otp_cooldown(self):
        # trigger fresh register then try 2x resend quickly
        email = f"TEST_iter83_resend_{uuid.uuid4().hex[:6]}@japap.com"
        s = _session()
        s.headers.update(CSRF_HEADERS)
        r = s.post(f"{API}/auth/register", json={
            "email": email, "password": "StrongPass1!",
            "first_name": "R", "last_name": "Cool", "country": "CM",
            "phone": "+237670000001", "terms_accepted": True,
        })
        assert r.status_code in (200, 201)
        r1 = s.post(f"{API}/auth/resend-otp", json={"email": email})
        r2 = s.post(f"{API}/auth/resend-otp", json={"email": email})
        # Second call within 60s should fail (429 or 400)
        assert r2.status_code in (400, 429), f"Expected cooldown rejection, got {r2.status_code}: {r2.text}"


# ================= Login =================
class TestLoginFlows:
    def test_regular_user_login(self):
        s = _session()
        s.headers.update(CSRF_HEADERS)
        r = s.post(f"{API}/auth/login", json={"email": USER_EMAIL, "password": USER_PWD})
        assert r.status_code == 200, f"user login {r.status_code}: {r.text}"
        data = r.json()
        # Should NOT be otp_required
        assert data.get("status") != "otp_required", f"Regular user should not trigger 2FA: {data}"
        assert "user" in data or "access_token" in data, f"Missing user/token: {data}"

    def test_admin_login_no_2fa(self):
        s = _session()
        s.headers.update(CSRF_HEADERS)
        r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PWD})
        assert r.status_code == 200, f"admin login {r.status_code}: {r.text}"
        data = r.json()
        assert data.get("status") != "otp_required", f"Admin should NOT trigger 2FA: {data}"
        user = data.get("user") or {}
        assert user.get("role") == "admin", f"Expected role=admin, got {user}"

    def test_superadmin_login_requires_2fa(self, superadmin_login_response):
        """Verify initial SA login returns otp_required and NO auth cookies."""
        s, data = superadmin_login_response
        assert data.get("status") == "otp_required", f"Expected otp_required, got {data}"
        assert not any("access" in c.name.lower() for c in s.cookies), \
            f"Auth cookies should NOT be set before 2FA: {[c.name for c in s.cookies]}"


# Module-scoped fixtures: login SA once, verify 2FA once, reuse for all tests.
@pytest.fixture(scope="module")
def superadmin_login_response():
    s = _session()
    s.headers.update(CSRF_HEADERS)
    r = s.post(f"{API}/auth/login", json={"email": SUPERADMIN_EMAIL, "password": SUPERADMIN_PWD})
    assert r.status_code == 200, f"SA login failed: {r.text}"
    return s, r.json()


# ================= Superadmin 2FA + admin mgmt =================
@pytest.fixture(scope="module")
def superadmin_session(superadmin_login_response):
    s, data = superadmin_login_response
    assert data.get("status") == "otp_required"
    time.sleep(3)
    otp = fetch_otp(SUPERADMIN_EMAIL, "login_2fa")
    assert otp, "No 2FA OTP found in email_otps"
    r2 = s.post(f"{API}/auth/verify-2fa", json={"email": SUPERADMIN_EMAIL, "code": otp})
    assert r2.status_code == 200, f"verify-2fa failed: {r2.text}"
    data2 = r2.json()
    user = data2.get("user") or {}
    assert user.get("role") == "superadmin", f"role not superadmin: {user}"
    return s


class TestSuperadminManagement:
    def test_list_admins(self, superadmin_session):
        r = superadmin_session.get(f"{API}/admin/super/admins")
        assert r.status_code == 200, f"list admins: {r.status_code} {r.text}"
        data = r.json()
        admins = data if isinstance(data, list) else data.get("admins", data.get("items", []))
        assert isinstance(admins, list)
        # Should include at least superadmin + admin
        emails = [a.get("email") for a in admins]
        assert SUPERADMIN_EMAIL in emails, f"superadmin missing from list: {emails}"
        assert ADMIN_EMAIL in emails, f"admin missing from list: {emails}"
        # admin_sub_roles field present
        for a in admins:
            assert "admin_sub_roles" in a or "sub_roles" in a, f"sub_roles missing on {a}"

    def test_url_token_returns_ddmmyy(self, superadmin_session):
        r = superadmin_session.get(f"{API}/admin/super/url-token")
        assert r.status_code == 200, f"url-token: {r.text}"
        data = r.json()
        token = data.get("token") or data.get("url_token")
        assert token and re.match(r"^\d{6}$", str(token)), f"bad token: {data}"
        today = datetime.now(timezone.utc).strftime("%d%m%y")
        assert str(token) == today, f"token {token} != today {today}"

    def test_url_check_public(self):
        # No auth required
        today = datetime.now(timezone.utc).strftime("%d%m%y")
        r = requests.get(f"{API}/admin/super/url-check", params={"token": today})
        assert r.status_code == 200, f"url-check valid: {r.text}"
        assert r.json().get("valid") is True
        r2 = requests.get(f"{API}/admin/super/url-check", params={"token": "000000"})
        assert r2.status_code == 200
        assert r2.json().get("valid") is False

    def test_create_admin_and_patch_and_delete(self, superadmin_session):
        new_email = f"TEST_iter83_admin_{uuid.uuid4().hex[:8]}@japap.com"
        payload = {
            "email": new_email,
            "password": "TempPass123!",
            "first_name": "Temp",
            "last_name": "Admin",
            "sub_roles": ["content_moderator", "wallet_manager"],
        }
        r = superadmin_session.post(f"{API}/admin/super/admins", json=payload)
        assert r.status_code in (200, 201), f"create admin: {r.status_code} {r.text}"
        created = r.json()
        new_id = (
            (created.get("admin") or {}).get("user_id")
            or (created.get("user") or {}).get("user_id")
            or created.get("user_id")
        )
        assert new_id, f"no user_id in create response: {created}"

        # verify appears in list
        lst = superadmin_session.get(f"{API}/admin/super/admins").json()
        admins = lst if isinstance(lst, list) else lst.get("admins", lst.get("items", []))
        assert new_email.lower() in [a.get("email", "").lower() for a in admins]

        # PATCH roles
        rp = superadmin_session.patch(
            f"{API}/admin/super/admins/{new_id}/roles",
            json={"sub_roles": ["campaign_manager", "support_agent", "wheel_admin"]},
        )
        assert rp.status_code == 200, f"patch roles: {rp.status_code} {rp.text}"

        # Reset password
        rr = superadmin_session.post(
            f"{API}/admin/super/admins/{new_id}/reset-password",
            json={"new_password": "NewTempPass123!"},
        )
        assert rr.status_code == 200, f"reset pwd: {rr.status_code} {rr.text}"

        # CSRF: DELETE without XRW header should be rejected
        plain = requests.Session()
        plain.cookies.update(superadmin_session.cookies)
        no_csrf = plain.delete(f"{API}/admin/super/admins/{new_id}")
        assert no_csrf.status_code in (400, 403, 401), \
            f"Expected CSRF rejection without XRW, got {no_csrf.status_code}"

        # DELETE (demote)
        rd = superadmin_session.delete(f"{API}/admin/super/admins/{new_id}")
        assert rd.status_code in (200, 204), f"delete/demote: {rd.status_code} {rd.text}"

    def test_audit_log(self, superadmin_session):
        r = superadmin_session.get(f"{API}/admin/super/audit-log")
        assert r.status_code == 200, f"audit-log: {r.text}"
        data = r.json()
        entries = data.get("logs") if isinstance(data, dict) else data
        assert isinstance(entries, list), f"logs missing: {data}"
        assert len(entries) > 0, "audit log should have entries after operations"
        # Verify recent entries include our actions
        actions = {e.get("action") for e in entries[:20]}
        assert any(a in actions for a in ("admin.create", "superadmin.login")), \
            f"Expected admin.create/superadmin.login in recent: {actions}"
        # Report missing audit actions as warning
        expected = {"admin.create", "admin.role_update", "admin.reset_password", "admin.delete"}
        missing = expected - actions
        if missing:
            print(f"WARNING: Missing audit actions in recent entries: {missing}")


# ================= Authorization =================
class TestAuthorization:
    def test_require_superadmin_rejects_admin(self):
        s = _session()
        s.headers.update(CSRF_HEADERS)
        r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PWD})
        assert r.status_code == 200
        # admin should get 403 on superadmin-only endpoints
        r2 = s.get(f"{API}/admin/super/admins")
        assert r2.status_code == 403, f"Expected 403 for admin, got {r2.status_code}: {r2.text}"

    def test_require_superadmin_rejects_user(self):
        s = _session()
        s.headers.update(CSRF_HEADERS)
        r = s.post(f"{API}/auth/login", json={"email": USER_EMAIL, "password": USER_PWD})
        assert r.status_code == 200
        r2 = s.get(f"{API}/admin/super/admins")
        assert r2.status_code in (401, 403), f"Expected 401/403 for user, got {r2.status_code}"

    def test_require_superadmin_rejects_unauth(self):
        r = requests.get(f"{API}/admin/super/admins")
        assert r.status_code in (401, 403)

    def test_create_admin_csrf_missing_header(self, superadmin_session):
        plain = requests.Session()
        plain.cookies.update(superadmin_session.cookies)
        plain.headers.update({"Content-Type": "application/json"})
        r = plain.post(f"{API}/admin/super/admins", json={
            "email": f"TEST_nocsrf_{uuid.uuid4().hex[:6]}@x.com",
            "password": "X1!aaaaa",
            "first_name": "A", "last_name": "B",
            "sub_roles": [],
        })
        assert r.status_code in (400, 403), f"Expected CSRF 400/403, got {r.status_code}"
