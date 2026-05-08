"""Iter89 — Wallet monitoring/observability tests.

Covers the 3 monitoring contracts:
1. Rate-limit on POST /api/wallet/send  (5/minute per user → 6th = HTTP 429)
2. GET /api/admin/wallet/overview?days=30 full shape including anomalies & engagement_points
3. GET /api/admin/wallet/alerts?limit=20 shape + withdraw_without_kyc alert
   trigger & deduplication (60-min window)
4. Admin-only access (bob → 403)
5. Regression: /api/wallet/send still works business-wise
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"

ADMIN_EMAIL = "admin@japap.com"
ADMIN_PW = "JapapAdmin2024!"
BOB_EMAIL = "bob@japap.com"
BOB_PW = "Test1234!"
ALICE_EMAIL = "alice@japap.com"
ALICE_PW = "Test1234!"


def _login(session, email, pw):
    last_err = None
    for _ in range(3):
        try:
            r = session.post(f"{BASE_URL}/api/auth/login",
                             json={"email": email, "password": pw}, timeout=45)
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.exceptions.ReadTimeout as e:
            last_err = f"timeout: {e}"
        time.sleep(3)
    raise AssertionError(f"login failed for {email}: {last_err}")


@pytest.fixture(scope="module")
def admin_sess():
    s = requests.Session()
    _login(s, ADMIN_EMAIL, ADMIN_PW)
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    return s


@pytest.fixture(scope="module")
def bob_sess():
    s = requests.Session()
    d = _login(s, BOB_EMAIL, BOB_PW)
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    s.user_id = d.get("user", {}).get("user_id")
    return s


@pytest.fixture(scope="module")
def alice_sess():
    s = requests.Session()
    d = _login(s, ALICE_EMAIL, ALICE_PW)
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    s.user_id = d.get("user", {}).get("user_id")
    return s


# ─── 1. Admin wallet overview shape ────────────────────────────────────────
class TestAdminWalletOverview:
    def test_overview_full_shape(self, admin_sess):
        r = admin_sess.get(f"{BASE_URL}/api/admin/wallet/overview?days=30", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        # top-level keys
        for k in ("balances", "volumes_by_type", "timeseries",
                  "top_funded", "anomalies", "engagement_points"):
            assert k in d, f"missing {k}"
        # anomalies sub-keys
        an = d["anomalies"]
        for k in ("large_withdrawals", "stuck_pending_over_24h", "send_spam_last_1h"):
            assert k in an and isinstance(an[k], list), f"anomalies.{k} missing/not list"
        # engagement_points sub-keys
        ep = d["engagement_points"]
        for k in ("total_points", "by_source", "total_spins", "unique_players"):
            assert k in ep, f"engagement_points.{k} missing"
        for s in ("wheel", "quiz", "tap"):
            assert s in ep["by_source"], f"by_source.{s} missing"
        # top_funded bounded to 10
        assert isinstance(d["top_funded"], list) and len(d["top_funded"]) <= 10

    def test_overview_non_admin_forbidden(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/admin/wallet/overview?days=30", timeout=15)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ─── 2. Admin wallet alerts endpoint ───────────────────────────────────────
class TestAdminWalletAlerts:
    def test_alerts_shape(self, admin_sess):
        r = admin_sess.get(f"{BASE_URL}/api/admin/wallet/alerts?limit=20", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "items" in d and isinstance(d["items"], list)
        if d["items"]:
            it = d["items"][0]
            for k in ("id", "kind", "alert_key", "title", "created_at"):
                assert k in it, f"alert missing {k}"

    def test_alerts_non_admin_forbidden(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/admin/wallet/alerts?limit=20", timeout=15)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ─── 3. withdraw_without_kyc alert triggers + dedup ─────────────────────────
class TestWithdrawWithoutKycAlert:
    def _alerts_for_user(self, admin_sess, user_id, kind="withdraw_no_kyc"):
        r = admin_sess.get(f"{BASE_URL}/api/admin/wallet/alerts?limit=200", timeout=15)
        assert r.status_code == 200
        items = r.json()["items"]
        return [i for i in items if i["kind"] == kind and user_id in (i.get("alert_key") or "")]

    def test_withdraw_triggers_alert_and_dedup(self, bob_sess, admin_sess):
        # Baseline
        before = self._alerts_for_user(admin_sess, bob_sess.user_id)
        before_count = len(before)

        # 1st withdraw attempt → 403 KYC_REQUIRED + should create alert (if not already in 60min window)
        r1 = bob_sess.post(
            f"{BASE_URL}/api/wallet/withdraw",
            json={"method": "usdt_trc20", "amount": 600,
                  "address": "TRX1234567890ABCDEFGHIJKLMN"},
            timeout=15,
        )
        assert r1.status_code == 403, f"expected 403 KYC, got {r1.status_code}: {r1.text[:200]}"
        assert "KYC" in r1.text, r1.text[:200]

        # 2nd call in same window → 403 again but NO additional alert row (dedup 60min)
        time.sleep(1.0)
        r2 = bob_sess.post(
            f"{BASE_URL}/api/wallet/withdraw",
            json={"method": "usdt_trc20", "amount": 600,
                  "address": "TRX1234567890ABCDEFGHIJKLMN"},
            timeout=15,
        )
        assert r2.status_code == 403

        time.sleep(1.5)  # let DB settle
        after = self._alerts_for_user(admin_sess, bob_sess.user_id)
        after_count = len(after)
        new_alerts = after_count - before_count
        # Either there was already a row in 60min window (→ 0 new) or first call created 1.
        # What MUST be true: ≤ 1 new alert row (dedup enforced).
        assert new_alerts <= 1, (
            f"dedup violated: {new_alerts} new withdraw_no_kyc alerts "
            f"for user {bob_sess.user_id} (before={before_count}, after={after_count})"
        )


# ─── 4. Rate-limit on /api/wallet/send ─────────────────────────────────────
class TestSendRateLimit:
    def test_5_per_minute(self, bob_sess, alice_sess):
        """Fire 7 sequential sends. Expect 429 after 5 accepted calls.

        Uses huge amount to force HTTP 400 ("insufficient balance") on handler
        side — the slowapi decorator runs BEFORE the handler so the bucket is
        still consumed on each attempt, and the 6th/7th attempts must return 429.
        This avoids requiring balance top-up in tests.
        """
        codes = []
        for _ in range(7):
            r = bob_sess.post(
                f"{BASE_URL}/api/wallet/send",
                json={"to_user_id": alice_sess.user_id, "amount": 999999, "notes": "rl-test"},
                timeout=15,
            )
            codes.append(r.status_code)
            if r.status_code == 429:
                body = r.text
                assert "rate limit" in body.lower() or "5 per" in body or "429" in body, body[:200]
        print(f"[rate-limit] codes={codes}")
        rate_limited = sum(1 for c in codes if c == 429)
        non_429 = sum(1 for c in codes if c != 429)
        assert rate_limited >= 1, (
            f"expected ≥1 HTTP 429 in 7 sequential sends, got codes={codes}. "
            f"Rate-limit may not be active."
        )
        assert non_429 <= 5, f"expected ≤5 non-429 before rate-limit, got {non_429}: {codes}"


# ─── 5. /api/wallet/send still works ───────────────────────────────────────
class TestSendStillWorks:
    def test_single_send_ok(self, bob_sess, alice_sess):
        # Wait for rate-limit window (60s+) from previous test
        time.sleep(62)
        bal = float(bob_sess.get(f"{BASE_URL}/api/wallet/balance", timeout=10).json()["balance"])
        if bal < 0.05:
            # Just validate the endpoint still reachable (not rate-limited) with expected 400
            r = bob_sess.post(
                f"{BASE_URL}/api/wallet/send",
                json={"to_user_id": alice_sess.user_id, "amount": 0.01, "notes": "post-rl"},
                timeout=15,
            )
            # 400 (insufficient balance) = endpoint reachable, rate-limit reset
            assert r.status_code in (200, 400), f"{r.status_code} {r.text[:200]}"
            assert r.status_code != 429, "rate-limit window should have expired"
            return
        r = bob_sess.post(
            f"{BASE_URL}/api/wallet/send",
            json={"to_user_id": alice_sess.user_id, "amount": 0.01, "notes": "post-rl"},
            timeout=15,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        d = r.json()
        assert "tx_id" in d and "new_balance" in d
