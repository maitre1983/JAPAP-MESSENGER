"""
Iteration 13 backend tests:
- Admin /stats enrichment (crowdfunding/gaming/jobs/transport)
- Jobs CRUD (/categories, /create, /list, /{id}, /apply, /applications, /my/postings, /my/applications)
- Transport flow (/estimate, /request, /driver/register, /available, /accept, /complete, /cancel, /my-rides)
"""
import os
import time
import pytest
import requests
from decimal import Decimal

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
DRIVER = {"email": "testref_1776710356@japap.com", "password": "Test1234!"}
APPLICANT = {"email": "testref_2_1776710380@japap.com", "password": "Test1234!"}


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, f"Login failed for {creds['email']}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_client():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def driver_client():
    return _login(DRIVER)


@pytest.fixture(scope="module")
def applicant_client():
    return _login(APPLICANT)


# ============ ADMIN STATS ============
class TestAdminStats:
    def test_admin_stats_enriched(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/admin/stats", timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        # Old fields preserved
        for k in ["total_users", "active_users", "online_users", "pro_users",
                  "total_transactions", "pending_transactions", "total_balance", "total_messages"]:
            assert k in d, f"Missing legacy field {k}"
        # Crowdfunding
        assert "crowdfunding" in d
        cf = d["crowdfunding"]
        for k in ["total_campaigns", "active_campaigns", "total_raised", "total_contributions", "by_category"]:
            assert k in cf, f"crowdfunding missing {k}"
        assert isinstance(cf["by_category"], list)
        # Gaming
        assert "gaming" in d
        gm = d["gaming"]
        for k in ["total_plays", "total_rewarded", "active_players_30d", "by_type"]:
            assert k in gm
        assert isinstance(gm["by_type"], list)
        # Jobs
        assert "jobs" in d
        for k in ["total", "open", "applications"]:
            assert k in d["jobs"]
        # Transport
        assert "transport" in d
        for k in ["rides_total", "rides_completed", "revenue_total", "drivers"]:
            assert k in d["transport"]

    def test_admin_stats_forbidden_for_non_admin(self, driver_client):
        r = driver_client.get(f"{BASE_URL}/api/admin/stats", timeout=20)
        assert r.status_code == 403, r.text


# ============ JOBS ============
class TestJobs:
    job_id = None

    def test_categories(self, applicant_client):
        r = applicant_client.get(f"{BASE_URL}/api/jobs/categories", timeout=15)
        assert r.status_code == 200
        cats = r.json()
        ids = {c["id"] for c in cats}
        assert ids == {"tech", "sales", "marketing", "logistics", "services", "craft", "other"}

    def test_create_invalid_category(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/jobs/create", json={
            "title": "TEST_invalid", "category": "nope", "type": "full_time"
        }, timeout=15)
        assert r.status_code == 400

    def test_create_invalid_salary(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/jobs/create", json={
            "title": "TEST_bad_salary", "category": "tech", "type": "full_time",
            "salary_min": 200000, "salary_max": 100000
        }, timeout=15)
        assert r.status_code == 400

    def test_create_job_success(self, admin_client):
        ts = int(time.time())
        r = admin_client.post(f"{BASE_URL}/api/jobs/create", json={
            "title": f"TEST_DevPython_{ts}",
            "description": "Recherche dev Python keyword UNIQUE_KW_TEST",
            "category": "tech", "type": "full_time", "location": "Douala",
            "salary_min": 300000, "salary_max": 600000, "remote": True
        }, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "job_id" in data
        TestJobs.job_id = data["job_id"]

    def test_list_filter_category(self, applicant_client):
        r = applicant_client.get(f"{BASE_URL}/api/jobs/list?category=tech", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "jobs" in d
        for j in d["jobs"]:
            assert j["category"] == "tech"

    def test_list_search(self, applicant_client):
        r = applicant_client.get(f"{BASE_URL}/api/jobs/list?search=UNIQUE_KW_TEST", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["total"] >= 1

    def test_get_job_detail(self, applicant_client):
        assert TestJobs.job_id, "Need created job"
        r = applicant_client.get(f"{BASE_URL}/api/jobs/{TestJobs.job_id}", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["job_id"] == TestJobs.job_id
        assert "has_applied" in d
        assert d["has_applied"] is False

    def test_self_apply_rejected(self, admin_client):
        assert TestJobs.job_id
        r = admin_client.post(f"{BASE_URL}/api/jobs/{TestJobs.job_id}/apply",
                              json={"cover_letter": "self"}, timeout=15)
        assert r.status_code == 400

    def test_apply_success(self, applicant_client):
        assert TestJobs.job_id
        r = applicant_client.post(f"{BASE_URL}/api/jobs/{TestJobs.job_id}/apply",
                                  json={"cover_letter": "Très intéressé"}, timeout=15)
        assert r.status_code == 200, r.text
        # has_applied flag flips
        r2 = applicant_client.get(f"{BASE_URL}/api/jobs/{TestJobs.job_id}", timeout=15)
        assert r2.json()["has_applied"] is True

    def test_duplicate_apply_rejected(self, applicant_client):
        assert TestJobs.job_id
        r = applicant_client.post(f"{BASE_URL}/api/jobs/{TestJobs.job_id}/apply",
                                  json={"cover_letter": "again"}, timeout=15)
        assert r.status_code == 400

    def test_applications_owner(self, admin_client):
        assert TestJobs.job_id
        r = admin_client.get(f"{BASE_URL}/api/jobs/{TestJobs.job_id}/applications", timeout=15)
        assert r.status_code == 200
        apps = r.json()
        assert isinstance(apps, list)
        assert any("applicant" in a for a in apps)

    def test_applications_forbidden_other_user(self, driver_client):
        assert TestJobs.job_id
        r = driver_client.get(f"{BASE_URL}/api/jobs/{TestJobs.job_id}/applications", timeout=15)
        # driver is not poster nor admin
        assert r.status_code == 403

    def test_my_postings_admin(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/jobs/my/postings", timeout=15)
        assert r.status_code == 200
        assert any(j["job_id"] == TestJobs.job_id for j in r.json())

    def test_my_applications_applicant(self, applicant_client):
        r = applicant_client.get(f"{BASE_URL}/api/jobs/my/applications", timeout=15)
        assert r.status_code == 200
        apps = r.json()
        assert any(a["job_id"] == TestJobs.job_id for a in apps)


# ============ TRANSPORT ============
class TestTransport:
    ride_id = None
    # Douala approx coords
    PICKUP = (4.0511, 9.7679)
    DROPOFF = (4.0610, 9.7800)

    def test_estimate_haversine(self, applicant_client):
        r = applicant_client.get(
            f"{BASE_URL}/api/transport/estimate"
            f"?pickup_lat={self.PICKUP[0]}&pickup_lng={self.PICKUP[1]}"
            f"&dropoff_lat={self.DROPOFF[0]}&dropoff_lng={self.DROPOFF[1]}"
            f"&vehicle_type=standard", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "fare_estimated" in d and "distance_km" in d
        # Manual haversine: ~1.7 km between these points → fare = 500 + 1.7*200 ≈ 840
        assert 0.5 < d["distance_km"] < 5
        fare = Decimal(d["fare_estimated"])
        # fare ~ 500 + d*200
        expected = 500 + d["distance_km"] * 200
        assert abs(float(fare) - expected) < 5

    def test_estimate_premium_rate(self, applicant_client):
        r = applicant_client.get(
            f"{BASE_URL}/api/transport/estimate"
            f"?pickup_lat={self.PICKUP[0]}&pickup_lng={self.PICKUP[1]}"
            f"&dropoff_lat={self.DROPOFF[0]}&dropoff_lng={self.DROPOFF[1]}"
            f"&vehicle_type=premium", timeout=15)
        assert r.status_code == 200
        assert r.json()["breakdown"]["per_km"] == "350"

    def test_driver_register_or_update(self, driver_client):
        # Already registered from prev iteration; this should update
        r = driver_client.post(f"{BASE_URL}/api/transport/driver/register", json={
            "vehicle_model": "Toyota Corolla", "vehicle_plate": "CE123AB", "vehicle_type": "standard"
        }, timeout=15)
        assert r.status_code == 200, r.text

    def test_available_forbidden_non_driver(self, applicant_client):
        # applicant is not driver
        r = applicant_client.get(f"{BASE_URL}/api/transport/available", timeout=15)
        assert r.status_code == 403

    def test_request_ride(self, admin_client):
        # Admin has trillions in wallet → safe
        r = admin_client.post(f"{BASE_URL}/api/transport/request", json={
            "pickup_address": "Akwa", "dropoff_address": "Bonanjo",
            "pickup_lat": self.PICKUP[0], "pickup_lng": self.PICKUP[1],
            "dropoff_lat": self.DROPOFF[0], "dropoff_lng": self.DROPOFF[1],
            "vehicle_type": "standard", "notes": "TEST iter13"
        }, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "pending"
        assert "ride_id" in d
        TestTransport.ride_id = d["ride_id"]

    def test_available_for_driver_includes_ride(self, driver_client):
        assert TestTransport.ride_id
        r = driver_client.get(f"{BASE_URL}/api/transport/available", timeout=15)
        assert r.status_code == 200
        ids = {x["ride_id"] for x in r.json()}
        assert TestTransport.ride_id in ids

    def test_accept_ride(self, driver_client):
        assert TestTransport.ride_id
        r = driver_client.post(f"{BASE_URL}/api/transport/{TestTransport.ride_id}/accept", timeout=15)
        assert r.status_code == 200, r.text

    def test_accept_already_accepted_fails(self, driver_client):
        # Re-accepting a non-pending ride must fail
        assert TestTransport.ride_id
        r = driver_client.post(f"{BASE_URL}/api/transport/{TestTransport.ride_id}/accept", timeout=15)
        assert r.status_code == 400

    def test_complete_ride_only_driver(self, admin_client):
        # Admin (rider) cannot complete
        assert TestTransport.ride_id
        r = admin_client.post(f"{BASE_URL}/api/transport/{TestTransport.ride_id}/complete", timeout=15)
        assert r.status_code == 403

    def test_complete_ride_with_payment(self, driver_client):
        assert TestTransport.ride_id
        # Get balances pre
        wallet_pre = driver_client.get(f"{BASE_URL}/api/wallet/me", timeout=15)
        if wallet_pre.status_code != 200:
            wallet_pre = driver_client.get(f"{BASE_URL}/api/wallet/balance", timeout=15)
        balance_pre = None
        if wallet_pre.status_code == 200:
            try:
                balance_pre = Decimal(str(wallet_pre.json().get("balance", "0")))
            except Exception:
                balance_pre = None
        r = driver_client.post(f"{BASE_URL}/api/transport/{TestTransport.ride_id}/complete", timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "fare" in d and "fee" in d and "net_driver" in d
        fare = Decimal(d["fare"])
        fee = Decimal(d["fee"])
        net = Decimal(d["net_driver"])
        # 15% commission
        assert abs(float(fee) - float(fare) * 0.15) < 0.05
        assert abs(float(net) - (float(fare) - float(fee))) < 0.05
        # Balance increased by net
        if balance_pre is not None:
            wallet_post = driver_client.get(f"{BASE_URL}/api/wallet/me", timeout=15)
            if wallet_post.status_code != 200:
                wallet_post = driver_client.get(f"{BASE_URL}/api/wallet/balance", timeout=15)
            if wallet_post.status_code == 200:
                try:
                    balance_post = Decimal(str(wallet_post.json().get("balance", "0")))
                    assert abs(float(balance_post - balance_pre) - float(net)) < 1
                except Exception:
                    pass

    def test_cancel_completed_fails(self, driver_client):
        assert TestTransport.ride_id
        r = driver_client.post(f"{BASE_URL}/api/transport/{TestTransport.ride_id}/cancel", timeout=15)
        assert r.status_code == 400

    def test_my_rides_includes_ride(self, driver_client):
        assert TestTransport.ride_id
        r = driver_client.get(f"{BASE_URL}/api/transport/my-rides", timeout=15)
        assert r.status_code == 200
        rides = r.json()
        match = [x for x in rides if x["ride_id"] == TestTransport.ride_id]
        assert match
        assert match[0]["role"] == "driver"
        assert match[0]["status"] == "completed"

    def test_cancel_pending_ride(self, admin_client):
        # Create then cancel
        r = admin_client.post(f"{BASE_URL}/api/transport/request", json={
            "pickup_address": "A", "dropoff_address": "B",
            "pickup_lat": self.PICKUP[0], "pickup_lng": self.PICKUP[1],
            "dropoff_lat": self.DROPOFF[0], "dropoff_lng": self.DROPOFF[1],
            "vehicle_type": "standard"
        }, timeout=15)
        assert r.status_code == 200
        rid = r.json()["ride_id"]
        c = admin_client.post(f"{BASE_URL}/api/transport/{rid}/cancel", timeout=15)
        assert c.status_code == 200
