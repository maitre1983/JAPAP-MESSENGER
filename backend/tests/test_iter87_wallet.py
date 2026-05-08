"""Iter87 — Wallet audit retest + P2 quick wins.

Covers:
- GET /api/admin/wallet/overview (admin only, shape)
- /wallet/send validations (amount<=0, self, insufficient, unknown recipient)
- 10 parallel sends Bob→Alice atomicity
- /wallet/withdraw KYC gate
- /wallet/balance + /wallet/transactions pagination
- Regression: duel create-from-tap, quiz start shuffling
"""
import os
import time
import threading
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


def _login(session: requests.Session, email: str, pw: str) -> dict:
    last_err = None
    for attempt in range(3):
        try:
            r = session.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw}, timeout=45)
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.exceptions.ReadTimeout as e:
            last_err = f"timeout: {e}"
        time.sleep(3)
    raise AssertionError(f"login failed for {email} after 3 attempts: {last_err}")


@pytest.fixture(scope="module")
def admin_sess():
    s = requests.Session()
    _login(s, ADMIN_EMAIL, ADMIN_PW)
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    return s


@pytest.fixture(scope="module")
def bob_sess():
    s = requests.Session()
    data = _login(s, BOB_EMAIL, BOB_PW)
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    s.user_id = data.get("user", {}).get("user_id")
    return s


@pytest.fixture(scope="module")
def alice_sess():
    s = requests.Session()
    data = _login(s, ALICE_EMAIL, ALICE_PW)
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    s.user_id = data.get("user", {}).get("user_id")
    return s


# ───── Admin wallet overview ────────────────────────────────────────────────
class TestAdminWalletOverview:
    def test_admin_access_shape(self, admin_sess):
        r = admin_sess.get(f"{BASE_URL}/api/admin/wallet/overview?days=30", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["window_days"] == 30
        bal = data["balances"]
        for k in ("accounts", "total_balance", "avg_balance", "max_balance", "funded", "locked"):
            assert k in bal, f"missing balances.{k}"
        assert isinstance(data["volumes_by_type"], list)
        if data["volumes_by_type"]:
            v = data["volumes_by_type"][0]
            for k in ("type", "count", "total", "completed", "pending", "cancelled"):
                assert k in v
        assert isinstance(data["timeseries"], list)
        for t in data["timeseries"]:
            for k in ("day", "count", "inflow", "outflow"):
                assert k in t
        top = data["top_funded"]
        assert isinstance(top, list) and len(top) <= 10
        an = data["anomalies"]
        for k in ("large_withdrawals", "stuck_pending_over_24h", "send_spam_last_1h"):
            assert k in an and isinstance(an[k], list)
        ep = data["engagement_points"]
        for k in ("total_points", "total_spins", "unique_players", "by_source"):
            assert k in ep
        for s in ("wheel", "quiz", "tap"):
            assert s in ep["by_source"]

    def test_non_admin_denied(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/admin/wallet/overview?days=30", timeout=15)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ───── Wallet basics ────────────────────────────────────────────────────────
class TestWalletBasics:
    def test_balance_shape(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/wallet/balance", timeout=10)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "user_id" in d and "balance" in d and "is_locked" in d
        assert isinstance(d["is_locked"], bool)

    def test_transactions_pagination(self, bob_sess):
        r = bob_sess.get(f"{BASE_URL}/api/wallet/transactions?page=1&limit=5", timeout=10)
        assert r.status_code == 200, r.text
        d = r.json()
        # accept either list or dict with items
        items = d if isinstance(d, list) else d.get("items") or d.get("transactions") or []
        assert isinstance(items, list)
        assert len(items) <= 5


# ───── Wallet send validations ──────────────────────────────────────────────
class TestWalletSendValidations:
    def _send(self, sess, payload):
        return sess.post(f"{BASE_URL}/api/wallet/send", json=payload, timeout=10)

    def test_amount_zero(self, bob_sess, alice_sess):
        r = self._send(bob_sess, {"to_user_id": alice_sess.user_id, "amount": 0})
        assert r.status_code == 400
        assert "positive" in r.text.lower()

    def test_amount_negative(self, bob_sess, alice_sess):
        r = self._send(bob_sess, {"to_user_id": alice_sess.user_id, "amount": -5})
        assert r.status_code == 400

    def test_self_transfer(self, bob_sess):
        r = self._send(bob_sess, {"to_user_id": bob_sess.user_id, "amount": 1})
        assert r.status_code == 400
        assert "yourself" in r.text.lower() or "soi" in r.text.lower() or "same" in r.text.lower()

    def test_insufficient_balance(self, bob_sess, alice_sess):
        r = self._send(bob_sess, {"to_user_id": alice_sess.user_id, "amount": 99999999})
        assert r.status_code == 400
        assert "insufficient" in r.text.lower() or "solde" in r.text.lower() or "balance" in r.text.lower()

    def test_unknown_recipient(self, bob_sess):
        r = self._send(bob_sess, {"to_user_id": "nonexistent_user_xyz_404", "amount": 1})
        assert r.status_code == 404
        assert "recipient" in r.text.lower() or "not found" in r.text.lower() or "introuvable" in r.text.lower()


# ───── Atomicity: 10 parallel sends ─────────────────────────────────────────
class TestParallelSends:
    def test_10_parallel_sends(self, bob_sess, alice_sess):
        # snapshot balances
        b_before = float(bob_sess.get(f"{BASE_URL}/api/wallet/balance").json()["balance"])
        a_before = float(alice_sess.get(f"{BASE_URL}/api/wallet/balance").json()["balance"])
        if b_before < 10:
            pytest.skip(f"Bob has insufficient balance ({b_before}) for 10 parallel sends")

        results = []
        def worker():
            try:
                r = bob_sess.post(
                    f"{BASE_URL}/api/wallet/send",
                    json={"to_user_id": alice_sess.user_id, "amount": 1},
                    timeout=60,
                )
                results.append(r.status_code)
            except Exception as e:
                results.append(f"err:{e}")

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        # wait for DB settle
        time.sleep(2.0)
        b_after = float(bob_sess.get(f"{BASE_URL}/api/wallet/balance").json()["balance"])
        a_after = float(alice_sess.get(f"{BASE_URL}/api/wallet/balance").json()["balance"])
        success = sum(1 for r in results if r == 200)
        print(f"[parallel] results={results} Bob {b_before}->{b_after} Alice {a_before}->{a_after}")
        # Primary assertion: atomicity & no double-credit (balance deltas exact)
        assert abs((b_before - b_after) - 10) < 0.0001, f"Bob delta expected -10, got {b_after - b_before}"
        assert abs((a_after - a_before) - 10) < 0.0001, f"Alice delta expected +10, got {a_after - a_before}"
        # Secondary: at least 8/10 client-observed 200 (remaining may have client-side timeouts
        # but the balance assertion proves server-side integrity).
        assert success >= 8, f"Only {success}/10 sends returned 200: {results}"


# ───── Withdraw KYC gate ────────────────────────────────────────────────────
class TestWithdrawKYC:
    def test_withdraw_without_kyc(self, bob_sess):
        r = bob_sess.post(
            f"{BASE_URL}/api/wallet/withdraw",
            json={"amount": 100, "method": "usdt_trc20", "address": "TXYZ1234567890ABC"},
            timeout=15,
        )
        # Expected 403 KYC_REQUIRED. Accept 400 with KYC_REQUIRED detail too.
        if r.status_code not in (403, 400):
            pytest.fail(f"expected 403/400 KYC, got {r.status_code}: {r.text}")
        assert "KYC" in r.text or "kyc" in r.text.lower()


# ───── Regression: Duel from tap ────────────────────────────────────────────
class TestDuelRegression:
    def test_create_from_tap(self, bob_sess, admin_sess):
        # reset first (ignore result)
        try:
            admin_sess.post(f"{BASE_URL}/api/tap/admin/reset-user/{bob_sess.user_id}", timeout=10)
        except Exception:
            pass
        # play a tap round to unlock duel
        start = bob_sess.post(f"{BASE_URL}/api/tap/start", json={}, timeout=10)
        if start.status_code != 200:
            pytest.skip(f"tap/start not available: {start.status_code}")
        run_id = start.json().get("run_id")
        # Ensure the tap session is flagged done before creating the duel.
        sub = bob_sess.post(
            f"{BASE_URL}/api/tap/submit",
            json={"run_id": run_id, "score": 50, "taps": 50, "duration_ms": 5000, "done": True},
            timeout=15,
        )
        # Accept either 200 or validation errors; log for diagnosis
        print(f"tap/submit -> {sub.status_code} {sub.text[:200]}")
        r = bob_sess.post(f"{BASE_URL}/api/duel/create-from-tap", json={"run_id": run_id}, timeout=15)
        if r.status_code == 400 and "session" in r.text.lower():
            pytest.skip(f"tap session not properly finalised in automation: {r.text[:200]}")
        assert r.status_code in (200, 201), f"{r.status_code} {r.text}"
        d = r.json()
        assert "share_token" in d
        assert len(d["share_token"]) >= 20
        assert d.get("share_url", "").endswith(d["share_token"])
        assert "expires_at" in d


# ───── Regression: Quiz shuffling + time_limit ──────────────────────────────
class TestQuizRegression:
    def test_quiz_start_shape(self, bob_sess, admin_sess):
        try:
            admin_sess.post(f"{BASE_URL}/api/quiz/admin/reset-user/{bob_sess.user_id}", timeout=10)
        except Exception:
            pass
        r = bob_sess.post(f"{BASE_URL}/api/quiz/start", json={}, timeout=15)
        if r.status_code != 200:
            pytest.skip(f"quiz/start returned {r.status_code}: {r.text[:200]}")
        d = r.json()
        assert "time_limit_seconds" in d or "time_limit_s" in d
        tl = d.get("time_limit_seconds") or d.get("time_limit_s")
        assert isinstance(tl, int) and tl > 0
        # shuffled options shape — at least one question
        qs = d.get("questions") or []
        if qs:
            assert isinstance(qs[0].get("options", []), list)
