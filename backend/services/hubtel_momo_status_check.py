"""
iter237af — Hubtel Mobile Money status check (cron, every 5 min).

Standalone background worker that catches transactions Hubtel never sent
a callback for. Fully idempotent: every credit / refund is wrapped in
`SELECT FOR UPDATE` + status check so a callback racing the cron will
never produce a double credit/refund.

  • Deposits pending > 5 min  →  GET txnstatus / collection
      Paid    → credit + completed   (only if still pending)
      Unpaid > 30 min → failed       (only if still pending)
  • Withdrawals pending > 5 min →  GET txnstatus / disbursement
      Success → completed            (only if still pending)
      Failed  → refund + failed      (only if still pending)
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from database import get_pool
from services.hubtel_momo import (
    get_collection_account,
    get_disbursement_account,
    get_hubtel_auth,
    get_proxies,
)

logger = logging.getLogger(__name__)

HUBTEL_TXN_STATUS_RECEIVE = (
    "https://api-txnstatus.hubtel.com/transactions/{account}/status"
)
HUBTEL_TXN_STATUS_SEND = (
    "https://smrsc.hubtel.com/api/merchants/{account}/transactions/status"
)
HUBTEL_TIMEOUT = 10.0
POLL_INTERVAL = 300.0     # 5 min
MIN_AGE_SECONDS = 300      # 5 min — only check tx older than that
DEPOSIT_FAIL_AGE_SECONDS = 1800  # 30 min — only fail unpaid deposits older than that


async def _insert_notification(conn, user_id: str, ntype: str, title: str,
                               message: str, data: dict) -> None:
    notif_id = f"notif_{uuid.uuid4().hex[:12]}"
    await conn.execute(
        """INSERT INTO notifications (notif_id, user_id, type, title, message, data)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        notif_id, user_id, ntype, title, message, json.dumps(data),
    )


async def _insert_audit(conn, user_id: str, action: str, details: dict) -> None:
    await conn.execute(
        """INSERT INTO audit_logs (user_id, action, resource, details)
           VALUES ($1, $2, 'wallet', $3)""",
        user_id, action, json.dumps(details),
    )


async def _hubtel_status(url_template: str, account: str, auth: str,
                         client_ref: str) -> dict | None:
    """Calls a Hubtel status endpoint via the Fixie proxy. Returns the
    parsed JSON body or None on any failure."""
    if not (account and auth and client_ref):
        return None
    url = url_template.format(account=account) + f"?clientReference={client_ref}"
    try:
        async with httpx.AsyncClient(timeout=HUBTEL_TIMEOUT,
                                      proxies=get_proxies() or None) as client:
            r = await client.get(url, headers={"Authorization": f"Basic {auth}"})
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except Exception:
            return None
    except Exception as e:  # noqa: BLE001
        logger.warning("[hubtel-status] %s call failed: %s", url_template, e)
        return None


async def _check_pending_deposits() -> None:
    """Sweep pending deposits older than 5 min. Calls Hubtel txnstatus
    and finalizes the transaction (credit on Paid, fail on stale Unpaid)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT tx_id, to_user_id, amount, reference,
                      EXTRACT(EPOCH FROM (NOW() - created_at)) AS age_seconds
                 FROM transactions
                WHERE provider = 'hubtel_momo'
                  AND type = 'deposit'
                  AND status = 'pending'
                  AND created_at < NOW() - INTERVAL '5 minutes'
                ORDER BY created_at ASC LIMIT 50"""
        )
    if not rows:
        return

    auth = await get_hubtel_auth()
    account = await get_collection_account()
    if not auth or not account:
        logger.info("[hubtel-status] deposits: misconfigured, skip")
        return

    for row in rows:
        body = await _hubtel_status(HUBTEL_TXN_STATUS_RECEIVE, account, auth, row["reference"])
        if not body:
            continue
        # Hubtel response shape: {"data": {"status": "Paid"|"Unpaid"|...}}
        data = body.get("data") or body.get("Data") or {}
        status = str(data.get("status") or data.get("Status") or "").lower()
        if status == "paid":
            await _finalize_deposit_paid(pool, row)
        elif status == "unpaid" and float(row["age_seconds"] or 0) > DEPOSIT_FAIL_AGE_SECONDS:
            await _finalize_deposit_failed(pool, row)


async def _finalize_deposit_paid(pool, row) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                "SELECT status FROM transactions WHERE tx_id=$1 FOR UPDATE",
                row["tx_id"],
            )
            if not tx or tx["status"] != "pending":
                return
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                row["amount"], datetime.now(timezone.utc), row["to_user_id"],
            )
            await conn.execute(
                "UPDATE transactions SET status='completed' WHERE tx_id=$1",
                row["tx_id"],
            )
            await _insert_audit(conn, row["to_user_id"],
                                "hubtel_momo_deposit_completed_via_cron",
                                {"tx_id": row["tx_id"], "reference": row["reference"]})
            await _insert_notification(
                conn, row["to_user_id"], "deposit_completed",
                "Mobile Money deposit confirmed",
                f"Your Mobile Money deposit of {row['amount']} USD has been confirmed and credited.",
                {"tx_id": row["tx_id"], "provider": "hubtel_momo", "via": "cron"},
            )


async def _finalize_deposit_failed(pool, row) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                "SELECT status FROM transactions WHERE tx_id=$1 FOR UPDATE",
                row["tx_id"],
            )
            if not tx or tx["status"] != "pending":
                return
            await conn.execute(
                "UPDATE transactions SET status='failed' WHERE tx_id=$1",
                row["tx_id"],
            )
            await _insert_audit(conn, row["to_user_id"],
                                "hubtel_momo_deposit_failed_via_cron",
                                {"tx_id": row["tx_id"], "reference": row["reference"]})
            await _insert_notification(
                conn, row["to_user_id"], "deposit_failed",
                "Mobile Money deposit failed",
                "Your Mobile Money deposit failed. No amount has been debited.",
                {"tx_id": row["tx_id"], "provider": "hubtel_momo", "via": "cron"},
            )


async def _check_pending_withdrawals() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT tx_id, from_user_id, amount, reference
                 FROM transactions
                WHERE provider = 'hubtel_momo'
                  AND type = 'withdrawal'
                  AND status = 'pending'
                  AND created_at < NOW() - INTERVAL '5 minutes'
                ORDER BY created_at ASC LIMIT 50"""
        )
    if not rows:
        return

    auth = await get_hubtel_auth()
    account = await get_disbursement_account()
    if not auth or not account:
        logger.info("[hubtel-status] withdrawals: misconfigured, skip")
        return

    for row in rows:
        body = await _hubtel_status(HUBTEL_TXN_STATUS_SEND, account, auth, row["reference"])
        if not body:
            continue
        data = body.get("data") or body.get("Data") or {}
        status = str(
            data.get("transactionStatus")
            or data.get("status")
            or data.get("Status")
            or ""
        ).lower()
        if status in ("success", "completed", "successful"):
            await _finalize_withdrawal_success(pool, row)
        elif status in ("failed", "rejected"):
            await _finalize_withdrawal_failed(pool, row)


async def _finalize_withdrawal_success(pool, row) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                "SELECT status FROM transactions WHERE tx_id=$1 FOR UPDATE",
                row["tx_id"],
            )
            if not tx or tx["status"] != "pending":
                return
            await conn.execute(
                "UPDATE transactions SET status='completed' WHERE tx_id=$1",
                row["tx_id"],
            )
            await _insert_audit(conn, row["from_user_id"],
                                "hubtel_momo_withdrawal_completed_via_cron",
                                {"tx_id": row["tx_id"], "reference": row["reference"]})
            await _insert_notification(
                conn, row["from_user_id"], "withdrawal_completed",
                "Mobile Money withdrawal confirmed",
                "Your Mobile Money withdrawal has been completed successfully.",
                {"tx_id": row["tx_id"], "provider": "hubtel_momo", "via": "cron"},
            )


async def _finalize_withdrawal_failed(pool, row) -> None:
    """Atomic refund — funds were already debited at withdrawal time."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                "SELECT status FROM transactions WHERE tx_id=$1 FOR UPDATE",
                row["tx_id"],
            )
            if not tx or tx["status"] != "pending":
                return
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                Decimal(str(row["amount"])), datetime.now(timezone.utc),
                row["from_user_id"],
            )
            await conn.execute(
                "UPDATE transactions SET status='failed' WHERE tx_id=$1",
                row["tx_id"],
            )
            await _insert_audit(conn, row["from_user_id"],
                                "hubtel_momo_withdrawal_refunded_via_cron",
                                {"tx_id": row["tx_id"], "reference": row["reference"]})
            await _insert_notification(
                conn, row["from_user_id"], "withdrawal_failed",
                "Mobile Money withdrawal failed",
                "Your Mobile Money withdrawal failed. Your balance has been refunded automatically.",
                {"tx_id": row["tx_id"], "provider": "hubtel_momo", "via": "cron"},
            )


# ─────────────────── Worker loop entrypoint ────────────────
async def status_check_loop() -> None:
    """Long-running coroutine. Started from server.py at app startup."""
    logger.info("[hubtel-status] worker started, interval=%ss", POLL_INTERVAL)
    while True:
        try:
            await _check_pending_deposits()
            await _check_pending_withdrawals()
        except Exception as e:  # noqa: BLE001
            logger.warning("[hubtel-status] tick error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


__all__ = ["status_check_loop"]
