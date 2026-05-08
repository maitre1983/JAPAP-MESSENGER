"""
Iter27 — JAPAP CONNECT Level 2: Pro gating + 2% revshare.

Covers:
  - GET /api/connect/me returns extended payload: gating{min_use,min_share,can_use,can_share,
    use_required_rank,share_required_rank} and revshare{enabled,pct,cap_monthly_usd,
    total_earned_usd,last_30d_usd,last_30d_count,eligible}.
  - POST /api/connect/start returns 403 detail PRO_REQUIRED:access:{tier} when plan_rank<min_use.
  - POST /api/connect/hotspots returns 403 detail PRO_REQUIRED:share:{tier} when plan_rank<min_share
    for type != 'public'.
  - POST /api/connect/hotspots accepts is_premium bool and country_code ISO2 (persisted+echoed).
  - GET /api/connect/leaderboard?country=XX filters by wifi_hotspots.country_code and returns rows
    with points, hotspots, connections, earned_usd.
  - GET /api/connect/revshare/history returns paginated {items,total,page,limit}.
  - credit_hotspot_owner_from_pro writes role='connect_revshare' rows in referral_rewards_log and
    a notification, then they surface in /me and /revshare/history.
  - Gating dynamic: with min_access=none+min_share=business, free user can_use=True can_share=False.
"""
import os
import asyncio
import subprocess
import pytest
import requests
from pathlib import Path
from decimal import Decimal


# ---------------- setup ----------------
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

DOUALA_LAT, DOUALA_LNG = 4.0611, 9.7876


def _psql(sql: str) -> str:
    try:
        r = subprocess.run(
            ["sudo", "-u", "postgres", "psql", "-d", "japap_messenger", "-tAc", sql],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return s


def _set_settings(admin_session, **kv):
    r = admin_session.put(f"{BASE_URL}/api/admin/settings", json={"settings": kv})
    assert r.status_code == 200, f"settings PUT: {r.status_code} {r.text}"
    return r.json()


def _grant_pro(admin_session, user_id: str, plan_id: str, days: int = 30):
    r = admin_session.post(f"{BASE_URL}/api/admin/pro/grant", json={
        "user_id": user_id, "plan_id": plan_id, "days": days,
    })
    assert r.status_code == 200, f"grant {plan_id}: {r.status_code} {r.text}"
    return r.json()


def _revoke_pro(admin_session, user_id: str):
    r = admin_session.post(f"{BASE_URL}/api/admin/pro/revoke/{user_id}")
    # accept 200 or 404 (no active sub)
    assert r.status_code in (200, 404, 400), f"revoke: {r.status_code} {r.text}"


# ---------------- fixtures ----------------
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
def setup_state(admin_session):
    """Restore default gating settings + clean connect tables and revshare log."""
    _psql("DELETE FROM wifi_connections;")
    _psql("DELETE FROM wifi_hotspots;")
    _psql("DELETE FROM referral_rewards_log WHERE role='connect_revshare';")
    # Ensure baseline settings — business share, starter access, revshare 2%, cap 100.
    _set_settings(
        admin_session,
        connect_enabled=True,
        connect_min_pro_to_access="starter",
        connect_min_pro_to_share="business",
        connect_revshare_pro_enabled=True,
        connect_revshare_pct=2.0,
        connect_revshare_cap_per_month_usd=100,
        connect_revshare_attribution_hours=720,
    )
    # Revoke any active subs on bob/alice from previous runs
    bob_uid = _psql("SELECT user_id FROM users WHERE email='bob@japap.com';")
    alice_uid = _psql("SELECT user_id FROM users WHERE email='alice@japap.com';")
    _revoke_pro(admin_session, bob_uid)
    _revoke_pro(admin_session, alice_uid)
    pytest.bob_uid = bob_uid
    pytest.alice_uid = alice_uid
    yield
    _psql("DELETE FROM wifi_connections;")
    _psql("DELETE FROM wifi_hotspots;")
    _psql("DELETE FROM referral_rewards_log WHERE role='connect_revshare';")
    _revoke_pro(admin_session, bob_uid)
    _revoke_pro(admin_session, alice_uid)
    # restore defaults
    _set_settings(
        admin_session,
        connect_min_pro_to_access="starter",
        connect_min_pro_to_share="business",
    )


# ============================== /me shape ==============================
class TestConnectMe:
    def test_me_returns_gating_and_revshare_sections(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/connect/me")
        assert r.status_code == 200
        d = r.json()
        # base fields
        for k in ("points", "level", "plan_rank", "badges",
                  "connections_made", "hotspots_owned",
                  "connections_received", "earned_usd"):
            assert k in d, f"missing {k}"
        # gating
        g = d["gating"]
        for k in ("min_use", "min_share", "can_use", "can_share",
                  "use_required_rank", "share_required_rank"):
            assert k in g, f"missing gating.{k}"
        assert g["min_use"] == "starter"
        assert g["min_share"] == "business"
        assert g["use_required_rank"] == 1
        assert g["share_required_rank"] == 3
        # revshare
        rs = d["revshare"]
        for k in ("enabled", "pct", "cap_monthly_usd", "total_earned_usd",
                  "last_30d_usd", "last_30d_count", "eligible"):
            assert k in rs, f"missing revshare.{k}"
        assert rs["enabled"] is True
        assert float(rs["pct"]) == 2.0
        assert float(rs["cap_monthly_usd"]) == 100.0

    def test_me_free_user_cannot_use_nor_share(self, bob_session):
        # Bob has no Pro → can_use=False (min=starter), can_share=False (min=business)
        d = bob_session.get(f"{BASE_URL}/api/connect/me").json()
        assert d["plan_rank"] == 0
        assert d["gating"]["can_use"] is False
        assert d["gating"]["can_share"] is False
        assert d["revshare"]["eligible"] is False  # not business

    def test_me_starter_user_can_use_but_not_share(self, admin_session, bob_session):
        _grant_pro(admin_session, pytest.bob_uid, "starter", 30)
        try:
            d = bob_session.get(f"{BASE_URL}/api/connect/me").json()
            assert d["plan_rank"] == 1
            assert d["gating"]["can_use"] is True
            assert d["gating"]["can_share"] is False
            assert d["revshare"]["eligible"] is False
        finally:
            _revoke_pro(admin_session, pytest.bob_uid)

    def test_me_business_is_eligible_revshare(self, admin_session, bob_session):
        _grant_pro(admin_session, pytest.bob_uid, "business", 30)
        try:
            d = bob_session.get(f"{BASE_URL}/api/connect/me").json()
            assert d["plan_rank"] == 3
            assert d["gating"]["can_use"] is True
            assert d["gating"]["can_share"] is True
            assert d["revshare"]["eligible"] is True
        finally:
            _revoke_pro(admin_session, pytest.bob_uid)


# ============================== /start PRO gating ==============================
class TestStartGating:
    def test_start_403_when_plan_rank_below_min_use(self, admin_session, bob_session, alice_session):
        # Make Alice business so she can create a hotspot
        _grant_pro(admin_session, pytest.alice_uid, "business", 30)
        try:
            ch = alice_session.post(f"{BASE_URL}/api/connect/hotspots", json={
                "alias": "AliceHS", "latitude": DOUALA_LAT, "longitude": DOUALA_LNG,
                "type": "user", "country_code": "CM",
            })
            assert ch.status_code == 200, ch.text
            hid = ch.json()["hotspot_id"]
            # Bob free (plan_rank=0) → 403 PRO_REQUIRED:access:starter
            r = bob_session.post(f"{BASE_URL}/api/connect/start", json={
                "hotspot_id": hid, "device_id": "dev-bob"
            })
            assert r.status_code == 403, r.text
            assert r.json()["detail"] == "PRO_REQUIRED:access:starter"
        finally:
            _revoke_pro(admin_session, pytest.alice_uid)
            _psql("DELETE FROM wifi_hotspots;")

    def test_start_ok_when_min_access_none(self, admin_session, bob_session, alice_session):
        _set_settings(admin_session, connect_min_pro_to_access="none")
        _grant_pro(admin_session, pytest.alice_uid, "business", 30)
        try:
            ch = alice_session.post(f"{BASE_URL}/api/connect/hotspots", json={
                "alias": "AliceHS2", "latitude": DOUALA_LAT, "longitude": DOUALA_LNG,
                "type": "user", "country_code": "CM",
            })
            assert ch.status_code == 200, ch.text
            hid = ch.json()["hotspot_id"]
            r = bob_session.post(f"{BASE_URL}/api/connect/start", json={
                "hotspot_id": hid, "device_id": "dev-bob-2"
            })
            assert r.status_code == 200, r.text
            assert r.json()["connection_id"].startswith("wc_")
        finally:
            _revoke_pro(admin_session, pytest.alice_uid)
            _set_settings(admin_session, connect_min_pro_to_access="starter")
            _psql("DELETE FROM wifi_connections;")
            _psql("DELETE FROM wifi_hotspots;")


# ============================== /hotspots share gating ==============================
class TestShareGating:
    def test_create_hotspot_403_when_below_min_share(self, bob_session):
        # Bob is free, min_share=business → 403 PRO_REQUIRED:share:business
        r = bob_session.post(f"{BASE_URL}/api/connect/hotspots", json={
            "alias": "FreeHS", "latitude": DOUALA_LAT, "longitude": DOUALA_LNG,
            "type": "user", "country_code": "CM",
        })
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "PRO_REQUIRED:share:business"

    def test_public_hotspot_bypasses_share_gating(self, bob_session):
        # type='public' is exempt from share gating per code (line 120).
        r = bob_session.post(f"{BASE_URL}/api/connect/hotspots", json={
            "alias": "PublicHS", "latitude": DOUALA_LAT, "longitude": DOUALA_LNG,
            "type": "public", "country_code": "CM",
        })
        assert r.status_code == 200, r.text
        _psql("DELETE FROM wifi_hotspots WHERE alias='PublicHS';")

    def test_hotspot_accepts_is_premium_and_country_code(self, admin_session, bob_session):
        _grant_pro(admin_session, pytest.bob_uid, "business", 30)
        try:
            r = bob_session.post(f"{BASE_URL}/api/connect/hotspots", json={
                "alias": "PremHS", "latitude": DOUALA_LAT, "longitude": DOUALA_LNG,
                "type": "user", "is_premium": True, "country_code": "cm",  # lower → upper
            })
            assert r.status_code == 200, r.text
            hid = r.json()["hotspot_id"]
            lst = bob_session.get(f"{BASE_URL}/api/connect/my-hotspots").json()
            h = next(h for h in lst if h["hotspot_id"] == hid)
            assert h["is_premium"] is True
            assert h["country_code"] == "CM"
        finally:
            _revoke_pro(admin_session, pytest.bob_uid)
            _psql("DELETE FROM wifi_hotspots;")


# ============================== Leaderboard country filter ==============================
class TestLeaderboardCountry:
    def test_leaderboard_country_filters_by_hotspot_country(
            self, admin_session, bob_session, alice_session):
        _grant_pro(admin_session, pytest.bob_uid, "business", 30)
        _grant_pro(admin_session, pytest.alice_uid, "business", 30)
        try:
            # Bob creates a hotspot in CM, Alice in FR
            rb = bob_session.post(f"{BASE_URL}/api/connect/hotspots", json={
                "alias": "BobCM", "latitude": DOUALA_LAT, "longitude": DOUALA_LNG,
                "type": "user", "country_code": "CM",
            })
            assert rb.status_code == 200
            bob_hid = rb.json()["hotspot_id"]
            ra = alice_session.post(f"{BASE_URL}/api/connect/hotspots", json={
                "alias": "AliceFR", "latitude": 48.85, "longitude": 2.35,
                "type": "user", "country_code": "FR",
            })
            assert ra.status_code == 200
            alice_hid = ra.json()["hotspot_id"]
            # Bump total_connections + rewarded manually to appear in leaderboard
            _psql(f"UPDATE wifi_hotspots SET total_connections=3, total_rewarded_usd=1.23 "
                  f"WHERE hotspot_id='{bob_hid}';")
            _psql(f"UPDATE wifi_hotspots SET total_connections=7, total_rewarded_usd=4.56 "
                  f"WHERE hotspot_id='{alice_hid}';")

            # global
            r = bob_session.get(f"{BASE_URL}/api/connect/leaderboard")
            assert r.status_code == 200
            names_global = [row["name"] for row in r.json()]
            assert any("Bob" in n for n in names_global)
            assert any("Alice" in n for n in names_global)

            # country=CM only Bob
            r = bob_session.get(f"{BASE_URL}/api/connect/leaderboard?country=CM")
            assert r.status_code == 200
            rows = r.json()
            assert len(rows) == 1
            assert "Bob" in rows[0]["name"]
            for k in ("points", "hotspots", "connections", "earned_usd"):
                assert k in rows[0]
            assert rows[0]["connections"] == 3

            # country=FR only Alice
            r = bob_session.get(f"{BASE_URL}/api/connect/leaderboard?country=FR")
            rows = r.json()
            assert len(rows) == 1
            assert "Alice" in rows[0]["name"]
            assert rows[0]["connections"] == 7
        finally:
            _revoke_pro(admin_session, pytest.bob_uid)
            _revoke_pro(admin_session, pytest.alice_uid)
            _psql("DELETE FROM wifi_hotspots;")


# ============================== credit_hotspot_owner_from_pro + /revshare/history ==============================
class TestRevshareCredit:
    def test_credit_and_history(self, admin_session, bob_session, alice_session):
        """
        Flow: Alice connects through Bob's hotspot (writes last_connect_owner_id on Alice),
        then we directly call credit_hotspot_owner_from_pro for Alice → credits Bob 2% of plan.
        Verify referral_rewards_log row + notification + /me + /revshare/history.
        """
        _set_settings(admin_session,
                      connect_min_pro_to_access="none",
                      connect_min_pro_to_share="none",
                      connect_revshare_pro_enabled=True,
                      connect_revshare_pct=2.0,
                      connect_revshare_cap_per_month_usd=100,
                      connect_min_session_seconds=60)
        # Make Bob business (hotspot owner)
        _grant_pro(admin_session, pytest.bob_uid, "business", 30)
        try:
            ch = bob_session.post(f"{BASE_URL}/api/connect/hotspots", json={
                "alias": "BobOwner", "latitude": DOUALA_LAT, "longitude": DOUALA_LNG,
                "type": "user", "country_code": "CM",
            })
            assert ch.status_code == 200, ch.text
            bob_hid = ch.json()["hotspot_id"]
            # Alice connects
            s = alice_session.post(f"{BASE_URL}/api/connect/start", json={
                "hotspot_id": bob_hid, "device_id": "rev-dev"})
            assert s.status_code == 200, s.text
            conn_id = s.json()["connection_id"]
            # Backdate started_at by 2 min so /end credits
            _psql(f"UPDATE wifi_connections SET started_at = NOW() - INTERVAL '130 seconds' "
                  f"WHERE connection_id='{conn_id}';")
            e = alice_session.post(f"{BASE_URL}/api/connect/end", json={"connection_id": conn_id})
            assert e.status_code == 200, e.text
            # Sanity: Alice now has last_connect_owner_id = bob
            owner = _psql(f"SELECT last_connect_owner_id FROM users WHERE user_id='{pytest.alice_uid}';")
            assert owner == pytest.bob_uid, owner

            # Now simulate Alice buying a Pro → call credit_hotspot_owner_from_pro directly.
            import sys
            sys.path.insert(0, "/app/backend")
            # Load backend .env so DATABASE_URL etc. is available for direct import call
            from pathlib import Path as _P
            _env = _P("/app/backend/.env")
            if _env.exists():
                for line in _env.read_text().splitlines():
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        v = v.strip().strip('"').strip("'")
                        os.environ[k.strip()] = v
            from routes.connect import credit_hotspot_owner_from_pro

            async def _run():
                return await credit_hotspot_owner_from_pro(
                    pytest.alice_uid, Decimal("30"), "Business Pro", "pro")
            result = asyncio.run(_run())
            assert result, f"no credit returned: {result}"
            assert result["credited_to"] == pytest.bob_uid
            assert Decimal(result["amount_usd"]) == Decimal("0.6000")

            # Verify DB row
            row_role = _psql(
                f"SELECT role FROM referral_rewards_log WHERE user_id='{pytest.bob_uid}' "
                "ORDER BY created_at DESC LIMIT 1;")
            assert row_role == "connect_revshare", row_role
            n = _psql(
                f"SELECT COUNT(*) FROM notifications WHERE user_id='{pytest.bob_uid}' "
                "AND type='connect_revshare';")
            assert int(n) >= 1, n

            # Verify /me exposes totals
            me = bob_session.get(f"{BASE_URL}/api/connect/me").json()
            assert Decimal(me["revshare"]["total_earned_usd"]) >= Decimal("0.6000")
            assert me["revshare"]["last_30d_count"] >= 1

            # /revshare/history pagination
            h = bob_session.get(f"{BASE_URL}/api/connect/revshare/history?page=1&limit=20")
            assert h.status_code == 200
            hd = h.json()
            assert hd["page"] == 1 and hd["limit"] == 20
            assert hd["total"] >= 1
            assert len(hd["items"]) >= 1
            it = hd["items"][0]
            for k in ("amount_usd", "currency", "created_at"):
                assert k in it
            assert Decimal(it["amount_usd"]) > 0
        finally:
            _revoke_pro(admin_session, pytest.bob_uid)
            _set_settings(admin_session,
                          connect_min_pro_to_access="starter",
                          connect_min_pro_to_share="business")

    def test_revshare_history_pagination_empty_user(self, alice_session):
        """Alice has no revshare → total=0, items=[]."""
        _psql(f"DELETE FROM referral_rewards_log WHERE user_id='{pytest.alice_uid}' "
              "AND role='connect_revshare';")
        r = alice_session.get(f"{BASE_URL}/api/connect/revshare/history?page=1&limit=20")
        assert r.status_code == 200
        d = r.json()
        assert d["total"] == 0
        assert d["items"] == []
        assert d["page"] == 1 and d["limit"] == 20
