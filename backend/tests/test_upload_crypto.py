"""
JAPAP Messenger - Upload & Crypto API Tests
Tests for: File Upload API, Crypto Buy/Sell/Stake/Unstake APIs
Iteration 6 - New features testing
"""
import pytest
import requests
import os
import io
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
TEST_USER_EMAIL = "testuser@japap.com"
TEST_USER_PASSWORD = "TestUser2024!"


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
def user_session():
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
    assert response.status_code == 200, f"User auth failed: {response.text}"
    return session


# ============ UPLOAD API TESTS ============

class TestUploadAPI:
    """File Upload API tests - POST /api/upload/, GET /api/upload/files/{filename}"""
    
    def test_upload_image_file(self, admin_session):
        """Test uploading an image file"""
        # Create a simple PNG file in memory
        png_header = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        
        files = {'file': ('test_image.png', io.BytesIO(png_header), 'image/png')}
        response = admin_session.post(f"{BASE_URL}/api/upload/", files=files)
        
        assert response.status_code == 200, f"Upload failed: {response.text}"
        data = response.json()
        assert "file_id" in data
        assert "filename" in data
        assert "url" in data
        assert data["url"].startswith("/api/upload/files/")
        assert data["original_name"] == "test_image.png"
        print(f"Image uploaded: {data['filename']}, URL: {data['url']}")
        return data
    
    def test_upload_pdf_file(self, admin_session):
        """Test uploading a PDF file"""
        # Minimal PDF content
        pdf_content = b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF'
        
        files = {'file': ('test_doc.pdf', io.BytesIO(pdf_content), 'application/pdf')}
        response = admin_session.post(f"{BASE_URL}/api/upload/", files=files)
        
        assert response.status_code == 200, f"PDF upload failed: {response.text}"
        data = response.json()
        assert data["original_name"] == "test_doc.pdf"
        print(f"PDF uploaded: {data['filename']}")
    
    def test_upload_invalid_file_type(self, admin_session):
        """Test uploading an invalid file type (should be rejected)"""
        files = {'file': ('test.exe', io.BytesIO(b'MZ\x90\x00'), 'application/x-msdownload')}
        response = admin_session.post(f"{BASE_URL}/api/upload/", files=files)
        
        assert response.status_code == 400, f"Invalid file type should be rejected: {response.text}"
        data = response.json()
        assert "not allowed" in data["detail"].lower()
        print(f"Invalid file type correctly rejected: {data['detail']}")
    
    def test_serve_uploaded_file(self, admin_session):
        """Test serving an uploaded file"""
        # First upload a file
        png_header = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        files = {'file': ('serve_test.png', io.BytesIO(png_header), 'image/png')}
        upload_resp = admin_session.post(f"{BASE_URL}/api/upload/", files=files)
        assert upload_resp.status_code == 200
        upload_data = upload_resp.json()
        
        # Now serve the file (no auth required for serving)
        serve_resp = requests.get(f"{BASE_URL}{upload_data['url']}")
        assert serve_resp.status_code == 200, f"Serve file failed: {serve_resp.text}"
        assert len(serve_resp.content) > 0
        print(f"File served successfully: {len(serve_resp.content)} bytes")
    
    def test_serve_nonexistent_file(self):
        """Test serving a file that doesn't exist"""
        response = requests.get(f"{BASE_URL}/api/upload/files/nonexistent_file_12345.png")
        assert response.status_code == 404
        print("Nonexistent file correctly returns 404")
    
    def test_upload_requires_auth(self):
        """Test that upload requires authentication"""
        files = {'file': ('test.png', io.BytesIO(b'test'), 'image/png')}
        response = requests.post(f"{BASE_URL}/api/upload/", files=files)
        assert response.status_code == 401
        print("Upload correctly requires authentication")
    
    def test_upload_multiple_files(self, admin_session):
        """Test uploading multiple files"""
        png_header = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        
        files = [
            ('files', ('multi1.png', io.BytesIO(png_header), 'image/png')),
            ('files', ('multi2.png', io.BytesIO(png_header), 'image/png')),
        ]
        response = admin_session.post(f"{BASE_URL}/api/upload/multiple", files=files)
        
        assert response.status_code == 200, f"Multiple upload failed: {response.text}"
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        print(f"Multiple files uploaded: {len(data)} files")


# ============ CRYPTO API TESTS ============

class TestCryptoMarketAPI:
    """Crypto Market API tests - GET /api/crypto/market"""
    
    def test_get_market_prices(self, admin_session):
        """Test getting crypto market prices - should return 8 coins"""
        response = admin_session.get(f"{BASE_URL}/api/crypto/market")
        
        assert response.status_code == 200, f"Get market failed: {response.text}"
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 8, f"Expected 8 coins, got {len(data)}"
        
        # Verify expected coins
        symbols = [coin["symbol"] for coin in data]
        expected_coins = ["BTC", "ETH", "BNB", "SOL", "USDT", "XRP", "ADA", "DOGE"]
        for coin in expected_coins:
            assert coin in symbols, f"Missing coin: {coin}"
        
        # Verify coin structure
        for coin in data:
            assert "symbol" in coin
            assert "name" in coin
            assert "price_xaf" in coin
            assert "change_24h" in coin
            assert "staking_apy" in coin
            assert coin["price_xaf"] > 0
        
        print(f"Market data: {len(data)} coins returned")
        for coin in data:
            print(f"  {coin['symbol']}: {coin['price_xaf']} XAF, APY: {coin['staking_apy']}%")
    
    def test_market_requires_auth(self):
        """Test that market endpoint requires authentication"""
        response = requests.get(f"{BASE_URL}/api/crypto/market")
        assert response.status_code == 401
        print("Market endpoint correctly requires authentication")


class TestCryptoPortfolioAPI:
    """Crypto Portfolio API tests - GET /api/crypto/portfolio"""
    
    def test_get_portfolio(self, admin_session):
        """Test getting user's crypto portfolio"""
        response = admin_session.get(f"{BASE_URL}/api/crypto/portfolio")
        
        assert response.status_code == 200, f"Get portfolio failed: {response.text}"
        data = response.json()
        assert "portfolio" in data
        assert "total_value_xaf" in data
        assert isinstance(data["portfolio"], list)
        
        print(f"Portfolio: {len(data['portfolio'])} holdings, total value: {data['total_value_xaf']} XAF")
        for holding in data["portfolio"]:
            print(f"  {holding['coin']}: balance={holding['balance']}, staked={holding['staked']}")


class TestCryptoBuyAPI:
    """Crypto Buy API tests - POST /api/crypto/buy"""
    
    def test_buy_crypto_success(self, admin_session):
        """Test buying crypto with XAF wallet balance"""
        # First deposit XAF to wallet
        deposit_resp = admin_session.post(f"{BASE_URL}/api/wallet/deposit", json={
            "amount": 100000.00,
            "method": "bank_transfer",
            "reference": "CRYPTO_TEST_DEP"
        })
        assert deposit_resp.status_code == 200, f"Deposit failed: {deposit_resp.text}"
        
        # Buy some DOGE (cheapest coin at 165 XAF)
        response = admin_session.post(f"{BASE_URL}/api/crypto/buy", json={
            "coin": "DOGE",
            "amount_fiat": 1000.00  # Buy 1000 XAF worth of DOGE
        })
        
        assert response.status_code == 200, f"Buy crypto failed: {response.text}"
        data = response.json()
        assert "tx_id" in data
        assert "coin" in data
        assert "amount" in data
        assert "spent_xaf" in data
        assert data["coin"] == "DOGE"
        assert float(data["spent_xaf"]) == 1000.00
        print(f"Bought {data['amount']} DOGE for {data['spent_xaf']} XAF, tx: {data['tx_id']}")
        return data
    
    def test_buy_crypto_insufficient_balance(self, user_session):
        """Test buying crypto with insufficient balance"""
        response = user_session.post(f"{BASE_URL}/api/crypto/buy", json={
            "coin": "BTC",
            "amount_fiat": 999999999.00
        })
        
        assert response.status_code == 400
        data = response.json()
        assert "insufficient" in data["detail"].lower()
        print(f"Insufficient balance correctly rejected: {data['detail']}")
    
    def test_buy_invalid_coin(self, admin_session):
        """Test buying an unsupported coin"""
        response = admin_session.post(f"{BASE_URL}/api/crypto/buy", json={
            "coin": "INVALID_COIN",
            "amount_fiat": 1000.00
        })
        
        assert response.status_code == 400
        data = response.json()
        assert "unsupported" in data["detail"].lower()
        print(f"Invalid coin correctly rejected: {data['detail']}")
    
    def test_buy_negative_amount(self, admin_session):
        """Test buying with negative amount"""
        response = admin_session.post(f"{BASE_URL}/api/crypto/buy", json={
            "coin": "BTC",
            "amount_fiat": -100.00
        })
        
        assert response.status_code == 400
        data = response.json()
        assert "positive" in data["detail"].lower()
        print(f"Negative amount correctly rejected: {data['detail']}")


class TestCryptoSellAPI:
    """Crypto Sell API tests - POST /api/crypto/sell"""
    
    def test_sell_crypto_success(self, admin_session):
        """Test selling crypto back to XAF"""
        # First ensure we have some crypto to sell
        admin_session.post(f"{BASE_URL}/api/wallet/deposit", json={
            "amount": 50000.00,
            "method": "bank_transfer"
        })
        admin_session.post(f"{BASE_URL}/api/crypto/buy", json={
            "coin": "DOGE",
            "amount_fiat": 5000.00
        })
        
        # Get portfolio to see how much DOGE we have
        portfolio_resp = admin_session.get(f"{BASE_URL}/api/crypto/portfolio")
        portfolio = portfolio_resp.json()["portfolio"]
        doge_holding = next((h for h in portfolio if h["coin"] == "DOGE"), None)
        
        if doge_holding and float(doge_holding["balance"]) > 0:
            sell_amount = min(float(doge_holding["balance"]), 1.0)  # Sell up to 1 DOGE
            
            response = admin_session.post(f"{BASE_URL}/api/crypto/sell", json={
                "coin": "DOGE",
                "amount_crypto": sell_amount
            })
            
            assert response.status_code == 200, f"Sell crypto failed: {response.text}"
            data = response.json()
            assert "tx_id" in data
            assert "received_xaf" in data
            print(f"Sold DOGE, received {data['received_xaf']} XAF, tx: {data['tx_id']}")
        else:
            pytest.skip("No DOGE balance to sell")
    
    def test_sell_insufficient_crypto_balance(self, admin_session):
        """Test selling more crypto than owned"""
        response = admin_session.post(f"{BASE_URL}/api/crypto/sell", json={
            "coin": "BTC",
            "amount_crypto": 999999.0
        })
        
        assert response.status_code == 400
        data = response.json()
        assert "insufficient" in data["detail"].lower()
        print(f"Insufficient crypto balance correctly rejected: {data['detail']}")


class TestCryptoStakingAPI:
    """Crypto Staking API tests - POST /api/crypto/stake, POST /api/crypto/unstake, GET /api/crypto/staking"""
    
    def test_stake_crypto_success(self, admin_session):
        """Test staking crypto"""
        # First ensure we have some crypto
        admin_session.post(f"{BASE_URL}/api/wallet/deposit", json={
            "amount": 50000.00,
            "method": "bank_transfer"
        })
        admin_session.post(f"{BASE_URL}/api/crypto/buy", json={
            "coin": "ADA",  # ADA has 20% APY
            "amount_fiat": 10000.00
        })
        
        # Get portfolio to see how much ADA we have
        portfolio_resp = admin_session.get(f"{BASE_URL}/api/crypto/portfolio")
        portfolio = portfolio_resp.json()["portfolio"]
        ada_holding = next((h for h in portfolio if h["coin"] == "ADA"), None)
        
        if ada_holding and float(ada_holding["balance"]) > 0:
            stake_amount = min(float(ada_holding["balance"]), 5.0)  # Stake up to 5 ADA
            
            response = admin_session.post(f"{BASE_URL}/api/crypto/stake", json={
                "coin": "ADA",
                "amount": stake_amount
            })
            
            assert response.status_code == 200, f"Stake crypto failed: {response.text}"
            data = response.json()
            assert "stake_id" in data
            assert "apy" in data
            print(f"Staked ADA at {data['apy']}% APY, stake_id: {data['stake_id']}")
            return data["stake_id"]
        else:
            pytest.skip("No ADA balance to stake")
    
    def test_get_staking_positions(self, admin_session):
        """Test getting staking positions"""
        response = admin_session.get(f"{BASE_URL}/api/crypto/staking")
        
        assert response.status_code == 200, f"Get staking failed: {response.text}"
        data = response.json()
        assert isinstance(data, list)
        
        print(f"Staking positions: {len(data)} found")
        for pos in data:
            print(f"  {pos['coin']}: {pos['amount']} staked at {pos['apy']}% APY, status: {pos['status']}")
    
    def test_unstake_crypto_success(self, admin_session):
        """Test unstaking crypto"""
        # Get active staking positions
        staking_resp = admin_session.get(f"{BASE_URL}/api/crypto/staking")
        positions = staking_resp.json()
        active_positions = [p for p in positions if p["status"] == "active"]
        
        if active_positions:
            stake_id = active_positions[0]["stake_id"]
            
            response = admin_session.post(f"{BASE_URL}/api/crypto/unstake", json={
                "stake_id": stake_id
            })
            
            assert response.status_code == 200, f"Unstake failed: {response.text}"
            data = response.json()
            assert "principal" in data
            assert "earned" in data
            assert "total" in data
            print(f"Unstaked: principal={data['principal']}, earned={data['earned']}, total={data['total']}")
        else:
            pytest.skip("No active staking positions to unstake")
    
    def test_unstake_invalid_position(self, admin_session):
        """Test unstaking with invalid stake_id"""
        response = admin_session.post(f"{BASE_URL}/api/crypto/unstake", json={
            "stake_id": "invalid_stake_id_12345"
        })
        
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()
        print(f"Invalid stake_id correctly rejected: {data['detail']}")
    
    def test_stake_insufficient_balance(self, admin_session):
        """Test staking more than available balance"""
        response = admin_session.post(f"{BASE_URL}/api/crypto/stake", json={
            "coin": "BTC",
            "amount": 999999.0
        })
        
        assert response.status_code == 400
        data = response.json()
        assert "insufficient" in data["detail"].lower()
        print(f"Insufficient balance for staking correctly rejected: {data['detail']}")


class TestCryptoTransactionsAPI:
    """Crypto Transactions API tests - GET /api/crypto/transactions"""
    
    def test_get_crypto_transactions(self, admin_session):
        """Test getting crypto transaction history"""
        response = admin_session.get(f"{BASE_URL}/api/crypto/transactions")
        
        assert response.status_code == 200, f"Get transactions failed: {response.text}"
        data = response.json()
        assert "transactions" in data
        assert "total" in data
        assert "page" in data
        assert isinstance(data["transactions"], list)
        
        print(f"Crypto transactions: {data['total']} total, page {data['page']}")
        for tx in data["transactions"][:5]:  # Show first 5
            print(f"  {tx['type']}: {tx['amount']} {tx['coin']} @ {tx['price_per_unit']} XAF")
    
    def test_get_crypto_transactions_pagination(self, admin_session):
        """Test crypto transactions pagination"""
        response = admin_session.get(f"{BASE_URL}/api/crypto/transactions?page=1&limit=5")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["transactions"]) <= 5
        print(f"Pagination working: {len(data['transactions'])} transactions on page 1")


# ============ INTEGRATION TESTS ============

class TestCryptoIntegration:
    """Integration tests for crypto flow"""
    
    def test_full_crypto_flow(self, admin_session):
        """Test complete crypto flow: deposit -> buy -> stake -> unstake -> sell"""
        print("\n=== FULL CRYPTO FLOW TEST ===")
        
        # 1. Deposit XAF
        deposit_resp = admin_session.post(f"{BASE_URL}/api/wallet/deposit", json={
            "amount": 100000.00,
            "method": "bank_transfer",
            "reference": "FULL_FLOW_TEST"
        })
        assert deposit_resp.status_code == 200
        print(f"1. Deposited 100,000 XAF")
        
        # 2. Buy crypto (DOGE - cheapest)
        buy_resp = admin_session.post(f"{BASE_URL}/api/crypto/buy", json={
            "coin": "DOGE",
            "amount_fiat": 10000.00
        })
        assert buy_resp.status_code == 200
        buy_data = buy_resp.json()
        doge_amount = float(buy_data["amount"])
        print(f"2. Bought {doge_amount} DOGE for 10,000 XAF")
        
        # 3. Check portfolio
        portfolio_resp = admin_session.get(f"{BASE_URL}/api/crypto/portfolio")
        assert portfolio_resp.status_code == 200
        portfolio = portfolio_resp.json()
        print(f"3. Portfolio total value: {portfolio['total_value_xaf']} XAF")
        
        # 4. Stake some DOGE
        stake_amount = min(doge_amount / 2, 10.0)  # Stake half or max 10
        stake_resp = admin_session.post(f"{BASE_URL}/api/crypto/stake", json={
            "coin": "DOGE",
            "amount": stake_amount
        })
        assert stake_resp.status_code == 200
        stake_data = stake_resp.json()
        stake_id = stake_data["stake_id"]
        print(f"4. Staked {stake_amount} DOGE at {stake_data['apy']}% APY")
        
        # 5. Check staking positions
        staking_resp = admin_session.get(f"{BASE_URL}/api/crypto/staking")
        assert staking_resp.status_code == 200
        positions = staking_resp.json()
        active_count = len([p for p in positions if p["status"] == "active"])
        print(f"5. Active staking positions: {active_count}")
        
        # 6. Unstake
        unstake_resp = admin_session.post(f"{BASE_URL}/api/crypto/unstake", json={
            "stake_id": stake_id
        })
        assert unstake_resp.status_code == 200
        unstake_data = unstake_resp.json()
        print(f"6. Unstaked: principal={unstake_data['principal']}, earned={unstake_data['earned']}")
        
        # 7. Sell some DOGE
        sell_resp = admin_session.post(f"{BASE_URL}/api/crypto/sell", json={
            "coin": "DOGE",
            "amount_crypto": 5.0
        })
        assert sell_resp.status_code == 200
        sell_data = sell_resp.json()
        print(f"7. Sold 5 DOGE, received {sell_data['received_xaf']} XAF")
        
        # 8. Check transactions
        tx_resp = admin_session.get(f"{BASE_URL}/api/crypto/transactions")
        assert tx_resp.status_code == 200
        tx_data = tx_resp.json()
        print(f"8. Total crypto transactions: {tx_data['total']}")
        
        print("=== FULL CRYPTO FLOW COMPLETED ===\n")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
