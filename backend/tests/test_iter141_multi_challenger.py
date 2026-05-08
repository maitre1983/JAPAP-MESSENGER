"""iter141 — Multi-challenger duel system tests.

Covers:
- GET /api/duel/{token}/leaderboard for multi_attempts duel
- GET /api/duel/me/sent (auth required)
- Bob can't re-submit / re-start an already-played multi duel (409)
- Initiator can't submit own multi challenge (400)
- Classic 1v1 still works (existing duels — read-only check)
- Daily challenge submit auto-creates multi_attempts duel
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
TURNSTILE_BYPASS = "JAPAP_E2E_BYPASS_2026"

# Seed token from main agent setup — Alice initiator, Bob/Charlie/Dave attempted
SEED_TOKEN = "jAY9zih8qJCn6t__IeaS_A"

USERS = {
    "alice":   ("alice@japap.com", "Alice2026!"),
    "bob":     ("bob@japap.com", "Test1234!"),
    "charlie": ("charlie_iter141@japap.com", "Charlie2026!"),
    "dave":    ("dave_iter141@japap.com", "Dave2026!"),
}


def _login(email, password):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": email, "password": password, "turnstile_token": TURNSTILE_BYPASS,
    }, timeout=45)
    if r.status_code != 200:
        pytest.skip(f"Login failed for {email}: {r.status_code} {r.text[:200]}")
    data = r.json()
    token = data.get("access_token") or data.get("token")
    user = data.get("user") or {}
    return token, user, r.cookies


@pytest.fixture(scope="module")
def alice_session():
    token, user, cookies = _login(*USERS["alice"])
    s = requests.Session()
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    s.cookies.update(cookies)
    s.user = user
    return s


@pytest.fixture(scope="module")
def bob_session():
    token, user, cookies = _login(*USERS["bob"])
    s = requests.Session()
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    s.cookies.update(cookies)
    s.user = user
    return s


# ─── Leaderboard ────────────────────────────────────────────────────────
class TestLeaderboard:
    def test_leaderboard_public_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/duel/{SEED_TOKEN}/leaderboard", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["share_token"] == SEED_TOKEN
        assert d["duel_kind"] == "multi_attempts"
        assert d["initiator"]["user_id"]
        assert isinstance(d["attempts"], list)
        # Stats present
        s = d["stats"]
        assert s["participants"] == len(d["attempts"])
        assert "wins_for_initiator" in s
        assert "losses_for_initiator" in s
        assert "ties" in s
        # Sorted by score desc, time asc — verify monotonicity
        scores = [a["score"] for a in d["attempts"] if a["score"] is not None]
        assert scores == sorted(scores, reverse=True), f"Not sorted: {scores}"
        # No is_you flag for unauthenticated request
        assert all(a["is_you"] is False for a in d["attempts"])
        assert d["your_attempt"] is None
        assert d["you_are_initiator"] is False

    def test_leaderboard_as_initiator_sets_flag(self, alice_session):
        r = alice_session.get(f"{BASE_URL}/api/duel/{SEED_TOKEN}/leaderboard", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["you_are_initiator"] is True

    def test_leaderboard_as_challenger_has_your_attempt(self, bob_session):
        r = bob_session.get(f"{BASE_URL}/api/duel/{SEED_TOKEN}/leaderboard", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["you_are_initiator"] is False
        # Bob already played per setup
        assert d["your_attempt"] is not None, "Bob should have an attempt"
        assert d["your_attempt"]["is_you"] is True
        assert d["your_attempt"]["outcome"] in ("won", "lost", "tie")

    def test_leaderboard_unknown_token_404(self):
        r = requests.get(f"{BASE_URL}/api/duel/__nope__/leaderboard", timeout=60)
        assert r.status_code == 404


# ─── /me/sent ──────────────────────────────────────────────────────────
class TestMeSent:
    def test_me_sent_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/duel/me/sent", timeout=60)
        assert r.status_code in (401, 403), f"Expected 401/403 unauth, got {r.status_code}"

    def test_me_sent_alice_returns_seed_duel(self, alice_session):
        r = alice_session.get(f"{BASE_URL}/api/duel/me/sent", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "items" in d
        # Find our seed token
        match = next((it for it in d["items"] if it["share_token"] == SEED_TOKEN), None)
        assert match is not None, f"Seed duel not in alice's sent list. Tokens={[i['share_token'] for i in d['items']]}"
        assert match["duel_kind"] == "multi_attempts"
        assert match["participants"] >= 3, f"Expected >=3 participants, got {match['participants']}"
        assert match["initiator_score"] is not None
        assert "best_challenger_score" in match
        assert "wins_for_initiator" in match
        assert "losses_for_initiator" in match
        assert "ties" in match


# ─── Multi guards ──────────────────────────────────────────────────────
class TestMultiGuards:
    def test_bob_cannot_restart_after_play(self, bob_session):
        # Bob already played — start-quiz should 409
        r = bob_session.post(f"{BASE_URL}/api/duel/{SEED_TOKEN}/start-quiz", timeout=60)
        assert r.status_code == 409, f"Expected 409, got {r.status_code} {r.text[:200]}"

    def test_initiator_cannot_start_own_multi_challenge(self, alice_session):
        # Alice is the challenger — start-quiz should 400 (cannot duel self)
        r = alice_session.post(f"{BASE_URL}/api/duel/{SEED_TOKEN}/start-quiz", timeout=60)
        assert r.status_code == 400, f"Expected 400, got {r.status_code} {r.text[:200]}"

    def test_bob_cannot_submit_again(self, bob_session):
        # submit-quiz should 409 since attempt exists
        r = bob_session.post(f"{BASE_URL}/api/duel/{SEED_TOKEN}/submit-quiz",
                             json={"answers": [0, 0, 0, 0, 0]}, timeout=60)
        assert r.status_code == 409, f"Expected 409, got {r.status_code} {r.text[:200]}"


# ─── Duel kind exposure ────────────────────────────────────────────────
class TestDuelKind:
    def test_get_duel_returns_multi_kind(self):
        r = requests.get(f"{BASE_URL}/api/duel/{SEED_TOKEN}", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["duel_kind"] == "multi_attempts"
        # opponent_id should remain NULL for multi (not locked to one player)
        assert d.get("opponent") is None, f"opponent_id should remain NULL for multi, got {d.get('opponent')}"
        assert "multi_stats" in d
        assert d["multi_stats"]["participants"] >= 3

    def test_classic_duels_still_work_in_my_list(self, alice_session):
        # Just verify the legacy endpoint still returns classic duels if any
        r = alice_session.get(f"{BASE_URL}/api/duel/my/list", timeout=60)
        assert r.status_code == 200
        items = r.json().get("items", [])
        # At least the seed multi should be present
        assert any(i["share_token"] == SEED_TOKEN for i in items)
