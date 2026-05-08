"""Iter105 — Dynamic Surge Pricing Engine (Phase D).

Tests the 5-layer multiplier engine:
  - GET  /api/transport/admin/surge/config
  - PUT  /api/transport/admin/surge/config (merge semantics)
  - GET  /api/transport/admin/surge/preview
  - GET  /api/transport/admin/surge/history
  - Integration: /api/transport/estimate (fare_low..fare_high + surge_label)
  - Integration: /api/transport/request (snapshot + history log + wallet check)
  - Cap enforcement, layer toggles, enabled=false short-circuit
"""
import os
import asyncio
import json
import uuid
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
BOB_EMAIL = "bob@japap.com"
ALICE_EMAIL = "alice@japap.com"

TEST_COUNTRY = "CM"
PICKUP_LAT = 3.848
PICKUP_LNG = 11.5021
DROPOFF_LAT = 3.870
DROPOFF_LNG = 11.520


# ─────────────────────────── Auth helpers ───────────────────────────
def _mint(uid, email, minutes=180):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": uid, "email": email, "type": "access",
         "iat": int(now.timestamp()),
         "exp": now + timedelta(minutes=minutes)},
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
    s.headers.update({"Authorization": f"Bearer {token}",
                      "Content-Type": "application/json"})
    s.user_id = uid
    return s


# ─────────────────────────── DB helpers ───────────────────────────
async def _wipe():
    """Clean surge_config/history + audit log rows for the test country."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Make sure tables exist (call backend once before this — done in fixture)
        await conn.execute(
            "DELETE FROM surge_config WHERE country_code = $1", TEST_COUNTRY
        )
        await conn.execute(
            "DELETE FROM surge_history WHERE country_code = $1", TEST_COUNTRY
        )
        await conn.execute(
            "DELETE FROM admin_audit_log WHERE action = 'transport.surge.config_update'"
        )
        # Bob: country_code=CM + healthy wallet
        u = await conn.fetchrow("SELECT user_id FROM users WHERE email=$1", BOB_EMAIL)
        if u:
            await conn.execute(
                "UPDATE users SET country_code='CM' WHERE user_id=$1", u["user_id"]
            )
            await conn.execute(
                """INSERT INTO wallets (user_id, balance) VALUES ($1, 50000)
                     ON CONFLICT (user_id) DO UPDATE SET balance = 50000""",
                u["user_id"],
            )
            # Cancel any in-progress rides so /request 409 guard does not fire
            await conn.execute(
                """UPDATE ride_requests SET status='cancelled', cancelled_at=NOW()
                     WHERE rider_id=$1
                       AND status IN ('pending','accepted','en_route','started')""",
                u["user_id"],
            )
        # Wipe ride_requests in the pickup H3 cell so demand/urban/traffic baseline
        # is zero between tests (we don't touch other cells).
        from services.transport_geo import h3_cell as _h3c
        cell = _h3c(PICKUP_LAT, PICKUP_LNG)
        await conn.execute(
            "DELETE FROM ride_requests WHERE pickup_h3=$1", cell,
        )
    finally:
        await conn.close()


@pytest.fixture(scope="module", autouse=True)
def _bootstrap():
    # Trigger DDL by hitting any admin endpoint once
    s = _session_for(ADMIN_EMAIL)
    try:
        s.get(f"{BASE_URL}/api/transport/admin/surge/config?country=CM", timeout=60)
    except Exception:
        pass  # cold-start; per-test calls will retry
    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


@pytest.fixture(autouse=True)
def _per_test_wipe():
    asyncio.run(_wipe())
    yield


@pytest.fixture
def admin():
    return _session_for(ADMIN_EMAIL)


@pytest.fixture
def bob():
    return _session_for(BOB_EMAIL)


@pytest.fixture
def pickup_cell():
    from services.transport_geo import h3_cell as _h3c
    return _h3c(PICKUP_LAT, PICKUP_LNG)


# Helper to PUT a config that makes label_threshold tiny so any uplift triggers
def _put_cfg(admin, **overrides):
    body = {"country_code": TEST_COUNTRY, "config": overrides}
    r = admin.put(f"{BASE_URL}/api/transport/admin/surge/config",
                  data=json.dumps(body), timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


# ════════════════════════════ TESTS ════════════════════════════════
# ───── Admin gating ─────
class TestAuthGating:
    def test_get_config_unauth_401(self):
        r = requests.get(f"{BASE_URL}/api/transport/admin/surge/config?country=CM",
                         timeout=15)
        assert r.status_code == 401, r.text

    def test_get_config_non_admin_403(self, bob):
        r = bob.get(f"{BASE_URL}/api/transport/admin/surge/config?country=CM",
                    timeout=15)
        assert r.status_code == 403, r.text

    def test_put_config_non_admin_403(self, bob):
        r = bob.put(f"{BASE_URL}/api/transport/admin/surge/config",
                    data=json.dumps({"country_code": "CM", "config": {}}), timeout=15)
        assert r.status_code == 403

    def test_preview_non_admin_403(self, bob):
        r = bob.get(f"{BASE_URL}/api/transport/admin/surge/preview"
                    f"?country=CM&pickup_lat={PICKUP_LAT}&pickup_lng={PICKUP_LNG}",
                    timeout=15)
        assert r.status_code == 403

    def test_history_non_admin_403(self, bob):
        r = bob.get(f"{BASE_URL}/api/transport/admin/surge/history?country=CM",
                    timeout=15)
        assert r.status_code == 403


# ───── GET /admin/surge/config ─────
class TestGetConfig:
    def test_returns_country_config_defaults(self, admin):
        r = admin.get(f"{BASE_URL}/api/transport/admin/surge/config?country=CM",
                      timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["country_code"] == "CM"
        assert isinstance(d["config"], dict)
        assert isinstance(d["defaults"], dict)
        # Default keys must be present
        for k in ("enabled", "max_surge", "time_enabled", "peak_morning_start",
                  "label_threshold", "premium_uplift"):
            assert k in d["config"]
            assert k in d["defaults"]


# ───── PUT /admin/surge/config (merge) ─────
class TestPutConfig:
    def test_partial_merge_does_not_wipe_other_keys(self, admin):
        # 1. Establish a full custom config
        _put_cfg(admin, max_surge=2.5, peak_uplift=0.45, label_threshold=1.10)
        # 2. PATCH only max_surge — peak_uplift must remain 0.45
        r = admin.put(
            f"{BASE_URL}/api/transport/admin/surge/config",
            data=json.dumps({"country_code": "CM", "config": {"max_surge": 1.5}}),
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok"
        assert body["country_code"] == "CM"
        assert float(body["config"]["max_surge"]) == 1.5
        assert float(body["config"]["peak_uplift"]) == 0.45
        assert float(body["config"]["label_threshold"]) == 1.10
        # 3. GET again to confirm persistence
        g = admin.get(f"{BASE_URL}/api/transport/admin/surge/config?country=CM",
                      timeout=15).json()
        assert float(g["config"]["max_surge"]) == 1.5
        assert float(g["config"]["peak_uplift"]) == 0.45

    def test_audit_log_inserted(self, admin):
        _put_cfg(admin, max_surge=1.7)
        # Verify audit row exists
        async def _check():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    "SELECT action, actor_id FROM admin_audit_log "
                    "WHERE action='transport.surge.config_update' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
                return row
            finally:
                await conn.close()
        row = asyncio.run(_check())
        assert row is not None, "audit row not inserted"
        assert row["action"] == "transport.surge.config_update"


# ───── GET /admin/surge/preview ─────
class TestPreview:
    def test_returns_full_breakdown(self, admin):
        # Force label by making threshold tiny + premium adds 0.10
        _put_cfg(admin, label_threshold=1.05, time_enabled=False,
                 demand_enabled=False, urban_enabled=False, traffic_enabled=False)
        r = admin.get(
            f"{BASE_URL}/api/transport/admin/surge/preview"
            f"?country=CM&pickup_lat={PICKUP_LAT}&pickup_lng={PICKUP_LNG}"
            f"&vehicle_type=premium",
            timeout=15,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("multiplier", "raw_multiplier", "label", "factors",
                  "details", "h3_cell", "config_used"):
            assert k in d, f"missing key {k}"
        for k in ("time", "demand", "urban", "traffic", "vehicle"):
            assert k in d["factors"]
        for k in ("time_band", "demand_supply_ratio", "urban_score", "traffic_kmh"):
            assert k in d["details"]
        # Only vehicle layer remains -> mult = 1.10 -> label "Forte demande"
        assert abs(d["factors"]["vehicle"] - 0.10) < 1e-3
        assert d["multiplier"] >= 1.10
        assert d["label"] == "Forte demande"

    def test_disabled_engine_returns_1(self, admin):
        _put_cfg(admin, enabled=False)
        r = admin.get(
            f"{BASE_URL}/api/transport/admin/surge/preview"
            f"?country=CM&pickup_lat={PICKUP_LAT}&pickup_lng={PICKUP_LNG}"
            f"&vehicle_type=premium",
            timeout=15,
        )
        assert r.status_code == 200
        d = r.json()
        assert d["multiplier"] == 1.0
        assert d["label"] == ""

    def test_layer_toggles_zero_factor(self, admin):
        # All layers off + standard vehicle -> multiplier == 1.0, label empty
        _put_cfg(admin, time_enabled=False, demand_enabled=False,
                 urban_enabled=False, traffic_enabled=False, label_threshold=1.20)
        r = admin.get(
            f"{BASE_URL}/api/transport/admin/surge/preview"
            f"?country=CM&pickup_lat={PICKUP_LAT}&pickup_lng={PICKUP_LNG}"
            f"&vehicle_type=standard",
            timeout=15,
        )
        d = r.json()
        for k in ("time", "demand", "urban", "traffic", "vehicle"):
            assert d["factors"][k] == 0.0, f"layer {k} should be zero"
        assert d["multiplier"] == 1.0
        assert d["label"] == ""

    def test_cap_enforcement(self, admin):
        # Stack many layers that would compute high; but max_surge=1.5 caps.
        _put_cfg(admin, max_surge=1.5, label_threshold=1.05,
                 time_enabled=False, demand_enabled=False,
                 urban_enabled=False, traffic_enabled=False,
                 premium_uplift=2.00)  # would push raw to 3.0
        r = admin.get(
            f"{BASE_URL}/api/transport/admin/surge/preview"
            f"?country=CM&pickup_lat={PICKUP_LAT}&pickup_lng={PICKUP_LNG}"
            f"&vehicle_type=premium",
            timeout=15,
        )
        d = r.json()
        assert d["raw_multiplier"] >= 2.5
        assert d["multiplier"] == 1.5  # capped
        assert d["label"] == "Forte demande"

    def test_invalid_coords_400(self, admin):
        r = admin.get(
            f"{BASE_URL}/api/transport/admin/surge/preview"
            f"?country=CM&pickup_lat=999&pickup_lng=999",
            timeout=15,
        )
        assert r.status_code == 400


# ───── Integration /estimate ─────
class TestEstimateIntegration:
    def test_returns_range_and_label(self, admin, bob):
        # Force label via tiny threshold + premium uplift
        _put_cfg(admin, label_threshold=1.05, max_surge=2.0,
                 time_enabled=False, demand_enabled=False,
                 urban_enabled=False, traffic_enabled=False,
                 premium_uplift=0.10)
        r = bob.get(
            f"{BASE_URL}/api/transport/estimate"
            f"?pickup_lat={PICKUP_LAT}&pickup_lng={PICKUP_LNG}"
            f"&dropoff_lat={DROPOFF_LAT}&dropoff_lng={DROPOFF_LNG}"
            f"&vehicle_type=premium",
            timeout=15,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("fare_low", "fare_high", "fare_estimated", "currency",
                  "surge_label", "surge_applied"):
            assert k in d
        # Backward compat: fare_estimated == fare_low
        assert d["fare_estimated"] == d["fare_low"]
        # fare_high >= fare_low
        assert Decimal(d["fare_high"]) >= Decimal(d["fare_low"])
        assert d["surge_applied"] is True
        assert d["surge_label"] == "Forte demande"

    def test_no_surge_no_label(self, admin, bob):
        _put_cfg(admin, enabled=False)
        r = bob.get(
            f"{BASE_URL}/api/transport/estimate"
            f"?pickup_lat={PICKUP_LAT}&pickup_lng={PICKUP_LNG}"
            f"&dropoff_lat={DROPOFF_LAT}&dropoff_lng={DROPOFF_LNG}"
            f"&vehicle_type=standard",
            timeout=15,
        )
        d = r.json()
        assert d["surge_label"] == ""
        assert d["surge_applied"] is False
        assert d["fare_estimated"] == d["fare_low"]


# ───── Integration /request ─────
class TestRequestIntegration:
    def test_request_snapshots_surge_and_logs_history(self, admin, bob, pickup_cell):
        # 1.5x via premium=0.50, all other layers off
        _put_cfg(admin, max_surge=2.0, label_threshold=1.20,
                 time_enabled=False, demand_enabled=False,
                 urban_enabled=False, traffic_enabled=False,
                 premium_uplift=0.50)
        body = {
            "pickup_address": "Test pickup", "dropoff_address": "Test dropoff",
            "pickup_lat": PICKUP_LAT, "pickup_lng": PICKUP_LNG,
            "dropoff_lat": DROPOFF_LAT, "dropoff_lng": DROPOFF_LNG,
            "vehicle_type": "premium",
        }
        r = bob.post(f"{BASE_URL}/api/transport/request",
                     data=json.dumps(body), timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "pending"
        assert float(d["surge_multiplier"]) == 1.5
        assert d["surge_label"] == "Forte demande"
        # final fare = base × 1.5
        assert Decimal(d["fare_estimated"]) == (Decimal(d["base_fare"]) * Decimal("1.5")).quantize(Decimal("1"))
        ride_id = d["ride_id"]

        # Verify ride_requests row carries surge_multiplier+label
        async def _check():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rr = await conn.fetchrow(
                    "SELECT surge_multiplier, surge_label FROM ride_requests "
                    "WHERE ride_id=$1", ride_id,
                )
                hist = await conn.fetchrow(
                    "SELECT final_multiplier, base_fare, final_fare, vehicle_factor "
                    "FROM surge_history WHERE ride_id=$1", ride_id,
                )
                return rr, hist
            finally:
                await conn.close()
        rr, hist = asyncio.run(_check())
        assert rr is not None
        assert float(rr["surge_multiplier"]) == 1.5
        assert rr["surge_label"] == "Forte demande"
        # surge_history row inserted
        assert hist is not None, "surge_history row missing"
        assert float(hist["final_multiplier"]) == 1.5
        assert float(hist["vehicle_factor"]) == 0.50

    def test_wallet_check_uses_surged_fare(self, admin, bob):
        # Force surge to push final fare above wallet (50000).
        # base ~ 500 + 200 * dist (~2.4km) = ~980 standard. We need ×100+ ⇒
        # use a tiny wallet instead. Override Bob's wallet to a small balance.
        async def _set_wallet(amount):
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                u = await conn.fetchrow(
                    "SELECT user_id FROM users WHERE email=$1", BOB_EMAIL
                )
                await conn.execute(
                    "UPDATE wallets SET balance=$1 WHERE user_id=$2",
                    amount, u["user_id"],
                )
            finally:
                await conn.close()
        # Set wallet just above base, below surged
        # Compute base fare (haversine ~ 2.4km) with default grid: 500 + 2.4*200=980
        # Set wallet=1000 (covers base) but with 1.5x multiplier needs 1470 → fail.
        asyncio.run(_set_wallet(1000))
        _put_cfg(admin, max_surge=2.0, label_threshold=1.20,
                 time_enabled=False, demand_enabled=False,
                 urban_enabled=False, traffic_enabled=False,
                 premium_uplift=0.50)
        body = {
            "pickup_address": "p", "dropoff_address": "d",
            "pickup_lat": PICKUP_LAT, "pickup_lng": PICKUP_LNG,
            "dropoff_lat": DROPOFF_LAT, "dropoff_lng": DROPOFF_LNG,
            "vehicle_type": "premium",
        }
        r = bob.post(f"{BASE_URL}/api/transport/request",
                     data=json.dumps(body), timeout=15)
        assert r.status_code == 400
        assert "Solde insuffisant" in r.json().get("detail", "")
        # Restore wallet for next tests
        asyncio.run(_set_wallet(50000))


# ───── GET /admin/surge/history ─────
class TestHistory:
    def test_history_after_ride(self, admin, bob):
        _put_cfg(admin, max_surge=2.0, label_threshold=1.20,
                 time_enabled=False, demand_enabled=False,
                 urban_enabled=False, traffic_enabled=False,
                 premium_uplift=0.50)
        body = {
            "pickup_address": "p", "dropoff_address": "d",
            "pickup_lat": PICKUP_LAT, "pickup_lng": PICKUP_LNG,
            "dropoff_lat": DROPOFF_LAT, "dropoff_lng": DROPOFF_LNG,
            "vehicle_type": "premium",
        }
        rr = bob.post(f"{BASE_URL}/api/transport/request",
                      data=json.dumps(body), timeout=15)
        assert rr.status_code == 200, rr.text

        r = admin.get(f"{BASE_URL}/api/transport/admin/surge/history"
                      f"?days=7&country=CM&min_multiplier=1.0&limit=100",
                      timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("days", "country_filter", "min_multiplier", "summary", "items"):
            assert k in d
        for k in ("total", "surged", "avg_multiplier", "max_multiplier", "extra_revenue"):
            assert k in d["summary"]
        assert d["summary"]["total"] >= 1
        assert d["summary"]["surged"] >= 1
        assert len(d["items"]) >= 1
        item = d["items"][0]
        for k in ("applied_at", "final_multiplier", "base_fare", "final_fare",
                  "factors", "time_band"):
            assert k in item

    def test_history_min_multiplier_filter(self, admin):
        # Only ask for multiplier >= 5.0 — should yield empty items
        r = admin.get(f"{BASE_URL}/api/transport/admin/surge/history"
                      f"?days=7&country=CM&min_multiplier=5.0",
                      timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["min_multiplier"] == 5.0
        assert d["summary"]["total"] == 0
        assert d["items"] == []
