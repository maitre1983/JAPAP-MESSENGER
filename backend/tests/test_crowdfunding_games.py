"""
Backend tests for Crowdfunding + Games + Marketplace regression.
JAPAP Iteration 12.
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"

VALID_CATEGORIES = {"business", "health", "community", "education", "emergency"}


def login(session, email, password):
    r = session.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text}"
    return r.json()


def register(session, email, password, first_name="Test", last_name="User"):
    payload = {
        "email": email, "password": password,
        "first_name": first_name, "last_name": last_name,
        "terms_accepted": True, "referral_code": "",
    }
    return session.post(f"{BASE_URL}/api/auth/register", json=payload)


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    login(s, ADMIN_EMAIL, ADMIN_PASSWORD)
    return s


@pytest.fixture(scope="module")
def admin_user(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 200
    return r.json()


@pytest.fixture(scope="module")
def secondary_session():
    """Create or login a secondary user for contribution tests (non-creator)."""
    s = requests.Session()
    email = f"cftester_{int(time.time())}@japap.com"
    pwd = "Test1234!"
    r = register(s, email, pwd, first_name="CF", last_name="Tester")
    if r.status_code not in (200, 201):
        # Try fallback existing tester
        email = "testref_1776710356@japap.com"
        pwd = "Test1234!"
        login(s, email, pwd)
    else:
        # auth cookie should already be set; ensure login
        login(s, email, pwd)
    return s, email


# ============================================================
# CROWDFUNDING
# ============================================================
class TestCrowdfunding:
    def test_categories(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/crowdfunding/categories")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        ids = {c["id"] for c in data}
        assert ids == VALID_CATEGORIES, f"got {ids}"
        # Each must have name, icon, color
        for c in data:
            assert c["name"] and c["icon"] and c["color"]

    def test_list_campaigns_basic(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/crowdfunding/campaigns")
        assert r.status_code == 200
        d = r.json()
        assert "campaigns" in d and "total" in d and "page" in d
        assert d["total"] >= 5  # legacy 5 migrated
        for c in d["campaigns"]:
            assert "campaign_id" in c
            assert "progress" in c
            assert isinstance(c["progress"], (int, float))

    def test_list_campaigns_filter_community(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/crowdfunding/campaigns?category=community")
        assert r.status_code == 200
        d = r.json()
        for c in d["campaigns"]:
            assert c["category"] == "community"

    def test_list_campaigns_invalid_category(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/crowdfunding/campaigns?category=invalid")
        assert r.status_code == 400

    def test_create_campaign_admin(self, admin_session):
        payload = {
            "title": "TEST_Campaign Backend Pytest",
            "description": "Test campaign created by pytest",
            "goal": 100000,
            "category": "business",
            "image": "",
            "duration_days": 30,
        }
        r = admin_session.post(f"{BASE_URL}/api/crowdfunding/campaigns", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["campaign_id"].startswith("camp_")
        pytest.admin_campaign_id = d["campaign_id"]

    def test_create_campaign_invalid_category(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/crowdfunding/campaigns", json={
            "title": "X", "goal": 100, "category": "invalid", "duration_days": 10,
        })
        assert r.status_code == 400

    def test_create_campaign_invalid_goal(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/crowdfunding/campaigns", json={
            "title": "X", "goal": 0, "category": "business", "duration_days": 10,
        })
        assert r.status_code == 400

    def test_create_campaign_invalid_duration(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/crowdfunding/campaigns", json={
            "title": "X", "goal": 100, "category": "business", "duration_days": 9999,
        })
        assert r.status_code == 400

    def test_get_campaign_detail(self, admin_session):
        cid = getattr(pytest, "admin_campaign_id", None)
        assert cid
        r = admin_session.get(f"{BASE_URL}/api/crowdfunding/campaigns/{cid}")
        assert r.status_code == 200
        d = r.json()
        assert d["campaign_id"] == cid
        assert "contributors" in d
        assert isinstance(d["contributors"], list)

    def test_get_campaign_404(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/crowdfunding/campaigns/camp_nonexistent")
        assert r.status_code == 404

    def test_self_contribute_rejected(self, admin_session):
        cid = getattr(pytest, "admin_campaign_id", None)
        r = admin_session.post(f"{BASE_URL}/api/crowdfunding/contribute", json={
            "campaign_id": cid, "amount": 100, "message": "self"
        })
        assert r.status_code == 400
        assert "propre" in r.json().get("detail", "").lower()

    def test_contribute_success(self, secondary_session, admin_session):
        sess, email = secondary_session
        cid = getattr(pytest, "admin_campaign_id", None)

        # Top up secondary user wallet via admin API if possible
        me = sess.get(f"{BASE_URL}/api/auth/me").json()
        sec_user_id = me["user_id"]
        # admin top-up via /wallet/send (admin has trillions in balance)
        admin_session.post(f"{BASE_URL}/api/wallet/send", json={
            "to_user_id": sec_user_id, "amount": 5000, "notes": "test_topup"
        })

        # Snapshot raised
        before = admin_session.get(f"{BASE_URL}/api/crowdfunding/campaigns/{cid}").json()
        raised_before = float(before["raised"])

        r = sess.post(f"{BASE_URL}/api/crowdfunding/contribute", json={
            "campaign_id": cid, "amount": 500, "message": "TEST contribution"
        })
        if r.status_code == 400 and "insuffisant" in r.text.lower():
            pytest.skip("Secondary wallet has insufficient balance, top-up endpoint unavailable")
        assert r.status_code == 200, r.text
        data = r.json()
        assert float(data["amount"]) == 500
        # 5% fee
        assert float(data["fee"]) == 25.0
        assert float(data["net_to_creator"]) == 475.0

        # Verify campaign raised increased
        after = admin_session.get(f"{BASE_URL}/api/crowdfunding/campaigns/{cid}").json()
        assert float(after["raised"]) == raised_before + 500
        assert after["backers_count"] >= 1

    def test_contribute_insufficient(self, secondary_session):
        sess, _ = secondary_session
        cid = getattr(pytest, "admin_campaign_id", None)
        r = sess.post(f"{BASE_URL}/api/crowdfunding/contribute", json={
            "campaign_id": cid, "amount": 999999999, "message": ""
        })
        assert r.status_code == 400

    def test_my_campaigns(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/crowdfunding/my-campaigns")
        assert r.status_code == 200
        out = r.json()
        ids = [c["campaign_id"] for c in out]
        assert getattr(pytest, "admin_campaign_id", None) in ids

    def test_my_contributions(self, secondary_session):
        sess, _ = secondary_session
        r = sess.get(f"{BASE_URL}/api/crowdfunding/my-contributions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ============================================================
# MARKETPLACE REGRESSION
# ============================================================
class TestMarketplaceRegression:
    def test_list_products(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/marketplace/products")
        assert r.status_code == 200
        # Accept either {products: [...]} or list
        body = r.json()
        if isinstance(body, dict):
            assert "products" in body or "items" in body
        else:
            assert isinstance(body, list)


# ============================================================
# GAMES
# ============================================================
class TestGames:
    def test_status(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/games/status")
        assert r.status_code == 200
        d = r.json()
        assert d["daily_cap"] == "2000.00"
        assert d["limits"] == {"spin": 3, "quiz": 10, "tap": 5}
        assert "games" in d
        for g in ("spin", "quiz", "tap"):
            assert g in d["games"]
            assert "remaining" in d["games"][g]

    def test_quiz_questions_no_answer(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/games/quiz/questions")
        assert r.status_code == 200
        qs = r.json()
        assert len(qs) == 8
        for q in qs:
            assert "id" in q and "question" in q and "options" in q
            assert "answer" not in q
            assert len(q["options"]) == 4

    def test_quiz_answer_correct(self, admin_session):
        # Get current status — only test if quota remaining
        st = admin_session.get(f"{BASE_URL}/api/games/status").json()
        if st["games"]["quiz"]["remaining"] <= 0:
            pytest.skip("Quiz daily limit exhausted")
        # Question 1: capital of Cameroon = Yaoundé index 1
        r = admin_session.post(f"{BASE_URL}/api/games/quiz/answer", json={
            "question_id": 1, "answer_index": 1
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["correct"] is True
        # If daily cap not reached, reward = 50
        # Check cap status
        st2 = admin_session.get(f"{BASE_URL}/api/games/status").json()
        earned = float(st2["earned_today"])
        # Reward should be either 50 or clamped
        assert d["reward"] in ("50.00", "50")
        assert d["correct_answer_index"] == 1

    def test_quiz_answer_wrong(self, admin_session):
        st = admin_session.get(f"{BASE_URL}/api/games/status").json()
        if st["games"]["quiz"]["remaining"] <= 0:
            pytest.skip("Quiz daily limit exhausted")
        r = admin_session.post(f"{BASE_URL}/api/games/quiz/answer", json={
            "question_id": 2, "answer_index": 3  # wrong
        })
        assert r.status_code == 200
        d = r.json()
        assert d["correct"] is False
        assert d["reward"] in ("0.00", "0")

    def test_quiz_invalid_question(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/games/quiz/answer", json={
            "question_id": 9999, "answer_index": 0
        })
        assert r.status_code == 404

    def test_spin(self, admin_session):
        st = admin_session.get(f"{BASE_URL}/api/games/status").json()
        if st["games"]["spin"]["remaining"] <= 0:
            pytest.skip("Spin daily limit exhausted")
        r = admin_session.post(f"{BASE_URL}/api/games/spin", json={})
        assert r.status_code == 200, r.text
        d = r.json()
        assert "play_id" in d
        assert "prize_slot" in d
        assert d["prize_slot"] in [0, 10, 25, 50, 100, 250, 500]

    def test_spin_limit_429(self, admin_session):
        # Drain spin to limit
        for _ in range(5):
            r = admin_session.post(f"{BASE_URL}/api/games/spin", json={})
            if r.status_code == 429:
                assert "Limite" in r.text or "Plafond" in r.text
                return
        pytest.skip("Could not exhaust spin limit (cap may be hit)")

    def test_tap_valid(self, admin_session):
        st = admin_session.get(f"{BASE_URL}/api/games/status").json()
        if st["games"]["tap"]["remaining"] <= 0:
            pytest.skip("Tap daily limit exhausted")
        r = admin_session.post(f"{BASE_URL}/api/games/tap", json={"score": 50})
        assert r.status_code == 200
        d = r.json()
        # 50//2 = 25 (unless capped by daily cap)
        # Accept "25" or "25.00" or clamped value
        reward = float(d["reward"])
        assert reward <= 25 and reward >= 0

    def test_tap_invalid_score_negative(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/games/tap", json={"score": -5})
        assert r.status_code == 400

    def test_tap_invalid_score_too_high(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/games/tap", json={"score": 9999})
        assert r.status_code == 400

    def test_history(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/games/history")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_leaderboard(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/games/leaderboard")
        assert r.status_code == 200
        lb = r.json()
        assert isinstance(lb, list)
        for entry in lb:
            assert "rank" in entry and "name" in entry and "total" in entry
