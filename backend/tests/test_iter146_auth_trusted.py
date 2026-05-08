"""iter146 — Auth bug fix + Trusted Device + reset/refresh tests.

Covers:
  - MIGRATION_RESET_REQUIRED only triggers for legacy users (3 invariants).
  - Trusted device flow: 1st login untrusted, 2nd same UA → trusted (90d),
    different UA → new fingerprint, untrusted.
  - GET /api/auth/devices and POST /api/auth/devices/untrust.
  - reset-password untrusts all devices.
  - /api/auth/refresh issues 90d cookie when device trusted, 7d otherwise.
"""
import os
import re
import asyncio
import pytest
import requests
import asyncpg
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
DATABASE_URL = os.environ["DATABASE_URL"]
BYPASS = "JAPAP_E2E_BYPASS_2026"

BOB = {"email": "bob@japap.com", "password": "Test1234!"}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!"}
LEGACY_EMAIL = "baunestali@gmail.com"

# Stable external client IP to simulate a real user across the test run.
# Needed because when calling the backend from inside the cluster (or via a
# path that doesn't preserve XFF), request.client.host rotates through pod
# IPs and the trusted-device fingerprint (sha256(ip+ua)) would change per
# request. The /api/auth/login handler now reads _client_ip(request) which
# prefers cf-connecting-ip → x-forwarded-for → client.host.
STABLE_XFF = "203.0.113.42"


# ── helpers ─────────────────────────────────────────────────────────────
async def _db():
    return await asyncpg.connect(DATABASE_URL)


async def _reset_trusted(user_id: str):
    c = await _db()
    try:
        await c.execute("DELETE FROM trusted_devices WHERE user_id=$1", user_id)
    finally:
        await c.close()


async def _get_user_id(email: str) -> str:
    c = await _db()
    try:
        r = await c.fetchrow("SELECT user_id FROM users WHERE email=$1", email)
        return r["user_id"] if r else None
    finally:
        await c.close()


def _login(email, password, ua="TestBrowser/1.0", session=None):
    s = session or requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password,
              "captcha_id": BYPASS, "captcha_answer": "0"},
        headers={"User-Agent": ua, "Content-Type": "application/json",
                 "X-Forwarded-For": STABLE_XFF},
        timeout=20,
    )
    return r, s


# ── 1. Auth bug — new users never get MIGRATION_RESET_REQUIRED ──────────
class TestMigrationResetGuard:
    def test_alice_normal_login_200(self):
        r, _ = _login(ALICE["email"], ALICE["password"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert "user" in body
        assert body["user"]["email"] == ALICE["email"]
        assert "device" in body
        assert "is_trusted" in body["device"]

    def test_pathological_flags_does_not_trigger_migration(self):
        """Flip is_legacy_account=TRUE + migration_completed=FALSE on a NEW
        user (legacy_id IS NULL) — login MUST still succeed (no 403)."""
        async def setup_and_test():
            uid = await _get_user_id(ALICE["email"])
            c = await _db()
            try:
                # snapshot original
                row = await c.fetchrow(
                    "SELECT is_legacy_account, migration_completed, migration_pending FROM users WHERE user_id=$1",
                    uid)
                # apply pathological flags
                await c.execute(
                    "UPDATE users SET is_legacy_account=TRUE, migration_completed=FALSE, migration_pending=TRUE WHERE user_id=$1",
                    uid)
                try:
                    r, _ = _login(ALICE["email"], ALICE["password"])
                    assert r.status_code == 200, f"Expected 200 even with pathological flags, got {r.status_code}: {r.text}"
                    assert "MIGRATION_RESET_REQUIRED" not in r.text
                finally:
                    # restore
                    await c.execute(
                        "UPDATE users SET is_legacy_account=$2, migration_completed=$3, migration_pending=$4 WHERE user_id=$1",
                        uid,
                        row["is_legacy_account"], row["migration_completed"], row["migration_pending"])
            finally:
                await c.close()
        asyncio.run(setup_and_test())

    def test_legacy_user_returns_403_migration(self):
        r = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": LEGACY_EMAIL, "password": "anything",
                  "captcha_id": BYPASS, "captcha_answer": "0"},
            headers={"User-Agent": "TestBrowser/1.0",
                     "X-Forwarded-For": STABLE_XFF},
            timeout=20,
        )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
        body = r.json()
        detail = body.get("detail", "")
        assert "MIGRATION_RESET_REQUIRED" in detail, detail


# ── 2. Trusted device flow ──────────────────────────────────────────────
class TestTrustedDevice:
    UA1 = "TrustedDeviceTest/UA1"
    UA2 = "TrustedDeviceTest/UA2"

    def setup_method(self, _m):
        async def reset():
            uid = await _get_user_id(BOB["email"])
            await _reset_trusted(uid)
        asyncio.run(reset())

    def test_trusted_progression(self):
        # 1st login same UA
        r1, _ = _login(BOB["email"], BOB["password"], ua=self.UA1)
        assert r1.status_code == 200, r1.text
        d1 = r1.json()["device"]
        assert d1["is_trusted"] is False, d1
        assert d1["successful_logins_count"] == 1, d1
        assert d1["refresh_ttl_days"] == 7, d1
        assert d1["newly_trusted"] is False

        # 2nd login same UA → trusted, newly_trusted=True, ttl=90
        r2, _ = _login(BOB["email"], BOB["password"], ua=self.UA1)
        assert r2.status_code == 200, r2.text
        d2 = r2.json()["device"]
        assert d2["is_trusted"] is True, d2
        assert d2["newly_trusted"] is True, d2
        assert d2["successful_logins_count"] == 2, d2
        assert d2["refresh_ttl_days"] == 90, d2

        # 3rd same UA → still trusted, newly_trusted=False
        r3, _ = _login(BOB["email"], BOB["password"], ua=self.UA1)
        assert r3.status_code == 200
        d3 = r3.json()["device"]
        assert d3["is_trusted"] is True
        assert d3["newly_trusted"] is False
        assert d3["successful_logins_count"] == 3
        assert d3["refresh_ttl_days"] == 90

        # Different UA → new fingerprint, count=1, untrusted
        r4, _ = _login(BOB["email"], BOB["password"], ua=self.UA2)
        assert r4.status_code == 200
        d4 = r4.json()["device"]
        assert d4["is_trusted"] is False
        assert d4["successful_logins_count"] == 1
        assert d4["refresh_ttl_days"] == 7


# ── 3. /api/auth/devices listing & untrust ──────────────────────────────
class TestDevicesEndpoints:
    UA = "DevicesEndpointTest/UA"

    def setup_method(self, _m):
        async def reset():
            uid = await _get_user_id(BOB["email"])
            await _reset_trusted(uid)
        asyncio.run(reset())

    def test_list_unauth_401(self):
        r = requests.get(f"{BASE_URL}/api/auth/devices", timeout=10)
        assert r.status_code == 401

    def test_list_with_session_and_untrust(self):
        # Login twice → device should be trusted
        r1, s = _login(BOB["email"], BOB["password"], ua=self.UA)
        assert r1.status_code == 200
        r2, s = _login(BOB["email"], BOB["password"], ua=self.UA, session=s)
        assert r2.status_code == 200

        # GET /devices
        r = s.get(f"{BASE_URL}/api/auth/devices",
                  headers={"User-Agent": self.UA,
                           "X-Forwarded-For": STABLE_XFF}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "devices" in body
        assert "current_fingerprint" in body
        devices = body["devices"]
        assert len(devices) >= 1
        current = [d for d in devices if d.get("is_current")]
        assert len(current) == 1
        cur = current[0]
        assert cur["is_trusted"] is True
        assert cur["successful_logins_count"] >= 2
        fingerprint = cur["fingerprint"]
        assert fingerprint == body["current_fingerprint"]

        # POST untrust
        u = s.post(
            f"{BASE_URL}/api/auth/devices/untrust",
            json={"fingerprint": fingerprint},
            headers={"User-Agent": self.UA, "Content-Type": "application/json",
                     "X-Requested-With": "XMLHttpRequest",
                     "X-Forwarded-For": STABLE_XFF},
            timeout=10,
        )
        assert u.status_code == 200, u.text
        assert u.json().get("untrusted") is True

        # Verify it's now untrusted, count=0
        r = s.get(f"{BASE_URL}/api/auth/devices",
                  headers={"User-Agent": self.UA,
                           "X-Forwarded-For": STABLE_XFF}, timeout=10)
        body = r.json()
        cur = [d for d in body["devices"] if d["fingerprint"] == fingerprint][0]
        assert cur["is_trusted"] is False
        assert cur["successful_logins_count"] == 0


# ── 4. reset-password untrusts all devices (DB-level simulation) ────────
class TestResetPasswordUntrust:
    UA = "ResetUntrustTest/UA"

    def test_reset_calls_untrust_all(self):
        """Simulate a password reset by inserting a token directly, then POST
        /reset-password and verify trusted_devices.is_trusted=FALSE."""
        async def run():
            import secrets
            from datetime import datetime, timedelta, timezone
            uid = await _get_user_id(BOB["email"])
            await _reset_trusted(uid)

            # Trust the device by 2 logins
            for _ in range(2):
                r, _ = _login(BOB["email"], BOB["password"], ua=self.UA)
                assert r.status_code == 200

            c = await _db()
            try:
                trow = await c.fetchrow(
                    "SELECT is_trusted, successful_logins_count FROM trusted_devices WHERE user_id=$1",
                    uid)
                assert trow["is_trusted"] is True

                # Insert a password reset token directly
                token = secrets.token_urlsafe(32)
                await c.execute(
                    "INSERT INTO password_reset_tokens (token, user_id, expires_at, used) "
                    "VALUES ($1, $2, $3, FALSE)",
                    token, uid,
                    datetime.now(timezone.utc) + timedelta(hours=1),
                )
            finally:
                await c.close()

            # Hit reset endpoint with same password (idempotent on the user)
            r = requests.post(
                f"{BASE_URL}/api/auth/reset-password",
                json={"token": token, "new_password": BOB["password"]},
                headers={"X-Forwarded-For": STABLE_XFF},
                timeout=20,
            )
            assert r.status_code == 200, r.text

            # Verify untrust
            c = await _db()
            try:
                rows = await c.fetch(
                    "SELECT is_trusted, successful_logins_count FROM trusted_devices WHERE user_id=$1",
                    uid)
                for row in rows:
                    assert row["is_trusted"] is False, row
                    assert row["successful_logins_count"] == 0, row
            finally:
                await c.close()

        asyncio.run(run())


# ── 5. /api/auth/refresh — TTL respects trusted state ───────────────────
class TestRefreshTTL:
    UA = "RefreshTTLTest/UA"

    def setup_method(self, _m):
        async def reset():
            uid = await _get_user_id(BOB["email"])
            await _reset_trusted(uid)
        asyncio.run(reset())

    def _refresh_max_age(self, session):
        r = session.post(
            f"{BASE_URL}/api/auth/refresh",
            headers={"User-Agent": self.UA,
                     "X-Requested-With": "XMLHttpRequest",
                     "X-Forwarded-For": STABLE_XFF},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        # Inspect set-cookie for max-age of refresh_token
        set_cookie = r.headers.get("set-cookie", "") + " " + " ".join(
            [v for k, v in r.raw.headers.items() if k.lower() == "set-cookie"]
        )
        # requests merges cookies; iter through raw headers
        max_age = None
        for header in r.raw.headers.getlist("set-cookie") if hasattr(r.raw.headers, "getlist") else [set_cookie]:
            if "refresh_token=" in header:
                m = re.search(r"max-age=(\d+)", header, re.IGNORECASE)
                if m:
                    max_age = int(m.group(1))
        return max_age, r

    def test_untrusted_refresh_ttl_7d(self):
        # Single login → untrusted
        r1, s = _login(BOB["email"], BOB["password"], ua=self.UA)
        assert r1.status_code == 200
        assert r1.json()["device"]["is_trusted"] is False

        max_age, r = self._refresh_max_age(s)
        assert max_age is not None, f"No refresh_token max-age in response headers: {dict(r.headers)}"
        # 7 days = 604800
        assert max_age == 604800, f"Expected 604800 (7d), got {max_age}"

    def test_trusted_refresh_ttl_90d(self):
        # 2 logins → trusted
        r1, s = _login(BOB["email"], BOB["password"], ua=self.UA)
        assert r1.status_code == 200
        r2, s = _login(BOB["email"], BOB["password"], ua=self.UA, session=s)
        assert r2.status_code == 200
        assert r2.json()["device"]["is_trusted"] is True

        max_age, r = self._refresh_max_age(s)
        assert max_age is not None
        # 90 days = 7776000
        assert max_age == 7776000, f"Expected 7776000 (90d), got {max_age}"
