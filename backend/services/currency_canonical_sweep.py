"""
JAPAP — Currency Canonical Sweep (iter178)
==========================================
Companion to `usd_canonical_migration.py` (iter158). The original migration
ran ONCE at boot but new wallet rows can still be created with currency='XAF'
(legacy default in some routes/seed scripts). This module:

1. Runs at every backend boot (idempotent).
2. Hooked from the payment_verify_retry_worker tick (every 120s) to catch
   any drift between releases.
3. Logs every conversion in `currency_migration_logs` (audit trail
   per CEO mandate).
4. Updates wallets balance to USD using FX from currency_conversion service.

CEO RULE (iter178): wallets.currency MUST be 'USD' — period.
This module enforces it without breaking 16 legacy subsystems that still
write XAF transactions: it just normalises post-write so storage stays USD.
"""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


async def ensure_log_table() -> None:
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS currency_migration_logs (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64),
                old_balance NUMERIC(20, 6),
                old_currency VARCHAR(8),
                new_balance_usd NUMERIC(20, 6),
                fx_rate_used NUMERIC(20, 6),
                source VARCHAR(32) NOT NULL DEFAULT 'sweep',
                notes TEXT DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_curmig_user ON currency_migration_logs(user_id);
            CREATE INDEX IF NOT EXISTS idx_curmig_created ON currency_migration_logs(created_at DESC);
        """)


async def sweep_wallets_to_usd(*, source: str = "sweep") -> dict:
    """Convert every wallets row whose currency != 'USD' to USD using the
    centralised FX service. Logs each conversion. Idempotent.

    Returns: {converted: int, total_old_usd: str}
    """
    from database import get_pool
    from services.currency_conversion import to_usd
    pool = await get_pool()
    converted = 0
    total_old_usd = Decimal("0")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, balance, currency FROM wallets "
            "WHERE currency IS DISTINCT FROM 'USD' LIMIT 500")
    for r in rows:
        try:
            old_balance = Decimal(str(r['balance'] or 0))
            old_ccy = (r['currency'] or 'USD').upper()
            if old_ccy == 'USD':
                continue
            # Convert
            new_balance_usd = await to_usd(old_balance, old_ccy, rounding=2)
            fx = (old_balance / new_balance_usd) if new_balance_usd > 0 else Decimal("1")
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE wallets SET balance = $1, currency = 'USD', "
                        "updated_at = NOW() WHERE user_id = $2",
                        new_balance_usd, r['user_id'])
                    await conn.execute("""
                        INSERT INTO currency_migration_logs
                          (user_id, old_balance, old_currency,
                           new_balance_usd, fx_rate_used, source, notes)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """, r['user_id'], old_balance, old_ccy,
                       new_balance_usd, fx, source,
                       f"sweep wallet {old_ccy}→USD")
            converted += 1
            total_old_usd += new_balance_usd
        except Exception as e:
            logger.warning(f"[currency-sweep] {r['user_id']} skipped: {e}")
    if converted:
        logger.info(
            f"[currency-sweep] {converted} wallet(s) → USD (≈{total_old_usd} USD) "
            f"source={source}")
    return {"converted": converted, "total_old_usd": str(total_old_usd)}


async def sweep_transactions_amount_usd(*, source: str = "sweep") -> dict:
    """Backfill `amount_usd` for any transactions row missing it (currency
    might still be XAF legacy — that's fine, only the canonical USD column
    matters for aggregations)."""
    from database import get_pool
    from services.currency_conversion import to_usd
    pool = await get_pool()
    fixed = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT tx_id, amount, currency FROM transactions
            WHERE amount_usd IS NULL LIMIT 500
        """)
    for r in rows:
        try:
            ccy = (r['currency'] or 'USD').upper()
            amt = Decimal(str(r['amount'] or 0))
            usd_val = amt if ccy == 'USD' else await to_usd(amt, ccy, rounding=6)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE transactions SET amount_usd = $1 WHERE tx_id = $2",
                    usd_val, r['tx_id'])
            fixed += 1
        except Exception as e:
            logger.warning(f"[tx-amount_usd-backfill] {r['tx_id']} skipped: {e}")
    if fixed:
        logger.info(f"[currency-sweep] {fixed} tx amount_usd backfilled (source={source})")
    return {"backfilled": fixed}


async def run_full_sweep(*, source: str = "boot") -> dict:
    """One-shot helper used at boot + periodic worker tick."""
    await ensure_log_table()
    w = await sweep_wallets_to_usd(source=source)
    t = await sweep_transactions_amount_usd(source=source)
    return {"wallets": w, "transactions": t}
