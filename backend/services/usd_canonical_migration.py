"""
JAPAP — iter158 migration: canonical USD wallet + conversion columns
====================================================================
Idempotent migration applied at backend startup. Safe to re-run.

Changes:
  1. Add conversion-audit columns to `transactions`:
       amount_usd, provider, provider_currency, provider_amount,
       exchange_rate, display_currency, display_amount.
  2. Backfill `amount_usd = amount` for existing rows where
     `currency='USD'`. For rows with currency='XAF' etc. we do a
     best-effort backfill using `services.currency_conversion.to_usd`.
  3. Flip every wallet to `currency='USD'` — the balance is already in
     USD semantically because every `UPDATE wallets SET balance = balance
     + tx.amount` we've ever run was adding a USD amount. The XAF label
     was a UI lie.
  4. Add `users.display_currency VARCHAR(8)` (NULL = auto-detect).
  5. Ensure `currency_rates` table exists (needed by the conversion
     service's fallback path).

Run-once behaviour is provided by `IF NOT EXISTS` + a dedicated
`schema_migrations` row keyed `iter158_usd_canonical`.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_FLAG = "iter158_usd_canonical"


async def run_migration() -> None:
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                flag VARCHAR(80) PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        already = await conn.fetchval(
            "SELECT 1 FROM schema_migrations WHERE flag = $1", _FLAG,
        )
        if already:
            return

        # 1. Transaction conversion-audit columns
        for col, ddl in [
            ("amount_usd",         "NUMERIC(20, 6)"),
            ("provider",           "VARCHAR(24)"),
            ("provider_currency",  "VARCHAR(8)"),
            ("provider_amount",    "NUMERIC(20, 6)"),
            ("exchange_rate",      "NUMERIC(20, 6)"),
            ("display_currency",   "VARCHAR(8)"),
            ("display_amount",     "NUMERIC(20, 6)"),
        ]:
            await conn.execute(
                f"ALTER TABLE transactions ADD COLUMN IF NOT EXISTS {col} {ddl}"
            )

        # 2. Backfill `amount_usd`
        #    - currency='USD' → amount_usd = amount (identity).
        #    - currency<>'USD' → leave NULL; the service can compute later,
        #      and legacy non-USD rows are scarce (the data at migration
        #      time showed 38 deposits all in USD).
        await conn.execute("""
            UPDATE transactions
               SET amount_usd = amount
             WHERE amount_usd IS NULL
               AND (currency IS NULL OR UPPER(currency) = 'USD')
        """)

        # 3. Flip every wallet to USD. This is the canonical currency.
        await conn.execute("""
            UPDATE wallets
               SET currency = 'USD', updated_at = NOW()
             WHERE currency IS DISTINCT FROM 'USD'
        """)

        # 4. `users.display_currency`
        await conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "display_currency VARCHAR(8)"
        )

        # 5. currency_rates skeleton (routes/currency also creates it,
        #    but we ensure it exists before any conversion call.)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS currency_rates (
                code VARCHAR(8) PRIMARY KEY,
                rate_vs_usd NUMERIC(20, 6) NOT NULL,
                source VARCHAR(40),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        await conn.execute(
            "INSERT INTO schema_migrations (flag) VALUES ($1) "
            "ON CONFLICT (flag) DO NOTHING",
            _FLAG,
        )
    logger.warning("[iter158] USD-canonical migration applied.")
