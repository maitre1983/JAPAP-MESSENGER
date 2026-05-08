"""
Backend tests for Referral Program & WebRTC Call History APIs.
JAPAP Iteration 11 — Referrals + Calls
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"

# ---------- helpers ----------
def login(session, email, password):
    r = session.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text}"
    return r.json()


def register(session, email, password, first_name="Test", last_name="User", referral_code=""):
    payload = {
        "email": email, "password": password,
        "first_name": first_name, "last_name": last_name,
        "terms_accepted": True, "referral_code": referral_code,
    }
    return session.post(f"{BASE_URL}/api/auth/register", json=payload)


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    login(s, ADMIN_EMAIL, ADMIN_PASSWORD)
    return s


@pytest.fixture(scope="module")
def admin_user(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 200
    return r.json()


# ============================================================
# REFERRAL TESTS
# ============================================================
class TestReferralAPI:
    def test_referrals_me(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/referrals/me")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "referral_code" in data and len(data["referral_code"]) >= 6
        assert "stats" in data
        for k in ["pending", "active", "rewarded", "total"]:
            assert k in data["stats"]
        assert "tiers" in data and len(data["tiers"]) == 2
        assert data["tiers"][0]["count"] == 3
        assert data["tiers"][1]["count"] == 10
        assert "can_claim_tier_1" in data and "can_claim_tier_2" in data

    def test_referrals_list(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/referrals/list")
        assert r.status_code == 200, r.text
        items = r.json()
        assert isinstance(items, list)
        if items:
            it = items[0]
            assert "status" in it and "friend" in it
            assert "email_masked" in it["friend"]
            # email should be masked (contain ***@)
            if it["friend"]["email_masked"]:
                assert "***@" in it["friend"]["email_masked"]

    def test_validate_code_valid(self, admin_session, admin_user):
        # Get admin's code first
        me = admin_session.get(f"{BASE_URL}/api/referrals/me").json()
        code = me["referral_code"]
        r = requests.post(f"{BASE_URL}/api/referrals/validate-code", json={"code": code})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["valid"] is True
        assert "referrer_name" in d

    def test_validate_code_invalid(self):
        r = requests.post(f"{BASE_URL}/api/referrals/validate-code", json={"code": "ZZZZZZZZ"})
        assert r.status_code == 404

    def test_validate_code_empty(self):
        r = requests.post(f"{BASE_URL}/api/referrals/validate-code", json={"code": ""})
        assert r.status_code == 400

    def test_self_referral_rejected(self, admin_session):
        me = admin_session.get(f"{BASE_URL}/api/referrals/me").json()
        code = me["referral_code"]
        r = admin_session.post(f"{BASE_URL}/api/referrals/apply", json={"code": code})
        # Either user already referred (400) or self-ref (400) — both OK as long as != 200
        assert r.status_code == 400, f"Self-ref should be rejected, got {r.status_code} {r.text}"

    def test_leaderboard(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/referrals/leaderboard")
        assert r.status_code == 200, r.text
        items = r.json()
        assert isinstance(items, list)
        assert len(items) <= 10
        if items:
            assert "rank" in items[0] and "active_count" in items[0]

    def test_register_with_referral_code(self, admin_session):
        """New user registers using admin's referral code → referral row created with status=pending."""
        me = admin_session.get(f"{BASE_URL}/api/referrals/me").json()
        admin_code = me["referral_code"]

        ts = int(time.time() * 1000)
        new_email = f"testref_pytest_{ts}@japap.com"
        sess = requests.Session()
        r = register(sess, new_email, "Test1234!", first_name="Pytest", referral_code=admin_code)
        assert r.status_code == 200, f"register failed: {r.text}"
        new_user = r.json()["user"]
        assert new_user["email"] == new_email

        # Admin's referral list should now contain this user with status pending
        ref_list = admin_session.get(f"{BASE_URL}/api/referrals/list").json()
        emails_masked = [it["friend"]["email_masked"] for it in ref_list]
        prefix = new_email[:2] + "***@"
        assert any(em.startswith(prefix) for em in emails_masked), \
            f"New referral not present in admin list. Got {emails_masked[:5]}"

        # Save for activation test
        TestReferralAPI._new_user_email = new_email
        TestReferralAPI._new_user_session = sess
        TestReferralAPI._new_user_id = new_user["user_id"]

    def test_activation_via_post(self, admin_session):
        """When a referred user creates a post, their referral becomes 'active'."""
        sess = getattr(TestReferralAPI, "_new_user_session", None)
        if not sess:
            pytest.skip("requires test_register_with_referral_code to have run")

        # Create a post as the new user
        r = sess.post(f"{BASE_URL}/api/feed/posts", json={"text": "TEST_referral_activation post"})
        assert r.status_code in (200, 201), f"post creation failed: {r.status_code} {r.text}"

        # Allow async activation to settle
        time.sleep(1.5)

        # Check the referral status flipped to active or rewarded
        ref_list = admin_session.get(f"{BASE_URL}/api/referrals/list").json()
        new_email = TestReferralAPI._new_user_email
        prefix = new_email[:2] + "***@"
        match = next((it for it in ref_list if it["friend"]["email_masked"].startswith(prefix)), None)
        assert match is not None, "referral row vanished"
        assert match["status"] in ("active", "rewarded"), \
            f"Expected activation, got status={match['status']}"

    def test_claim_tier_2_insufficient(self, admin_session):
        """Tier 2 (10 actifs) should fail if active < 10."""
        me = admin_session.get(f"{BASE_URL}/api/referrals/me").json()
        active = me["stats"]["active"]
        if active >= 10:
            pytest.skip("Admin already has 10+ active referrals; cannot test insufficient case")
        r = admin_session.post(f"{BASE_URL}/api/referrals/claim", json={"tier": 2})
        assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text}"
        assert "filleul" in r.json().get("detail", "").lower()

    def test_claim_invalid_tier(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/referrals/claim", json={"tier": 5})
        assert r.status_code == 400


# ============================================================
# CALLS TESTS
# ============================================================
class TestCallsAPI:
    def test_initiate_self_call_rejected(self, admin_session, admin_user):
        r = admin_session.post(f"{BASE_URL}/api/calls/initiate",
                               json={"callee_id": admin_user["user_id"], "type": "audio"})
        assert r.status_code == 400, r.text

    def test_initiate_invalid_type(self, admin_session):
        # Need a real callee — find one
        r = admin_session.get(f"{BASE_URL}/api/users/list?limit=10")
        # Fallback: try search any other user via referrals/list
        callee_id = None
        if r.status_code == 200:
            users = r.json()
            for u in (users if isinstance(users, list) else users.get("users", [])):
                uid = u.get("user_id")
                if uid and uid != "admin":
                    callee_id = uid
                    break
        if not callee_id:
            ref_list = admin_session.get(f"{BASE_URL}/api/referrals/list").json()
            if ref_list:
                callee_id = ref_list[0]["friend"]["user_id"]
        if not callee_id:
            pytest.skip("no callee available")
        r = admin_session.post(f"{BASE_URL}/api/calls/initiate",
                               json={"callee_id": callee_id, "type": "telepathic"})
        assert r.status_code == 400

    def test_initiate_unknown_callee(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/calls/initiate",
                               json={"callee_id": "user_does_not_exist_xyz", "type": "audio"})
        assert r.status_code == 404, r.text

    def test_initiate_and_end_and_history(self, admin_session):
        # find a real callee from referrals
        ref_list = admin_session.get(f"{BASE_URL}/api/referrals/list").json()
        if not ref_list:
            pytest.skip("no referred user to call")
        callee_id = ref_list[0]["friend"]["user_id"]

        r = admin_session.post(f"{BASE_URL}/api/calls/initiate",
                               json={"callee_id": callee_id, "type": "video"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "call_id" in data
        assert data["type"] == "video"
        assert data["callee"]["user_id"] == callee_id
        call_id = data["call_id"]

        # End the call
        r = admin_session.post(f"{BASE_URL}/api/calls/end",
                               json={"call_id": call_id, "duration": 42, "status": "ended"})
        assert r.status_code == 200, r.text
        ed = r.json()
        assert ed["status"] == "ended"
        assert ed["duration"] == 42

        # History should contain it
        r = admin_session.get(f"{BASE_URL}/api/calls/history?limit=20")
        assert r.status_code == 200, r.text
        history = r.json()
        assert isinstance(history, list)
        match = next((c for c in history if c["call_id"] == call_id), None)
        assert match is not None, "call missing from history"
        assert match["direction"] == "outgoing"
        assert match["status"] == "ended"
        assert match["duration"] == 42

    def test_end_unknown_call(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/calls/end",
                               json={"call_id": "call_unknown_abc", "duration": 0, "status": "ended"})
        assert r.status_code == 404


# ============================================================
# AUTH REGRESSION (referral_code optional)
# ============================================================
class TestAuthRegression:
    def test_login_admin(self):
        s = requests.Session()
        r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        assert "user" in r.json()

    def test_me(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_register_no_referral(self):
        ts = int(time.time() * 1000)
        sess = requests.Session()
        r = register(sess, f"reg_noref_{ts}@japap.com", "Test1234!", first_name="NoRef")
        assert r.status_code == 200, r.text
