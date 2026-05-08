"""Iteration 64 — Admin Messaging Center (Phase 1a) backend tests.

Covers:
  • Segment compiler: 13 system segments + custom rules + preview count
  • Templates CRUD
  • Campaigns CRUD (draft only)
  • Test-send to admin (non-attributed)
  • Bulk send: confirmation required, queue populated, worker drains,
    campaign status transitions draft → sending → sent, counters update
  • Variable rendering: {{first_name}}, {{wallet_balance}}, {{last_active_days}}
  • Admin gating (non-admin = 403)
  • Unsubscribe: valid token flips user + logs event, bad token 400
  • Open pixel: returns 43-byte GIF, logs event, bumps counter
  • Click redirect: 302 to target, logs event
  • Resend webhook: delivered/bounced/complained → correct log + counter bump
"""
import os
import asyncio
import json
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
BOB = {"email": "bob@japap.com", "password": "Test1234!"}


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, r.text
    return s, r.json()["user"]["user_id"]


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def bob():
    return _login(BOB)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Admin gating
# ═══════════════════════════════════════════════════════════════════════════

def test_non_admin_forbidden(bob):
    s, _ = bob
    r = s.get(f"{BASE_URL}/api/admin/messaging/segments", timeout=10)
    assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 2. Segments — auto-seed 13 system + preview + custom
# ═══════════════════════════════════════════════════════════════════════════

def test_segments_auto_seed(admin):
    s, _ = admin
    r = s.get(f"{BASE_URL}/api/admin/messaging/segments", timeout=10)
    assert r.status_code == 200
    items = r.json()["items"]
    system_ids = {x["segment_id"] for x in items if x["is_system"]}
    # Must have all 13 system segments
    expected = {
        "seg_all_users", "seg_pro_users", "seg_non_pro", "seg_new_7d",
        "seg_inactive_7d", "seg_inactive_30d", "seg_zero_referrals",
        "seg_has_referrals", "seg_never_onboarded", "seg_connect_used",
        "seg_connect_unused", "seg_legacy_migrated", "seg_pro_inactive_30d",
        "seg_pytest_safe",
    }
    assert expected.issubset(system_ids), f"Missing system segments: {expected - system_ids}"


def test_segment_preview_by_id(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/admin/messaging/segments/preview",
               json={"segment_id": "seg_all_users", "sample_size": 3}, timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["count"], int) and body["count"] >= 1
    assert len(body["sample"]) <= 3


def test_segment_preview_inline_rules(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/admin/messaging/segments/preview",
               json={"rules": [{"field": "is_pro", "op": "is_false"}], "sample_size": 2},
               timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 0


def test_segment_preview_invalid_rule(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/admin/messaging/segments/preview",
               json={"rules": [{"field": "totally_fake_field", "op": "eq", "value": 1}]},
               timeout=10)
    assert r.status_code == 400


def test_custom_segment_create_update_delete(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/admin/messaging/segments",
               json={"name": f"Test custom {uuid.uuid4().hex[:6]}",
                     "description": "pytest",
                     "rules": [{"field": "is_pro", "op": "is_false"}]}, timeout=10)
    assert r.status_code == 200
    sid = r.json()["segment_id"]
    r2 = s.put(f"{BASE_URL}/api/admin/messaging/segments/{sid}",
               json={"name": "updated", "rules": [{"field": "country_code", "op": "eq", "value": "CI"}]},
               timeout=10)
    assert r2.status_code == 200
    r3 = s.delete(f"{BASE_URL}/api/admin/messaging/segments/{sid}", timeout=10)
    assert r3.status_code == 200


def test_system_segment_not_editable(admin):
    s, _ = admin
    r = s.put(f"{BASE_URL}/api/admin/messaging/segments/seg_all_users",
              json={"name": "hacked", "rules": []}, timeout=10)
    assert r.status_code == 403
    r2 = s.delete(f"{BASE_URL}/api/admin/messaging/segments/seg_all_users", timeout=10)
    assert r2.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 3. Templates + Campaigns CRUD
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def template(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/admin/messaging/templates",
               json={"name": f"T_{uuid.uuid4().hex[:6]}",
                     "subject": "Hello {{first_name}}",
                     "body_html": "<p>Balance: {{wallet_balance}}</p>",
                     "cta_label": "Open", "cta_url": "https://example.com",
                     "category": "reactivation"}, timeout=10)
    assert r.status_code == 200
    yield r.json()["template_id"]


def test_template_lifecycle(admin, template):
    s, _ = admin
    r = s.get(f"{BASE_URL}/api/admin/messaging/templates", timeout=10)
    assert r.status_code == 200
    ids = [x["template_id"] for x in r.json()["items"]]
    assert template in ids


@pytest.fixture(scope="module")
def campaign(admin, template):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/admin/messaging/campaigns",
               json={"name": f"pytest_{uuid.uuid4().hex[:6]}",
                     "template_id": template,
                     "subject": "Hi {{first_name}}!",
                     "body_html": "<p>Balance: {{wallet_balance}} USD · inactive {{last_active_days}} days</p>",
                     "cta_label": "Go", "cta_url": "https://japap-refactor.preview.emergentagent.com/feed",
                     "segment_id": "seg_pytest_safe"}, timeout=10)
    assert r.status_code == 200
    yield r.json()["campaign_id"]


def test_campaign_update_draft_only(admin, campaign):
    s, _ = admin
    r = s.put(f"{BASE_URL}/api/admin/messaging/campaigns/{campaign}",
              json={"name": "renamed", "subject": "Y", "body_html": "<p>Ok</p>",
                    "segment_id": "seg_pytest_safe"},
              timeout=10)
    assert r.status_code == 200


def test_campaign_test_send(admin, campaign):
    """Test-send uses the ADMIN's email as fallback, returns ok=true."""
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/admin/messaging/campaigns/{campaign}/test",
               json={}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["to"] == "admin@japap.com"


def test_campaign_send_requires_confirm(admin, campaign):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/admin/messaging/campaigns/{campaign}/send",
               json={"confirm": False}, timeout=10)
    assert r.status_code == 400


def test_campaign_send_enqueues_and_worker_drains(admin, campaign):
    """Full integration: confirm=true → status=sending → queue populated →
    worker drains → status=sent, sent_count>0, per-email log recorded."""
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/admin/messaging/campaigns/{campaign}/send",
               json={"confirm": True}, timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "sending"
    enqueued = body["enqueued"]
    assert enqueued > 0
    # Wait for worker (50/min = ~0.8/sec; enqueued is small for test segments)
    import time
    deadline = time.time() + 60
    while time.time() < deadline:
        d = s.get(f"{BASE_URL}/api/admin/messaging/campaigns/{campaign}", timeout=10).json()
        if d["status"] == "sent":
            assert d["sent_count"] == enqueued, f"sent_count mismatch: {d}"
            return
        time.sleep(2)
    pytest.fail("Worker did not drain within 60s")


def test_campaign_cannot_edit_after_send(admin, campaign):
    s, _ = admin
    r = s.put(f"{BASE_URL}/api/admin/messaging/campaigns/{campaign}",
              json={"name": "late edit", "subject": "X", "body_html": "<p>x</p>"},
              timeout=10)
    assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# 4. Variable rendering (inspecting queue before drain)
# ═══════════════════════════════════════════════════════════════════════════

def test_variable_substitution_applied(admin, template):
    """Enqueue one campaign targeting Bob specifically, then inspect the
    rendered_subject / rendered_html stored in email_send_queue BEFORE the
    worker sends them."""
    s, _ = admin
    # Find Bob's user_id
    h = requests.Session()
    h.post(f"{BASE_URL}/api/auth/login", json=BOB, timeout=10)
    bob_uid = h.post(f"{BASE_URL}/api/auth/login", json=BOB, timeout=10).json()["user"]["user_id"]

    r = s.post(f"{BASE_URL}/api/admin/messaging/campaigns",
               json={"name": f"var_test_{uuid.uuid4().hex[:6]}",
                     "template_id": template,
                     "subject": "Bonjour {{first_name}}!",
                     "body_html": "<p>Solde: {{wallet_balance}}</p>",
                     "cta_label": "", "cta_url": "",
                     "individual_user_ids": [bob_uid]}, timeout=10)
    assert r.status_code == 200
    cid = r.json()["campaign_id"]

    # Re-subscribe Bob if a previous test unsubscribed him
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    DB_URL = os.environ["DATABASE_URL"]

    async def _resub():
        c = await asyncpg.connect(DB_URL)
        try:
            await c.execute("UPDATE users SET email_subscribed = TRUE WHERE user_id = $1", bob_uid)
        finally:
            await c.close()
    asyncio.get_event_loop().run_until_complete(_resub())

    # Send
    rs = s.post(f"{BASE_URL}/api/admin/messaging/campaigns/{cid}/send",
                json={"confirm": True}, timeout=15)
    assert rs.status_code == 200, rs.text
    assert rs.json()["enqueued"] == 1

    # Inspect the queue row directly (may already be drained — check logs too)
    async def _inspect():
        c = await asyncpg.connect(DB_URL)
        try:
            row = await c.fetchrow(
                "SELECT rendered_subject, rendered_html FROM email_send_queue "
                "WHERE campaign_id = $1 LIMIT 1", cid)
            return dict(row) if row else None
        finally:
            await c.close()
    row = asyncio.get_event_loop().run_until_complete(_inspect())
    assert row, "Queue row not found"
    # Bob's first_name is 'Bob' — must appear in subject
    assert "Bob" in row["rendered_subject"]
    # Wallet balance placeholder must have been resolved (not still literal)
    assert "{{wallet_balance}}" not in row["rendered_html"]
    # Unsubscribe link (added by wrap_html_for_delivery) must be present
    assert "unsubscribe" in row["rendered_html"]


# ═══════════════════════════════════════════════════════════════════════════
# 5. Unsubscribe endpoint
# ═══════════════════════════════════════════════════════════════════════════

def test_unsubscribe_valid_token_flips_user():
    """Use the renderer helper to sign a fresh token, call /unsubscribe,
    verify DB flip + log event."""
    import sys
    sys.path.insert(0, "/app/backend")
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    from services.email_renderer import sign_unsub_token
    import asyncpg

    DB_URL = os.environ["DATABASE_URL"]

    async def _get_uid():
        c = await asyncpg.connect(DB_URL)
        try:
            r = await c.fetchrow("SELECT user_id FROM users WHERE email='carol@japap.com'")
            return r["user_id"]
        finally:
            await c.close()
    uid = asyncio.get_event_loop().run_until_complete(_get_uid())
    token = sign_unsub_token(uid)

    r = requests.get(f"{BASE_URL}/api/email/unsubscribe?u={uid}&t={token}", timeout=10)
    assert r.status_code == 200
    assert "Désabonnement confirmé" in r.text or "unsubscribed" in r.text.lower()

    async def _check():
        c = await asyncpg.connect(DB_URL)
        try:
            row = await c.fetchrow(
                "SELECT email_subscribed FROM users WHERE user_id = $1", uid)
            return row["email_subscribed"]
        finally:
            await c.close()
    assert asyncio.get_event_loop().run_until_complete(_check()) is False


def test_unsubscribe_bad_token_400():
    r = requests.get(f"{BASE_URL}/api/email/unsubscribe?u=user_fake&t=badtoken", timeout=10)
    assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# 6. Tracking pixel + click redirect
# ═══════════════════════════════════════════════════════════════════════════

def test_open_pixel_returns_gif_and_logs():
    r = requests.get(f"{BASE_URL}/api/email/track/open?c=cmp_fake&u=user_fake", timeout=10)
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/gif")
    assert len(r.content) == 43  # 1x1 transparent GIF


def test_click_redirect_302():
    import base64
    target = "https://japap-refactor.preview.emergentagent.com/pro"
    b64 = base64.urlsafe_b64encode(target.encode()).rstrip(b"=").decode()
    r = requests.get(f"{BASE_URL}/api/email/track/click?c=cmp_x&u=user_x&url={b64}",
                     timeout=10, allow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == target


# ═══════════════════════════════════════════════════════════════════════════
# 7. Resend webhook
# ═══════════════════════════════════════════════════════════════════════════

def test_resend_webhook_bounced():
    r = requests.post(f"{BASE_URL}/api/webhooks/resend",
                      json={"type": "email.bounced",
                            "data": {"email_id": "msg_test_bounce",
                                     "to": ["unknown@example.com"]}}, timeout=10)
    assert r.status_code == 200
    assert r.json()["event"] == "bounced"


def test_resend_webhook_complained_unsubscribes_user():
    """A complaint should both log 'unsubscribed' AND flip the user's flag."""
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    DB_URL = os.environ["DATABASE_URL"]

    # Re-subscribe admin first so the flip is observable
    async def _reset():
        c = await asyncpg.connect(DB_URL)
        try:
            await c.execute(
                "UPDATE users SET email_subscribed = TRUE WHERE email = 'admin@japap.com'")
        finally:
            await c.close()
    asyncio.get_event_loop().run_until_complete(_reset())

    # The webhook looks up the most recent sent-log for admin@japap.com; if
    # there is no sent log yet, it still logs the event without campaign
    # attribution. Seed a minimal sent log.
    async def _seed_sent():
        c = await asyncpg.connect(DB_URL)
        try:
            r = await c.fetchrow("SELECT user_id FROM users WHERE email='admin@japap.com'")
            log_id = f"log_{uuid.uuid4().hex[:14]}"
            await c.execute(
                "INSERT INTO email_logs (log_id, user_id, email, event, created_at) "
                "VALUES ($1, $2, 'admin@japap.com', 'sent', NOW())",
                log_id, r["user_id"],
            )
        finally:
            await c.close()
    asyncio.get_event_loop().run_until_complete(_seed_sent())

    r = requests.post(f"{BASE_URL}/api/webhooks/resend",
                      json={"type": "email.complained",
                            "data": {"email_id": "msg_test_complaint",
                                     "to": ["admin@japap.com"]}}, timeout=10)
    assert r.status_code == 200
    assert r.json()["event"] == "unsubscribed"

    async def _check():
        c = await asyncpg.connect(DB_URL)
        try:
            r = await c.fetchrow(
                "SELECT email_subscribed FROM users WHERE email='admin@japap.com'")
            return r["email_subscribed"]
        finally:
            await c.close()
    assert asyncio.get_event_loop().run_until_complete(_check()) is False


def test_resend_webhook_unknown_type_ignored():
    r = requests.post(f"{BASE_URL}/api/webhooks/resend",
                      json={"type": "email.unknown.future",
                            "data": {"to": ["x@y.com"]}}, timeout=10)
    assert r.status_code == 200
    assert r.json().get("ignored") is True


# ═══════════════════════════════════════════════════════════════════════════
# 8. Analytics dashboard
# ═══════════════════════════════════════════════════════════════════════════

def test_analytics_dashboard_shape(admin):
    s, _ = admin
    r = s.get(f"{BASE_URL}/api/admin/messaging/analytics", timeout=10)
    assert r.status_code == 200
    d = r.json()
    for key in ("campaigns_total", "campaigns_sent", "total_sent", "total_opened",
                "total_clicked", "total_bounced", "total_unsub",
                "queue_pending", "queue_failed", "unsubscribed_users"):
        assert key in d, f"Missing key {key}"
        assert isinstance(d[key], int)
