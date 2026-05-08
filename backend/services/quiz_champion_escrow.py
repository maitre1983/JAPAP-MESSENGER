"""
Quiz Champion — Escrow helpers (iter125, Phase 3.B).
====================================================

All escrow operations are performed inside the caller's `conn.transaction()`
to guarantee atomicity. Each operation:
  • Acquires a row-level lock on the involved wallet via SELECT ... FOR UPDATE
  • Updates wallet.balance with a single UPDATE … SET balance = balance ± $1
  • Inserts a `transactions` row of the appropriate type for full auditability

Transaction types (NEVER silent — every escrow movement leaves a row):
  • quiz_challenge_lock        — debit a player's wallet on stake-lock
  • quiz_challenge_release     — credit the winner with (pot - commission)
  • quiz_challenge_refund      — credit a player back on refuse/expiry/tie
  • quiz_challenge_commission  — JAPAP commission (no to_user_id, house)
  • quiz_challenge_bonus       — engagement-points bonus (logged in transactions
                                  AND mirrored in wheel_cycles via add_points,
                                  so it shows up in both ledgers)
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def _new_tx_id() -> str:
    return f"qct_{uuid.uuid4().hex[:18]}"


async def lock_stake(conn, *, user_id: str, amount: Decimal, currency: str,
                     challenge_id: str) -> dict:
    """Debit the user's wallet by `amount`. Returns the tx_id and new balance.
    Raises 400/402 if balance insufficient or wallet locked.
    MUST be called inside an active conn.transaction()."""
    wallet = await conn.fetchrow(
        "SELECT user_id, balance, currency, is_locked FROM wallets WHERE user_id = $1 FOR UPDATE",
        user_id,
    )
    if not wallet:
        raise HTTPException(status_code=404, detail="Portefeuille introuvable.")
    if wallet["is_locked"]:
        raise HTTPException(status_code=403, detail="Votre portefeuille est verrouillé.")
    # Currency match: a paid challenge requires both wallets to use the same
    # currency. Cross-currency challenges are out of scope for the MVP.
    if (wallet["currency"] or "").upper() != currency.upper():
        raise HTTPException(
            status_code=400,
            detail=f"Devise non compatible : votre portefeuille est en {wallet['currency']}, la mise en {currency}.",
        )
    bal = Decimal(str(wallet["balance"]))
    if bal < amount:
        # iter231 — surface the exact gap so the UI can guide the user to
        # recharge with the right amount instead of a vague "insufficient".
        gap = (amount - bal).quantize(Decimal("0.01"))
        raise HTTPException(
            status_code=402,
            detail=(f"Solde insuffisant : disponible {bal} {currency}, "
                    f"requis {amount} {currency} (manque {gap} {currency})."),
        )
    await conn.execute(
        "UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
        amount, datetime.now(timezone.utc), user_id,
    )
    tx_id = _new_tx_id()
    await conn.execute(
        """INSERT INTO transactions (tx_id, from_user_id, to_user_id, type,
                                     amount, currency, status, reference, notes, created_at)
           VALUES ($1, $2, NULL, 'quiz_challenge_lock', $3, $4, 'completed', $5, $6, NOW())""",
        tx_id, user_id, amount, currency.upper(), challenge_id,
        f"Lock for challenge {challenge_id}",
    )
    new_balance = await conn.fetchval(
        "SELECT balance FROM wallets WHERE user_id = $1", user_id,
    )
    return {"tx_id": tx_id, "new_balance": str(new_balance), "amount": str(amount)}


async def release_to_winner(conn, *, winner_user_id: str, gross_pot: Decimal,
                            commission_pct: Decimal, currency: str,
                            challenge_id: str) -> dict:
    """Credit the winner with (gross_pot - commission). Records the commission
    as a separate house book-entry row.
    """
    if gross_pot <= 0:
        return {"payout_tx_id": None, "commission_tx_id": None,
                "payout_amount": "0", "commission_amount": "0"}
    commission = (gross_pot * commission_pct / Decimal("100")).quantize(Decimal("0.01"))
    if commission < 0:
        commission = Decimal("0")
    if commission > gross_pot:
        commission = gross_pot
    payout = gross_pot - commission

    # Verify winner wallet exists + same currency.
    w = await conn.fetchrow(
        "SELECT currency FROM wallets WHERE user_id = $1 FOR UPDATE",
        winner_user_id,
    )
    if not w:
        raise HTTPException(status_code=404, detail="Portefeuille gagnant introuvable.")
    if (w["currency"] or "").upper() != currency.upper():
        # Should not happen since lock_stake enforced same currency; fail safe.
        raise HTTPException(status_code=500,
                            detail="Incohérence de devise (escrow corrompu).")
    if payout > 0:
        await conn.execute(
            "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
            payout, datetime.now(timezone.utc), winner_user_id,
        )
    payout_tx_id = _new_tx_id()
    await conn.execute(
        """INSERT INTO transactions (tx_id, from_user_id, to_user_id, type,
                                     amount, currency, status, reference, notes, created_at)
           VALUES ($1, NULL, $2, 'quiz_challenge_release', $3, $4, 'completed', $5, $6, NOW())""",
        payout_tx_id, winner_user_id, payout, currency.upper(), challenge_id,
        f"Payout (pot {gross_pot}, commission {commission}) for {challenge_id}",
    )
    commission_tx_id = None
    if commission > 0:
        commission_tx_id = _new_tx_id()
        await conn.execute(
            """INSERT INTO transactions (tx_id, from_user_id, to_user_id, type,
                                         amount, currency, status, reference, notes, created_at)
               VALUES ($1, NULL, NULL, 'quiz_challenge_commission', $2, $3, 'completed', $4, $5, NOW())""",
            commission_tx_id, commission, currency.upper(), challenge_id,
            f"JAPAP commission ({commission_pct}%) on challenge {challenge_id}",
        )
    return {
        "payout_tx_id": payout_tx_id, "commission_tx_id": commission_tx_id,
        "payout_amount": str(payout), "commission_amount": str(commission),
    }


async def lock_stake_double(conn, *, user_id: str, amount: Decimal, currency: str,
                             challenge_id: str, notes: Optional[str] = None) -> dict:
    """iter232 — Mission 2 (Doubler la mise).
    Same as `lock_stake` but stamps `quiz_challenge_lock_double` so admin
    audit can separate the pre-authorised double-stake slice from the
    base stake.
    Used for:
      • A's pre-authorisation when allow_double=True at challenge open
      • B's matching slice when B accepts with double=True
    """
    wallet = await conn.fetchrow(
        "SELECT user_id, balance, currency, is_locked FROM wallets WHERE user_id = $1 FOR UPDATE",
        user_id,
    )
    if not wallet:
        raise HTTPException(status_code=404, detail="Portefeuille introuvable.")
    if wallet["is_locked"]:
        raise HTTPException(status_code=403, detail="Votre portefeuille est verrouillé.")
    if (wallet["currency"] or "").upper() != currency.upper():
        raise HTTPException(
            status_code=400,
            detail=f"Devise non compatible : portefeuille en {wallet['currency']}, mise en {currency}.",
        )
    bal = Decimal(str(wallet["balance"]))
    if bal < amount:
        gap = (amount - bal).quantize(Decimal("0.01"))
        raise HTTPException(
            status_code=402,
            detail=(f"Solde insuffisant pour le doublement : disponible {bal} {currency}, "
                    f"requis {amount} {currency} de plus (manque {gap} {currency})."),
        )
    await conn.execute(
        "UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
        amount, datetime.now(timezone.utc), user_id,
    )
    tx_id = _new_tx_id()
    await conn.execute(
        """INSERT INTO transactions (tx_id, from_user_id, to_user_id, type,
                                     amount, currency, status, reference, notes, created_at)
           VALUES ($1, $2, NULL, 'quiz_challenge_lock_double', $3, $4, 'completed', $5, $6, NOW())""",
        tx_id, user_id, amount, currency.upper(), challenge_id,
        notes or f"Lock (×2) for challenge {challenge_id}",
    )
    new_balance = await conn.fetchval(
        "SELECT balance FROM wallets WHERE user_id = $1", user_id,
    )
    return {"tx_id": tx_id, "new_balance": str(new_balance), "amount": str(amount)}


async def refund_player(conn, *, user_id: str, amount: Decimal, currency: str,
                        challenge_id: str, reason: str = "refund") -> Optional[str]:
    """Credit a player back. Returns the tx_id."""
    if amount <= 0:
        return None
    w = await conn.fetchrow(
        "SELECT currency FROM wallets WHERE user_id = $1 FOR UPDATE", user_id,
    )
    if not w:
        # Ledger-safe: still emit a transactions row for traceability even if
        # the wallet vanished (extremely unlikely; would surface in audit).
        logger.warning("refund_player: wallet missing for %s", user_id)
        tx_id = _new_tx_id()
        await conn.execute(
            """INSERT INTO transactions (tx_id, from_user_id, to_user_id, type,
                                         amount, currency, status, reference, notes, created_at)
               VALUES ($1, NULL, $2, 'quiz_challenge_refund', $3, $4, 'failed', $5, $6, NOW())""",
            tx_id, user_id, amount, currency.upper(), challenge_id,
            f"Refund {reason} for {challenge_id} (wallet missing)",
        )
        return tx_id
    if (w["currency"] or "").upper() != currency.upper():
        raise HTTPException(status_code=500, detail="Devise non compatible (refund).")
    await conn.execute(
        "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
        amount, datetime.now(timezone.utc), user_id,
    )
    tx_id = _new_tx_id()
    await conn.execute(
        """INSERT INTO transactions (tx_id, from_user_id, to_user_id, type,
                                     amount, currency, status, reference, notes, created_at)
           VALUES ($1, NULL, $2, 'quiz_challenge_refund', $3, $4, 'completed', $5, $6, NOW())""",
        tx_id, user_id, amount, currency.upper(), challenge_id,
        f"Refund ({reason}) for {challenge_id}",
    )
    return tx_id


async def log_bonus(conn, *, user_id: str, amount_pts: int,
                    challenge_id: str, reason: str) -> Optional[str]:
    """Audit-log a points bonus in `transactions` (no wallet movement —
    the points are added separately via add_points to wheel_cycles).
    """
    if amount_pts <= 0:
        return None
    tx_id = _new_tx_id()
    await conn.execute(
        """INSERT INTO transactions (tx_id, from_user_id, to_user_id, type,
                                     amount, currency, status, reference, notes, created_at)
           VALUES ($1, NULL, $2, 'quiz_challenge_bonus', $3, 'PTS', 'completed', $4, $5, NOW())""",
        tx_id, user_id, Decimal(str(amount_pts)), challenge_id,
        f"Engagement bonus +{amount_pts} pts ({reason}) for {challenge_id}",
    )
    return tx_id
