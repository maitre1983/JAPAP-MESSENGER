"""
iter231 backend tests — covers the critical hotfix surfaces:
  P0  GET /api/quiz/champion/challenges/{cid} returns can_play=true and
      status='awaiting_acceptor' immediately after POST /challenge/open
      for the challenger (was the root cause of the blank page).
  P1  POST /challenge/open with paid stake when balance is insufficient
      now returns HTTP 402 with the explicit GAP message (iter231).
  P2  Full P2P paid E2E: Alice opens 1 USD open challenge, plays/submits,
      Bob claims via the public link helper, plays/submits, status
      reaches 'completed' and both wallets show lock + release/refund
      transactions referencing the cid.

Login uses the captcha bypass (id=JAPAP_E2E_BYPASS_2026, answer=0).
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

import pytest
import requests

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://japap-refactor.preview.emergentagent.com",
).rstrip("/")
CAPTCHA = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!", **CAPTCHA}
BOB = {"email": "bob@japap.com", "password": "Test1234!", **CAPTCHA}
CHARLIE = {"email": "charlie_iter141@japap.com", "password": "Charlie2026!", **CAPTCHA}


def _login(creds):
    s = requests.Session()
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    last = None
    for _ in range(3):
        try:
            r = s.post(f"{BASE}/api/auth/login", json=creds, timeout=60)
            if r.status_code == 200:
                me = s.get(f"{BASE}/api/auth/me", timeout=30)
                if me.status_code == 200:
                    s._user = me.json()  # type: ignore[attr-defined]
                return s
            last = (r.status_code, r.text[:300])
        except Exception as e:
            last = ("exc", str(e))
        time.sleep(3)
    pytest.skip(f"Login failed for {creds['email']}: {last}")


@pytest.fixture(scope="module")
def alice():
    return _login(ALICE)


@pytest.fixture(scope="module")
def bob():
    return _login(BOB)


@pytest.fixture(scope="module")
def charlie():
    return _login(CHARLIE)


def _balance(sess) -> Decimal:
    r = sess.get(f"{BASE}/api/wallet/balance", timeout=30)
    assert r.status_code == 200, r.text[:200]
    j = r.json()
    return Decimal(str(j.get("balance_usd") or j.get("balance") or "0"))


def _all_transactions(sess, limit_pages: int = 8):
    out = []
    for p in range(1, limit_pages + 1):
        r = sess.get(f"{BASE}/api/wallet/transactions?page={p}&limit=20", timeout=30)
        if r.status_code != 200:
            break
        j = r.json()
        out.extend(j.get("transactions") or [])
        if len(out) >= (j.get("total") or 0):
            break
    return out


# ───────────────────────────── P0 ─────────────────────────────
def test_p0_open_challenge_free_can_play_after_create(alice):
    """iter231 P0 — After /challenge/open in free mode, the GET endpoint
    must return status='awaiting_acceptor' AND can_play=true so the
    detail page is no longer blank for the challenger."""
    payload = {"mode": "free", "country_code": "CM"}
    r = alice.post(f"{BASE}/api/quiz/champion/challenge/open", json=payload, timeout=60)
    assert r.status_code == 200, f"open free failed: {r.status_code} {r.text[:300]}"
    j = r.json()
    cid = j.get("challenge_id")
    assert cid and cid.startswith("qcc_"), f"missing/bad challenge_id in response: {j}"

    # Immediately read it back as the challenger — the iter231 fix.
    r = alice.get(f"{BASE}/api/quiz/champion/challenges/{cid}", timeout=30)
    assert r.status_code == 200, f"get failed: {r.status_code} {r.text[:300]}"
    detail = r.json()
    assert detail["status"] == "awaiting_acceptor", (
        f"expected awaiting_acceptor, got {detail.get('status')}"
    )
    assert detail.get("can_play") is True, (
        f"can_play must be True for challenger right after open. detail={detail}"
    )
    print(f"[P0] cid={cid} status={detail['status']} can_play={detail['can_play']}")


# ───────────────────────────── P1 ─────────────────────────────
def test_p1_paid_open_charlie_returns_402_with_gap(charlie):
    """iter231 P1 — Charlie has ~0 USD; opening a paid 200 USD challenge
    must yield HTTP 402 with a French detail mentioning the explicit GAP
    (the iter231 enhancement to lock_stake)."""
    payload = {"mode": "paid", "stake_amount": 200, "country_code": "CM"}
    r = charlie.post(f"{BASE}/api/quiz/champion/challenge/open", json=payload, timeout=60)
    if r.status_code == 403 and "désactivé" in r.text:
        pytest.skip("Paid challenges disabled in admin settings.")
    assert r.status_code == 402, f"expected 402, got {r.status_code} body={r.text[:300]}"
    detail = ""
    try:
        detail = r.json().get("detail", "")
    except Exception:
        detail = r.text
    assert "Solde insuffisant" in detail, f"missing 'Solde insuffisant' in: {detail!r}"
    assert "manque" in detail, f"missing 'manque' (gap label) in: {detail!r}"
    assert "200" in detail, f"missing requested amount 200 in: {detail!r}"
    print(f"[P1] 402 detail={detail!r}")


# ───────────────────────── Full E2E P2P ───────────────────────
@pytest.fixture(scope="module")
def e2e(alice, bob):
    state: dict = {}
    a_before = _balance(alice)
    b_before = _balance(bob)
    state["a_before"] = a_before
    state["b_before"] = b_before

    if a_before < Decimal("1"):
        pytest.skip(f"Alice balance {a_before} < 1 USD — cannot run paid E2E.")
    if b_before < Decimal("1"):
        pytest.skip(f"Bob balance {b_before} < 1 USD — cannot run paid E2E.")

    payload = {"mode": "paid", "stake_amount": 1, "country_code": "CM"}
    r = alice.post(f"{BASE}/api/quiz/champion/challenge/open", json=payload, timeout=120)
    if r.status_code == 403 and "désactivé" in r.text:
        pytest.skip("Paid challenges disabled in admin settings.")
    assert r.status_code == 200, f"open paid failed: {r.status_code} {r.text[:300]}"
    j = r.json()
    cid = j["challenge_id"]
    state["cid"] = cid
    state["stake_amount"] = Decimal(str(j["stake_amount"]))
    print(f"[E2E] cid={cid} stake={j['stake_amount']} {j['stake_currency']}")

    # iter231 critical: challenger should see can_play=true straight away.
    r = alice.get(f"{BASE}/api/quiz/champion/challenges/{cid}", timeout=30)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    state["initial_status"] = d.get("status")
    state["initial_can_play"] = d.get("can_play")
    assert d.get("status") == "awaiting_acceptor"
    assert d.get("can_play") is True

    # Alice plays + submits
    r = alice.post(f"{BASE}/api/quiz/champion/challenge/{cid}/play", timeout=60)
    assert r.status_code == 200, r.text[:300]
    r = alice.post(
        f"{BASE}/api/quiz/champion/challenge/{cid}/submit",
        json={"answers": [0, 0, 0, 0, 0]},
        timeout=60,
    )
    assert r.status_code == 200, r.text[:300]
    assert r.json().get("resolved") is None

    # public landing
    pub = requests.get(f"{BASE}/api/quiz/champion/challenge/public/{cid}", timeout=30)
    assert pub.status_code == 200, pub.text[:200]
    assert pub.json().get("challenge_id") == cid

    # Bob claims + plays + submits → resolution
    r = bob.post(f"{BASE}/api/quiz/champion/challenge/{cid}/claim", timeout=60)
    assert r.status_code == 200, f"bob claim: {r.status_code} {r.text[:300]}"
    r = bob.post(f"{BASE}/api/quiz/champion/challenge/{cid}/play", timeout=60)
    assert r.status_code == 200, r.text[:300]
    r = bob.post(
        f"{BASE}/api/quiz/champion/challenge/{cid}/submit",
        json={"answers": [1, 1, 1, 1, 1]},
        timeout=60,
    )
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    state["resolved"] = j.get("resolved")
    assert state["resolved"] is not None, "must auto-resolve after both submit"
    return state


def test_e2e_resolution_payload(e2e):
    res = e2e["resolved"]
    assert "challenger_score" in res and "champion_score" in res
    print(
        f"[E2E] A={res['challenger_score']} B={res['champion_score']} "
        f"winner={res.get('winner_user_id')}"
    )


def test_e2e_alice_lock_present(alice, e2e):
    cid = e2e["cid"]
    txs = _all_transactions(alice)
    locks = [t for t in txs if t.get("type") == "quiz_challenge_lock"
             and (t.get("ref_id") == cid or cid in str(t))]
    assert locks, f"Alice lock for {cid} not found among {len(txs)} txs"


def test_e2e_bob_lock_present(bob, e2e):
    cid = e2e["cid"]
    txs = _all_transactions(bob)
    locks = [t for t in txs if t.get("type") == "quiz_challenge_lock"
             and (t.get("ref_id") == cid or cid in str(t))]
    assert locks, f"Bob lock for {cid} not found among {len(txs)} txs"


def test_e2e_release_or_refund_present(alice, bob, e2e):
    cid = e2e["cid"]
    res = e2e["resolved"]
    winner = res.get("winner_user_id")
    a_user = (alice._user.get("user_id") or alice._user.get("id"))  # type: ignore[attr-defined]
    b_user = (bob._user.get("user_id") or bob._user.get("id"))      # type: ignore[attr-defined]

    a_txs = _all_transactions(alice)
    b_txs = _all_transactions(bob)

    def _find(txs, types):
        return [t for t in txs
                if t.get("type") in types and (t.get("ref_id") == cid or cid in str(t))]

    if winner is None:
        assert _find(a_txs, {"quiz_challenge_refund"}), "tie but no Alice refund"
        assert _find(b_txs, {"quiz_challenge_refund"}), "tie but no Bob refund"
    elif winner == a_user:
        assert _find(a_txs, {"quiz_challenge_release"}), "Alice winner but no release"
    elif winner == b_user:
        assert _find(b_txs, {"quiz_challenge_release"}), "Bob winner but no release"
    else:
        pytest.fail(f"winner uid {winner} matches neither alice nor bob")


# ────────────────────── Regression: champion flow ─────────────────────
def test_regression_champion_pending_flow_unchanged(alice):
    """Old DefyChampion flow (POST /challenge with target champion) must
    keep returning status='pending' (NOT awaiting_acceptor) so the
    'En attente du champion' banner still drives that path."""
    # Find current CM champion — ignore if endpoint absent.
    r = alice.get(f"{BASE}/api/quiz/champion/CM", timeout=30)
    if r.status_code != 200:
        pytest.skip(f"No champion endpoint reachable: {r.status_code}")
    j = r.json()
    champion_uid = (
        (j.get("champion") or {}).get("user_id")
        or j.get("champion_user_id")
        or j.get("user_id")
    )
    if not champion_uid:
        pytest.skip(f"No active CM champion to defy: {j}")
    if champion_uid == (alice._user.get("user_id") or alice._user.get("id")):  # type: ignore[attr-defined]
        pytest.skip("Alice is the current champion — cannot self-defy.")

    payload = {"target_user_id": champion_uid, "mode": "free", "country_code": "CM"}
    r = alice.post(f"{BASE}/api/quiz/champion/challenge", json=payload, timeout=60)
    if r.status_code == 404:
        pytest.skip("Champion-based /challenge endpoint not present in this build.")
    assert r.status_code == 200, f"champion challenge failed: {r.status_code} {r.text[:300]}"
    cid = r.json().get("challenge_id")
    assert cid

    r = alice.get(f"{BASE}/api/quiz/champion/challenges/{cid}", timeout=30)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d.get("status") == "pending", (
        f"expected legacy 'pending' status, got {d.get('status')}"
    )
    print(f"[REG] champion-flow cid={cid} status={d.get('status')}")
