"""
JAPAP Messenger — Gaming Module (Phase 1)
Jeux simples: spin (roue), quiz, tap. Récompenses en wallet XAF avec contrôle anti-abus.

Règles anti-abus:
  - Spin: max 3/jour, reward 10-500 XAF, probabilités fixes
  - Quiz: max 10/jour, reward fixe si bonne réponse (50 XAF)
  - Tap: max 5/jour, reward scale selon score
Cap: 2000 XAF max/jour par user tous jeux confondus
"""
import uuid
import random
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from database import get_pool
from routes.auth import get_current_user
from routes.kyc import is_user_kyc_approved
from services.settings_service import get_bool, get_int, get_float, get_json

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/games", tags=["games"])

DAILY_CAP = Decimal("2000.00")

LIMITS = {
    "spin": 3,
    "quiz": 10,
    "tap": 5,
}

# Spin wheel configuration: (reward_xaf, weight_out_of_100)
SPIN_PRIZES = [
    (0, 40),       # 40% rien
    (10, 20),      # 20% petite
    (25, 15),
    (50, 12),
    (100, 8),
    (250, 4),
    (500, 1),      # 1% jackpot
]

QUIZ_QUESTIONS = [
    {"id": 1, "question": "Quelle est la capitale du Cameroun ?", "options": ["Douala", "Yaoundé", "Bafoussam", "Garoua"], "answer": 1},
    {"id": 2, "question": "Quelle monnaie est utilisée au Cameroun ?", "options": ["Franc CFA", "Euro", "Dollar", "Naira"], "answer": 0},
    {"id": 3, "question": "Combien de régions compte le Cameroun ?", "options": ["8", "10", "12", "6"], "answer": 1},
    {"id": 4, "question": "Qui est le président actuel du Cameroun (2024) ?", "options": ["Paul Biya", "Maurice Kamto", "Ni John Fru Ndi", "Garga Haman"], "answer": 0},
    {"id": 5, "question": "Quel fleuve traverse Yaoundé ?", "options": ["Le Nyong", "La Sanaga", "Le Mfoundi", "Le Wouri"], "answer": 2},
    {"id": 6, "question": "Combien de langues officielles au Cameroun ?", "options": ["1", "2", "3", "4"], "answer": 1},
    {"id": 7, "question": "Quel est le plat national camerounais le plus célèbre ?", "options": ["Ndolé", "Poulet DG", "Eru", "Koki"], "answer": 0},
    {"id": 8, "question": "Le mont Cameroun est-il un volcan actif ?", "options": ["Oui", "Non", "Éteint", "Dormant"], "answer": 0},
]

QUIZ_REWARD = Decimal("50.00")


class SpinRequest(BaseModel):
    pass  # no input


class QuizAnswerRequest(BaseModel):
    question_id: int
    answer_index: int


class TapResultRequest(BaseModel):
    score: int  # number of taps in 10s window


async def _check_daily_limits(conn, user_id: str, game_type: str):
    """Enforce per-game and global daily caps. Returns (plays_today, total_reward_today)."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    row = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE game_type = $2) AS plays_game,
            COALESCE(SUM(reward), 0) AS reward_total
        FROM game_plays
        WHERE user_id = $1 AND created_at > $3
    """, user_id, game_type, since)
    plays = row['plays_game'] or 0
    total = Decimal(str(row['reward_total']))
    return plays, total


async def _credit_reward(conn, user_id: str, game_type: str, reward: Decimal, metadata: dict, score: int = 0):
    """Credit wallet, record game_plays + transaction, audit log."""
    play_id = f"gp_{uuid.uuid4().hex[:12]}"
    import json
    await conn.execute("""
        INSERT INTO game_plays (play_id, user_id, game_type, score, reward, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
    """, play_id, user_id, game_type, score, reward, json.dumps(metadata))

    if reward > 0:
        await conn.execute("UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                           reward, datetime.now(timezone.utc), user_id)
        tx_id = f"gm_{uuid.uuid4().hex[:12]}"
        await conn.execute("""
            INSERT INTO transactions (tx_id, to_user_id, type, amount, status, notes, reference)
            VALUES ($1, $2, 'game_reward', $3, 'completed', $4, $5)
        """, tx_id, user_id, reward, f"Récompense jeu {game_type}", play_id)

    return play_id


def _weighted_pick(prizes):
    total = sum(w for _, w in prizes)
    r = random.uniform(0, total)
    cum = 0
    for prize, w in prizes:
        cum += w
        if r <= cum:
            return prize
    return 0



@router.get("/toggles")
async def games_toggles(request: Request):
    """Public toggles for the 4 engagement games — consumed by the user UI
    to show a "temporarily unavailable" message when a game is disabled.

    No auth required on purpose : the landing screen should be able to show
    the state before login.
    """
    from services.points_service import is_game_enabled
    from services.settings_service import get_bool, get_json
    from services.games_settings import get_quiz_config
    cfg = await get_json("wheel_config_json", {}) or {}
    qcfg = await get_quiz_config()
    return {
        "wheel_enabled": await is_game_enabled("wheel"),
        "quiz_enabled": await is_game_enabled("quiz"),
        "tap_enabled": await is_game_enabled("tap"),
        "duel_enabled": await get_bool("duel_enabled", True),
        "quiz_timer_seconds": int(cfg.get("quiz_timer_seconds", 10)),
        # iter126 — Phase 3.C: expose paid-challenge runtime config so the
        # Defy modal can show the slider, bounds and commission breakdown
        # without an extra round-trip. SAFE because these are not secrets.
        "quiz_challenge_paid_enabled":            bool(qcfg.get("quiz_challenge_paid_enabled", False)),
        "quiz_challenge_commission_pct":          int(qcfg.get("quiz_challenge_commission_pct", 10)),
        "quiz_challenge_stake_min":               int(qcfg.get("quiz_challenge_stake_min", 1)),
        "quiz_challenge_stake_max":               int(qcfg.get("quiz_challenge_stake_max", 10000)),
        "quiz_challenge_expiry_hours":            int(qcfg.get("quiz_challenge_expiry_hours", 24)),
        "quiz_challenge_challenger_bonus_points": int(qcfg.get("quiz_challenge_challenger_bonus_points", 50)),
        "unavailable_message": "Ce jeu est temporairement indisponible.",
    }


@router.get("/status")
async def status(request: Request):
    """Daily play counts + remaining quota."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        rows = await conn.fetch("""
            SELECT game_type, COUNT(*) AS plays, COALESCE(SUM(reward), 0) AS total
            FROM game_plays WHERE user_id = $1 AND created_at > $2
            GROUP BY game_type
        """, user['user_id'], since)
        played = {r['game_type']: {'plays': r['plays'], 'total': str(r['total'])} for r in rows}
        total_reward = sum((Decimal(v['total']) for v in played.values()), Decimal(0))
        return {
            "daily_cap": str(DAILY_CAP),
            "earned_today": str(total_reward),
            "remaining": str(max(Decimal(0), DAILY_CAP - total_reward)),
            "limits": LIMITS,
            "games": {
                g: {
                    "limit": LIMITS[g],
                    "played_today": played.get(g, {}).get('plays', 0),
                    "remaining": max(0, LIMITS[g] - played.get(g, {}).get('plays', 0)),
                }
                for g in LIMITS
            },
        }


@router.get("/spin/config")
async def spin_config(request: Request):
    """Public-ish: returns the spin configuration the user UI needs."""
    await get_current_user(request)
    rewards = await get_json("spin_rewards_json", [
        {"amount": r, "weight": w} for r, w in SPIN_PRIZES
    ])
    return {
        "enabled": await get_bool("spin_enabled", True),
        "is_paid": await get_bool("spin_is_paid", False),
        "cost_xaf": await get_int("spin_cost_xaf", 0),
        "max_daily_plays": await get_int("spin_max_daily_plays", LIMITS["spin"]),
        "daily_cap_xaf": await get_int("spin_daily_cap_xaf", int(DAILY_CAP)),
        "rewards": rewards,
    }


@router.post("/spin")
async def spin(request: Request):
    user = await get_current_user(request)
    if not await get_bool("spin_enabled", True):
        raise HTTPException(status_code=503, detail="JAPAP Spin est actuellement désactivé.")

    is_paid = await get_bool("spin_is_paid", False)
    cost = Decimal(str(await get_int("spin_cost_xaf", 0))) if is_paid else Decimal("0")
    daily_limit = await get_int("spin_max_daily_plays", LIMITS["spin"])
    daily_cap_val = Decimal(str(await get_int("spin_daily_cap_xaf", int(DAILY_CAP))))
    rewards_cfg = await get_json("spin_rewards_json", [
        {"amount": r, "weight": w} for r, w in SPIN_PRIZES
    ])
    prizes_runtime = [(int(p.get("amount", 0)), int(p.get("weight", 0))) for p in rewards_cfg]

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Paid spin → requires KYC only if the admin says so
        if is_paid and cost > 0 and await get_bool("kyc_required_for_paid_games", True):
            if not await is_user_kyc_approved(conn, user['user_id']):
                raise HTTPException(
                    status_code=403,
                    detail="KYC_REQUIRED: Verify your identity to play paid games."
                )

        async with conn.transaction():
            plays, total_today = await _check_daily_limits(conn, user['user_id'], 'spin')
            if plays >= daily_limit:
                raise HTTPException(status_code=429, detail=f"Limite quotidienne atteinte ({daily_limit}/jour)")
            if total_today >= daily_cap_val:
                raise HTTPException(status_code=429, detail="Plafond quotidien atteint")

            # Debit cost (paid mode)
            if is_paid and cost > 0:
                wallet = await conn.fetchrow("SELECT balance, is_locked FROM wallets WHERE user_id = $1 FOR UPDATE", user['user_id'])
                if not wallet:
                    raise HTTPException(status_code=404, detail="Wallet not found")
                if wallet['is_locked']:
                    raise HTTPException(status_code=403, detail="Wallet locked")
                if wallet['balance'] < cost:
                    raise HTTPException(status_code=400, detail=f"Solde insuffisant pour jouer ({cost} XAF requis).")
                await conn.execute(
                    "UPDATE wallets SET balance = balance - $1, updated_at = NOW() WHERE user_id = $2",
                    cost, user['user_id'])
                await conn.execute("""
                    INSERT INTO transactions (tx_id, from_user_id, type, amount, status, notes)
                    VALUES ($1, $2, 'game_bet', $3, 'completed', 'JAPAP Spin bet')
                """, f"bet_{uuid.uuid4().hex[:12]}", user['user_id'], cost)

            reward_int = _weighted_pick(prizes_runtime if prizes_runtime else SPIN_PRIZES)
            reward = Decimal(str(reward_int))
            # Clamp to remaining cap
            if total_today + reward > daily_cap_val:
                reward = max(Decimal(0), daily_cap_val - total_today)

            play_id = await _credit_reward(conn, user['user_id'], 'spin', reward,
                                           {"prize_slot": reward_int, "cost": str(cost)},
                                           score=reward_int)
            net = reward - cost
            return {
                "play_id": play_id,
                "reward": str(reward),
                "cost": str(cost),
                "net": str(net),
                "prize_slot": reward_int,
                "message": "Gagné !" if reward > 0 else "Pas cette fois, retentez demain !",
            }


@router.get("/quiz/questions")
async def quiz_questions(request: Request):
    """Return quiz questions without the answer field."""
    await get_current_user(request)
    return [
        {"id": q["id"], "question": q["question"], "options": q["options"]}
        for q in QUIZ_QUESTIONS
    ]


@router.post("/quiz/answer")
async def quiz_answer(req: QuizAnswerRequest, request: Request):
    user = await get_current_user(request)
    q = next((x for x in QUIZ_QUESTIONS if x["id"] == req.question_id), None)
    if not q:
        raise HTTPException(status_code=404, detail="Question introuvable")

    is_correct = q["answer"] == req.answer_index

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            plays, total_today = await _check_daily_limits(conn, user['user_id'], 'quiz')
            if plays >= LIMITS['quiz']:
                raise HTTPException(status_code=429, detail=f"Limite quotidienne atteinte ({LIMITS['quiz']}/jour)")

            reward = QUIZ_REWARD if is_correct else Decimal(0)
            if total_today + reward > DAILY_CAP:
                reward = max(Decimal(0), DAILY_CAP - total_today)

            play_id = await _credit_reward(conn, user['user_id'], 'quiz', reward,
                                           {"question_id": req.question_id, "correct": is_correct},
                                           score=1 if is_correct else 0)
            return {
                "play_id": play_id,
                "correct": is_correct,
                "reward": str(reward),
                "correct_answer_index": q["answer"],
            }


@router.post("/tap")
async def tap(req: TapResultRequest, request: Request):
    user = await get_current_user(request)
    if req.score < 0 or req.score > 500:
        raise HTTPException(status_code=400, detail="Score invalide")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            plays, total_today = await _check_daily_limits(conn, user['user_id'], 'tap')
            if plays >= LIMITS['tap']:
                raise HTTPException(status_code=429, detail=f"Limite quotidienne atteinte ({LIMITS['tap']}/jour)")

            # Reward scales: 1 XAF per 2 taps, capped 200 per play
            reward = min(Decimal(req.score // 2), Decimal("200"))
            if total_today + reward > DAILY_CAP:
                reward = max(Decimal(0), DAILY_CAP - total_today)

            play_id = await _credit_reward(conn, user['user_id'], 'tap', reward,
                                           {"score": req.score}, score=req.score)
            return {
                "play_id": play_id,
                "score": req.score,
                "reward": str(reward),
            }


@router.get("/history")
async def history(request: Request, limit: int = 50):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM game_plays WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2
        """, user['user_id'], limit)
        return [
            {
                'play_id': r['play_id'],
                'game_type': r['game_type'],
                'score': r['score'],
                'reward': str(r['reward']),
                'metadata': r['metadata'],
                'created_at': r['created_at'].isoformat(),
            } for r in rows
        ]


@router.get("/leaderboard")
async def leaderboard(
    request: Request,
    scope: str = "global",            # 'global' | 'country'
    country: str = "",                # ISO-2 code (defaults to current user's country when scope=country)
    game: str = "all",                # 'all' | 'quiz' | 'tap' | 'wheel' | 'duel'
    period: str = "30d",              # '7d' | '30d' | 'all'
    limit: int = 50,
):
    """Unified leaderboard with country / global segmentation.

    Returns:
      {
        scope, country, game, period,
        items: [{rank, user_id, name, avatar, country_code, total, plays}, ...],
        me:   {rank_global, rank_country, total, plays},
      }
    """
    user = await get_current_user(request)
    pool = await get_pool()

    # Period filter
    days_map = {"7d": 7, "30d": 30, "all": 365 * 5}
    days = days_map.get(period, 30)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Game filter — game_plays.game_type carries 'quiz', 'tap', 'wheel', 'duel'
    # (clauses are constructed dynamically inside the query block below).

    # Country filter — only when scope='country'. Defaults to the requester's
    # country when missing so the rider can browse "my country" without
    # passing a code explicitly.
    cc = (country or "").upper()[:2]
    if scope == "country" and not cc:
        cc = (user.get("country_code") or "").upper()[:2]

    # Limit
    limit = max(1, min(int(limit or 50), 200))

    async with pool.acquire() as conn:
        # Build the dynamic WHERE clause
        clauses = ["gp.created_at > $1", "gp.reward > 0"]
        params: list = [since]
        if game and game != "all":
            params.append(game)
            clauses.append(f"gp.game_type = ${len(params)}")
        if scope == "country" and cc:
            params.append(cc)
            clauses.append(f"u.country_code = ${len(params)}")
        params.append(limit)
        sql = f"""
            SELECT u.user_id, u.first_name, u.last_name, u.avatar, u.username,
                   u.country_code,
                   SUM(gp.reward) AS total, COUNT(gp.id) AS plays
              FROM game_plays gp JOIN users u ON u.user_id = gp.user_id
             WHERE {' AND '.join(clauses)}
             GROUP BY u.user_id, u.first_name, u.last_name, u.avatar, u.username,
                      u.country_code
             ORDER BY total DESC
             LIMIT ${len(params)}
        """
        rows = await conn.fetch(sql, *params)

        # Compute the requester's two ranks (global + country) over the same
        # period + game filter — independent of `scope` so the UI can show
        # both at once. We use COUNT-of-better instead of pulling the entire
        # board.
        async def _rank_of(extra_clauses: list[str], extra_params: list, label: str):
            base_clauses = ["gp.created_at > $1", "gp.reward > 0"]
            base_params: list = [since]
            if game and game != "all":
                base_params.append(game)
                base_clauses.append(f"gp.game_type = ${len(base_params)}")
            for c in extra_clauses:
                base_params.append(extra_params.pop(0))
                base_clauses.append(c.replace("?", f"${len(base_params)}"))
            # User's score
            my = await conn.fetchrow(
                f"""SELECT COALESCE(SUM(gp.reward), 0)::numeric AS total,
                          COUNT(gp.id)::int AS plays
                      FROM game_plays gp JOIN users u ON u.user_id = gp.user_id
                     WHERE {' AND '.join(base_clauses)} AND u.user_id = ${len(base_params)+1}""",
                *base_params, user["user_id"],
            )
            my_total = my["total"] or 0
            if my_total == 0:
                return {"rank": None, "total": "0", "plays": 0}
            # Number of users with strictly higher total
            higher = await conn.fetchval(
                f"""SELECT COUNT(*) FROM (
                       SELECT u.user_id, SUM(gp.reward) AS s
                         FROM game_plays gp JOIN users u ON u.user_id = gp.user_id
                        WHERE {' AND '.join(base_clauses)}
                        GROUP BY u.user_id
                       HAVING SUM(gp.reward) > $%d
                    ) t""" % (len(base_params) + 1),
                *base_params, my_total,
            )
            return {
                "rank": int(higher or 0) + 1,
                "total": str(my_total),
                "plays": int(my["plays"] or 0),
                "scope": label,
            }

        my_global  = await _rank_of([], [], "global")
        my_country_cc = (user.get("country_code") or "").upper()[:2]
        my_country = (
            await _rank_of(["u.country_code = ?"], [my_country_cc], "country")
            if my_country_cc else {"rank": None, "total": "0", "plays": 0, "scope": "country"}
        )

    # Top-10 country champion badge — earned when the player ranks #1..#10
    # in their own country over the same period+game filter. Computed
    # against the same period filter as the leaderboard so it's consistent.
    is_country_champion = (
        my_country.get("rank") is not None and my_country["rank"] <= 10
    )

    return {
        "scope": scope,
        "country": cc,
        "game": game,
        "period": period,
        "items": [
            {
                "rank": i + 1,
                "user_id": r["user_id"],
                "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["username"],
                "avatar": r["avatar"] or "",
                "country_code": (r["country_code"] or "").upper()[:2],
                "total": str(r["total"]),
                "plays": int(r["plays"] or 0),
            } for i, r in enumerate(rows)
        ],
        "me": {
            "user_id": user["user_id"],
            "country_code": my_country_cc,
            "rank_global":  my_global["rank"],
            "rank_country": my_country["rank"],
            "total":        my_global["total"],
            "plays":        my_global["plays"],
            "is_country_champion": is_country_champion,
            "country_champion_badge": (
                f"Champion {my_country_cc} · Top 10"
                if is_country_champion and my_country_cc else ""
            ),
        },
    }


@router.get("/champion-badge/{user_id}")
async def get_champion_badge(user_id: str, request: Request,
                             period: str = "30d"):
    """Returns the country-champion badge for ANY user (used by profile pages,
    leaderboard rows, and post cards). Auth required."""
    await get_current_user(request)
    pool = await get_pool()
    days_map = {"7d": 7, "30d": 30, "all": 365 * 5}
    days = days_map.get(period, 30)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT country_code, first_name, last_name, username FROM users WHERE user_id = $1",
            user_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable")
        cc = (row["country_code"] or "").upper()[:2]
        if not cc:
            return {"is_country_champion": False, "rank_country": None,
                    "country_code": "", "badge": ""}
        # Rank by total reward in own country
        my_total = await conn.fetchval(
            """SELECT COALESCE(SUM(reward), 0)::numeric FROM game_plays
                 WHERE user_id = $1 AND created_at > $2 AND reward > 0""",
            user_id, since,
        ) or 0
        if my_total == 0:
            return {"is_country_champion": False, "rank_country": None,
                    "country_code": cc, "badge": ""}
        higher = await conn.fetchval(
            """SELECT COUNT(*) FROM (
                   SELECT u.user_id, SUM(gp.reward) AS s
                     FROM game_plays gp JOIN users u ON u.user_id = gp.user_id
                    WHERE gp.created_at > $1 AND gp.reward > 0
                      AND u.country_code = $2
                    GROUP BY u.user_id
                   HAVING SUM(gp.reward) > $3
                ) t""",
            since, cc, my_total,
        ) or 0
        rank = int(higher) + 1
    is_champ = rank <= 10
    return {
        "is_country_champion": is_champ,
        "rank_country": rank,
        "country_code": cc,
        "badge": f"Champion {cc} · Top 10" if is_champ else "",
    }
