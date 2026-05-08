"""Iter95 — Send-to-Audience: segments + migration batches + double-send + bg enqueue.

Covers:
  • GET  /api/admin/messaging/audience-options
  • POST /api/admin/messaging/templates/{id}/send-to-audience (guards + 200 + 409)
  • Batch status transitions
  • Background enqueue completion
  • Regressions: /segments listing, /campaigns/{id}/send
"""
import os
import time
import asyncio
import pytest
import requests
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv as _load_env
_load_env("/app/frontend/.env")
BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MIGRATION_SEG = "seg_migration_1to4"
TPL_ID = "tpl_sys_migration_1_to_4"
SAFE_SEG = "seg_pytest_safe"


def _mint_admin_token():
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    import jwt
    import asyncpg

    async def run():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            u = await conn.fetchrow("SELECT user_id, email FROM users WHERE email=$1", "admin@japap.com")
            now = datetime.now(timezone.utc)
            return jwt.encode(
                {"sub": u["user_id"], "email": u["email"], "type": "access",
                 "iat": int(now.timestamp()), "exp": now + timedelta(minutes=120)},
                os.environ["JWT_SECRET"], algorithm="HS256",
            )
        finally:
            await conn.close()
    return asyncio.run(run())


@pytest.fixture(scope="module")
def admin_headers():
    token = _mint_admin_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def cleanup_after(admin_headers):
    """Clean test-created campaigns + queue rows after the module finishes."""
    yield
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    import asyncpg

    async def cleanup():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            cids = await conn.fetch(
                "SELECT campaign_id FROM email_campaigns "
                "WHERE batch_key LIKE 'seg_migration_1to4:%' OR segment_id=$1",
                SAFE_SEG,
            )
            ids = [r["campaign_id"] for r in cids]
            if ids:
                await conn.execute("DELETE FROM email_send_queue WHERE campaign_id = ANY($1)", ids)
                await conn.execute("DELETE FROM email_campaigns WHERE campaign_id = ANY($1)", ids)
            print(f"[cleanup_after] Removed {len(ids)} test campaigns + their queue rows")
        finally:
            await conn.close()
    asyncio.run(cleanup())


# ═══════════════════ 1) AUDIENCE-OPTIONS shape ═══════════════════

class TestAudienceOptions:
    def test_returns_16_segments_and_6_batches(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/admin/messaging/audience-options",
                         headers=admin_headers, timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert len(d["segments"]) == 16, f"expected 16 segments, got {len(d['segments'])}"
        assert len(d["migration_batches"]) == 6
        seg_ids = {s["segment_id"] for s in d["segments"]}
        # migration seg MUST be excluded from flat list
        assert MIGRATION_SEG not in seg_ids
        # required new segments
        assert "seg_active_users" in seg_ids
        assert "seg_inactive_users" in seg_ids

    def test_batch_labels_and_sizes(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/admin/messaging/audience-options",
                         headers=admin_headers, timeout=60)
        batches = r.json()["migration_batches"]
        # First 5 batches: size 5000, label "Migration JAPAP 1.0 → 4.0 — Batch N / 5000"
        for i in range(5):
            b = batches[i]
            assert b["batch_key"] == f"{MIGRATION_SEG}:batch_{i+1:03d}"
            assert b["batch_index"] == i + 1
            assert b["batch_total"] == 6
            assert b["size"] == 5000
            assert b["label"] == f"Migration JAPAP 1.0 → 4.0 — Batch {i+1} / 5000"
        # Last batch: restant (3913)
        last = batches[5]
        assert last["batch_key"] == f"{MIGRATION_SEG}:batch_006"
        assert last["size"] == 3913
        assert "restant" in last["label"]
        assert "3913" in last["label"]

    def test_initial_batch_status_not_sent(self, admin_headers, cleanup_after):
        r = requests.get(f"{BASE_URL}/api/admin/messaging/audience-options",
                         headers=admin_headers, timeout=60)
        for b in r.json()["migration_batches"]:
            # after cleanup only — status should be 'not_sent' for all except the
            # one we actually send in test_send_batch_001_success. Just smoke-check
            # that key is present.
            assert "status" in b
            assert "campaign_id" in b


# ═══════════════════ 2) GUARDS (400 / 404) ═══════════════════

class TestSendGuards:
    def test_missing_confirm_returns_400(self, admin_headers):
        r = requests.post(
            f"{BASE_URL}/api/admin/messaging/templates/{TPL_ID}/send-to-audience",
            headers=admin_headers,
            json={"segment_id": MIGRATION_SEG, "batch_key": f"{MIGRATION_SEG}:batch_001"},
            timeout=30,
        )
        assert r.status_code == 400
        assert "confirm" in r.text.lower()

    def test_missing_segment_id_returns_400(self, admin_headers):
        r = requests.post(
            f"{BASE_URL}/api/admin/messaging/templates/{TPL_ID}/send-to-audience",
            headers=admin_headers,
            json={"confirm": True},
            timeout=30,
        )
        assert r.status_code == 400
        assert "segment_id" in r.text.lower()

    def test_migration_without_batch_key_returns_400(self, admin_headers):
        r = requests.post(
            f"{BASE_URL}/api/admin/messaging/templates/{TPL_ID}/send-to-audience",
            headers=admin_headers,
            json={"confirm": True, "segment_id": MIGRATION_SEG},
            timeout=30,
        )
        assert r.status_code == 400
        assert "batch_key" in r.text.lower()

    def test_invalid_batch_key_returns_400(self, admin_headers):
        r = requests.post(
            f"{BASE_URL}/api/admin/messaging/templates/{TPL_ID}/send-to-audience",
            headers=admin_headers,
            json={"confirm": True, "segment_id": MIGRATION_SEG,
                  "batch_key": "seg_other:batch_001"},
            timeout=30,
        )
        assert r.status_code == 400

    def test_non_existent_template_returns_404(self, admin_headers):
        r = requests.post(
            f"{BASE_URL}/api/admin/messaging/templates/tpl_does_not_exist/send-to-audience",
            headers=admin_headers,
            json={"confirm": True, "segment_id": MIGRATION_SEG,
                  "batch_key": f"{MIGRATION_SEG}:batch_001"},
            timeout=30,
        )
        assert r.status_code == 404


# ═══════════════════ 3) SUCCESS + DOUBLE-SEND + BG ENQUEUE ═══════════════════

class TestSendSuccess:
    """Uses batch_006 (smallest, 3913 users) to minimize DB load."""

    # Module-level state shared across methods via class attr
    campaign_id = None
    batch_key = f"{MIGRATION_SEG}:batch_006"

    def test_send_batch_006_returns_200_within_5s(self, admin_headers, cleanup_after):
        t0 = time.time()
        r = requests.post(
            f"{BASE_URL}/api/admin/messaging/templates/{TPL_ID}/send-to-audience",
            headers=admin_headers,
            json={"confirm": True, "segment_id": MIGRATION_SEG,
                  "batch_key": self.batch_key},
            timeout=60,
        )
        elapsed = time.time() - t0
        print(f"\n[send] batch_006 response time: {elapsed:.2f}s status={r.status_code}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:500]}"
        d = r.json()
        assert d["status"] == "sending"
        assert d["batch_key"] == self.batch_key
        assert d["batch_index"] == 6
        assert d["batch_total"] == 6
        assert 3800 <= d["audience_size"] <= 3913, f"audience_size={d['audience_size']}"
        assert d["enqueued"] == d["audience_size"]
        assert d["campaign_id"].startswith("cmp_")
        TestSendSuccess.campaign_id = d["campaign_id"]
        # Report latency separately — fail non-fatally if > 5s so we surface issue
        if elapsed > 5:
            pytest.fail(
                f"Send took {elapsed:.2f}s — requirement is <5s (fire-and-forget). "
                f"Campaign was created so rest of suite can continue."
            )

    def test_double_send_returns_409(self, admin_headers):
        assert TestSendSuccess.campaign_id is not None, "previous test must have run"
        r = requests.post(
            f"{BASE_URL}/api/admin/messaging/templates/{TPL_ID}/send-to-audience",
            headers=admin_headers,
            json={"confirm": True, "segment_id": MIGRATION_SEG,
                  "batch_key": self.batch_key},
            timeout=30,
        )
        assert r.status_code == 409, f"Expected 409, got {r.status_code}: {r.text}"
        assert "Double-envoi" in r.text or "déjà" in r.text

    def test_batch_status_updated_in_audience_options(self, admin_headers):
        assert TestSendSuccess.campaign_id is not None
        r = requests.get(f"{BASE_URL}/api/admin/messaging/audience-options",
                         headers=admin_headers, timeout=60)
        batches = r.json()["migration_batches"]
        b006 = next(b for b in batches if b["batch_key"] == self.batch_key)
        assert b006["status"] in ("sending", "sent", "pending")
        assert b006["campaign_id"] == TestSendSuccess.campaign_id

    def test_background_enqueue_populates_queue(self, admin_headers):
        """Wait up to 30s for background task to populate email_send_queue."""
        assert TestSendSuccess.campaign_id is not None
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        import asyncpg

        async def check_queue():
            conn = await asyncpg.connect(os.environ["DATABASE_URL"])
            try:
                deadline = time.time() + 30
                last_count = 0
                while time.time() < deadline:
                    n = await conn.fetchval(
                        "SELECT COUNT(*) FROM email_send_queue WHERE campaign_id=$1",
                        TestSendSuccess.campaign_id,
                    )
                    last_count = n
                    if n >= 3800:
                        return n
                    await asyncio.sleep(2)
                return last_count
            finally:
                await conn.close()

        count = asyncio.run(check_queue())
        assert count >= 3800, f"Only {count} queue rows after 30s (expected ~3900)"


# ═══════════════════ 4) NON-MIGRATION SEND + CAP ═══════════════════

class TestNonMigrationSend:
    def test_send_to_safe_segment_no_batch_key(self, admin_headers, cleanup_after):
        r = requests.post(
            f"{BASE_URL}/api/admin/messaging/templates/{TPL_ID}/send-to-audience",
            headers=admin_headers,
            json={"confirm": True, "segment_id": SAFE_SEG, "force": True},
            timeout=60,
        )
        # Expect either 200 (enqueued) OR 400 if audience empty after filter.
        assert r.status_code in (200, 400), f"Got {r.status_code}: {r.text}"
        if r.status_code == 200:
            d = r.json()
            assert d["batch_key"] is None
            assert d["status"] == "sending"


# ═══════════════════ 5) REGRESSIONS ═══════════════════

class TestRegressions:
    def test_segments_endpoint_includes_new_segments(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/admin/messaging/segments",
                         headers=admin_headers, timeout=30)
        assert r.status_code == 200
        data = r.json()
        # Shape can be a list or dict with 'segments' key
        segs = data.get("items") or data.get("segments") or (data if isinstance(data, list) else [])
        ids = {s["segment_id"] for s in segs}
        assert "seg_active_users" in ids
        assert "seg_inactive_users" in ids
        assert "seg_migration_1to4" in ids
        # Check migration seg has ~28908 count (actual env shows 28913)
        mig = next(s for s in segs if s["segment_id"] == "seg_migration_1to4")
        assert 28000 <= int(mig.get("estimated_count") or 0) <= 30000

    def test_turnstile_login_still_400_without_token(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": "admin@japap.com", "password": "x"},
                          timeout=30)
        # 400 because missing turnstile_token (regression from iter94)
        assert r.status_code == 400

    def test_existing_campaign_send_endpoint_still_available(self, admin_headers):
        # Just reach the endpoint; expect 404 for bogus campaign (not 500)
        r = requests.post(
            f"{BASE_URL}/api/admin/messaging/campaigns/cmp_nonexistent/send",
            headers=admin_headers, json={"confirm": True}, timeout=30,
        )
        assert r.status_code in (404, 400, 403), f"unexpected {r.status_code}"
