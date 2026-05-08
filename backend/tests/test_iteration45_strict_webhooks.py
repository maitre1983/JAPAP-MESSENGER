"""Iteration 45 — STRICT zero-fraud webhook tests + NowPayments integration."""
import os
import json
import hmac
import hashlib
import uuid
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
USER_EMAIL = "bob@japap.com"
USER_PASSWORD = "Test1234!"
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
CAROL_EMAIL = "carol@japap.com"
CAROL_PASSWORD = "Test1234!"
NP_IPN_SECRET = "njMcKsf2xVabhLkDzGWvuXWkdEQz6V"


def _login(s, email, pwd):
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pwd})
    return r


@pytest.fixture(scope="module")
def user_session():
    s = requests.Session()
    r = _login(s, USER_EMAIL, USER_PASSWORD)
    assert r.status_code == 200, f"Bob login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = _login(s, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert r.status_code == 200
    return s


@pytest.fixture(scope="module")
def carol_session():
    s = requests.Session()
    r = _login(s, CAROL_EMAIL, CAROL_PASSWORD)
    if r.status_code != 200:
        # Try register
        rr = s.post(f"{BASE_URL}/api/auth/register",
                    json={"email": CAROL_EMAIL, "password": CAROL_PASSWORD,
                          "first_name": "Carol", "last_name": "Test",
                          "phone": "+22500000099", "country": "CI",
                          "preferred_lang": "fr", "terms_accepted": True})
        # Try login again
        r = _login(s, CAROL_EMAIL, CAROL_PASSWORD)
        if r.status_code != 200:
            pytest.skip(f"Carol register/login failed: register={rr.status_code} {rr.text[:120]}; login={r.status_code} {r.text[:120]}")
    return s


def _balance(s):
    r = s.get(f"{BASE_URL}/api/wallet/balance")
    assert r.status_code == 200, r.text
    return float(r.json()["balance"])


def _create_deposit(s, method, amount):
    r = s.post(f"{BASE_URL}/api/wallet/deposit",
               json={"amount": amount, "method": method,
                     "reference": f"TEST_iter45_{uuid.uuid4().hex[:8]}"})
    return r


# ============ Payment methods listing ============
class TestPaymentMethods:
    def test_lists_5_deposit_methods(self, user_session):
        r = user_session.get(f"{BASE_URL}/api/wallet/payment-methods")
        assert r.status_code == 200, r.text
        data = r.json()
        # Could be {"deposit": [...], "withdraw": [...]} or just list
        deposit = data.get("deposit") if isinstance(data, dict) else data
        assert deposit is not None, f"No deposit methods in response: {data}"
        codes = {m.get("code") or m.get("method") or m.get("id") for m in deposit}
        expected = {"usdt_trc20", "usdt_bep20", "hubtel_card",
                    "nowpayments_usdttrc20", "nowpayments_usdtbsc"}
        assert expected.issubset(codes), f"Missing methods. Got {codes}"
        for m in deposit:
            assert m.get("enabled", True) is True


# ============ NowPayments test-connection ============
class TestNowPaymentsConnection:
    def test_admin_test_connection_ok(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/wallet/nowpayments/test-connection")
        # Endpoint may be GET or POST per route definition (it's GET in code)
        if r.status_code == 405:
            r = admin_session.get(f"{BASE_URL}/api/wallet/nowpayments/test-connection")
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        body = r.json()
        assert body.get("ok") is True, f"Test conn not ok: {body}"
        assert body.get("environment") == "production"

    def test_non_admin_forbidden(self, user_session):
        r = user_session.get(f"{BASE_URL}/api/wallet/nowpayments/test-connection")
        if r.status_code == 405:
            r = user_session.post(f"{BASE_URL}/api/wallet/nowpayments/test-connection")
        assert r.status_code == 403


# ============ NowPayments create deposit (real invoice) ============
class TestNowPaymentsDeposit:
    def _deposit_invoice(self, s, method):
        r = _create_deposit(s, method, 3.0)
        assert r.status_code == 200, f"{method}: {r.status_code} {r.text}"
        d = r.json()
        assert "checkout_url" in d
        assert "nowpayments.io/payment" in d["checkout_url"], d["checkout_url"]
        assert "iid=" in d["checkout_url"], d["checkout_url"]
        ptx = str(d.get("provider_tx_id") or "")
        assert ptx.isdigit(), f"provider_tx_id not numeric: {ptx}"
        return d

    def test_invoice_trc20(self, user_session):
        self._deposit_invoice(user_session, "nowpayments_usdttrc20")

    def test_invoice_bsc(self, user_session):
        self._deposit_invoice(user_session, "nowpayments_usdtbsc")


# ============ STRICT — Hubtel spoofed webhook must NOT credit ============
class TestHubtelStrictWebhook:
    def test_spoofed_hubtel_webhook_does_not_credit(self, user_session, admin_session):
        # Make sure secret is empty so signature gating doesn't 401 first
        admin_session.put(f"{BASE_URL}/api/admin/settings",
                          json={"settings": {"hubtel_webhook_secret": ""}})
        bal_before = _balance(user_session)
        # Create hubtel_card pending deposit
        r = _create_deposit(user_session, "hubtel_card", 4.0)
        # Hubtel may 502 if invalid keys; in that case pending tx may not exist.
        # Use USDT as fallback to ensure we have a pending deposit row to attack.
        if r.status_code != 200:
            r = _create_deposit(user_session, "usdt_trc20", 4.0)
            assert r.status_code == 200, r.text
        tx_id = r.json()["tx_id"]

        payload = {
            "ResponseCode": "0000", "Status": "Success",
            "Data": {"ClientReference": tx_id, "Status": "Success",
                     "CheckoutId": f"spoof_{uuid.uuid4().hex[:6]}", "Amount": 4.0},
        }
        wh = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook", json=payload)
        # Acceptable: 200 with pending_verification/unverified, OR 502/503 if config error
        assert wh.status_code in (200, 502, 503), f"{wh.status_code} {wh.text}"
        if wh.status_code == 200:
            status = wh.json().get("status")
            assert status in ("pending_verification", "unverified"), \
                f"DANGER: status={status} body={wh.json()}"
        bal_after = _balance(user_session)
        assert bal_after == pytest.approx(bal_before, abs=0.01), \
            f"P0 CRITICAL: spoofed webhook credited wallet: {bal_before} -> {bal_after}"
        # Verify tx still pending
        tr = user_session.get(f"{BASE_URL}/api/wallet/transactions")
        assert tr.status_code == 200
        txs = tr.json() if isinstance(tr.json(), list) else tr.json().get("transactions", [])
        match = next((t for t in txs if t.get("tx_id") == tx_id), None)
        if match:
            assert match.get("status") == "pending", f"tx status={match.get('status')}"


# ============ STRICT — NowPayments spoofed webhook must NOT credit ============
class TestNowPaymentsStrictWebhook:
    def _create_np_pending(self, s):
        r = _create_deposit(s, "nowpayments_usdttrc20", 6.0)
        assert r.status_code == 200, r.text
        return r.json()  # contains tx_id, provider_tx_id, checkout_url

    def test_spoofed_no_signature_returns_401(self, user_session):
        d = self._create_np_pending(user_session)
        bal_before = _balance(user_session)
        payload = {"order_id": d["tx_id"], "payment_id": d["provider_tx_id"],
                   "payment_status": "finished"}
        r = requests.post(f"{BASE_URL}/api/wallet/nowpayments/webhook", json=payload)
        assert r.status_code == 401, f"Expected 401, got {r.status_code} {r.text}"
        assert _balance(user_session) == pytest.approx(bal_before, abs=0.01)

    def test_spoofed_wrong_signature_returns_401(self, user_session):
        d = self._create_np_pending(user_session)
        payload = {"order_id": d["tx_id"], "payment_id": d["provider_tx_id"],
                   "payment_status": "finished"}
        r = requests.post(f"{BASE_URL}/api/wallet/nowpayments/webhook",
                          json=payload, headers={"x-nowpayments-sig": "deadbeef"})
        assert r.status_code == 401

    def test_valid_hmac_but_unpaid_payment_no_credit(self, user_session):
        d = self._create_np_pending(user_session)
        bal_before = _balance(user_session)
        payload = {"order_id": d["tx_id"], "payment_id": d["provider_tx_id"],
                   "payment_status": "finished"}
        sorted_body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        sig = hmac.new(NP_IPN_SECRET.encode(), sorted_body.encode(),
                       hashlib.sha512).hexdigest()
        r = requests.post(f"{BASE_URL}/api/wallet/nowpayments/webhook",
                          data=sorted_body,
                          headers={"x-nowpayments-sig": sig,
                                   "Content-Type": "application/json"})
        assert r.status_code in (200, 502, 503), f"{r.status_code} {r.text}"
        if r.status_code == 200:
            status = r.json().get("status")
            assert status in ("pending_verification", "unverified"), \
                f"DANGER: status={status} body={r.json()}"
        bal_after = _balance(user_session)
        assert bal_after == pytest.approx(bal_before, abs=0.01), \
            f"P0 CRITICAL: HMAC-valid but unpaid webhook credited: {bal_before} -> {bal_after}"


# ============ Manual verify endpoints ============
class TestManualVerify:
    def test_nowpayments_verify_owner_unpaid(self, user_session):
        r = _create_deposit(user_session, "nowpayments_usdttrc20", 3.5)
        assert r.status_code == 200
        tx_id = r.json()["tx_id"]
        v = user_session.post(f"{BASE_URL}/api/wallet/nowpayments/verify/{tx_id}")
        assert v.status_code == 200, f"{v.status_code} {v.text}"
        body = v.json()
        assert body.get("status") in ("check_failed", "not_paid_yet"), body
        if body.get("status") == "check_failed":
            assert "404" in (body.get("reason") or "") or "not found" in (body.get("reason") or "").lower()

    def test_nowpayments_verify_non_owner_403(self, user_session, carol_session):
        r = _create_deposit(user_session, "nowpayments_usdttrc20", 3.5)
        assert r.status_code == 200
        tx_id = r.json()["tx_id"]
        v = carol_session.post(f"{BASE_URL}/api/wallet/nowpayments/verify/{tx_id}")
        assert v.status_code == 403, f"Expected 403 got {v.status_code} {v.text}"

    def test_hubtel_verify_owner_unpaid(self, user_session):
        # Create a hubtel deposit; if backend 502s use usdt and put a fake hubtel_ref
        r = _create_deposit(user_session, "hubtel_card", 5.0)
        if r.status_code != 200:
            pytest.skip(f"Hubtel deposit could not be created: {r.status_code} {r.text[:200]}")
        tx_id = r.json()["tx_id"]
        v = user_session.post(f"{BASE_URL}/api/wallet/hubtel/verify/{tx_id}")
        assert v.status_code in (200, 502, 503), f"{v.status_code} {v.text}"
        if v.status_code == 200:
            assert v.json().get("status") in ("check_failed", "not_paid_yet"), v.json()


# ============ Idempotency (legacy success path) ============
class TestIdempotency:
    def test_replay_completed_returns_already_completed(self, user_session, admin_session):
        admin_session.put(f"{BASE_URL}/api/admin/settings",
                          json={"settings": {"hubtel_webhook_secret": ""}})
        # Use the known-working legacy webhook path that bypasses Hubtel API verify?
        # Actually new strict policy goes through verify. So test idempotency by
        # checking that a completed transaction stays unchanged when webhook replays.
        # Easiest: find an existing 'completed' deposit for bob.
        r = user_session.get(f"{BASE_URL}/api/wallet/transactions")
        assert r.status_code == 200
        txs = r.json() if isinstance(r.json(), list) else r.json().get("transactions", [])
        completed = [t for t in txs if t.get("type") == "deposit"
                     and t.get("status") == "completed"]
        if not completed:
            pytest.skip("No completed deposit available for idempotency test")
        tx_id = completed[0]["tx_id"]
        bal_before = _balance(user_session)
        payload = {
            "ResponseCode": "0000", "Status": "Success",
            "Data": {"ClientReference": tx_id, "Status": "Success",
                     "CheckoutId": "idem_replay", "Amount": completed[0].get("amount", 1)},
        }
        wh = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook", json=payload)
        assert wh.status_code == 200
        assert wh.json().get("status") == "already_completed"
        assert _balance(user_session) == pytest.approx(bal_before, abs=0.01)
