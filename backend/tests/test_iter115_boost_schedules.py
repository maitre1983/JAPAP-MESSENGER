"""
JAPAP iter115 — Wheel Boost Schedules (CRUD + worker E2E)
=========================================================
Tests:
- GET / POST / PUT / DELETE /api/wheel/admin/boost/schedules
- Validation errors (kind=recurring missing fields, bad time format,
  kind=dated bad ISO, date_end <= date_start)
- E2E worker activation (creates a recurring schedule covering "now",
  waits 65s, asserts /api/wheel/admin/boost live.active=true)
- E2E worker deactivation (delete the schedule, wait 65s, assert
  live.active=false)
- Worker safety: a manual admin boost (wheel_boost_owner empty)
  must NOT be touched by the scheduler.
"""
import os
import time
import asyncio
import pytest
import requests

# NOTE: Public ingress (japap-refactor.preview.emergentagent.com) is currently
# very slow (~40s on /api/health) so we exercise the backend over the local
# loopback. Same FastAPI app, same DB, same middlewares — only the network
# hop changes. Toggle JAPAP_TEST_BASE_URL=public to force the public URL.
BASE_URL = (os.environ.get("JAPAP_TEST_BASE_URL")
            or "http://localhost:8001").rstrip("/")
SUPERADMIN_EMAIL = "emileparfait2003@gmail.com"
SUPERADMIN_PWD = "Gerard0103@"
TURNSTILE_BYPASS = "JAPAP_E2E_BYPASS_2026"


def _fetch_otp(email: str, purpose: str = "login_2fa") -> str | None:
    """Read the latest unused OTP straight from Postgres."""
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")

    async def _q():
        c = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            r = await c.fetchrow(
                "SELECT code FROM email_otps WHERE email=$1 AND purpose=$2 "
                "AND used=FALSE ORDER BY created_at DESC LIMIT 1",
                email, purpose,
            )
            return r["code"] if r else None
        finally:
            await c.close()

    return asyncio.run(_q())


@pytest.fixture(scope="module")
def admin_session() -> requests.Session:
    """Logged-in superadmin session (cookies + CSRF header pre-wired)."""
    s = requests.Session()
    # Step 1: prime CSRF
    s.get(f"{BASE_URL}/api/health", timeout=60)
    # Step 2: login -> triggers OTP 2FA  (bcrypt: ~40s on this box)
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": SUPERADMIN_EMAIL, "password": SUPERADMIN_PWD,
              "turnstile_token": TURNSTILE_BYPASS},
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=90,
    )
    if r.status_code not in (200, 202):
        pytest.skip(f"Login failed: {r.status_code} {r.text[:200]}")

    # Step 3: pull OTP from DB and verify
    code = None
    for _ in range(5):
        time.sleep(1)
        code = _fetch_otp(SUPERADMIN_EMAIL, "login_2fa")
        if code:
            break
    if not code:
        pytest.skip("Could not fetch login_2fa OTP from DB")

    r2 = s.post(
        f"{BASE_URL}/api/auth/verify-2fa",
        json={"email": SUPERADMIN_EMAIL, "code": code},
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=60,
    )
    assert r2.status_code == 200, f"verify-2fa failed: {r2.status_code} {r2.text[:200]}"
    access_token = r2.json().get("access_token")
    assert access_token, "verify-2fa returned no access_token"
    # Always send CSRF marker + bearer on subsequent requests (cookies with
    # secure=True won't be re-sent over plain HTTP loopback).
    s.headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Authorization": f"Bearer {access_token}",
    })
    return s


# ──────────────────────────────────────────────────────────────────────────
#  CRUD
# ──────────────────────────────────────────────────────────────────────────
class TestBoostSchedulesCRUD:
    """Wheel Boost Schedules — basic CRUD"""

    def test_list_initial(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/wheel/admin/boost/schedules", timeout=15)
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert "schedules" in data
        assert isinstance(data["schedules"], list)

    def test_create_recurring_ok(self, admin_session):
        payload = {
            "name": "TEST_iter115_recur_basic",
            "kind": "recurring",
            "dow_start": 4, "time_start": "18:00",
            "dow_end": 6,   "time_end": "23:00",
            "gain_multiplier": 1.5,
            "perdu_reduction_percent": 50,
            "unlock_jackpot_all_phases": False,
            "jackpot_odds_during_boost": 25,
            "enabled": True,
        }
        r = admin_session.post(f"{BASE_URL}/api/wheel/admin/boost/schedules",
                                json=payload, timeout=15)
        assert r.status_code == 200, r.text[:200]
        sched = r.json()["schedule"]
        assert sched["id"] > 0
        assert sched["kind"] == "recurring"
        assert sched["name"] == "TEST_iter115_recur_basic"
        assert sched["time_start"] == "18:00"
        # Cleanup
        admin_session.delete(
            f"{BASE_URL}/api/wheel/admin/boost/schedules/{sched['id']}", timeout=10)

    def test_create_recurring_missing_fields(self, admin_session):
        payload = {"name": "TEST_recur_bad", "kind": "recurring"}
        r = admin_session.post(f"{BASE_URL}/api/wheel/admin/boost/schedules",
                                json=payload, timeout=15)
        assert r.status_code == 400, r.text[:200]
        detail = r.json().get("detail", "")
        assert "obligatoires" in detail.lower() or "obligatoir" in detail.lower()

    def test_create_recurring_bad_time(self, admin_session):
        payload = {"name": "TEST_bad_time", "kind": "recurring",
                   "dow_start": 0, "time_start": "25:99",
                   "dow_end": 0,   "time_end": "23:00"}
        r = admin_session.post(f"{BASE_URL}/api/wheel/admin/boost/schedules",
                                json=payload, timeout=15)
        # 422 if Pydantic regex catches "25:99", 400 if validator catches it.
        assert r.status_code in (400, 422), r.text[:200]

    def test_create_dated_ok(self, admin_session):
        payload = {"name": "TEST_dated_ok", "kind": "dated",
                   "date_start": "2026-05-01T00:00:00Z",
                   "date_end":   "2026-05-01T23:59:00Z"}
        r = admin_session.post(f"{BASE_URL}/api/wheel/admin/boost/schedules",
                                json=payload, timeout=15)
        assert r.status_code == 200, r.text[:200]
        sched = r.json()["schedule"]
        assert sched["kind"] == "dated"
        assert sched["date_start"] is not None
        admin_session.delete(
            f"{BASE_URL}/api/wheel/admin/boost/schedules/{sched['id']}", timeout=10)

    def test_create_dated_bad_iso(self, admin_session):
        payload = {"name": "TEST_dated_bad", "kind": "dated",
                   "date_start": "not-a-date",
                   "date_end":   "2026-05-01T23:59:00Z"}
        r = admin_session.post(f"{BASE_URL}/api/wheel/admin/boost/schedules",
                                json=payload, timeout=15)
        assert r.status_code == 400, r.text[:200]

    def test_create_dated_inverted_range(self, admin_session):
        payload = {"name": "TEST_dated_inv", "kind": "dated",
                   "date_start": "2026-05-02T00:00:00Z",
                   "date_end":   "2026-05-01T00:00:00Z"}
        r = admin_session.post(f"{BASE_URL}/api/wheel/admin/boost/schedules",
                                json=payload, timeout=15)
        assert r.status_code == 400, r.text[:200]

    def test_update_full_replace(self, admin_session):
        # Create then PUT
        payload = {"name": "TEST_upd_orig", "kind": "recurring",
                   "dow_start": 0, "time_start": "08:00",
                   "dow_end": 0,   "time_end": "10:00"}
        r = admin_session.post(f"{BASE_URL}/api/wheel/admin/boost/schedules",
                                json=payload, timeout=15)
        assert r.status_code == 200
        sid = r.json()["schedule"]["id"]
        new = {"name": "TEST_upd_new", "kind": "recurring",
               "dow_start": 1, "time_start": "09:00",
               "dow_end": 1,   "time_end": "11:00",
               "gain_multiplier": 2.0, "perdu_reduction_percent": 30}
        r2 = admin_session.put(
            f"{BASE_URL}/api/wheel/admin/boost/schedules/{sid}",
            json=new, timeout=15)
        assert r2.status_code == 200, r2.text[:200]
        body = r2.json()["schedule"]
        assert body["name"] == "TEST_upd_new"
        assert body["dow_start"] == 1
        assert abs(body["gain_multiplier"] - 2.0) < 1e-3
        # Cleanup
        admin_session.delete(
            f"{BASE_URL}/api/wheel/admin/boost/schedules/{sid}", timeout=10)

    def test_update_404(self, admin_session):
        new = {"name": "TEST_x", "kind": "recurring",
               "dow_start": 0, "time_start": "00:00",
               "dow_end": 0,   "time_end": "01:00"}
        r = admin_session.put(
            f"{BASE_URL}/api/wheel/admin/boost/schedules/99999999",
            json=new, timeout=15)
        assert r.status_code == 404

    def test_delete_404(self, admin_session):
        r = admin_session.delete(
            f"{BASE_URL}/api/wheel/admin/boost/schedules/99999999", timeout=10)
        assert r.status_code == 404

    def test_delete_ok(self, admin_session):
        payload = {"name": "TEST_del_ok", "kind": "recurring",
                   "dow_start": 0, "time_start": "00:00",
                   "dow_end": 0,   "time_end": "00:30"}
        r = admin_session.post(f"{BASE_URL}/api/wheel/admin/boost/schedules",
                                json=payload, timeout=15)
        assert r.status_code == 200
        sid = r.json()["schedule"]["id"]
        r2 = admin_session.delete(
            f"{BASE_URL}/api/wheel/admin/boost/schedules/{sid}", timeout=10)
        assert r2.status_code == 200
        body = r2.json()
        assert body["status"] == "ok"
        assert body["deleted_id"] == sid


# ──────────────────────────────────────────────────────────────────────────
#  Worker E2E (≥65s waits)
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.e2e
class TestBoostScheduleWorkerE2E:
    """Worker auto-enable / disable behavior (60s tick)"""

    def test_worker_activates_then_deactivates(self, admin_session):
        # Cleanup any leftover TEST_ schedules first (lower ids would steal the
        # active slot since _find_active_schedule sorts by id ASC).
        rl = admin_session.get(f"{BASE_URL}/api/wheel/admin/boost/schedules", timeout=15)
        if rl.status_code == 200:
            for s in rl.json().get("schedules", []):
                if (s.get("name") or "").startswith("TEST_"):
                    admin_session.delete(
                        f"{BASE_URL}/api/wheel/admin/boost/schedules/{s['id']}",
                        timeout=10)
        # Make sure no manual or scheduler boost is leftover.
        admin_session.put(
            f"{BASE_URL}/api/wheel/admin/boost",
            json={"enabled": False, "name": "off",
                  "gain_multiplier": 1.0, "perdu_reduction_percent": 0,
                  "unlock_jackpot_all_phases": False,
                  "jackpot_odds_during_boost": 25,
                  "starts_at": "", "ends_at": ""},
            timeout=15,
        )

        # Build a window covering NOW (UTC).
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=2)
        payload = {
            "name": "TEST_e2e_worker_now",
            "kind": "recurring",
            "dow_start": now.weekday(),
            "time_start": now.strftime("%H:%M"),
            "dow_end":   end.weekday(),
            "time_end":   end.strftime("%H:%M"),
            "gain_multiplier": 1.5,
            "perdu_reduction_percent": 50,
            "enabled": True,
        }
        r = admin_session.post(f"{BASE_URL}/api/wheel/admin/boost/schedules",
                                json=payload, timeout=15)
        assert r.status_code == 200, r.text[:200]
        sid = r.json()["schedule"]["id"]

        # Wait for worker tick (poll every 5s up to 130s — worker cycle is 60s).
        active = False
        for _ in range(26):
            time.sleep(5)
            try:
                rr = admin_session.get(f"{BASE_URL}/api/wheel/admin/boost", timeout=20)
            except requests.exceptions.RequestException:
                continue
            if rr.status_code == 200:
                live = rr.json().get("live", {})
                if live.get("active") and (live.get("settings", {}).get("id", "")
                                            .startswith(f"sched_{sid}_")):
                    active = True
                    break
        assert active, "Worker did not activate boost within 130s"

        # last_triggered_at should be populated
        rl = admin_session.get(f"{BASE_URL}/api/wheel/admin/boost/schedules", timeout=15)
        rows = rl.json()["schedules"]
        match = [s for s in rows if s["id"] == sid]
        assert match and match[0]["last_triggered_at"] is not None

        # Now delete the schedule -> worker should disable boost.
        rd = admin_session.delete(
            f"{BASE_URL}/api/wheel/admin/boost/schedules/{sid}", timeout=15)
        assert rd.status_code == 200

        deactivated = False
        for _ in range(26):
            time.sleep(5)
            try:
                rr = admin_session.get(f"{BASE_URL}/api/wheel/admin/boost", timeout=20)
            except requests.exceptions.RequestException:
                continue
            if rr.status_code == 200:
                live = rr.json().get("live", {})
                if not live.get("active"):
                    deactivated = True
                    break
        assert deactivated, "Worker did not deactivate boost within 130s after schedule delete"

    def test_worker_does_not_touch_manual_boost(self, admin_session):
        # Make sure we start clean
        admin_session.put(
            f"{BASE_URL}/api/wheel/admin/boost",
            json={"enabled": False, "name": "off",
                  "gain_multiplier": 1.0, "perdu_reduction_percent": 0,
                  "unlock_jackpot_all_phases": False,
                  "jackpot_odds_during_boost": 25,
                  "starts_at": "", "ends_at": ""},
            timeout=15,
        )
        # Enable a MANUAL boost
        manual_payload = {
            "enabled": True,
            "name": "TEST_manual_safety",
            "gain_multiplier": 2.0,
            "perdu_reduction_percent": 60,
            "unlock_jackpot_all_phases": False,
            "jackpot_odds_during_boost": 25,
            "starts_at": "",
            "ends_at": "",
        }
        r = admin_session.put(f"{BASE_URL}/api/wheel/admin/boost",
                               json=manual_payload, timeout=15)
        assert r.status_code == 200, r.text[:200]

        # Wait > 1 worker tick
        time.sleep(70)

        rr = admin_session.get(f"{BASE_URL}/api/wheel/admin/boost", timeout=15)
        assert rr.status_code == 200
        live = rr.json().get("live", {})
        assert live.get("active") is True, "Worker wrongfully disabled a manual boost!"
        assert live.get("name") == "TEST_manual_safety"

        # Cleanup: turn manual boost off
        admin_session.put(
            f"{BASE_URL}/api/wheel/admin/boost",
            json={**manual_payload, "enabled": False}, timeout=15,
        )
