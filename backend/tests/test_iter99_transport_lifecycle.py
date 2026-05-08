"""Iter99 — Transport Phase 2: GPS validation + full ride lifecycle + heatmap."""
import os
import asyncio
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
ALICE_EMAIL = "alice@japap.com"   # driver
BOB_EMAIL = "bob@japap.com"       # rider

TEST_IMG = "/api/upload/files/test_license.webp"

# Yaoundé GPS points
PICKUP = (3.886, 11.516)    # Bastos
DROPOFF = (3.853, 11.530)   # Mvog-Mbi


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


def _valid_driver_payload():
    return {
        "vehicle_model": "Toyota Corolla",
        "vehicle_plate": "LT 123 AB",
        "vehicle_type": "standard",
        "personal_phone": "+237670000111",
        "emergency_contact_name": "Jean Dupont",
        "emergency_contact_phone": "+237670000222",
        "license_number": "CMR-2024-001",
        "license_issue_date": "2020-05-12",
        "license_image_url": TEST_IMG,
        "id_card_image_url": TEST_IMG,
        "selfie_with_license_url": TEST_IMG,
        "country_code": "CM",
    }


async def _setup():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT user_id, email FROM users WHERE email IN ('alice@japap.com','bob@japap.com')"
        )
        ids = {r["email"]: r["user_id"] for r in rows}
        alice_id = ids.get("alice@japap.com")
        bob_id = ids.get("bob@japap.com")
        # Cleanup previous state
        if alice_id and bob_id:
            await conn.execute(
                "DELETE FROM ride_requests WHERE rider_id=ANY($1::text[]) OR driver_id=ANY($1::text[])",
                [alice_id, bob_id],
            )
            await conn.execute(
                "DELETE FROM notifications WHERE user_id=ANY($1::text[]) AND type LIKE 'ride_%'",
                [alice_id, bob_id],
            )
            await conn.execute(
                "DELETE FROM driver_kyc_decisions WHERE user_id=ANY($1::text[])", [alice_id, bob_id]
            )
            await conn.execute(
                "DELETE FROM drivers WHERE user_id=ANY($1::text[])", [alice_id, bob_id]
            )
        # Ensure Bob wallet has funds
        if bob_id:
            w = await conn.fetchval("SELECT 1 FROM wallets WHERE user_id=$1", bob_id)
            if w:
                await conn.execute(
                    "UPDATE wallets SET balance=50000 WHERE user_id=$1", bob_id
                )
            else:
                await conn.execute(
                    "INSERT INTO wallets (user_id, balance, currency) VALUES ($1, 50000, 'XAF')",
                    bob_id,
                )
        return alice_id, bob_id
    finally:
        await conn.close()


async def _teardown():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT user_id FROM users WHERE email IN ('alice@japap.com','bob@japap.com')"
        )
        ids = [r["user_id"] for r in rows]
        if ids:
            await conn.execute(
                "DELETE FROM ride_requests WHERE rider_id=ANY($1::text[]) OR driver_id=ANY($1::text[])", ids
            )
            await conn.execute(
                "DELETE FROM notifications WHERE user_id=ANY($1::text[]) AND type LIKE 'ride_%'", ids
            )
            await conn.execute(
                "DELETE FROM driver_kyc_decisions WHERE user_id=ANY($1::text[])", ids
            )
            await conn.execute(
                "DELETE FROM drivers WHERE user_id=ANY($1::text[])", ids
            )
    finally:
        await conn.close()


@pytest.fixture(scope="module", autouse=True)
def _prepare(admin_session, alice_session):
    alice_id, bob_id = asyncio.run(_setup())
    # Warm-up the API
    try:
        requests.get(f"{BASE_URL}/api/health", timeout=60)
    except Exception:
        pass
    # Register Alice as driver (retry on transient 502/timeout)
    last = None
    for _ in range(3):
        try:
            r = alice_session.post(f"{BASE_URL}/api/transport/driver/register",
                                   json=_valid_driver_payload(), timeout=90)
            last = r
            if r.status_code == 200:
                break
        except requests.exceptions.RequestException as e:
            last = e
    assert hasattr(last, "status_code") and last.status_code == 200, getattr(last, "text", str(last))
    # Admin approves Alice
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
    asyncio.run(_teardown())


# ─────────── GPS validation on /request ───────────
class TestGPSValidation:
    def test_invalid_lat_999(self, bob_session):
        p = {"pickup_address": "A", "dropoff_address": "B",
             "pickup_lat": 999, "pickup_lng": 11.5,
             "dropoff_lat": 3.85, "dropoff_lng": 11.5}
        r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
        assert r.status_code == 400
        assert "invalides" in r.json()["detail"].lower()

    def test_invalid_nan(self, bob_session):
        # JSON doesn't allow NaN; use a huge out-of-range lng as equivalent
        p = {"pickup_address": "A", "dropoff_address": "B",
             "pickup_lat": 3.88, "pickup_lng": 11.5,
             "dropoff_lat": 3.85, "dropoff_lng": 999}
        r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
        assert r.status_code == 400

    def test_distance_too_short(self, bob_session):
        p = {"pickup_address": "A", "dropoff_address": "B",
             "pickup_lat": 3.886, "pickup_lng": 11.516,
             "dropoff_lat": 3.886, "dropoff_lng": 11.516}
        r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
        assert r.status_code == 400
        assert "court" in r.json()["detail"].lower()

    def test_distance_too_long(self, bob_session):
        # Yaoundé → far away (>500 km)
        p = {"pickup_address": "A", "dropoff_address": "B",
             "pickup_lat": 3.886, "pickup_lng": 11.516,
             "dropoff_lat": 14.0, "dropoff_lng": 15.0}
        r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
        assert r.status_code == 400
        assert "long" in r.json()["detail"].lower()


# ─────────── Full lifecycle happy path ───────────
@pytest.fixture(scope="module")
def ride_state():
    return {"ride_id": None}


class TestLifecycle:
    def test_1_create_valid_ride(self, bob_session, ride_state):
        p = {"pickup_address": "Bastos", "dropoff_address": "Mvog-Mbi",
             "pickup_lat": PICKUP[0], "pickup_lng": PICKUP[1],
             "dropoff_lat": DROPOFF[0], "dropoff_lng": DROPOFF[1],
             "vehicle_type": "standard", "notes": "test"}
        r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ride_id"].startswith("ride_")
        assert d["status"] == "pending"
        assert isinstance(d["pickup_h3"], str) and len(d["pickup_h3"]) == 15
        assert d["pickup_h3"].startswith("89")
        assert isinstance(d["dropoff_h3"], str) and len(d["dropoff_h3"]) == 15
        assert d["dropoff_h3"].startswith("89")
        ride_state["ride_id"] = d["ride_id"]

    def test_2_duplicate_active_ride_409(self, bob_session):
        p = {"pickup_address": "Bastos", "dropoff_address": "Mvog-Mbi",
             "pickup_lat": PICKUP[0], "pickup_lng": PICKUP[1],
             "dropoff_lat": DROPOFF[0], "dropoff_lng": DROPOFF[1]}
        r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
        assert r.status_code == 409
        assert "course en cours" in r.json()["detail"].lower()

    def test_3_accept_by_rider_400(self, bob_session, ride_state):
        # Rider tries to accept own ride — Bob is not a driver → 403 first
        r = bob_session.post(f"{BASE_URL}/api/transport/{ride_state['ride_id']}/accept")
        assert r.status_code == 403

    def test_4_accept_by_driver(self, alice_session, ride_state):
        r = alice_session.post(f"{BASE_URL}/api/transport/{ride_state['ride_id']}/accept")
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"

    def test_5_complete_before_start_409(self, alice_session, ride_state):
        r = alice_session.post(f"{BASE_URL}/api/transport/{ride_state['ride_id']}/complete")
        assert r.status_code == 409
        assert "terminer" in r.json()["detail"].lower() or "démarré" in r.json()["detail"].lower()

    def test_6_en_route_ok(self, alice_session, ride_state):
        r = alice_session.post(
            f"{BASE_URL}/api/transport/{ride_state['ride_id']}/en-route",
            json={"driver_lat": 3.88, "driver_lng": 11.52},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "en_route"

    def test_7_en_route_twice_409(self, alice_session, ride_state):
        r = alice_session.post(
            f"{BASE_URL}/api/transport/{ride_state['ride_id']}/en-route",
            json={},
        )
        assert r.status_code == 409

    def test_8_position_ping(self, alice_session, ride_state):
        r = alice_session.post(
            f"{BASE_URL}/api/transport/{ride_state['ride_id']}/position",
            json={"driver_lat": 3.87, "driver_lng": 11.52},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_9_position_wrong_driver_403(self, bob_session, ride_state):
        r = bob_session.post(
            f"{BASE_URL}/api/transport/{ride_state['ride_id']}/position",
            json={"driver_lat": 3.87, "driver_lng": 11.52},
        )
        assert r.status_code == 403

    def test_10_start_ride(self, alice_session, ride_state):
        r = alice_session.post(
            f"{BASE_URL}/api/transport/{ride_state['ride_id']}/start",
            json={"driver_lat": 3.86, "driver_lng": 11.52},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "started"

    def test_11_cancel_after_start_409(self, bob_session, ride_state):
        r = bob_session.post(f"{BASE_URL}/api/transport/{ride_state['ride_id']}/cancel")
        assert r.status_code == 409
        assert "non annulable" in r.json()["detail"].lower()

    def test_12_complete_from_started_and_wallet_flow(self, alice_session, bob_session, ride_state):
        # Snapshot wallet balances via DB directly
        async def _balances():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                bob_bal = await conn.fetchval(
                    "SELECT balance FROM wallets WHERE user_id=(SELECT user_id FROM users WHERE email='bob@japap.com')"
                )
                alice_bal = await conn.fetchval(
                    "SELECT balance FROM wallets WHERE user_id=(SELECT user_id FROM users WHERE email='alice@japap.com')"
                )
                return float(bob_bal or 0), float(alice_bal or 0)
            finally:
                await conn.close()
        bob_before, alice_before = asyncio.run(_balances())

        r = alice_session.post(f"{BASE_URL}/api/transport/{ride_state['ride_id']}/complete")
        assert r.status_code == 200, r.text
        data = r.json()
        fare = float(data["fare"])

        bob_after, alice_after = asyncio.run(_balances())
        assert bob_after == pytest.approx(bob_before - fare, abs=0.01)
        assert alice_after > alice_before  # received net share


# ─────────── Ride detail / cancel path / access control ───────────
class TestDetailAndCancel:
    def test_detail_full_payload(self, bob_session, ride_state):
        r = bob_session.get(f"{BASE_URL}/api/transport/{ride_state['ride_id']}")
        assert r.status_code == 200
        d = r.json()
        for k in ["status", "distance_km", "fare_estimated", "pickup_h3",
                  "dropoff_h3", "accepted_at", "en_route_at", "started_at",
                  "completed_at", "rider", "driver"]:
            assert k in d, f"missing {k}"
        assert d["status"] == "completed"
        assert d["driver"]["vehicle_model"]
        assert d["driver_position"] is not None

    def test_detail_access_denied_for_others(self, admin_session, ride_state):
        # admin is neither rider nor driver
        r = admin_session.get(f"{BASE_URL}/api/transport/{ride_state['ride_id']}")
        assert r.status_code == 403

    def test_cancel_pending_ride_ok(self, bob_session):
        p = {"pickup_address": "A", "dropoff_address": "B",
             "pickup_lat": PICKUP[0], "pickup_lng": PICKUP[1],
             "dropoff_lat": DROPOFF[0], "dropoff_lng": DROPOFF[1]}
        r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
        assert r.status_code == 200, r.text
        rid = r.json()["ride_id"]
        c = bob_session.post(f"{BASE_URL}/api/transport/{rid}/cancel")
        assert c.status_code == 200
        assert c.json()["status"] == "cancelled"


# ─────────── Admin heatmap ───────────
class TestHeatmap:
    def test_heatmap_pickup(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/transport/admin/heatmap?days=7&kind=pickup")
        assert r.status_code == 200
        d = r.json()
        assert "cells" in d and "total" in d
        assert d["kind"] == "pickup"
        # We created at least one ride during this module run
        assert d["total"] >= 1
        c0 = d["cells"][0]
        assert isinstance(c0["cell"], str) and c0["cell"].startswith("89")
        assert isinstance(c0["count"], int) and c0["count"] >= 1
        assert isinstance(c0["lat"], float) and isinstance(c0["lng"], float)

    def test_heatmap_dropoff(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/transport/admin/heatmap?days=7&kind=dropoff")
        assert r.status_code == 200
        assert r.json()["kind"] == "dropoff"

    def test_heatmap_non_admin_403(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/transport/admin/heatmap")
        assert r.status_code == 403


# ─────────── Schema audit ───────────
class TestSchemaAudit:
    def test_ride_requests_columns(self):
        async def _check():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='ride_requests'"
                )
                cols = {r["column_name"] for r in rows}
                required = {
                    "pickup_address", "dropoff_address",
                    "pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng",
                    "distance_km", "fare_estimated", "notes",
                    "pickup_h3", "dropoff_h3",
                    "en_route_at", "started_at",
                    "driver_lat", "driver_lng", "driver_position_at",
                }
                missing = required - cols
                assert not missing, f"missing columns: {missing}"
            finally:
                await conn.close()
        asyncio.run(_check())

    def test_driver_fk_dropped(self):
        async def _check():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchval(
                    """SELECT 1 FROM information_schema.table_constraints
                         WHERE table_name='ride_requests'
                           AND constraint_name='ride_requests_driver_id_fkey'"""
                )
                assert row is None, "ride_requests_driver_id_fkey should be dropped"
            finally:
                await conn.close()
        asyncio.run(_check())
