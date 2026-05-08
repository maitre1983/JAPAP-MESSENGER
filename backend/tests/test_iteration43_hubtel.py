"""Iteration 42-43: Hubtel webhook (native format) + deposit flow tests."""
import os
import hmac
import hashlib
import json
import uuid
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or
             "https://japap-refactor.preview.emergentagent.com").rstrip("/")
USER_EMAIL = "bob@japap.com"
USER_PASSWORD = "Test1234!"
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"


@pytest.fixture(scope="module")
def user_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": USER_EMAIL, "password": USER_PASSWORD})
    assert r.status_code == 200, f"User login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    return s


def _get_balance(user_session):
    r = user_session.get(f"{BASE_URL}/api/wallet/balance")
    assert r.status_code == 200
    return float(r.json()["balance"])


def _create_usdt_deposit(user_session, amount=10.0):
    """Create a USDT pending deposit so we can then credit via hubtel webhook."""
    r = user_session.post(f"{BASE_URL}/api/wallet/deposit",
                          json={"amount": amount, "method": "usdt_trc20",
                                "reference": f"TEST_iter43_{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 200, f"Deposit creation failed: {r.status_code} {r.text}"
    return r.json()["tx_id"]


class TestHubtelWebhookNative:
    """Hubtel NATIVE payload format webhook tests."""

    def test_native_success_credits_wallet(self, user_session):
        bal_before = _get_balance(user_session)
        tx_id = _create_usdt_deposit(user_session, amount=10.0)

        payload = {
            "ResponseCode": "0000",
            "Status": "Success",
            "Data": {
                "ClientReference": tx_id,
                "Status": "Success",
                "CheckoutId": "abc_native_" + uuid.uuid4().hex[:6],
                "Amount": 10.0,
            }
        }
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook", json=payload)
        assert r.status_code == 200, f"Webhook failed: {r.status_code} {r.text}"
        data = r.json()
        assert data["status"] == "completed"
        assert data["tx_id"] == tx_id

        bal_after = _get_balance(user_session)
        assert bal_after == pytest.approx(bal_before + 10.0, abs=0.01), \
            f"Balance not credited: {bal_before} -> {bal_after}"

    def test_idempotency_no_double_credit(self, user_session):
        tx_id = _create_usdt_deposit(user_session, amount=5.0)
        payload = {
            "ResponseCode": "0000", "Status": "Success",
            "Data": {"ClientReference": tx_id, "Status": "Success",
                     "CheckoutId": "idemp_1", "Amount": 5.0}
        }
        r1 = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook", json=payload)
        assert r1.status_code == 200 and r1.json()["status"] == "completed"
        bal_after_first = _get_balance(user_session)

        # Replay the same webhook
        r2 = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook", json=payload)
        assert r2.status_code == 200
        assert r2.json()["status"] == "already_completed"
        bal_after_replay = _get_balance(user_session)
        assert bal_after_replay == pytest.approx(bal_after_first, abs=0.01)

    def test_failure_path_rejects_no_credit(self, user_session):
        bal_before = _get_balance(user_session)
        tx_id = _create_usdt_deposit(user_session, amount=7.0)
        payload = {
            "ResponseCode": "2001", "Status": "Failed",
            "Data": {"ClientReference": tx_id, "Status": "Failed",
                     "CheckoutId": "fail_1", "Amount": 7.0}
        }
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook", json=payload)
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"
        bal_after = _get_balance(user_session)
        # bal_before is AFTER USDT deposit creation (which doesn't credit wallet)
        # so should remain equal
        assert bal_after == pytest.approx(bal_before, abs=0.01)

    def test_legacy_format_still_works(self, user_session):
        bal_before = _get_balance(user_session)
        tx_id = _create_usdt_deposit(user_session, amount=3.0)
        payload = {"tx_id": tx_id, "status": "PAID", "hubtel_ref": "legacy_ref_xyz"}
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook", json=payload)
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        bal_after = _get_balance(user_session)
        assert bal_after == pytest.approx(bal_before + 3.0, abs=0.01)

    def test_missing_client_reference_400(self):
        payload = {"ResponseCode": "0000", "Status": "Success",
                   "Data": {"Status": "Success", "CheckoutId": "x"}}
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook", json=payload)
        assert r.status_code == 400
        assert "ClientReference" in r.text or "tx_id" in r.text


class TestHubtelWebhookSignature:
    """HMAC signature verification when hubtel_webhook_secret is set."""

    SECRET = "test_secret_123"

    @pytest.fixture(autouse=True)
    def set_and_clear_secret(self, admin_session):
        # Set the secret
        r = admin_session.put(f"{BASE_URL}/api/admin/settings",
                              json={"settings": {"hubtel_webhook_secret": self.SECRET}})
        assert r.status_code == 200, f"Set secret failed: {r.status_code} {r.text}"
        yield
        # Clear the secret
        r = admin_session.put(f"{BASE_URL}/api/admin/settings",
                              json={"settings": {"hubtel_webhook_secret": ""}})
        assert r.status_code == 200

    def test_missing_signature_returns_401(self, user_session):
        tx_id = _create_usdt_deposit(user_session, amount=1.0)
        payload = {"ResponseCode": "0000", "Status": "Success",
                   "Data": {"ClientReference": tx_id, "Status": "Success",
                            "CheckoutId": "sig_miss", "Amount": 1.0}}
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook", json=payload)
        assert r.status_code == 401

    def test_wrong_signature_returns_401(self, user_session):
        tx_id = _create_usdt_deposit(user_session, amount=1.0)
        payload = {"ResponseCode": "0000", "Status": "Success",
                   "Data": {"ClientReference": tx_id, "Status": "Success",
                            "CheckoutId": "sig_wrong", "Amount": 1.0}}
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook",
                          json=payload,
                          headers={"X-Auth-Signature": "deadbeef"})
        assert r.status_code == 401

    def test_correct_signature_returns_200(self, user_session):
        bal_before = _get_balance(user_session)
        tx_id = _create_usdt_deposit(user_session, amount=2.0)
        payload = {"ResponseCode": "0000", "Status": "Success",
                   "Data": {"ClientReference": tx_id, "Status": "Success",
                            "CheckoutId": "sig_ok", "Amount": 2.0}}
        raw = json.dumps(payload).encode()
        sig = hmac.new(self.SECRET.encode(), raw, hashlib.sha256).hexdigest()
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook",
                          data=raw,
                          headers={"X-Auth-Signature": sig,
                                   "Content-Type": "application/json"})
        assert r.status_code == 200, f"Correct sig failed: {r.status_code} {r.text}"
        assert r.json()["status"] == "completed"
        bal_after = _get_balance(user_session)
        assert bal_after == pytest.approx(bal_before + 2.0, abs=0.01)


class TestHubtelDepositWithInvalidKeys:
    """Hubtel deposit endpoint with (current) invalid admin keys should return 502."""

    def test_hubtel_card_502_with_invalid_keys(self, user_session):
        bal_before = _get_balance(user_session)
        r = user_session.post(f"{BASE_URL}/api/wallet/deposit",
                              json={"amount": 15.0, "method": "hubtel_card"})
        # Expected: 502 with 'Erreur Hubtel' detail OR 200 with mocked:true
        # depending on whether keys are set at all.
        if r.status_code == 502:
            assert "Hubtel" in r.text or "Erreur" in r.text
        elif r.status_code == 200:
            data = r.json()
            # Config missing -> mocked path
            assert data.get("mocked") is True, \
                f"Expected 502 or mocked:true, got 200 with {data}"
        else:
            pytest.fail(f"Unexpected status {r.status_code}: {r.text}")

        # Balance must not change either way (transaction is pending)
        bal_after = _get_balance(user_session)
        assert bal_after == pytest.approx(bal_before, abs=0.01)
