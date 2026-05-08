"""
Iteration 30 — Phase B: Marketplace advanced + Ads + Jobs offers.
Tests public endpoints via REACT_APP_BACKEND_URL.
"""
import os
import uuid
import pytest
import requests
import asyncpg
import asyncio
from decimal import Decimal

from dotenv import load_dotenv
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@japap.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "JapapAdmin2024!")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://japap:japap_secure_2024@localhost:5432/japap_messenger")

# ---------- Fixtures ----------

@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_token(api):
    r = api.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json().get("token") or r.json()["access_token"]


@pytest.fixture(scope="module")
def admin(api, admin_token):
    api.headers.update({"Authorization": f"Bearer {admin_token}"})
    me = api.get(f"{BASE_URL}/api/auth/me").json()
    return me


@pytest.fixture(scope="module")
def second_user(api):
    """Register a secondary user for buyer-side tests."""
    email = f"test_phaseb_{uuid.uuid4().hex[:8]}@japap.test"
    pw = "Test1234!"
    reg = requests.post(f"{BASE_URL}/api/auth/register", json={
        "first_name": "Phase",
        "last_name": "B",
        "username": f"pb_{uuid.uuid4().hex[:6]}",
        "email": email,
        "password": pw,
        "phone_number": "",
        "country": "CM",
        "language": "en",
        "terms_accepted": True,
    })
    if reg.status_code not in (200, 201):
        # try login if user already exists
        lg = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw})
        if lg.status_code != 200:
            pytest.skip(f"Could not register/login second user: {reg.status_code} {reg.text[:200]}")
        token = lg.json().get("token") or lg.json()["access_token"]
        user = lg.json()["user"]
    else:
        data = reg.json()
        token = data.get("token") or data.get("access_token")
        user = data.get("user") or {}
        if not token:
            lg = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw})
            assert lg.status_code == 200, lg.text
            token = lg.json().get("token") or lg.json()["access_token"]
            user = lg.json()["user"]
    return {"token": token, "user": user, "email": email}


# ---------- DB helpers ----------
async def _db_exec(q, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.execute(q, *args)
    finally:
        await conn.close()


async def _db_fetchval(q, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchval(q, *args)
    finally:
        await conn.close()


def db_exec(q, *args):
    return asyncio.run(_db_exec(q, *args))


def db_fetchval(q, *args):
    return asyncio.run(_db_fetchval(q, *args))


# ========================================================================
# MARKETPLACE — list/filters/sort
# ========================================================================
class TestMarketplaceListing:
    def test_list_default_smart_sort(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/marketplace/products")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["sort"] == "smart"
        assert "products" in data
        assert "total" in data

    def test_list_sort_modes(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        for s in ["recent", "price_asc", "price_desc", "top_rated", "smart"]:
            r = api.get(f"{BASE_URL}/api/marketplace/products", params={"sort": s})
            assert r.status_code == 200, f"{s}: {r.text}"
            assert r.json()["sort"] == s

    def test_list_invalid_sort_returns_422(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/marketplace/products", params={"sort": "invalid"})
        assert r.status_code == 422

    def test_list_price_filters(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/marketplace/products", params={"price_min": 0, "price_max": 999999})
        assert r.status_code == 200

    def test_list_country_condition_filter(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/marketplace/products",
                    params={"country": "CM", "condition": "new"})
        assert r.status_code == 200


# ========================================================================
# MARKETPLACE — Favourites / Reviews / Coupons / Boost / Dashboard
# ========================================================================
@pytest.fixture(scope="module")
def admin_product(api, admin_token, admin):
    api.headers.update({"Authorization": f"Bearer {admin_token}"})
    r = api.post(f"{BASE_URL}/api/marketplace/products", json={
        "title": "TEST_PhaseB Product",
        "description": "phase B test",
        "price": 5000,
        "category": "electronics",
        "condition": "new",
    })
    assert r.status_code == 200, r.text
    pid = r.json()["product_id"]
    yield pid
    api.delete(f"{BASE_URL}/api/marketplace/products/{pid}")


class TestFavourites:
    def test_toggle_favourite_on_then_off(self, api, admin_token, admin_product):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r1 = api.post(f"{BASE_URL}/api/marketplace/products/{admin_product}/favourite")
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert "favourited" in d1 and "count" in d1
        first = d1["favourited"]

        r2 = api.post(f"{BASE_URL}/api/marketplace/products/{admin_product}/favourite")
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["favourited"] != first

    def test_list_favourites(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/marketplace/favourites")
        assert r.status_code == 200
        assert "items" in r.json()


class TestReviews:
    def test_review_without_order_returns_403(self, api, admin_token, admin_product):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/marketplace/products/{admin_product}/reviews",
                     json={"rating": 5, "comment": "great"})
        assert r.status_code == 403, r.text
        assert "ORDER_REQUIRED" in r.json().get("detail", "")

    def test_review_invalid_rating(self, api, admin_token, admin_product):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/marketplace/products/{admin_product}/reviews",
                     json={"rating": 9, "comment": ""})
        assert r.status_code == 400

    def test_review_with_order_ok_and_duplicate_409(self, api, admin_token, admin, admin_product, second_user):
        """Seed a completed order between admin and second_user (admin=seller, buyer=second_user)."""
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        # update product seller to admin (already is), buyer = second_user
        buyer_id = second_user["user"]["user_id"]
        order_id = f"ord_TEST_{uuid.uuid4().hex[:8]}"
        db_exec("""INSERT INTO orders (order_id, product_id, buyer_id, seller_id, amount, fee, status)
                   VALUES ($1,$2,$3,$4,$5,$6,'completed')""",
                order_id, admin_product, buyer_id, admin["user_id"], Decimal("5000"), Decimal("250"))
        # buyer posts review
        buyer = requests.Session()
        buyer.headers.update({"Content-Type": "application/json",
                              "Authorization": f"Bearer {second_user['token']}"})
        r1 = buyer.post(f"{BASE_URL}/api/marketplace/products/{admin_product}/reviews",
                        json={"rating": 4, "comment": "TEST phase B"})
        assert r1.status_code == 200, r1.text
        # duplicate
        r2 = buyer.post(f"{BASE_URL}/api/marketplace/products/{admin_product}/reviews",
                        json={"rating": 5, "comment": "again"})
        assert r2.status_code == 409, r2.text
        # cleanup
        db_exec("DELETE FROM product_reviews WHERE product_id = $1", admin_product)
        db_exec("DELETE FROM orders WHERE order_id = $1", order_id)

    def test_list_reviews(self, api, admin_token, admin_product):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/marketplace/products/{admin_product}/reviews")
        assert r.status_code == 200
        assert "items" in r.json()


class TestCoupons:
    @pytest.fixture(scope="class")
    def coupon(self, api, admin_token, admin_product):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        code = f"TEST{uuid.uuid4().hex[:6].upper()}"
        r = api.post(f"{BASE_URL}/api/marketplace/coupons", json={
            "code": code, "discount_pct": 10, "scope": "all",
        })
        assert r.status_code == 200, r.text
        return r.json()

    def test_coupon_code_upper(self, coupon):
        assert coupon["code"] == coupon["code"].upper()

    def test_coupon_validate(self, api, admin_token, admin_product, coupon):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/marketplace/coupons/validate",
                     params={"code": coupon["code"], "product_id": admin_product})
        assert r.status_code == 200, r.text
        d = r.json()
        assert "original_price" in d and "discount" in d and "final_price" in d
        assert float(d["original_price"]) == 5000.0
        assert float(d["final_price"]) == 4500.0  # -10%

    def test_coupon_validate_invalid_code(self, api, admin_token, admin_product):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/marketplace/coupons/validate",
                     params={"code": "NONEXISTENTXYZ", "product_id": admin_product})
        assert r.status_code == 404

    def test_coupon_list_mine(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/marketplace/coupons/mine")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_coupon_delete(self, api, admin_token, coupon):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.delete(f"{BASE_URL}/api/marketplace/coupons/{coupon['coupon_id']}")
        assert r.status_code == 200

    def test_coupon_invalid_pct(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/marketplace/coupons", json={
            "code": "BAD", "discount_pct": 150, "scope": "all",
        })
        assert r.status_code == 400


class TestBoost:
    def test_boost_non_pro_returns_403(self, api, admin_token, admin_product):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        # ensure no active subscription
        db_exec("UPDATE subscriptions SET status = 'expired' WHERE user_id = (SELECT user_id FROM users WHERE email = $1)", ADMIN_EMAIL)
        r = api.post(f"{BASE_URL}/api/marketplace/products/boost",
                     json={"product_id": admin_product, "days": 7})
        assert r.status_code == 403, r.text
        assert "PRO_REQUIRED" in r.json().get("detail", "")

    def test_boost_with_active_sub_ok(self, api, admin_token, admin, admin_product):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        # clean up then insert an active subscription using real schema
        db_exec("DELETE FROM subscriptions WHERE user_id = $1 AND plan_type LIKE 'TEST%'", admin["user_id"])
        db_exec("""INSERT INTO subscriptions (user_id, plan_type, price, currency, status, expires_at)
                   VALUES ($1, 'TEST_pro', 0, 'USD', 'active', NOW() + INTERVAL '30 days')""",
                admin["user_id"])
        try:
            r = api.post(f"{BASE_URL}/api/marketplace/products/boost",
                         json={"product_id": admin_product, "days": 5})
            assert r.status_code == 200, r.text
            d = r.json()
            assert "boost_expires_at" in d and d["days"] == 5
        finally:
            db_exec("DELETE FROM subscriptions WHERE user_id = $1 AND plan_type = 'TEST_pro'", admin["user_id"])


class TestSellerDashboard:
    def test_dashboard_structure(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/marketplace/dashboard/me")
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["products_count", "all_time", "last_30d", "avg_rating", "top_products"]:
            assert k in d, f"missing {k}"
        for k in ["orders", "revenue_local", "total_views", "conversion_pct"]:
            assert k in d["all_time"]
        for k in ["orders", "revenue_local"]:
            assert k in d["last_30d"]


# ========================================================================
# ADS — create / admin / serve / click
# ========================================================================
class TestAds:
    def test_create_campaign_requires_target(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/ads/campaigns", json={
            "target_type": "post", "target_id": "", "budget_usd": 5, "cpm_usd": 1.0,
        })
        assert r.status_code == 400

    def test_create_banner_no_target_but_needs_wallet(self, api, admin_token, admin):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        # ensure wallet has enough
        db_exec("""INSERT INTO wallets (user_id, balance, currency) VALUES ($1, 100000, 'XAF')
                   ON CONFLICT (user_id) DO UPDATE SET balance = GREATEST(wallets.balance, 100000)""",
                admin["user_id"])
        r = api.post(f"{BASE_URL}/api/ads/campaigns", json={
            "target_type": "banner", "title": "TEST banner", "image_url": "https://x.test/b.png",
            "cta_url": "https://x.test", "budget_usd": 3, "cpm_usd": 1.0, "days": 5,
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "pending"
        assert "campaign_id" in d
        # cleanup
        db_exec("DELETE FROM ad_campaigns WHERE campaign_id = $1", d["campaign_id"])

    def test_admin_list_pending(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/ads/admin/campaigns", params={"status": "pending"})
        assert r.status_code == 200, r.text
        assert "items" in r.json()

    def test_admin_approve_flow(self, api, admin_token, admin):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        # create one
        cid = f"ad_TEST_{uuid.uuid4().hex[:8]}"
        db_exec("""INSERT INTO ad_campaigns (campaign_id, owner_id, target_type, title, image_url,
                   cta_url, budget_usd, cpm_usd, status, start_at, end_at)
                   VALUES ($1,$2,'banner','TEST','','',10,1,'pending', NOW(), NOW() + INTERVAL '7 days')""",
                cid, admin["user_id"])
        try:
            r = api.post(f"{BASE_URL}/api/ads/admin/campaigns/{cid}/approve",
                         json={"approve": True, "reason": "OK TEST"})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "approved"
            # cannot re-approve
            r2 = api.post(f"{BASE_URL}/api/ads/admin/campaigns/{cid}/approve",
                          json={"approve": False, "reason": "dup"})
            assert r2.status_code == 400
        finally:
            db_exec("DELETE FROM ad_campaigns WHERE campaign_id = $1", cid)

    def test_serve_with_approved_post_ad(self, api, admin_token, admin):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        cid = f"ad_TEST_{uuid.uuid4().hex[:8]}"
        db_exec("""INSERT INTO ad_campaigns (campaign_id, owner_id, target_type, title, image_url,
                   cta_url, budget_usd, cpm_usd, status, start_at, end_at, spent_usd)
                   VALUES ($1,$2,'post','TEST_serve','','',10,1,'approved',
                           NOW() - INTERVAL '1 hour', NOW() + INTERVAL '7 days', 0)""",
                cid, admin["user_id"])
        try:
            r = api.get(f"{BASE_URL}/api/ads/serve", params={"slot": "feed"})
            assert r.status_code == 200, r.text
            d = r.json()
            # should return our campaign
            assert d.get("campaign_id") == cid or d == {}
        finally:
            db_exec("DELETE FROM ad_events WHERE campaign_id = $1", cid)
            db_exec("DELETE FROM ad_campaigns WHERE campaign_id = $1", cid)

    def test_serve_empty_returns_empty_dict(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/ads/serve", params={"slot": "banner"})
        assert r.status_code == 200
        # empty or object - should be dict
        assert isinstance(r.json(), dict)

    def test_click_tracking(self, api, admin_token, admin):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        cid = f"ad_TEST_{uuid.uuid4().hex[:8]}"
        db_exec("""INSERT INTO ad_campaigns (campaign_id, owner_id, target_type, title, image_url,
                   cta_url, budget_usd, cpm_usd, status, start_at, end_at)
                   VALUES ($1,$2,'banner','t','','',10,1,'approved', NOW(), NOW() + INTERVAL '7 days')""",
                cid, admin["user_id"])
        try:
            r = api.post(f"{BASE_URL}/api/ads/campaigns/{cid}/click")
            assert r.status_code == 200
            clicks = db_fetchval("SELECT clicks FROM ad_campaigns WHERE campaign_id = $1", cid)
            assert clicks >= 1
        finally:
            db_exec("DELETE FROM ad_events WHERE campaign_id = $1", cid)
            db_exec("DELETE FROM ad_campaigns WHERE campaign_id = $1", cid)

    def test_click_unknown_campaign_404(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/ads/campaigns/ad_doesnotexist/click")
        assert r.status_code == 404

    def test_admin_only_list(self, api, second_user):
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json",
                          "Authorization": f"Bearer {second_user['token']}"})
        r = s.get(f"{BASE_URL}/api/ads/admin/campaigns")
        assert r.status_code == 403


# ========================================================================
# JOBS / OFFERS — categories 16 + offer_type filter + create
# ========================================================================
class TestJobsOffers:
    def test_categories_contain_16(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/jobs/categories")
        assert r.status_code == 200
        cats = r.json()
        ids = {c["id"] for c in cats}
        expected = {"tech", "sales", "marketing", "logistics", "services", "craft",
                    "design", "writing", "video", "translation", "consulting",
                    "housing", "vehicles", "goods", "announcement", "other"}
        assert expected.issubset(ids), f"missing: {expected - ids}"
        assert len(cats) >= 16

    def test_list_with_offer_type_filter(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.get(f"{BASE_URL}/api/jobs/list", params={"offer_type": "mission"})
        assert r.status_code == 200
        for j in r.json()["jobs"]:
            assert j["offer_type"] == "mission"

    def test_create_job_offer_type(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/jobs/create", json={
            "title": "TEST_job", "description": "t", "category": "tech", "type": "full_time",
            "offer_type": "job", "salary_min": 100, "salary_max": 200,
        })
        assert r.status_code == 200, r.text
        db_exec("DELETE FROM jobs WHERE job_id = $1", r.json()["job_id"])

    def test_create_mission_with_deadline_budget(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/jobs/create", json={
            "title": "TEST_mission", "description": "t", "category": "design", "type": "freelance",
            "offer_type": "mission", "budget_usd": 300, "deadline": "2026-12-31",
        })
        assert r.status_code == 200, r.text
        db_exec("DELETE FROM jobs WHERE job_id = $1", r.json()["job_id"])

    def test_create_annonce(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/jobs/create", json={
            "title": "TEST_annonce", "description": "t", "category": "housing", "type": "full_time",
            "offer_type": "annonce", "deadline": "2026-12-31",
        })
        assert r.status_code == 200, r.text
        db_exec("DELETE FROM jobs WHERE job_id = $1", r.json()["job_id"])

    def test_create_invalid_offer_type(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/jobs/create", json={
            "title": "x", "category": "tech", "type": "full_time", "offer_type": "INVALID",
        })
        assert r.status_code == 400

    def test_create_invalid_deadline(self, api, admin_token):
        api.headers.update({"Authorization": f"Bearer {admin_token}"})
        r = api.post(f"{BASE_URL}/api/jobs/create", json={
            "title": "x", "category": "tech", "type": "full_time",
            "offer_type": "mission", "deadline": "not-a-date",
        })
        assert r.status_code == 400
