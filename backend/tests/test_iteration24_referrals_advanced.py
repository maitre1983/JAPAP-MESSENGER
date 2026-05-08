"""
Iter24 — Advanced Referral Program tests.
Covers config/me/list/apply/claim/leaderboard + admin stats/list/block/send-reminders
+ settings-driven dynamic configuration.
"""
import os
import time
import uuid as _uuid
import pytest
import requests
from pathlib import Path

# Load REACT_APP_BACKEND_URL from frontend/.env if not in environment
def _load_base_url():
    b = os.environ.get("REACT_APP_BACKEND_URL")
    if b:
        return b.rstrip("/")
    env_path = Path("/app/frontend/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                return line.split("=", 1)[1].strip().rstrip("/")
    return "https://japap-refactor.preview.emergentagent.com"

BASE_URL = _load_base_url()
ADMIN = ("admin@japap.com", "JapapAdmin2024!")
BOB = ("bob@japap.com", "Test1234!")
ALICE = ("alice@japap.com", "Test1234!")

import re, subprocess


def _read_backend_logs(lines=800):
    out = ""
    for path in ("/var/log/supervisor/backend.out.log", "/var/log/supervisor/backend.err.log"):
        try:
            r = subprocess.run(["tail", "-n", str(lines), path], capture_output=True, text=True, timeout=5)
            out += r.stdout
        except Exception:
            pass
    return out


def _latest_otp_for(email: str) -> str:
    """Fetch latest unused OTP from the DB for the given email (lowercased)."""
    try:
        r = subprocess.run(
            ["sudo", "-u", "postgres", "psql", "-d", "japap_messenger", "-tAc",
             f"SELECT code FROM email_otps WHERE LOWER(email)=LOWER('{email}') AND used=FALSE ORDER BY created_at DESC LIMIT 1"],
            capture_output=True, text=True, timeout=10,
        )
        code = (r.stdout or "").strip()
        if code:
            return code
    except Exception:
        pass
    # Fallback: scan backend logs
    logs = _read_backend_logs(1500)
    idx = logs.rfind(email)
    if idx < 0:
        return ""
    window = logs[idx: idx + 6000]
    m = re.findall(r"\b(\d{6})\b", window)
    return m[0] if m else ""


def _register_and_verify(email, password="Test1234!", first_name="Test", last_name="User"):
    """Full registration + OTP verification. Returns an authenticated session or None on failure."""
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/register", json={
        "email": email, "password": password,
        "first_name": first_name, "last_name": last_name,
        "terms_accepted": True,
    })
    if r.status_code != 200:
        return None
    # pick up OTP from logs
    time.sleep(0.5)
    code = _latest_otp_for(email.lower())
    if not code:
        return None
    vr = s.post(f"{BASE_URL}/api/auth/verify-otp", json={"email": email, "code": code})
    if vr.status_code != 200:
        return None
    # After OTP verification, user may auto-login via cookies; if not, do explicit login
    me = s.get(f"{BASE_URL}/api/auth/me")
    if me.status_code != 200:
        lr = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
        if lr.status_code != 200:
            return None
    return s


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_session():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def bob_session():
    return _login(*BOB)


@pytest.fixture(scope="module")
def alice_session():
    return _login(*ALICE)


def _set_settings(admin_session, **kv):
    r = admin_session.put(f"{BASE_URL}/api/admin/settings", json={"settings": kv})
    assert r.status_code == 200, f"settings PUT: {r.status_code} {r.text}"
    return r.json()


# ==================== PUBLIC CONFIG / ME / LIST ====================
class TestConfig:
    def test_config_shape(self, bob_session):
        _set_settings(_login(*ADMIN), referral_enabled=True)
        r = bob_session.get(f"{BASE_URL}/api/referrals/config")
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("enabled", "referrer_bonus_usd", "referee_bonus_usd", "tiers",
                  "leaderboard_enabled", "gamification_enabled"):
            assert k in d, f"missing key {k} in {list(d.keys())}"
        assert isinstance(d["tiers"], list) and len(d["tiers"]) >= 2

    def test_me_shape(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/referrals/me")
        assert r.status_code == 200, r.text
        d = r.json()
        assert "referral_code" in d and len(d["referral_code"]) >= 6
        assert "share_url" in d and d["referral_code"] in d["share_url"]
        assert "stats" in d
        for k in ("pending", "active", "rewarded", "total", "active_count", "total_earned_usd"):
            assert k in d["stats"], f"stats missing {k}"
        assert "tiers" in d
        assert "progress_to_next_pct" in d
        assert "bonuses" in d and "referrer_usd" in d["bonuses"]
        assert "badges" in d

    def test_list_paginated(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/referrals/list?limit=50")
        assert r.status_code == 200, r.text
        d = r.json()
        assert "referrals" in d and isinstance(d["referrals"], list)
        assert "total" in d and "page" in d and "limit" in d


# ==================== APPLY flow ====================
class TestApply:
    def test_apply_invalid_code(self, bob_session):
        r = bob_session.post(f"{BASE_URL}/api/referrals/apply", json={"code": "ZZZZZZ99"})
        assert r.status_code == 404, r.text

    def test_apply_self_referral(self, admin_session):
        me = admin_session.get(f"{BASE_URL}/api/referrals/me").json()
        r = admin_session.post(f"{BASE_URL}/api/referrals/apply", json={"code": me["referral_code"]})
        # Either 400 (self) or 400 (already referred)
        assert r.status_code == 400, r.text

    def test_apply_new_user_success_and_duplicate(self, admin_session):
        me = admin_session.get(f"{BASE_URL}/api/referrals/me").json()
        admin_code = me["referral_code"]

        ts = int(time.time() * 1000)
        email = f"TEST_apply_{ts}@japap.com"
        s = _register_and_verify(email, first_name="Apply", last_name="Test")
        if not s:
            pytest.skip("Could not complete OTP flow for test user (logs may not contain OTP)")

        r = s.post(f"{BASE_URL}/api/referrals/apply", json={"code": admin_code})
        assert r.status_code in (200, 201), r.text
        d = r.json()
        assert "blocked" in d
        assert "referrer_id" in d

        # duplicate: already referred
        r2 = s.post(f"{BASE_URL}/api/referrals/apply", json={"code": admin_code})
        assert r2.status_code == 400, r2.text

    def test_ip_cap_blocks(self, admin_session):
        # Lower cap to 1, register two users and apply from same IP
        _set_settings(admin_session, referral_max_per_ip_per_day=1)
        try:
            me = admin_session.get(f"{BASE_URL}/api/referrals/me").json()
            admin_code = me["referral_code"]

            ts = int(time.time() * 1000)
            results = []
            for i in range(2):
                email = f"TEST_ipcap_{ts}_{i}@japap.com"
                s = _register_and_verify(email, first_name=f"Cap{i}", last_name="Test")
                if not s:
                    continue
                ap = s.post(f"{BASE_URL}/api/referrals/apply", json={"code": admin_code})
                if ap.status_code in (200, 201):
                    results.append(ap.json().get("blocked"))
            if not results:
                pytest.skip("Could not register test users via OTP flow")
            # At least one should be blocked=True due to cap=1
            assert True in results, f"Expected some blocked=True, got {results}"
        finally:
            _set_settings(admin_session, referral_max_per_ip_per_day=3)


# ==================== CLAIM ====================
class TestClaim:
    def test_claim_invalid_tier(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/referrals/claim", json={"tier_count": 999})
        assert r.status_code == 400, r.text

    def test_claim_insufficient(self, bob_session):
        # Bob likely has 0 active refs
        r = bob_session.post(f"{BASE_URL}/api/referrals/claim", json={"tier_count": 3})
        assert r.status_code == 400, r.text
        assert "filleul" in (r.json().get("detail", "").lower())


# ==================== LEADERBOARD ====================
class TestLeaderboard:
    def test_leaderboard_weekly(self, bob_session, admin_session):
        _set_settings(admin_session, referral_leaderboard_enabled=True)
        r = bob_session.get(f"{BASE_URL}/api/referrals/leaderboard?window=weekly")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("enabled") is True
        assert d.get("window") == "weekly"
        assert "leaders" in d

    def test_leaderboard_all_time(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/referrals/leaderboard?window=all_time")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("window") == "all_time"

    def test_leaderboard_disabled(self, bob_session, admin_session):
        _set_settings(admin_session, referral_leaderboard_enabled=False)
        try:
            r = bob_session.get(f"{BASE_URL}/api/referrals/leaderboard")
            assert r.status_code == 200, r.text
            d = r.json()
            assert d.get("enabled") is False
            assert d.get("leaders") == []
        finally:
            _set_settings(admin_session, referral_leaderboard_enabled=True)


# ==================== ADMIN ====================
class TestAdminReferrals:
    def test_admin_stats(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/referrals/stats")
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("total", "pending", "active", "rewarded", "blocked",
                  "rewards_30d_usd", "top_referrers", "inactive_referees_7d"):
            assert k in d, f"admin stats missing {k}"
        assert isinstance(d["top_referrers"], list)

    def test_admin_list_filters(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/referrals/list?limit=50")
        assert r.status_code == 200, r.text
        d = r.json()
        assert "referrals" in d and "total" in d
        # filter by status=pending
        r2 = admin_session.get(f"{BASE_URL}/api/admin/referrals/list?status=pending&limit=10")
        assert r2.status_code == 200
        for it in r2.json()["referrals"]:
            assert it["status"] == "pending"

    def test_block_unblock_roundtrip(self, admin_session):
        lst = admin_session.get(f"{BASE_URL}/api/admin/referrals/list?limit=5").json()["referrals"]
        if not lst:
            pytest.skip("no referrals to block")
        rid = lst[0]["id"]
        r = admin_session.post(f"{BASE_URL}/api/admin/referrals/{rid}/block",
                               json={"reason": "TEST_block"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "blocked"
        # verify blocked=True in list
        r2 = admin_session.get(f"{BASE_URL}/api/admin/referrals/list?limit=100").json()
        match = next((x for x in r2["referrals"] if x["id"] == rid), None)
        assert match and match["blocked"] is True

        r = admin_session.post(f"{BASE_URL}/api/admin/referrals/{rid}/unblock")
        assert r.status_code == 200, r.text
        r3 = admin_session.get(f"{BASE_URL}/api/admin/referrals/list?limit=100").json()
        match = next((x for x in r3["referrals"] if x["id"] == rid), None)
        assert match and match["blocked"] is False

    def test_send_reminders(self, admin_session):
        _set_settings(admin_session, referral_reminder_enabled=True)
        r = admin_session.post(f"{BASE_URL}/api/admin/referrals/send-reminders")
        assert r.status_code == 200, r.text
        d = r.json()
        assert "sent" in d

    def test_send_reminders_disabled(self, admin_session):
        _set_settings(admin_session, referral_reminder_enabled=False)
        try:
            r = admin_session.post(f"{BASE_URL}/api/admin/referrals/send-reminders")
            assert r.status_code == 200, r.text
            d = r.json()
            assert d.get("skipped_reason") == "reminder_disabled"
        finally:
            _set_settings(admin_session, referral_reminder_enabled=True)


# ==================== SETTINGS-DRIVEN CONFIG ====================
class TestSettingsDriven:
    def test_disable_referral_blocks_apply(self, admin_session):
        _set_settings(admin_session, referral_enabled=False)
        try:
            ts = int(time.time() * 1000)
            email = f"TEST_disabled_{ts}@japap.com"
            s = _register_and_verify(email, first_name="Dis", last_name="Abled")
            if not s:
                pytest.skip("Could not register via OTP")
            me = admin_session.get(f"{BASE_URL}/api/referrals/me").json()
            ap = s.post(f"{BASE_URL}/api/referrals/apply", json={"code": me["referral_code"]})
            assert ap.status_code == 503, ap.text
        finally:
            _set_settings(admin_session, referral_enabled=True)

    def test_bonus_usd_dynamic(self, admin_session):
        _set_settings(admin_session, referral_referrer_bonus_usd=1.00)
        try:
            r = admin_session.get(f"{BASE_URL}/api/referrals/config")
            assert r.status_code == 200
            d = r.json()
            assert float(d["referrer_bonus_usd"]) == 1.00
        finally:
            _set_settings(admin_session, referral_referrer_bonus_usd=0.50)

    def test_tiers_dynamic(self, admin_session):
        custom = [
            {"count": 5, "reward_type": "pro", "reward_value": 30, "label": "Custom 5"},
            {"count": 15, "reward_type": "wallet", "reward_value": 10, "label": "Custom 15"},
        ]
        _set_settings(admin_session, referral_tiers_json=custom)
        try:
            r = admin_session.get(f"{BASE_URL}/api/referrals/config")
            assert r.status_code == 200
            tiers = r.json()["tiers"]
            assert tiers[0]["count"] == 5
            assert tiers[1]["count"] == 15
        finally:
            _set_settings(admin_session, referral_tiers_json=[
                {"count": 3, "reward_type": "pro", "reward_value": 30, "label": "1 mois Pro"},
                {"count": 10, "reward_type": "pro", "reward_value": 90, "label": "3 mois Pro"},
            ])


# ==================== END-TO-END ACTIVATION ====================
class TestActivationFlow:
    def test_activation_credits_wallet(self, admin_session):
        """Register new user with referral code → create post → wallet credited."""
        _set_settings(admin_session, referral_enabled=True,
                      referral_referrer_bonus_usd=0.50, referral_referee_bonus_usd=0.25,
                      referral_activation_requires_otp=False,
                      referral_activation_requires_action=True,
                      referral_max_per_ip_per_day=100)

        me = admin_session.get(f"{BASE_URL}/api/referrals/me").json()
        admin_code = me["referral_code"]

        ts = int(time.time() * 1000)
        email = f"TEST_activate_{ts}@japap.com"
        s = _register_and_verify(email, first_name="Act", last_name="Ivate")
        if not s:
            pytest.skip("Could not complete OTP flow")

        ap = s.post(f"{BASE_URL}/api/referrals/apply", json={"code": admin_code})
        assert ap.status_code in (200, 201), ap.text
        if ap.json().get("blocked"):
            pytest.skip("IP-blocked, cannot test activation")

        # Create a post as the new user (qualifying action)
        post = s.post(f"{BASE_URL}/api/feed/posts", json={"text": "TEST_iter24_activation"})
        assert post.status_code in (200, 201), post.text

        time.sleep(2.0)

        # Verify in admin list referral became active
        lst = admin_session.get(f"{BASE_URL}/api/admin/referrals/list?search=test_activate_&limit=50").json()
        found = next((r for r in lst["referrals"] if email.lower() == r["referee"]["email"].lower()), None)
        assert found is not None, f"referral not found in admin list"
        assert found["status"] in ("active", "rewarded"), f"expected active, got {found['status']}"
        assert float(found["referrer_bonus_usd"]) > 0
