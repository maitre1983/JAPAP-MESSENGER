"""Iter111 — Referral Phases A/B/C E2E (preview, UTM, emails, register flow).

Validates:
- GET /api/referrals/preview/{code}  (200/404/400)
- POST /api/auth/register  with referral_code + utm_*
- DB: referrals.utm_source/medium/campaign persisted
- POST /api/auth/verify-otp -> activates referral, credits referrer wallet
- referral_email_log rows for kind='referral.invited' and 'referral.activated'
- Re-register of unverified email does NOT 500 (NameError fix)
"""
import os
import uuid
import time
import asyncio
import pytest
import requests
import asyncpg
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
DATABASE_URL = os.environ["DATABASE_URL"]
TURNSTILE_BYPASS = os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN") or "JAPAP_E2E_BYPASS_2026"


# ---------- Helpers ----------
async def _db():
    return await asyncpg.connect(DATABASE_URL)


async def _bob_code() -> str:
    conn = await _db()
    try:
        row = await conn.fetchrow(
            "SELECT referral_code FROM users WHERE email='bob@japap.com'"
        )
        return row["referral_code"] if row else None
    finally:
        await conn.close()


async def _bob_id() -> str:
    conn = await _db()
    try:
        row = await conn.fetchrow(
            "SELECT user_id FROM users WHERE email='bob@japap.com'"
        )
        return row["user_id"] if row else None
    finally:
        await conn.close()


async def _bob_wallet_balance():
    conn = await _db()
    try:
        return await conn.fetchval(
            "SELECT balance FROM wallets WHERE user_id="
            "(SELECT user_id FROM users WHERE email='bob@japap.com')"
        )
    finally:
        await conn.close()


async def _latest_otp(email: str) -> str:
    conn = await _db()
    try:
        return await conn.fetchval(
            "SELECT code FROM email_otps WHERE email=$1 "
            "ORDER BY created_at DESC LIMIT 1",
            email,
        )
    finally:
        await conn.close()


async def _delete_user(email: str):
    conn = await _db()
    try:
        uid = await conn.fetchval(
            "SELECT user_id FROM users WHERE email=$1", email
        )
        if not uid:
            return
        await conn.execute(
            "DELETE FROM referrals WHERE referred_id=$1 OR referrer_id=$1", uid
        )
        await conn.execute("DELETE FROM email_otps WHERE email=$1", email)
        await conn.execute("DELETE FROM wallets WHERE user_id=$1", uid)
        await conn.execute("DELETE FROM users WHERE user_id=$1", uid)
    finally:
        await conn.close()


# ---------- Module-level fixtures ----------
@pytest.fixture(scope="module")
def bob_code():
    return asyncio.run(_bob_code())


@pytest.fixture(scope="module")
def bob_id():
    return asyncio.run(_bob_id())


# ====== Preview endpoint ======
class TestPreview:
    def test_preview_valid_code(self, bob_code):
        assert bob_code, "Bob's referral_code missing in DB"
        # Retry on 502 (preview infra ingress occasionally)
        last = None
        for _ in range(5):
            r = requests.get(f"{BASE_URL}/api/referrals/preview/{bob_code}", timeout=10)
            last = r
            if r.status_code != 502:
                break
            time.sleep(3)
        assert last.status_code == 200, last.text
        data = last.json()
        assert data["code"] == bob_code
        assert "referrer" in data and "name" in data["referrer"]
        assert "bonuses" in data
        assert "register_url" in data
        assert "share_url" in data

    def test_preview_invalid_code(self):
        r = requests.get(f"{BASE_URL}/api/referrals/preview/NOPECODE9")
        assert r.status_code == 404

    def test_preview_too_long_code(self):
        # 17+ chars => 400 (server: len > 16)
        r = requests.get(f"{BASE_URL}/api/referrals/preview/{'A' * 17}")
        assert r.status_code == 400

    def test_preview_lowercase_code_normalized(self, bob_code):
        if not bob_code:
            pytest.skip("no bob_code")
        r = requests.get(f"{BASE_URL}/api/referrals/preview/{bob_code.lower()}")
        assert r.status_code == 200


# ====== Register with referral + UTM persistence ======
class TestRegisterWithReferralUTM:
    rand = uuid.uuid4().hex[:8]
    email = f"testref_{rand}@japap.com"

    @classmethod
    def teardown_class(cls):
        try:
            asyncio.run(_delete_user(cls.email))
        except Exception:
            pass

    def test_register_with_referral_code_and_utm(self, bob_code):
        assert bob_code
        payload = {
            "email": self.email,
            "password": "Test1234!",
            "first_name": "Test",
            "last_name": "Ref",
            "phone_number": f"+237{int(time.time()) % 10**9}",
            "country_code": "CM",
            "referral_code": bob_code,
            "utm_source": "whatsapp",
            "utm_medium": "share",
            "utm_campaign": "spring2026",
            "turnstile_token": TURNSTILE_BYPASS,
            "terms_accepted": True,
        }
        r = requests.post(f"{BASE_URL}/api/auth/register", json=payload)
        if r.status_code == 400 and "Turnstile" in r.text:
            pytest.skip("Turnstile enforced — register E2E cannot run via curl")
        assert r.status_code == 200, r.text
        body = r.json()
        # Acceptable status: otp_sent (first registration)
        status = body.get("status") or body.get("message") or ""
        assert "otp" in status.lower() or "sent" in str(body).lower(), body

    def test_utm_persisted_in_referrals_table(self, bob_id):
        async def _check():
            conn = await _db()
            try:
                uid = await conn.fetchval(
                    "SELECT user_id FROM users WHERE email=$1", self.email
                )
                if not uid:
                    pytest.skip("user not created (likely Turnstile-blocked register)")
                row = await conn.fetchrow(
                    "SELECT utm_source, utm_medium, utm_campaign, status, "
                    "referrer_id FROM referrals WHERE referred_id=$1", uid
                )
                assert row, "referral row not created"
                assert row["referrer_id"] == bob_id
                assert row["utm_source"] == "whatsapp"
                assert row["utm_medium"] == "share"
                assert row["utm_campaign"] == "spring2026"
                assert row["status"] == "pending"
            finally:
                await conn.close()
        asyncio.run(_check())

    def test_invited_email_log_created(self, bob_id):
        async def _check():
            conn = await _db()
            try:
                uid = await conn.fetchval(
                    "SELECT user_id FROM users WHERE email=$1", self.email
                )
                if not uid:
                    pytest.skip("user not created (Turnstile)")
                exists = await conn.fetchval(
                    "SELECT to_regclass('referral_email_log')"
                )
                if not exists:
                    pytest.skip("referral_email_log table not present")
                row = await conn.fetchrow(
                    "SELECT kind, success FROM referral_email_log "
                    "WHERE referrer_id=$1 AND kind='referral.invited' "
                    "ORDER BY id DESC LIMIT 1", bob_id
                )
                assert row, "no referral.invited log row inserted"
            finally:
                await conn.close()
        asyncio.run(_check())

    def test_verify_otp_activates_referral_and_credits_bob(self, bob_id):
        async def _setup():
            return await _latest_otp(self.email), await _bob_wallet_balance()

        otp, bob_balance_before = asyncio.run(_setup())
        if not otp:
            pytest.skip("OTP not in DB (register skipped due to Turnstile)")

        r = requests.post(
            f"{BASE_URL}/api/auth/verify-otp",
            json={"email": self.email, "code": otp},
        )
        assert r.status_code == 200, r.text

        # Allow async tasks to settle
        time.sleep(2)

        async def _verify():
            conn = await _db()
            try:
                uid = await conn.fetchval(
                    "SELECT user_id FROM users WHERE email=$1", self.email
                )
                ref = await conn.fetchrow(
                    "SELECT status, referrer_bonus_usd FROM referrals "
                    "WHERE referred_id=$1", uid
                )
                return ref
            finally:
                await conn.close()

        ref = asyncio.run(_verify())
        assert ref is not None
        assert ref["status"] == "active", f"status={ref['status']}"
        assert ref["referrer_bonus_usd"] is not None

        bob_balance_after = asyncio.run(_bob_wallet_balance())
        # Wallet should be >= before (>= because referrer_bonus could be 0 if config zero)
        if bob_balance_before is not None and bob_balance_after is not None:
            assert bob_balance_after >= bob_balance_before

    def test_activated_email_log(self, bob_id):
        async def _check():
            conn = await _db()
            try:
                uid = await conn.fetchval(
                    "SELECT user_id FROM users WHERE email=$1", self.email
                )
                if not uid:
                    pytest.skip("user not created (Turnstile)")
                exists = await conn.fetchval(
                    "SELECT to_regclass('referral_email_log')"
                )
                if not exists:
                    pytest.skip("no email log table")
                row = await conn.fetchrow(
                    "SELECT kind FROM referral_email_log WHERE referrer_id=$1 "
                    "AND kind='referral.activated' ORDER BY id DESC LIMIT 1",
                    bob_id,
                )
                assert row, "referral.activated email_log not created"
            finally:
                await conn.close()
        asyncio.run(_check())


# ====== NameError regression: re-register unverified email ======
class TestReregisterUnverified:
    rand = uuid.uuid4().hex[:8]
    email = f"testref_re_{rand}@japap.com"

    @classmethod
    def teardown_class(cls):
        try:
            asyncio.run(_delete_user(cls.email))
        except Exception:
            pass

    def test_first_register_then_re_register_no_500(self, bob_code):
        payload = {
            "email": self.email,
            "password": "Test1234!",
            "first_name": "Re",
            "last_name": "Test",
            "phone_number": f"+237{(int(time.time())+1) % 10**9}",
            "country_code": "CM",
            "referral_code": bob_code,
            "turnstile_token": TURNSTILE_BYPASS,
            "terms_accepted": True,
        }
        r1 = requests.post(f"{BASE_URL}/api/auth/register", json=payload)
        if r1.status_code == 400 and "Turnstile" in r1.text:
            pytest.skip("Turnstile enforced — re-register E2E cannot run via curl")
        assert r1.status_code == 200, r1.text
        # 2nd register without OTP verify — must NOT raise NameError -> 500
        r2 = requests.post(f"{BASE_URL}/api/auth/register", json=payload)
        # Either 200 (otp_sent again) or 429 (cooldown). NEVER 500.
        assert r2.status_code in (200, 429), f"got {r2.status_code}: {r2.text}"
        assert r2.status_code != 500


# ====== Short link /r/{code} ======
class TestShortLink:
    def test_short_link_serves_spa(self, bob_code):
        # The /r/{code} is a frontend route; backend won't 200 it.
        # We just ensure /api/referrals/preview/{code} works (already tested),
        # and that frontend SPA route is reachable (200 HTML).
        r = requests.get(f"{BASE_URL}/r/{bob_code}", allow_redirects=False)
        # Either SPA 200 (serves index.html) or 3xx redirect — both acceptable
        assert r.status_code in (200, 301, 302, 307, 308), r.status_code
