"""
iter232 — Backfill `transactions.currency` for quiz_challenge_* rows.
====================================================================

Historic escrow inserts (`lock/release/refund/commission`) never set the
`currency` column explicitly, so Postgres fell back to the table default
'XAF' even though all wallets migrated to canonical USD. That's why the
wallet history screenshot showed "−4 XAF" / "−1 XAF" for what were really
1 USD stakes.

This migration:
  • For lock/release/refund rows → take the currency of the real wallet
    (`from_user_id` for debits, `to_user_id` for credits).
  • For commission rows (no user) → infer from the sibling release row
    that shares the same `reference` (challenge_id).
  • Leave quiz_challenge_bonus rows alone (they're points, already 'PTS'
    going forward; legacy ones can be left as 'XAF' since the amount is
    a point count, not a money amount).

Safe to run multiple times; only updates rows where currency still
differs from the canonical wallet currency.
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("iter232_backfill")


async def run() -> None:
    dsn = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            # 1) Fix lock rows (debit) — use from_user_id wallet currency.
            n_lock = await conn.execute(
                """
                UPDATE transactions t
                   SET currency = w.currency
                  FROM wallets w
                 WHERE t.type = 'quiz_challenge_lock'
                   AND t.from_user_id = w.user_id
                   AND COALESCE(t.currency,'') <> COALESCE(w.currency,'')
                """
            )
            # 2) Release rows (credit to winner) — use to_user_id wallet.
            n_rel = await conn.execute(
                """
                UPDATE transactions t
                   SET currency = w.currency
                  FROM wallets w
                 WHERE t.type = 'quiz_challenge_release'
                   AND t.to_user_id = w.user_id
                   AND COALESCE(t.currency,'') <> COALESCE(w.currency,'')
                """
            )
            # 3) Refund rows — use to_user_id wallet.
            n_ref = await conn.execute(
                """
                UPDATE transactions t
                   SET currency = w.currency
                  FROM wallets w
                 WHERE t.type = 'quiz_challenge_refund'
                   AND t.to_user_id = w.user_id
                   AND COALESCE(t.currency,'') <> COALESCE(w.currency,'')
                """
            )
            # 4) Commission rows — infer from the sibling release row.
            n_com = await conn.execute(
                """
                UPDATE transactions c
                   SET currency = r.currency
                  FROM transactions r
                 WHERE c.type = 'quiz_challenge_commission'
                   AND r.type = 'quiz_challenge_release'
                   AND c.reference = r.reference
                   AND c.reference <> ''
                   AND COALESCE(c.currency,'') <> COALESCE(r.currency,'')
                """
            )
            # 5) Bonus rows — points, not money. Normalise to 'PTS'.
            n_bon = await conn.execute(
                """
                UPDATE transactions
                   SET currency = 'PTS'
                 WHERE type = 'quiz_challenge_bonus'
                   AND COALESCE(currency,'') <> 'PTS'
                """
            )
            log.info(
                "Backfill done: lock=%s release=%s refund=%s commission=%s bonus=%s",
                n_lock, n_rel, n_ref, n_com, n_bon,
            )
            # Sanity: remaining quiz_challenge_* rows still mislabelled ?
            remaining = await conn.fetch(
                """
                SELECT t.type, COUNT(*) AS n
                  FROM transactions t
                  LEFT JOIN wallets w ON w.user_id = COALESCE(t.from_user_id, t.to_user_id)
                 WHERE t.type LIKE 'quiz_challenge_%'
                   AND t.type <> 'quiz_challenge_bonus'
                   AND w.currency IS NOT NULL
                   AND COALESCE(t.currency,'') <> w.currency
                 GROUP BY t.type
                """
            )
            if remaining:
                log.warning("Residual mislabelled rows: %s",
                            [dict(r) for r in remaining])
            else:
                log.info("✓ No residual mislabelled quiz_challenge_* rows.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        log.error("Migration failed: %s", e)
        sys.exit(1)
