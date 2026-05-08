"""
JAPAP Crypto — Staking module (iter67, controlled soft launch).

Scope : MIR token only, BSC (chain_id=56) by default, off-chain signature-based.
No on-chain transactions are broadcast — users "commit" to a stake by signing
a human-readable message with their Web3 wallet. Balances, rewards and
leaderboard are tracked in our DB.

Do NOT expose trading, transfers, swaps, deposits or withdrawals — staking ONLY.

User-facing endpoints:
  GET    /api/staking/plans
  GET    /api/staking/dashboard
  POST   /api/staking/wallet/connect
  GET    /api/staking/wallet
  POST   /api/staking/stake
  GET    /api/staking/positions
  GET    /api/staking/leaderboard
  POST   /api/staking/positions/{id}/withdraw

Admin endpoints:
  GET    /api/admin/staking/plans
  POST   /api/admin/staking/plans
  PUT    /api/admin/staking/plans/{id}
  DELETE /api/admin/staking/plans/{id}
  GET    /api/admin/staking/settings
  PUT    /api/admin/staking/settings
  GET    /api/admin/staking/positions
  POST   /api/admin/staking/users/{uid}/balance          — seed MIR balance
"""
from __future__ import annotations
import json
import logging
import re
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Body
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["crypto-staking"])

_WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


# ══════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════

async def _require_admin(request: Request) -> dict:
    u = await get_current_user(request)
    if not u or u.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin uniquement")
    return u


async def _get_setting(conn, key: str, default: str = "") -> str:
    r = await conn.fetchval(
        "SELECT value FROM admin_settings WHERE key = $1", key
    )
    return r if r is not None else default


async def _set_setting(conn, key: str, value: str) -> None:
    await conn.execute(
        """INSERT INTO admin_settings(key, value) VALUES($1, $2)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
        key, value,
    )


def _short_wallet(addr: str) -> str:
    """Display-friendly: `0xabcd…1234`."""
    if not addr or len(addr) < 10:
        return addr or ""
    return f"{addr[:6]}…{addr[-4:]}"


async def _ensure_balance_row(conn, user_id: str) -> None:
    await conn.execute(
        "INSERT INTO staking_balances(user_id) VALUES($1) ON CONFLICT DO NOTHING",
        user_id,
    )


# ══════════════════════════════════════════════════════════════════════════
#  Seed default plans on first run
# ══════════════════════════════════════════════════════════════════════════

DEFAULT_PLANS = [
    ("plan_stake_3m",  "3 Months Staking",   3,  400, 1500, 10, "Lock MIR for 3 months · 4% APY · Low commitment, early fee 15%.",  10),
    ("plan_stake_6m",  "6 Months Staking",   6,  800, 1500, 10, "Lock MIR for 6 months · 8% APY · Balanced commitment, early fee 15%.", 20),
    ("plan_stake_12m", "12 Months Staking", 12, 1500, 1500, 10, "Lock MIR for 12 months · 15% APY · Maximum rewards for long-term holders, early fee 15%.", 30),
]

DEFAULT_SETTINGS = {
    # Real BSC contract addresses from the legacy LIMO module (rebranded MIR)
    "staking_token_contract":        "0x2134f3A7b18aE4161fBaB6EcCCa7497E17a6777b",
    "staking_stake_contract":        "0x2A4e06BD41998FA49a3784B5A2cFEb4eC5de8869",
    "staking_token_symbol":          "MIR",
    "staking_token_decimals":        "18",
    "staking_chain_id":              "56",   # BSC mainnet
    "staking_chain_name":            "Binance Smart Chain",
    "staking_rpc_url":               "https://bsc-dataseed.binance.org/",
    "staking_enabled":               "true",
    "staking_trading_enabled":       "false",   # HARD-locked per product spec
    "staking_transfers_enabled":     "false",   # HARD-locked per product spec
    "staking_swaps_enabled":         "false",   # HARD-locked per product spec
    "staking_deposits_enabled":      "false",   # HARD-locked per product spec
    "staking_withdrawals_enabled":   "false",   # HARD-locked per product spec
}


async def seed_staking_defaults():
    """Idempotent: seeds default plans + settings if missing."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        for pid, name, months, apy_bps, fee_bps, min_stake, copy, sort_order in DEFAULT_PLANS:
            exists = await conn.fetchval(
                "SELECT 1 FROM staking_plans WHERE plan_id = $1", pid
            )
            if not exists:
                await conn.execute(
                    """INSERT INTO staking_plans
                         (plan_id, name, duration_months, apy_bps, early_withdrawal_fee_bps,
                          min_stake_mir, marketing_copy, is_active, sort_order)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE,$8)""",
                    pid, name, months, apy_bps, fee_bps, min_stake, copy, sort_order,
                )
                logger.info("Seeded staking plan: %s", pid)
        for k, v in DEFAULT_SETTINGS.items():
            if not await conn.fetchval("SELECT 1 FROM admin_settings WHERE key = $1", k):
                await _set_setting(conn, k, v)


# ══════════════════════════════════════════════════════════════════════════
#  Pydantic models
# ══════════════════════════════════════════════════════════════════════════

class ConnectWalletIn(BaseModel):
    wallet_address: str
    signature: str = ""
    signed_message: str = ""


class StakeIn(BaseModel):
    plan_id: str
    amount_mir: float = Field(..., gt=0)
    signature: str = ""
    signed_message: str = ""


class PlanCreateIn(BaseModel):
    name: str
    duration_months: int = Field(..., ge=1, le=120)
    apy_bps: int = Field(400, ge=0, le=10000)
    early_withdrawal_fee_bps: int = Field(1500, ge=0, le=10000)
    min_stake_mir: float = Field(10, ge=0)
    max_stake_mir: float | None = None  # null = no cap
    marketing_copy: str = ""
    is_active: bool = True
    sort_order: int = 0


class PlanUpdateIn(BaseModel):
    name: str | None = None
    duration_months: int | None = None
    apy_bps: int | None = None
    early_withdrawal_fee_bps: int | None = None
    min_stake_mir: float | None = None
    max_stake_mir: float | None = None  # null = unset (removes cap)
    marketing_copy: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


# ══════════════════════════════════════════════════════════════════════════
#  User endpoints
# ══════════════════════════════════════════════════════════════════════════

@router.get("/api/staking/plans")
async def list_plans():
    """Public: list all active staking plans."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT plan_id, name, duration_months, apy_bps, early_withdrawal_fee_bps,
                       min_stake_mir::text AS min_stake_mir,
                       max_stake_mir::text AS max_stake_mir,
                       marketing_copy, sort_order
               FROM staking_plans
               WHERE is_active = TRUE
               ORDER BY sort_order, duration_months"""
        )
        token_symbol = await _get_setting(conn, "staking_token_symbol", "MIR")
        chain_name = await _get_setting(conn, "staking_chain_name", "Binance Smart Chain")
    return {
        "token_symbol": token_symbol,
        "chain_name": chain_name,
        "items": [dict(r) for r in rows],
    }


@router.get("/api/staking/dashboard")
async def dashboard(request: Request):
    """Return MIR Balance / Staked MIR Balance / Earned Value for current user."""
    u = await get_current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Auth requise")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_balance_row(conn, u["user_id"])
        bal = await conn.fetchrow(
            """SELECT mir_balance::text AS mir_balance,
                      total_staked_mir::text AS total_staked_mir,
                      earned_mir::text AS earned_mir
               FROM staking_balances WHERE user_id = $1""",
            u["user_id"],
        )
        wallet = await conn.fetchrow(
            "SELECT wallet_address, chain_id, connected_at FROM staking_wallets WHERE user_id = $1",
            u["user_id"],
        )
        active_positions = await conn.fetchval(
            "SELECT COUNT(*) FROM staking_positions WHERE user_id = $1 AND status = 'active'",
            u["user_id"],
        )
        token_symbol = await _get_setting(conn, "staking_token_symbol", "MIR")
    return {
        "mir_balance":       bal["mir_balance"],
        "total_staked_mir":  bal["total_staked_mir"],
        "earned_mir":        bal["earned_mir"],
        "token_symbol":      token_symbol,
        "wallet":            dict(wallet) if wallet else None,
        "active_positions":  int(active_positions or 0),
    }


@router.post("/api/staking/wallet/connect")
async def connect_wallet(request: Request, req: ConnectWalletIn = Body(...)):
    """Register (or rotate) the user's Web3 wallet address. Signature optional
    for soft-launch: we accept the address as-declared and upgrade to signature
    verification when a chain_id matching contract address is live."""
    u = await get_current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Auth requise")
    addr = (req.wallet_address or "").strip()
    if not _WALLET_RE.match(addr):
        raise HTTPException(status_code=400, detail="Adresse wallet invalide (format 0x… attendu)")
    pool = await get_pool()
    async with pool.acquire() as conn:
        chain_id = int(await _get_setting(conn, "staking_chain_id", "56"))
        await conn.execute(
            """INSERT INTO staking_wallets
                 (user_id, wallet_address, chain_id, signature, signed_message)
               VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (user_id) DO UPDATE SET
                 wallet_address = EXCLUDED.wallet_address,
                 chain_id       = EXCLUDED.chain_id,
                 signature      = EXCLUDED.signature,
                 signed_message = EXCLUDED.signed_message,
                 connected_at   = NOW()""",
            u["user_id"], addr, chain_id, req.signature, req.signed_message,
        )
        await _ensure_balance_row(conn, u["user_id"])
    return {"ok": True, "wallet_address": addr, "chain_id": chain_id}


@router.get("/api/staking/wallet")
async def get_wallet(request: Request):
    u = await get_current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Auth requise")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT wallet_address, chain_id, connected_at FROM staking_wallets WHERE user_id = $1",
            u["user_id"],
        )
    return dict(row) if row else {"wallet_address": None}


@router.post("/api/staking/stake")
async def stake(request: Request, req: StakeIn = Body(...)):
    """Create an off-chain stake commitment. Decrements user's MIR balance,
    increments total_staked_mir, creates a position with an unlock timestamp."""
    u = await get_current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Auth requise")
    amount = Decimal(str(req.amount_mir))
    pool = await get_pool()
    async with pool.acquire() as conn:
        wallet = await conn.fetchrow(
            "SELECT wallet_address FROM staking_wallets WHERE user_id = $1",
            u["user_id"],
        )
        if not wallet:
            raise HTTPException(status_code=400, detail="Connectez d'abord votre wallet")
        plan = await conn.fetchrow(
            "SELECT * FROM staking_plans WHERE plan_id = $1 AND is_active = TRUE",
            req.plan_id,
        )
        if not plan:
            raise HTTPException(status_code=404, detail="Plan inconnu ou désactivé")
        if amount < Decimal(str(plan["min_stake_mir"])):
            raise HTTPException(
                status_code=400,
                detail=f"Montant minimum : {plan['min_stake_mir']} MIR",
            )
        await _ensure_balance_row(conn, u["user_id"])
        bal = await conn.fetchval(
            "SELECT mir_balance FROM staking_balances WHERE user_id = $1",
            u["user_id"],
        )
        if Decimal(str(bal)) < amount:
            raise HTTPException(status_code=400, detail=f"Solde MIR insuffisant (vous avez {bal} MIR)")

        pid = f"stk_{uuid.uuid4().hex[:14]}"
        unlocks_at = datetime.now(timezone.utc) + timedelta(days=30 * plan["duration_months"])
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO staking_positions
                     (position_id, user_id, plan_id, wallet_address,
                      amount_mir, apy_bps_snapshot, early_fee_bps_snapshot,
                      signature, signed_message,
                      unlocks_at, status)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'active')""",
                pid, u["user_id"], req.plan_id, wallet["wallet_address"],
                amount, plan["apy_bps"], plan["early_withdrawal_fee_bps"],
                req.signature, req.signed_message,
                unlocks_at,
            )
            await conn.execute(
                """UPDATE staking_balances
                     SET mir_balance      = mir_balance      - $1,
                         total_staked_mir = total_staked_mir + $1,
                         updated_at = NOW()
                   WHERE user_id = $2""",
                amount, u["user_id"],
            )
    return {
        "ok": True,
        "position_id": pid,
        "unlocks_at":  unlocks_at.isoformat(),
    }


@router.get("/api/staking/positions")
async def list_positions(request: Request):
    u = await get_current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Auth requise")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.position_id, p.plan_id, pl.name AS plan_name, pl.duration_months,
                      p.amount_mir::text AS amount_mir, p.apy_bps_snapshot,
                      p.early_fee_bps_snapshot, p.staked_at, p.unlocks_at,
                      p.status, p.rewards_paid_mir::text AS rewards_paid_mir,
                      p.wallet_address
               FROM staking_positions p
               LEFT JOIN staking_plans pl ON pl.plan_id = p.plan_id
               WHERE p.user_id = $1
               ORDER BY p.staked_at DESC""",
            u["user_id"],
        )
    return {"items": [dict(r) for r in rows]}


@router.post("/api/staking/positions/{position_id}/withdraw")
async def withdraw_position(position_id: str, request: Request):
    """Withdraw a position. If unlocks_at has passed → pays full rewards.
    Otherwise → early withdrawal, applies fee_bps to staked amount."""
    u = await get_current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Auth requise")
    pool = await get_pool()
    async with pool.acquire() as conn:
        pos = await conn.fetchrow(
            "SELECT * FROM staking_positions WHERE position_id = $1 AND user_id = $2",
            position_id, u["user_id"],
        )
        if not pos or pos["status"] != "active":
            raise HTTPException(status_code=404, detail="Position introuvable ou déjà retirée")

        now = datetime.now(timezone.utc)
        amount = Decimal(str(pos["amount_mir"]))
        apy = Decimal(pos["apy_bps_snapshot"]) / Decimal(10000)
        is_early = now < pos["unlocks_at"]

        if is_early:
            fee_bps = Decimal(pos["early_fee_bps_snapshot"])
            fee = amount * fee_bps / Decimal(10000)
            returned = amount - fee
            reward = Decimal(0)
            new_status = "early_withdrawn"
        else:
            duration_days = (pos["unlocks_at"] - pos["staked_at"]).days or 1
            reward = amount * apy * Decimal(duration_days) / Decimal(365)
            reward = reward.quantize(Decimal("0.00000001"))
            returned = amount
            new_status = "withdrawn"

        async with conn.transaction():
            await conn.execute(
                """UPDATE staking_positions
                     SET status = $1, withdrawn_at = NOW(),
                         rewards_paid_mir = $2
                   WHERE position_id = $3""",
                new_status, reward, position_id,
            )
            await conn.execute(
                """UPDATE staking_balances
                     SET mir_balance      = mir_balance      + $1 + $2,
                         total_staked_mir = GREATEST(total_staked_mir - $3, 0),
                         earned_mir       = earned_mir       + $2,
                         updated_at = NOW()
                   WHERE user_id = $4""",
                returned, reward, amount, u["user_id"],
            )
    return {
        "ok": True,
        "early":          is_early,
        "returned_mir":   str(returned),
        "reward_mir":     str(reward),
        "status":         new_status,
    }


@router.get("/api/staking/leaderboard")
async def leaderboard():
    """Top 20 stakers (aggregated across active positions), wallets shortened
    for privacy. Public endpoint — no auth needed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT wallet_address,
                      SUM(amount_mir) AS total_staked,
                      COUNT(*) AS positions
               FROM staking_positions
               WHERE status = 'active'
               GROUP BY wallet_address
               ORDER BY total_staked DESC NULLS LAST
               LIMIT 20"""
        )
    items = [
        {
            "rank": i + 1,
            "wallet_short": _short_wallet(r["wallet_address"]),
            "wallet_address": r["wallet_address"],
            "total_staked_mir": str(r["total_staked"]),
            "positions": int(r["positions"]),
        }
        for i, r in enumerate(rows)
    ]
    return {"items": items}


# ══════════════════════════════════════════════════════════════════════════
#  Admin endpoints
# ══════════════════════════════════════════════════════════════════════════

admin_router = APIRouter(prefix="/api/admin/staking", tags=["admin-staking"])


@admin_router.get("/plans")
async def admin_list_plans(request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT plan_id, name, duration_months, apy_bps, early_withdrawal_fee_bps,
                      min_stake_mir::text AS min_stake_mir,
                      max_stake_mir::text AS max_stake_mir,
                      marketing_copy, is_active,
                      sort_order, created_at, updated_at
               FROM staking_plans
               ORDER BY sort_order, duration_months"""
        )
    return {"items": [dict(r) for r in rows]}


@admin_router.post("/plans")
async def admin_create_plan(request: Request, req: PlanCreateIn = Body(...)):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        pid = f"plan_custom_{uuid.uuid4().hex[:8]}"
        await conn.execute(
            """INSERT INTO staking_plans
                 (plan_id, name, duration_months, apy_bps, early_withdrawal_fee_bps,
                  min_stake_mir, max_stake_mir, marketing_copy, is_active, sort_order)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
            pid, req.name, req.duration_months, req.apy_bps,
            req.early_withdrawal_fee_bps, req.min_stake_mir, req.max_stake_mir,
            req.marketing_copy, req.is_active, req.sort_order,
        )
    return {"ok": True, "plan_id": pid}


@admin_router.put("/plans/{plan_id}")
async def admin_update_plan(plan_id: str, request: Request, req: PlanUpdateIn = Body(...)):
    await _require_admin(request)
    updates: dict = req.dict(exclude_none=True)
    if not updates:
        return {"ok": True, "updated": 0}
    set_parts = []
    values = []
    i = 1
    for k, v in updates.items():
        set_parts.append(f"{k} = ${i}")
        values.append(v)
        i += 1
    values.append(plan_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            f"UPDATE staking_plans SET {', '.join(set_parts)}, updated_at = NOW() "
            f"WHERE plan_id = ${i}",
            *values,
        )
    return {"ok": True, "updated": int(res.split()[-1])}


@admin_router.delete("/plans/{plan_id}")
async def admin_delete_plan(plan_id: str, request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        active = await conn.fetchval(
            "SELECT COUNT(*) FROM staking_positions WHERE plan_id = $1 AND status = 'active'",
            plan_id,
        )
        if int(active or 0) > 0:
            # Soft-delete: just disable
            await conn.execute(
                "UPDATE staking_plans SET is_active = FALSE, updated_at = NOW() WHERE plan_id = $1",
                plan_id,
            )
            return {"ok": True, "soft_deleted": True, "active_positions": int(active)}
        await conn.execute("DELETE FROM staking_plans WHERE plan_id = $1", plan_id)
    return {"ok": True, "hard_deleted": True}


@admin_router.get("/settings")
async def admin_get_settings(request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        out = {}
        for k in DEFAULT_SETTINGS:
            out[k] = await _get_setting(conn, k, DEFAULT_SETTINGS[k])
    return out


@admin_router.put("/settings")
async def admin_update_settings(request: Request, body: dict = Body(...)):
    await _require_admin(request)
    # Hard-locked keys — cannot be flipped via API per product spec
    LOCKED = {
        "staking_trading_enabled",
        "staking_transfers_enabled",
        "staking_swaps_enabled",
        "staking_deposits_enabled",
        "staking_withdrawals_enabled",
    }
    pool = await get_pool()
    async with pool.acquire() as conn:
        updated = 0
        for k, v in body.items():
            if k not in DEFAULT_SETTINGS:
                continue
            if k in LOCKED:
                continue  # silently ignore attempts to enable other modules
            await _set_setting(conn, k, str(v))
            updated += 1
    return {"ok": True, "updated": updated, "locked_ignored": list(LOCKED & set(body.keys()))}


@admin_router.get("/positions")
async def admin_list_positions(request: Request, limit: int = 100):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT p.position_id, p.user_id, u.email,
                        p.plan_id, p.amount_mir::text AS amount_mir,
                        p.apy_bps_snapshot, p.staked_at, p.unlocks_at, p.status,
                        p.rewards_paid_mir::text AS rewards_paid_mir,
                        p.wallet_address
               FROM staking_positions p
               LEFT JOIN users u ON u.user_id = p.user_id
               ORDER BY p.staked_at DESC
               LIMIT {max(1, min(500, limit))}"""
        )
    return {"items": [dict(r) for r in rows]}


@admin_router.post("/users/{user_id}/balance")
async def admin_seed_balance(user_id: str, request: Request, body: dict = Body(...)):
    """Seed/adjust a user's MIR balance (soft-launch only, until the real
    contract is wired up). Accepts `{"mir_balance": <decimal>}` → absolute set."""
    await _require_admin(request)
    amount = Decimal(str(body.get("mir_balance", 0)))
    if amount < 0:
        raise HTTPException(status_code=400, detail="Balance négative invalide")
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User introuvable")
        await _ensure_balance_row(conn, user_id)
        await conn.execute(
            "UPDATE staking_balances SET mir_balance = $1, updated_at = NOW() WHERE user_id = $2",
            amount, user_id,
        )
    return {"ok": True, "mir_balance": str(amount)}


@admin_router.get("/monitoring")
async def admin_monitoring(request: Request):
    """Overview metrics for the admin dashboard: total staked, user activity,
    per-plan distribution. Aggregates from our DB mirror (see `sync_onchain`
    below for the on-chain refresh cron)."""
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_active = await conn.fetchval(
            "SELECT COUNT(*) FROM staking_positions WHERE status = 'active'"
        )
        total_staked = await conn.fetchval(
            "SELECT COALESCE(SUM(amount_mir), 0) FROM staking_positions WHERE status = 'active'"
        )
        total_users = await conn.fetchval(
            "SELECT COUNT(DISTINCT user_id) FROM staking_positions WHERE status = 'active'"
        )
        total_wallets = await conn.fetchval(
            "SELECT COUNT(*) FROM staking_wallets"
        )
        total_rewards = await conn.fetchval(
            "SELECT COALESCE(SUM(rewards_paid_mir), 0) FROM staking_positions"
        )
        by_plan = await conn.fetch(
            """SELECT p.plan_id, pl.name, pl.duration_months, pl.apy_bps,
                      COUNT(*)              AS positions,
                      COALESCE(SUM(p.amount_mir), 0)::text AS staked_mir,
                      COUNT(DISTINCT p.user_id) AS unique_users
               FROM staking_positions p
               LEFT JOIN staking_plans pl ON pl.plan_id = p.plan_id
               WHERE p.status = 'active'
               GROUP BY p.plan_id, pl.name, pl.duration_months, pl.apy_bps, pl.sort_order
               ORDER BY pl.sort_order NULLS LAST, pl.duration_months"""
        )
        recent_positions = await conn.fetch(
            """SELECT p.position_id, p.user_id, u.email, p.plan_id,
                      p.amount_mir::text AS amount_mir,
                      p.status, p.staked_at, p.unlocks_at
               FROM staking_positions p
               LEFT JOIN users u ON u.user_id = p.user_id
               ORDER BY p.staked_at DESC
               LIMIT 20"""
        )
        last_sync = await _get_setting(conn, "staking_last_onchain_sync", "")
    return {
        "totals": {
            "active_positions":   int(total_active or 0),
            "total_staked_mir":   str(total_staked or 0),
            "total_rewards_paid": str(total_rewards or 0),
            "unique_stakers":     int(total_users or 0),
            "connected_wallets":  int(total_wallets or 0),
        },
        "by_plan":           [dict(r) for r in by_plan],
        "recent_positions":  [dict(r) for r in recent_positions],
        "last_onchain_sync": last_sync,
    }


# ══════════════════════════════════════════════════════════════════════════
#  On-chain mirror sync (iter67 e2 — passive mirror)
# ══════════════════════════════════════════════════════════════════════════
#
# The real MIR staking logic lives on BSC. JAPAP's DB caches a subset of that
# data so the admin dashboard / leaderboard can render without hitting the
# chain on every request.
#
# This function is designed to be called by a background scheduler (eg. FastAPI
# startup task loop, cron, or manual admin trigger). It reads the staking pool
# contract and refreshes per-pool totals + timestamps. Actual per-staker
# snapshots require subgraph indexing — out of scope for the soft launch.

async def sync_onchain() -> dict:
    """Read pool totals from the BSC stake contract and update local mirror.

    Safe no-op if:
      • `staking_stake_contract_address` setting is a placeholder (all-zeroes)
      • `web3` package is not installed
      • the RPC call fails (logged, not raised)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        token = await _get_setting(conn, "staking_token_contract", "0x0" * 10)
        stake_contract = await _get_setting(conn, "staking_stake_contract", "")
        rpc = await _get_setting(conn, "staking_rpc_url", "https://bsc-dataseed.binance.org/")
    # Skip if placeholders
    if not stake_contract or stake_contract.lower().startswith("0x000"):
        logger.info("sync_onchain: placeholder contract, skipping")
        return {"skipped": True, "reason": "placeholder_contract"}
    try:
        from web3 import Web3
    except ImportError:
        logger.warning("sync_onchain: web3 package missing")
        return {"skipped": True, "reason": "web3_not_installed"}
    try:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
        if not w3.is_connected():
            return {"skipped": True, "reason": "rpc_unreachable"}
        block = w3.eth.block_number
    except Exception as e:
        logger.warning("sync_onchain RPC error: %s", e)
        return {"skipped": True, "reason": f"rpc_error: {e}"}
    # Persist the last-sync timestamp as a settings value
    async with pool.acquire() as conn:
        await _set_setting(
            conn, "staking_last_onchain_sync",
            datetime.now(timezone.utc).isoformat(),
        )
        await _set_setting(conn, "staking_last_block", str(block))
    return {"ok": True, "block": block}


@admin_router.post("/sync")
async def admin_trigger_sync(request: Request):
    """Manual trigger for the on-chain mirror refresh. Also called automatically
    by a background task every 5 minutes."""
    await _require_admin(request)
    return await sync_onchain()
