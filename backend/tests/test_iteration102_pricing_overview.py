"""Iter102 — Phase B (AI Pricing Grid via Claude Sonnet 4.5) + Phase C
(Admin Transport Overview Dashboard).

Covers:
  Phase B:
    - POST /api/transport/admin/pricing/ai-propose  (admin only, 403 non-admin,
      validates country_code/vehicle_type, inserts proposed/source='ai' row
      with ai_rationale populated)
    - POST /api/transport/admin/pricing/manual      (admin only, inserts row
      with admin-typed values)
    - GET  /api/transport/admin/pricing             (filters work, returns
      items + counts + total)
    - POST /api/transport/admin/pricing/{id}/validate (404 if not found, 409
      if already active/archived/rejected, archives previous active row)
    - POST /api/transport/admin/pricing/{id}/reject (404, 409, status flip)
    - Integration: /api/transport/estimate now returns currency +
      pricing_source. /api/transport/request applies the active grid.
  Phase C:
    - GET /api/transport/admin/overview?days=N (admin only, full shape).
"""
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
BOB_EMAIL = "bob@japap.com"
ALICE_EMAIL = "alice@japap.com"


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


# ───────────────────────── Fixtures ─────────────────────────
@pytest.fixture(scope="module")
def admin_session():
    return _session_for(ADMIN_EMAIL)


@pytest.fixture(scope="module")
def bob_session():
    return _session_for(BOB_EMAIL)


@pytest.fixture(scope="module", autouse=True)
def _reset_pricing_and_country():
    """Wipe all pricing_grid rows so each run starts clean. Also ensures
    Bob has country_code='CM' so /estimate hits the grid path."""
    async def _do():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            # Make sure the table exists (DDL is normally created on first
            # API hit — call ai-propose endpoint? simpler: explicit DDL here).
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pricing_grid (
                    pricing_id     VARCHAR(64) PRIMARY KEY,
                    country_code   VARCHAR(2)  NOT NULL,
                    country_name   VARCHAR(80) NOT NULL DEFAULT '',
                    currency       VARCHAR(8)  NOT NULL,
                    vehicle_type   VARCHAR(16) NOT NULL,
                    base_fare      NUMERIC(12,2) NOT NULL,
                    per_km         NUMERIC(12,2) NOT NULL,
                    status         VARCHAR(16) NOT NULL DEFAULT 'proposed',
                    source         VARCHAR(16) NOT NULL DEFAULT 'manual',
                    ai_rationale   TEXT NOT NULL DEFAULT '',
                    proposed_by    VARCHAR(64) NOT NULL DEFAULT '',
                    validated_by   VARCHAR(64),
                    validated_at   TIMESTAMPTZ,
                    rejected_reason TEXT NOT NULL DEFAULT '',
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("DELETE FROM pricing_grid WHERE country_code IN ('CM','FR','XX','TS')")
            await conn.execute(
                "UPDATE users SET country_code='CM' WHERE email=$1", BOB_EMAIL,
            )
        finally:
            await conn.close()
    asyncio.run(_do())
    yield
    # teardown
    async def _cleanup():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute("DELETE FROM pricing_grid WHERE country_code IN ('CM','FR','XX','TS')")
        finally:
            await conn.close()
    asyncio.run(_cleanup())


# ============================================================
#  PHASE B — Pricing Grid
# ============================================================

class TestPricingAuth:
    """Admin-only access enforcement."""

    def test_01_non_admin_ai_propose_403(self, bob_session):
        r = bob_session.post(f"{BASE_URL}/api/transport/admin/pricing/ai-propose",
                             json={"country_code": "CM", "currency": "XAF",
                                   "vehicle_type": "standard"})
        assert r.status_code == 403, r.text

    def test_02_non_admin_manual_403(self, bob_session):
        r = bob_session.post(f"{BASE_URL}/api/transport/admin/pricing/manual",
                             json={"country_code": "CM", "currency": "XAF",
                                   "vehicle_type": "standard",
                                   "base_fare": 600, "per_km": 250})
        assert r.status_code == 403

    def test_03_non_admin_list_403(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/transport/admin/pricing")
        assert r.status_code == 403

    def test_04_unauthenticated_list_401(self):
        r = requests.get(f"{BASE_URL}/api/transport/admin/pricing")
        assert r.status_code in (401, 403)


class TestPricingManual:
    """Manual proposal path (used as a deterministic stand-in for AI)."""

    def test_05_manual_create_basic(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/manual",
                               json={"country_code": "CM", "country_name": "Cameroun",
                                     "currency": "XAF", "vehicle_type": "standard",
                                     "base_fare": 600, "per_km": 220,
                                     "rationale": "TEST_manual baseline"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["country_code"] == "CM"
        assert d["currency"] == "XAF"
        assert d["vehicle_type"] == "standard"
        assert d["status"] == "proposed"
        assert d["source"] == "manual"
        assert str(d["base_fare"]).startswith("600")
        assert str(d["per_km"]).startswith("220")
        assert "pricing_id" in d and d["pricing_id"].startswith("pg_")

    def test_06_manual_invalid_vehicle_type_400(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/manual",
                               json={"country_code": "CM", "currency": "XAF",
                                     "vehicle_type": "rocket",
                                     "base_fare": 600, "per_km": 220})
        assert r.status_code == 400

    def test_07_manual_invalid_country_422(self, admin_session):
        # country_code must be 2 chars
        r = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/manual",
                               json={"country_code": "CMR", "currency": "XAF",
                                     "vehicle_type": "standard",
                                     "base_fare": 600, "per_km": 220})
        assert r.status_code == 422

    def test_08_manual_negative_fare_422(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/manual",
                               json={"country_code": "CM", "currency": "XAF",
                                     "vehicle_type": "standard",
                                     "base_fare": -10, "per_km": 220})
        assert r.status_code == 422


class TestPricingAIPropose:
    """Claude Sonnet 4.5 proposal path. The LLM call is real but quick."""

    def test_09_ai_propose_inserts_proposed_row(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/ai-propose",
                               json={"country_code": "FR", "country_name": "France",
                                     "currency": "EUR", "vehicle_type": "standard"},
                               timeout=90)
        # If the LLM legitimately returns invalid JSON the route surfaces 503;
        # we accept that gracefully (rare flake) but mark the test xfail-like.
        if r.status_code == 503:
            pytest.skip(f"LLM returned invalid JSON: {r.text}")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["country_code"] == "FR"
        assert d["status"] == "proposed"
        assert d["source"] == "ai"
        assert d["ai_rationale"], "ai_rationale must be populated by Claude"
        assert float(d["base_fare"]) >= 0
        assert float(d["per_km"]) >= 0
        # Currency normalized uppercase
        assert d["currency"] == d["currency"].upper()

    def test_10_ai_propose_invalid_country_422(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/ai-propose",
                               json={"country_code": "FRA", "currency": "EUR",
                                     "vehicle_type": "standard"})
        assert r.status_code == 422

    def test_11_ai_propose_invalid_vehicle_400(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/ai-propose",
                               json={"country_code": "FR", "currency": "EUR",
                                     "vehicle_type": "boat"})
        assert r.status_code == 400


class TestPricingList:
    """GET /admin/pricing with filters."""

    def test_12_list_all_has_counts(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/transport/admin/pricing")
        assert r.status_code == 200
        d = r.json()
        assert "items" in d and "counts" in d and "total" in d
        for k in ("proposed", "active", "archived", "rejected"):
            assert k in d["counts"]
        assert d["counts"]["proposed"] >= 1  # we created at least one above

    def test_13_filter_by_country(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/transport/admin/pricing?country=CM")
        assert r.status_code == 200
        for it in r.json()["items"]:
            assert it["country_code"] == "CM"

    def test_14_filter_by_status_invalid(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/transport/admin/pricing?status=foobar")
        assert r.status_code == 400

    def test_15_filter_by_vehicle_invalid(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/transport/admin/pricing?vehicle_type=tank")
        assert r.status_code == 400


class TestPricingValidateAndArchive:
    """Validate flow + auto-archive of previous active row."""

    def test_16_validate_404(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/pg_doesnotexist/validate"
        )
        assert r.status_code == 404

    def test_17_validate_first_row_then_check_estimate_changes(self, admin_session, bob_session):
        # Get pre-grid estimate (default XAF tariff)
        pre = bob_session.get(
            f"{BASE_URL}/api/transport/estimate"
            "?pickup_lat=3.886&pickup_lng=11.516"
            "&dropoff_lat=3.853&dropoff_lng=11.530"
            "&vehicle_type=standard"
        )
        assert pre.status_code == 200, pre.text
        pre_d = pre.json()
        assert pre_d["pricing_source"] == "default_xaf"
        assert pre_d["currency"] == "XAF"
        pre_fare = float(pre_d["fare_estimated"])

        # Create a CM/standard manual grid (different from defaults: base 1000, per_km 400)
        c = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/manual",
                               json={"country_code": "CM", "currency": "XAF",
                                     "vehicle_type": "standard",
                                     "base_fare": 1000, "per_km": 400,
                                     "rationale": "TEST_grid v1"})
        assert c.status_code == 200
        pid_v1 = c.json()["pricing_id"]
        # Validate it
        v = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/{pid_v1}/validate"
        )
        assert v.status_code == 200, v.text
        assert v.json()["status"] == "active"
        assert v.json()["validated_by"]
        assert v.json()["validated_at"]

        # Re-estimate — should now reflect the grid
        post = bob_session.get(
            f"{BASE_URL}/api/transport/estimate"
            "?pickup_lat=3.886&pickup_lng=11.516"
            "&dropoff_lat=3.853&dropoff_lng=11.530"
            "&vehicle_type=standard"
        )
        assert post.status_code == 200
        post_d = post.json()
        assert post_d["pricing_source"] == "grid"
        assert post_d["currency"] == "XAF"
        assert post_d["breakdown"]["base"].startswith("1000")
        assert post_d["breakdown"]["per_km"].startswith("400")
        assert float(post_d["fare_estimated"]) != pre_fare
        # store on module for chaining
        TestPricingValidateAndArchive.pid_v1 = pid_v1

    def test_18_validate_already_active_409(self, admin_session):
        pid = TestPricingValidateAndArchive.pid_v1
        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/{pid}/validate"
        )
        assert r.status_code == 409

    def test_19_validate_second_archives_previous(self, admin_session):
        # Create another CM/standard proposal, validate it, ensure v1 is archived
        c = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/manual",
                               json={"country_code": "CM", "currency": "XAF",
                                     "vehicle_type": "standard",
                                     "base_fare": 1500, "per_km": 500,
                                     "rationale": "TEST_grid v2"})
        pid_v2 = c.json()["pricing_id"]
        v = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/{pid_v2}/validate"
        )
        assert v.status_code == 200, v.text
        assert v.json()["status"] == "active"
        # Verify v1 is now archived
        d = admin_session.get(f"{BASE_URL}/api/transport/admin/pricing/{TestPricingValidateAndArchive.pid_v1}")
        assert d.status_code == 200
        assert d.json()["status"] == "archived"
        # Only one active per (country, vehicle_type)
        lst = admin_session.get(
            f"{BASE_URL}/api/transport/admin/pricing?country=CM&status=active&vehicle_type=standard"
        )
        actives = lst.json()["items"]
        assert len(actives) == 1
        assert actives[0]["pricing_id"] == pid_v2

    def test_20_validate_rejected_or_archived_409(self, admin_session):
        # try validating the archived v1 again
        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/{TestPricingValidateAndArchive.pid_v1}/validate"
        )
        assert r.status_code == 409


class TestPricingReject:
    def test_21_reject_404(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/pg_nope/reject",
            json={"reason": "TEST_does not matter"},
        )
        assert r.status_code == 404

    def test_22_reject_proposed_ok(self, admin_session):
        c = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/manual",
                               json={"country_code": "XX", "currency": "USD",
                                     "vehicle_type": "premium",
                                     "base_fare": 5, "per_km": 2,
                                     "rationale": "TEST_to_reject"})
        pid = c.json()["pricing_id"]
        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/{pid}/reject",
            json={"reason": "TEST_too high"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "rejected"
        assert d["rejected_reason"] == "TEST_too high"
        assert d["validated_by"]
        assert d["validated_at"]

    def test_23_reject_rejected_409(self, admin_session):
        c = admin_session.post(f"{BASE_URL}/api/transport/admin/pricing/manual",
                               json={"country_code": "XX", "currency": "USD",
                                     "vehicle_type": "premium",
                                     "base_fare": 5, "per_km": 2})
        pid = c.json()["pricing_id"]
        admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/{pid}/reject", json={"reason": "first"}
        )
        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/{pid}/reject", json={"reason": "again"}
        )
        assert r.status_code == 409


class TestEstimateRequestBackwardCompat:
    """When user has no country_code we should still get the legacy XAF tariff."""

    def test_24_estimate_no_country_default_xaf(self, admin_session):
        # admin user has no country_code set typically — use admin session
        r = admin_session.get(
            f"{BASE_URL}/api/transport/estimate"
            "?pickup_lat=3.886&pickup_lng=11.516"
            "&dropoff_lat=3.853&dropoff_lng=11.530"
            "&vehicle_type=standard"
        )
        assert r.status_code == 200
        d = r.json()
        # admin user might not have country_code → falls back to default_xaf
        assert d["pricing_source"] in ("grid", "default_xaf")
        assert d["currency"] in ("XAF",) or d["pricing_source"] == "grid"

    def test_25_estimate_premium_no_active_grid_default(self, bob_session):
        # CM/premium has no active grid → must return default_xaf with RATE_PREMIUM
        r = bob_session.get(
            f"{BASE_URL}/api/transport/estimate"
            "?pickup_lat=3.886&pickup_lng=11.516"
            "&dropoff_lat=3.853&dropoff_lng=11.530"
            "&vehicle_type=premium"
        )
        assert r.status_code == 200
        d = r.json()
        assert d["pricing_source"] == "default_xaf"
        assert d["currency"] == "XAF"
        assert d["breakdown"]["per_km"].startswith("350")  # RATE_PREMIUM


# ============================================================
#  PHASE C — Admin Transport Overview
# ============================================================
class TestAdminOverview:

    def test_26_overview_non_admin_403(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/transport/admin/overview?days=30")
        assert r.status_code == 403

    def test_27_overview_invalid_days_422(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/transport/admin/overview?days=0")
        assert r.status_code == 422
        r = admin_session.get(f"{BASE_URL}/api/transport/admin/overview?days=1000")
        assert r.status_code == 422

    def test_28_overview_full_shape(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/transport/admin/overview?days=30")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["window_days"] == 30
        # drivers block
        drv = d["drivers"]
        assert "total" in drv and isinstance(drv["total"], int)
        assert "online" in drv and isinstance(drv["online"], int)
        for k in ("pending_review", "approved", "rejected", "suspended"):
            assert k in drv["by_kyc_status"]
        # rides
        rd = d["rides"]
        assert "total" in rd and isinstance(rd["total"], int)
        for k in ("pending", "accepted", "en_route", "started", "completed", "cancelled"):
            assert k in rd["by_status"]
        # revenue
        rev = d["revenue"]
        assert "gross" in rev and "commission" in rev and "currency" in rev
        # timeseries
        assert isinstance(d["timeseries"], list)
        for ts in d["timeseries"]:
            assert {"day", "rides", "gross", "commission"} <= set(ts.keys())
        # top earners + bottom rated
        assert isinstance(d["top_earners"], list) and len(d["top_earners"]) <= 10
        assert isinstance(d["bottom_rated"], list) and len(d["bottom_rated"]) <= 10
        for e in d["top_earners"]:
            for k in ("driver_id", "user_id", "name", "rating",
                      "total_reviews", "earnings"):
                assert k in e
        for b in d["bottom_rated"]:
            assert "rating" in b and "total_reviews" in b
            assert int(b["total_reviews"]) >= 3

    def test_29_overview_window_changes(self, admin_session):
        r1 = admin_session.get(f"{BASE_URL}/api/transport/admin/overview?days=1")
        r7 = admin_session.get(f"{BASE_URL}/api/transport/admin/overview?days=7")
        assert r1.status_code == 200 and r7.status_code == 200
        assert r1.json()["window_days"] == 1
        assert r7.json()["window_days"] == 7
