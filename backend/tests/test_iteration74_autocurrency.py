"""
JAPAP — Auto currency detection at signup regression (iter74).

Exercises `services.currency_detector.detect_user_currency` plus the
signup flow that wires it, plus the extended `/preferences` endpoint.
"""
import os
import uuid
import pytest
import httpx
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("PYTEST_BASE", "http://localhost:8001")
USER_EMAIL = "bob@japap.com"
USER_PASSWORD = "Test1234!"


def _login(client, email, password):
    r = client.post(
        f"{BASE}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    return {"Authorization": f"Bearer {data.get('token') or data.get('access_token')}"}


@pytest.fixture
def client():
    with httpx.Client(timeout=90) as c:
        yield c


# ─── Unit tests on the detector ──────────────────────────────────────────

def test_detector_honors_client_hint():
    from services.currency_detector import detect_user_currency
    assert detect_user_currency(detected_currency="EUR") == "EUR"
    assert detect_user_currency(detected_currency="ngn") == "NGN"  # normalises case


def test_detector_ignores_unsupported_hint():
    from services.currency_detector import detect_user_currency
    # "ZZZ" is ISO-valid shape but unknown — rejected.
    assert detect_user_currency(detected_currency="ZZZ") is None
    # Not a 3-letter code → rejected.
    assert detect_user_currency(detected_currency="USDT-too-long") is None


def test_detector_maps_country_to_currency():
    from services.currency_detector import detect_user_currency
    # Core JAPAP African markets
    assert detect_user_currency(country_code="CM") == "XAF"
    assert detect_user_currency(country_code="NG") == "NGN"
    assert detect_user_currency(country_code="KE") == "KES"
    # South Asia
    assert detect_user_currency(country_code="IN") == "INR"
    assert detect_user_currency(country_code="BD") == "BDT"
    # MENA
    assert detect_user_currency(country_code="EG") == "EGP"


def test_detector_prefers_client_hint_over_country():
    """User told us EUR explicitly — Cameroon IP shouldn't override that."""
    from services.currency_detector import detect_user_currency
    assert detect_user_currency(detected_currency="EUR", country_code="CM") == "EUR"


def test_detector_uses_proxy_country_fallback():
    from services.currency_detector import detect_user_currency
    assert detect_user_currency(proxy_country="CI") == "XOF"  # Cote d'Ivoire
    # Garbage proxy header → None
    assert detect_user_currency(proxy_country="ZZZ") is None


def test_detector_returns_none_on_nothing():
    from services.currency_detector import detect_user_currency
    assert detect_user_currency() is None


# ─── /preferences PUT ────────────────────────────────────────────────────

def test_preferences_accepts_supported_currency(client):
    headers = _login(client, USER_EMAIL, USER_PASSWORD)
    r = client.put(
        f"{BASE}/api/auth/preferences",
        json={"preferred_currency": "XAF"},
        headers=headers,
    )
    assert r.status_code == 200, r.text


def test_preferences_rejects_unknown_currency(client):
    headers = _login(client, USER_EMAIL, USER_PASSWORD)
    r = client.put(
        f"{BASE}/api/auth/preferences",
        json={"preferred_currency": "BTCX"},  # unknown
        headers=headers,
    )
    assert r.status_code == 400


def test_preferences_accepts_combined_update(client):
    """Updating both preferred_lang AND preferred_currency in one call
    must be valid (AuthContext auto-tag flow uses it on first login)."""
    headers = _login(client, USER_EMAIL, USER_PASSWORD)
    r = client.put(
        f"{BASE}/api/auth/preferences",
        json={"preferred_lang": "es", "preferred_currency": "USD"},
        headers=headers,
    )
    assert r.status_code == 200


# ─── End-to-end signup ───────────────────────────────────────────────────

def test_signup_auto_sets_preferred_currency_from_country(client):
    """Register with country_code=NG → DB preferred_currency=NGN."""
    suffix = uuid.uuid4().hex[:8]
    email = f"ccy_country_{suffix}@japap-dev.com"
    r = client.post(
        f"{BASE}/api/auth/register",
        json={
            "email": email,
            "password": "Test1234!",
            "first_name": "CurrencyAuto",
            "last_name": "NG",
            "terms_accepted": True,
            "country_code": "NG",
        },
    )
    assert r.status_code in (200, 201), r.text

    import asyncio, asyncpg
    async def _fetch():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            row = await conn.fetchrow(
                "SELECT preferred_currency, preferred_lang FROM users WHERE email = $1",
                email,
            )
            return dict(row) if row else None
        finally:
            await conn.close()
    got = asyncio.get_event_loop().run_until_complete(_fetch())
    assert got["preferred_currency"] == "NGN"
    assert got["preferred_lang"] == "yo"  # also verifies iter73 still works

    async def _cleanup():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM email_otps WHERE email = $1", email)
            await conn.execute("DELETE FROM wallets WHERE user_id IN (SELECT user_id FROM users WHERE email = $1)", email)
            await conn.execute("DELETE FROM users WHERE email = $1", email)
        finally:
            await conn.close()
    asyncio.get_event_loop().run_until_complete(_cleanup())


def test_signup_honors_detected_currency_client_hint(client):
    """detected_currency=EUR overrides country_code=NG."""
    suffix = uuid.uuid4().hex[:8]
    email = f"ccy_hint_{suffix}@japap-dev.com"
    r = client.post(
        f"{BASE}/api/auth/register",
        json={
            "email": email,
            "password": "Test1234!",
            "first_name": "CurrencyHint",
            "last_name": "EUR",
            "terms_accepted": True,
            "country_code": "NG",
            "detected_currency": "EUR",
        },
    )
    assert r.status_code in (200, 201), r.text

    import asyncio, asyncpg
    async def _fetch():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            row = await conn.fetchrow(
                "SELECT preferred_currency FROM users WHERE email = $1", email
            )
            return row["preferred_currency"] if row else None
        finally:
            await conn.close()
    assert asyncio.get_event_loop().run_until_complete(_fetch()) == "EUR"

    async def _cleanup():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM email_otps WHERE email = $1", email)
            await conn.execute("DELETE FROM wallets WHERE user_id IN (SELECT user_id FROM users WHERE email = $1)", email)
            await conn.execute("DELETE FROM users WHERE email = $1", email)
        finally:
            await conn.close()
    asyncio.get_event_loop().run_until_complete(_cleanup())
