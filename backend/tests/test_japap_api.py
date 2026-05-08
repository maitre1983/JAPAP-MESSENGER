"""
JAPAP Messenger API Tests
Tests for: Auth, Wallet, Messaging, Admin, Users endpoints
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
TEST_USER_EMAIL = "testuser@japap.com"
TEST_USER_PASSWORD = "TestUser2024!"


class TestHealthAndBasics:
    """Basic health check and API root tests"""
    
    def test_api_root(self):
        """Test API root endpoint"""
        response = requests.get(f"{BASE_URL}/api")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "JAPAP" in data["message"]
        print(f"API Root: {data}")
    
    def test_health_endpoint(self):
        """Test health check endpoint"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        print(f"Health: {data}")


class TestAuthEndpoints:
    """Authentication endpoint tests"""
    
    def test_admin_login_success(self):
        """Test admin login with correct credentials"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        data = response.json()
        assert "user" in data
        assert "access_token" in data
        assert data["user"]["email"] == ADMIN_EMAIL
        assert data["user"]["role"] == "admin"
        print(f"Admin login successful: {data['user']['email']}")
        return session
    
    def test_login_invalid_credentials(self):
        """Test login with wrong password"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": "wrongpassword"
        })
        assert response.status_code == 401
        data = response.json()
        assert "detail" in data
        print(f"Invalid login correctly rejected: {data['detail']}")
    
    def test_register_new_user(self):
        """Test user registration"""
        session = requests.Session()
        # First try to register - may fail if user exists
        response = session.post(f"{BASE_URL}/api/auth/register", json={
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD,
            "first_name": "Test",
            "last_name": "User"
        })
        
        if response.status_code == 400 and "already registered" in response.text.lower():
            # User exists, try login instead
            response = session.post(f"{BASE_URL}/api/auth/login", json={
                "email": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD
            })
            assert response.status_code == 200, f"Login failed for existing user: {response.text}"
            print("User already exists, logged in successfully")
        else:
            assert response.status_code == 200, f"Registration failed: {response.text}"
            data = response.json()
            assert "user" in data
            assert data["user"]["email"] == TEST_USER_EMAIL
            print(f"User registered: {data['user']['email']}")
        
        return session
    
    def test_get_me_authenticated(self):
        """Test /me endpoint with authenticated session"""
        session = requests.Session()
        # Login first
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
        
        # Get user info
        response = session.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 200, f"Get me failed: {response.text}"
        data = response.json()
        assert data["email"] == ADMIN_EMAIL
        assert "wallet_balance" in data
        print(f"Get me successful: {data['email']}, balance: {data.get('wallet_balance')}")
    
    def test_get_me_unauthenticated(self):
        """Test /me endpoint without authentication"""
        response = requests.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 401
        print("Unauthenticated /me correctly rejected")
    
    def test_logout(self):
        """Test logout endpoint"""
        session = requests.Session()
        # Login first
        session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        
        # Logout
        response = session.post(f"{BASE_URL}/api/auth/logout")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        print(f"Logout successful: {data['message']}")


class TestWalletEndpoints:
    """Wallet endpoint tests"""
    
    @pytest.fixture
    def auth_session(self):
        """Get authenticated session for test user"""
        session = requests.Session()
        # Try login first
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        })
        if response.status_code != 200:
            # Register if not exists
            response = session.post(f"{BASE_URL}/api/auth/register", json={
                "email": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD,
                "first_name": "Test",
                "last_name": "User"
            })
        assert response.status_code == 200, f"Auth failed: {response.text}"
        return session
    
    def test_get_balance(self, auth_session):
        """Test wallet balance endpoint"""
        response = auth_session.get(f"{BASE_URL}/api/wallet/balance")
        assert response.status_code == 200, f"Get balance failed: {response.text}"
        data = response.json()
        assert "balance" in data
        assert "currency" in data
        assert data["currency"] == "XAF"
        print(f"Balance: {data['balance']} {data['currency']}")
    
    def test_deposit_money(self, auth_session):
        """Test deposit endpoint"""
        response = auth_session.post(f"{BASE_URL}/api/wallet/deposit", json={
            "amount": 1000.00,
            "method": "bank_transfer",
            "reference": "TEST_DEP_001",
            "notes": "Test deposit"
        })
        assert response.status_code == 200, f"Deposit failed: {response.text}"
        data = response.json()
        assert "tx_id" in data
        assert "new_balance" in data
        assert data["message"] == "Deposit successful"
        print(f"Deposit successful: {data['tx_id']}, new balance: {data['new_balance']}")
    
    def test_withdraw_money(self, auth_session):
        """Test withdraw endpoint"""
        # First deposit to ensure balance
        auth_session.post(f"{BASE_URL}/api/wallet/deposit", json={
            "amount": 500.00,
            "method": "bank_transfer"
        })
        
        response = auth_session.post(f"{BASE_URL}/api/wallet/withdraw", json={
            "amount": 100.00,
            "method": "mobile_money",
            "reference": "TEST_WDR_001",
            "notes": "Test withdrawal"
        })
        assert response.status_code == 200, f"Withdraw failed: {response.text}"
        data = response.json()
        assert "tx_id" in data
        assert "new_balance" in data
        print(f"Withdrawal submitted: {data['tx_id']}")
    
    def test_withdraw_insufficient_balance(self, auth_session):
        """Test withdraw with insufficient balance"""
        response = auth_session.post(f"{BASE_URL}/api/wallet/withdraw", json={
            "amount": 999999999.00,
            "method": "mobile_money"
        })
        assert response.status_code == 400
        data = response.json()
        assert "Insufficient" in data["detail"]
        print(f"Insufficient balance correctly rejected: {data['detail']}")
    
    def test_get_transactions(self, auth_session):
        """Test transactions list endpoint"""
        response = auth_session.get(f"{BASE_URL}/api/wallet/transactions")
        assert response.status_code == 200, f"Get transactions failed: {response.text}"
        data = response.json()
        assert "transactions" in data
        assert "total" in data
        assert isinstance(data["transactions"], list)
        print(f"Transactions: {data['total']} total")


class TestSendMoney:
    """Test sending money between users"""
    
    def test_send_money_between_users(self):
        """Test sending money from admin to test user"""
        # Login as admin
        admin_session = requests.Session()
        admin_resp = admin_session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert admin_resp.status_code == 200
        admin_data = admin_resp.json()
        
        # Login/register test user to get their user_id
        user_session = requests.Session()
        user_resp = user_session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        })
        if user_resp.status_code != 200:
            user_resp = user_session.post(f"{BASE_URL}/api/auth/register", json={
                "email": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD,
                "first_name": "Test",
                "last_name": "User"
            })
        assert user_resp.status_code == 200
        user_data = user_resp.json()
        test_user_id = user_data["user"]["user_id"]
        
        # Deposit money to admin wallet first
        admin_session.post(f"{BASE_URL}/api/wallet/deposit", json={
            "amount": 5000.00,
            "method": "bank_transfer"
        })
        
        # Send money from admin to test user
        response = admin_session.post(f"{BASE_URL}/api/wallet/send", json={
            "to_user_id": test_user_id,
            "amount": 100.00,
            "notes": "Test transfer"
        })
        assert response.status_code == 200, f"Send money failed: {response.text}"
        data = response.json()
        assert "tx_id" in data
        assert "new_balance" in data
        print(f"Money sent: {data['tx_id']}")
    
    def test_send_money_to_self_fails(self):
        """Test that sending money to yourself fails"""
        session = requests.Session()
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
        user_id = login_resp.json()["user"]["user_id"]
        
        response = session.post(f"{BASE_URL}/api/wallet/send", json={
            "to_user_id": user_id,
            "amount": 100.00
        })
        assert response.status_code == 400
        assert "yourself" in response.json()["detail"].lower()
        print("Send to self correctly rejected")


class TestMessagingEndpoints:
    """Messaging endpoint tests"""
    
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
    
    def test_get_conversations(self, auth_session):
        """Test conversations list endpoint"""
        response = auth_session.get(f"{BASE_URL}/api/messages/conversations")
        assert response.status_code == 200, f"Get conversations failed: {response.text}"
        data = response.json()
        assert isinstance(data, list)
        print(f"Conversations: {len(data)} found")
    
    def test_send_message(self, auth_session):
        """Test sending a message to another user"""
        # Get test user id
        user_session = requests.Session()
        user_resp = user_session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        })
        if user_resp.status_code != 200:
            user_resp = user_session.post(f"{BASE_URL}/api/auth/register", json={
                "email": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD,
                "first_name": "Test",
                "last_name": "User"
            })
        assert user_resp.status_code == 200
        test_user_id = user_resp.json()["user"]["user_id"]
        
        # Send message
        response = auth_session.post(f"{BASE_URL}/api/messages/send", json={
            "to_user_id": test_user_id,
            "text": "Hello from test!"
        })
        assert response.status_code == 200, f"Send message failed: {response.text}"
        data = response.json()
        assert "message" in data
        assert "conv_id" in data
        assert data["message"]["text"] == "Hello from test!"
        print(f"Message sent, conv_id: {data['conv_id']}")


class TestUserEndpoints:
    """User endpoint tests"""
    
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
    
    def test_search_users(self, auth_session):
        """Test user search endpoint"""
        response = auth_session.get(f"{BASE_URL}/api/users/search?q=test")
        assert response.status_code == 200, f"Search users failed: {response.text}"
        data = response.json()
        assert isinstance(data, list)
        print(f"Search results: {len(data)} users found")
    
    def test_search_users_requires_query(self, auth_session):
        """Test that search requires a query parameter"""
        response = auth_session.get(f"{BASE_URL}/api/users/search")
        assert response.status_code == 422  # Validation error
        print("Search without query correctly rejected")


class TestAdminEndpoints:
    """Admin endpoint tests"""
    
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
    def user_session(self):
        """Get authenticated regular user session"""
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
                "last_name": "User"
            })
        assert response.status_code == 200
        return session
    
    def test_admin_stats(self, admin_session):
        """Test admin stats endpoint"""
        response = admin_session.get(f"{BASE_URL}/api/admin/stats")
        assert response.status_code == 200, f"Admin stats failed: {response.text}"
        data = response.json()
        assert "total_users" in data
        assert "total_transactions" in data
        assert "total_messages" in data
        assert "total_balance" in data
        print(f"Admin stats: {data['total_users']} users, {data['total_transactions']} transactions")
    
    def test_admin_list_users(self, admin_session):
        """Test admin list users endpoint"""
        response = admin_session.get(f"{BASE_URL}/api/admin/users")
        assert response.status_code == 200, f"Admin list users failed: {response.text}"
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert isinstance(data["users"], list)
        print(f"Admin users: {data['total']} total")
    
    def test_admin_list_transactions(self, admin_session):
        """Test admin list transactions endpoint"""
        response = admin_session.get(f"{BASE_URL}/api/admin/transactions")
        assert response.status_code == 200, f"Admin list transactions failed: {response.text}"
        data = response.json()
        assert "transactions" in data
        assert "total" in data
        print(f"Admin transactions: {data['total']} total")
    
    def test_admin_audit_logs(self, admin_session):
        """Test admin audit logs endpoint"""
        response = admin_session.get(f"{BASE_URL}/api/admin/audit-logs")
        assert response.status_code == 200, f"Admin audit logs failed: {response.text}"
        data = response.json()
        assert "logs" in data
        assert "total" in data
        print(f"Audit logs: {data['total']} total")
    
    def test_admin_endpoints_require_admin_role(self, user_session):
        """Test that admin endpoints reject non-admin users"""
        response = user_session.get(f"{BASE_URL}/api/admin/stats")
        assert response.status_code == 403
        print("Admin endpoint correctly rejected non-admin user")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
