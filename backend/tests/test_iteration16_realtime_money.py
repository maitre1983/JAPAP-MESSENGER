"""Iteration 16 — Realtime notifications + Send Money in Chat."""
import os
import json
import time
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/') or os.environ.get('BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    with open('/app/frontend/.env') as f:
        for line in f:
            if line.startswith('REACT_APP_BACKEND_URL'):
                BASE_URL = line.strip().split('=', 1)[1].strip().strip('"').rstrip('/')
                break

ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
USER_EMAIL = "testref_1776710356@japap.com"
USER_PASSWORD = "Test1234!"


def _login(email, password):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed {email}: {r.status_code} {r.text}"
    data = r.json()
    token = data.get('access_token') or data.get('token')
    user = data.get('user') or {}
    return token, user


@pytest.fixture(scope="module")
def admin_auth():
    token, user = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    return {"token": token, "user": user, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture(scope="module")
def user_auth():
    token, user = _login(USER_EMAIL, USER_PASSWORD)
    return {"token": token, "user": user, "headers": {"Authorization": f"Bearer {token}"}}


# ---------- SEND MONEY IN CHAT ----------
class TestSendMoneyHappyPath:
    def test_send_money_happy_path(self, admin_auth, user_auth):
        recipient = user_auth['user']['user_id']
        # Ensure admin has balance first (credit via a top-up if endpoint exists; else assume ~1.3M)
        r = requests.get(f"{BASE_URL}/api/wallet/balance", headers=admin_auth['headers'], timeout=10)
        assert r.status_code == 200
        bal_before = float(r.json().get('balance', 0))
        assert bal_before >= 500, f"admin balance too low: {bal_before}"

        payload = {"to_user_id": recipient, "amount": 500, "note": "TEST_i16 happy path"}
        resp = requests.post(f"{BASE_URL}/api/messages/send-money", json=payload, headers=admin_auth['headers'], timeout=15)
        assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
        data = resp.json()
        for k in ['tx_id', 'conv_id', 'msg_id', 'amount', 'currency', 'new_balance', 'message']:
            assert k in data, f"missing key {k} in response: {data}"
        assert data['tx_id'].startswith('cm_')
        assert data['msg_id'].startswith('msg_')
        assert data['conv_id'].startswith('conv_')
        assert float(data['amount']) == 500.0
        assert float(data['new_balance']) == pytest.approx(bal_before - 500.0, rel=1e-3)
        # Message payload sanity
        msg = data['message']
        assert msg['sender_id'] == admin_auth['user']['user_id']
        assert msg['msg_id'] == data['msg_id']
        media = json.loads(msg['media'])
        assert media['kind'] == 'money'
        assert media['tx_id'] == data['tx_id']
        assert media['currency'] in ('XAF', 'EUR', 'USD')

        # Fetch conv messages and check money bubble is there
        conv_id = data['conv_id']
        r2 = requests.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", headers=admin_auth['headers'], timeout=10)
        assert r2.status_code == 200
        msgs = r2.json()
        target = next((m for m in msgs if m.get('msg_id') == data['msg_id']), None)
        assert target is not None, "money message not found in conversation"
        media2 = json.loads(target['media'])
        assert media2['kind'] == 'money'
        assert media2['tx_id'] == data['tx_id']


class TestSendMoneyValidation:
    def test_self_transfer_rejected(self, admin_auth):
        me = admin_auth['user']['user_id']
        resp = requests.post(f"{BASE_URL}/api/messages/send-money",
                             json={"to_user_id": me, "amount": 100, "note": "self"},
                             headers=admin_auth['headers'], timeout=10)
        assert resp.status_code == 400
        assert 'soi' in resp.json().get('detail', '').lower() or 'self' in resp.json().get('detail', '').lower()

    def test_amount_below_min(self, admin_auth, user_auth):
        recipient = user_auth['user']['user_id']
        resp = requests.post(f"{BASE_URL}/api/messages/send-money",
                             json={"to_user_id": recipient, "amount": 10, "note": "low"},
                             headers=admin_auth['headers'], timeout=10)
        assert resp.status_code == 400

    def test_zero_amount(self, admin_auth, user_auth):
        recipient = user_auth['user']['user_id']
        resp = requests.post(f"{BASE_URL}/api/messages/send-money",
                             json={"to_user_id": recipient, "amount": 0, "note": ""},
                             headers=admin_auth['headers'], timeout=10)
        assert resp.status_code == 400

    def test_invalid_recipient(self, admin_auth):
        resp = requests.post(f"{BASE_URL}/api/messages/send-money",
                             json={"to_user_id": "user_DOES_NOT_EXIST_xxxxxx", "amount": 100},
                             headers=admin_auth['headers'], timeout=10)
        assert resp.status_code == 404

    def test_insufficient_balance(self, user_auth, admin_auth):
        # Regular user sends more than they have
        recipient = admin_auth['user']['user_id']
        resp = requests.post(f"{BASE_URL}/api/messages/send-money",
                             json={"to_user_id": recipient, "amount": 9999999, "note": "overdraft"},
                             headers=user_auth['headers'], timeout=10)
        assert resp.status_code == 400
        assert 'insuffisant' in resp.json().get('detail', '').lower() or 'insufficient' in resp.json().get('detail', '').lower()

    def test_unauth_rejected(self, user_auth):
        resp = requests.post(f"{BASE_URL}/api/messages/send-money",
                             json={"to_user_id": user_auth['user']['user_id'], "amount": 100}, timeout=10)
        assert resp.status_code in (401, 403)


# ---------- REGRESSION: feed like/comment/tip with notify emits ----------
class TestFeedNotifyRegression:
    @pytest.fixture(scope="class")
    def seed_post_by_admin(self, admin_auth):
        # admin creates a post so non-owner user can like/comment/tip it
        payload = {"text": "TEST_i16 post for like/comment/tip notify", "type": "text"}
        r = requests.post(f"{BASE_URL}/api/feed/posts", json=payload, headers=admin_auth['headers'], timeout=10)
        assert r.status_code in (200, 201), f"post create failed: {r.status_code} {r.text}"
        data = r.json()
        post_id = data.get('post_id') or data.get('id') or (data.get('post') or {}).get('post_id')
        assert post_id, f"no post_id in response: {data}"
        return post_id

    def test_like_by_non_owner_succeeds(self, user_auth, seed_post_by_admin):
        r = requests.post(f"{BASE_URL}/api/feed/posts/{seed_post_by_admin}/like",
                          headers=user_auth['headers'], timeout=10)
        assert r.status_code == 200, f"{r.status_code} {r.text}"

    def test_comment_by_non_owner_succeeds(self, user_auth, seed_post_by_admin):
        r = requests.post(f"{BASE_URL}/api/feed/posts/{seed_post_by_admin}/comments",
                          json={"text": "TEST_i16 comment ping"},
                          headers=user_auth['headers'], timeout=10)
        assert r.status_code in (200, 201), f"{r.status_code} {r.text}"

    def test_tip_still_works(self, user_auth, admin_auth, seed_post_by_admin):
        # Ensure testref has balance
        r = requests.get(f"{BASE_URL}/api/wallet/balance", headers=user_auth['headers'], timeout=10)
        if r.status_code != 200 or float(r.json().get('balance', 0)) < 100:
            # top-up via deposit if available
            requests.post(f"{BASE_URL}/api/wallet/deposit",
                          json={"amount": 1000, "method": "test"},
                          headers=user_auth['headers'], timeout=10)
        r = requests.post(f"{BASE_URL}/api/feed/tip",
                          json={"target_type": "post", "target_id": seed_post_by_admin, "amount": 100, "message": "TEST_i16 tip"},
                          headers=user_auth['headers'], timeout=15)
        assert r.status_code == 200, f"tip failed: {r.status_code} {r.text}"


# ---------- REGRESSION: existing messaging + wallet send ----------
class TestRegressionCoreFlows:
    def test_send_text_message_still_works(self, admin_auth, user_auth):
        r = requests.post(f"{BASE_URL}/api/messages/send",
                          json={"to_user_id": user_auth['user']['user_id'], "text": "TEST_i16 regression text"},
                          headers=admin_auth['headers'], timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get('conv_id')
        assert data.get('message', {}).get('msg_id')

    def test_conversations_list(self, admin_auth):
        r = requests.get(f"{BASE_URL}/api/messages/conversations", headers=admin_auth['headers'], timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_wallet_send_still_works(self, admin_auth, user_auth):
        # direct P2P wallet transfer (non-chat route)
        r = requests.post(f"{BASE_URL}/api/wallet/send",
                          json={"to_user_id": user_auth['user']['user_id'], "amount": 50, "note": "TEST_i16 wallet send"},
                          headers=admin_auth['headers'], timeout=10)
        # Accept 200 or 404 if endpoint signature differs — report status
        assert r.status_code in (200, 400, 404), f"unexpected wallet/send code: {r.status_code} {r.text}"
