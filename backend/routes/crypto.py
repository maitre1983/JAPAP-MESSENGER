import uuid
import logging
import httpx
import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/crypto", tags=["crypto"])

# CoinGecko coin IDs mapping
COIN_MAP = {
    "BTC": {"id": "bitcoin", "name": "Bitcoin"},
    "ETH": {"id": "ethereum", "name": "Ethereum"},
    "BNB": {"id": "binancecoin", "name": "BNB"},
    "SOL": {"id": "solana", "name": "Solana"},
    "USDT": {"id": "tether", "name": "Tether"},
    "XRP": {"id": "ripple", "name": "Ripple"},
    "ADA": {"id": "cardano", "name": "Cardano"},
    "DOGE": {"id": "dogecoin", "name": "Dogecoin"},
}

# XAF/USD rate (approximate, 1 USD ≈ 610 XAF)
XAF_USD_RATE = 610

# Fallback prices if CoinGecko is unreachable
FALLBACK_PRICES = {
    "BTC": {"usd": 102000}, "ETH": {"usd": 5200}, "BNB": {"usd": 670},
    "SOL": {"usd": 295}, "USDT": {"usd": 1.0}, "XRP": {"usd": 2.35},
    "ADA": {"usd": 0.83}, "DOGE": {"usd": 0.27},
}

STAKING_APY = {
    "BTC": 5.0, "ETH": 8.0, "BNB": 12.0, "SOL": 15.0,
    "USDT": 10.0, "XRP": 7.0, "ADA": 20.0, "DOGE": 25.0,
}

# Cache for CoinGecko prices (5 min)
_price_cache = {"data": None, "ts": 0}


async def fetch_coingecko_prices():
    """Fetch real prices from CoinGecko API, with caching and fallback."""
    now = datetime.now(timezone.utc).timestamp()
    if _price_cache["data"] and (now - _price_cache["ts"]) < 300:
        return _price_cache["data"]

    ids = ",".join([v["id"] for v in COIN_MAP.values()])
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
            )
            if resp.status_code == 200:
                raw = resp.json()
                prices = {}
                for symbol, info in COIN_MAP.items():
                    coin_data = raw.get(info["id"], {})
                    usd_price = coin_data.get("usd", FALLBACK_PRICES.get(symbol, {}).get("usd", 0))
                    change_24h = coin_data.get("usd_24h_change", 0)
                    prices[symbol] = {
                        "name": info["name"],
                        "price_usd": usd_price,
                        "price_xaf": round(usd_price * XAF_USD_RATE),
                        "change_24h": round(change_24h, 2) if change_24h else 0,
                    }
                _price_cache["data"] = prices
                _price_cache["ts"] = now
                logger.info("CoinGecko prices fetched successfully")
                return prices
    except Exception as e:
        logger.warning(f"CoinGecko API error: {e}, using fallback")

    # Fallback
    prices = {}
    for symbol, info in COIN_MAP.items():
        usd = FALLBACK_PRICES.get(symbol, {}).get("usd", 0)
        prices[symbol] = {
            "name": info["name"],
            "price_usd": usd,
            "price_xaf": round(usd * XAF_USD_RATE),
            "change_24h": 0,
        }
    return prices


def get_market_prices_sync():
    """Synchronous wrapper for use in non-async contexts."""
    return {s: {"price": FALLBACK_PRICES.get(s, {}).get("usd", 0) * XAF_USD_RATE} for s in COIN_MAP}


class BuyCryptoRequest(BaseModel):
    coin: str
    amount_fiat: float  # XAF to spend

class SellCryptoRequest(BaseModel):
    coin: str
    amount_crypto: float

class StakeRequest(BaseModel):
    coin: str
    amount: float

class UnstakeRequest(BaseModel):
    stake_id: str


@router.get("/market")
async def get_market(request: Request):
    await get_current_user(request)
    prices = await fetch_coingecko_prices()
    coins = []
    for symbol, info in prices.items():
        coins.append({
            "symbol": symbol,
            "name": info["name"],
            "price_usd": info["price_usd"],
            "price_xaf": info["price_xaf"],
            "change_24h": info["change_24h"],
            "staking_apy": STAKING_APY.get(symbol, 0),
        })
    return coins


@router.get("/portfolio")
async def get_portfolio(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        wallets = await conn.fetch("SELECT * FROM crypto_wallets WHERE user_id = $1", user['user_id'])
        portfolio = []
        total_value = Decimal("0")
        prices = await fetch_coingecko_prices()
        for w in wallets:
            pinfo = prices.get(w['coin'], {})
            price_xaf = pinfo.get('price_xaf', 0)
            value = w['balance'] * Decimal(str(price_xaf))
            staked_value = w['staked'] * Decimal(str(price_xaf))
            total_value += value + staked_value
            portfolio.append({
                "coin": w['coin'],
                "name": pinfo.get('name', w['coin']),
                "balance": str(w['balance']),
                "staked": str(w['staked']),
                "price_xaf": price_xaf,
                "value_xaf": str(value),
                "staked_value_xaf": str(staked_value),
                "staking_apy": STAKING_APY.get(w['coin'], 0),
            })
        return {"portfolio": portfolio, "total_value_xaf": str(total_value)}


@router.post("/buy")
async def buy_crypto(req: BuyCryptoRequest, request: Request):
    user = await get_current_user(request)
    coin = req.coin.upper()
    if coin not in COIN_MAP:
        raise HTTPException(status_code=400, detail=f"Unsupported coin: {coin}")
    if req.amount_fiat <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    prices = await fetch_coingecko_prices()
    price = prices.get(coin, {}).get('price_xaf', 0)
    if price <= 0:
        raise HTTPException(status_code=503, detail="Price unavailable")
    crypto_amount = Decimal(str(req.amount_fiat)) / Decimal(str(price))
    fiat_amount = Decimal(str(req.amount_fiat))

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user['user_id'])
            if not wallet or wallet['balance'] < fiat_amount:
                raise HTTPException(status_code=400, detail="Insufficient XAF balance")

            await conn.execute("UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
                               fiat_amount, datetime.now(timezone.utc), user['user_id'])

            existing = await conn.fetchrow("SELECT * FROM crypto_wallets WHERE user_id = $1 AND coin = $2", user['user_id'], coin)
            if existing:
                await conn.execute("UPDATE crypto_wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3 AND coin = $4",
                                   crypto_amount, datetime.now(timezone.utc), user['user_id'], coin)
            else:
                await conn.execute("INSERT INTO crypto_wallets (user_id, coin, balance) VALUES ($1, $2, $3)",
                                   user['user_id'], coin, crypto_amount)

            ctx_id = f"cbuy_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO crypto_transactions (ctx_id, user_id, type, coin, amount, price_per_unit, total_fiat)
                VALUES ($1, $2, 'buy', $3, $4, $5, $6)
            """, ctx_id, user['user_id'], coin, crypto_amount, Decimal(str(price)), fiat_amount)

            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'crypto_buy', 'crypto', $2)
            """, user['user_id'], f'{{"coin": "{coin}", "amount": "{crypto_amount}", "fiat": "{fiat_amount}"}}')

            return {
                "message": f"Achat de {crypto_amount:.8f} {coin} reussi",
                "tx_id": ctx_id,
                "coin": coin,
                "amount": str(crypto_amount),
                "spent_xaf": str(fiat_amount),
                "price_per_unit": price,
            }


@router.post("/sell")
async def sell_crypto(req: SellCryptoRequest, request: Request):
    user = await get_current_user(request)
    coin = req.coin.upper()
    if coin not in COIN_MAP:
        raise HTTPException(status_code=400, detail=f"Unsupported coin: {coin}")
    if req.amount_crypto <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    prices = await fetch_coingecko_prices()
    price = prices.get(coin, {}).get('price_xaf', 0)
    if price <= 0:
        raise HTTPException(status_code=503, detail="Price unavailable")
    crypto_amount = Decimal(str(req.amount_crypto))
    fiat_amount = crypto_amount * Decimal(str(price))

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            cw = await conn.fetchrow("SELECT * FROM crypto_wallets WHERE user_id = $1 AND coin = $2 FOR UPDATE", user['user_id'], coin)
            if not cw or cw['balance'] < crypto_amount:
                raise HTTPException(status_code=400, detail=f"Insufficient {coin} balance")

            await conn.execute("UPDATE crypto_wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3 AND coin = $4",
                               crypto_amount, datetime.now(timezone.utc), user['user_id'], coin)
            await conn.execute("UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                               fiat_amount, datetime.now(timezone.utc), user['user_id'])

            ctx_id = f"csel_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO crypto_transactions (ctx_id, user_id, type, coin, amount, price_per_unit, total_fiat)
                VALUES ($1, $2, 'sell', $3, $4, $5, $6)
            """, ctx_id, user['user_id'], coin, crypto_amount, Decimal(str(price)), fiat_amount)

            return {"message": f"Vente de {crypto_amount:.8f} {coin} reussie", "tx_id": ctx_id,
                    "received_xaf": str(fiat_amount)}


@router.post("/stake")
async def stake_crypto(req: StakeRequest, request: Request):
    user = await get_current_user(request)
    coin = req.coin.upper()
    if coin not in STAKING_APY:
        raise HTTPException(status_code=400, detail=f"Staking not available for {coin}")
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    amount = Decimal(str(req.amount))
    apy = Decimal(str(STAKING_APY[coin]))

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            cw = await conn.fetchrow("SELECT * FROM crypto_wallets WHERE user_id = $1 AND coin = $2 FOR UPDATE", user['user_id'], coin)
            if not cw or cw['balance'] < amount:
                raise HTTPException(status_code=400, detail=f"Insufficient {coin} balance")

            await conn.execute("""
                UPDATE crypto_wallets SET balance = balance - $1, staked = staked + $1, updated_at = $2
                WHERE user_id = $3 AND coin = $4
            """, amount, datetime.now(timezone.utc), user['user_id'], coin)

            stake_id = f"stk_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO staking_positions (stake_id, user_id, coin, amount, apy, status, ends_at)
                VALUES ($1, $2, $3, $4, $5, 'active', $6)
            """, stake_id, user['user_id'], coin, amount, apy,
               datetime.now(timezone.utc) + timedelta(days=365))

            ctx_id = f"cstk_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO crypto_transactions (ctx_id, user_id, type, coin, amount, price_per_unit, total_fiat)
                VALUES ($1, $2, 'stake', $3, $4, 0, 0)
            """, ctx_id, user['user_id'], coin, amount)

            return {"message": f"Staking de {amount:.8f} {coin} actif a {apy}% APY",
                    "stake_id": stake_id, "apy": str(apy)}


@router.post("/unstake")
async def unstake_crypto(req: UnstakeRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            pos = await conn.fetchrow(
                "SELECT * FROM staking_positions WHERE stake_id = $1 AND user_id = $2 AND status = 'active'",
                req.stake_id, user['user_id'])
            if not pos:
                raise HTTPException(status_code=404, detail="Staking position not found")

            # Calculate earned rewards
            days_staked = max(1, (datetime.now(timezone.utc) - pos['started_at'].replace(tzinfo=timezone.utc)).days)
            daily_rate = pos['apy'] / Decimal("365") / Decimal("100")
            earned = pos['amount'] * daily_rate * days_staked
            total_return = pos['amount'] + earned

            await conn.execute("""
                UPDATE crypto_wallets SET staked = staked - $1, balance = balance + $2, updated_at = $3
                WHERE user_id = $4 AND coin = $5
            """, pos['amount'], total_return, datetime.now(timezone.utc), user['user_id'], pos['coin'])

            await conn.execute("""
                UPDATE staking_positions SET status = 'completed', earned = $1 WHERE stake_id = $2
            """, earned, req.stake_id)

            return {"message": f"Unstaking reussi: {total_return:.8f} {pos['coin']} retourne (dont {earned:.8f} de gains)",
                    "principal": str(pos['amount']), "earned": str(earned), "total": str(total_return)}


@router.get("/staking")
async def get_staking_positions(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM staking_positions WHERE user_id = $1 ORDER BY created_at DESC
        """, user['user_id'])
        positions = []
        for r in rows:
            p = dict(r)
            p['amount'] = str(p['amount'])
            p['earned'] = str(p['earned'])
            p['apy'] = str(p['apy'])
            p['created_at'] = p['created_at'].isoformat()
            p['started_at'] = p['started_at'].isoformat()
            if p.get('ends_at'):
                p['ends_at'] = p['ends_at'].isoformat()
            positions.append(p)
        return positions


@router.get("/transactions")
async def get_crypto_transactions(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=50)):
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM crypto_transactions WHERE user_id = $1", user['user_id'])
        rows = await conn.fetch("""
            SELECT * FROM crypto_transactions WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3
        """, user['user_id'], limit, offset)
        txs = []
        for r in rows:
            t = dict(r)
            t['amount'] = str(t['amount'])
            t['price_per_unit'] = str(t['price_per_unit'])
            t['total_fiat'] = str(t['total_fiat'])
            t['created_at'] = t['created_at'].isoformat()
            txs.append(t)
        return {"transactions": txs, "total": count, "page": page}
