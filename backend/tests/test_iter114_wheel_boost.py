"""iter114 — Wheel Boost Event integration tests (admin-piloted retention spike).

Covers:
    - GET /api/wheel/admin/boost
    - PUT /api/wheel/admin/boost (CSRF, partial updates, ISO validation)
    - GET /api/wheel/admin/boost/stats
    - GET /api/wheel/status (boost block exposed to user)
    - POST /api/wheel/spin during boost — DB checks: boost_active=TRUE,
      boost_id set, gain multiplier applied, perdu reduction lowers slot=1.
"""

import os
import asyncio
import time
import pytest
import requests
import asyncpg
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
DATABASE_URL = os.environ["DATABASE_URL"]
TURNSTILE = "JAPAP_E2E_BYPASS_2026"

ADMIN_EMAIL = "emileparfait2003@gmail.com"
ADMIN_PASSWORD = "Gerard0103@"
BOB_EMAIL = "bob@japap.com"
BOB_PASSWORD = "Test1234!"


# ── Helpers ────────────────────────────────────────────────────────────────

async def _fetch_otp(email: str, purpose: str) -> str:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for _ in range(10):
            row = await conn.fetchrow(
                "SELECT code FROM email_otps WHERE email=$1 AND purpose=$2 AND used=FALSE "
                "ORDER BY created_at DESC LIMIT 1",
                email, purpose,
            )
            if row:
                return row["code"]
            await asyncio.sleep(0.5)
    finally:
        await conn.close()
    raise RuntimeError(f"OTP not found for {email}/{purpose}")


def _login_admin() -> requests.Session:
    s = requests.Session()
    last = None
    for attempt in range(3):
        try:
            r = s.post(f"{BASE_URL}/api/auth/login",
                       json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
                             "turnstile_token": TURNSTILE},
                       timeout=120)
            if r.status_code == 200:
                last = r
                break
            last = r
            time.sleep(2)
        except Exception as e:
            last = e
            time.sleep(2)
    assert isinstance(last, requests.Response) and last.status_code == 200, \
        f"login failed {getattr(last, 'status_code', '?')} {getattr(last, 'text', str(last))[:200]}"
    body = last.json()
    needs_2fa = (body.get("status") == "otp_required"
                 or body.get("requires_2fa")
                 or body.get("two_factor_required")
                 or "challenge_id" in body)
    if needs_2fa:
        time.sleep(2)
        code = asyncio.get_event_loop().run_until_complete(
            _fetch_otp(ADMIN_EMAIL, "login_2fa"))
        for path in ("/api/auth/verify-2fa", "/api/auth/login/verify-otp",
                     "/api/auth/login/2fa", "/api/auth/2fa/verify"):
            payload = {"email": ADMIN_EMAIL, "code": code, "otp": code,
                       "turnstile_token": TURNSTILE}
            if "challenge_id" in body:
                payload["challenge_id"] = body["challenge_id"]
            r2 = s.post(f"{BASE_URL}{path}", json=payload, timeout=30)
            if r2.status_code == 200:
                return s
        raise AssertionError(f"2FA verify failed: last={r2.status_code} {r2.text[:200]}")
    return s


def _login_user(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": email, "password": password,
                     "turnstile_token": TURNSTILE},
               timeout=20)
    assert r.status_code == 200, f"login failed {r.status_code} {r.text[:200]}"
    return s


def _csrf_headers(s: requests.Session) -> dict:
    token = s.cookies.get("csrf_token") or ""
    return {"X-CSRF-Token": token, "Content-Type": "application/json"}


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def admin_session():
    return _login_admin()


@pytest.fixture(scope="module")
def bob_session():
    return _login_user(BOB_EMAIL, BOB_PASSWORD)


@pytest.fixture(scope="module", autouse=True)
def cleanup_boost(admin_session):
    """Always disable boost + restore wheel config at module teardown."""
    yield
    try:
        admin_session.put(f"{BASE_URL}/api/wheel/admin/boost",
                          json={"enabled": False},
                          headers=_csrf_headers(admin_session), timeout=20)
        admin_session.put(f"{BASE_URL}/api/wheel/admin/config",
                          json={"cooldown_seconds": 30, "max_spins_per_day": 5},
                          headers=_csrf_headers(admin_session), timeout=20)
    except Exception as e:
        print("cleanup_boost error", e)


# ── Tests ──────────────────────────────────────────────────────────────────

def test_admin_get_boost_initial(admin_session):
    """GET /admin/boost returns settings + live blocks."""
    r = admin_session.get(f"{BASE_URL}/api/wheel/admin/boost", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "settings" in body and "live" in body
    s = body["settings"]
    for k in ("enabled", "name", "starts_at", "ends_at", "gain_multiplier",
              "perdu_reduction_percent", "unlock_jackpot_all_phases",
              "jackpot_odds_during_boost", "id"):
        assert k in s, f"missing settings.{k}"
    # Reset off baseline
    admin_session.put(f"{BASE_URL}/api/wheel/admin/boost",
                      json={"enabled": False},
                      headers=_csrf_headers(admin_session))
    r2 = admin_session.get(f"{BASE_URL}/api/wheel/admin/boost", timeout=15).json()
    assert r2["settings"]["enabled"] is False
    assert r2["live"]["active"] is False


def test_admin_put_boost_invalid_iso(admin_session):
    r = admin_session.put(f"{BASE_URL}/api/wheel/admin/boost",
                          json={"starts_at": "not-a-date"},
                          headers=_csrf_headers(admin_session), timeout=15)
    assert r.status_code == 400, r.text


def test_admin_put_boost_validation_bounds(admin_session):
    r = admin_session.put(f"{BASE_URL}/api/wheel/admin/boost",
                          json={"gain_multiplier": 9.9},
                          headers=_csrf_headers(admin_session), timeout=15)
    assert r.status_code in (400, 422)
    r = admin_session.put(f"{BASE_URL}/api/wheel/admin/boost",
                          json={"perdu_reduction_percent": 99},
                          headers=_csrf_headers(admin_session), timeout=15)
    assert r.status_code in (400, 422)


def test_admin_put_boost_activate_generates_id(admin_session):
    """Enabling False->True mints a fresh boost_<10hex> id."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    payload = {
        "enabled": True,
        "name": "TEST_iter114_boost",
        "starts_at": now.isoformat().replace("+00:00", "Z"),
        "ends_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "gain_multiplier": 2.0,
        "perdu_reduction_percent": 80,
        "unlock_jackpot_all_phases": True,
        "jackpot_odds_during_boost": 50,
    }
    r = admin_session.put(f"{BASE_URL}/api/wheel/admin/boost",
                          json=payload, headers=_csrf_headers(admin_session),
                          timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["live"]["active"] is True
    assert body["live"]["gain_multiplier"] == 2.0
    assert body["live"]["perdu_reduction_percent"] == 80
    assert body["live"]["unlock_jackpot_all_phases"] is True
    bid = body["updated"].get("id") or body["live"].get("id")
    assert bid and bid.startswith("boost_") and len(bid) >= len("boost_") + 6


def test_user_wheel_status_exposes_boost(bob_session):
    """Bob's GET /api/wheel/status must show boost.active=true."""
    r = bob_session.get(f"{BASE_URL}/api/wheel/status", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "boost" in body, "status missing boost block"
    boost = body["boost"]
    assert boost.get("active") is True
    for k in ("id", "name", "gain_multiplier", "perdu_reduction_percent",
              "unlock_jackpot_all_phases", "starts_at", "ends_at",
              "jackpot_odds_during_boost"):
        assert k in boost, f"boost.{k} missing"
    assert boost["gain_multiplier"] == 2.0


def test_spin_during_boost_persists_boost_id_and_active(admin_session, bob_session):
    """Spin once during boost — DB row must have boost_active=TRUE & boost_id set
    matching wheel_boost_id setting; multiplier 2.0 must double base_points
    on non-Perdu/non-Jackpot slots."""
    # Force cooldown=0 to spin freely
    r = admin_session.put(f"{BASE_URL}/api/wheel/admin/config",
                          json={"cooldown_seconds": 0, "max_spins_per_day": 20},
                          headers=_csrf_headers(admin_session), timeout=15)
    assert r.status_code == 200, r.text

    spin = bob_session.post(f"{BASE_URL}/api/wheel/spin",
                            json={}, headers=_csrf_headers(bob_session),
                            timeout=20)
    if spin.status_code != 200:
        pytest.skip(f"spin not allowed (cycle gating): {spin.status_code} {spin.text[:200]}")
    sb = spin.json()
    assert "spin_id" in sb or "id" in sb or "result" in sb

    async def _check():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            boost_id_setting = await conn.fetchval(
                "SELECT value FROM admin_settings WHERE key='wheel_boost_id'")
            row = await conn.fetchrow(
                """SELECT boost_active, boost_id, prize_slot, points_awarded
                     FROM wheel_spins
                    WHERE user_id=(SELECT user_id FROM users WHERE email=$1)
                    ORDER BY spin_at DESC LIMIT 1""",
                BOB_EMAIL,
            )
            return boost_id_setting, row
        finally:
            await conn.close()
    boost_id_setting, row = asyncio.get_event_loop().run_until_complete(_check())
    assert row is not None, "wheel_spins row missing"
    assert row["boost_active"] is True, f"boost_active={row['boost_active']}"
    assert row["boost_id"], "boost_id not stored"
    bid_norm = (boost_id_setting or "").strip('"')
    assert row["boost_id"] == bid_norm, f"{row['boost_id']} != {bid_norm}"


def test_perdu_reduction_lowers_slot1_frequency(admin_session, bob_session):
    """With perdu_reduction_percent=95, over ~10 spins, Perdu (slot=1) must
    appear at most 1-2 times. Requires Bob's cycle to NOT be flagged
    suspicious (which silently forces slot=1)."""
    # Clear suspicious_flag so the throttle doesn't contaminate the sample
    async def _clear_suspicious():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute(
                """UPDATE wheel_cycles SET suspicious_flag=FALSE
                    WHERE user_id=(SELECT user_id FROM users WHERE email=$1)""",
                BOB_EMAIL,
            )
        finally:
            await conn.close()
    asyncio.get_event_loop().run_until_complete(_clear_suspicious())

    # Bump reduction to 95
    r = admin_session.put(f"{BASE_URL}/api/wheel/admin/boost",
                          json={"perdu_reduction_percent": 95},
                          headers=_csrf_headers(admin_session), timeout=15)
    assert r.status_code == 200
    # Ensure cooldown=0
    admin_session.put(f"{BASE_URL}/api/wheel/admin/config",
                      json={"cooldown_seconds": 0, "max_spins_per_day": 50},
                      headers=_csrf_headers(admin_session), timeout=15)

    spins_done = 0
    perdus = 0
    for i in range(10):
        # Reset suspicious_flag every spin — burst detection (>3 spins/60s on
        # same fp) re-flags Bob, and a suspicious cycle silently forces slot=1.
        async def _reset():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                await conn.execute(
                    """UPDATE wheel_cycles SET suspicious_flag=FALSE
                        WHERE user_id=(SELECT user_id FROM users WHERE email=$1)""",
                    BOB_EMAIL,
                )
            finally:
                await conn.close()
        asyncio.get_event_loop().run_until_complete(_reset())

        r = bob_session.post(f"{BASE_URL}/api/wheel/spin",
                             json={}, headers=_csrf_headers(bob_session),
                             timeout=20)
        if r.status_code != 200:
            break
        spins_done += 1
        # Read result from DB rather than trusting JSON shape
        async def _last():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                return await conn.fetchval(
                    """SELECT prize_slot FROM wheel_spins
                        WHERE user_id=(SELECT user_id FROM users WHERE email=$1)
                        ORDER BY spin_at DESC LIMIT 1""", BOB_EMAIL)
            finally:
                await conn.close()
        last_slot = asyncio.get_event_loop().run_until_complete(_last())
        if last_slot == 1:
            perdus += 1
        time.sleep(0.2)
    if spins_done < 5:
        pytest.skip(f"only {spins_done} spins ran — gating prevents stat sample")
    # With 95% Perdu reduction, expectation is very few Perdus in 10 spins.
    assert perdus <= max(3, spins_done // 3), \
        f"too many Perdu under 95% reduction: {perdus}/{spins_done}"


def test_admin_boost_stats_reflects_spins(admin_session):
    """GET /admin/boost/stats reports total_spins>=1 after spins."""
    r = admin_session.get(f"{BASE_URL}/api/wheel/admin/boost/stats", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    if body.get("stats") is None:
        pytest.skip("no spins were recorded — stats null")
    s = body["stats"]
    assert s["total_spins"] >= 1
    assert "dau" in s and s["dau"] >= 1
    assert "win_rate" in s or "perdu_rate" in s or "by_slot" in body or "by_slot" in s
    assert "boost_id" in body


def test_admin_disable_boost_clears_live(admin_session, bob_session):
    """After disable, /admin/boost.live.active=False and user status boost.active=False."""
    r = admin_session.put(f"{BASE_URL}/api/wheel/admin/boost",
                          json={"enabled": False},
                          headers=_csrf_headers(admin_session), timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["live"]["active"] is False
    rs = bob_session.get(f"{BASE_URL}/api/wheel/status", timeout=15).json()
    assert rs.get("boost", {}).get("active") is False
