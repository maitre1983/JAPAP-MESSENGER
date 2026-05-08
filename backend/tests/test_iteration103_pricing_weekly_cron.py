"""Iter103 — Weekly AI Pricing Cron + Manual Trigger.

Covers:
  - POST /api/transport/admin/pricing/run-ai-batch
      * 403 for non-admin
      * default ?force=true bypasses iso-week dedup
      * returns {skipped:true, reason:'no_active_grid'} when nothing is active
      * returns {skipped:false, week, targets, proposals_created, failures, force}
        when at least one active grid exists, and inserts ONE pricing_grid
        row per (country, vehicle_type) with status='proposed', source='ai',
        proposed_by='cron_weekly', ai_rationale populated. Previous active
        row stays untouched.
      * admin_alerts row written with kind='pricing.weekly_batch' and
        alert_key containing the iso week.
  - force=false respects:
      * admin_setting `pricing_ai_weekly_enabled` (skipped reason='disabled')
      * admin_setting `pricing_ai_last_run_iso_week`
        (skipped reason='already_ran_this_week' if equal to current week)
      * sets pricing_ai_last_run_iso_week after a successful non-forced run.
  - Idempotency: two consecutive force=false calls in same iso-week run only
    once; force=true always runs.
  - Regression: phase B (list/manual/validate) + estimate still green.
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


def _iso_week_key(dt: datetime) -> str:
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


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


# ───────────────────── Async DB helpers ─────────────────────
async def _wipe_pricing():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
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
        await conn.execute("DELETE FROM pricing_grid WHERE country_code IN ('CM','FR','XX','TS','ZZ')")
    finally:
        await conn.close()


async def _delete_alerts_for_week(week_key: str):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "DELETE FROM admin_alerts WHERE alert_key = $1",
            f"pricing.weekly_batch:{week_key}",
        )
    finally:
        await conn.close()


def _put_setting(admin_session, key: str, value):
    """Update an admin setting via the public PUT endpoint — this is the
    same path the UI uses and properly invalidates the in-memory cache."""
    r = admin_session.put(
        f"{BASE_URL}/api/admin/settings/{key}",
        json={"value": value},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _set_setting(key: str, value: str):
    """Direct DB write to admin_settings — avoids cross-event-loop issues
    with the production asyncpg pool used by services.settings_service.
    Does NOT invalidate the in-memory cache, so prefer _put_setting() in
    tests that immediately re-read the value through the API."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute(
            """INSERT INTO admin_settings (key, value, updated_at)
               VALUES ($1, $2, NOW())
               ON CONFLICT (key) DO UPDATE
                 SET value = EXCLUDED.value, updated_at = NOW()""",
            key, value,
        )
    finally:
        await conn.close()


async def _get_setting(key: str, default: str = ""):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        v = await conn.fetchval(
            "SELECT value FROM admin_settings WHERE key=$1", key,
        )
        return v if v is not None else default
    finally:
        await conn.close()


async def _count_proposed_ai_for(country_code: str, vehicle_type: str) -> int:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        n = await conn.fetchval(
            """SELECT COUNT(*) FROM pricing_grid
                WHERE country_code=$1 AND vehicle_type=$2
                  AND status='proposed' AND source='ai'
                  AND proposed_by='cron_weekly'""",
            country_code, vehicle_type,
        )
        return int(n)
    finally:
        await conn.close()


async def _get_active_pricing(country_code: str, vehicle_type: str):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchrow(
            """SELECT pricing_id, status FROM pricing_grid
                WHERE country_code=$1 AND vehicle_type=$2 AND status='active'""",
            country_code, vehicle_type,
        )
    finally:
        await conn.close()


async def _find_alert_for_week(week_key: str):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchrow(
            """SELECT id, kind, alert_key, title, body
                 FROM admin_alerts
                WHERE alert_key = $1
                ORDER BY created_at DESC LIMIT 1""",
            f"pricing.weekly_batch:{week_key}",
        )
    finally:
        await conn.close()


# ───────────────────────── Fixtures ─────────────────────────
@pytest.fixture(scope="module")
def admin_session():
    return _session_for(ADMIN_EMAIL)


@pytest.fixture(scope="module")
def bob_session():
    return _session_for(BOB_EMAIL)


@pytest.fixture(scope="module", autouse=True)
def _reset_state():
    """Module-scope: wipe pricing_grid rows, clean admin_alerts for current
    iso-week, reset both relevant settings to defaults."""
    week = _iso_week_key(datetime.now(timezone.utc))

    async def _do():
        await _wipe_pricing()
        await _delete_alerts_for_week(week)
        # Default both settings to clean state
        await _set_setting("pricing_ai_weekly_enabled", "false")
        await _set_setting("pricing_ai_last_run_iso_week", "")
    asyncio.run(_do())

    yield

    async def _cleanup():
        await _wipe_pricing()
        await _delete_alerts_for_week(week)
        await _set_setting("pricing_ai_weekly_enabled", "false")
        await _set_setting("pricing_ai_last_run_iso_week", "")
    asyncio.run(_cleanup())


def _seed_one_active(admin_session):
    """Use the public manual + validate endpoints to create exactly ONE
    active pricing row (CM/standard) so the cron has a target."""
    r = admin_session.post(
        f"{BASE_URL}/api/transport/admin/pricing/manual",
        json={"country_code": "CM", "country_name": "Cameroun",
              "currency": "XAF", "vehicle_type": "standard",
              "base_fare": 600, "per_km": 220,
              "rationale": "TEST iter103 baseline"},
    )
    assert r.status_code == 200, r.text
    pid = r.json()["pricing_id"]
    v = admin_session.post(
        f"{BASE_URL}/api/transport/admin/pricing/{pid}/validate", json={}
    )
    assert v.status_code == 200, v.text
    return pid


# ============================================================
#  PHASE 1 — Auth gate
# ============================================================
class TestAuth:
    def test_01_non_admin_run_batch_403(self, bob_session):
        r = bob_session.post(f"{BASE_URL}/api/transport/admin/pricing/run-ai-batch")
        assert r.status_code == 403, r.text

    def test_02_unauth_run_batch_401_or_403(self):
        r = requests.post(f"{BASE_URL}/api/transport/admin/pricing/run-ai-batch")
        assert r.status_code in (401, 403)


# ============================================================
#  PHASE 2 — No active grid
# ============================================================
class TestNoActiveGrid:
    def test_03_force_run_with_no_active_grid_returns_skipped(self, admin_session):
        """No active row exists yet → cron should short-circuit with
        reason='no_active_grid'. Use force=true to bypass dedup."""
        # Sanity: no rows at all
        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/run-ai-batch?force=true"
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("skipped") is True, d
        assert d.get("reason") == "no_active_grid", d
        # week key returned for traceability
        assert "week" in d


# ============================================================
#  PHASE 3 — force=true main happy path
# ============================================================
class TestForceRun:
    def test_04_force_run_creates_proposal(self, admin_session):
        """Seed one active grid → run-ai-batch?force=true → expect at least
        one proposed AI row inserted with proposed_by='cron_weekly' and the
        previous active row UNTOUCHED. Also expect an admin_alerts row."""
        active_pid = _seed_one_active(admin_session)
        week = _iso_week_key(datetime.now(timezone.utc))
        # Make sure no leftover alert from a previous flake
        asyncio.run(_delete_alerts_for_week(week))

        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/run-ai-batch?force=true",
            timeout=120,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("skipped") is False, d
        assert d.get("force") is True, d
        assert d.get("week") == week, d
        assert d.get("targets") == 1, d
        # Even if Claude flakes we still want the structure to be right.
        # In the success path we expect exactly 1 proposal.
        assert d.get("proposals_created", 0) >= 1, d
        assert isinstance(d.get("failures"), list)

        # Pricing row inserted: status='proposed', source='ai', proposed_by='cron_weekly'
        cnt = asyncio.run(_count_proposed_ai_for("CM", "standard"))
        assert cnt >= 1, f"expected at least 1 proposed AI row, got {cnt}"

        # Previous active row UNTOUCHED.
        active = asyncio.run(_get_active_pricing("CM", "standard"))
        assert active is not None
        assert active["pricing_id"] == active_pid

        # admin_alerts row written (fire-and-forget, give it up to 5s).
        alert = None
        for _ in range(10):
            alert = asyncio.run(_find_alert_for_week(week))
            if alert:
                break
            import time as _t
            _t.sleep(0.5)
        assert alert is not None, "admin_alerts row not written for weekly batch"
        assert alert["kind"] == "pricing.weekly_batch"
        assert week in alert["alert_key"]

    def test_05_ai_rationale_populated_on_proposed_row(self):
        """The freshly-inserted AI proposal must carry a non-empty rationale."""
        async def _fetch():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                return await conn.fetchrow(
                    """SELECT ai_rationale, source, proposed_by, status
                         FROM pricing_grid
                        WHERE country_code='CM' AND vehicle_type='standard'
                          AND source='ai' AND proposed_by='cron_weekly'
                        ORDER BY created_at DESC LIMIT 1"""
                )
            finally:
                await conn.close()
        row = asyncio.run(_fetch())
        assert row is not None, "no AI-proposed row found"
        assert row["status"] == "proposed"
        assert row["source"] == "ai"
        assert row["proposed_by"] == "cron_weekly"
        assert (row["ai_rationale"] or "").strip(), "ai_rationale empty"


# ============================================================
#  PHASE 4 — force=false respects settings
# ============================================================
class TestNonForcedDedup:
    def test_06_disabled_setting_returns_skipped(self, admin_session):
        """When pricing_ai_weekly_enabled='false', force=false must skip
        with reason='disabled'."""
        _put_setting(admin_session, "pricing_ai_weekly_enabled", False)
        _put_setting(admin_session, "pricing_ai_last_run_iso_week", "")
        # Cache TTL is 60s in services.settings_service — but PUT
        # invalidates the cache for the written key.
        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/run-ai-batch?force=false"
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("skipped") is True
        assert d.get("reason") == "disabled", d

    def test_07_first_unforced_run_executes_and_persists_iso_week(self, admin_session):
        """Enable + clear last week → force=false runs and stamps the week."""
        week = _iso_week_key(datetime.now(timezone.utc))

        _put_setting(admin_session, "pricing_ai_weekly_enabled", True)
        _put_setting(admin_session, "pricing_ai_last_run_iso_week", "")
        asyncio.run(_delete_alerts_for_week(week))

        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/run-ai-batch?force=false",
            timeout=120,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        # Active grid still exists from test_04, so it should run not skip.
        assert d.get("skipped") is False, d
        assert d.get("force") is False, d
        assert d.get("week") == week

        # last_run_iso_week now equals current week
        last = asyncio.run(_get_setting("pricing_ai_last_run_iso_week", ""))
        assert last == week, f"expected last={week}, got {last!r}"

    def test_08_second_unforced_call_is_skipped_already_ran(self, admin_session):
        """Immediately re-call force=false → must return
        skipped:true, reason:'already_ran_this_week'."""
        week = _iso_week_key(datetime.now(timezone.utc))
        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/run-ai-batch?force=false"
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("skipped") is True, d
        assert d.get("reason") == "already_ran_this_week", d
        assert d.get("week") == week

    def test_09_force_true_still_runs_after_dedup_set(self, admin_session):
        """Even when last_run_iso_week==current_week, force=true must bypass."""
        r = admin_session.post(
            f"{BASE_URL}/api/transport/admin/pricing/run-ai-batch?force=true",
            timeout=120,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("skipped") is False, d
        assert d.get("force") is True


# ============================================================
#  PHASE 5 — Regression on Phase B basics
# ============================================================
class TestPhaseBRegression:
    def test_10_admin_pricing_list_returns_ok(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/transport/admin/pricing")
        assert r.status_code == 200, r.text
        d = r.json()
        assert "items" in d
        assert "total" in d
        # Should contain at least one CM/standard proposed AI row from cron
        ai_rows = [it for it in d["items"]
                   if it.get("source") == "ai" and it.get("proposed_by") == "cron_weekly"]
        assert len(ai_rows) >= 1, "no cron_weekly rows visible in admin list"

    def test_11_admin_pricing_filter_by_status(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/transport/admin/pricing?status=proposed"
        )
        assert r.status_code == 200, r.text
        for it in r.json().get("items", []):
            assert it.get("status") == "proposed"

    def test_12_estimate_still_returns_currency_and_pricing_source(self, bob_session):
        """Phase B integration: /estimate must still expose currency and
        pricing_source on a country with an active grid (CM)."""
        # /estimate is a GET with query params
        r = bob_session.get(
            f"{BASE_URL}/api/transport/estimate",
            params={
                "pickup_lat": 3.848, "pickup_lng": 11.502,
                "dropoff_lat": 3.870, "dropoff_lng": 11.520,
                "vehicle_type": "standard",
            },
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert "currency" in d
        assert "pricing_source" in d
        # Bob's country_code must be CM for the grid path; we set it during
        # iter102's fixture but re-affirm here defensively.
        assert d["pricing_source"] in ("grid", "default_xaf")
