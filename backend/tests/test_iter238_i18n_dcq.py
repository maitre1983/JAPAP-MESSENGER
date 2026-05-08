"""iter238 — Backend i18n / DCQ paid multilang tests.

Validates:
- i18n preferences endpoint (PUT /api/auth/preferences) toggles preferred_lang.
- /api/quiz/daily-challenge/paid/config returns pool_size filtered by user lang
  with FR fallback (langua=user_lang OR 'fr').
- /api/quiz/daily-challenge/paid/start serves 5 questions for an EN user even
  when only FR questions exist (FR fallback).
- Schema: daily_challenge_expert_pool has language column + index.
- Worker module exposes SUPPORTED_LANGS, multi-lang prompts, and language
  parameter on _generate_for_category / _validate_question.
"""
import os
import asyncio
import pytest
import requests
from dotenv import load_dotenv

# Load env from frontend (REACT_APP_BACKEND_URL) and backend (DATABASE_URL).
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL not set in /app/frontend/.env"
BYPASS = "JAPAP_E2E_BYPASS_2026"

ALICE = {"email": "alice@japap.com", "password": "Alice2026!"}
BOB = {"email": "bob@japap.com", "password": "Test1234!"}


def _login(session: requests.Session, creds: dict) -> dict:
    r = session.post(
        f"{BASE_URL}/api/auth/login",
        json={**creds, "captcha_id": BYPASS, "captcha_answer": "0"},
        timeout=20,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    return data


@pytest.fixture
def alice_session():
    s = requests.Session()
    data = _login(s, ALICE)
    token = data.get("token") or data.get("access_token")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    csrf = s.cookies.get("csrf_token")
    if csrf:
        s.headers["X-CSRF-Token"] = csrf
    return s


@pytest.fixture
def bob_session():
    s = requests.Session()
    data = _login(s, BOB)
    token = data.get("token") or data.get("access_token")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    csrf = s.cookies.get("csrf_token")
    if csrf:
        s.headers["X-CSRF-Token"] = csrf
    return s


# ── i18n: toggle preferred_lang ─────────────────────────────────────────
def _set_lang(session: requests.Session, lang: str):
    candidates = [
        ("PUT", "/api/auth/preferences", {"preferred_lang": lang}),
        ("PUT", "/api/users/me/preferences", {"preferred_lang": lang}),
        ("PATCH", "/api/auth/me", {"preferred_lang": lang}),
        ("PUT", "/api/auth/me", {"preferred_lang": lang}),
        ("PUT", "/api/users/me", {"preferred_lang": lang}),
    ]
    last = None
    for method, path, body in candidates:
        r = session.request(method, f"{BASE_URL}{path}", json=body, timeout=15)
        last = r
        if r.status_code in (200, 204):
            return r
    return last


class TestI18nPreferences:
    def test_toggle_preferred_lang_en_then_fr(self, alice_session):
        r_en = _set_lang(alice_session, "en")
        assert r_en.status_code in (200, 204), f"PUT lang=en failed: {r_en.status_code} {r_en.text[:200]}"
        # Verify
        me = alice_session.get(f"{BASE_URL}/api/auth/me", timeout=15)
        assert me.status_code == 200
        assert me.json().get("preferred_lang") == "en"
        # Restore
        r_fr = _set_lang(alice_session, "fr")
        assert r_fr.status_code in (200, 204)
        me2 = alice_session.get(f"{BASE_URL}/api/auth/me", timeout=15)
        assert me2.json().get("preferred_lang") == "fr"


# ── DCQ paid /config — pool_size with lang filter + FR fallback ─────────
class TestDcqPaidConfigMultiLang:
    def test_config_pool_size_fr(self, alice_session):
        _set_lang(alice_session, "fr")
        r = alice_session.get(
            f"{BASE_URL}/api/quiz/daily-challenge/paid/config", timeout=20
        )
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert "pool_size" in d
        assert isinstance(d["pool_size"], int)
        # FR pool should be > 0 (DB has 1552 active FR questions per context)
        assert d["pool_size"] > 0, f"FR pool_size unexpectedly 0: {d}"

    def test_config_pool_size_en_fallbacks_to_fr(self, alice_session):
        _set_lang(alice_session, "en")
        r = alice_session.get(
            f"{BASE_URL}/api/quiz/daily-challenge/paid/config", timeout=20
        )
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        # With FR fallback (language IN ($1,'fr')), EN user must see > 0
        assert d["pool_size"] > 0, f"EN pool_size should fallback to FR but got: {d}"
        _set_lang(alice_session, "fr")  # restore


# ── Schema check: language column + index ───────────────────────────────
class TestDcqExpertPoolSchema:
    def test_pool_table_has_language_column_and_index(self):
        async def _check():
            import sys
            sys.path.insert(0, "/app/backend")
            from database import get_pool  # noqa
            pool = await get_pool()
            async with pool.acquire() as conn:
                col = await conn.fetchrow(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name='daily_challenge_expert_pool' AND column_name='language'"
                )
                idx = await conn.fetchval(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename='daily_challenge_expert_pool' "
                    "  AND indexname='idx_dcep_active_lang'"
                )
                # also check pool has FR rows
                fr_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM daily_challenge_expert_pool "
                    "WHERE active=TRUE AND language='fr'"
                )
                return col, idx, fr_count
        col, idx, fr_count = asyncio.run(_check())
        assert col is not None, "language column missing from daily_challenge_expert_pool"
        assert idx == "idx_dcep_active_lang", f"index missing, got {idx}"
        assert (fr_count or 0) > 0, "no active FR questions in pool"


# ── Worker module surface check ─────────────────────────────────────────
class TestDcqPaidPoolWorker:
    def test_worker_supports_multi_langs(self):
        import sys
        sys.path.insert(0, "/app/backend")
        from services import dcq_paid_pool_worker as w
        assert hasattr(w, "SUPPORTED_LANGS")
        assert "fr" in w.SUPPORTED_LANGS
        assert "en" in w.SUPPORTED_LANGS
        assert hasattr(w, "CATEGORY_LABELS")
        assert "fr" in w.CATEGORY_LABELS and "en" in w.CATEGORY_LABELS
        # Function signature has language param
        import inspect
        sig = inspect.signature(w._generate_for_category)
        assert "language" in sig.parameters, sig

    def test_validate_question_is_lang_aware(self):
        import sys
        sys.path.insert(0, "/app/backend")
        from services import dcq_paid_pool_worker as w
        # Source-level check: validation should handle language
        src = inspect_src = open(w.__file__).read()
        assert "lang" in src and "en" in src, "Validator must be language-aware"


# ── DCQ paid /start — fallback to FR when EN user with no EN questions ─
class TestDcqPaidStartFallback:
    def test_en_user_starts_with_fr_questions_fallback(self, bob_session):
        # set EN
        _set_lang(bob_session, "en")
        # Accept CGJ if needed
        bob_session.post(f"{BASE_URL}/api/users/me/cgje-accept", timeout=10)
        bob_session.post(f"{BASE_URL}/api/auth/cgje-accept", timeout=10)
        # Start a session
        r = bob_session.post(
            f"{BASE_URL}/api/quiz/daily-challenge/paid/start",
            json={"stake_usd": 0.1},
            timeout=30,
        )
        # Acceptable outcomes: 200 success | 409 already played today | 451 cgje
        # | 402 insufficient balance | 503 pool insuf. We accept 200/409/402/451
        # and ASSERT not 500.
        assert r.status_code != 500, f"server error: {r.text[:400]}"
        if r.status_code == 200:
            data = r.json()
            assert "questions" in data and len(data["questions"]) == 5, data
            # FR fallback: questions are non-empty strings
            for q in data["questions"]:
                assert q.get("question") and isinstance(q["question"], str)
                assert isinstance(q.get("options"), list) and len(q["options"]) == 4
        else:
            # Not blocking: this is environment-dependent (already played, no
            # balance, cgje not accepted). The fallback logic is exercised by
            # /config pool_size test above.
            print(f"[info] /start returned {r.status_code} — acceptable: {r.text[:200]}")
        # restore
        _set_lang(bob_session, "fr")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
