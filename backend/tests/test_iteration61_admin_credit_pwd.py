"""Iteration 61 — Admin panel (credit + password reset) backend tests.

These endpoints already existed (iter before this), but the spec requires
explicit coverage of :
    - audit_logs entry with admin_id + target user_id on credit
    - transactions row of type 'admin_credit' / 'admin_debit'
    - force-logout on password reset (user_sessions wiped)
    - admin-only RBAC (bob → 403)
"""
import os
import uuid
import pytest
import requests
import asyncpg
import asyncio
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

BOB = {"email": "bob@japap.com", "password": "Test1234!"}
CAROL = {"email": "carol@japap.com", "password": "Test1234!"}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, r.text
    return s, r.json()["user"]["user_id"]


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module", autouse=True)
def ensure_test_users_fresh(admin):
    """Reset bob+carol passwords + clear login_attempts before this suite runs,
    in case a previous crashing iteration left them in an inconsistent state.
    This keeps the suite idempotent and order-independent."""
    async def reset_all():
        import bcrypt
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            h = bcrypt.hashpw(b"Test1234!", bcrypt.gensalt()).decode()
            await conn.execute(
                "UPDATE users SET password_hash = $1, password_changed_at = NULL "
                "WHERE email IN ('bob@japap.com','carol@japap.com')", h,
            )
            await conn.execute("DELETE FROM login_attempts")
        finally:
            await conn.close()
    asyncio.get_event_loop().run_until_complete(reset_all())
    yield


@pytest.fixture(scope="module")
def bob():
    return _login(BOB)


@pytest.fixture
def carol():
    # fresh per-test: we mutate Carol's password + balance
    return _login(CAROL)


# ─── 1. Credit endpoint ─────────────────────────────────────────────────────

def test_credit_requires_admin(bob, carol):
    s_bob, _ = bob
    _, carol_uid = carol
    r = s_bob.post(f"{BASE_URL}/api/admin/wallet/adjust",
                   json={"user_id": carol_uid, "amount": 10.0, "notes": "test"},
                   timeout=10)
    assert r.status_code in (401, 403)


def test_credit_happy_path_logs_tx_and_audit(admin, carol):
    s_admin, admin_uid = admin
    _, carol_uid = carol
    amount = 3.14
    notes = f"pytest iter61 {uuid.uuid4().hex[:6]}"
    r = s_admin.post(f"{BASE_URL}/api/admin/wallet/adjust",
                     json={"user_id": carol_uid, "amount": amount, "notes": notes},
                     timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tx_id"].startswith("adj_")
    tx_id = body["tx_id"]

    async def check():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            tx = await conn.fetchrow(
                "SELECT type, amount, notes, status FROM transactions WHERE tx_id = $1", tx_id,
            )
            assert tx is not None, "Transaction not persisted"
            assert tx["type"] == "admin_credit"
            assert tx["status"] == "completed"
            assert notes in (tx["notes"] or "")
            audit = await conn.fetchrow(
                "SELECT action, resource, details::text FROM audit_logs "
                "WHERE user_id = $1 AND action = 'admin_wallet_adjust' "
                "ORDER BY created_at DESC LIMIT 1",
                admin_uid,
            )
            assert audit is not None, "Audit log missing"
            assert carol_uid in (audit["details"] or ""), "Target user_id missing in audit details"
            assert tx_id in (audit["details"] or "")
        finally:
            await conn.close()
    asyncio.get_event_loop().run_until_complete(check())


def test_credit_negative_amount_is_debit(admin, carol):
    s_admin, _ = admin
    _, carol_uid = carol
    r = s_admin.post(f"{BASE_URL}/api/admin/wallet/adjust",
                     json={"user_id": carol_uid, "amount": -2.0, "notes": "debit test"},
                     timeout=10)
    assert r.status_code == 200, r.text

    async def check():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            row = await conn.fetchrow(
                "SELECT type FROM transactions WHERE tx_id = $1",
                r.json()["tx_id"],
            )
            assert row["type"] == "admin_debit"
        finally:
            await conn.close()
    asyncio.get_event_loop().run_until_complete(check())


def test_credit_unknown_user_404(admin):
    s_admin, _ = admin
    r = s_admin.post(f"{BASE_URL}/api/admin/wallet/adjust",
                     json={"user_id": f"user_{uuid.uuid4().hex[:10]}", "amount": 1},
                     timeout=10)
    assert r.status_code == 404


# ─── 2. Password reset ──────────────────────────────────────────────────────

def test_reset_pwd_requires_admin(bob):
    s_bob, bob_uid = bob
    r = s_bob.post(f"{BASE_URL}/api/admin/users/{bob_uid}/reset-password",
                   json={"new_password": "NewPassword123!"}, timeout=10)
    assert r.status_code in (401, 403)


def test_reset_pwd_too_short(admin, carol):
    s_admin, _ = admin
    _, carol_uid = carol
    r = s_admin.post(f"{BASE_URL}/api/admin/users/{carol_uid}/reset-password",
                     json={"new_password": "short"}, timeout=10)
    assert r.status_code == 400


def test_reset_pwd_happy_path_invalidates_sessions(admin):
    """Log in as Carol → admin resets her pw → Carol's cookie is dead (sessions wiped),
    old pw fails on login, new pw works, then restore the known pw at the end."""
    s_admin, admin_uid = admin
    # Clear login_attempts so the earlier tests' login churn doesn't trip
    # brute-force protection inside this test.
    async def clear_attempts():
        conn = await asyncpg.connect(DATABASE_URL)
        try: await conn.execute("DELETE FROM login_attempts")
        finally: await conn.close()
    asyncio.get_event_loop().run_until_complete(clear_attempts())

    # Fresh Carol session
    carol_s, carol_uid = _login(CAROL)
    # Sanity-check her cookie is valid
    ok = carol_s.get(f"{BASE_URL}/api/auth/me", timeout=10)
    assert ok.status_code == 200, ok.text

    new_pw = f"Reset_{uuid.uuid4().hex[:8]}!"
    r = s_admin.post(f"{BASE_URL}/api/admin/users/{carol_uid}/reset-password",
                     json={"new_password": new_pw}, timeout=10)
    assert r.status_code == 200, r.text

    # ── Force-logout effect: Carol's cookie must no longer work ──
    dead = carol_s.get(f"{BASE_URL}/api/auth/me", timeout=10)
    assert dead.status_code in (401, 403), \
        f"Session should be invalidated, got {dead.status_code}"

    # ── Old password must fail ──
    s = requests.Session()
    r_old = s.post(f"{BASE_URL}/api/auth/login",
                   json={"email": CAROL["email"], "password": CAROL["password"]},
                   timeout=10)
    assert r_old.status_code == 401

    # ── New password must work ──
    s2 = requests.Session()
    r_new = s2.post(f"{BASE_URL}/api/auth/login",
                    json={"email": CAROL["email"], "password": new_pw}, timeout=10)
    assert r_new.status_code == 200

    # ── Restore original pw so subsequent suites keep passing ──
    r_restore = s_admin.post(f"{BASE_URL}/api/admin/users/{carol_uid}/reset-password",
                             json={"new_password": CAROL["password"]}, timeout=10)
    assert r_restore.status_code == 200

    # ── Audit log exists ──
    async def check_audit():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            row = await conn.fetchrow(
                "SELECT details::text FROM audit_logs "
                "WHERE user_id = $1 AND action = 'admin_reset_password' "
                "ORDER BY created_at DESC LIMIT 1",
                admin_uid,
            )
            assert row is not None
            assert carol_uid in (row["details"] or "")
        finally:
            await conn.close()
    asyncio.get_event_loop().run_until_complete(check_audit())
