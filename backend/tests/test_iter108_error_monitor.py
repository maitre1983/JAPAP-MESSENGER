"""iter108 — AI Error Monitor backend test suite.

Covers:
- POST /api/errors/report (public, rate-limited)
- Admin auth via superadmin login + 2FA OTP (read OTP from email_otps PG table)
- GET /api/admin/errors (filters + summary counters)
- GET /api/admin/errors/{signature}
- POST /api/admin/errors/{signature}/{action}  (investigate/fix/ignore/reopen)
- POST /api/admin/errors/{signature}/ai-suggest (Claude Sonnet 4.5)
- Auto-reopen of fixed group when same error recurs
"""
import os
import time
import uuid
import asyncio
import pytest
import requests
import asyncpg

BASE_URL = os.environ.get("TEST_BASE_URL") or os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
TIMEOUT = 60
DATABASE_URL = os.environ.get("DATABASE_URL") or "postgresql://neondb_owner:npg_YFaoTc01dJkx@ep-still-boat-algu2h2h-pooler.c-3.eu-central-1.aws.neon.tech/neondb?sslmode=require"

SUPERADMIN_EMAIL = "emileparfait2003@gmail.com"
SUPERADMIN_PASSWORD = "Gerard0103@"

# read frontend .env if present (BASE_URL fallback) — only if no env override
if "TEST_BASE_URL" not in os.environ:
    try:
        with open("/app/frontend/.env") as _f:
            for _l in _f:
                if _l.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = _l.split("=", 1)[1].strip().strip('"').rstrip("/")
    except Exception:
        pass


# ---------------- helpers ----------------

async def _fetch_recent_otp(email: str, purpose: str = "login_2fa") -> str:
    conn = await asyncpg.connect(DATABASE_URL.replace("&channel_binding=require", ""))
    try:
        row = await conn.fetchrow(
            """SELECT code FROM email_otps
                 WHERE email=$1 AND purpose=$2
                 ORDER BY created_at DESC LIMIT 1""",
            email, purpose,
        )
        return row["code"] if row else None
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def admin_session():
    """Returns a requests.Session() authenticated as superadmin via 2FA."""
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": SUPERADMIN_EMAIL, "password": SUPERADMIN_PASSWORD},
               timeout=TIMEOUT)
    if r.status_code != 200:
        pytest.skip(f"Superadmin login failed: {r.status_code} {r.text[:200]}")
    body = r.json()
    if body.get("status") != "otp_required":
        # already authenticated maybe
        return s
    # poll OTP table
    code = None
    for _ in range(8):
        time.sleep(1.0)
        try:
            code = asyncio.run(_fetch_recent_otp(SUPERADMIN_EMAIL))
        except Exception as e:
            pytest.skip(f"DB fetch OTP failed: {e}")
        if code:
            break
    if not code:
        pytest.skip("No 2FA OTP found in email_otps")
    r2 = s.post(f"{BASE_URL}/api/auth/verify-2fa",
                json={"email": SUPERADMIN_EMAIL, "code": code},
                timeout=TIMEOUT)
    if r2.status_code != 200:
        pytest.skip(f"verify-2fa failed: {r2.status_code} {r2.text[:200]}")
    return s


@pytest.fixture
def unique_msg():
    return f"TEST_iter108 synthetic FE error {uuid.uuid4().hex[:8]}"


# ---------------- Public report endpoint ----------------

class TestPublicReport:
    def test_report_minimal_payload(self, unique_msg):
        r = requests.post(f"{BASE_URL}/api/errors/report",
                          json={"source": "frontend", "module": "test.iter108",
                                "message": unique_msg, "severity": "high"},
                          timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("status") == "ok"
        assert isinstance(d.get("signature"), str) and len(d["signature"]) == 16

    def test_report_validates_severity(self):
        r = requests.post(f"{BASE_URL}/api/errors/report",
                          json={"source": "frontend", "module": "test",
                                "message": "x", "severity": "BOGUS"},
                          timeout=TIMEOUT)
        assert r.status_code == 422

    def test_report_validates_source(self):
        r = requests.post(f"{BASE_URL}/api/errors/report",
                          json={"source": "weird", "module": "test",
                                "message": "x"},
                          timeout=TIMEOUT)
        assert r.status_code == 422

    def test_grouping_signature_stable(self):
        msg = f"TEST_iter108 stable grouping {uuid.uuid4().hex[:6]}"
        r1 = requests.post(f"{BASE_URL}/api/errors/report",
                           json={"source": "frontend", "module": "test.group",
                                 "message": msg, "severity": "low"}, timeout=TIMEOUT)
        r2 = requests.post(f"{BASE_URL}/api/errors/report",
                           json={"source": "frontend", "module": "test.group",
                                 "message": msg, "severity": "low"}, timeout=TIMEOUT)
        assert r1.json()["signature"] == r2.json()["signature"]


# ---------------- Admin endpoints ----------------

class TestAdminErrors:
    def _seed(self, msg: str, sev: str = "high") -> str:
        r = requests.post(f"{BASE_URL}/api/errors/report",
                          json={"source": "frontend", "module": "test.admin_iter108",
                                "message": msg, "severity": sev}, timeout=TIMEOUT)
        assert r.status_code == 200
        return r.json()["signature"]

    def test_list_requires_admin(self):
        r = requests.get(f"{BASE_URL}/api/admin/errors", timeout=TIMEOUT)
        assert r.status_code in (401, 403)

    def test_list_groups(self, admin_session, unique_msg):
        sig = self._seed(unique_msg)
        r = admin_session.get(f"{BASE_URL}/api/admin/errors",
                              params={"since_days": 7, "limit": 200},
                              timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "items" in data and "summary" in data
        summary = data["summary"]
        for k in ("open_count", "investigating_count", "fixed_count", "ignored_count"):
            assert k in summary
        # our just-seeded sig should be in the list
        assert any(g["signature"] == sig for g in data["items"]), \
            f"Seeded signature {sig} missing from list"

    def test_get_group_detail(self, admin_session, unique_msg):
        sig = self._seed(unique_msg)
        r = admin_session.get(f"{BASE_URL}/api/admin/errors/{sig}", timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["group"]["signature"] == sig
        assert isinstance(d["events"], list) and len(d["events"]) >= 1

    def test_get_group_404(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/errors/deadbeefdeadbeef", timeout=TIMEOUT)
        assert r.status_code == 404

    def test_action_workflow_and_auto_reopen(self, admin_session, unique_msg):
        sig = self._seed(unique_msg)
        # investigate
        r = admin_session.post(f"{BASE_URL}/api/admin/errors/{sig}/investigate",
                               timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "investigating"
        # fix
        r = admin_session.post(f"{BASE_URL}/api/admin/errors/{sig}/fix", timeout=TIMEOUT)
        assert r.status_code == 200
        assert r.json()["status"] == "fixed"
        # auto-reopen by re-reporting
        self._seed(unique_msg)
        r = admin_session.get(f"{BASE_URL}/api/admin/errors/{sig}", timeout=TIMEOUT)
        assert r.status_code == 200
        assert r.json()["group"]["status"] == "open", \
            "Group should auto-reopen when same error recurs after 'fixed'"
        # ignore
        r = admin_session.post(f"{BASE_URL}/api/admin/errors/{sig}/ignore", timeout=TIMEOUT)
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"
        # reopen
        r = admin_session.post(f"{BASE_URL}/api/admin/errors/{sig}/reopen", timeout=TIMEOUT)
        assert r.status_code == 200
        assert r.json()["status"] == "open"

    def test_action_invalid(self, admin_session, unique_msg):
        sig = self._seed(unique_msg)
        r = admin_session.post(f"{BASE_URL}/api/admin/errors/{sig}/bogus", timeout=TIMEOUT)
        assert r.status_code == 400

    def test_filter_status(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/errors",
                              params={"status": "open", "since_days": 30, "limit": 50},
                              timeout=TIMEOUT)
        assert r.status_code == 200
        for g in r.json()["items"]:
            assert g["status"] == "open"

    def test_filter_invalid_status(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/errors",
                              params={"status": "xxx"}, timeout=TIMEOUT)
        assert r.status_code == 400


# ---------------- AI suggest ----------------

class TestAISuggest:
    def test_ai_suggest(self, admin_session):
        msg = f"TEST_iter108 AI suggest {uuid.uuid4().hex[:6]}: NoneType has no attribute id"
        r = requests.post(f"{BASE_URL}/api/errors/report",
                          json={"source": "backend", "module": "wallet.deposit",
                                "message": msg, "severity": "high",
                                "stack": "Traceback...\n  File 'a.py' line 1\nAttributeError"},
                          timeout=TIMEOUT)
        assert r.status_code == 200
        sig = r.json()["signature"]
        r = admin_session.post(f"{BASE_URL}/api/admin/errors/{sig}/ai-suggest",
                               timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["signature"] == sig
        sug = d["ai_suggestion"]
        assert isinstance(sug, dict)
        assert "summary" in sug or "root_cause" in sug
