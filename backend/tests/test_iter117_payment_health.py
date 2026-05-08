"""
iter117 — Unit tests for payment_health service utilities.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.payment_health import (
    BACKOFF_MINUTES, MAX_RETRY_ATTEMPTS, _format_digest_html,
)


def test_backoff_strictly_increasing_and_bounded():
    """Each retry waits at least as long as the previous attempt."""
    for i in range(1, len(BACKOFF_MINUTES)):
        assert BACKOFF_MINUTES[i] >= BACKOFF_MINUTES[i - 1], \
            f"Backoff non-monotonic at index {i}"
    # Total max wait must stay within a reasonable bound (≤ 24h).
    total = sum(BACKOFF_MINUTES)
    assert total < 24 * 60, f"Cumulated backoff too high: {total} min"


def test_max_retry_attempts_reasonable():
    """Don't hammer providers forever."""
    assert 3 <= MAX_RETRY_ATTEMPTS <= 12


def test_format_digest_html_handles_empty_cockpit():
    """Should never crash on a fresh cockpit (no data yet)."""
    cockpit = {
        "window_hours": 24,
        "generated_at": "2026-04-25T10:00:00+00:00",
        "providers": {
            "hubtel": {"verify_calls": 0, "verify_ok": 0, "verify_ok_rate": 0,
                       "paid_count": 0, "latency_avg_ms": 0,
                       "latency_p50_ms": 0, "latency_p95_ms": 0,
                       "latency_max_ms": 0, "ipn_errors": 0},
            "nowpayments": {"verify_calls": 0, "verify_ok": 0, "verify_ok_rate": 0,
                             "paid_count": 0, "latency_avg_ms": 0,
                             "latency_p50_ms": 0, "latency_p95_ms": 0,
                             "latency_max_ms": 0, "ipn_errors": 0},
        },
        "top_errors": [],
        "pending_verification": [],
        "retry_queue": {"due_now": 0, "scheduled": 0, "abandoned": 0},
    }
    html = _format_digest_html(cockpit)
    assert "Payment Health Daily" in html
    assert "HUBTEL" in html
    assert "NOWPAYMENTS" in html


def test_format_digest_html_renders_top_errors():
    cockpit = {
        "window_hours": 6, "generated_at": "2026-04-25T10:00:00+00:00",
        "providers": {
            "hubtel": {"verify_calls": 5, "verify_ok": 5, "verify_ok_rate": 100.0,
                       "paid_count": 4, "latency_avg_ms": 200,
                       "latency_p50_ms": 180, "latency_p95_ms": 350,
                       "latency_max_ms": 500, "ipn_errors": 2},
            "nowpayments": {"verify_calls": 1, "verify_ok": 0,
                             "verify_ok_rate": 0.0, "paid_count": 0,
                             "latency_avg_ms": 8000,
                             "latency_p50_ms": 8000, "latency_p95_ms": 8000,
                             "latency_max_ms": 8000, "ipn_errors": 5},
        },
        "top_errors": [
            {"module": "wallet.hubtel.ipn", "severity": "high",
             "occurrences": 3, "affected_users": 1,
             "message_sample": "HMAC mismatch", "last_seen": None,
             "status": "open"},
        ],
        "pending_verification": [{"tx_id": "dep_abc"}],
        "retry_queue": {"due_now": 1, "scheduled": 2, "abandoned": 0},
    }
    html = _format_digest_html(cockpit)
    assert "wallet.hubtel.ipn" in html
    assert "HMAC mismatch" in html
    # Pending count surfaces in the HTML
    assert ">1<" in html or "1</td>" in html
