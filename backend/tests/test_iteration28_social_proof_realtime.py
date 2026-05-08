"""
Iteration 28 — Social Proof widget + notify_connect_revshare + dynamic pct.

Covers:
- GET /api/pro/social-proof shape & dynamic pct
- PUT /api/admin/settings propagates connect_revshare_pct to:
    /api/pro/social-proof, /api/connect/me.revshare.pct, /api/settings/public
- notify_connect_revshare emits Socket.IO event with correct payload
- Seeded referral_rewards_log rows surface in social-proof counts
"""
import os
import sys
import uuid
import asyncio
from decimal import Decimal
import pytest
import requests

sys.path.insert(0, "/app/backend")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
USER = {"email": "bob@japap.com", "password": "Test1234!"}


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return s, r.json().get("user") or r.json()


@pytest.fixture(scope="module")
def admin_sess():
    s, _ = _login(ADMIN["email"], ADMIN["password"])
    return s


@pytest.fixture(scope="module")
def user_sess():
    s, u = _login(USER["email"], USER["password"])
    return s, u


@pytest.fixture(scope="module", autouse=True)
def reset_pct_after(admin_sess):
    yield
    admin_sess.put(f"{BASE_URL}/api/admin/settings",
                   json={"settings": {"connect_revshare_pct": "2.0", "connect_revshare_pro_enabled": "true"}},
                   timeout=10)


# ------------------------------------------------------------------
# 1) GET /api/pro/social-proof — shape + auth
# ------------------------------------------------------------------
class TestSocialProofEndpoint:
    def test_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/pro/social-proof", timeout=10)
        assert r.status_code in (401, 403), f"expected auth required, got {r.status_code}"

    def test_shape(self, user_sess):
        s, _ = user_sess
        r = s.get(f"{BASE_URL}/api/pro/social-proof", timeout=10)
        assert r.status_code == 200, r.text
        d = r.json()
        # top-level
        for k in ("enabled", "pct", "last_30d", "all_time"):
            assert k in d, f"missing key {k}"
        assert isinstance(d["enabled"], bool)
        assert isinstance(d["pct"], (int, float))
        # last_30d
        for k in ("owner_count", "total_usd", "credits", "last_at"):
            assert k in d["last_30d"]
        assert isinstance(d["last_30d"]["owner_count"], int)
        assert isinstance(d["last_30d"]["credits"], int)
        # all_time
        for k in ("owner_count", "total_usd"):
            assert k in d["all_time"]


# ------------------------------------------------------------------
# 2) DYNAMIC pct — PUT admin settings → propagates to all 3 endpoints
# ------------------------------------------------------------------
class TestDynamicPct:
    def test_pct_propagates_5p5(self, admin_sess, user_sess):
        s_u, _ = user_sess
        # Set to 5.5 via bulk admin update
        r = admin_sess.put(f"{BASE_URL}/api/admin/settings",
                           json={"settings": {"connect_revshare_pct": "5.5"}}, timeout=10)
        assert r.status_code == 200, r.text

        # /api/pro/social-proof
        sp = s_u.get(f"{BASE_URL}/api/pro/social-proof", timeout=10).json()
        assert abs(float(sp["pct"]) - 5.5) < 1e-6, f"social-proof pct={sp['pct']}"

        # /api/connect/me
        me = s_u.get(f"{BASE_URL}/api/connect/me", timeout=10).json()
        assert abs(float(me["revshare"]["pct"]) - 5.5) < 1e-6, f"connect/me revshare.pct={me['revshare']['pct']}"

        # /api/settings/public
        pub = requests.get(f"{BASE_URL}/api/settings/public", timeout=10).json()
        assert "connect_revshare_pct" in pub, "connect_revshare_pct missing from public settings"
        assert abs(float(pub["connect_revshare_pct"]) - 5.5) < 1e-6, f"public={pub['connect_revshare_pct']}"

    def test_pct_reset_to_2(self, admin_sess, user_sess):
        s_u, _ = user_sess
        r = admin_sess.put(f"{BASE_URL}/api/admin/settings",
                           json={"settings": {"connect_revshare_pct": "2.0"}}, timeout=10)
        assert r.status_code == 200
        sp = s_u.get(f"{BASE_URL}/api/pro/social-proof", timeout=10).json()
        assert abs(float(sp["pct"]) - 2.0) < 1e-6


# ------------------------------------------------------------------
# 3) Seeded referral_rewards_log rows surface into social-proof
# ------------------------------------------------------------------
class TestSocialProofData:
    _inserted_ids = []

    def test_insert_and_surface(self, user_sess):
        s, me = user_sess
        # Need DB access - use asyncpg directly via env
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        import asyncpg

        async def _seed_and_read():
            dsn = os.environ["DATABASE_URL"]
            conn = await asyncpg.connect(dsn)
            try:
                # Baseline counts
                before = await conn.fetchrow("""
                    SELECT COUNT(*) AS n, COALESCE(SUM(amount_usd),0) AS s
                    FROM referral_rewards_log WHERE role='connect_revshare'
                """)
                uid = me.get("user_id") or me.get("id")
                assert uid, f"no user_id in {me}"
                # Insert 2 test rows — track ids via unique tag in details
                tag = f"TEST_iter28_{uuid.uuid4().hex[:10]}"
                ids = [tag]
                for amt, local in [("0.60", 600), ("0.40", 400)]:
                    await conn.execute("""
                        INSERT INTO referral_rewards_log
                            (user_id, role, reward_type, amount_usd, amount_local, currency, details, created_at)
                        VALUES ($1, 'connect_revshare', 'wallet', $2, $3, 'XAF', $4::jsonb, NOW())
                    """, uid, Decimal(amt), Decimal(local),
                       '{"reason":"' + tag + '"}')
                after = await conn.fetchrow("""
                    SELECT COUNT(*) AS n, COALESCE(SUM(amount_usd),0) AS s
                    FROM referral_rewards_log WHERE role='connect_revshare'
                """)
                return ids, before, after
            finally:
                await conn.close()

        ids, before, after = asyncio.run(_seed_and_read())
        TestSocialProofData._inserted_ids = ids
        assert after["n"] - before["n"] == 2
        assert float(after["s"] - before["s"]) == pytest.approx(1.0, abs=0.001)

        # Now read social-proof and verify increases
        sp = s.get(f"{BASE_URL}/api/pro/social-proof", timeout=10).json()
        assert sp["last_30d"]["owner_count"] >= 1
        assert sp["last_30d"]["credits"] >= 2
        assert float(sp["last_30d"]["total_usd"]) >= 1.0
        assert sp["all_time"]["owner_count"] >= 1
        assert float(sp["all_time"]["total_usd"]) >= 1.0
        assert sp["last_30d"]["last_at"] is not None

    def test_cleanup(self):
        ids = TestSocialProofData._inserted_ids
        if not ids:
            pytest.skip("nothing to clean")
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        import asyncpg

        async def _del():
            dsn = os.environ["DATABASE_URL"]
            conn = await asyncpg.connect(dsn)
            try:
                tag = ids[0]
                await conn.execute(
                    "DELETE FROM referral_rewards_log WHERE details->>'reason' = $1", tag)
            finally:
                await conn.close()

        asyncio.run(_del())


# ------------------------------------------------------------------
# 4) notify_connect_revshare — event name, payload shape (unit)
# ------------------------------------------------------------------
class TestNotifyConnectRevshare:
    def test_emits_correct_payload(self):
        """Unit-check the realtime helper emits 'notify_connect_revshare' with
        a priority=high payload and a dynamic pct."""
        from routes import realtime

        captured = {}

        class FakeSio:
            async def emit(self, event, payload, room=None):
                captured["event"] = event
                captured["payload"] = payload
                captured["room"] = room

        realtime.init_realtime(FakeSio(), {"owner_x": ["sid_1"]})

        asyncio.run(realtime.notify_connect_revshare(
            "owner_x", "0.60", "600", "XAF", "Creator Pro", 5.5))

        assert captured.get("event") == "notify_connect_revshare"
        p = captured.get("payload") or {}
        assert p.get("type") == "connect_revshare"
        assert p.get("priority") == "high"
        assert p.get("amount_usd") == "0.60"
        assert p.get("amount_local") == "600"
        assert p.get("currency") == "XAF"
        assert p.get("plan_name") == "Creator Pro"
        assert float(p.get("pct")) == 5.5
        assert captured.get("room") == "sid_1"

    def test_noop_when_offline(self):
        from routes import realtime
        class FakeSio:
            def __init__(self): self.calls = 0
            async def emit(self, *a, **kw): self.calls += 1
        sio = FakeSio()
        realtime.init_realtime(sio, {})  # no sids for user
        asyncio.run(realtime.notify_connect_revshare(
            "ghost", "0.10", None, "USD", "Starter Pro", 2.0))
        assert sio.calls == 0
