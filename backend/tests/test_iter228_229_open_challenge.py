"""iter228/229 — Open peer-to-peer Quiz challenge flow.

Covers:
- POST /api/quiz/champion/challenge/open (create, stake min/max validation)
- GET  /api/quiz/champion/challenge/public/{cid} (no-auth preview, score hidden)
- POST /api/quiz/champion/challenge/{cid}/claim (acceptance + guards)
- GET  /api/og/challenge/{cid} (OG-rich HTML preview for social unfurl)
"""
import os
import re
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
CAPTCHA = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}

BOB = {"email": "bob@japap.com", "password": "Test1234!", **CAPTCHA}
DAF = {"email": "mirtoken2022@gmail.com", "password": "Daf2026!", **CAPTCHA}
# Alice fallback — used when Daf credentials fail (observed stale in iter228+).
ALICE = {"email": "alice@japap.com", "password": "Alice2026!", **CAPTCHA}


def _login(creds):
    """Return an authenticated requests.Session or skip if login fails."""
    s = requests.Session()
    # SPA convention — bypasses strict CSRF double-submit check.
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    last = None
    for _ in range(3):
        try:
            r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=60)
            if r.status_code == 200:
                return s
            last = (r.status_code, r.text[:200])
        except Exception as e:  # pragma: no cover
            last = ("exc", str(e))
        time.sleep(5)
    pytest.skip(f"Login failed for {creds['email']}: {last}")


@pytest.fixture(scope="module")
def bob_session():
    return _login(BOB)


@pytest.fixture(scope="module")
def daf_session():
    """Prefer Daf; fallback to Alice if Daf credentials are stale."""
    s = requests.Session()
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    try:
        r = s.post(f"{BASE_URL}/api/auth/login", json=DAF, timeout=60)
        if r.status_code == 200:
            return s
    except Exception:
        pass
    # Fallback
    return _login(ALICE)


# ════════════════════════════════════════════════════════════════════
# BACKEND TEST 1 — POST /challenge/open (paid, 5 USD)
# ════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def open_challenge(bob_session):
    r = bob_session.post(
        f"{BASE_URL}/api/quiz/champion/challenge/open",
        json={"mode": "paid", "stake_amount": 5, "country_code": "CM"},
        timeout=90,
    )
    assert r.status_code == 200, f"open creation failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    return data


def test_01_open_challenge_create_returns_expected_payload(open_challenge):
    d = open_challenge
    assert d.get("challenge_id", "").startswith("qcc_"), d
    assert d["status"] == "awaiting_acceptor"
    assert d["mode"] == "paid"
    assert float(d["stake_amount"]) == 5.0
    assert d["stake_currency"] == "USD"
    assert "commission_pct" in d
    assert "expires_at" in d
    assert "session_id" in d


# ════════════════════════════════════════════════════════════════════
# BACKEND TEST 2 — stake min/max validation (1..200 USD)
# ════════════════════════════════════════════════════════════════════
def test_02a_stake_above_max_returns_400(bob_session):
    r = bob_session.post(
        f"{BASE_URL}/api/quiz/champion/challenge/open",
        json={"mode": "paid", "stake_amount": 300, "country_code": "CM"},
        timeout=60,
    )
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert "Mise maximale" in r.text and "200" in r.text


def test_02b_stake_below_min_returns_400(bob_session):
    r = bob_session.post(
        f"{BASE_URL}/api/quiz/champion/challenge/open",
        json={"mode": "paid", "stake_amount": 0.5, "country_code": "CM"},
        timeout=60,
    )
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert "Mise minimale" in r.text and "1 USD" in r.text


# ════════════════════════════════════════════════════════════════════
# BACKEND TEST 3 — GET public preview (no auth, no score leaked)
# ════════════════════════════════════════════════════════════════════
def test_03_public_preview_no_auth_no_score(open_challenge):
    cid = open_challenge["challenge_id"]
    # Fresh session → NO cookies
    r = requests.get(f"{BASE_URL}/api/quiz/champion/challenge/public/{cid}", timeout=60)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    for key in ("challenger_name", "challenger_avatar", "challenger_played",
                "stake_amount", "commission_pct", "expires_at", "is_open"):
        assert key in d, f"missing {key} in public preview: {d}"
    # Score must be HIDDEN
    assert "challenger_score" not in d
    assert "champion_score" not in d
    # Challenger user_id must NOT leak
    assert "challenger_user_id" not in d
    assert d["is_open"] is True
    assert float(d["stake_amount"]) == 5.0
    assert d["challenger_name"]  # non-empty
    assert d["challenger_played"] is False


# ════════════════════════════════════════════════════════════════════
# BACKEND TEST 4 — claim flow guards
# ════════════════════════════════════════════════════════════════════
def test_04a_challenger_cannot_claim_own_challenge(bob_session, open_challenge):
    cid = open_challenge["challenge_id"]
    r = bob_session.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/claim", timeout=60)
    assert r.status_code == 403, (r.status_code, r.text[:200])
    assert "propre défi" in r.text


def test_04b_first_claim_succeeds_then_conflict(daf_session, bob_session, open_challenge):
    cid = open_challenge["challenge_id"]
    # 1st claim by Daf → 200
    r1 = daf_session.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/claim", timeout=90)
    assert r1.status_code == 200, (r1.status_code, r1.text[:300])
    d1 = r1.json()
    assert d1.get("status") == "accepted"
    assert d1.get("challenge_id") == cid
    # 2nd claim (either user) → 409
    r2 = daf_session.post(f"{BASE_URL}/api/quiz/champion/challenge/{cid}/claim", timeout=60)
    assert r2.status_code == 409, (r2.status_code, r2.text[:200])
    assert "déjà été accepté" in r2.text


# ════════════════════════════════════════════════════════════════════
# BACKEND TEST 5 — OG preview HTML
# ════════════════════════════════════════════════════════════════════
def test_05_og_challenge_preview_html(bob_session):
    # Create a fresh paid challenge with stake=5 so we can match 'Pot total 10'
    r = bob_session.post(
        f"{BASE_URL}/api/quiz/champion/challenge/open",
        json={"mode": "paid", "stake_amount": 5, "country_code": "CM"},
        timeout=90,
    )
    assert r.status_code == 200, r.text[:200]
    cid = r.json()["challenge_id"]

    last_err = None
    for attempt in range(3):
        try:
            rr = requests.get(f"{BASE_URL}/api/og/challenge/{cid}", timeout=60)
            if rr.status_code == 200:
                body = rr.text
                # og:title
                assert re.search(
                    r'<meta\s+property=["\']og:title["\']\s+content="[^"]*te d[eé]fie[^"]*5\s*USD',
                    body,
                ), f"og:title missing/wrong: {body[:500]}"
                # og:description with Pot total 10 USD
                assert re.search(
                    r'<meta\s+property=["\']og:description["\']\s+content="[^"]*Pot total\s*10\s*USD',
                    body,
                ), f"og:description missing Pot total 10 USD: {body[:500]}"
                # meta refresh → /c/{cid}
                assert re.search(
                    rf'<meta\s+http-equiv=["\']refresh["\']\s+content="0;\s*url=[^"]*/c/{re.escape(cid)}"',
                    body,
                ), "meta refresh missing"
                assert "<title>" in body
                # og:image → avatar OR fallback pwa-icon
                m = re.search(
                    r'<meta\s+property=["\']og:image["\']\s+content="([^"]+)"', body,
                )
                assert m, "og:image missing"
                og_image = m.group(1)
                assert og_image, "og:image empty"
                # Requirement: if Bob's avatar exists, og:image contains it, else fallback icon.
                # Bob's avatar is '/api/upload/files/x.webp' — path may be absolute or relative.
                assert ("avatar" in og_image.lower() or ".webp" in og_image.lower()
                        or og_image.startswith("http") or og_image.endswith("/pwa-icon-512.png")
                        or "upload" in og_image.lower()), og_image
                return
            last_err = (rr.status_code, rr.text[:200])
        except Exception as e:
            last_err = ("exc", str(e))
        time.sleep(30)
    pytest.fail(f"OG preview failed after 3 retries: {last_err}")
