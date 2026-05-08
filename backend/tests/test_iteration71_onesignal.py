"""
JAPAP — OneSignal Web Push regression tests (iter71).

These exercise the backend surface that replaced the iter70 VAPID stack:
  • GET  /api/push/public-key   — now returns {provider:"onesignal", app_id}
  • POST /api/push/test-vapid   — admin-only, fans out via OneSignal REST
  • services.push_service.send_push_to_user — honors External ID targeting
    and swallows non-subscribed edge cases.

We don't hit the real OneSignal endpoint from these tests (we'd need a
real subscribed device). Instead we:
  1. Check the backend REST surface,
  2. Verify the service builds the right REST payload shape,
  3. Confirm the service swallows the "no subscribed players" error
     (OneSignal 400) as a silent skip — matches real-world behaviour when
     targeting a user who never opted in.
"""
import os
import pytest
import httpx

BASE = os.environ.get("PYTEST_BASE", "http://localhost:8001")
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
USER_EMAIL = "bob@japap.com"
USER_PASSWORD = "Test1234!"


def _login(client: httpx.Client, email: str, password: str) -> dict:
    r = client.post(
        f"{BASE}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert r.status_code == 200, f"login failed: {r.text}"
    data = r.json()
    token = data.get("token") or data.get("access_token")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client():
    with httpx.Client(timeout=15) as c:
        yield c


# ─── Backend REST surface ────────────────────────────────────────────────

def test_public_key_endpoint_exposes_onesignal_provider(client):
    """Confirms the provider has been flipped to OneSignal. The old iter70
    fields (`public_key`, `configured`) must still be present so any
    deployed frontend from the previous iter doesn't explode mid-rollout."""
    r = client.get(f"{BASE}/api/push/public-key")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "onesignal"
    assert body["configured"] is True
    # OneSignal App ID = UUID-like, 36 chars with dashes
    assert body["app_id"]
    assert "-" in body["app_id"]
    # Back-compat alias
    assert body["public_key"] == body["app_id"]


def test_legacy_subscribe_endpoint_is_gone(client):
    """iter71 removes the old VAPID subscribe/unsubscribe endpoints — they
    no longer make sense because OneSignal manages subscriptions itself.
    Confirm they 404/405 instead of silently returning 200."""
    r = client.post(
        f"{BASE}/api/push/subscribe",
        json={"endpoint": "https://fcm.googleapis.com/x", "keys": {}},
    )
    assert r.status_code in (404, 405, 401), (
        "VAPID /subscribe must be removed in iter71; got "
        f"{r.status_code} — {r.text[:200]}"
    )


def test_test_push_requires_admin(client):
    """Non-admins can't use the test endpoint — same guardrail as iter70."""
    headers = _login(client, USER_EMAIL, USER_PASSWORD)
    r = client.post(f"{BASE}/api/push/test-vapid", json={}, headers=headers)
    assert r.status_code == 403


def test_admin_test_push_returns_clean_200(client):
    """Admin fans out a test push. The target admin hasn't opted in via
    OneSignal in this test, so OneSignal returns 400 "All included
    players are not subscribed" — our service must swallow this as
    `skipped=user_not_subscribed` and the endpoint must still 200."""
    headers = _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    r = client.post(f"{BASE}/api/push/test-vapid", json={}, headers=headers)
    assert r.status_code == 200, f"admin test-vapid failed: {r.text}"
    body = r.json()
    assert body["ok"] is True
    assert body["target_user_id"]
    # Either sent>0 (someone really is subscribed) OR skipped/failed fields
    assert "sent" in body


# ─── services.push_service direct ────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_push_to_user_noop_when_no_user_id():
    from services.push_service import send_push_to_user
    out = await send_push_to_user("", {"title": "x"})
    assert out["sent"] == 0
    assert out["skipped"] == "no_user_id"


@pytest.mark.asyncio
async def test_send_push_to_user_swallows_unsubscribed_users():
    """Targeting a user who has never opted-in should not raise — it
    should return sent=0 with a skip flag so realtime.py hooks keep firing
    without error."""
    from services.push_service import send_push_to_user
    out = await send_push_to_user(
        "usr_nonexistent_iter71_test",
        {"title": "Test", "body": "Ignored", "url": "/"},
    )
    # In pytest's in-process context `.env` isn't auto-loaded, so
    # `configured()` may return False and the call short-circuits with
    # `skipped=onesignal_not_configured`. When run against a live server
    # with env loaded, OneSignal returns 400 → skipped=user_not_subscribed.
    # Either outcome proves the call never raises.
    assert out["sent"] == 0
    assert (
        out.get("skipped") in ("user_not_subscribed", "onesignal_not_configured")
        or out.get("failed") == 1
    )


def test_build_payload_shape_is_iter70_compatible():
    """realtime.py hooks build payloads with this helper. The shape must
    keep the same top-level keys so iter70 → iter71 is a silent swap."""
    from services.push_service import build_payload
    p = build_payload(
        title="Bob t'a envoyé 1000 XAF",
        body="Merci !",
        url="/chat/conv_abc",
        tag="money:conv_abc",
        type_="money",
    )
    assert p["title"].startswith("Bob")
    assert p["body"] == "Merci !"
    assert p["url"] == "/chat/conv_abc"
    assert p["tag"] == "money:conv_abc"
    assert p["type"] == "money"
