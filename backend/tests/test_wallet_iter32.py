"""
JAPAP — Iteration 32 Wallet Refactor Tests
==========================================
Covers: payment-methods, deposit (3 methods), withdraw (USDT TRC20/BEP20),
fee computation (percent + flat), KYC gating, validation errors, persistence.
"""
import os
import subprocess
import uuid
import pytest
import requests
from decimal import Decimal

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
USER = {"email": "bob@japap.com", "password": "Test1234!"}


def _psql(sql: str) -> str:
    r = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "japap_messenger", "-t", "-A", "-c", sql],
        capture_output=True, text=True, timeout=15,
    )
    return (r.stdout or "").strip()


def _login(session: requests.Session, creds: dict) -> dict:
    r = session.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, f"Login failed ({creds['email']}): {r.status_code} {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def admin_sess():
    s = requests.Session()
    _login(s, ADMIN)
    yield s


@pytest.fixture(scope="module")
def user_sess():
    s = requests.Session()
    data = _login(s, USER)
    # attach user_id
    s.user_id = data.get("user", {}).get("user_id") or _psql(
        "SELECT user_id FROM users WHERE email='bob@japap.com';"
    )
    yield s


@pytest.fixture(scope="module")
def bob_user_id():
    uid = _psql("SELECT user_id FROM users WHERE email='bob@japap.com';")
    assert uid, "bob user_id not found"
    return uid


def _set_setting(admin_sess: requests.Session, key: str, value: str):
    r = admin_sess.put(f"{BASE_URL}/api/admin/settings/{key}", json={"value": value}, timeout=15)
    assert r.status_code == 200, f"set_setting {key}={value} failed: {r.status_code} {r.text}"


def _approve_bob_kyc(bob_user_id: str):
    """Force-approve KYC for bob via direct DB write (admin API requires photo)."""
    existing = _psql(
        f"SELECT status FROM kyc_verifications WHERE user_id='{bob_user_id}' "
        "ORDER BY created_at DESC LIMIT 1;"
    )
    if existing == "approved":
        return
    kyc_id = f"kyc_{uuid.uuid4().hex[:16]}"
    _psql(
        f"INSERT INTO kyc_verifications (kyc_id, user_id, full_name, id_type, id_number, "
        f"id_photo_url, selfie_url, status, reviewed_at) "
        f"VALUES ('{kyc_id}','{bob_user_id}','Bob Test','national_id','TEST12345',"
        f"'/api/upload/files/test.jpg','/api/upload/files/self.jpg','approved',NOW()) "
        f"ON CONFLICT DO NOTHING;"
    )
    _psql(
        f"UPDATE kyc_verifications SET status='approved', reviewed_at=NOW() "
        f"WHERE user_id='{bob_user_id}' AND status IN ('pending','rejected');"
    )
    _psql(f"UPDATE users SET is_verified=TRUE WHERE user_id='{bob_user_id}';")


def _set_wallet_balance(user_id: str, balance: str = "500.00"):
    _psql(
        f"INSERT INTO wallets (user_id, balance, currency) VALUES ('{user_id}', {balance}, 'USD') "
        f"ON CONFLICT (user_id) DO UPDATE SET balance={balance};"
    )


# ========== Payment methods ==========
class TestPaymentMethods:
    def test_payment_methods_shape(self, user_sess):
        r = user_sess.get(f"{BASE_URL}/api/wallet/payment-methods", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        dep_ids = {m["id"] for m in d["deposit"]}
        wd_ids = {m["id"] for m in d["withdraw"]}
        assert dep_ids == {"usdt_trc20", "usdt_bep20", "hubtel_card"}, dep_ids
        assert wd_ids == {"usdt_trc20", "usdt_bep20"}, wd_ids
        assert d["fee"]["mode"] in ("percent", "flat")
        assert "value" in d["fee"] and "label" in d["fee"]
        assert "min_withdraw_usd" in d
        # chain metadata
        chain_by_id = {m["id"]: m.get("chain") for m in d["deposit"]}
        assert chain_by_id["usdt_trc20"] == "TRON"
        assert chain_by_id["usdt_bep20"] == "BSC"


# ========== Deposit ==========
class TestDeposit:
    def test_deposit_usdt_trc20(self, user_sess):
        r = user_sess.post(f"{BASE_URL}/api/wallet/deposit",
                           json={"amount": 10, "method": "usdt_trc20"}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "pending"
        assert d["chain"] == "TRON"
        assert d["method"] == "usdt_trc20"
        assert "address" in d
        assert "instruction" in d and "TRON" in d["instruction"]
        assert d["tx_id"].startswith("dep_")
        # Verify pending tx row was created
        status = _psql(f"SELECT status FROM transactions WHERE tx_id='{d['tx_id']}';")
        assert status == "pending"
        ttype = _psql(f"SELECT type FROM transactions WHERE tx_id='{d['tx_id']}';")
        assert ttype == "deposit"

    def test_deposit_usdt_bep20(self, user_sess):
        r = user_sess.post(f"{BASE_URL}/api/wallet/deposit",
                           json={"amount": 15, "method": "usdt_bep20"}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["chain"] == "BSC"
        assert d["status"] == "pending"

    def test_deposit_hubtel_mocked(self, user_sess):
        r = user_sess.post(f"{BASE_URL}/api/wallet/deposit",
                           json={"amount": 10, "method": "hubtel_card"}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("mocked") is True
        assert "checkout_url" in d and d["checkout_url"].startswith("http")
        assert d["status"] == "pending"

    def test_deposit_invalid_method(self, user_sess):
        r = user_sess.post(f"{BASE_URL}/api/wallet/deposit",
                           json={"amount": 10, "method": "paypal"}, timeout=15)
        assert r.status_code == 400

    def test_deposit_nonpositive_amount(self, user_sess):
        r = user_sess.post(f"{BASE_URL}/api/wallet/deposit",
                           json={"amount": 0, "method": "usdt_trc20"}, timeout=15)
        assert r.status_code == 400
        r = user_sess.post(f"{BASE_URL}/api/wallet/deposit",
                           json={"amount": -5, "method": "usdt_trc20"}, timeout=15)
        assert r.status_code == 400


# ========== Withdraw ==========
class TestWithdraw:
    def test_withdraw_kyc_required(self, admin_sess, user_sess, bob_user_id):
        # Revoke KYC and ensure kyc_required=true
        _set_setting(admin_sess, "kyc_required_for_withdraw", "true")
        _psql(f"UPDATE kyc_verifications SET status='pending' WHERE user_id='{bob_user_id}';")
        r = user_sess.post(f"{BASE_URL}/api/wallet/withdraw",
                           json={"amount": 5, "method": "usdt_trc20",
                                 "address": "TTestAddress123456789"}, timeout=15)
        assert r.status_code == 403, r.text
        assert "KYC_REQUIRED" in r.json().get("detail", "")

    def test_withdraw_method_not_whitelisted(self, admin_sess, user_sess, bob_user_id):
        _approve_bob_kyc(bob_user_id)
        _set_wallet_balance(bob_user_id, "500.00")
        r = user_sess.post(f"{BASE_URL}/api/wallet/withdraw",
                           json={"amount": 5, "method": "mobile_money",
                                 "address": "TTestAddress123456789"}, timeout=15)
        assert r.status_code == 400

    def test_withdraw_address_too_short(self, admin_sess, user_sess, bob_user_id):
        _approve_bob_kyc(bob_user_id)
        r = user_sess.post(f"{BASE_URL}/api/wallet/withdraw",
                           json={"amount": 5, "method": "usdt_trc20", "address": "short"},
                           timeout=15)
        assert r.status_code == 400

    def test_withdraw_percent_fee(self, admin_sess, user_sess, bob_user_id):
        _approve_bob_kyc(bob_user_id)
        _set_wallet_balance(bob_user_id, "500.00")
        _set_setting(admin_sess, "withdraw_fee_mode", "percent")
        _set_setting(admin_sess, "withdraw_fee_value", "2")
        _set_setting(admin_sess, "withdraw_min_amount_usd", "1")
        before = Decimal(_psql(f"SELECT balance FROM wallets WHERE user_id='{bob_user_id}';"))
        r = user_sess.post(f"{BASE_URL}/api/wallet/withdraw",
                           json={"amount": 50, "method": "usdt_trc20",
                                 "address": "TTestAddress123456789"}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["fee_mode"] == "percent"
        assert Decimal(d["fee_usd"]) == Decimal("1.0000") or Decimal(d["fee_usd"]) == Decimal("1")
        assert Decimal(d["net_usd"]) == Decimal("49") or Decimal(d["net_usd"]) == Decimal("49.0000")
        assert d["chain"] == "TRON"
        assert d["address"] == "TTestAddress123456789"
        # Verify full amount debited (fee stored separately on tx row)
        after = Decimal(_psql(f"SELECT balance FROM wallets WHERE user_id='{bob_user_id}';"))
        assert (before - after) == Decimal("50"), f"before={before} after={after}"
        # Fee stored on tx row
        fee = _psql(f"SELECT fee FROM transactions WHERE tx_id='{d['tx_id']}';")
        assert Decimal(fee) == Decimal("1.0000") or Decimal(fee) == Decimal("1")

    def test_withdraw_flat_fee(self, admin_sess, user_sess, bob_user_id):
        _approve_bob_kyc(bob_user_id)
        _set_wallet_balance(bob_user_id, "500.00")
        _set_setting(admin_sess, "withdraw_fee_mode", "flat")
        _set_setting(admin_sess, "withdraw_fee_value", "1")
        r = user_sess.post(f"{BASE_URL}/api/wallet/withdraw",
                           json={"amount": 10, "method": "usdt_bep20",
                                 "address": "0xTestBep20Address1234567"}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["fee_mode"] == "flat"
        assert Decimal(d["fee_usd"]) == Decimal("1")
        assert Decimal(d["net_usd"]) == Decimal("9")
        assert d["chain"] == "BSC"
        # payment-methods should reflect flat
        r2 = user_sess.get(f"{BASE_URL}/api/wallet/payment-methods", timeout=15)
        assert r2.json()["fee"]["mode"] == "flat"
        assert r2.json()["fee"]["label"] == "USDT"


# ========== Regression ==========
class TestRegression:
    def test_balance_endpoint(self, user_sess):
        r = user_sess.get(f"{BASE_URL}/api/wallet/balance", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "balance" in d and "currency" in d
        assert "_id" not in d

    def test_transactions_endpoint(self, user_sess):
        r = user_sess.get(f"{BASE_URL}/api/wallet/transactions?limit=50", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "transactions" in d
        types = {t["type"] for t in d["transactions"]}
        # After running deposit + withdraw tests, both should appear
        assert "deposit" in types or "withdrawal" in types
        for t in d["transactions"]:
            assert "_id" not in t


@pytest.fixture(scope="module", autouse=True)
def _reset_settings_after(admin_sess):
    yield
    # Restore defaults after test module completes
    try:
        _set_setting(admin_sess, "withdraw_fee_mode", "percent")
        _set_setting(admin_sess, "withdraw_fee_value", "2")
    except Exception:
        pass
