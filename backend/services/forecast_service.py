"""iter241a — Forecast (Prediction Markets) service.

MVP scope:
  • Single canonical USD wallet (table `wallets`). No MIR/LIMO/USDT split.
  • Atomic place-bet: balance check + debit + bet insert in a single tx.
  • Admin creates markets manually (no AI suggestions yet — iter241b).
  • Admin resolves: winners credited (multiplier × net_stake), losers stay debited.
  • Admin cancels: every placed bet refunded to the user's wallet.

Schema (created idempotently in `ensure_forecast_tables`):
  forecast_settings    (singleton row id=1, module + token + UX defaults)
  forecast_markets     (admin-curated questions)
  forecast_options     (one row per outcome with multiplier)
  forecast_bets        (one row per user stake, status placed/won/lost/refunded)
  forecast_results     (admin-locked outcome, one row per resolved market)

Everything keyed on `user_id VARCHAR(80)` for parity with the rest of JAPAP.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException

from database import get_pool

logger = logging.getLogger(__name__)

# Single source of truth — keep enum-style sets immutable & local.
VALID_TOKENS = ("usd",)  # iter241a MVP — USD canonical only. Future: mir/limo/usdt.
VALID_CATEGORIES = ("politics", "economy", "crypto", "culture", "world", "sport")
VALID_MARKET_TYPES = ("binary", "multiple_choice")


# ---------------------------------------------------------------------------
# DDL — idempotent, called once at server boot.
# ---------------------------------------------------------------------------
async def ensure_forecast_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_settings (
            id                            INTEGER PRIMARY KEY DEFAULT 1,
            module_enabled                BOOLEAN DEFAULT TRUE,
            token_usd_enabled             BOOLEAN DEFAULT TRUE,
            token_mir_enabled             BOOLEAN DEFAULT FALSE,
            token_limo_enabled            BOOLEAN DEFAULT FALSE,
            token_usdt_enabled            BOOLEAN DEFAULT FALSE,
            ai_suggestions_enabled        BOOLEAN DEFAULT FALSE,
            ai_result_detection_enabled   BOOLEAN DEFAULT FALSE,
            ai_abuse_detection_enabled    BOOLEAN DEFAULT FALSE,
            default_min_bet               NUMERIC(18,4) DEFAULT 1,
            default_max_bet               NUMERIC(18,4) DEFAULT 10000,
            default_max_bet_per_user      NUMERIC(18,4) DEFAULT 1000,
            default_max_exposure          NUMERIC(18,4) DEFAULT 50000,
            default_platform_fee_percent  NUMERIC(5,2)  DEFAULT 5.0,
            referral_commission_percent   NUMERIC(5,2)  DEFAULT 10.0,
            updated_at                    TIMESTAMPTZ DEFAULT NOW()
        )
        """)
        # iter241a-share — additive column for the referral commission %
        # (idempotent for environments where forecast_settings already exists).
        await conn.execute("""
        ALTER TABLE forecast_settings
        ADD COLUMN IF NOT EXISTS referral_commission_percent NUMERIC(5,2) DEFAULT 10.0
        """)
        # iter241a-share-tiers — Power-sharer reward configuration.
        # A user who attracted more than `power_sharer_threshold` winning
        # referrals in the trailing 30 days earns `power_sharer_commission_percent`
        # on future winning referrals (instead of the standard `referral_commission_percent`).
        await conn.execute("""
        ALTER TABLE forecast_settings
        ADD COLUMN IF NOT EXISTS power_sharer_threshold INTEGER DEFAULT 10,
        ADD COLUMN IF NOT EXISTS power_sharer_commission_percent NUMERIC(5,2) DEFAULT 15.0,
        ADD COLUMN IF NOT EXISTS power_sharer_window_days INTEGER DEFAULT 30
        """)
        await conn.execute("""
        INSERT INTO forecast_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_markets (
            id                    SERIAL PRIMARY KEY,
            market_id             VARCHAR(40) UNIQUE NOT NULL,
            title                 TEXT NOT NULL,
            description           TEXT DEFAULT '',
            category              VARCHAR(30) NOT NULL DEFAULT 'world',
            market_type           VARCHAR(20) DEFAULT 'binary',
            status                VARCHAR(20) DEFAULT 'draft',
            is_paused             BOOLEAN DEFAULT FALSE,
            closes_at             TIMESTAMPTZ NOT NULL,
            resolves_at           TIMESTAMPTZ,
            source_label          VARCHAR(255) DEFAULT '',
            source_url            VARCHAR(500) DEFAULT '',
            min_bet               NUMERIC(18,4) DEFAULT 1,
            max_bet               NUMERIC(18,4) DEFAULT 10000,
            max_bet_per_user      NUMERIC(18,4) DEFAULT 1000,
            max_exposure          NUMERIC(18,4) DEFAULT 50000,
            platform_fee_percent  NUMERIC(5,2)  DEFAULT 5.0,
            created_by            VARCHAR(80),
            created_at            TIMESTAMPTZ DEFAULT NOW(),
            updated_at            TIMESTAMPTZ DEFAULT NOW()
        )
        """)
        await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_fc_mk_status_closes
        ON forecast_markets(status, closes_at)
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_options (
            id              SERIAL PRIMARY KEY,
            option_id       VARCHAR(40) UNIQUE NOT NULL,
            market_id       VARCHAR(40) NOT NULL,
            label           TEXT NOT NULL,
            multiplier      NUMERIC(8,4) DEFAULT 2.0,
            display_order   INTEGER DEFAULT 0,
            is_winner       BOOLEAN DEFAULT FALSE,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
        """)
        await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_fc_opt_market ON forecast_options(market_id)
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_bets (
            id                 SERIAL PRIMARY KEY,
            bet_id             VARCHAR(40) UNIQUE NOT NULL,
            market_id          VARCHAR(40) NOT NULL,
            option_id          VARCHAR(40) NOT NULL,
            user_id            VARCHAR(80) NOT NULL,
            token_type         VARCHAR(10) NOT NULL DEFAULT 'usd',
            stake_amount       NUMERIC(18,4) NOT NULL,
            platform_fee       NUMERIC(18,4) DEFAULT 0,
            net_stake          NUMERIC(18,4) NOT NULL,
            multiplier         NUMERIC(8,4) NOT NULL,
            potential_payout   NUMERIC(18,4) NOT NULL,
            status             VARCHAR(20) DEFAULT 'placed',
            payout_amount      NUMERIC(18,4) DEFAULT 0,
            referrer_id        VARCHAR(80),
            referral_commission NUMERIC(18,4) DEFAULT 0,
            resolved_at        TIMESTAMPTZ,
            created_at         TIMESTAMPTZ DEFAULT NOW(),
            updated_at         TIMESTAMPTZ DEFAULT NOW()
        )
        """)
        # iter241a-share — additive columns for referral tracking
        # (idempotent for previously-created DBs).
        await conn.execute("""
        ALTER TABLE forecast_bets
        ADD COLUMN IF NOT EXISTS referrer_id VARCHAR(80),
        ADD COLUMN IF NOT EXISTS referral_commission NUMERIC(18,4) DEFAULT 0
        """)
        await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_fc_bet_referrer
        ON forecast_bets(referrer_id) WHERE referrer_id IS NOT NULL
        """)
        await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_fc_bet_market
        ON forecast_bets(market_id, status)
        """)
        await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_fc_bet_user
        ON forecast_bets(user_id, created_at DESC)
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_results (
            id                  SERIAL PRIMARY KEY,
            market_id           VARCHAR(40) UNIQUE NOT NULL,
            winning_option_id   VARCHAR(40) NOT NULL,
            result_notes        TEXT DEFAULT '',
            source_reference    TEXT DEFAULT '',
            resolved_by         VARCHAR(80),
            resolved_at         TIMESTAMPTZ DEFAULT NOW()
        )
        """)
    logger.info("[iter241a] forecast tables ensured")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


async def get_settings() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM forecast_settings WHERE id = 1")
    return _row_to_dict(row) if row else {}


async def update_settings(payload: dict, admin_id: str) -> dict:
    """Whitelist-update of `forecast_settings`. Silently ignores unknown keys."""
    allowed = {
        "module_enabled", "token_usd_enabled", "token_mir_enabled",
        "token_limo_enabled", "token_usdt_enabled",
        "ai_suggestions_enabled", "ai_result_detection_enabled",
        "ai_abuse_detection_enabled",
        "default_min_bet", "default_max_bet", "default_max_bet_per_user",
        "default_max_exposure", "default_platform_fee_percent",
        "referral_commission_percent",
        # iter241a-share-tiers — power-sharer configurable knobs.
        "power_sharer_threshold",
        "power_sharer_commission_percent",
        "power_sharer_window_days",
    }
    sets, args = [], []
    for k, v in (payload or {}).items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ${len(args) + 1}")
        args.append(v)
    if not sets:
        return await get_settings()
    sets.append(f"updated_at = ${len(args) + 1}")
    args.append(datetime.now(timezone.utc))
    sql = f"UPDATE forecast_settings SET {', '.join(sets)} WHERE id = 1"
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(sql, *args)
    logger.info("[iter241a] settings updated by %s: %s", admin_id, list(payload.keys()))
    return await get_settings()


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    out = dict(row)
    # Decimal -> float, datetime -> iso, for JSON-friendliness.
    for k, v in list(out.items()):
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
    return out


async def _require_module_enabled():
    s = await get_settings()
    if not s.get("module_enabled"):
        raise HTTPException(status_code=503, detail="Module Prédictions désactivé.")


# ---------------------------------------------------------------------------
# iter241a-share-tiers — Power-sharer helpers.
# ---------------------------------------------------------------------------
async def _count_winning_referrals_window(conn, user_id: str, window_days: int) -> int:
    """Count of `won` bets attributed to `user_id` as the upstream referrer
    over the trailing `window_days` (based on `resolved_at`)."""
    if not user_id:
        return 0
    row = await conn.fetchval(f"""
        SELECT COUNT(*) FROM forecast_bets
        WHERE referrer_id = $1
          AND status = 'won'
          AND resolved_at >= NOW() - INTERVAL '{int(window_days)} days'
    """, user_id)
    return int(row or 0)


def _tier_for(winning_referrals: int, threshold: int,
              standard_pct: Decimal, boosted_pct: Decimal) -> tuple[str, Decimal]:
    """Return (tier_label, commission_pct) for a referrer."""
    if winning_referrals > threshold:
        return ("power_sharer", boosted_pct)
    return ("standard", standard_pct)


async def get_tier_status(user_id: str) -> dict:
    """Public-facing tier summary for the current user. Used by ForecastPage
    to render the tier banner + progress towards the boost."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        s = await conn.fetchrow("""
            SELECT referral_commission_percent,
                   power_sharer_threshold,
                   power_sharer_commission_percent,
                   power_sharer_window_days
            FROM forecast_settings WHERE id = 1
        """)
        standard_pct = Decimal(str((s and s["referral_commission_percent"]) or 10))
        boosted_pct  = Decimal(str((s and s["power_sharer_commission_percent"]) or 15))
        threshold    = int((s and s["power_sharer_threshold"]) or 10)
        window_days  = int((s and s["power_sharer_window_days"]) or 30)
        won = await _count_winning_referrals_window(conn, user_id, window_days)
    tier, commission = _tier_for(won, threshold, standard_pct, boosted_pct)
    return {
        "user_id": user_id,
        "tier": tier,                          # 'standard' | 'power_sharer'
        "commission_percent": float(commission),
        "standard_percent": float(standard_pct),
        "boosted_percent": float(boosted_pct),
        "winning_referrals_window": won,
        "threshold": threshold,
        "window_days": window_days,
        "is_forecast_influencer": tier == "power_sharer",
    }


async def is_forecast_influencer(user_id: str) -> bool:
    """Thin helper used by the public profile endpoint to render the badge."""
    try:
        st = await get_tier_status(user_id)
        return bool(st.get("is_forecast_influencer"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public — markets + bet
# ---------------------------------------------------------------------------
async def list_markets(category: Optional[str], status: str = "active",
                        limit: int = 50, offset: int = 0) -> list[dict]:
    pool = await get_pool()
    where = ["m.status = $1"]
    params: list = [status]
    if category and category in VALID_CATEGORIES:
        where.append(f"m.category = ${len(params) + 1}")
        params.append(category)
    sql = f"""
        SELECT m.*,
               (SELECT COUNT(*)            FROM forecast_bets WHERE market_id = m.market_id AND status='placed') AS bets_count,
               (SELECT COUNT(DISTINCT user_id) FROM forecast_bets WHERE market_id = m.market_id AND status='placed') AS unique_bettors,
               (SELECT COALESCE(SUM(stake_amount),0) FROM forecast_bets WHERE market_id = m.market_id AND status='placed') AS total_staked
        FROM forecast_markets m
        WHERE {' AND '.join(where)}
        ORDER BY m.closes_at ASC
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
    """
    params += [limit, offset]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
        markets = []
        for r in rows:
            m = _row_to_dict(r)
            opts = await conn.fetch(
                "SELECT * FROM forecast_options WHERE market_id = $1 ORDER BY display_order, id",
                m["market_id"],
            )
            m["options"] = [_row_to_dict(o) for o in opts]
            markets.append(m)
    return markets


async def get_market(market_id: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        m = await conn.fetchrow("SELECT * FROM forecast_markets WHERE market_id = $1", market_id)
        if not m:
            return None
        out = _row_to_dict(m)
        opts = await conn.fetch(
            "SELECT * FROM forecast_options WHERE market_id = $1 ORDER BY display_order, id",
            market_id,
        )
        out["options"] = [_row_to_dict(o) for o in opts]
        stats = await conn.fetchrow("""
            SELECT COUNT(*) AS bets_count,
                   COUNT(DISTINCT user_id) AS unique_bettors,
                   COALESCE(SUM(stake_amount), 0) AS total_staked
            FROM forecast_bets WHERE market_id = $1 AND status = 'placed'
        """, market_id)
        out["bets_count"] = stats["bets_count"] or 0
        out["unique_bettors"] = stats["unique_bettors"] or 0
        out["total_staked"] = float(stats["total_staked"] or 0)
        # Per-option breakdown.
        for o in out["options"]:
            opt_stats = await conn.fetchrow("""
                SELECT COUNT(*) AS n, COALESCE(SUM(stake_amount), 0) AS staked
                FROM forecast_bets WHERE option_id = $1 AND status = 'placed'
            """, o["option_id"])
            o["bets_count"] = opt_stats["n"] or 0
            o["total_staked"] = float(opt_stats["staked"] or 0)
    # Already-resolved winner highlight.
    res = await get_result(market_id)
    if res:
        out["result"] = res
    return out


async def get_result(market_id: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow("SELECT * FROM forecast_results WHERE market_id = $1", market_id)
    return _row_to_dict(r) if r else None


async def place_bet(user_id: str, market_id: str, option_id: str,
                     token_type: str, stake_amount: float,
                     referrer_id: Optional[str] = None) -> dict:
    """Atomic bet placement. Debits the user's USD wallet, inserts a bet row,
    returns the new balance. Raises HTTPException on any guard violation —
    every guard message is user-visible so keep them clean & translatable
    on the frontend if needed.

    iter241a-share — `referrer_id` is an optional upstream user (the one
    who shared the link). It's validated against `users.user_id`, must NOT
    equal the bettor (no self-referral), and is recorded for later
    commission payout at resolution time.
    """
    await _require_module_enabled()

    token_type = (token_type or "usd").lower()
    if token_type not in VALID_TOKENS:
        raise HTTPException(status_code=400, detail=f"Token non supporté: {token_type}")
    settings = await get_settings()
    if not settings.get(f"token_{token_type}_enabled"):
        raise HTTPException(status_code=503, detail=f"Token {token_type.upper()} désactivé.")

    try:
        stake = Decimal(str(stake_amount))
    except Exception:
        raise HTTPException(status_code=400, detail="Montant invalide.")
    if stake <= 0:
        raise HTTPException(status_code=400, detail="Montant doit être positif.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Market + option lookup (must match) ----------------------------------
        market = await conn.fetchrow(
            "SELECT * FROM forecast_markets WHERE market_id = $1", market_id,
        )
        if not market:
            raise HTTPException(status_code=404, detail="Marché introuvable.")
        if market["status"] != "active":
            raise HTTPException(status_code=400, detail="Marché non actif.")
        if market["is_paused"]:
            raise HTTPException(status_code=400, detail="Marché en pause.")
        if market["closes_at"] and market["closes_at"] <= datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Marché fermé.")

        opt = await conn.fetchrow(
            "SELECT * FROM forecast_options WHERE option_id = $1 AND market_id = $2",
            option_id, market_id,
        )
        if not opt:
            raise HTTPException(status_code=404, detail="Option invalide.")

        # Per-market limits ----------------------------------------------------
        min_bet = Decimal(str(market["min_bet"]))
        max_bet = Decimal(str(market["max_bet"]))
        max_per_user = Decimal(str(market["max_bet_per_user"]))
        if stake < min_bet:
            raise HTTPException(status_code=400, detail=f"Mise minimum {min_bet} USD.")
        if stake > max_bet:
            raise HTTPException(status_code=400, detail=f"Mise maximum {max_bet} USD.")

        # Per-user lifetime cap on this market.
        already = await conn.fetchval("""
            SELECT COALESCE(SUM(stake_amount), 0) FROM forecast_bets
            WHERE market_id = $1 AND user_id = $2 AND status IN ('placed','won','lost')
        """, market_id, user_id) or 0
        if Decimal(str(already)) + stake > max_per_user:
            raise HTTPException(
                status_code=400,
                detail=f"Plafond atteint pour ce marché ({max_per_user} USD).",
            )

        # Compute fee + net stake + potential payout --------------------------
        fee_pct = Decimal(str(market["platform_fee_percent"] or 0))
        fee = (stake * fee_pct / Decimal("100")).quantize(Decimal("0.0001"))
        net = (stake - fee).quantize(Decimal("0.0001"))
        mult = Decimal(str(opt["multiplier"]))
        potential = (net * mult).quantize(Decimal("0.0001"))

        # Atomic debit + insert ------------------------------------------------
        async with conn.transaction():
            w = await conn.fetchrow(
                "SELECT balance, is_locked FROM wallets WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if not w:
                raise HTTPException(status_code=404, detail="Wallet introuvable.")
            if w["is_locked"]:
                raise HTTPException(status_code=403, detail="Wallet verrouillé.")
            if Decimal(str(w["balance"])) < stake:
                raise HTTPException(status_code=400, detail="Solde insuffisant.")

            now = datetime.now(timezone.utc)
            await conn.execute(
                "UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
                stake, now, user_id,
            )

            bet_id = _new_id("bet")
            # iter241a-share — referrer guard: must be a real user_id and
            # NOT the bettor (no self-referral).
            ref = (referrer_id or "").strip() or None
            if ref:
                if ref == user_id:
                    ref = None  # silently ignore self-referrals
                else:
                    exists = await conn.fetchval(
                        "SELECT 1 FROM users WHERE user_id = $1", ref,
                    )
                    if not exists:
                        ref = None
            await conn.execute("""
                INSERT INTO forecast_bets
                  (bet_id, market_id, option_id, user_id, token_type,
                   stake_amount, platform_fee, net_stake, multiplier,
                   potential_payout, status, referrer_id, created_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'placed',$11,$12,$12)
            """, bet_id, market_id, option_id, user_id, token_type,
               stake, fee, net, mult, potential, ref, now)

            # Mirror in transactions for the user's history & for admin audit.
            await conn.execute("""
                INSERT INTO transactions
                  (tx_id, from_user_id, type, amount, currency, status, notes, reference)
                VALUES ($1, $2, 'forecast_bet', $3, 'USD', 'completed', $4, $5)
            """, _new_id("tx"), user_id, stake,
               f"[Prédiction] {market['title'][:60]} → {opt['label'][:40]} · ×{mult}",
               bet_id)

            new_balance = await conn.fetchval(
                "SELECT balance FROM wallets WHERE user_id = $1", user_id,
            )

    return {
        "bet_id": bet_id,
        "stake_amount": float(stake),
        "platform_fee": float(fee),
        "net_stake": float(net),
        "multiplier": float(mult),
        "potential_payout": float(potential),
        "new_balance": float(new_balance),
    }


async def list_my_bets(user_id: str, status: Optional[str] = None,
                        limit: int = 50, offset: int = 0) -> list[dict]:
    pool = await get_pool()
    where = ["b.user_id = $1"]
    params: list = [user_id]
    if status:
        where.append(f"b.status = ${len(params) + 1}")
        params.append(status)
    sql = f"""
        SELECT b.*,
               m.title AS market_title, m.status AS market_status,
               m.category AS market_category, m.closes_at AS market_closes_at,
               o.label AS option_label
        FROM forecast_bets b
        JOIN forecast_markets m ON m.market_id = b.market_id
        JOIN forecast_options o ON o.option_id = b.option_id
        WHERE {' AND '.join(where)}
        ORDER BY b.created_at DESC
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
    """
    params += [limit, offset]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Admin — CRUD + lifecycle
# ---------------------------------------------------------------------------
async def admin_list_markets(status: Optional[str], limit: int, offset: int) -> list[dict]:
    pool = await get_pool()
    where = []
    params: list = []
    if status:
        where.append(f"status = ${len(params) + 1}")
        params.append(status)
    sql = f"""
        SELECT * FROM forecast_markets
        {('WHERE ' + ' AND '.join(where)) if where else ''}
        ORDER BY created_at DESC
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
    """
    params += [limit, offset]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
        out = []
        for r in rows:
            m = _row_to_dict(r)
            opts = await conn.fetch(
                "SELECT * FROM forecast_options WHERE market_id = $1 ORDER BY display_order, id",
                m["market_id"],
            )
            m["options"] = [_row_to_dict(o) for o in opts]
            stats = await conn.fetchrow("""
                SELECT COUNT(*) AS n, COALESCE(SUM(stake_amount), 0) AS staked
                FROM forecast_bets WHERE market_id = $1 AND status = 'placed'
            """, m["market_id"])
            m["bets_count"] = stats["n"] or 0
            m["total_staked"] = float(stats["staked"] or 0)
            out.append(m)
    return out


async def admin_create_market(payload: dict, admin_id: str) -> dict:
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Titre requis.")
    closes_at = payload.get("closes_at")
    if not closes_at:
        raise HTTPException(status_code=422, detail="Date de fermeture requise.")
    category = (payload.get("category") or "world").lower()
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=422, detail="Catégorie invalide.")
    market_type = (payload.get("market_type") or "binary").lower()
    if market_type not in VALID_MARKET_TYPES:
        raise HTTPException(status_code=422, detail="Type de marché invalide.")
    options = payload.get("options") or []
    if not isinstance(options, list) or len(options) < 2:
        raise HTTPException(status_code=422, detail="Au moins 2 options requises.")

    settings = await get_settings()
    min_bet = float(payload.get("min_bet") or settings.get("default_min_bet") or 1)
    max_bet = float(payload.get("max_bet") or settings.get("default_max_bet") or 10000)
    max_pu  = float(payload.get("max_bet_per_user") or settings.get("default_max_bet_per_user") or 1000)
    max_exp = float(payload.get("max_exposure") or settings.get("default_max_exposure") or 50000)
    fee_pct = float(payload.get("platform_fee_percent") or settings.get("default_platform_fee_percent") or 5)

    market_id = _new_id("mkt")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                INSERT INTO forecast_markets
                  (market_id, title, description, category, market_type, status,
                   closes_at, source_label, source_url,
                   min_bet, max_bet, max_bet_per_user, max_exposure,
                   platform_fee_percent, created_by)
                VALUES ($1,$2,$3,$4,$5,'draft',$6,$7,$8,$9,$10,$11,$12,$13,$14)
            """, market_id, title, payload.get("description") or "",
               category, market_type, closes_at,
               (payload.get("source_label") or "")[:255],
               (payload.get("source_url") or "")[:500],
               min_bet, max_bet, max_pu, max_exp, fee_pct, admin_id)

            for i, o in enumerate(options):
                label = (o.get("label") or "").strip()
                if not label:
                    raise HTTPException(status_code=422,
                                         detail=f"Option #{i+1} sans libellé.")
                try:
                    mult = float(o.get("multiplier") or 2.0)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=422,
                                         detail=f"Multiplicateur invalide pour option #{i+1}.")
                if mult < 1.01 or mult > 100:
                    raise HTTPException(status_code=422,
                                         detail="Multiplicateur doit être entre 1.01 et 100.")
                await conn.execute("""
                    INSERT INTO forecast_options
                      (option_id, market_id, label, multiplier, display_order)
                    VALUES ($1, $2, $3, $4, $5)
                """, _new_id("opt"), market_id, label, mult, i)
    return await get_market(market_id)


async def admin_set_market_status(market_id: str, action: str) -> dict:
    """action ∈ {activate, pause, resume, close, cancel}."""
    transitions = {
        "activate": ("draft", "active",     False),
        "pause":    ("active", "active",     True),
        "resume":   ("active", "active",     False),
        "close":    ("active", "closed",     None),
        "cancel":   (None,    "cancelled",  None),
    }
    if action not in transitions:
        raise HTTPException(status_code=400, detail="Action invalide.")
    from_status, to_status, paused = transitions[action]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            m = await conn.fetchrow(
                "SELECT * FROM forecast_markets WHERE market_id = $1 FOR UPDATE",
                market_id,
            )
            if not m:
                raise HTTPException(status_code=404, detail="Marché introuvable.")
            if from_status and m["status"] != from_status:
                raise HTTPException(
                    status_code=400,
                    detail=f"Action '{action}' impossible depuis le statut '{m['status']}'.",
                )
            sets = ["status = $1", "updated_at = $2"]
            params: list = [to_status, datetime.now(timezone.utc)]
            if paused is not None:
                sets.append(f"is_paused = ${len(params) + 1}")
                params.append(paused)
            params.append(market_id)
            await conn.execute(
                f"UPDATE forecast_markets SET {', '.join(sets)} "
                f"WHERE market_id = ${len(params)}",
                *params,
            )
            if action == "cancel":
                await _refund_all_bets(conn, market_id, reason="market_cancelled")
    return await get_market(market_id)


async def admin_resolve_market(market_id: str, winning_option_id: str,
                                result_notes: str, source_reference: str,
                                admin_id: str) -> dict:
    """Mark the winning option, settle every placed bet, pay out winners."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            m = await conn.fetchrow(
                "SELECT * FROM forecast_markets WHERE market_id = $1 FOR UPDATE",
                market_id,
            )
            if not m:
                raise HTTPException(status_code=404, detail="Marché introuvable.")
            if m["status"] not in ("closed", "active"):
                raise HTTPException(status_code=400, detail="Marché non résolvable.")
            opt = await conn.fetchrow(
                "SELECT * FROM forecast_options WHERE option_id = $1 AND market_id = $2",
                winning_option_id, market_id,
            )
            if not opt:
                raise HTTPException(status_code=404, detail="Option gagnante invalide.")

            now = datetime.now(timezone.utc)
            await conn.execute(
                "UPDATE forecast_markets SET status='resolved', resolves_at=$1, updated_at=$1 "
                "WHERE market_id = $2",
                now, market_id,
            )
            await conn.execute(
                "UPDATE forecast_options SET is_winner = (option_id = $1) WHERE market_id = $2",
                winning_option_id, market_id,
            )
            await conn.execute("""
                INSERT INTO forecast_results
                  (market_id, winning_option_id, result_notes, source_reference, resolved_by, resolved_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (market_id) DO UPDATE
                  SET winning_option_id = EXCLUDED.winning_option_id,
                      result_notes      = EXCLUDED.result_notes,
                      source_reference  = EXCLUDED.source_reference,
                      resolved_by       = EXCLUDED.resolved_by,
                      resolved_at       = EXCLUDED.resolved_at
            """, market_id, winning_option_id,
               (result_notes or "")[:2000], (source_reference or "")[:500],
               admin_id, now)

            # Mark losers.
            await conn.execute("""
                UPDATE forecast_bets SET status='lost', resolved_at=$1, updated_at=$1
                WHERE market_id = $2 AND status = 'placed' AND option_id <> $3
            """, now, market_id, winning_option_id)

            # Pay winners. We loop because each row updates a different wallet.
            # iter241a-share — also pay X% referral commission to each
            # winning bettor's referrer (if any).
            # iter241a-share-tiers — Power-sharer boost: if the referrer
            # earned more than `power_sharer_threshold` winning referrals in
            # the trailing `power_sharer_window_days`, apply
            # `power_sharer_commission_percent` instead of the standard rate.
            settings_row = await conn.fetchrow("""
                SELECT referral_commission_percent,
                       power_sharer_threshold,
                       power_sharer_commission_percent,
                       power_sharer_window_days
                FROM forecast_settings WHERE id = 1
            """)
            standard_pct = Decimal(str((settings_row and settings_row["referral_commission_percent"]) or 0))
            boosted_pct  = Decimal(str((settings_row and settings_row["power_sharer_commission_percent"]) or 0))
            ps_threshold = int((settings_row and settings_row["power_sharer_threshold"]) or 10)
            ps_window    = int((settings_row and settings_row["power_sharer_window_days"]) or 30)
            winners = await conn.fetch("""
                SELECT bet_id, user_id, stake_amount, potential_payout, referrer_id
                FROM forecast_bets
                WHERE market_id = $1 AND option_id = $2 AND status = 'placed'
            """, market_id, winning_option_id)
            for w in winners:
                # 1) Pay the bettor's potential payout.
                await conn.execute(
                    "UPDATE wallets SET balance = balance + $1, updated_at = $2 "
                    "WHERE user_id = $3",
                    w["potential_payout"], now, w["user_id"],
                )
                # 2) Pay referral commission to the referrer (on the original
                # stake), if any. Tier is computed BEFORE this win is settled
                # so the referrer can't tip themselves over the threshold
                # mid-resolution (fairness + idempotency).
                commission = Decimal("0")
                commission_pct = Decimal("0")
                if w["referrer_id"] and (standard_pct > 0 or boosted_pct > 0):
                    won_count = await _count_winning_referrals_window(
                        conn, w["referrer_id"], ps_window,
                    )
                    _tier, commission_pct = _tier_for(
                        won_count, ps_threshold, standard_pct, boosted_pct,
                    )
                    commission = (Decimal(str(w["stake_amount"])) * commission_pct
                                  / Decimal("100")).quantize(Decimal("0.0001"))
                    if commission > 0:
                        updated = await conn.fetchval(
                            "UPDATE wallets SET balance = balance + $1, updated_at = $2 "
                            "WHERE user_id = $3 RETURNING 1",
                            commission, now, w["referrer_id"],
                        )
                        if updated:
                            await conn.execute("""
                                INSERT INTO transactions
                                  (tx_id, to_user_id, type, amount, currency, status, notes, reference)
                                VALUES ($1, $2, 'forecast_referral_commission',
                                        $3, 'USD', 'completed', $4, $5)
                            """, _new_id("tx"), w["referrer_id"], commission,
                               f"[Prédiction · commission filleul {commission_pct}%] {m['title'][:50]}",
                               w["bet_id"])
                        else:
                            commission = Decimal("0")  # referrer has no wallet → no commission
                await conn.execute("""
                    UPDATE forecast_bets
                    SET status='won', payout_amount=$1, referral_commission=$2,
                        resolved_at=$3, updated_at=$3
                    WHERE bet_id = $4
                """, w["potential_payout"], commission, now, w["bet_id"])
                await conn.execute("""
                    INSERT INTO transactions
                      (tx_id, to_user_id, type, amount, currency, status, notes, reference)
                    VALUES ($1, $2, 'forecast_win', $3, 'USD', 'completed', $4, $5)
                """, _new_id("tx"), w["user_id"], w["potential_payout"],
                   f"[Prédiction gagnée] {m['title'][:60]} → {opt['label'][:40]}",
                   w["bet_id"])
    logger.info("[iter241a] market %s resolved by %s — winner=%s", market_id, admin_id, winning_option_id)
    return await get_market(market_id)


async def _refund_all_bets(conn, market_id: str, reason: str = "refund") -> int:
    """Refund every placed bet on this market in the same transaction.
    The caller MUST already hold `conn.transaction()` open. Returns the
    number of refunded bets."""
    now = datetime.now(timezone.utc)
    bets = await conn.fetch("""
        SELECT bet_id, user_id, stake_amount FROM forecast_bets
        WHERE market_id = $1 AND status = 'placed'
    """, market_id)
    for b in bets:
        await conn.execute(
            "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
            b["stake_amount"], now, b["user_id"],
        )
        await conn.execute("""
            UPDATE forecast_bets
            SET status='refunded', payout_amount=$1, resolved_at=$2, updated_at=$2
            WHERE bet_id = $3
        """, b["stake_amount"], now, b["bet_id"])
        await conn.execute("""
            INSERT INTO transactions
              (tx_id, to_user_id, type, amount, currency, status, notes, reference)
            VALUES ($1, $2, 'forecast_refund', $3, 'USD', 'completed', $4, $5)
        """, _new_id("tx"), b["user_id"], b["stake_amount"],
           f"[Prédiction remboursée — {reason}]", b["bet_id"])
    return len(bets)


async def admin_market_exposure(market_id: str) -> dict:
    """Returns the worst-case payout the platform owes per option, so the
    admin can spot a market that's about to lose money."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        m = await conn.fetchrow("SELECT * FROM forecast_markets WHERE market_id = $1", market_id)
        if not m:
            raise HTTPException(status_code=404, detail="Marché introuvable.")
        opts = await conn.fetch("""
            SELECT o.option_id, o.label, o.multiplier, o.display_order,
                   COALESCE(SUM(b.stake_amount), 0) AS staked,
                   COALESCE(SUM(b.net_stake * o.multiplier), 0) AS worst_payout,
                   COUNT(b.id) AS n_bets
            FROM forecast_options o
            LEFT JOIN forecast_bets b
              ON b.option_id = o.option_id AND b.status = 'placed'
            WHERE o.market_id = $1
            GROUP BY o.option_id, o.label, o.multiplier, o.display_order, o.id
            ORDER BY o.display_order, o.id
        """, market_id)
        total_staked = await conn.fetchval("""
            SELECT COALESCE(SUM(stake_amount), 0) FROM forecast_bets
            WHERE market_id = $1 AND status = 'placed'
        """, market_id) or 0
        total_fees = await conn.fetchval("""
            SELECT COALESCE(SUM(platform_fee), 0) FROM forecast_bets
            WHERE market_id = $1 AND status = 'placed'
        """, market_id) or 0

    options_breakdown = [_row_to_dict(o) for o in opts]
    worst_case = max((o["worst_payout"] for o in options_breakdown), default=0.0)
    net_exposure = float(worst_case) - float(total_staked)
    return {
        "market_id":     market_id,
        "options":       options_breakdown,
        "total_staked":  float(total_staked),
        "total_fees":    float(total_fees),
        "worst_payout":  float(worst_case),
        "net_exposure":  net_exposure,
        "max_exposure_limit": float(m["max_exposure"]),
        "exposure_ratio_pct": round(
            (net_exposure / float(m["max_exposure"]) * 100) if m["max_exposure"] else 0, 2,
        ),
    }


async def admin_update_market(market_id: str, payload: dict) -> dict:
    """Patch metadata + limits. Title/desc/limits editable any time; closes_at
    & options editable ONLY while draft & no bets exist (otherwise we'd alter
    the rules people already placed bets on)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            m = await conn.fetchrow(
                "SELECT * FROM forecast_markets WHERE market_id = $1 FOR UPDATE",
                market_id,
            )
            if not m:
                raise HTTPException(status_code=404, detail="Marché introuvable.")
            if m["status"] in ("resolved", "cancelled"):
                raise HTTPException(status_code=400,
                                     detail=f"Marché {m['status']} non modifiable.")

            has_bets = await conn.fetchval(
                "SELECT COUNT(*) FROM forecast_bets WHERE market_id = $1 AND status='placed'",
                market_id,
            ) or 0

            # Build allowed-fields UPDATE.
            allowed = {
                "title", "description", "category", "source_label", "source_url",
                "min_bet", "max_bet", "max_bet_per_user", "max_exposure",
                "platform_fee_percent",
            }
            if has_bets == 0 and m["status"] == "draft":
                allowed.add("closes_at")  # safe to retime an empty draft

            sets, args = [], []
            for k, v in (payload or {}).items():
                if k not in allowed:
                    continue
                if k == "category" and v not in VALID_CATEGORIES:
                    raise HTTPException(status_code=422, detail="Catégorie invalide.")
                if k == "closes_at" and isinstance(v, str):
                    try:
                        from datetime import datetime as _dt
                        v = _dt.fromisoformat(v.replace("Z", "+00:00"))
                    except ValueError:
                        raise HTTPException(status_code=422, detail="closes_at invalide.")
                sets.append(f"{k} = ${len(args) + 1}")
                args.append(v)
            if sets:
                sets.append(f"updated_at = ${len(args) + 1}")
                args.append(datetime.now(timezone.utc))
                args.append(market_id)
                await conn.execute(
                    f"UPDATE forecast_markets SET {', '.join(sets)} "
                    f"WHERE market_id = ${len(args)}",
                    *args,
                )

            # Replace options only when no bets AND draft.
            options = payload.get("options")
            if options and has_bets == 0 and m["status"] == "draft":
                if len(options) < 2:
                    raise HTTPException(status_code=422, detail="Au moins 2 options requises.")
                await conn.execute(
                    "DELETE FROM forecast_options WHERE market_id = $1", market_id,
                )
                for i, o in enumerate(options):
                    label = (o.get("label") or "").strip()
                    if not label:
                        raise HTTPException(status_code=422,
                                             detail=f"Option #{i+1} sans libellé.")
                    try:
                        mult = float(o.get("multiplier") or 2.0)
                    except (TypeError, ValueError):
                        raise HTTPException(status_code=422,
                                             detail=f"Multiplicateur invalide pour option #{i+1}.")
                    if mult < 1.01 or mult > 100:
                        raise HTTPException(status_code=422,
                                             detail="Multiplicateur doit être entre 1.01 et 100.")
                    await conn.execute("""
                        INSERT INTO forecast_options
                          (option_id, market_id, label, multiplier, display_order)
                        VALUES ($1, $2, $3, $4, $5)
                    """, _new_id("opt"), market_id, label, mult, i)
    return await get_market(market_id)


async def admin_delete_market(market_id: str) -> None:
    """Hard-delete a market — only while draft (no bets) or cancelled
    (already refunded). Resolved/active markets must stay for audit."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            m = await conn.fetchrow(
                "SELECT status FROM forecast_markets WHERE market_id = $1 FOR UPDATE",
                market_id,
            )
            if not m:
                raise HTTPException(status_code=404, detail="Marché introuvable.")
            if m["status"] not in ("draft", "cancelled"):
                raise HTTPException(
                    status_code=400,
                    detail="Seuls les brouillons et marchés annulés peuvent être supprimés. "
                           "Annulez d'abord le marché (les mises seront remboursées).",
                )
            await conn.execute("DELETE FROM forecast_results WHERE market_id = $1", market_id)
            await conn.execute("DELETE FROM forecast_options WHERE market_id = $1", market_id)
            # Keep `forecast_bets` rows for cancelled markets so the user
            # history page can still show "refunded" entries with context.
            await conn.execute("DELETE FROM forecast_markets WHERE market_id = $1", market_id)


async def list_my_referral_earnings(user_id: str) -> dict:
    """iter241a-share — Aggregate referral commission for a user (as upstream
    referrer). Used by ForecastPage → "Mes paris" → encart commissions."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        agg = await conn.fetchrow("""
            SELECT
              COUNT(*)                                 AS referrals_total,
              COUNT(*) FILTER (WHERE status = 'won')   AS referrals_won,
              COALESCE(SUM(referral_commission), 0)    AS commission_earned,
              COUNT(DISTINCT user_id)                  AS unique_followers
            FROM forecast_bets
            WHERE referrer_id = $1
        """, user_id)
        last = await conn.fetch("""
            SELECT b.bet_id, b.stake_amount, b.referral_commission, b.status,
                   b.created_at, b.resolved_at,
                   b.user_id AS follower_id,
                   m.title AS market_title
            FROM forecast_bets b
            JOIN forecast_markets m ON m.market_id = b.market_id
            WHERE b.referrer_id = $1
            ORDER BY b.created_at DESC
            LIMIT 30
        """, user_id)
        settings = await conn.fetchrow(
            "SELECT referral_commission_percent, power_sharer_threshold, "
            "power_sharer_commission_percent, power_sharer_window_days "
            "FROM forecast_settings WHERE id = 1",
        )
        standard_pct = float((settings and settings["referral_commission_percent"]) or 10.0)
        boosted_pct  = float((settings and settings["power_sharer_commission_percent"]) or 15.0)
        threshold    = int((settings and settings["power_sharer_threshold"]) or 10)
        window_days  = int((settings and settings["power_sharer_window_days"]) or 30)
        # iter241a-share-tiers — Live tier of this user (used for the
        # "Power-sharer" banner above the earnings card).
        won_in_window = await _count_winning_referrals_window(
            conn, user_id, window_days,
        )
    is_power = won_in_window > threshold
    commission_percent = boosted_pct if is_power else standard_pct
    return {
        "commission_percent": commission_percent,
        "standard_percent":   standard_pct,
        "boosted_percent":    boosted_pct,
        "tier":               "power_sharer" if is_power else "standard",
        "winning_referrals_window": won_in_window,
        "threshold":          threshold,
        "window_days":        window_days,
        "is_forecast_influencer": is_power,
        "referrals_total":    agg["referrals_total"] or 0,
        "referrals_won":      agg["referrals_won"] or 0,
        "unique_followers":   agg["unique_followers"] or 0,
        "commission_earned":  float(agg["commission_earned"] or 0),
        "recent":             [_row_to_dict(r) for r in last],
    }


async def admin_metrics() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
              (SELECT COUNT(*) FROM forecast_markets)                          AS markets_total,
              (SELECT COUNT(*) FROM forecast_markets WHERE status='active')    AS markets_active,
              (SELECT COUNT(*) FROM forecast_markets WHERE status='resolved')  AS markets_resolved,
              (SELECT COUNT(*) FROM forecast_bets)                             AS bets_total,
              (SELECT COALESCE(SUM(stake_amount),0)  FROM forecast_bets WHERE status IN ('placed','won','lost')) AS total_staked,
              (SELECT COALESCE(SUM(platform_fee),0)  FROM forecast_bets WHERE status IN ('placed','won','lost')) AS total_fees,
              (SELECT COALESCE(SUM(payout_amount),0) FROM forecast_bets WHERE status='won')                       AS total_payouts,
              (SELECT COUNT(DISTINCT user_id) FROM forecast_bets) AS unique_users
        """)
    out = dict(row) if row else {}
    for k, v in list(out.items()):
        if isinstance(v, Decimal):
            out[k] = float(v)
    return out
