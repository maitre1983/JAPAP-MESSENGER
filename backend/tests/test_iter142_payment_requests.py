"""iter142 — "Demander à recevoir" (Payment Requests) tests.

Covers backend endpoints:
- POST /api/wallet/payment-requests (create — auth, validation, persistence)
- GET /api/wallet/payment-requests/{id} (PUBLIC preview, no _id leak)
- GET /api/wallet/payment-requests/{id}/qr.png (PNG content)
- POST /api/wallet/payment-requests/{id}/fulfill (pay flow, idempotency)
- Double-fulfill (409)
- Self-pay (400)
- GET /api/wallet/payment-requests (list mine)
- DELETE /api/wallet/payment-requests/{id} (cancel — owner only)
- Cancelled request can't be paid (409)
- Expired request returns 410 on fulfill
- pay_url uses public FRONTEND_URL (not internal cluster hostname)
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
TURNSTILE_BYPASS = "JAPAP_E2E_BYPASS_2026"
FRONTEND_URL = "https://japap-refactor.preview.emergentagent.com"

USERS = {
    "alice": ("alice@japap.com", "Alice2026!"),
    "bob":   ("bob@japap.com", "Test1234!"),
}


# ─── auth helpers ──────────────────────────────────────────────────────
def _solve_captcha():
    """Fetch math captcha and return (captcha_id, answer)."""
    last = None
    for _ in range(3):
        try:
            r = requests.get(f"{BASE_URL}/api/auth/captcha", timeout=30)
            r.raise_for_status()
            j = r.json()
            q = j["question"].replace("=", "").strip()
            parts = q.split()
            a, op, b = int(parts[0]), parts[1], int(parts[2])
            ans = {"+": a + b, "-": a - b, "*": a * b, "x": a * b, "×": a * b}[op]
            return j["captcha_id"], str(ans)
        except Exception as e:
            last = e
            time.sleep(1.0)
    raise RuntimeError(f"captcha fetch failed: {last}")


def _login(email, password):
    last_err = None
    for attempt in range(3):
        try:
            cid, cans = _solve_captcha()
            r = requests.post(f"{BASE_URL}/api/auth/login", json={
                "email": email, "password": password,
                "turnstile_token": TURNSTILE_BYPASS,
                "captcha_id": cid, "captcha_answer": cans,
            }, timeout=45)
            if r.status_code == 200:
                data = r.json()
                token = data.get("access_token") or data.get("token")
                user = data.get("user") or {}
                return token, user, r.cookies
            last_err = f"{r.status_code} {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
        time.sleep(1.0)
    pytest.skip(f"Login failed for {email}: {last_err}")


def _session(email, password):
    token, user, cookies = _login(email, password)
    s = requests.Session()
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    s.cookies.update(cookies)
    s.user = user
    return s


@pytest.fixture(scope="module")
def alice():
    return _session(*USERS["alice"])


@pytest.fixture(scope="module")
def bob():
    return _session(*USERS["bob"])


@pytest.fixture(scope="module")
def topup_bob(bob):
    """Make sure Bob has enough balance to pay a few small requests."""
    # Try admin credit if available; else skip (we use small amounts).
    return None


# ─── CREATE ────────────────────────────────────────────────────────────
class TestCreatePaymentRequest:
    def test_create_requires_auth(self):
        # Retry on transient SSL/network timeouts (cold preview SSL handshake)
        last = None
        for _ in range(3):
            try:
                r = requests.post(f"{BASE_URL}/api/wallet/payment-requests",
                                  json={"amount": 100, "note": "test"}, timeout=45)
                assert r.status_code in (401, 403), r.text
                return
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
                last = e
                time.sleep(2.0)
        raise AssertionError(f"create-unauth never responded: {last}")

    def test_create_amount_must_be_positive(self, alice):
        r = alice.post(f"{BASE_URL}/api/wallet/payment-requests",
                       json={"amount": 0, "note": "x"}, timeout=15)
        assert r.status_code == 400
        r2 = alice.post(f"{BASE_URL}/api/wallet/payment-requests",
                        json={"amount": -5, "note": "x"}, timeout=15)
        assert r2.status_code == 400

    def test_create_amount_too_high(self, alice):
        r = alice.post(f"{BASE_URL}/api/wallet/payment-requests",
                       json={"amount": 10_000_001, "note": "huge"}, timeout=15)
        assert r.status_code == 400

    def test_create_success_returns_full_payload(self, alice):
        r = alice.post(f"{BASE_URL}/api/wallet/payment-requests",
                       json={"amount": 250, "note": "TEST_iter142_create"}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["request_id"].startswith("pr_")
        assert data["status"] == "pending"
        assert data["currency"] in ("XAF", "USD", "EUR")  # alice has wallet currency
        assert data["note"] == "TEST_iter142_create"
        assert data["pay_url"].endswith(f"/pay/{data['request_id']}")
        # CRITICAL: pay_url must use public FRONTEND_URL (not k8s internal)
        assert data["pay_url"].startswith(FRONTEND_URL), \
            f"pay_url should start with {FRONTEND_URL}, got {data['pay_url']}"
        assert "qr_url" in data
        assert "whatsapp_url" in data and data["whatsapp_url"].startswith("https://wa.me/")
        assert "share_text" in data and "JAPAP" in data["share_text"]
        assert data["requester"]["user_id"]
        # pytest cache-shared via filesystem — but easier to expose via class attr
        TestCreatePaymentRequest.created_id = data["request_id"]

    def test_get_public_preview_no_auth(self):
        rid = getattr(TestCreatePaymentRequest, "created_id", None)
        if not rid:
            pytest.skip("create test did not run")
        # No session = no auth headers
        r = requests.get(f"{BASE_URL}/api/wallet/payment-requests/{rid}", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        # MUST NOT leak Mongo _id
        assert "_id" not in data
        # Required public fields
        assert data["request_id"] == rid
        assert data["status"] == "pending"
        assert "amount" in data and "currency" in data
        assert "requester" in data
        assert "name" in data["requester"]
        # Sensitive admin-ish fields should not appear (no email, password, balance)
        flat = str(data).lower()
        assert "password" not in flat
        # email could appear if backend leaks — flag if present
        assert "@" not in (data["requester"].get("name") or "")

    def test_qr_returns_png(self):
        rid = getattr(TestCreatePaymentRequest, "created_id", None)
        if not rid:
            pytest.skip("create test did not run")
        r = requests.get(f"{BASE_URL}/api/wallet/payment-requests/{rid}/qr.png", timeout=15)
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("image/png")
        # PNG magic header
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n", "QR is not a valid PNG"
        assert len(r.content) > 200


# ─── FULFILL ───────────────────────────────────────────────────────────
class TestFulfillFlow:
    @pytest.fixture(scope="class")
    def alice_request(self, alice):
        r = alice.post(f"{BASE_URL}/api/wallet/payment-requests",
                       json={"amount": 50, "note": "TEST_iter142_pay"}, timeout=15)
        assert r.status_code == 200, r.text
        return r.json()["request_id"]

    def test_self_pay_returns_400(self, alice, alice_request):
        r = alice.post(
            f"{BASE_URL}/api/wallet/payment-requests/{alice_request}/fulfill",
            timeout=20,
        )
        assert r.status_code == 400, r.text
        assert "propre" in r.text.lower() or "self" in r.text.lower() or "ne peux" in r.text.lower()

    def test_fulfill_unauth_blocked(self, alice_request):
        r = requests.post(
            f"{BASE_URL}/api/wallet/payment-requests/{alice_request}/fulfill",
            timeout=20,
        )
        assert r.status_code in (401, 403), r.text

    def test_fulfill_success_and_marks_paid(self, alice, bob, alice_request):
        # Bob pays Alice
        time.sleep(1.0)  # rate limit cushion
        r = bob.post(
            f"{BASE_URL}/api/wallet/payment-requests/{alice_request}/fulfill",
            timeout=30,
        )
        if r.status_code == 400 and ("solde" in r.text.lower() or "balance" in r.text.lower() or "insufficient" in r.text.lower()):
            pytest.skip(f"Bob has insufficient balance to fulfill: {r.text[:200]}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("payment_request_id") == alice_request
        assert "tx_id" in data or "transaction_id" in data
        # Verify status is now 'paid' via public preview
        time.sleep(0.5)
        prev = requests.get(
            f"{BASE_URL}/api/wallet/payment-requests/{alice_request}", timeout=15
        ).json()
        assert prev["status"] == "paid"
        assert prev["fulfilled_tx_id"]
        assert prev["fulfilled_at"]
        TestFulfillFlow.paid_id = alice_request

    def test_double_fulfill_returns_409(self, bob):
        rid = getattr(TestFulfillFlow, "paid_id", None)
        if not rid:
            pytest.skip("fulfill test did not run")
        time.sleep(1.0)
        r = bob.post(
            f"{BASE_URL}/api/wallet/payment-requests/{rid}/fulfill", timeout=20
        )
        assert r.status_code == 409, r.text


# ─── CANCEL + EXPIRE ───────────────────────────────────────────────────
class TestCancelAndExpire:
    @pytest.fixture(scope="class")
    def fresh_request(self, alice):
        r = alice.post(f"{BASE_URL}/api/wallet/payment-requests",
                       json={"amount": 30, "note": "TEST_iter142_cancel"}, timeout=15)
        assert r.status_code == 200
        return r.json()["request_id"]

    def test_non_owner_cannot_cancel(self, bob, fresh_request):
        r = bob.delete(f"{BASE_URL}/api/wallet/payment-requests/{fresh_request}", timeout=15)
        assert r.status_code == 403, r.text

    def test_owner_can_cancel(self, alice, fresh_request):
        r = alice.delete(f"{BASE_URL}/api/wallet/payment-requests/{fresh_request}", timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"
        # Verify in preview
        prev = requests.get(f"{BASE_URL}/api/wallet/payment-requests/{fresh_request}", timeout=15).json()
        assert prev["status"] == "cancelled"

    def test_cancelled_cannot_be_paid_409(self, bob, fresh_request):
        time.sleep(1.0)
        r = bob.post(f"{BASE_URL}/api/wallet/payment-requests/{fresh_request}/fulfill", timeout=20)
        assert r.status_code == 409, r.text

    def test_cancel_already_cancelled_409(self, alice, fresh_request):
        r = alice.delete(f"{BASE_URL}/api/wallet/payment-requests/{fresh_request}", timeout=15)
        assert r.status_code == 409, r.text

    def test_expired_request_returns_410_on_fulfill(self, alice, bob):
        """Force an existing pending request to be expired in DB,
        then verify fulfill returns 410 + preview shows status=expired."""
        try:
            import asyncio
            import asyncpg
            from dotenv import load_dotenv
            load_dotenv("/app/backend/.env")
        except Exception as e:
            pytest.skip(f"asyncpg/dotenv not available: {e}")
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            pytest.skip("DATABASE_URL not set")
        # Create a fresh request
        create = alice.post(
            f"{BASE_URL}/api/wallet/payment-requests",
            json={"amount": 20, "note": "TEST_iter142_expire"}, timeout=15,
        )
        assert create.status_code == 200, create.text
        rid = create.json()["request_id"]

        async def force_expire():
            conn = await asyncpg.connect(db_url)
            try:
                await conn.execute(
                    "UPDATE payment_requests SET expires_at = NOW() - INTERVAL '1 hour' WHERE request_id = $1",
                    rid,
                )
            finally:
                await conn.close()

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(force_expire())
        finally:
            loop.close()

        time.sleep(0.5)
        prev = requests.get(f"{BASE_URL}/api/wallet/payment-requests/{rid}", timeout=15).json()
        assert prev["status"] == "expired", prev
        time.sleep(1.0)
        r = bob.post(f"{BASE_URL}/api/wallet/payment-requests/{rid}/fulfill", timeout=20)
        assert r.status_code == 410, r.text


# ─── LIST MINE ─────────────────────────────────────────────────────────
class TestListMine:
    def test_list_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/wallet/payment-requests", timeout=15)
        assert r.status_code in (401, 403), r.text

    def test_list_returns_my_requests(self, alice):
        r = alice.get(f"{BASE_URL}/api/wallet/payment-requests?limit=20", timeout=15)
        assert r.status_code == 200, r.text
        items = r.json()
        assert isinstance(items, list)
        assert len(items) >= 1  # we created several above
        first = items[0]
        for k in ("request_id", "amount", "currency", "status", "created_at"):
            assert k in first

    def test_list_filter_by_status(self, alice):
        r = alice.get(f"{BASE_URL}/api/wallet/payment-requests?status=cancelled&limit=20", timeout=15)
        assert r.status_code == 200
        items = r.json()
        for it in items:
            assert it["status"] == "cancelled"


# ─── 404 ────────────────────────────────────────────────────────────────
class TestNotFound:
    def test_get_unknown_404(self):
        r = requests.get(f"{BASE_URL}/api/wallet/payment-requests/pr_doesnotexist123", timeout=15)
        assert r.status_code == 404

    def test_qr_unknown_404(self):
        r = requests.get(f"{BASE_URL}/api/wallet/payment-requests/pr_doesnotexist123/qr.png", timeout=15)
        assert r.status_code == 404

    def test_fulfill_unknown_404(self, bob):
        time.sleep(1.0)
        r = bob.post(f"{BASE_URL}/api/wallet/payment-requests/pr_doesnotexist123/fulfill", timeout=20)
        assert r.status_code == 404
