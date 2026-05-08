"""
Admin wallet management — deposits & withdrawals moderation.
=============================================================
Aligned with the EAA logic requested by the product team:
  - Deposits are AUTO when webhook is wired, but admin can still approve
    pending ones manually (or reject them).
  - Withdrawals are MANUAL by default. Admin can approve (→ completed)
    or reject (→ refund + rejected).

Endpoints:
  GET  /api/admin/deposits
  POST /api/admin/deposits/{tx_id}/approve
  POST /api/admin/deposits/{tx_id}/reject
  GET  /api/admin/withdrawals
  POST /api/admin/withdrawals/{tx_id}/approve
  POST /api/admin/withdrawals/{tx_id}/reject
"""
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from database import get_pool
from routes.admin import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin-wallet"])


class ModerationRequest(BaseModel):
    reason: str = ""
    admin_notes: str = ""


async def _list_tx(tx_type: str, status: Optional[str], page: int, limit: int,
                   q: Optional[str], date_from: Optional[str], date_to: Optional[str]):
    """Generic lister with filters. Joins users for name/email."""
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        params = [tx_type]
        base = """
            FROM transactions t
            LEFT JOIN users u ON u.user_id = COALESCE(t.to_user_id, t.from_user_id)
            WHERE t.type = $1
        """
        if status:
            params.append(status)
            base += f" AND t.status = ${len(params)}"
        if q:
            params.append(f"%{q}%")
            base += (
                f" AND (u.email ILIKE ${len(params)} OR u.username ILIKE ${len(params)} "
                f"OR u.first_name ILIKE ${len(params)} OR u.last_name ILIKE ${len(params)} "
                f"OR t.tx_id ILIKE ${len(params)} OR t.reference ILIKE ${len(params)})"
            )
        if date_from:
            try:
                dt_from = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            except ValueError:
                dt_from = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            params.append(dt_from)
            base += f" AND t.created_at >= ${len(params)}"
        if date_to:
            try:
                dt_to = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            except ValueError:
                dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            params.append(dt_to)
            base += f" AND t.created_at <= ${len(params)}"

        count = await conn.fetchval(f"SELECT COUNT(*) {base}", *params)
        volume = await conn.fetchval(f"SELECT COALESCE(SUM(t.amount), 0) {base}", *params)
        params_q = params + [limit, offset]
        rows = await conn.fetch(
            f"""
            SELECT t.*, u.email, u.username, u.first_name, u.last_name, u.avatar
            {base}
            ORDER BY t.created_at DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params_q,
        )
        items = []
        for row in rows:
            d = dict(row)
            d['amount'] = str(d['amount'])
            d['fee'] = str(d['fee'])
            d['created_at'] = d['created_at'].isoformat()
            # Parse the method / processing_mode out of notes (stored as "[Label] mode=..."):
            notes = d.get('notes') or ''
            d['method_label'] = ''
            d['processing_mode'] = ''
            if notes.startswith('['):
                try:
                    d['method_label'] = notes.split(']', 1)[0].lstrip('[').strip()
                except Exception:
                    pass
            if 'mode=auto' in notes:
                d['processing_mode'] = 'auto'
            elif 'mode=manual' in notes:
                d['processing_mode'] = 'manual'
            items.append(d)
        return {
            "items": items,
            "total": count,
            "volume_total_usd": str(volume),
            "page": page,
            "limit": limit,
        }


@router.get("/deposits")
async def list_deposits(request: Request,
                        status: Optional[str] = Query(None),
                        page: int = Query(1, ge=1),
                        limit: int = Query(20, ge=1, le=100),
                        q: Optional[str] = None,
                        date_from: Optional[str] = None,
                        date_to: Optional[str] = None):
    await require_admin(request)
    return await _list_tx("deposit", status, page, limit, q, date_from, date_to)


@router.get("/withdrawals")
async def list_withdrawals(request: Request,
                           status: Optional[str] = Query(None),
                           page: int = Query(1, ge=1),
                           limit: int = Query(20, ge=1, le=100),
                           q: Optional[str] = None,
                           date_from: Optional[str] = None,
                           date_to: Optional[str] = None):
    await require_admin(request)
    return await _list_tx("withdrawal", status, page, limit, q, date_from, date_to)


async def _notify(conn, user_id: str, title: str, message: str, data: str = "{}"):
    notif_id = f"notif_{uuid.uuid4().hex[:12]}"
    await conn.execute("""
        INSERT INTO notifications (notif_id, user_id, type, title, message, data)
        VALUES ($1, $2, 'wallet', $3, $4, $5)
    """, notif_id, user_id, title, message, data)


@router.post("/deposits/{tx_id}/approve")
async def approve_deposit(tx_id: str, req: ModerationRequest, request: Request):
    """iter156 — Manual deposit approval is DISABLED.

    Deposits are auto-credited 100 % via provider webhook (Hubtel /
    NowPayments) + active retry worker. An admin should never flip the
    state of a payment manually — it's a business risk (double-credit,
    fraud, audit trail loss).

    If you need to reconcile a stuck deposit, use:
      POST /api/admin/deposits/{tx_id}/reverify
    which asks the provider again and credits IFF the provider confirms.
    """
    _ = await require_admin(request)
    raise HTTPException(
        status_code=410,
        detail=("MANUAL_DEPOSIT_APPROVAL_DISABLED: les dépôts sont "
                "auto-confirmés via webhook provider. Utilise "
                "POST /api/admin/deposits/{tx_id}/reverify pour "
                "re-interroger le provider si le webhook est en retard."),
    )


@router.post("/deposits/{tx_id}/reject")
async def reject_deposit(tx_id: str, req: ModerationRequest, request: Request):
    """iter156 — Manual deposit rejection is DISABLED. See /approve docstring."""
    _ = await require_admin(request)
    raise HTTPException(
        status_code=410,
        detail=("MANUAL_DEPOSIT_REJECTION_DISABLED: les dépôts sont "
                "auto-confirmés via webhook provider. Un dépôt non "
                "confirmé expire automatiquement via le retry worker."),
    )


@router.post("/deposits/{tx_id}/reverify")
async def reverify_deposit(tx_id: str, request: Request):
    """iter156 — Ask the payment provider again for the current status
    of this deposit, and credit the wallet IFF the provider confirms the
    payment. Does NOT grant the admin any power to "force" a deposit —
    the final word belongs to Hubtel / NowPayments.

    This endpoint is idempotent and safe to call repeatedly.
    """
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            """SELECT tx_id, to_user_id, amount, amount_usd, currency, status,
                      reference, notes, created_at
                 FROM transactions WHERE tx_id = $1 AND type = 'deposit'""",
            tx_id,
        )
    if not tx:
        raise HTTPException(status_code=404, detail="Dépôt introuvable")
    if tx["status"] == "completed":
        return {"tx_id": tx_id, "result": "already_completed",
                "credited": False, "already": True}

    # Detect provider from notes populated at /deposit creation.
    notes_lower = (tx["notes"] or "").lower()
    if "nowpayments" in notes_lower:
        provider = "nowpayments"
    elif "hubtel" in notes_lower:
        provider = "hubtel"
    else:
        raise HTTPException(status_code=400,
                            detail="Provider inconnu pour cette transaction.")
    ref = tx["reference"] or ""
    if not ref:
        raise HTTPException(
            status_code=400,
            detail="Aucune référence provider — le webhook n'est pas arrivé "
                   "et la référence n'a jamais été enregistrée. Attendre le "
                   "webhook ou demander à l'utilisateur de relancer le paiement.",
        )

    # Call the provider API to verify the real current state.
    verified_paid = False
    details: dict = {}
    try:
        if provider == "nowpayments":
            from services.nowpayments_service import verify_payment_status
            v = await verify_payment_status(ref)
            verified_paid = bool(v.get("ok") and v.get("is_paid"))
            details = {"provider_status": v.get("status"),
                       "amount": str(v.get("amount") or ""),
                       "currency": v.get("currency")}
        else:  # hubtel
            from services.hubtel_service import verify_transaction_status
            # Hubtel uses checkout_id (hubtelTransactionId) as the
            # canonical cross-ref; our stored `reference` is that value.
            v = await verify_transaction_status(checkout_id=ref)
            verified_paid = bool(v.get("ok") and v.get("is_paid"))
            details = {"provider_status": v.get("status"),
                       "amount": str(v.get("amount") or "")}
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f"Provider API indisponible : {e}")

    # Audit the observation first (even if we do not credit).
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_logs (user_id, action, resource, details)
               VALUES ($1, 'admin_reverify_deposit', 'transactions', $2)""",
            admin["user_id"],
            f'{{"tx_id":"{tx_id}","provider":"{provider}","verified_paid":{str(verified_paid).lower()}}}',
        )

    if not verified_paid:
        return {"tx_id": tx_id, "provider": provider,
                "result": "not_confirmed", "credited": False,
                "details": details}

    # Provider says PAID — credit atomically + idempotently.
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Re-read with row-lock to prevent races with the webhook.
            locked = await conn.fetchrow(
                "SELECT status FROM transactions WHERE tx_id = $1 FOR UPDATE",
                tx_id,
            )
            if locked and locked["status"] == "completed":
                return {"tx_id": tx_id, "result": "already_completed",
                        "credited": False, "already": True}
            # iter158 — always credit USD amount.
            credit_amt = tx["amount_usd"] if tx["amount_usd"] is not None else tx["amount"]
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = $2 "
                "WHERE user_id = $3",
                credit_amt, now, tx["to_user_id"],
            )
            await conn.execute(
                """UPDATE transactions SET status = 'completed',
                        admin_notes = COALESCE(NULLIF(admin_notes, ''), '') ||
                                      ' | admin_reverify confirmed via ' || $1
                    WHERE tx_id = $2""",
                provider, tx_id,
            )
            await _notify(conn, tx["to_user_id"],
                          "Dépôt crédité",
                          f"Votre dépôt de {tx['amount']} {tx['currency']} "
                          f"vient d'être confirmé par {provider} et crédité.",
                          f'{{"tx_id":"{tx_id}","amount":"{tx["amount"]}"}}')
    return {"tx_id": tx_id, "provider": provider, "result": "credited",
            "credited": True, "details": details}


@router.post("/withdrawals/{tx_id}/approve")
async def approve_withdrawal(tx_id: str, req: ModerationRequest, request: Request):
    """Mark a pending withdrawal as completed. The admin has already sent the
    USDT externally (manually or via SDK). We just finalize the record."""
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT * FROM transactions WHERE tx_id = $1 AND type = 'withdrawal'",
            tx_id,
        )
        if not tx:
            raise HTTPException(status_code=404, detail="Retrait introuvable")
        if tx['status'] == 'completed':
            return {"status": "already_completed"}
        if tx['status'] == 'rejected':
            raise HTTPException(status_code=400, detail="Retrait déjà refusé")
        await conn.execute("""
            UPDATE transactions SET status = 'completed',
                admin_notes = COALESCE(NULLIF(admin_notes, ''), '') || $1
            WHERE tx_id = $2
        """, f" | Approved by {admin['user_id']}: {req.admin_notes or req.reason}", tx_id)
        await _notify(conn, tx['from_user_id'],
                      "Retrait validé",
                      f"Votre retrait de {tx['amount']} USDT a été envoyé.",
                      f'{{"tx_id":"{tx_id}","amount":"{tx["amount"]}"}}')
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_approve_withdrawal', 'transactions', $2)
        """, admin['user_id'],
            f'{{"tx_id":"{tx_id}","amount":"{tx["amount"]}"}}')
    return {"status": "completed", "tx_id": tx_id}


@router.post("/withdrawals/{tx_id}/reject")
async def reject_withdrawal(tx_id: str, req: ModerationRequest, request: Request):
    """Reject a pending withdrawal AND refund the user's wallet."""
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT * FROM transactions WHERE tx_id = $1 AND type = 'withdrawal'",
            tx_id,
        )
        if not tx:
            raise HTTPException(status_code=404, detail="Retrait introuvable")
        if tx['status'] == 'completed':
            raise HTTPException(status_code=400, detail="Impossible de refuser un retrait déjà complété")
        if tx['status'] == 'rejected':
            return {"status": "already_rejected"}
        async with conn.transaction():
            # Refund the user's wallet for the FULL amount originally debited
            # (amount includes the fee, since the fee was debited along with
            # the net amount — this matches /api/wallet/withdraw which debits
            # the full amount in one UPDATE).
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                tx['amount'], datetime.now(timezone.utc), tx['from_user_id'],
            )
            await conn.execute("""
                UPDATE transactions SET status = 'rejected',
                    admin_notes = COALESCE(NULLIF(admin_notes, ''), '') || $1
                WHERE tx_id = $2
            """, f" | Rejected by {admin['user_id']}: {req.reason} (refund issued)", tx_id)
            await _notify(conn, tx['from_user_id'],
                          "Retrait refusé — remboursé",
                          f"Votre retrait de {tx['amount']} USDT a été refusé. Motif : {req.reason or 'non précisé'}. Les fonds ont été recrédités sur votre wallet.",
                          f'{{"tx_id":"{tx_id}","reason":"{req.reason}"}}')
            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'admin_reject_withdrawal', 'transactions', $2)
            """, admin['user_id'],
                f'{{"tx_id":"{tx_id}","reason":"{req.reason}","refunded":"{tx["amount"]}"}}')
    return {"status": "rejected", "tx_id": tx_id, "refunded": str(tx['amount'])}
