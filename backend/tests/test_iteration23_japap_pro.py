"""
Iteration 23 — JAPAP PRO subscriptions tests
Covers: /api/pro/plans (quotes/discounts/trial state), subscribe (trial/paid/errors),
status, cancel, admin CRUD plans, grant/revoke/extend, stats, expire-now,
admin settings toggles affecting pro (pro_enabled, trial, durations, discounts),
and feed smart-sort +0.25 is_pro bonus.
"""
import os
import time
import pytest
import requests
from decimal import Decimal

def _load_base_url():
    v = os.environ.get("REACT_APP_BACKEND_URL", "").strip()
    if v:
        return v.rstrip("/")
    # Fallback: read from frontend/.env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
    return ""


BASE_URL = _load_base_url()
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
BOB_EMAIL = "bob@japap.com"
BOB_PASSWORD = "Test1234!"
ALICE_EMAIL = "alice@japap.com"
ALICE_PASSWORD = "Test1234!"


# ---------- Auth helpers ----------
def _login(email, password):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"Login failed {email}: {r.status_code} {r.text[:150]}")
    return r.json()["access_token"], r.json().get("user") or {}


@pytest.fixture(scope="session")
def admin_token():
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)[0]


@pytest.fixture(scope="session")
def bob_token():
    return _login(BOB_EMAIL, BOB_PASSWORD)[0]


@pytest.fixture(scope="session")
def alice_token():
    return _login(ALICE_EMAIL, ALICE_PASSWORD)[0]


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _user_id_of(admin_token, email):
    r = requests.get(f"{BASE_URL}/api/admin/users", headers=_h(admin_token),
                     params={"search": email, "limit": 5}, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    users = data.get("users") or data.get("items") or (data if isinstance(data, list) else [])
    for u in users:
        if u.get("email") == email:
            return u.get("user_id") or u.get("id")
    pytest.skip(f"user_id not found for {email}")


@pytest.fixture(scope="session")
def bob_user_id(admin_token):
    return _user_id_of(admin_token, BOB_EMAIL)


@pytest.fixture(scope="session")
def alice_user_id(admin_token):
    return _user_id_of(admin_token, ALICE_EMAIL)


# ---------- Reset subs state before paid/trial tests ----------
@pytest.fixture(scope="session", autouse=True)
def _reset_users_subs(admin_token, bob_user_id, alice_user_id):
    """Revoke any active subs for Bob & Alice so tests start clean, and
    delete prior trial history for Alice (test isolation only - local DB)."""
    for uid in (bob_user_id, alice_user_id):
        requests.post(f"{BASE_URL}/api/admin/pro/revoke/{uid}", headers=_h(admin_token), timeout=10)
    # Best-effort local SQL wipe so trial-once tests are re-runnable
    import subprocess
    try:
        subprocess.run(
            ["sudo", "-u", "postgres", "psql", "japap_messenger", "-c",
             f"DELETE FROM subscriptions WHERE user_id IN ('{bob_user_id}','{alice_user_id}');"
             f"UPDATE users SET is_pro=FALSE, pro_type=0, pro_expires_at=NULL "
             f"WHERE user_id IN ('{bob_user_id}','{alice_user_id}');"],
            timeout=10, capture_output=True, check=False)
    except Exception:
        pass
    yield


# ============== Settings helpers ==============
def _set_settings(admin_token, **kwargs):
    r = requests.put(f"{BASE_URL}/api/admin/settings", headers=_h(admin_token), json={"settings": kwargs}, timeout=15)
    assert r.status_code == 200, f"settings update failed: {r.status_code} {r.text[:200]}"
    return r.json()


@pytest.fixture(scope="session", autouse=True)
def _ensure_pro_defaults(admin_token):
    """Ensure baseline pro settings across tests."""
    _set_settings(admin_token,
                  pro_enabled=True,
                  pro_trial_enabled=True,
                  pro_trial_days=7,
                  pro_trial_plans="all",
                  pro_duration_1m_enabled=True,
                  pro_duration_3m_enabled=True,
                  pro_duration_12m_enabled=True,
                  pro_discount_3m_pct=5,
                  pro_discount_12m_pct=25)
    yield
    # restore defaults
    _set_settings(admin_token,
                  pro_enabled=True, pro_trial_enabled=True,
                  pro_duration_1m_enabled=True, pro_duration_3m_enabled=True, pro_duration_12m_enabled=True,
                  pro_discount_3m_pct=5, pro_discount_12m_pct=25)


# ============== Plans & quotes ==============
class TestPlansEndpoint:
    def test_plans_has_three_seeded(self, alice_token):
        r = requests.get(f"{BASE_URL}/api/pro/plans", headers=_h(alice_token), timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["pro_enabled"] is True
        ids = {p["plan_id"] for p in data["plans"]}
        assert {"starter", "creator", "business"}.issubset(ids)

    def test_quote_math_12m_25pct(self, alice_token):
        r = requests.get(f"{BASE_URL}/api/pro/plans", headers=_h(alice_token), timeout=15)
        creator = next(p for p in r.json()["plans"] if p["plan_id"] == "creator")
        q12 = creator["quotes"]["12m"]
        # creator = $10, 12m × 25% default
        assert q12["months"] == 12
        assert Decimal(q12["subtotal_usd"]) == Decimal("120.00")
        assert q12["discount_pct"] == 25
        assert Decimal(q12["discount_usd"]) == Decimal("30.00")
        assert Decimal(q12["total_usd"]) == Decimal("90.00")

    def test_quote_math_3m_5pct(self, alice_token):
        r = requests.get(f"{BASE_URL}/api/pro/plans", headers=_h(alice_token), timeout=15)
        starter = next(p for p in r.json()["plans"] if p["plan_id"] == "starter")
        q3 = starter["quotes"]["3m"]
        assert Decimal(q3["subtotal_usd"]) == Decimal("15.00")
        assert q3["discount_pct"] == 5
        assert Decimal(q3["discount_usd"]) == Decimal("0.75")
        assert Decimal(q3["total_usd"]) == Decimal("14.25")

    def test_trial_state_exposed(self, alice_token):
        r = requests.get(f"{BASE_URL}/api/pro/plans", headers=_h(alice_token), timeout=15)
        assert "trial" in r.json()
        t = r.json()["trial"]
        assert "enabled" in t and "days" in t and "already_used" in t


# ============== Subscribe flow ==============
class TestSubscribe:
    def test_plan_not_found(self, alice_token):
        r = requests.post(f"{BASE_URL}/api/pro/subscribe", headers=_h(alice_token),
                          json={"plan_id": "nonexistent", "duration": "1m"}, timeout=15)
        assert r.status_code == 404

    def test_trial_alice_starter(self, alice_token):
        r = requests.post(f"{BASE_URL}/api/pro/subscribe", headers=_h(alice_token),
                          json={"plan_id": "starter", "use_trial": True}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["is_trial"] is True
        assert Decimal(data["paid_usd"]) == Decimal("0")

    def test_duplicate_while_active_400(self, alice_token):
        r = requests.post(f"{BASE_URL}/api/pro/subscribe", headers=_h(alice_token),
                          json={"plan_id": "creator", "duration": "1m"}, timeout=15)
        assert r.status_code == 400

    def test_status_returns_pro(self, alice_token):
        r = requests.get(f"{BASE_URL}/api/pro/status", headers=_h(alice_token), timeout=15)
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["is_pro"] is True
        assert s["plan_id"] == "starter"
        assert isinstance(s["features"], list)
        assert isinstance(s["limits"], dict)
        assert s["is_trial"] is True
        assert "days_remaining" in s

    def test_cancel_immediate(self, alice_token):
        r = requests.post(f"{BASE_URL}/api/pro/cancel", headers=_h(alice_token),
                          json={"immediate": True}, timeout=15)
        assert r.status_code == 200, r.text
        # verify status
        r2 = requests.get(f"{BASE_URL}/api/pro/status", headers=_h(alice_token), timeout=15)
        assert r2.status_code == 200
        assert r2.json()["is_pro"] is False

    def test_trial_already_used_400(self, alice_token):
        # Alice already trialed above — should now be rejected
        r = requests.post(f"{BASE_URL}/api/pro/subscribe", headers=_h(alice_token),
                          json={"plan_id": "creator", "use_trial": True}, timeout=15)
        assert r.status_code == 400

    def test_paid_12m_bob_debits_wallet(self, bob_token, admin_token, bob_user_id):
        # Ensure Bob wallet has enough funds (top up via admin if needed)
        # First fetch Bob wallet
        rw = requests.get(f"{BASE_URL}/api/wallet/balance", headers=_h(bob_token), timeout=15)
        assert rw.status_code == 200, rw.text
        w = rw.json()
        currency = (w.get("currency") or "USD").upper()
        balance = Decimal(str(w.get("balance", 0)))
        # Fetch rate
        rr = requests.get(f"{BASE_URL}/api/currency/rates", timeout=15)
        rate = Decimal("1")
        if rr.status_code == 200:
            rates = rr.json().get("rates") or rr.json()
            if isinstance(rates, list):
                for rec in rates:
                    if rec.get("code") == currency:
                        rate = Decimal(str(rec.get("rate_vs_usd", 1)))
            elif isinstance(rates, dict) and currency in rates:
                rate = Decimal(str(rates[currency]))
        needed = Decimal("90.00") * rate
        if balance < needed:
            # top-up via admin adjust
            top = needed - balance + Decimal("10")
            ra = requests.post(f"{BASE_URL}/api/admin/wallet/adjust", headers=_h(admin_token),
                               json={"user_id": bob_user_id, "amount": float(top),
                                     "notes": "test prep pro paid 12m"}, timeout=15)
            # ok if endpoint exists; otherwise just try subscribe and skip
            if ra.status_code not in (200, 201):
                pytest.skip(f"cannot top up Bob ({ra.status_code}); skipping paid test")

        r = requests.post(f"{BASE_URL}/api/pro/subscribe", headers=_h(bob_token),
                          json={"plan_id": "creator", "duration": "12m"}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["is_trial"] is False
        assert data["duration"] == "12m"
        assert data["discount_pct"] == 25
        assert Decimal(data["paid_usd"]) == Decimal("90.00")
        assert Decimal(data["original_usd"]) == Decimal("120.00")

    def test_status_bob_paid(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/pro/status", headers=_h(bob_token), timeout=15)
        assert r.status_code == 200
        s = r.json()
        assert s["is_pro"] is True
        assert s["plan_id"] == "creator"
        assert s["is_trial"] is False
        assert s["days_remaining"] >= 360

    def test_cancel_at_period_end(self, bob_token):
        r = requests.post(f"{BASE_URL}/api/pro/cancel", headers=_h(bob_token),
                          json={"immediate": False}, timeout=15)
        assert r.status_code == 200, r.text
        r2 = requests.get(f"{BASE_URL}/api/pro/status", headers=_h(bob_token), timeout=15)
        assert r2.status_code == 200
        assert r2.json()["is_pro"] is True  # still active until period end
        assert r2.json()["cancel_at_period_end"] is True


# ============== Admin toggles ==============
class TestAdminToggles:
    def test_pro_disabled_returns_503(self, admin_token, alice_token):
        _set_settings(admin_token, pro_enabled=False)
        try:
            r = requests.post(f"{BASE_URL}/api/pro/subscribe", headers=_h(alice_token),
                              json={"plan_id": "starter", "duration": "1m"}, timeout=15)
            assert r.status_code == 503
        finally:
            _set_settings(admin_token, pro_enabled=True)

    def test_trial_disabled_400(self, admin_token, alice_token):
        _set_settings(admin_token, pro_trial_enabled=False)
        try:
            r = requests.post(f"{BASE_URL}/api/pro/subscribe", headers=_h(alice_token),
                              json={"plan_id": "starter", "use_trial": True}, timeout=15)
            assert r.status_code == 400
        finally:
            _set_settings(admin_token, pro_trial_enabled=True)

    def test_discount_12m_50pct(self, admin_token, alice_token):
        _set_settings(admin_token, pro_discount_12m_pct=50)
        try:
            r = requests.get(f"{BASE_URL}/api/pro/plans", headers=_h(alice_token), timeout=15)
            creator = next(p for p in r.json()["plans"] if p["plan_id"] == "creator")
            q = creator["quotes"]["12m"]
            assert q["discount_pct"] == 50
            assert Decimal(q["total_usd"]) == Decimal("60.00")
        finally:
            _set_settings(admin_token, pro_discount_12m_pct=25)

    def test_duration_3m_disabled(self, admin_token, alice_token):
        _set_settings(admin_token, pro_duration_3m_enabled=False)
        try:
            r = requests.post(f"{BASE_URL}/api/pro/subscribe", headers=_h(alice_token),
                              json={"plan_id": "starter", "duration": "3m"}, timeout=15)
            assert r.status_code == 400
        finally:
            _set_settings(admin_token, pro_duration_3m_enabled=True)


# ============== Admin plans CRUD ==============
class TestAdminPlans:
    def test_list(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/pro/plans", headers=_h(admin_token), timeout=15)
        assert r.status_code == 200, r.text
        plans = r.json()
        assert len(plans) >= 3

    def test_update_price(self, admin_token):
        r = requests.put(f"{BASE_URL}/api/admin/pro/plans/starter", headers=_h(admin_token),
                         json={"plan_id": "starter", "price_usd": 5.50}, timeout=15)
        assert r.status_code == 200, r.text
        # verify
        r2 = requests.get(f"{BASE_URL}/api/admin/pro/plans", headers=_h(admin_token), timeout=15)
        starter = next(p for p in r2.json() if p["plan_id"] == "starter")
        assert Decimal(starter["price_usd"]) == Decimal("5.50")
        # restore
        requests.put(f"{BASE_URL}/api/admin/pro/plans/starter", headers=_h(admin_token),
                     json={"plan_id": "starter", "price_usd": 5.00}, timeout=15)

    def test_deactivate_blocks_subscribe(self, admin_token, alice_token):
        # First make sure Alice has no active sub
        aid = _user_id_of(admin_token, ALICE_EMAIL)
        requests.post(f"{BASE_URL}/api/admin/pro/revoke/{aid}", headers=_h(admin_token), timeout=10)

        requests.put(f"{BASE_URL}/api/admin/pro/plans/business", headers=_h(admin_token),
                     json={"plan_id": "business", "is_active": False}, timeout=15)
        try:
            r = requests.post(f"{BASE_URL}/api/pro/subscribe", headers=_h(alice_token),
                              json={"plan_id": "business", "duration": "1m"}, timeout=15)
            assert r.status_code == 404
        finally:
            requests.put(f"{BASE_URL}/api/admin/pro/plans/business", headers=_h(admin_token),
                         json={"plan_id": "business", "is_active": True}, timeout=15)


# ============== Admin subscribers / grant / revoke / extend / stats ==============
class TestAdminLifecycle:
    def test_grant_alice(self, admin_token, alice_user_id):
        # ensure clean
        requests.post(f"{BASE_URL}/api/admin/pro/revoke/{alice_user_id}", headers=_h(admin_token), timeout=10)
        r = requests.post(f"{BASE_URL}/api/admin/pro/grant", headers=_h(admin_token),
                          json={"user_id": alice_user_id, "plan_id": "business", "days": 60, "note": "test"},
                          timeout=15)
        assert r.status_code == 200, r.text

    def test_subscribers_filter(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/pro/subscribers",
                         headers=_h(admin_token),
                         params={"plan_id": "business", "status": "active", "page": 1, "limit": 20},
                         timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "subscribers" in d and "total" in d and "page" in d

    def test_extend(self, admin_token, alice_user_id):
        r = requests.post(f"{BASE_URL}/api/admin/pro/extend/{alice_user_id}",
                          headers=_h(admin_token), json={"days": 10}, timeout=15)
        assert r.status_code == 200, r.text

    def test_revoke(self, admin_token, alice_user_id):
        r = requests.post(f"{BASE_URL}/api/admin/pro/revoke/{alice_user_id}",
                          headers=_h(admin_token), timeout=15)
        assert r.status_code == 200

    def test_stats_shape(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/pro/stats", headers=_h(admin_token), timeout=15)
        assert r.status_code == 200, r.text
        s = r.json()
        for k in ("active_total", "active_by_plan", "revenue_30d_usd", "conversion_pct"):
            assert k in s

    def test_expire_now(self, admin_token):
        r = requests.post(f"{BASE_URL}/api/admin/pro/expire-now", headers=_h(admin_token), timeout=15)
        assert r.status_code == 200
        assert "expired_count" in r.json()


# ============== Feed boost ==============
class TestFeedBoost:
    def test_smart_feed_returns_score(self, bob_token, admin_token, bob_user_id):
        # Ensure Bob is pro via grant (fast)
        requests.post(f"{BASE_URL}/api/admin/pro/revoke/{bob_user_id}", headers=_h(admin_token), timeout=10)
        requests.post(f"{BASE_URL}/api/admin/pro/grant", headers=_h(admin_token),
                      json={"user_id": bob_user_id, "plan_id": "creator", "days": 7}, timeout=15)
        r = requests.get(f"{BASE_URL}/api/feed/posts", headers=_h(bob_token),
                         params={"sort": "smart", "limit": 20}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        posts = data.get("posts") or []
        if not posts:
            pytest.skip("No posts in feed")
        # find a Bob post and verify score field exists
        assert any("score" in p for p in posts), "smart sort should return 'score' on posts"
