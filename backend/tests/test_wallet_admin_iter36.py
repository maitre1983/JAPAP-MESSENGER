"""
Iteration 36 — Wallet admin moderation + webhooks + mode switching tests.
Covers:
  - GET  /api/admin/deposits (filters, pagination, volume_total_usd, method_label/processing_mode)
  - GET  /api/admin/withdrawals
  - POST /api/admin/deposits/{tx_id}/approve, /reject
  - POST /api/admin/withdrawals/{tx_id}/approve, /reject (refund)
  - POST /api/wallet/deposit (deposits_enabled kill switch)
  - POST /api/wallet/withdraw (manual/auto/both modes)
  - POST /api/wallet/hubtel/webhook
  - POST /api/wallet/nowpayments/webhook
  - Regression: /api/wallet/payment-methods still returns fee + best_pro_fee
"""
import os
import pytest
import requests
from decimal import Decimal

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
USER_EMAIL = "bob@japap.com"
USER_PASSWORD = "Test1234!"


# ---------- Fixtures ----------
@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def user_client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": USER_EMAIL, "password": USER_PASSWORD})
    assert r.status_code == 200, f"user login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module", autouse=True)
def restore_settings(admin_client):
    """Snapshot & restore settings touched by these tests."""
    keys = [
        "deposits_enabled", "manual_withdraw_enabled", "auto_withdraw_enabled",
        "withdraw_enabled", "kyc_required_for_withdraw",
    ]
    r = admin_client.get(f"{BASE_URL}/api/admin/settings")
    snap = {}
    if r.status_code == 200:
        settings = r.json().get("settings", {})
        snap = {k: settings.get(k) for k in keys}
    yield
    # restore
    payload = {k: v for k, v in snap.items() if v is not None}
    if payload:
        admin_client.put(f"{BASE_URL}/api/admin/settings", json={"settings": payload})


def _set_settings(admin_client, **kwargs):
    payload = {k: ("true" if v is True else "false" if v is False else v) for k, v in kwargs.items()}
    r = admin_client.put(f"{BASE_URL}/api/admin/settings", json={"settings": payload})
    assert r.status_code == 200, f"settings update failed: {r.text}"


# ---------- 1. Regression: payment-methods ----------
class TestPaymentMethodsRegression:
    def test_payment_methods_keys_present(self, user_client):
        r = user_client.get(f"{BASE_URL}/api/wallet/payment-methods")
        assert r.status_code == 200
        data = r.json()
        assert "deposit" in data and "withdraw" in data
        assert "fee" in data
        assert "mode" in data["fee"] and "value" in data["fee"]
        assert "best_pro_fee" in data  # nullable OK
        assert "min_withdraw_usd" in data


# ---------- 2. Admin listing ----------
class TestAdminListing:
    def test_list_deposits_structure(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/admin/deposits?status=pending&page=1&limit=20")
        assert r.status_code == 200
        d = r.json()
        assert set(["items", "total", "volume_total_usd", "page", "limit"]).issubset(d.keys())
        assert isinstance(d["items"], list)
        # If items exist, check shape
        if d["items"]:
            row = d["items"][0]
            for k in ("tx_id", "status", "amount", "email", "first_name", "method_label", "processing_mode", "fee"):
                assert k in row, f"missing key {k} in deposit row"

    def test_list_withdrawals_has_processing_mode(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/admin/withdrawals?limit=50")
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d["items"], list)
        if d["items"]:
            assert "processing_mode" in d["items"][0]

    def test_filters_q_and_status(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/admin/deposits?q=bob&status=pending")
        assert r.status_code == 200
        d = r.json()
        # every item must match status
        for it in d["items"]:
            assert it["status"] == "pending"

    def test_non_admin_forbidden(self, user_client):
        r = user_client.get(f"{BASE_URL}/api/admin/deposits")
        assert r.status_code in (401, 403)


# ---------- 3. Deposits kill switch ----------
class TestDepositsKillSwitch:
    def test_deposit_503_when_disabled(self, admin_client, user_client):
        _set_settings(admin_client, deposits_enabled=False)
        try:
            r = user_client.post(f"{BASE_URL}/api/wallet/deposit",
                                 json={"amount": 10, "method": "usdt_trc20"})
            assert r.status_code == 503, f"expected 503 got {r.status_code}: {r.text}"
        finally:
            _set_settings(admin_client, deposits_enabled=True)

    def test_deposit_ok_when_enabled(self, admin_client, user_client):
        _set_settings(admin_client, deposits_enabled=True)
        r = user_client.post(f"{BASE_URL}/api/wallet/deposit",
                             json={"amount": 5, "method": "usdt_trc20"})
        assert r.status_code == 200
        assert r.json().get("status") == "pending"


# ---------- 4. Approve / Reject deposits ----------
class TestDepositModeration:
    def _create_pending(self, user_client, amount=7.0):
        r = user_client.post(f"{BASE_URL}/api/wallet/deposit",
                             json={"amount": amount, "method": "usdt_trc20", "reference": "TEST_HASH"})
        assert r.status_code == 200, r.text
        return r.json()["tx_id"]

    def _balance(self, user_client):
        r = user_client.get(f"{BASE_URL}/api/wallet/balance")
        return Decimal(r.json()["balance"])

    def test_approve_credits_wallet(self, admin_client, user_client):
        tx_id = self._create_pending(user_client, 11.0)
        before = self._balance(user_client)
        r = admin_client.post(f"{BASE_URL}/api/admin/deposits/{tx_id}/approve", json={"reason": "ok"})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        after = self._balance(user_client)
        assert after - before == Decimal("11"), f"expected +11, got {after - before}"

    def test_approve_idempotent(self, admin_client, user_client):
        tx_id = self._create_pending(user_client, 3.0)
        admin_client.post(f"{BASE_URL}/api/admin/deposits/{tx_id}/approve", json={"reason": "ok"})
        r = admin_client.post(f"{BASE_URL}/api/admin/deposits/{tx_id}/approve", json={"reason": "again"})
        assert r.status_code == 200
        assert r.json()["status"] in ("already_completed", "completed")

    def test_approve_unknown_404(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/admin/deposits/dep_DOESNOTEXIST/approve", json={"reason": "x"})
        assert r.status_code == 404

    def test_reject_does_not_credit(self, admin_client, user_client):
        tx_id = self._create_pending(user_client, 8.0)
        before = self._balance(user_client)
        r = admin_client.post(f"{BASE_URL}/api/admin/deposits/{tx_id}/reject", json={"reason": "suspect"})
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"
        after = self._balance(user_client)
        assert after == before, "balance should not change on reject"

    def test_reject_after_completed_400(self, admin_client, user_client):
        tx_id = self._create_pending(user_client, 2.0)
        admin_client.post(f"{BASE_URL}/api/admin/deposits/{tx_id}/approve", json={"reason": "ok"})
        r = admin_client.post(f"{BASE_URL}/api/admin/deposits/{tx_id}/reject", json={"reason": "late"})
        assert r.status_code == 400


# ---------- 5. Withdraw modes ----------
class TestWithdrawModes:
    @pytest.fixture(autouse=True)
    def _no_kyc(self, admin_client):
        _set_settings(admin_client, kyc_required_for_withdraw=False, withdraw_enabled=True)
        yield

    def test_both_disabled_returns_503(self, admin_client, user_client):
        _set_settings(admin_client, manual_withdraw_enabled=False, auto_withdraw_enabled=False)
        try:
            r = user_client.post(f"{BASE_URL}/api/wallet/withdraw",
                                 json={"amount": 10, "method": "usdt_trc20",
                                       "address": "TTestAddress123456789"})
            assert r.status_code == 503
            assert "Aucun mode" in r.json().get("detail", "") or "retrait" in r.json().get("detail", "").lower()
        finally:
            _set_settings(admin_client, manual_withdraw_enabled=True, auto_withdraw_enabled=False)

    def test_auto_mode_processing(self, admin_client, user_client):
        _set_settings(admin_client, manual_withdraw_enabled=True, auto_withdraw_enabled=True)
        try:
            r = user_client.post(f"{BASE_URL}/api/wallet/withdraw",
                                 json={"amount": 10, "method": "usdt_trc20",
                                       "address": "TTestAddress123456789"})
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["processing_mode"] == "auto"
            assert d["status"] == "processing"
        finally:
            _set_settings(admin_client, auto_withdraw_enabled=False)

    def test_manual_mode_pending(self, admin_client, user_client):
        _set_settings(admin_client, manual_withdraw_enabled=True, auto_withdraw_enabled=False)
        r = user_client.post(f"{BASE_URL}/api/wallet/withdraw",
                             json={"amount": 10, "method": "usdt_trc20",
                                   "address": "TTestAddress123456789"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["processing_mode"] == "manual"
        assert d["status"] == "pending"


# ---------- 6. Withdraw approve/reject(refund) ----------
class TestWithdrawModeration:
    @pytest.fixture(autouse=True)
    def _setup(self, admin_client):
        _set_settings(admin_client, kyc_required_for_withdraw=False,
                      manual_withdraw_enabled=True, auto_withdraw_enabled=False,
                      withdraw_enabled=True)

    def _balance(self, user_client):
        return Decimal(user_client.get(f"{BASE_URL}/api/wallet/balance").json()["balance"])

    def _create_pending(self, user_client, amt=10.0):
        r = user_client.post(f"{BASE_URL}/api/wallet/withdraw",
                             json={"amount": amt, "method": "usdt_trc20",
                                   "address": "TTestAddress123456789"})
        assert r.status_code == 200, r.text
        return r.json()["tx_id"]

    def test_approve_marks_completed(self, admin_client, user_client):
        tx_id = self._create_pending(user_client, 6.0)
        r = admin_client.post(f"{BASE_URL}/api/admin/withdrawals/{tx_id}/approve", json={"reason": "sent"})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_reject_refunds_wallet(self, admin_client, user_client):
        before_creation = self._balance(user_client)
        tx_id = self._create_pending(user_client, 7.5)
        after_creation = self._balance(user_client)
        # Balance has been debited (full amount)
        assert before_creation - after_creation == Decimal("7.5")
        r = admin_client.post(f"{BASE_URL}/api/admin/withdrawals/{tx_id}/reject",
                              json={"reason": "bad address"})
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "rejected"
        assert "refunded" in d
        after_refund = self._balance(user_client)
        assert after_refund == before_creation, f"expected refund to restore balance: {after_refund} vs {before_creation}"

    def test_reject_unknown_404(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/admin/withdrawals/wdr_DOESNOTEXIST/reject",
                              json={"reason": "x"})
        assert r.status_code == 404


# ---------- 7. Webhooks ----------
class TestWebhooks:
    @pytest.fixture(autouse=True)
    def _enable_deposits(self, admin_client):
        _set_settings(admin_client, deposits_enabled=True)

    def _create_pending_deposit(self, user_client, amount=4.0):
        r = user_client.post(f"{BASE_URL}/api/wallet/deposit",
                             json={"amount": amount, "method": "hubtel_card"})
        assert r.status_code == 200, r.text
        return r.json()["tx_id"]

    def _balance(self, user_client):
        return Decimal(user_client.get(f"{BASE_URL}/api/wallet/balance").json()["balance"])

    def test_hubtel_paid_credits(self, user_client):
        tx_id = self._create_pending_deposit(user_client, 4.0)
        before = self._balance(user_client)
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook",
                          json={"tx_id": tx_id, "status": "PAID", "hubtel_ref": "HUB_TEST_123"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"
        after = self._balance(user_client)
        assert after - before == Decimal("4")

    def test_hubtel_failed_rejects(self, user_client):
        tx_id = self._create_pending_deposit(user_client, 4.0)
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook",
                          json={"tx_id": tx_id, "status": "FAILED"})
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    def test_hubtel_unknown_404(self):
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook",
                          json={"tx_id": "dep_UNKNOWN", "status": "PAID"})
        assert r.status_code == 404

    def test_nowpayments_finished_credits(self, user_client):
        tx_id = self._create_pending_deposit(user_client, 6.0)
        before = self._balance(user_client)
        r = requests.post(f"{BASE_URL}/api/wallet/nowpayments/webhook",
                          json={"order_id": tx_id, "payment_status": "finished", "payment_id": "NP_123"})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        after = self._balance(user_client)
        assert after - before == Decimal("6")

    def test_nowpayments_failed_rejects(self, user_client):
        tx_id = self._create_pending_deposit(user_client, 3.0)
        r = requests.post(f"{BASE_URL}/api/wallet/nowpayments/webhook",
                          json={"order_id": tx_id, "payment_status": "failed"})
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    def test_nowpayments_confirming_pending(self, user_client):
        tx_id = self._create_pending_deposit(user_client, 3.0)
        r = requests.post(f"{BASE_URL}/api/wallet/nowpayments/webhook",
                          json={"order_id": tx_id, "payment_status": "confirming"})
        assert r.status_code == 200
        assert r.json()["status"] == "pending"

    def test_nowpayments_missing_order_400(self):
        r = requests.post(f"{BASE_URL}/api/wallet/nowpayments/webhook",
                          json={"payment_status": "finished"})
        assert r.status_code == 400
