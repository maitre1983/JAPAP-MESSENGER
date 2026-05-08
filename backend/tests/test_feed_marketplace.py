"""
JAPAP Messenger - Feed & Marketplace API Tests (Iteration 5)
Tests for: Feed Posts (CRUD, likes, comments), Marketplace (products, orders, escrow, categories)
"""
import pytest
import requests
import os
import time
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
TEST_USER_EMAIL = "testuser@japap.com"
TEST_USER_PASSWORD = "TestUser2024!"
TEST_USER2_EMAIL = f"testuser2_{uuid.uuid4().hex[:6]}@japap.com"
TEST_USER2_PASSWORD = "TestUser2024!"


@pytest.fixture(scope="module")
def admin_session():
    """Get authenticated admin session"""
    session = requests.Session()
    response = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    assert response.status_code == 200, f"Admin login failed: {response.text}"
    return session


@pytest.fixture(scope="module")
def test_user_session():
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
            "last_name": "User"
        })
    assert response.status_code == 200, f"Test user auth failed: {response.text}"
    return session


@pytest.fixture(scope="module")
def test_user2_session():
    """Get authenticated second test user session for marketplace buyer tests"""
    session = requests.Session()
    response = session.post(f"{BASE_URL}/api/auth/register", json={
        "email": TEST_USER2_EMAIL,
        "password": TEST_USER2_PASSWORD,
        "first_name": "Buyer",
        "last_name": "Test"
    })
    if response.status_code == 400 and "already registered" in response.text.lower():
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_USER2_EMAIL,
            "password": TEST_USER2_PASSWORD
        })
    assert response.status_code == 200, f"Test user2 auth failed: {response.text}"
    return session


# ==================== FEED API TESTS ====================

class TestFeedPosts:
    """Feed Posts API tests - Create, List, Like, Comment"""
    
    def test_create_post(self, admin_session):
        """Test POST /api/feed/posts creates a post"""
        response = admin_session.post(f"{BASE_URL}/api/feed/posts", json={
            "text": f"Test post from iteration 5 - {uuid.uuid4().hex[:8]}",
            "media": []
        })
        assert response.status_code == 200, f"Create post failed: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "post_id" in data
        assert "text" in data
        assert "user_id" in data
        assert "first_name" in data
        assert "created_at" in data
        assert "likes_count" in data
        assert "comments_count" in data
        assert data["is_liked"] == False
        print(f"Post created: {data['post_id']}")
        return data["post_id"]
    
    def test_create_post_empty_text_fails(self, admin_session):
        """Test that empty post text is rejected"""
        response = admin_session.post(f"{BASE_URL}/api/feed/posts", json={
            "text": "   ",
            "media": []
        })
        assert response.status_code == 400
        assert "required" in response.json()["detail"].lower()
        print("Empty post text correctly rejected")
    
    def test_get_feed_posts(self, admin_session):
        """Test GET /api/feed/posts returns posts list"""
        response = admin_session.get(f"{BASE_URL}/api/feed/posts?page=1&limit=20")
        assert response.status_code == 200, f"Get feed failed: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "posts" in data
        assert "total" in data
        assert "page" in data
        assert isinstance(data["posts"], list)
        assert data["page"] == 1
        
        if len(data["posts"]) > 0:
            post = data["posts"][0]
            assert "post_id" in post
            assert "text" in post
            assert "first_name" in post
            assert "is_liked" in post
        
        print(f"Feed: {data['total']} total posts, {len(data['posts'])} returned")
    
    def test_toggle_like_post(self, admin_session):
        """Test POST /api/feed/posts/{id}/like toggles like"""
        # First create a post
        create_resp = admin_session.post(f"{BASE_URL}/api/feed/posts", json={
            "text": f"Like test post - {uuid.uuid4().hex[:8]}"
        })
        assert create_resp.status_code == 200
        post_id = create_resp.json()["post_id"]
        
        # Like the post
        like_resp = admin_session.post(f"{BASE_URL}/api/feed/posts/{post_id}/like")
        assert like_resp.status_code == 200, f"Like failed: {like_resp.text}"
        data = like_resp.json()
        assert data["liked"] == True
        print(f"Post {post_id} liked")
        
        # Unlike the post (toggle)
        unlike_resp = admin_session.post(f"{BASE_URL}/api/feed/posts/{post_id}/like")
        assert unlike_resp.status_code == 200
        data = unlike_resp.json()
        assert data["liked"] == False
        print(f"Post {post_id} unliked (toggle works)")
    
    def test_create_comment(self, admin_session):
        """Test POST /api/feed/posts/{id}/comments creates a comment"""
        # First create a post
        create_resp = admin_session.post(f"{BASE_URL}/api/feed/posts", json={
            "text": f"Comment test post - {uuid.uuid4().hex[:8]}"
        })
        assert create_resp.status_code == 200
        post_id = create_resp.json()["post_id"]
        
        # Add a comment
        comment_resp = admin_session.post(f"{BASE_URL}/api/feed/posts/{post_id}/comments", json={
            "text": "This is a test comment!"
        })
        assert comment_resp.status_code == 200, f"Comment failed: {comment_resp.text}"
        data = comment_resp.json()
        assert "comment_id" in data
        assert data["text"] == "This is a test comment!"
        print(f"Comment created: {data['comment_id']}")
    
    def test_get_comments(self, admin_session):
        """Test GET /api/feed/posts/{id}/comments returns comments"""
        # First create a post with a comment
        create_resp = admin_session.post(f"{BASE_URL}/api/feed/posts", json={
            "text": f"Get comments test - {uuid.uuid4().hex[:8]}"
        })
        post_id = create_resp.json()["post_id"]
        
        admin_session.post(f"{BASE_URL}/api/feed/posts/{post_id}/comments", json={
            "text": "Test comment for retrieval"
        })
        
        # Get comments
        response = admin_session.get(f"{BASE_URL}/api/feed/posts/{post_id}/comments")
        assert response.status_code == 200, f"Get comments failed: {response.text}"
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["text"] == "Test comment for retrieval"
        print(f"Retrieved {len(data)} comments")
    
    def test_feed_requires_auth(self):
        """Test that feed endpoints require authentication"""
        response = requests.get(f"{BASE_URL}/api/feed/posts")
        assert response.status_code == 401
        print("Feed correctly requires authentication")


# ==================== MARKETPLACE API TESTS ====================

class TestMarketplaceCategories:
    """Marketplace Categories API tests"""
    
    def test_get_categories(self, admin_session):
        """Test GET /api/marketplace/categories returns 9 categories"""
        response = admin_session.get(f"{BASE_URL}/api/marketplace/categories")
        assert response.status_code == 200, f"Get categories failed: {response.text}"
        data = response.json()
        
        assert isinstance(data, list)
        assert len(data) == 9, f"Expected 9 categories, got {len(data)}"
        
        # Verify category structure
        category_ids = [c["id"] for c in data]
        expected_ids = ["electronics", "clothing", "food", "home", "beauty", "sports", "vehicles", "services", "general"]
        for expected in expected_ids:
            assert expected in category_ids, f"Missing category: {expected}"
        
        print(f"Categories: {len(data)} returned - {category_ids}")


class TestMarketplaceProducts:
    """Marketplace Products API tests - CRUD operations"""
    
    def test_create_product(self, admin_session):
        """Test POST /api/marketplace/products creates a product"""
        response = admin_session.post(f"{BASE_URL}/api/marketplace/products", json={
            "title": f"Test Product {uuid.uuid4().hex[:8]}",
            "description": "A test product for iteration 5",
            "price": 5000.00,
            "category": "electronics",
            "condition": "new",
            "location": "Douala, Cameroon"
        })
        assert response.status_code == 200, f"Create product failed: {response.text}"
        data = response.json()
        
        assert "product_id" in data
        assert data["message"] == "Product created"
        print(f"Product created: {data['product_id']}")
        return data["product_id"]
    
    def test_create_product_invalid_price(self, admin_session):
        """Test that negative price is rejected"""
        response = admin_session.post(f"{BASE_URL}/api/marketplace/products", json={
            "title": "Invalid Product",
            "price": -100.00,
            "category": "general"
        })
        assert response.status_code == 400
        assert "positive" in response.json()["detail"].lower()
        print("Negative price correctly rejected")
    
    def test_list_products(self, admin_session):
        """Test GET /api/marketplace/products lists products"""
        response = admin_session.get(f"{BASE_URL}/api/marketplace/products?page=1&limit=20")
        assert response.status_code == 200, f"List products failed: {response.text}"
        data = response.json()
        
        assert "products" in data
        assert "total" in data
        assert "page" in data
        assert isinstance(data["products"], list)
        
        if len(data["products"]) > 0:
            product = data["products"][0]
            assert "product_id" in product
            assert "title" in product
            assert "price" in product
            assert "seller_id" in product
            assert "first_name" in product
        
        print(f"Products: {data['total']} total, {len(data['products'])} returned")
    
    def test_list_products_with_category_filter(self, admin_session):
        """Test product listing with category filter"""
        response = admin_session.get(f"{BASE_URL}/api/marketplace/products?category=electronics")
        assert response.status_code == 200
        data = response.json()
        
        # All returned products should be in electronics category
        for product in data["products"]:
            assert product["category"] == "electronics"
        
        print(f"Filtered by electronics: {len(data['products'])} products")
    
    def test_list_products_with_search(self, admin_session):
        """Test product listing with search query"""
        # First create a product with unique name
        unique_name = f"UniqueSearchTest{uuid.uuid4().hex[:8]}"
        admin_session.post(f"{BASE_URL}/api/marketplace/products", json={
            "title": unique_name,
            "price": 1000.00,
            "category": "general"
        })
        
        # Search for it
        response = admin_session.get(f"{BASE_URL}/api/marketplace/products?search={unique_name[:10]}")
        assert response.status_code == 200
        data = response.json()
        
        # Should find the product
        found = any(unique_name in p["title"] for p in data["products"])
        assert found, f"Search did not find product with name containing {unique_name[:10]}"
        print(f"Search found product: {unique_name}")
    
    def test_get_single_product(self, admin_session):
        """Test GET /api/marketplace/products/{id} returns product details"""
        # First create a product
        create_resp = admin_session.post(f"{BASE_URL}/api/marketplace/products", json={
            "title": f"Single Product Test {uuid.uuid4().hex[:8]}",
            "price": 2500.00,
            "category": "home"
        })
        product_id = create_resp.json()["product_id"]
        
        # Get the product
        response = admin_session.get(f"{BASE_URL}/api/marketplace/products/{product_id}")
        assert response.status_code == 200, f"Get product failed: {response.text}"
        data = response.json()
        
        assert data["product_id"] == product_id
        assert "title" in data
        assert "price" in data
        assert "views_count" in data
        print(f"Product retrieved: {data['title']}, views: {data['views_count']}")
    
    def test_update_product(self, admin_session):
        """Test PUT /api/marketplace/products/{id} updates product"""
        # First create a product
        create_resp = admin_session.post(f"{BASE_URL}/api/marketplace/products", json={
            "title": "Update Test Product",
            "price": 3000.00,
            "category": "general"
        })
        product_id = create_resp.json()["product_id"]
        
        # Update the product
        response = admin_session.put(f"{BASE_URL}/api/marketplace/products/{product_id}", json={
            "title": "Updated Product Title",
            "price": 3500.00
        })
        assert response.status_code == 200, f"Update product failed: {response.text}"
        
        # Verify update
        get_resp = admin_session.get(f"{BASE_URL}/api/marketplace/products/{product_id}")
        data = get_resp.json()
        assert data["title"] == "Updated Product Title"
        assert float(data["price"]) == 3500.00
        print(f"Product updated: {data['title']}, price: {data['price']}")
    
    def test_delete_product(self, admin_session):
        """Test DELETE /api/marketplace/products/{id} soft deletes product"""
        # First create a product
        create_resp = admin_session.post(f"{BASE_URL}/api/marketplace/products", json={
            "title": "Delete Test Product",
            "price": 1000.00,
            "category": "general"
        })
        product_id = create_resp.json()["product_id"]
        
        # Delete the product
        response = admin_session.delete(f"{BASE_URL}/api/marketplace/products/{product_id}")
        assert response.status_code == 200, f"Delete product failed: {response.text}"
        
        # Product should no longer appear in active listings
        list_resp = admin_session.get(f"{BASE_URL}/api/marketplace/products")
        products = list_resp.json()["products"]
        product_ids = [p["product_id"] for p in products]
        assert product_id not in product_ids, "Deleted product still appears in listings"
        print(f"Product deleted: {product_id}")


class TestMarketplaceOrders:
    """Marketplace Orders API tests - Escrow system with 5% commission"""
    
    def test_create_order_escrow(self, test_user_session, admin_session):
        """Test POST /api/marketplace/orders creates order with escrow"""
        # Admin creates a product
        create_resp = admin_session.post(f"{BASE_URL}/api/marketplace/products", json={
            "title": f"Order Test Product {uuid.uuid4().hex[:8]}",
            "price": 10000.00,
            "category": "electronics"
        })
        assert create_resp.status_code == 200
        product_id = create_resp.json()["product_id"]
        
        # Test user deposits money to wallet
        deposit_resp = test_user_session.post(f"{BASE_URL}/api/wallet/deposit", json={
            "amount": 50000.00,
            "method": "bank_transfer"
        })
        assert deposit_resp.status_code == 200
        
        # Get test user's balance before purchase
        balance_before = test_user_session.get(f"{BASE_URL}/api/wallet/balance").json()["balance"]
        
        # Test user buys the product
        order_resp = test_user_session.post(f"{BASE_URL}/api/marketplace/orders", json={
            "product_id": product_id,
            "notes": "Test order"
        })
        assert order_resp.status_code == 200, f"Create order failed: {order_resp.text}"
        data = order_resp.json()
        
        assert "order_id" in data
        assert "tx_id" in data
        assert "escrow" in data["message"].lower()
        
        # Verify balance was deducted
        balance_after = test_user_session.get(f"{BASE_URL}/api/wallet/balance").json()["balance"]
        assert float(balance_after) < float(balance_before), "Balance should be deducted"
        
        print(f"Order created: {data['order_id']}, funds in escrow")
        return data["order_id"], product_id
    
    def test_cannot_buy_own_product(self, admin_session):
        """Test that user cannot buy their own product"""
        # Admin creates a product
        create_resp = admin_session.post(f"{BASE_URL}/api/marketplace/products", json={
            "title": "Self-buy Test Product",
            "price": 1000.00,
            "category": "general"
        })
        product_id = create_resp.json()["product_id"]
        
        # Admin tries to buy their own product
        order_resp = admin_session.post(f"{BASE_URL}/api/marketplace/orders", json={
            "product_id": product_id
        })
        assert order_resp.status_code == 400
        assert "own product" in order_resp.json()["detail"].lower()
        print("Cannot buy own product - correctly rejected")
    
    def test_insufficient_balance_order(self, test_user2_session, admin_session):
        """Test order fails with insufficient balance"""
        # Admin creates expensive product
        create_resp = admin_session.post(f"{BASE_URL}/api/marketplace/products", json={
            "title": "Expensive Product",
            "price": 999999999.00,
            "category": "vehicles"
        })
        product_id = create_resp.json()["product_id"]
        
        # Test user2 tries to buy without enough balance
        order_resp = test_user2_session.post(f"{BASE_URL}/api/marketplace/orders", json={
            "product_id": product_id
        })
        assert order_resp.status_code == 400
        assert "insufficient" in order_resp.json()["detail"].lower()
        print("Insufficient balance correctly rejected")
    
    def test_list_orders_buyer(self, test_user_session):
        """Test GET /api/marketplace/orders?role=buyer lists buyer orders"""
        response = test_user_session.get(f"{BASE_URL}/api/marketplace/orders?role=buyer")
        assert response.status_code == 200, f"List buyer orders failed: {response.text}"
        data = response.json()
        
        assert isinstance(data, list)
        if len(data) > 0:
            order = data[0]
            assert "order_id" in order
            assert "product_title" in order
            assert "amount" in order
            assert "status" in order
        
        print(f"Buyer orders: {len(data)} found")
    
    def test_list_orders_seller(self, admin_session):
        """Test GET /api/marketplace/orders?role=seller lists seller orders"""
        response = admin_session.get(f"{BASE_URL}/api/marketplace/orders?role=seller")
        assert response.status_code == 200, f"List seller orders failed: {response.text}"
        data = response.json()
        
        assert isinstance(data, list)
        print(f"Seller orders: {len(data)} found")
    
    def test_confirm_order_releases_payment(self, test_user_session, admin_session):
        """Test PUT /api/marketplace/orders/{id}/confirm releases payment to seller"""
        # Admin creates a product
        create_resp = admin_session.post(f"{BASE_URL}/api/marketplace/products", json={
            "title": f"Confirm Test Product {uuid.uuid4().hex[:8]}",
            "price": 5000.00,
            "category": "electronics"
        })
        product_id = create_resp.json()["product_id"]
        
        # Test user deposits and buys
        test_user_session.post(f"{BASE_URL}/api/wallet/deposit", json={
            "amount": 10000.00,
            "method": "bank_transfer"
        })
        
        order_resp = test_user_session.post(f"{BASE_URL}/api/marketplace/orders", json={
            "product_id": product_id
        })
        assert order_resp.status_code == 200
        order_id = order_resp.json()["order_id"]
        
        # Get admin balance before confirmation
        admin_balance_before = admin_session.get(f"{BASE_URL}/api/wallet/balance").json()["balance"]
        
        # Test user confirms the order
        confirm_resp = test_user_session.put(f"{BASE_URL}/api/marketplace/orders/{order_id}/confirm")
        assert confirm_resp.status_code == 200, f"Confirm order failed: {confirm_resp.text}"
        data = confirm_resp.json()
        assert "released" in data["message"].lower() or "confirmed" in data["message"].lower()
        
        # Verify admin (seller) received payment minus 5% fee
        admin_balance_after = admin_session.get(f"{BASE_URL}/api/wallet/balance").json()["balance"]
        expected_payment = 5000.00 * 0.95  # 5% commission
        balance_increase = float(admin_balance_after) - float(admin_balance_before)
        
        # Allow small floating point tolerance
        assert abs(balance_increase - expected_payment) < 1, f"Expected ~{expected_payment}, got {balance_increase}"
        
        print(f"Order confirmed, seller received {balance_increase} XAF (5% commission deducted)")
    
    def test_confirm_order_not_found(self, test_user_session):
        """Test confirming non-existent order fails"""
        response = test_user_session.put(f"{BASE_URL}/api/marketplace/orders/fake_order_123/confirm")
        assert response.status_code == 404
        print("Confirm non-existent order correctly rejected")


class TestMarketplaceAuth:
    """Test marketplace endpoints require authentication"""
    
    def test_products_require_auth(self):
        """Test that product endpoints require authentication"""
        response = requests.get(f"{BASE_URL}/api/marketplace/products")
        assert response.status_code == 401
        print("Products endpoint correctly requires auth")
    
    def test_categories_require_auth(self):
        """Test that categories endpoint requires authentication"""
        response = requests.get(f"{BASE_URL}/api/marketplace/categories")
        assert response.status_code == 401
        print("Categories endpoint correctly requires auth")
    
    def test_orders_require_auth(self):
        """Test that orders endpoint requires authentication"""
        response = requests.get(f"{BASE_URL}/api/marketplace/orders")
        assert response.status_code == 401
        print("Orders endpoint correctly requires auth")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
