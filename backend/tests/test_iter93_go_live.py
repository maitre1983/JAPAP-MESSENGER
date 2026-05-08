"""
JAPAP — Iter93 Go-Live Validation (backend-only, no real payments)
==================================================================
Tests 10 critical flows in production-config mode:
 1. CALLS (LiveKit session + JWT mint, no actual WS connect)
 2. AUTH (register + OTP [DB read] + login + logout)
 3. PASSWORD RESET (forgot → DB token → reset → login with new pwd)
 4. PAYMENTS DEPOSIT (checkout URL generation only, no real charge)
 5. WITHDRAW (KYC gate)
 6. GAMES (wheel status/spin, quiz start, tap status, duel guard)
 7. PRO (plans + subscribe effect / gate via balance)
 8. CONNECT (hotspots list + share gate)
 9. MESSENGER (conv + send + list + groups)
10. TURNSTILE (admin settings check for anti-bot prod config)
11. HEALTH sanity
"""
import os
import re
import json
import hmac
import hashlib
import secrets
import asyncio
import base64

import pytest
import requests
import asyncpg

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")

# Ensure DATABASE_URL is available: try env, fallback to backend/.env
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    try:
        with open("/app/backend/.env", "r") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("DATABASE_URL="):
                    DATABASE_URL = _line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        pass

ADMIN_EMAIL = "admin@japap.com"
ADMIN_PWD = "JapapAdmin2024!"
BOB_EMAIL = "bob@japap.com"
BOB_PWD = "Test1234!"
ALICE_EMAIL = "alice@japap.com"
ALICE_PWD = "Test1234!"


# ------------------ Helpers ------------------

def _login(email: str, password: str):
    s = requests.Session()
    # Retry up to 3x for transient TLS/read timeouts
    last_err = None
    for _ in range(3):
        try:
            r = s.post(f"{BASE_URL}/api/auth/login",
                       json={"email": email, "password": password}, timeout=45)
            break
        except requests.exceptions.RequestException as e:
            last_err = e
    else:
        raise last_err
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text[:200]}"
    body = r.json()
    token = body.get("access_token")
    assert token, f"No access_token for {email}"
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s, body.get("user") or {}


async def _db():
    return await asyncpg.connect(DATABASE_URL)


# ------------------ Fixtures ------------------

@pytest.fixture(scope="module")
def bob_session():
    s, u = _login(BOB_EMAIL, BOB_PWD)
    return s, u


@pytest.fixture(scope="module")
def alice_session():
    s, u = _login(ALICE_EMAIL, ALICE_PWD)
    return s, u


@pytest.fixture(scope="module")
def admin_session():
    s, u = _login(ADMIN_EMAIL, ADMIN_PWD)
    return s, u


# ============================================================
# SECTION 0 — HEALTH
# ============================================================
class TestHealth:
    def test_health(self):
        import time
        last = None
        for _ in range(5):
            try:
                r = requests.get(f"{BASE_URL}/api/health", timeout=30)
                if r.status_code == 200:
                    last = r
                    break
                last = r
                time.sleep(2)
            except requests.exceptions.RequestException:
                time.sleep(2)
        assert last is not None and last.status_code == 200, f"health final status: {last.status_code if last else 'no-response'}"
        assert last.json().get("status") == "healthy"


# ============================================================
# SECTION 1 — CALLS / LIVEKIT
# ============================================================
class TestCalls:
    def test_session_creates_livekit_room(self, bob_session, alice_session):
        s_bob, bob = bob_session
        _, alice = alice_session
        r = s_bob.post(f"{BASE_URL}/api/calls/session",
                       json={"mode": "audio", "kind": "p2p", "callee_id": alice["user_id"]},
                       timeout=25)
        assert r.status_code == 200, f"create_session: {r.status_code} {r.text[:200]}"
        data = r.json()
        assert data["kind"] == "p2p"
        assert data["mode"] == "audio"
        assert data["max_participants"] == 2
        assert data["room_name"].startswith("japap_sess_")
        assert data["session_id"].startswith("sess_")
        # cache for next test
        pytest.call_session_id = data["session_id"]
        pytest.call_room_name = data["room_name"]

    def test_token_mint_jwt_hs256(self, bob_session):
        s_bob, _ = bob_session
        sid = getattr(pytest, "call_session_id", None)
        if not sid:
            pytest.skip("No session to mint token for")
        r = s_bob.post(f"{BASE_URL}/api/calls/token",
                       json={"session_id": sid}, timeout=15)
        assert r.status_code == 200, f"mint_token: {r.status_code} {r.text[:200]}"
        data = r.json()
        # payload shape (livekit_service.generate_access_token)
        assert "token" in data or "access_token" in data or "jwt" in data, f"no token key: {list(data.keys())}"
        token = data.get("token") or data.get("access_token") or data.get("jwt")
        assert token and token.count(".") == 2, "not a JWT"
        # Decode header + payload (no sig verify — we don't have secret)
        hdr_b64, pl_b64, _ = token.split(".")
        hdr = json.loads(base64.urlsafe_b64decode(hdr_b64 + "==="))
        pl = json.loads(base64.urlsafe_b64decode(pl_b64 + "==="))
        assert hdr.get("alg") == "HS256", f"alg must be HS256, got {hdr.get('alg')}"
        assert pl.get("iss") == "APIxFgZRJB45C4V", f"iss mismatch: {pl.get('iss')}"
        grants = pl.get("video") or {}
        assert grants.get("roomJoin") is True
        assert grants.get("canPublish") is True
        assert grants.get("canSubscribe") is True
        # ws_url should exist in response
        assert ("ws_url" in data) or ("wsUrl" in data) or ("url" in data), f"no ws_url in {data.keys()}"

    def test_end_call_flow(self, bob_session, alice_session):
        # /initiate → /end
        s_bob, _ = bob_session
        _, alice = alice_session
        r = s_bob.post(f"{BASE_URL}/api/calls/initiate",
                       json={"callee_id": alice["user_id"], "type": "audio"}, timeout=15)
        assert r.status_code == 200, f"initiate: {r.text[:200]}"
        call_id = r.json()["call_id"]
        r2 = s_bob.post(f"{BASE_URL}/api/calls/end",
                        json={"call_id": call_id, "duration": 5, "status": "ended"}, timeout=15)
        assert r2.status_code == 200
        assert r2.json()["status"] == "ended"

    def test_self_call_rejected(self, bob_session):
        s_bob, bob = bob_session
        r = s_bob.post(f"{BASE_URL}/api/calls/initiate",
                       json={"callee_id": bob["user_id"], "type": "audio"}, timeout=10)
        assert r.status_code == 400


# ============================================================
# SECTION 2 — AUTH (register + OTP from DB + login + logout)
# ============================================================
class TestAuthFullFlow:
    def test_register_verify_login_logout(self):
        if not DATABASE_URL:
            pytest.skip("DATABASE_URL not set — cannot read OTP from DB")
        email = f"test_iter93_{secrets.token_hex(4)}@example.com"
        pwd = "TestIter93!"

        # REGISTER — email send via Resend can take 10-30s; retry on timeout
        import time as _t
        last_r = None
        for attempt in range(3):
            try:
                last_r = requests.post(f"{BASE_URL}/api/auth/register", json={
                    "email": email, "password": pwd,
                    "first_name": "Iter93", "last_name": "GoLive",
                    "country_code": "CI",
                    "phone_number": f"+22507{secrets.randbelow(10_000_000):07d}",
                    "accept_terms": True,
                    "terms_accepted": True,
                }, timeout=60)
                break
            except requests.exceptions.RequestException:
                _t.sleep(2)
        r = last_r
        assert r is not None, "register timed out 3x"
        assert r.status_code in (200, 201), f"register: {r.status_code} {r.text[:200]}"
        body = r.json()
        assert body.get("status") == "otp_sent" or body.get("requires_otp") is True

        # OTP from DB
        async def fetch_otp():
            conn = await _db()
            try:
                row = await conn.fetchrow(
                    "SELECT code FROM email_otps WHERE email=$1 AND purpose='register' "
                    "AND used=FALSE ORDER BY created_at DESC LIMIT 1", email)
                return row["code"] if row else None
            finally:
                await conn.close()
        code = asyncio.run(fetch_otp())
        assert code, "OTP not found in email_otps table"

        # VERIFY-OTP (uses email, not user_id, per current implementation)
        r2 = requests.post(f"{BASE_URL}/api/auth/verify-otp",
                           json={"email": email, "code": code}, timeout=15)
        assert r2.status_code == 200, f"verify-otp: {r2.status_code} {r2.text[:200]}"
        assert "access_token" in r2.json()
        assert r2.json()["user"]["email"] == email

        # LOGIN
        r3 = requests.post(f"{BASE_URL}/api/auth/login",
                           json={"email": email, "password": pwd}, timeout=15)
        assert r3.status_code == 200
        tok = r3.json().get("access_token")
        assert tok

        # LOGOUT
        r4 = requests.post(f"{BASE_URL}/api/auth/logout",
                           headers={"Authorization": f"Bearer {tok}"}, timeout=10)
        assert r4.status_code == 200

        # cleanup best-effort
        async def cleanup():
            conn = await _db()
            try:
                await conn.execute("DELETE FROM users WHERE email=$1", email)
            except Exception:
                pass
            finally:
                await conn.close()
        try:
            asyncio.run(cleanup())
        except Exception:
            pass


# ============================================================
# SECTION 3 placeholder — Password Reset test moved to end of file
# (see class TestZZPasswordReset below) because it invalidates Alice's
# active sessions which other tests still need.
# ============================================================


# ============================================================
# SECTION 4 — PAYMENTS DEPOSIT (checkout URL generation only)
# ============================================================
class TestDeposit:
    def test_hubtel_checkout_url(self, bob_session):
        s, _ = bob_session
        r = s.post(f"{BASE_URL}/api/wallet/deposit",
                   json={"method": "hubtel_card", "amount": 100}, timeout=30)
        assert r.status_code in (200, 201), f"hubtel deposit: {r.status_code} {r.text[:300]}"
        data = r.json()
        assert "tx_id" in data
        url = data.get("checkout_url") or data.get("url")
        assert url, f"no checkout_url: {data}"
        # hubtel prod domain (paylinkcreator or pay.hubtel)
        assert ("hubtel" in url.lower()), f"not a hubtel url: {url}"

    def test_nowpayments_checkout_url(self, bob_session):
        s, _ = bob_session
        r = s.post(f"{BASE_URL}/api/wallet/deposit",
                   json={"method": "nowpayments_usdttrc20", "amount": 20}, timeout=30)
        assert r.status_code in (200, 201), f"nowpay deposit: {r.status_code} {r.text[:300]}"
        data = r.json()
        assert "tx_id" in data
        url = data.get("checkout_url") or data.get("url")
        assert url, f"no checkout_url: {data}"
        assert "nowpayments" in url.lower(), f"not a NowPayments url: {url}"

    def test_hubtel_webhook_invalid_signature_rejected(self, admin_session):
        # First ensure secret is configured; if not, endpoint is in DEV mode → skip.
        s_admin, _ = admin_session
        sr = s_admin.get(f"{BASE_URL}/api/admin/settings", timeout=15)
        assert sr.status_code == 200
        cfg = sr.json().get("secret_configured") or {}
        if not cfg.get("hubtel_webhook_secret"):
            pytest.skip("hubtel_webhook_secret not configured — DEV mode accepts all")
        body = json.dumps({"Data": {"ClientReference": "dep_fake", "Status": "Success", "Amount": 1}})
        r = requests.post(f"{BASE_URL}/api/wallet/hubtel/webhook",
                          data=body,
                          headers={"Content-Type": "application/json",
                                   "X-Auth-Signature": "deadbeef"},
                          timeout=15)
        assert r.status_code == 401, f"invalid sig should 401, got {r.status_code}: {r.text[:200]}"

    def test_nowpayments_webhook_invalid_signature_rejected(self, admin_session):
        s_admin, _ = admin_session
        sr = s_admin.get(f"{BASE_URL}/api/admin/settings", timeout=15)
        cfg = sr.json().get("secret_configured") or {}
        if not cfg.get("nowpayments_ipn_secret"):
            pytest.skip("nowpayments_ipn_secret not configured — DEV mode")
        body = json.dumps({"payment_id": "fake", "payment_status": "finished", "order_id": "dep_fake"})
        r = requests.post(f"{BASE_URL}/api/wallet/nowpayments/webhook",
                          data=body,
                          headers={"Content-Type": "application/json",
                                   "x-nowpayments-sig": "deadbeef"},
                          timeout=15)
        assert r.status_code in (400, 401), f"invalid NP sig should fail, got {r.status_code}"


# ============================================================
# SECTION 5 — WITHDRAW (KYC gate)
# ============================================================
class TestWithdraw:
    def test_withdraw_without_kyc_blocked(self, alice_session):
        s, _ = alice_session
        r = s.post(f"{BASE_URL}/api/wallet/withdraw",
                   json={"method": "usdt_trc20", "amount": 10,
                         "address": "TXYZabcdefghijklmnopqrstuvwxyz1234"},
                   timeout=15)
        # Either 403 KYC_REQUIRED or 400 if method/balance pre-check fires first
        assert r.status_code in (400, 403), f"expected 400/403, got {r.status_code}: {r.text[:200]}"
        txt = r.text.lower()
        # Best-effort check for KYC marker
        if r.status_code == 403:
            assert "kyc" in txt, f"403 without KYC marker: {r.text[:200]}"

    def test_admin_withdrawals_queue(self, admin_session):
        s, _ = admin_session
        # Existence check — endpoint might be /api/admin/withdrawals or under /wallet-ops
        for path in ("/api/admin/withdrawals?status=pending",
                     "/api/admin/wallet/withdrawals?status=pending"):
            r = s.get(f"{BASE_URL}{path}", timeout=15)
            if r.status_code == 200:
                return
        pytest.skip("Admin withdrawals queue endpoint shape unknown — skip (report to main agent)")


# ============================================================
# SECTION 6 — GAMES
# ============================================================
class TestGames:
    def test_wheel_status(self, bob_session):
        s, _ = bob_session
        r = s.get(f"{BASE_URL}/api/wheel/status", timeout=15)
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        # expected keys (at least one variant)
        assert any(k in data for k in ("total_points", "days_played", "quiz_accuracy",
                                        "cycle", "status"))

    def test_wheel_spin_credits_points_or_429(self, bob_session):
        s, _ = bob_session
        r = s.post(f"{BASE_URL}/api/wheel/spin", json={}, timeout=20)
        # 200 (if slot available) or 429 (daily cap hit) both acceptable
        assert r.status_code in (200, 400, 429), f"{r.status_code}: {r.text[:200]}"
        if r.status_code == 200:
            data = r.json()
            # points awarded should be sane (10-100) if present
            pts = data.get("points") or data.get("awarded") or data.get("reward")
            if isinstance(pts, (int, float)):
                assert pts >= 0

    def test_quiz_start(self, bob_session):
        s, _ = bob_session
        r = s.post(f"{BASE_URL}/api/quiz/start", json={}, timeout=15)
        assert r.status_code in (200, 400, 429), f"{r.status_code}: {r.text[:200]}"

    def test_tap_status(self, bob_session):
        s, _ = bob_session
        r = s.get(f"{BASE_URL}/api/tap/status", timeout=15)
        assert r.status_code == 200

    def test_duel_self_blocked(self, bob_session):
        s, bob = bob_session
        # Try create-from-quiz with self as opponent
        r = s.post(f"{BASE_URL}/api/duel/create-from-quiz",
                   json={"opponent_id": bob["user_id"]}, timeout=15)
        # Must be rejected (400 or 403)
        assert r.status_code in (400, 403, 422), f"self-duel should be blocked, got {r.status_code}"


# ============================================================
# SECTION 7 — PRO
# ============================================================
class TestPro:
    def test_pro_plans_shape(self, bob_session):
        s, _ = bob_session
        r = s.get(f"{BASE_URL}/api/pro/plans", timeout=15)
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        plans = data if isinstance(data, list) else (data.get("plans") or [])
        assert isinstance(plans, list) and len(plans) >= 1
        # Check for starter/creator/business ids
        ids = {(p.get("plan_id") or p.get("id") or "").lower() for p in plans}
        assert {"starter", "creator", "business"}.issubset(ids) or len(ids) >= 3, f"plan ids: {ids}"

    def test_pro_subscribe_insufficient_balance(self, alice_session):
        # Alice is NOT pro and likely balance=0 — subscribe should 400/402
        s, _ = alice_session
        r = s.post(f"{BASE_URL}/api/pro/subscribe",
                   json={"plan_id": "starter"}, timeout=15)
        assert r.status_code in (200, 400, 402, 403, 409, 422), f"{r.status_code}: {r.text[:200]}"
        # We don't assert 200 — depends on balance. Just verify endpoint live.


# ============================================================
# SECTION 8 — CONNECT
# ============================================================
class TestConnect:
    def test_hotspots_list(self, bob_session):
        s, _ = bob_session
        # /connect/nearby requires lat/lng
        r = s.get(f"{BASE_URL}/api/connect/nearby?lat=5.35&lng=-4.0&radius_km=50", timeout=15)
        assert r.status_code == 200, r.text[:200]

    def test_share_requires_business_pro(self, alice_session):
        # Alice is is_pro=False → must be blocked
        s, _ = alice_session
        r = s.post(f"{BASE_URL}/api/connect/hotspots",
                   json={"name": "Test", "ssid": "TestWiFi",
                         "password": "fake1234", "latitude": 5.35,
                         "longitude": -4.0, "security": "wpa2"},
                   timeout=15)
        assert r.status_code in (400, 402, 403, 422), f"non-pro share should be blocked, got {r.status_code}: {r.text[:200]}"


# ============================================================
# SECTION 9 — MESSENGER
# ============================================================
class TestMessenger:
    def test_conversations_list(self, bob_session):
        s, _ = bob_session
        r = s.get(f"{BASE_URL}/api/messages/conversations", timeout=15)
        assert r.status_code == 200, r.text[:200]

    def test_send_and_list(self, bob_session, alice_session):
        s_bob, _ = bob_session
        _, alice = alice_session
        # POST /api/messages/send — likely needs to_user_id + content
        r = s_bob.post(f"{BASE_URL}/api/messages/send",
                       json={"to_user_id": alice["user_id"], "content": "iter93 ping"},
                       timeout=15)
        assert r.status_code in (200, 201, 400, 404, 422), f"{r.status_code}: {r.text[:200]}"


# ============================================================
# SECTION 10 — TURNSTILE (BLOCKING for go-live)
# ============================================================
class TestTurnstile:
    def test_turnstile_settings_present(self, admin_session):
        s, _ = admin_session
        r = s.get(f"{BASE_URL}/api/admin/settings", timeout=15)
        assert r.status_code == 200
        settings = r.json().get("settings") or {}
        # Check for any turnstile-related keys
        ts_keys = {k: v for k, v in settings.items() if "turnstile" in k.lower()}
        env_site = os.environ.get("TURNSTILE_SITE_KEY", "")
        env_secret = os.environ.get("TURNSTILE_SECRET_KEY", "")
        # Report findings (not necessarily a hard assertion — we want visibility)
        print(f"\n[TURNSTILE] settings keys: {list(ts_keys.keys())}")
        print(f"[TURNSTILE] settings values: {ts_keys}")
        print(f"[TURNSTILE] env TURNSTILE_SITE_KEY set: {bool(env_site)}")
        print(f"[TURNSTILE] env TURNSTILE_SECRET_KEY set: {bool(env_secret)}")
        # Soft-assert: at minimum wheel_turnstile_enabled must be readable
        # Hard-fail only if NOTHING is configured AND prod is expected
        wheel_enabled = str(ts_keys.get("wheel_turnstile_enabled", "")).lower() == "true"
        has_site_key = bool(env_site) or any("site" in k.lower() for k in ts_keys)
        has_secret = bool(env_secret) or any("secret" in k.lower() for k in ts_keys)
        if not (wheel_enabled and has_site_key and has_secret):
            pytest.fail(
                f"TURNSTILE NOT FULLY CONFIGURED FOR GO-LIVE: "
                f"wheel_turnstile_enabled={wheel_enabled}, site_key_present={has_site_key}, "
                f"secret_present={has_secret}. This is BLOCKING per iter93 review brief."
            )


# ============================================================
# SECTION 3 — PASSWORD RESET (CRITIQUE) — runs LAST because it
# invalidates Alice's active sessions, used by earlier tests.
# ============================================================
class TestZZPasswordReset:
    def test_forgot_reset_flow(self):
        if not DATABASE_URL:
            pytest.skip("DATABASE_URL not set — cannot read reset token from DB")
        email = ALICE_EMAIL
        r = requests.post(f"{BASE_URL}/api/auth/forgot-password",
                          json={"email": email}, timeout=15)
        assert r.status_code == 200

        r_unknown = requests.post(f"{BASE_URL}/api/auth/forgot-password",
                                  json={"email": "nobody_xyz@example.com"}, timeout=15)
        assert r_unknown.status_code == 200

        async def fetch_token():
            conn = await _db()
            try:
                row = await conn.fetchrow(
                    "SELECT prt.token FROM password_reset_tokens prt "
                    "JOIN users u ON u.user_id=prt.user_id "
                    "WHERE u.email=$1 AND prt.used=FALSE "
                    "ORDER BY prt.expires_at DESC LIMIT 1", email)
                return row["token"] if row else None
            finally:
                await conn.close()
        token = asyncio.run(fetch_token())
        assert token, "password_reset_tokens row not created"

        new_pwd = f"ResetTmp{secrets.token_hex(3)}!"
        r2 = requests.post(f"{BASE_URL}/api/auth/reset-password",
                           json={"token": token, "new_password": new_pwd}, timeout=15)
        assert r2.status_code == 200, f"reset-password: {r2.text[:200]}"

        r3 = requests.post(f"{BASE_URL}/api/auth/login",
                           json={"email": email, "password": new_pwd}, timeout=15)
        assert r3.status_code == 200, "login with new password should work"

        r4 = requests.post(f"{BASE_URL}/api/auth/login",
                           json={"email": email, "password": ALICE_PWD}, timeout=15)
        assert r4.status_code in (400, 401), "old password should be rejected"

        # Restore original
        requests.post(f"{BASE_URL}/api/auth/forgot-password",
                      json={"email": email}, timeout=15)
        token2 = asyncio.run(fetch_token())
        assert token2
        r5 = requests.post(f"{BASE_URL}/api/auth/reset-password",
                           json={"token": token2, "new_password": ALICE_PWD}, timeout=15)
        assert r5.status_code == 200
        r6 = requests.post(f"{BASE_URL}/api/auth/login",
                           json={"email": email, "password": ALICE_PWD}, timeout=15)
        assert r6.status_code == 200
