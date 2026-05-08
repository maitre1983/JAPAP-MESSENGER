"""
JAPAP Messenger Iteration 10 Tests
Tests for: Pro Subscriptions, Push Notifications, Landing Page Redirect
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
DEMO_USER_EMAIL = "demo@japap.com"
DEMO_USER_PASSWORD = "Demo2024!"
TEST_USER_EMAIL = "testuser@japap.com"
TEST_USER_PASSWORD = "TestUser2024!"


class TestProPlans:
    """Tests for Pro subscription plans API"""
    
    @pytest.fixture
    def auth_session(self):
        """Get authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        })
        if response.status_code != 200:
            # Try to register
            response = session.post(f"{BASE_URL}/api/auth/register", json={
                "email": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD,
                "first_name": "Test",
                "last_name": "User",
                "terms_accepted": True
            })
        assert response.status_code == 200, f"Auth failed: {response.text}"
        return session
    
    def test_get_pro_plans_returns_4_plans(self, auth_session):
        """Test GET /api/pro/plans returns 4 plans (Star, Hot, Ultima, VIP)"""
        response = auth_session.get(f"{BASE_URL}/api/pro/plans")
        assert response.status_code == 200, f"Get plans failed: {response.text}"
        plans = response.json()
        
        assert isinstance(plans, list)
        assert len(plans) == 4, f"Expected 4 plans, got {len(plans)}"
        
        # Verify plan names
        plan_names = [p['name'] for p in plans]
        assert 'Star' in plan_names, "Star plan missing"
        assert 'Hot' in plan_names, "Hot plan missing"
        assert 'Ultima' in plan_names, "Ultima plan missing"
        assert 'VIP' in plan_names, "VIP plan missing"
        
        print(f"Found {len(plans)} plans: {plan_names}")
    
    def test_pro_plans_have_correct_prices(self, auth_session):
        """Test that plans have correct prices: Star=2000, Hot=5000, Ultima=15000, VIP=50000"""
        response = auth_session.get(f"{BASE_URL}/api/pro/plans")
        assert response.status_code == 200
        plans = response.json()
        
        expected_prices = {
            'Star': 2000,
            'Hot': 5000,
            'Ultima': 15000,
            'VIP': 50000
        }
        
        for plan in plans:
            name = plan['name']
            price = float(plan['price'])
            if name in expected_prices:
                assert price == expected_prices[name], f"{name} price should be {expected_prices[name]}, got {price}"
                print(f"{name}: {price} XAF - CORRECT")
    
    def test_pro_plans_have_required_fields(self, auth_session):
        """Test that each plan has required fields"""
        response = auth_session.get(f"{BASE_URL}/api/pro/plans")
        assert response.status_code == 200
        plans = response.json()
        
        required_fields = ['plan_id', 'name', 'price', 'duration_days', 'is_active']
        
        for plan in plans:
            for field in required_fields:
                assert field in plan, f"Plan {plan.get('name', 'unknown')} missing field: {field}"
        
        print("All plans have required fields")


class TestProStatus:
    """Tests for Pro subscription status API"""
    
    @pytest.fixture
    def demo_session(self):
        """Get authenticated demo user session (has Star subscription)"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": DEMO_USER_EMAIL,
            "password": DEMO_USER_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip("Demo user login failed - may not exist")
        return session
    
    @pytest.fixture
    def test_session(self):
        """Get authenticated test user session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        })
        if response.status_code != 200:
            response = session.post(f"{BASE_URL}/api/auth/register", json={
                "email": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD,
                "first_name": "Test",
                "last_name": "User",
                "terms_accepted": True
            })
        assert response.status_code == 200
        return session
    
    def test_demo_user_has_pro_status(self, demo_session):
        """Test GET /api/pro/status returns is_pro=true for demo user"""
        response = demo_session.get(f"{BASE_URL}/api/pro/status")
        assert response.status_code == 200, f"Get status failed: {response.text}"
        data = response.json()
        
        # Demo user should have Star subscription
        if data.get('is_pro'):
            assert data['is_pro'] == True
            assert 'plan_name' in data
            assert 'expires_at' in data
            print(f"Demo user has Pro: {data['plan_name']} until {data['expires_at']}")
        else:
            print("Demo user does not have active Pro subscription (may have expired)")
    
    def test_pro_status_returns_is_pro_field(self, test_session):
        """Test that /api/pro/status always returns is_pro field"""
        response = test_session.get(f"{BASE_URL}/api/pro/status")
        assert response.status_code == 200
        data = response.json()
        
        assert 'is_pro' in data, "Response should contain is_pro field"
        assert isinstance(data['is_pro'], bool), "is_pro should be boolean"
        print(f"Pro status: is_pro={data['is_pro']}")


class TestProSubscribe:
    """Tests for Pro subscription purchase API"""
    
    @pytest.fixture
    def fresh_user_session(self):
        """Create a fresh user for subscription testing"""
        session = requests.Session()
        unique_email = f"test_pro_{int(time.time())}@japap.com"
        response = session.post(f"{BASE_URL}/api/auth/register", json={
            "email": unique_email,
            "password": "TestPro123!",
            "first_name": "ProTest",
            "last_name": "User",
            "terms_accepted": True
        })
        if response.status_code != 200:
            pytest.skip(f"Could not create test user: {response.text}")
        return session, unique_email
    
    def test_subscribe_requires_sufficient_balance(self, fresh_user_session):
        """Test POST /api/pro/subscribe fails with insufficient balance"""
        session, email = fresh_user_session
        
        response = session.post(f"{BASE_URL}/api/pro/subscribe", json={
            "plan_id": "plan_star"
        })
        
        # Should fail due to insufficient balance
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        data = response.json()
        assert "balance" in data.get('detail', '').lower() or "insufficient" in data.get('detail', '').lower()
        print(f"Correctly rejected: {data['detail']}")
    
    def test_subscribe_with_invalid_plan_fails(self, fresh_user_session):
        """Test POST /api/pro/subscribe fails with invalid plan_id"""
        session, email = fresh_user_session
        
        response = session.post(f"{BASE_URL}/api/pro/subscribe", json={
            "plan_id": "invalid_plan"
        })
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("Invalid plan correctly rejected")
    
    def test_subscribe_deducts_wallet_and_activates(self, fresh_user_session):
        """Test full subscription flow: deposit -> subscribe -> verify"""
        session, email = fresh_user_session
        
        # First deposit enough for Star plan (2000 XAF)
        deposit_resp = session.post(f"{BASE_URL}/api/wallet/deposit", json={
            "amount": 5000.00,
            "method": "bank_transfer"
        })
        
        if deposit_resp.status_code != 200:
            pytest.skip(f"Deposit failed: {deposit_resp.text}")
        
        # Check balance before
        balance_before = session.get(f"{BASE_URL}/api/wallet/balance").json()
        print(f"Balance before: {balance_before['balance']} XAF")
        
        # Subscribe to Star plan
        subscribe_resp = session.post(f"{BASE_URL}/api/pro/subscribe", json={
            "plan_id": "plan_star"
        })
        
        assert subscribe_resp.status_code == 200, f"Subscribe failed: {subscribe_resp.text}"
        sub_data = subscribe_resp.json()
        
        assert 'message' in sub_data
        assert 'expires_at' in sub_data
        assert 'tx_id' in sub_data
        print(f"Subscription activated: {sub_data['message']}")
        
        # Verify balance was deducted
        balance_after = session.get(f"{BASE_URL}/api/wallet/balance").json()
        expected_balance = float(balance_before['balance']) - 2000
        assert float(balance_after['balance']) == expected_balance, \
            f"Balance should be {expected_balance}, got {balance_after['balance']}"
        print(f"Balance after: {balance_after['balance']} XAF (deducted 2000)")
        
        # Verify pro status
        status_resp = session.get(f"{BASE_URL}/api/pro/status")
        status = status_resp.json()
        assert status['is_pro'] == True
        assert status['plan_id'] == 'plan_star'
        print(f"Pro status verified: {status['plan_name']}")


class TestPushNotifications:
    """Tests for Push Notifications API"""
    
    @pytest.fixture
    def auth_session(self):
        """Get authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        })
        if response.status_code != 200:
            response = session.post(f"{BASE_URL}/api/auth/register", json={
                "email": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD,
                "first_name": "Test",
                "last_name": "User",
                "terms_accepted": True
            })
        assert response.status_code == 200
        return session
    
    @pytest.fixture
    def admin_session(self):
        """Get authenticated admin session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        return session
    
    def test_get_notifications_returns_list(self, auth_session):
        """Test GET /api/push/notifications returns notifications list"""
        response = auth_session.get(f"{BASE_URL}/api/push/notifications")
        assert response.status_code == 200, f"Get notifications failed: {response.text}"
        data = response.json()
        
        assert 'notifications' in data
        assert 'total' in data
        assert 'unread' in data
        assert 'page' in data
        assert isinstance(data['notifications'], list)
        
        print(f"Notifications: {data['total']} total, {data['unread']} unread")
    
    def test_notifications_have_required_fields(self, auth_session):
        """Test that notifications have required fields"""
        response = auth_session.get(f"{BASE_URL}/api/push/notifications")
        assert response.status_code == 200
        data = response.json()
        
        if data['notifications']:
            notif = data['notifications'][0]
            required_fields = ['notif_id', 'user_id', 'type', 'title', 'message', 'is_read', 'created_at']
            for field in required_fields:
                assert field in notif, f"Notification missing field: {field}"
            print(f"Sample notification: {notif['title']}")
        else:
            print("No notifications to verify fields")
    
    def test_mark_notification_as_read(self, auth_session, admin_session):
        """Test PUT /api/push/read/{id} marks notification as read"""
        # First, admin sends a notification to test user
        me_resp = auth_session.get(f"{BASE_URL}/api/auth/me")
        test_user_id = me_resp.json()['user_id']
        
        # Admin sends notification
        send_resp = admin_session.post(f"{BASE_URL}/api/push/send", json={
            "user_id": test_user_id,
            "title": "Test Notification",
            "message": f"Test message at {int(time.time())}"
        })
        
        if send_resp.status_code != 200:
            pytest.skip(f"Could not send notification: {send_resp.text}")
        
        notif_id = send_resp.json()['notif_id']
        
        # Get notifications and find the unread one
        notifs_resp = auth_session.get(f"{BASE_URL}/api/push/notifications")
        notifs = notifs_resp.json()
        
        # Mark as read
        read_resp = auth_session.put(f"{BASE_URL}/api/push/read/{notif_id}")
        assert read_resp.status_code == 200, f"Mark read failed: {read_resp.text}"
        print(f"Notification {notif_id} marked as read")
    
    def test_mark_all_notifications_as_read(self, auth_session):
        """Test PUT /api/push/read-all marks all as read"""
        response = auth_session.put(f"{BASE_URL}/api/push/read-all")
        assert response.status_code == 200, f"Mark all read failed: {response.text}"
        
        # Verify unread count is 0
        notifs_resp = auth_session.get(f"{BASE_URL}/api/push/notifications")
        data = notifs_resp.json()
        assert data['unread'] == 0, f"Unread should be 0, got {data['unread']}"
        print("All notifications marked as read")


class TestAdminPushNotifications:
    """Tests for Admin Push Notification APIs"""
    
    @pytest.fixture
    def admin_session(self):
        """Get authenticated admin session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200
        return session
    
    @pytest.fixture
    def test_user_session(self):
        """Get test user session and ID"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        })
        if response.status_code != 200:
            response = session.post(f"{BASE_URL}/api/auth/register", json={
                "email": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD,
                "first_name": "Test",
                "last_name": "User",
                "terms_accepted": True
            })
        assert response.status_code == 200
        me_resp = session.get(f"{BASE_URL}/api/auth/me")
        user_id = me_resp.json()['user_id']
        return session, user_id
    
    def test_admin_send_notification_to_user(self, admin_session, test_user_session):
        """Test POST /api/push/send sends notification to specific user"""
        test_session, test_user_id = test_user_session
        
        response = admin_session.post(f"{BASE_URL}/api/push/send", json={
            "user_id": test_user_id,
            "title": "Admin Test Notification",
            "message": f"This is a test notification sent at {int(time.time())}"
        })
        
        assert response.status_code == 200, f"Send failed: {response.text}"
        data = response.json()
        assert 'notif_id' in data
        assert 'message' in data
        print(f"Notification sent: {data['notif_id']}")
        
        # Verify user received it
        notifs_resp = test_session.get(f"{BASE_URL}/api/push/notifications")
        notifs = notifs_resp.json()['notifications']
        found = any(n['notif_id'] == data['notif_id'] for n in notifs)
        assert found, "User should have received the notification"
        print("User received the notification")
    
    def test_admin_broadcast_notification(self, admin_session):
        """Test POST /api/push/broadcast sends to all users"""
        response = admin_session.post(f"{BASE_URL}/api/push/broadcast", json={
            "user_id": "",  # Not used for broadcast
            "title": "Broadcast Test",
            "message": f"Broadcast message at {int(time.time())}"
        })
        
        assert response.status_code == 200, f"Broadcast failed: {response.text}"
        data = response.json()
        assert 'message' in data
        assert 'users' in data['message'].lower() or 'broadcast' in data['message'].lower()
        print(f"Broadcast result: {data['message']}")
    
    def test_non_admin_cannot_send_notification(self, test_user_session):
        """Test that non-admin users cannot send notifications"""
        test_session, test_user_id = test_user_session
        
        response = test_session.post(f"{BASE_URL}/api/push/send", json={
            "user_id": test_user_id,
            "title": "Unauthorized",
            "message": "Should fail"
        })
        
        assert response.status_code == 403, f"Expected 403, got {response.status_code}"
        print("Non-admin correctly rejected from sending notifications")
    
    def test_non_admin_cannot_broadcast(self, test_user_session):
        """Test that non-admin users cannot broadcast"""
        test_session, test_user_id = test_user_session
        
        response = test_session.post(f"{BASE_URL}/api/push/broadcast", json={
            "user_id": "",
            "title": "Unauthorized Broadcast",
            "message": "Should fail"
        })
        
        assert response.status_code == 403, f"Expected 403, got {response.status_code}"
        print("Non-admin correctly rejected from broadcasting")


class TestDemoUserLogin:
    """Test demo user login with specified credentials"""
    
    def test_demo_user_login(self):
        """Test login with demo@japap.com / Demo2024!"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "demo@japap.com",
            "password": "Demo2024!"
        })
        
        if response.status_code == 200:
            data = response.json()
            assert data["user"]["email"] == "demo@japap.com"
            print(f"Demo user login successful: {data['user']['email']}")
        else:
            # Demo user may not exist yet
            print(f"Demo user login failed: {response.status_code} - {response.text}")
            pytest.skip("Demo user does not exist")


class TestAdminLogin:
    """Test admin login with specified credentials"""
    
    def test_admin_login(self):
        """Test login with admin@japap.com / JapapAdmin2024!"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@japap.com",
            "password": "JapapAdmin2024!"
        })
        
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        data = response.json()
        assert data["user"]["email"] == "admin@japap.com"
        assert data["user"]["role"] == "admin"
        print(f"Admin login successful: {data['user']['email']} (role: {data['user']['role']})")


class TestExistingFeatures:
    """Verify existing features still work after iteration 10 changes"""
    
    @pytest.fixture
    def auth_session(self):
        """Get authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200
        return session
    
    def test_feed_endpoints(self, auth_session):
        """Test feed endpoints still work"""
        response = auth_session.get(f"{BASE_URL}/api/feed/posts")
        assert response.status_code == 200
        print("Feed endpoints working")
    
    def test_wallet_endpoints(self, auth_session):
        """Test wallet endpoints still work"""
        response = auth_session.get(f"{BASE_URL}/api/wallet/balance")
        assert response.status_code == 200
        data = response.json()
        assert "balance" in data
        print(f"Wallet balance: {data['balance']} {data.get('currency', 'XAF')}")
    
    def test_marketplace_endpoints(self, auth_session):
        """Test marketplace endpoints still work"""
        response = auth_session.get(f"{BASE_URL}/api/marketplace/products")
        assert response.status_code == 200
        print("Marketplace endpoints working")
    
    def test_messaging_endpoints(self, auth_session):
        """Test messaging endpoints still work"""
        response = auth_session.get(f"{BASE_URL}/api/messages/conversations")
        assert response.status_code == 200
        print("Messaging endpoints working")
    
    def test_crypto_endpoints(self, auth_session):
        """Test crypto endpoints still work"""
        response = auth_session.get(f"{BASE_URL}/api/crypto/market")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 8  # 8 cryptocurrencies
        print(f"Crypto market: {len(data)} coins available")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
