"""Iter101 — Phase A: Driver Rating & Review System

Covers:
  - POST /api/transport/{ride_id}/review
      • rider can post a 1-5 stars + comment only when ride.status=completed
      • idempotent (409 on 2nd POST for same ride)
      • 403 if caller is not the rider
      • 404 if ride missing
      • Pydantic validation on rating range (422 for 0 / 6)
      • Rolling avg + total_reviews recomputed atomically on drivers table
  - GET /api/transport/{ride_id}/review
      • Returns {ride_status, can_submit, review:null|{...}}
      • Both rider and driver may read; stranger gets 403
      • can_submit=true ONLY when completed + user=rider + review is null
  - GET /api/transport/driver/{driver_id}/reviews (paginated public list)
      • summary:{average,total,histogram:{1..5}}, items[]
  - GET /api/transport/admin/drivers/{driver_id} — includes 'reviews' block
  - Regression of iter100 share/lifecycle still green (reused fixtures).
"""
import os
import asyncio
import pytest
import requests
import jwt
import asyncpg
from datetime import datetime, timezone, timedelta
from decimal import Decimal
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
            # ride_reviews must be cleaned before rides (or cascade doesn't exist)
            try:
                await conn.execute(
                    "DELETE FROM ride_reviews WHERE rider_id=ANY($1::text[]) OR driver_id=ANY($1::text[])",
                    ids,
                )
            except asyncpg.UndefinedTableError:
                pass
            await conn.execute(
                "DELETE FROM ride_requests WHERE rider_id=ANY($1::text[]) OR driver_id=ANY($1::text[])",
                ids,
            )
            await conn.execute(
                "DELETE FROM notifications WHERE user_id=ANY($1::text[]) AND type LIKE 'ride_%'", ids,
            )
            await conn.execute(
                "DELETE FROM notifications WHERE user_id=ANY($1::text[]) AND type='driver_review'", ids,
            )
            await conn.execute(
                "DELETE FROM driver_kyc_decisions WHERE user_id=ANY($1::text[])", ids,
            )
            await conn.execute("DELETE FROM drivers WHERE user_id=ANY($1::text[])", ids)
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


async def _db_driver_row(email):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchrow(
            """SELECT d.driver_id, d.user_id, d.rating, d.total_reviews
                 FROM drivers d JOIN users u ON d.user_id=u.user_id WHERE u.email=$1""",
            email,
        )
    finally:
        await conn.close()


def _run_lifecycle(bob_session, alice_session):
    """Create a ride (Bob) → accept/en-route/start/complete (Alice). Return ride_id."""
    p = {"pickup_address": "Bastos", "dropoff_address": "Mvog-Mbi",
         "pickup_lat": PICKUP[0], "pickup_lng": PICKUP[1],
         "dropoff_lat": DROPOFF[0], "dropoff_lng": DROPOFF[1],
         "vehicle_type": "standard", "notes": "rev-test"}
    r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
    assert r.status_code == 200, r.text
    rid = r.json()["ride_id"]
    assert alice_session.post(f"{BASE_URL}/api/transport/{rid}/accept").status_code == 200
    assert alice_session.post(f"{BASE_URL}/api/transport/{rid}/en-route", json={}).status_code == 200
    assert alice_session.post(f"{BASE_URL}/api/transport/{rid}/start", json={}).status_code == 200
    cp = alice_session.post(f"{BASE_URL}/api/transport/{rid}/complete")
    assert cp.status_code == 200, cp.text
    return rid


@pytest.fixture(scope="module", autouse=True)
def _prepare(admin_session, alice_session):
    asyncio.run(_reset())
    try:
        requests.get(f"{BASE_URL}/api/health", timeout=60)
    except Exception:
        pass
    r = alice_session.post(
        f"{BASE_URL}/api/transport/driver/register", json=_driver_payload(), timeout=90
    )
    assert r.status_code == 200, r.text
    lst = admin_session.get(
        f"{BASE_URL}/api/transport/admin/drivers?status=pending_review&limit=200"
    ).json()
    did = next((it["driver_id"] for it in lst["items"] if it["user"]["email"] == ALICE_EMAIL), None)
    assert did, "Alice driver not in pending list"
    ap = admin_session.post(f"{BASE_URL}/api/transport/admin/drivers/{did}/approve", json={})
    assert ap.status_code == 200
    yield


@pytest.fixture(scope="module")
def state():
    return {}


# ───────────────── POST review — core & error paths ─────────────────
class TestSubmitReview:
    def test_01_before_complete_409_not_completed(self, bob_session, alice_session, state):
        # Create ride, leave in pending — rider should not be able to review
        p = {"pickup_address": "Bastos", "dropoff_address": "Mvog-Mbi",
             "pickup_lat": PICKUP[0], "pickup_lng": PICKUP[1],
             "dropoff_lat": DROPOFF[0], "dropoff_lng": DROPOFF[1]}
        r = bob_session.post(f"{BASE_URL}/api/transport/request", json=p)
        assert r.status_code == 200
        pending_rid = r.json()["ride_id"]
        rv = bob_session.post(
            f"{BASE_URL}/api/transport/{pending_rid}/review",
            json={"rating": 4, "comment": "too early"},
        )
        assert rv.status_code == 409, rv.text
        # Clean it up so the next ride can be created (unique active constraint)
        assert bob_session.post(f"{BASE_URL}/api/transport/{pending_rid}/cancel").status_code == 200

    def test_02_404_unknown_ride(self, bob_session):
        r = bob_session.post(
            f"{BASE_URL}/api/transport/ride_does_not_exist/review",
            json={"rating": 5, "comment": "ghost"},
        )
        assert r.status_code == 404

    def test_03_full_lifecycle_and_rider_posts_4_stars(
        self, bob_session, alice_session, state
    ):
        rid = _run_lifecycle(bob_session, alice_session)
        state["rid1"] = rid
        # Snapshot Alice BEFORE review — confirm her seed rating is 5.00 / total_reviews=0
        pre = asyncio.run(_db_driver_row(ALICE_EMAIL))
        assert pre is not None
        assert int(pre["total_reviews"]) == 0
        assert Decimal(pre["rating"]) == Decimal("5.00")
        state["driver_id"] = pre["driver_id"]

        rv = bob_session.post(
            f"{BASE_URL}/api/transport/{rid}/review",
            json={"rating": 4, "comment": "Bonne conduite"},
        )
        assert rv.status_code == 200, rv.text
        body = rv.json()
        assert body["rating"] == 4
        assert body["comment"] == "Bonne conduite"
        assert body["driver_total_reviews"] == 1
        # Rolling avg must equal the single rating (4.00), flipping from 5.00
        assert Decimal(body["driver_avg_rating"]) == Decimal("4.00")

        # Confirm persisted in DB
        post = asyncio.run(_db_driver_row(ALICE_EMAIL))
        assert int(post["total_reviews"]) == 1
        assert Decimal(post["rating"]) == Decimal("4.00")

    def test_04_idempotent_409_on_second_post(self, bob_session, state):
        rv = bob_session.post(
            f"{BASE_URL}/api/transport/{state['rid1']}/review",
            json={"rating": 3, "comment": "retry"},
        )
        assert rv.status_code == 409, rv.text

    def test_05_403_when_not_rider(self, alice_session, admin_session, state):
        # Alice is the driver — she may NOT submit (only rider)
        rv = alice_session.post(
            f"{BASE_URL}/api/transport/{state['rid1']}/review",
            json={"rating": 1, "comment": "self-rate"},
        )
        assert rv.status_code == 403
        # Admin is neither rider nor driver → 403
        rv2 = admin_session.post(
            f"{BASE_URL}/api/transport/{state['rid1']}/review",
            json={"rating": 2, "comment": "not-rider"},
        )
        assert rv2.status_code == 403

    def test_06_pydantic_rating_out_of_range(self, bob_session, alice_session, state):
        # Use a fresh, non-reviewed completed ride to avoid 409
        rid = _run_lifecycle(bob_session, alice_session)
        state["rid2"] = rid
        for bad in (0, 6, -1):
            r = bob_session.post(
                f"{BASE_URL}/api/transport/{rid}/review",
                json={"rating": bad, "comment": "bad"},
            )
            assert r.status_code == 422, f"rating={bad} → {r.status_code} / {r.text}"

    def test_07_second_review_recomputes_rolling_avg(self, bob_session, state):
        # rid2 was created in test_06 — still completed, no review yet
        rv = bob_session.post(
            f"{BASE_URL}/api/transport/{state['rid2']}/review",
            json={"rating": 2, "comment": "ok"},
        )
        assert rv.status_code == 200, rv.text
        body = rv.json()
        # (4 + 2) / 2 = 3.00 — the drivers.rating column is numeric(3,2) so
        # the service returns the DB-rounded string.
        assert body["driver_total_reviews"] == 2
        assert Decimal(body["driver_avg_rating"]) == Decimal("3.00")
        post = asyncio.run(_db_driver_row(ALICE_EMAIL))
        assert int(post["total_reviews"]) == 2
        assert Decimal(post["rating"]) == Decimal("3.00")


# ───────────────── GET /{ride_id}/review ─────────────────
class TestGetRideReview:
    def test_10_rider_reads_review(self, bob_session, state):
        r = bob_session.get(f"{BASE_URL}/api/transport/{state['rid1']}/review")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ride_status"] == "completed"
        assert d["can_submit"] is False  # already submitted
        assert d["review"] is not None
        assert d["review"]["rating"] == 4
        assert d["review"]["comment"] == "Bonne conduite"

    def test_11_driver_may_read(self, alice_session, state):
        r = alice_session.get(f"{BASE_URL}/api/transport/{state['rid1']}/review")
        assert r.status_code == 200
        d = r.json()
        # Driver is never allowed to submit
        assert d["can_submit"] is False
        assert d["review"]["rating"] == 4

    def test_12_stranger_forbidden(self, admin_session, state):
        r = admin_session.get(f"{BASE_URL}/api/transport/{state['rid1']}/review")
        assert r.status_code == 403

    def test_13_404_missing_ride(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/transport/ride_does_not_exist/review")
        assert r.status_code == 404

    def test_14_can_submit_true_only_when_all_conditions_met(
        self, bob_session, alice_session
    ):
        rid = _run_lifecycle(bob_session, alice_session)
        # Rider on a completed, un-reviewed ride → can_submit=True
        r = bob_session.get(f"{BASE_URL}/api/transport/{rid}/review")
        assert r.status_code == 200
        d = r.json()
        assert d["ride_status"] == "completed"
        assert d["can_submit"] is True
        assert d["review"] is None
        # Driver on the same ride → can_submit=False even though review is None
        r2 = alice_session.get(f"{BASE_URL}/api/transport/{rid}/review")
        assert r2.status_code == 200
        assert r2.json()["can_submit"] is False


# ───────────────── GET /driver/{driver_id}/reviews ─────────────────
class TestDriverReviewsPublic:
    def test_20_list_summary_and_histogram(self, bob_session, state):
        r = bob_session.get(
            f"{BASE_URL}/api/transport/driver/{state['driver_id']}/reviews?limit=10&offset=0"
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["driver_id"] == state["driver_id"]
        assert d["limit"] == 10 and d["offset"] == 0
        s = d["summary"]
        assert int(s["total"]) == 2
        assert Decimal(s["average"]) == Decimal("3.00")
        # Histogram must carry all 5 buckets and sum to total
        h = s["histogram"]
        assert set(h.keys()) == {"1", "2", "3", "4", "5"}
        assert h["4"] == 1 and h["2"] == 1
        assert sum(int(v) for v in h.values()) == 2
        # Items — newest first, first name only (no last_name / email)
        assert len(d["items"]) == 2
        for it in d["items"]:
            for forbidden in ("rider_last_name", "rider_email", "rider_id"):
                assert forbidden not in it
            assert it["rider_first_name"]
            assert it["rating"] in (1, 2, 3, 4, 5)

    def test_21_pagination(self, bob_session, state):
        r = bob_session.get(
            f"{BASE_URL}/api/transport/driver/{state['driver_id']}/reviews?limit=1&offset=0"
        )
        assert r.status_code == 200
        d = r.json()
        assert len(d["items"]) == 1
        # Summary stays global regardless of paging window
        assert int(d["summary"]["total"]) == 2

    def test_22_404_unknown_driver(self, bob_session):
        r = bob_session.get(
            f"{BASE_URL}/api/transport/driver/drv_doesnotexist/reviews"
        )
        assert r.status_code == 404


# ───────────────── GET /admin/drivers/{driver_id} ─────────────────
class TestAdminDriverDetailIncludesReviews:
    def test_30_reviews_block_present(self, admin_session, state):
        r = admin_session.get(
            f"{BASE_URL}/api/transport/admin/drivers/{state['driver_id']}"
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert "reviews" in d, "Missing `reviews` block on admin driver detail"
        rv = d["reviews"]
        assert "summary" in rv and "recent" in rv
        assert int(rv["summary"]["total"]) == 2
        assert Decimal(rv["summary"]["average"]) == Decimal("3.00")
        # Last 5 most recent — we have 2, newest first
        assert 1 <= len(rv["recent"]) <= 5
        assert rv["recent"][0]["rating"] in (2, 4)
        for item in rv["recent"]:
            assert "rider_first_name" in item
            assert "created_at" in item

    def test_31_non_admin_forbidden(self, bob_session, state):
        r = bob_session.get(
            f"{BASE_URL}/api/transport/admin/drivers/{state['driver_id']}"
        )
        assert r.status_code == 403
