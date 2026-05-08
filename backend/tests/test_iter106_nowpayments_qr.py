"""Iter106 Phase 1 — NowPayments USDT deposit + QR receive + QR scanner backend tests.

Covers:
- BUG #1: POST /api/wallet/deposit method=nowpayments_usdttrc20|usdtbsc → real /v1/payment
- BUG #1 follow-up: GET /api/wallet/deposit/{tx_id}/status auth-gated + NP status probe
- BUG #6: /api/users/me/qr-payload + /api/users/me/qr-code.png
- BUG #7: /api/users/resolve-qr (unchanged) – regression only
- Regression: hubtel_card still returns checkout_url; resolve-qr invalid → 400
"""
import os
import asyncio
from datetime import datetime, timedelta, timezone
import requests
import pytest
from dotenv import load_dotenv as _load_env

_load_env("/app/frontend/.env")
_load_env("/app/backend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
JWT_SECRET = os.environ["JWT_SECRET"]
DATABASE_URL = os.environ["DATABASE_URL"]


def _mint_token(user_id: str, email: str, minutes: int = 120) -> str:
    import jwt
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user_id, "email": email, "type": "access",
         "iat": int(now.timestamp()), "exp": now + timedelta(minutes=minutes)},
        JWT_SECRET, algorithm="HS256",
    )


async def _mint_for_email(email: str) -> str:
    import asyncpg
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        u = await conn.fetchrow("SELECT user_id, email FROM users WHERE email=$1", email)
        assert u, f"user {email} not found"
        return _mint_token(u["user_id"], u["email"])
    finally:
        await conn.close()


def _session_for(email: str) -> requests.Session:
    tok = asyncio.run(_mint_for_email(email))
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def bob_session():
    return _session_for("bob@japap.com")


@pytest.fixture(scope="module")
def alice_session():
    return _session_for("alice@japap.com")


@pytest.fixture(scope="module")
def admin_session():
    return _session_for("admin@japap.com")


# ── BUG #1: NowPayments deposit create ──────────────────────────────────────
class TestNowPaymentsDeposit:
    def _assert_np_payment_shape(self, body: dict, method: str):
        required = [
            "tx_id", "status", "method", "amount_usd",
            "payment_id", "payment_status",
            "pay_address", "pay_amount", "pay_currency",
            "price_amount", "price_currency",
            "expiration_estimate_date", "instruction",
        ]
        for k in required:
            assert k in body, f"missing key '{k}' in response: {list(body.keys())}"
        assert body["status"] == "pending"
        assert body["method"] == method
        # must NOT have checkout_url any more
        assert "checkout_url" not in body, "checkout_url must NOT be present for nowpayments_*"
        # non-empty provider fields
        assert body["payment_id"], "payment_id must be non-empty"
        assert body["pay_address"], "pay_address must be non-empty"
        assert str(body["pay_amount"]), "pay_amount must be non-empty"
        assert body["payment_status"] in (
            "waiting", "confirming", "confirmed", "sending", "partially_paid",
            "finished", "pending",
        ), f"unexpected payment_status={body['payment_status']}"
        # FR instruction with amount+currency hint
        assert "adresse" in body["instruction"].lower() or "envoyez" in body["instruction"].lower()

    def test_deposit_nowpayments_trc20(self, bob_session):
        r = bob_session.post(
            f"{API}/wallet/deposit",
            json={"amount": 15, "method": "nowpayments_usdttrc20", "notes": "TEST_iter106_trc20"},
            timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        body = r.json()
        self._assert_np_payment_shape(body, "nowpayments_usdttrc20")
        assert body["pay_currency"].lower().startswith("usdt")
        # Save tx_id for status poll
        pytest.np_trc20_tx_id = body["tx_id"]
        pytest.np_trc20_payment_id = body["payment_id"]

    def test_deposit_nowpayments_bsc(self, bob_session):
        r = bob_session.post(
            f"{API}/wallet/deposit",
            json={"amount": 15, "method": "nowpayments_usdtbsc", "notes": "TEST_iter106_bsc"},
            timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        body = r.json()
        self._assert_np_payment_shape(body, "nowpayments_usdtbsc")

    def test_deposit_nowpayments_unauth(self):
        r = requests.post(
            f"{API}/wallet/deposit",
            json={"amount": 5, "method": "nowpayments_usdttrc20"},
            timeout=15,
        )
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ── BUG #1 follow-up: status endpoint ───────────────────────────────────────
class TestDepositStatus:
    def test_status_unauth(self):
        r = requests.get(f"{API}/wallet/deposit/dep_doesnotexist/status", timeout=10)
        assert r.status_code in (401, 403)

    def test_status_missing(self, bob_session):
        r = bob_session.get(f"{API}/wallet/deposit/dep_not_there_xyz/status", timeout=10)
        assert r.status_code == 404, f"expected 404, got {r.status_code} {r.text[:200]}"

    def test_status_wrong_user(self, bob_session, alice_session):
        tx_id = getattr(pytest, "np_trc20_tx_id", None)
        if not tx_id:
            pytest.skip("no tx_id from Bob's NP deposit")
        r = alice_session.get(f"{API}/wallet/deposit/{tx_id}/status", timeout=10)
        assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text[:200]}"

    def test_status_pending_nowpayments(self, bob_session):
        tx_id = getattr(pytest, "np_trc20_tx_id", None)
        if not tx_id:
            pytest.skip("no tx_id from Bob's NP deposit")
        r = bob_session.get(f"{API}/wallet/deposit/{tx_id}/status", timeout=20)
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        body = r.json()
        for k in ("tx_id", "tx_status", "payment_status", "is_paid"):
            assert k in body
        assert body["tx_id"] == tx_id
        assert body["tx_status"] == "pending"
        assert body["is_paid"] is False
        # NP probe should return one of the waiting-family statuses
        assert body["payment_status"] in (
            "waiting", "confirming", "confirmed", "sending", "partially_paid",
            "finished", "pending", "expired", "failed",
        )


# ── BUG #1 env audit ────────────────────────────────────────────────────────
class TestNowPaymentsEnv:
    def test_test_connection(self, admin_session):
        r = admin_session.get(f"{API}/wallet/nowpayments/test-connection", timeout=30)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        data = r.json()
        assert data.get("ok") is True, f"NP connection not ok: {data}"
        assert data.get("environment") == "production"
        assert data.get("authenticated") is True
        assert data.get("liveness") is True


# ── BUG #6: QR receive ──────────────────────────────────────────────────────
class TestQRReceive:
    def test_qr_payload_auth(self, bob_session):
        r = bob_session.get(f"{API}/users/me/qr-payload", timeout=10)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        body = r.json()
        assert body.get("t") == "japap.pay"
        assert body.get("v") == 1
        assert body.get("uid")
        assert "name" in body
        assert "ccy" in body

    def test_qr_payload_unauth(self):
        r = requests.get(f"{API}/users/me/qr-payload", timeout=10)
        assert r.status_code in (401, 403)

    def test_qr_code_png_auth(self, bob_session):
        r = bob_session.get(f"{API}/users/me/qr-code.png", timeout=15)
        assert r.status_code == 200, f"{r.status_code} {r.text[:120]}"
        ct = r.headers.get("content-type", "")
        assert ct.startswith("image/png"), f"expected image/png, got {ct}"
        assert len(r.content) > 100, "png body too small"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG magic header"

    def test_qr_code_png_unauth(self):
        r = requests.get(f"{API}/users/me/qr-code.png", timeout=10)
        assert r.status_code in (401, 403)


# ── BUG #7 regression: resolve-qr ───────────────────────────────────────────
class TestResolveQR:
    def test_resolve_valid(self, bob_session, alice_session):
        # get alice's payload, then bob tries to resolve it
        p = alice_session.get(f"{API}/users/me/qr-payload", timeout=10)
        assert p.status_code == 200
        payload = p.json()
        r = bob_session.post(f"{API}/users/resolve-qr", json=payload, timeout=10)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        body = r.json()
        assert body.get("uid") == payload["uid"] or body.get("user_id") == payload["uid"] \
            or "name" in body

    def test_resolve_invalid_missing_fields(self, bob_session):
        r = bob_session.post(f"{API}/users/resolve-qr", json={"foo": "bar"}, timeout=10)
        assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text[:200]}"

    def test_resolve_invalid_type(self, bob_session):
        r = bob_session.post(
            f"{API}/users/resolve-qr",
            json={"t": "not.japap", "v": 1, "uid": "user_xxx"},
            timeout=10,
        )
        assert r.status_code in (400, 404), f"got {r.status_code} {r.text[:200]}"


# ── Regression: hubtel_card still works ─────────────────────────────────────
class TestHubtelRegression:
    def test_hubtel_card_deposit(self, bob_session):
        r = bob_session.post(
            f"{API}/wallet/deposit",
            json={"amount": 5, "method": "hubtel_card", "notes": "TEST_iter106_hubtel"},
            timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        body = r.json()
        assert body["status"] == "pending"
        assert body["method"] == "hubtel_card"
        assert "checkout_url" in body and body["checkout_url"], "hubtel must return checkout_url"
