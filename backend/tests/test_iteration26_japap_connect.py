"""
Iter26 — JAPAP CONNECT: WiFi community rewards.
Covers:
  - hotspots CRUD (create/list/update/delete)
  - admin gates (connect_enabled=false → 503, pro_required_to_share=true + non-pro → 403)
  - nearby (haversine precision, bounding box, sort by distance)
  - captive flow start/end (duration ≥ min → reward, Pro multiplier 1.5, cap, blocked → 0)
  - anti-fraud caps (user_hotspot/day → blocked:true + no reward on end)
  - stats, leaderboard
  - admin stats/list/block/unblock/sponsor (+ audit logs)
  - settings-driven: connect_reward_per_connection_usd bumps new sessions, connect_enabled=false kills create
"""
import os
import subprocess
import time
import pytest
import requests
from pathlib import Path


def _load_base_url():
    b = os.environ.get("REACT_APP_BACKEND_URL")
    if b:
        return b.rstrip("/")
    env_path = Path("/app/frontend/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                return line.split("=", 1)[1].strip().rstrip("/")
    raise RuntimeError("REACT_APP_BACKEND_URL not set")


BASE_URL = _load_base_url()
ADMIN = ("admin@japap.com", "JapapAdmin2024!")
BOB = ("bob@japap.com", "Test1234!")
ALICE = ("alice@japap.com", "Test1234!")

# Douala test coordinates
DOUALA_LAT = 4.0611
DOUALA_LNG = 9.7876


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return s


def _psql(sql):
    """Run a quick psql against local DB."""
    try:
        r = subprocess.run(
            ["sudo", "-u", "postgres", "psql", "-d", "japap_messenger", "-tAc", sql],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _set_settings(admin_session, **kv):
    r = admin_session.put(f"{BASE_URL}/api/admin/settings", json={"settings": kv})
    assert r.status_code == 200, f"settings PUT: {r.status_code} {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def admin_session():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def bob_session():
    return _login(*BOB)


@pytest.fixture(scope="module")
def alice_session():
    return _login(*ALICE)


@pytest.fixture(scope="module", autouse=True)
def clean_and_enable(admin_session):
    """Ensure clean slate + connect enabled + defaults."""
    _psql("DELETE FROM wifi_connections;")
    _psql("DELETE FROM wifi_hotspots;")
    _set_settings(
        admin_session,
        connect_enabled=True,
        connect_pro_required_to_share=False,
        connect_reward_per_connection_usd=0.05,
        connect_reward_per_minute_usd=0.002,
        connect_max_reward_per_session_usd=0.50,
        connect_min_session_seconds=60,
        connect_pro_reward_multiplier=1.5,
        connect_max_connections_per_ip_per_day=5,
        connect_max_connections_per_device_per_day=5,
        connect_max_connections_per_user_per_hotspot_per_day=1,
        connect_search_radius_km=5.0,
    )
    yield
    # teardown best-effort
    _psql("DELETE FROM wifi_connections;")
    _psql("DELETE FROM wifi_hotspots;")


# ============================== Hotspot CRUD ==============================
class TestHotspotCRUD:
    def test_create_hotspot_bob(self, bob_session):
        r = bob_session.post(f"{BASE_URL}/api/connect/hotspots", json={
            "alias": "TEST_Bob Douala WiFi",
            "description": "Test hotspot",
            "latitude": DOUALA_LAT, "longitude": DOUALA_LNG,
            "address": "Rue test, Douala",
            "type": "user", "max_daily_users": 10,
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert "hotspot_id" in d and d["hotspot_id"].startswith("hs_")
        pytest.bob_hotspot_id = d["hotspot_id"]

    def test_create_blocked_by_connect_disabled(self, bob_session, admin_session):
        _set_settings(admin_session, connect_enabled=False)
        try:
            r = bob_session.post(f"{BASE_URL}/api/connect/hotspots", json={
                "alias": "TEST_Should Fail", "latitude": DOUALA_LAT,
                "longitude": DOUALA_LNG, "type": "user",
            })
            assert r.status_code == 503, f"expected 503 got {r.status_code}: {r.text}"
        finally:
            _set_settings(admin_session, connect_enabled=True)

    def test_create_blocked_by_pro_required(self, alice_session, admin_session):
        _set_settings(admin_session, connect_pro_required_to_share=True)
        try:
            r = alice_session.post(f"{BASE_URL}/api/connect/hotspots", json={
                "alias": "TEST_Alice nonpro", "latitude": DOUALA_LAT,
                "longitude": DOUALA_LNG, "type": "user",
            })
            assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text}"
        finally:
            _set_settings(admin_session, connect_pro_required_to_share=False)

    def test_my_hotspots(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/connect/my-hotspots")
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list) and len(items) >= 1
        assert any(h["hotspot_id"] == pytest.bob_hotspot_id for h in items)

    def test_update_hotspot_owner_ok(self, bob_session):
        r = bob_session.put(f"{BASE_URL}/api/connect/hotspots/{pytest.bob_hotspot_id}",
                            json={"alias": "TEST_Bob Renamed"})
        assert r.status_code == 200, r.text
        # Verify persisted via my-hotspots
        lst = bob_session.get(f"{BASE_URL}/api/connect/my-hotspots").json()
        hs = next(h for h in lst if h["hotspot_id"] == pytest.bob_hotspot_id)
        assert hs["alias"] == "TEST_Bob Renamed"

    def test_update_hotspot_non_owner_403(self, alice_session):
        r = alice_session.put(f"{BASE_URL}/api/connect/hotspots/{pytest.bob_hotspot_id}",
                              json={"alias": "Hacked"})
        assert r.status_code == 403, r.text

    def test_delete_non_owner_403(self, alice_session):
        r = alice_session.delete(f"{BASE_URL}/api/connect/hotspots/{pytest.bob_hotspot_id}")
        assert r.status_code == 403, r.text


# ============================== Nearby ==============================
class TestNearby:
    def test_nearby_returns_hotspot_with_accurate_distance(self, alice_session):
        r = alice_session.get(f"{BASE_URL}/api/connect/nearby",
                              params={"lat": DOUALA_LAT, "lng": DOUALA_LNG, "radius_km": 5})
        assert r.status_code == 200, r.text
        d = r.json()
        found = [h for h in d["hotspots"] if h["hotspot_id"] == pytest.bob_hotspot_id]
        assert found, f"hotspot not found in nearby. Got: {d}"
        assert found[0]["distance_km"] <= 0.1, f"distance should be ~0km, got {found[0]['distance_km']}"
        assert "owner" in found[0] and "is_pro" in found[0]["owner"]

    def test_nearby_bounding_box_excludes_far(self, alice_session):
        r = alice_session.get(f"{BASE_URL}/api/connect/nearby",
                              params={"lat": 14.6928, "lng": -17.4467, "radius_km": 5})  # Dakar
        assert r.status_code == 200
        ids = [h["hotspot_id"] for h in r.json()["hotspots"]]
        assert pytest.bob_hotspot_id not in ids

    def test_nearby_haversine_precision(self, alice_session):
        # Point ~1.1 km north of hotspot (0.01° lat ≈ 1.11 km)
        r = alice_session.get(f"{BASE_URL}/api/connect/nearby",
                              params={"lat": DOUALA_LAT + 0.01, "lng": DOUALA_LNG, "radius_km": 5})
        d = r.json()
        found = [h for h in d["hotspots"] if h["hotspot_id"] == pytest.bob_hotspot_id]
        assert found
        assert 1.0 < found[0]["distance_km"] < 1.3, f"expected ~1.11 km got {found[0]['distance_km']}"


# ============================== start/end flow ==============================
class TestCaptiveFlow:
    def test_start_owner_cannot_connect_own(self, bob_session):
        r = bob_session.post(f"{BASE_URL}/api/connect/start",
                             json={"hotspot_id": pytest.bob_hotspot_id})
        assert r.status_code == 400, r.text

    def test_start_nonexistent_404(self, alice_session):
        r = alice_session.post(f"{BASE_URL}/api/connect/start",
                               json={"hotspot_id": "hs_doesnotexist"})
        assert r.status_code == 404

    def test_start_ok_alice_to_bob(self, alice_session):
        r = alice_session.post(f"{BASE_URL}/api/connect/start",
                               json={"hotspot_id": pytest.bob_hotspot_id,
                                     "device_id": "dev_alice_01"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert "connection_id" in d and d["connection_id"].startswith("wc_")
        assert d["blocked"] is False
        pytest.alice_conn_id = d["connection_id"]

    def test_end_below_min_seconds_zero_reward(self, alice_session):
        # Session just opened — duration < 60s → reward 0
        r = alice_session.post(f"{BASE_URL}/api/connect/end",
                               json={"connection_id": pytest.alice_conn_id})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "ended"
        assert float(d["reward_usd"]) == 0.0

    def test_end_already_ended(self, alice_session):
        r = alice_session.post(f"{BASE_URL}/api/connect/end",
                               json={"connection_id": pytest.alice_conn_id})
        assert r.status_code == 200
        d = r.json()
        assert d.get("already_ended") is True

    def test_end_wrong_user_403(self, bob_session):
        # Bob tries to end Alice's session
        r = bob_session.post(f"{BASE_URL}/api/connect/end",
                             json={"connection_id": pytest.alice_conn_id})
        # Already ended returns before auth check? Let's check current behavior: owner check happens
        # BEFORE the already-ended branch in the code, so 403.
        assert r.status_code == 403, r.text

    def test_reward_credited_on_long_session_with_pro_mult(self, alice_session, bob_session, admin_session):
        """Simulate a >= 60s session by backdating started_at in DB, then end → reward credited.
        Bob is Pro → 1.5× multiplier. User-hotspot cap is 1/day so we reset the connections table
        for this user+hotspot first to avoid the previous session blocking this one.
        """
        _psql(f"DELETE FROM wifi_connections WHERE user_id=(SELECT user_id FROM users WHERE email='alice@japap.com') AND hotspot_id='{pytest.bob_hotspot_id}'")

        # Snapshot Bob wallet before (via /api/wallet/balance)
        bw = bob_session.get(f"{BASE_URL}/api/wallet/balance")
        assert bw.status_code == 200, f"wallet balance: {bw.status_code} {bw.text}"
        bal_before = float(bw.json().get("balance", 0) or 0)

        r = alice_session.post(f"{BASE_URL}/api/connect/start",
                               json={"hotspot_id": pytest.bob_hotspot_id,
                                     "device_id": "dev_alice_02"})
        assert r.status_code == 200, r.text
        conn_id = r.json()["connection_id"]
        # Backdate to 130 seconds ago
        _psql(f"UPDATE wifi_connections SET started_at = NOW() - INTERVAL '130 seconds' WHERE connection_id='{conn_id}'")

        r = alice_session.post(f"{BASE_URL}/api/connect/end",
                               json={"connection_id": conn_id})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "ended"
        reward = float(d["reward_usd"])
        # Base 0.05 + per_min 0.002 * 2 = 0.054 → × 1.5 (Bob Pro) = 0.081
        # Allow small tolerance
        assert 0.07 < reward < 0.10, f"expected ~0.081 with Pro multiplier, got {reward}"

        bw2 = bob_session.get(f"{BASE_URL}/api/wallet/balance")
        bal_after = float(bw2.json().get("balance", 0) or 0)
        assert bal_after > bal_before, f"Bob wallet did not increase: {bal_before} → {bal_after}"

    def test_user_hotspot_cap_blocked(self, alice_session):
        """Alice tries to connect again same day → cap=1 → blocked:true, no reward."""
        r = alice_session.post(f"{BASE_URL}/api/connect/start",
                               json={"hotspot_id": pytest.bob_hotspot_id,
                                     "device_id": "dev_alice_03"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["blocked"] is True, f"expected blocked:true got {d}"
        assert d["blocked_reason"] == "user_hotspot_limit"
        conn_id = d["connection_id"]
        # Backdate & end → should be reward 0 even with duration > min
        _psql(f"UPDATE wifi_connections SET started_at = NOW() - INTERVAL '130 seconds' WHERE connection_id='{conn_id}'")
        r2 = alice_session.post(f"{BASE_URL}/api/connect/end", json={"connection_id": conn_id})
        assert r2.status_code == 200
        assert float(r2.json()["reward_usd"]) == 0.0

    def test_reward_cap_enforced(self, admin_session, alice_session, bob_session):
        """Set base reward to 10 USD → cap 0.50 → should be capped."""
        _psql("DELETE FROM wifi_connections;")
        _set_settings(admin_session,
                      connect_reward_per_connection_usd=10.0,
                      connect_max_reward_per_session_usd=0.50)
        try:
            r = alice_session.post(f"{BASE_URL}/api/connect/start",
                                   json={"hotspot_id": pytest.bob_hotspot_id,
                                         "device_id": "dev_capcheck"})
            conn_id = r.json()["connection_id"]
            _psql(f"UPDATE wifi_connections SET started_at = NOW() - INTERVAL '130 seconds' WHERE connection_id='{conn_id}'")
            r2 = alice_session.post(f"{BASE_URL}/api/connect/end",
                                    json={"connection_id": conn_id})
            reward = float(r2.json()["reward_usd"])
            assert reward == 0.50, f"expected capped at 0.50, got {reward}"
        finally:
            _set_settings(admin_session,
                          connect_reward_per_connection_usd=0.05,
                          connect_max_reward_per_session_usd=0.50)


# ============================== stats & leaderboard ==============================
class TestStatsLeaderboard:
    def test_hotspot_stats_owner_ok(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/connect/hotspots/{pytest.bob_hotspot_id}/stats")
        assert r.status_code == 200
        d = r.json()
        assert "hotspot" in d and "connections_24h" in d

    def test_hotspot_stats_non_owner_403(self, alice_session):
        r = alice_session.get(f"{BASE_URL}/api/connect/hotspots/{pytest.bob_hotspot_id}/stats")
        assert r.status_code == 403

    def test_hotspot_stats_admin_ok(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/connect/hotspots/{pytest.bob_hotspot_id}/stats")
        assert r.status_code == 200

    def test_leaderboard(self, alice_session):
        r = alice_session.get(f"{BASE_URL}/api/connect/leaderboard")
        assert r.status_code == 200
        lst = r.json()
        assert isinstance(lst, list)
        # Bob should appear (he had a successful connection)
        if lst:
            assert all("rank" in x and "connections" in x for x in lst)


# ============================== admin endpoints ==============================
class TestAdminConnect:
    def test_admin_stats(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/connect/stats")
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("hotspots_total", "hotspots_active", "hotspots_blocked",
                  "hotspots_sponsored", "connections_total", "connections_24h",
                  "rewarded_all_time_usd", "top_hotspots"):
            assert k in d, f"missing {k}"
        assert d["hotspots_total"] >= 1

    def test_admin_list_pagination_filters(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/connect/hotspots",
                              params={"page": 1, "limit": 10, "search": "bob"})
        assert r.status_code == 200
        d = r.json()
        assert "hotspots" in d and "total" in d

    def test_admin_block_unblock_flow(self, admin_session, alice_session):
        r = admin_session.post(f"{BASE_URL}/api/admin/connect/hotspots/{pytest.bob_hotspot_id}/block",
                               json={"reason": "TEST_block"})
        assert r.status_code == 200
        # /start must now 404 (filtered by is_blocked=FALSE)
        r2 = alice_session.post(f"{BASE_URL}/api/connect/start",
                                json={"hotspot_id": pytest.bob_hotspot_id})
        assert r2.status_code == 404, r2.text

        # audit log created
        audit = _psql("SELECT COUNT(*) FROM audit_logs WHERE action='admin_connect_block'")
        assert int(audit or 0) >= 1

        # unblock
        r3 = admin_session.post(f"{BASE_URL}/api/admin/connect/hotspots/{pytest.bob_hotspot_id}/unblock")
        assert r3.status_code == 200

    def test_admin_sponsor(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/admin/connect/hotspots/{pytest.bob_hotspot_id}/sponsor",
                               json={"sponsor_name": "TEST_Sponsor Corp", "is_sponsored": True})
        assert r.status_code == 200
        # verify
        lst = admin_session.get(f"{BASE_URL}/api/admin/connect/hotspots",
                                params={"sponsored": "true"}).json()
        hs = next((h for h in lst["hotspots"] if h["hotspot_id"] == pytest.bob_hotspot_id), None)
        assert hs is not None
        assert hs["is_sponsored"] is True
        assert hs["type"] == "partner"
        assert hs["sponsor_name"] == "TEST_Sponsor Corp"


# ============================== settings-driven ==============================
class TestSettingsDriven:
    def test_bumped_reward_per_connection(self, admin_session, alice_session):
        _psql("DELETE FROM wifi_connections;")
        _set_settings(admin_session, connect_reward_per_connection_usd=0.20)
        try:
            r = alice_session.post(f"{BASE_URL}/api/connect/start",
                                   json={"hotspot_id": pytest.bob_hotspot_id,
                                         "device_id": "dev_setting_bump"})
            conn_id = r.json()["connection_id"]
            _psql(f"UPDATE wifi_connections SET started_at = NOW() - INTERVAL '130 seconds' WHERE connection_id='{conn_id}'")
            r2 = alice_session.post(f"{BASE_URL}/api/connect/end",
                                    json={"connection_id": conn_id})
            # 0.20 + 0.002*2 = 0.204 × 1.5 (Bob Pro) = 0.306 → not hit 0.50 cap
            reward = float(r2.json()["reward_usd"])
            assert 0.29 < reward < 0.32, f"expected ~0.306 got {reward}"
        finally:
            _set_settings(admin_session, connect_reward_per_connection_usd=0.05)
