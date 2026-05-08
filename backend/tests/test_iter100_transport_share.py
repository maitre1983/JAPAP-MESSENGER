"""Iter100 — Phase 3 Transport share link (no-auth public /track/{token}).

Covers:
  - POST /api/transport/{ride_id}/share (rider + driver allowed, others 403,
    completed/cancelled → 400)
  - GET /api/transport/share/{token} (no auth, sanitised payload,
    410 expired, 400 invalid)
  - Regression: full lifecycle pending → accepted → en_route → started → completed
  - Regression: GET /api/transport/{ride_id} returns full payload incl. driver
    object and driver_position
"""
import os
import asyncio
import time
import pytest
import requests
import jwt
import asyncpg
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
DATABASE_URL = os.environ["DATABASE_URL"]
JWT_SECRET = os.environ["JWT_SECRET"]

ADMIN_EMAIL = "admin@japap.com"
ALICE_EMAIL = "alice@japap.com"
BOB_EMAIL = "bob@japap.com"

TEST_IMG = "/api/upload/files/test_license.webp"
PICKUP = (3.886, 11.516)
DROPOFF = (3.853, 11.530)


def _mint(uid, email, minutes=180):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": uid, "email": email, "type": "access",
         "iat": int(now.timestamp()), "exp": now + timedelta(minutes=minutes)},
        JWT_SECRET, algorithm="HS256",
    )


async def _mint_for(email):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        u = await conn.fetchrow("SELECT user_id, email FROM users WHERE email=$1", email)
        assert u, f"user {email} not found"
        return u["user_id"], _mint(u["user_id"], u["email"])
    finally:
        await conn.close()


def _session_for(email):
    uid, token = asyncio.run(_mint_for(email))
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    s.user_id = uid
    return s


@pytest.fixture(scope="module")
def admin_session():
    return _session_for(ADMIN_EMAIL)


@pytest.fixture(scope="module")
def alice_session():
    return _session_for(ALICE_EMAIL)


@pytest.fixture(scope="module")
def bob_session():
    return _session_for(BOB_EMAIL)


def _driver_payload():
    return {
        "vehicle_model": "Toyota Corolla",
        "vehicle_plate": "LT 123 AB",
        "vehicle_type": "standard",
        "personal_phone": "+237670000111",
        "emergency_contact_name": "Jean Dupont",
        "emergency_contact_phone": "+237670000222",
        "license_number": "CMR-2024-100",
        "license_issue_date": "2020-05-12",
        "license_image_url": TEST_IMG,
        "id_card_image_url": TEST_IMG,
        "selfie_with_license_url": TEST_IMG,
        "country_code": "CM",
    }


async def _reset():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT user_id FROM users WHERE email IN ('alice@japap.com','bob@japap.com')"
        )
        ids = [r["user_id"] for r in rows]
        if ids:
            await conn.execute(
                "DELETE FROM ride_requests WHERE rider_id=ANY($1::text[]) OR driver_id=ANY($1::text[])",
                ids,
            )
            await conn.execute(
                "DELETE FROM notifications WHERE user_id=ANY($1::text[]) AND type LIKE 'ride_%'", ids,
            )
            await conn.execute(
                "DELETE FROM driver_kyc_decisions WHERE user_id=ANY($1::text[])", ids,
            )
            await conn.execute("DELETE FROM drivers WHERE user_id=ANY($1::text[])", ids)
        # Ensure Bob wallet has funds
        bob_id = await conn.fetchval(
            "SELECT user_id FROM users WHERE email='bob@japap.com'")
        if bob_id:
            w = await conn.fetchval("SELECT 1 FROM wallets WHERE user_id=$1", bob_id)
            if w:
                await conn.execute("UPDATE wallets SET balance=50000 WHERE user_id=$1", bob_id)
            else:
                await conn.execute(
                    "INSERT INTO wallets (user_id, balance, currency) VALUES ($1, 50000, 'XAF')",
                    bob_id,
                )
    finally:
        await conn.close()


@pytest.fixture(scope="module", autouse=True)
def _prepare(admin_session, alice_session):
    asyncio.run(_reset())
    try:
        requests.get(f"{BASE_URL}/api/health", timeout=60)
    except Exception:
        pass
    # Register + approve Alice as driver
    last = None
    for _ in range(3):
        try:
            r = alice_session.post(f"{BASE_URL}/api/transport/driver/register",
                                   json=_driver_payload(), timeout=90)
            last = r
            if r.status_code == 200:
                break
        except requests.exceptions.RequestException as e:
            last = e
    assert hasattr(last, "status_code") and last.status_code == 200, getattr(last, "text", str(last))
    lst = admin_session.get(f"{BASE_URL}/api/transport/admin/drivers?status=pending_review&limit=200").json()
    did = None
    for it in lst["items"]:
        if it["user"]["email"] == ALICE_EMAIL:
            did = it["driver_id"]
            break
    assert did, "Alice driver not in pending list"
    ap = admin_session.post(f"{BASE_URL}/api/transport/admin/drivers/{did}/approve", json={})
    assert ap.status_code == 200
    yield


# Module-scoped shared state for the ride id used across tests
@pytest.fixture(scope="module")
def ride_state():
    return {"ride_id": None}


# ────────────────── Share endpoint (auth) ──────────────────
class TestShareMint:
    def test_01_create_ride(self, bob_session, ride_state):
        p = {"pickup_address": "Bastos", "dropoff_address": "Mvog-Mbi",
             "pickup_lat": PICKUP[0], "pickup_lng": PICKUP[1],
             "dropoff_lat": DROPOFF[0], "dropoff_lng": DROPOFF[1],
             "vehicle_type": "standard", "notes": "test"}
        r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
        assert r.status_code == 200, r.text
        ride_state["ride_id"] = r.json()["ride_id"]

    def test_02_share_rider_pending(self, bob_session, ride_state):
        r = bob_session.post(f"{BASE_URL}/api/transport/{ride_state['ride_id']}/share")
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("token", "url", "expires_at", "ttl_hours"):
            assert k in d, f"missing {k}"
        assert d["ttl_hours"] == 12
        assert f"/track/{d['token']}" in d["url"]
        ride_state["token"] = d["token"]
        ride_state["url"] = d["url"]

    def test_03_share_denies_stranger(self, admin_session, ride_state):
        r = admin_session.post(f"{BASE_URL}/api/transport/{ride_state['ride_id']}/share")
        assert r.status_code == 403

    def test_04_share_404_unknown_ride(self, bob_session):
        r = bob_session.post(f"{BASE_URL}/api/transport/ride_does_not_exist/share")
        assert r.status_code == 404

    def test_05_accept_then_driver_can_share(self, alice_session, ride_state):
        a = alice_session.post(f"{BASE_URL}/api/transport/{ride_state['ride_id']}/accept")
        assert a.status_code == 200
        r = alice_session.post(f"{BASE_URL}/api/transport/{ride_state['ride_id']}/share")
        assert r.status_code == 200
        assert "token" in r.json()


# ────────────────── Public no-auth endpoint ──────────────────
class TestSharePublic:
    def test_10_public_get_sanitised(self, ride_state):
        # Intentionally a plain requests call — no auth header.
        r = requests.get(f"{BASE_URL}/api/transport/share/{ride_state['token']}")
        assert r.status_code == 200, r.text
        d = r.json()
        # Rider info is sanitised: first name only, no email/full name
        assert "rider_first_name" in d and d["rider_first_name"]
        assert "rider_email" not in d
        assert "rider_full_name" not in d
        # No fare / payment info
        for forbidden in ("fare", "fare_estimated", "payment", "rider_id"):
            assert forbidden not in d, f"{forbidden} leaked into public payload"
        # Driver block present since ride has been accepted
        assert d.get("driver") and d["driver"]["vehicle_plate"] == "LT 123 AB"
        assert d["status"] in ("accepted", "pending")
        # Timeline fields
        for k in ("created_at", "accepted_at", "en_route_at", "started_at",
                  "completed_at", "cancelled_at"):
            assert k in d

    def test_11_public_invalid_token_400(self):
        r = requests.get(f"{BASE_URL}/api/transport/share/not-a-jwt")
        assert r.status_code == 400

    def test_12_public_expired_410(self):
        now = datetime.now(timezone.utc)
        payload = {
            "rid": "ride_anything",
            "iat": int((now - timedelta(hours=13)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
            "aud": "japap.ride.share",
        }
        expired = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        r = requests.get(f"{BASE_URL}/api/transport/share/{expired}")
        assert r.status_code == 410

    def test_13_public_404_missing_ride(self):
        # Valid token shape but ride_id does not exist
        now = datetime.now(timezone.utc)
        payload = {
            "rid": "ride_does_not_exist_xyz",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "aud": "japap.ride.share",
        }
        ghost = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        r = requests.get(f"{BASE_URL}/api/transport/share/{ghost}")
        assert r.status_code == 404

    def test_14_public_wrong_audience_400(self):
        now = datetime.now(timezone.utc)
        payload = {
            "rid": "ride_anything",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "aud": "wrong.audience",
        }
        bad = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        r = requests.get(f"{BASE_URL}/api/transport/share/{bad}")
        assert r.status_code == 400


# ────────────────── Lifecycle + detail regression ──────────────────
class TestLifecycleRegression:
    def test_20_en_route(self, alice_session, ride_state):
        r = alice_session.post(
            f"{BASE_URL}/api/transport/{ride_state['ride_id']}/en-route",
            json={"driver_lat": 3.88, "driver_lng": 11.52},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "en_route"

    def test_21_position_ping(self, alice_session, ride_state):
        r = alice_session.post(
            f"{BASE_URL}/api/transport/{ride_state['ride_id']}/position",
            json={"driver_lat": 3.87, "driver_lng": 11.52},
        )
        assert r.status_code == 200

    def test_22_detail_has_driver_and_position(self, bob_session, ride_state):
        r = bob_session.get(f"{BASE_URL}/api/transport/{ride_state['ride_id']}")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "en_route"
        assert d.get("driver") and d["driver"].get("vehicle_model")
        assert d.get("driver_position") is not None
        assert "lat" in d["driver_position"]

    def test_23_public_shows_driver_position(self, ride_state):
        r = requests.get(f"{BASE_URL}/api/transport/share/{ride_state['token']}")
        assert r.status_code == 200
        d = r.json()
        assert d.get("driver_position") is not None
        assert d["status"] == "en_route"

    def test_24_start(self, alice_session, ride_state):
        r = alice_session.post(
            f"{BASE_URL}/api/transport/{ride_state['ride_id']}/start",
            json={"driver_lat": 3.86, "driver_lng": 11.52},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "started"

    def test_25_complete(self, alice_session, bob_session, ride_state):
        r = alice_session.post(
            f"{BASE_URL}/api/transport/{ride_state['ride_id']}/complete")
        assert r.status_code == 200, r.text
        # /complete returns {fare, ...}; verify via detail GET
        det = bob_session.get(f"{BASE_URL}/api/transport/{ride_state['ride_id']}")
        assert det.status_code == 200
        assert det.json()["status"] == "completed"

    def test_26_share_refused_on_completed_400(self, bob_session, ride_state):
        r = bob_session.post(f"{BASE_URL}/api/transport/{ride_state['ride_id']}/share")
        assert r.status_code == 400

    def test_27_public_after_completed_still_ok(self, ride_state):
        # Existing token should still return the ride (timeline + completed)
        r = requests.get(f"{BASE_URL}/api/transport/share/{ride_state['token']}")
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_28_share_refused_on_cancelled_400(self, bob_session, admin_session, alice_session):
        # Create fresh ride then cancel it
        p = {"pickup_address": "Bastos", "dropoff_address": "Mvog-Mbi",
             "pickup_lat": PICKUP[0], "pickup_lng": PICKUP[1],
             "dropoff_lat": DROPOFF[0], "dropoff_lng": DROPOFF[1]}
        r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
        assert r.status_code == 200, r.text
        rid = r.json()["ride_id"]
        c = bob_session.post(f"{BASE_URL}/api/transport/{rid}/cancel")
        assert c.status_code == 200
        s = bob_session.post(f"{BASE_URL}/api/transport/{rid}/share")
        assert s.status_code == 400
