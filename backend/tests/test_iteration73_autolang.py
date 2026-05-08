"""
JAPAP — Auto language detection at signup regression (iter73).

Exercises `services.language_detector.detect_user_language` plus the
signup endpoint that wires it.
"""
import os
import pytest
import httpx
import uuid

from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

BASE = os.environ.get("PYTEST_BASE", "http://localhost:8001")


@pytest.fixture
def client():
    # Register endpoint sends an OTP email which may stall on network;
    # generous timeout so we don't flake in CI.
    with httpx.Client(timeout=90) as c:
        yield c


# ─── Pure unit tests on the detector ─────────────────────────────────────

def test_detector_honors_client_hint_when_supported():
    from services.language_detector import detect_user_language
    assert detect_user_language(detected_lang="fr") == "fr"
    assert detect_user_language(detected_lang="AR") == "ar"


def test_detector_ignores_unsupported_client_hint():
    from services.language_detector import detect_user_language
    assert detect_user_language(detected_lang="klingon") is None


def test_detector_falls_back_to_country_mapping():
    from services.language_detector import detect_user_language
    # Cameroon → fr, Nigeria → yo, Tanzania → sw
    assert detect_user_language(country_code="CM") == "fr"
    assert detect_user_language(country_code="NG") == "yo"
    assert detect_user_language(country_code="TZ") == "sw"
    # India → hi, Bangladesh → bn
    assert detect_user_language(country_code="IN") == "hi"
    assert detect_user_language(country_code="BD") == "bn"


def test_detector_prefers_client_hint_over_country():
    """If the user's browser ships in French but they're in Nigeria, we
    honor the browser — country is only used as a fallback."""
    from services.language_detector import detect_user_language
    assert detect_user_language(detected_lang="fr", country_code="NG") == "fr"


def test_detector_uses_proxy_country():
    from services.language_detector import detect_user_language
    assert detect_user_language(proxy_country="EG") == "ar"
    # Garbage header → ignored
    assert detect_user_language(proxy_country="ZZZ") is None


def test_detector_parses_accept_language():
    from services.language_detector import detect_user_language
    assert detect_user_language(
        accept_language="pt-PT,pt;q=0.9,en;q=0.8"
    ) == "pt"
    # Unknown dialect → falls through to first supported match (es)
    assert detect_user_language(
        accept_language="de-DE,de;q=0.9,es;q=0.8"
    ) == "es"
    # Nothing supported at all → None
    assert detect_user_language(accept_language="zh-CN,zh;q=0.9,ja;q=0.8") is None


# ─── End-to-end: signup sets preferred_lang automatically ────────────────

def test_signup_auto_sets_preferred_lang_from_detected_lang(client):
    """Register a new user with an Arabic detected_lang → after OTP verify
    their preferred_lang MUST be 'ar' without any manual tweak."""
    suffix = uuid.uuid4().hex[:8]
    email = f"i18n_auto_{suffix}@japap-dev.com"
    r = client.post(
        f"{BASE}/api/auth/register",
        json={
            "email": email,
            "password": "Test1234!",
            "first_name": "AutoLang",
            "last_name": "Test",
            "terms_accepted": True,
            "detected_lang": "ar",
        },
    )
    # Signup returns 200 (OTP sent) or 400 if email already taken — fresh
    # email though, so expect 200.
    assert r.status_code in (200, 201), f"register failed: {r.text}"

    # Query the DB directly to avoid needing the OTP flow
    import asyncpg, asyncio, os as _os
    async def _fetch():
        conn = await asyncpg.connect(_os.environ["DATABASE_URL"])
        try:
            row = await conn.fetchrow(
                "SELECT preferred_lang FROM users WHERE email = $1", email
            )
            return row["preferred_lang"] if row else None
        finally:
            await conn.close()
    preferred = asyncio.get_event_loop().run_until_complete(_fetch())
    assert preferred == "ar", f"expected preferred_lang=ar got {preferred!r}"

    # Cleanup
    async def _cleanup():
        conn = await asyncpg.connect(_os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM email_otps WHERE email = $1", email)
            await conn.execute("DELETE FROM wallets WHERE user_id IN (SELECT user_id FROM users WHERE email = $1)", email)
            await conn.execute("DELETE FROM users WHERE email = $1", email)
        finally:
            await conn.close()
    asyncio.get_event_loop().run_until_complete(_cleanup())


def test_signup_falls_back_to_country_when_no_detected_lang(client):
    """No detected_lang, but a CM country_code → preferred_lang must be fr."""
    suffix = uuid.uuid4().hex[:8]
    email = f"i18n_country_{suffix}@japap-dev.com"
    r = client.post(
        f"{BASE}/api/auth/register",
        json={
            "email": email,
            "password": "Test1234!",
            "first_name": "CountryLang",
            "last_name": "Test",
            "terms_accepted": True,
            "country_code": "NG",  # Nigeria → yo
        },
    )
    assert r.status_code in (200, 201)

    import asyncpg, asyncio, os as _os
    async def _fetch():
        conn = await asyncpg.connect(_os.environ["DATABASE_URL"])
        try:
            row = await conn.fetchrow(
                "SELECT preferred_lang FROM users WHERE email = $1", email
            )
            return row["preferred_lang"] if row else None
        finally:
            await conn.close()
    preferred = asyncio.get_event_loop().run_until_complete(_fetch())
    assert preferred == "yo"

    async def _cleanup():
        conn = await asyncpg.connect(_os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM email_otps WHERE email = $1", email)
            await conn.execute("DELETE FROM wallets WHERE user_id IN (SELECT user_id FROM users WHERE email = $1)", email)
            await conn.execute("DELETE FROM users WHERE email = $1", email)
        finally:
            await conn.close()
    asyncio.get_event_loop().run_until_complete(_cleanup())
