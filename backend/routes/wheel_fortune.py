"""
JAPAP Roue de la Fortune v2 — Engagement Engine (iter83)

Règles de gain (backend-controlled) :
  1. L'utilisateur joue gratuitement pour accumuler des points (pas de XAF)
  2. Cycle de 30 jours
  3. Pour gagner : atteindre 10 000 points ET jouer ≥ 25 jours DISTINCTS
  4. Récompense : 1 Pack Starter Pro (30j) activé le mois suivant
  5. Après attribution : reset du cycle
  6. Si échec (points<10k ou jours<25) : reset sans récompense

Logique de distribution (rareté contrôlée) :
  - Phase 1 (j1-10)   : petits gains (objectif: créer l'habitude)
  - Phase 2 (j11-20)  : gains moyens + "quasi gros gains"
  - Phase 3 (j21-25+) : gains élevés + jackpot possible
  - Jackpot Points : déclenchable UNIQUEMENT backend quand user proche de 10k
  - Effet "presque gagné" : ralentissement visuel sur jackpot puis tomber à côté
  - Jamais >24 jours joués = impossible d'atteindre 10k (barrière mathématique)

Bonus fidélité (streak de jours consécutifs) :
  - 3 jours  → +50 pts
  - 7 jours  → +150 pts
  - 15 jours → +400 pts

Anti-bot :
  - Cooldown 30s entre spins (admin tunable)
  - Cloudflare Turnstile obligatoire (admin toggle)
  - Device fingerprinting (security_service.device_fingerprint)
  - Flagging IA : trop fréquent / même IP / comportement bot
  - Aucun calcul côté client : le frontend ne reçoit QUE l'index gagnant
  - Logs complets de chaque spin
"""
from __future__ import annotations
import logging
import os
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import get_current_user
from services.settings_service import get_bool, get_int, get_json

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/wheel", tags=["wheel-fortune"])


# ══════════════════════════════════════════════════════════════════════════
#  DDL — idempotent, appelé au boot
# ══════════════════════════════════════════════════════════════════════════
_DDL = [
    """
    CREATE TABLE IF NOT EXISTS wheel_cycles (
        id BIGSERIAL PRIMARY KEY,
        user_id VARCHAR(32) NOT NULL,
        cycle_start_date DATE NOT NULL,
        cycle_end_date DATE NOT NULL,
        points_cycle INT NOT NULL DEFAULT 0,
        days_played_count INT NOT NULL DEFAULT 0,
        last_played_date DATE,
        streak_days INT NOT NULL DEFAULT 0,
        suspicious_flag BOOLEAN NOT NULL DEFAULT FALSE,
        reward_status VARCHAR(32) NOT NULL DEFAULT 'in_progress',
        reward_claimed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wheel_cycles_user ON wheel_cycles(user_id, reward_status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_wheel_cycles_user_active "
    "ON wheel_cycles(user_id) WHERE reward_status = 'in_progress'",
    """
    CREATE TABLE IF NOT EXISTS wheel_spins (
        id BIGSERIAL PRIMARY KEY,
        cycle_id BIGINT REFERENCES wheel_cycles(id) ON DELETE CASCADE,
        user_id VARCHAR(32) NOT NULL,
        spin_date DATE NOT NULL,
        spin_at TIMESTAMPTZ DEFAULT NOW(),
        prize_slot INT NOT NULL,
        points_awarded INT NOT NULL,
        phase INT NOT NULL,
        near_miss BOOLEAN DEFAULT FALSE,
        jackpot_triggered BOOLEAN DEFAULT FALSE,
        streak_bonus INT DEFAULT 0,
        ip_address VARCHAR(64),
        device_fingerprint VARCHAR(64),
        user_agent VARCHAR(512),
        turnstile_passed BOOLEAN DEFAULT TRUE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wheel_spins_user ON wheel_spins(user_id, spin_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_wheel_spins_cycle ON wheel_spins(cycle_id, spin_date)",
    # iter83 — Unified points across Roue + Quiz + Tap.
    "ALTER TABLE wheel_cycles ADD COLUMN IF NOT EXISTS quiz_answers_correct INT NOT NULL DEFAULT 0",
    "ALTER TABLE wheel_cycles ADD COLUMN IF NOT EXISTS quiz_answers_total INT NOT NULL DEFAULT 0",
    "ALTER TABLE wheel_cycles ADD COLUMN IF NOT EXISTS tap_runs INT NOT NULL DEFAULT 0",
    "ALTER TABLE wheel_spins ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'wheel'",
    "CREATE INDEX IF NOT EXISTS idx_wheel_spins_source ON wheel_spins(source, spin_at DESC)",
    # iter114 — Wheel Boost Event tracking columns
    "ALTER TABLE wheel_spins ADD COLUMN IF NOT EXISTS boost_active BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE wheel_spins ADD COLUMN IF NOT EXISTS boost_id VARCHAR(40)",
    "CREATE INDEX IF NOT EXISTS idx_wheel_spins_boost ON wheel_spins(boost_id, spin_at DESC) WHERE boost_active = TRUE",
]


async def ensure_wheel_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for stmt in _DDL:
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning(f"wheel DDL failed: {e} — {stmt[:60]}")


# ══════════════════════════════════════════════════════════════════════════
#  CONFIG (tous tunables via admin_settings)
# ══════════════════════════════════════════════════════════════════════════

# La roue a 8 cases (index 0..7). Index 0 = Jackpot.
WHEEL_SLOTS = [
    {"idx": 0, "label": "Jackpot", "color": "#FFD700", "is_jackpot": True,
     "base_points": 2000},
    {"idx": 1, "label": "Perdu",   "color": "#111111", "base_points": 0},
    {"idx": 2, "label": "+50",     "color": "#E01C2E", "base_points": 50},
    {"idx": 3, "label": "+150",    "color": "#10B981", "base_points": 150},
    {"idx": 4, "label": "+25",     "color": "#111111", "base_points": 25},
    {"idx": 5, "label": "+400",    "color": "#E01C2E", "base_points": 400},
    {"idx": 6, "label": "+100",    "color": "#10B981", "base_points": 100},
    {"idx": 7, "label": "+75",     "color": "#111111", "base_points": 75},
]

# Distribution par phase : (slot_idx, weight). La somme n'a pas besoin d'être 100.
# iter113 — Logique progressive (dopamine loop) :
#  • Phase 1 (j1-10)   : Perdu RARE (5%), gains fréquents → expérience gagnante,
#                        crée l'habitude, hook l'utilisateur.
#  • Phase 2 (j11-20)  : Perdu modéré (15%), gains moyens + "quasi-gros".
#  • Phase 3 (j21-25+) : Perdu fréquent (35%), gros gains plus rares,
#                        tension maximale, jackpot pilotable backend.
# Le jackpot (slot 0) reste piloté hors distribution en phase 3 uniquement.
PHASE_DISTRIBUTIONS = {
    1: [(1, 5),  (2, 35), (4, 30), (6, 20), (7, 10)],                 # j1-10 — addictive
    2: [(1, 15), (2, 20), (4, 15), (6, 20), (7, 15), (3, 15)],        # j11-20 — équilibré
    3: [(1, 35), (2, 10), (3, 20), (5, 25), (6, 5),  (7, 5)],         # j21+ — tension max
}

# Seuil points global par défaut
POINTS_GOAL = 10_000
DAYS_GOAL = 25
CYCLE_LENGTH_DAYS = 30

# Plafond de gain par spin selon la phase. Il sert à doser la vitesse de
# progression mais n'est PAS la seule garantie anti-exploit : la barrière
# mathématique absolue est imposée dans `wheel_spin` via le clamp
# `new_total = min(new_total, POINTS_GOAL - 1)` tant que
# `days_played_count < DAYS_GOAL`. Voir FIX P0 ci-dessous.
MAX_POINTS_PER_DAY_BY_PHASE = {1: 200, 2: 500, 3: 900}

# Statuts possibles du cycle (source unique de vérité)
CYCLE_STATUS_IN_PROGRESS = "in_progress"
CYCLE_STATUS_REWARD_PENDING = "reward_pending"   # objectif atteint, attente claim
CYCLE_STATUS_REWARD_CLAIMED = "reward_claimed"
CYCLE_STATUS_COMPLETED_WON = "completed_won"     # objectif atteint mais expiré sans claim (7j)
CYCLE_STATUS_COMPLETED_LOST = "completed_lost"
# Fenêtre de grâce pour réclamer après la fin d'un cycle gagnant
CLAIM_GRACE_DAYS = 7


class SpinRequest(BaseModel):
    turnstile_token: Optional[str] = Field(default=None, max_length=2048)
    device_fingerprint: Optional[str] = Field(default=None, max_length=128)


# ══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _today() -> date:
    return datetime.now(timezone.utc).date()


def _compute_phase(days_played: int) -> int:
    if days_played <= 10:
        return 1
    if days_played <= 20:
        return 2
    return 3


async def _verify_turnstile(token: Optional[str], remote_ip: str) -> bool:
    """Verify a Cloudflare Turnstile token. Returns True when disabled or OK."""
    if not await get_bool("wheel_turnstile_enabled", False):
        return True
    secret = os.environ.get("TURNSTILE_SECRET_KEY", "")
    if not secret:
        logger.warning("wheel_turnstile_enabled=true but TURNSTILE_SECRET_KEY missing")
        return True  # fail-open when mis-configured, but log loud
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": secret, "response": token, "remoteip": remote_ip or ""},
            )
        return bool(r.json().get("success"))
    except Exception as e:
        logger.warning(f"Turnstile verify failed: {e}")
        return False


async def _notify_reward_pending(conn, user_id: str, cycle_id: int, deadline: date) -> None:
    """Fire-and-forget push + email when a cycle flips to reward_pending.

    Idempotent via ``wheel_notifications_sent`` (PK ``cycle_id+trigger_tag``)
    — running twice is a no-op. Silent failures (push / email outages must
    never break the spin / status flow)."""
    tag = "reward_pending"
    try:
        await conn.execute(_NOTIF_DDL)
        inserted = await conn.fetchval(
            """INSERT INTO wheel_notifications_sent (cycle_id, trigger_tag)
               VALUES ($1, $2) ON CONFLICT DO NOTHING RETURNING 1""",
            cycle_id, tag,
        )
        if not inserted:
            return  # already sent
    except Exception as e:
        logger.warning(f"wheel reward_pending dedupe insert failed: {e}")
        return

    # Resolve user email + locale for the branded email
    try:
        u = await conn.fetchrow(
            "SELECT email, first_name FROM users WHERE user_id = $1",
            user_id,
        )
    except Exception:
        u = None

    # ── Push (OneSignal) — best effort
    try:
        from services.push_service import configured as _push_configured, send_push_to_user, build_payload
        if _push_configured():
            days_left = max(0, (deadline - _today()).days)
            await send_push_to_user(
                user_id,
                build_payload(
                    title="🏆 Votre Starter Pro vous attend !",
                    body=(
                        f"Vous avez atteint l'objectif. Réclamez-le avant le "
                        f"{deadline.strftime('%d/%m')} — plus que {days_left} jour"
                        f"{'s' if days_left > 1 else ''}."
                    ),
                    url="/games/wheel",
                    tag=f"wheel-reward-pending-{cycle_id}",
                    type_="wheel_reward_pending",
                ),
            )
    except Exception as e:
        logger.warning(f"reward_pending push failed user={user_id}: {e}")

    # ── Email (Resend) — best effort, French branding
    try:
        if u and u["email"]:
            from services.email_service import send_email
            first = (u["first_name"] or "").strip() or "à vous"
            base = os.environ.get("FRONTEND_URL") or os.environ.get("REACT_APP_BACKEND_URL") or ""
            base = base.rstrip("/")
            cta = f"{base}/games/wheel" if base else "/games/wheel"
            deadline_str = deadline.strftime("%d/%m/%Y")
            html = f"""
            <div style="font-family:Manrope,Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;">
              <div style="background:#fff;padding:28px;border-radius:16px;border:1px solid #eee;">
                <h2 style="color:#0F056B;font-family:'Outfit',Arial,sans-serif;margin:0 0 12px 0;">
                  🏆 Bravo {first}, votre Starter Pro est prêt !
                </h2>
                <p style="color:#444;font-size:15px;line-height:1.5;">
                  Vous avez atteint les <strong>10 000 points</strong> et joué au moins
                  <strong>25 jours distincts</strong> sur votre cycle Roue de la Fortune.
                </p>
                <p style="color:#444;font-size:15px;line-height:1.5;">
                  Il vous reste jusqu'au <strong>{deadline_str}</strong> pour réclamer votre
                  Pack Starter Pro (30 jours offerts). Passé ce délai, la récompense expire.
                </p>
                <div style="text-align:center;margin:28px 0;">
                  <a href="{cta}" style="display:inline-block;padding:14px 28px;background:linear-gradient(90deg,#FFD700,#F7931A);color:#111;text-decoration:none;border-radius:999px;font-weight:700;font-family:'Outfit',Arial,sans-serif;">
                    Réclamer maintenant
                  </a>
                </div>
                <p style="color:#888;font-size:12px;text-align:center;margin:0;">
                  JAPAP — cycle Roue de la Fortune
                </p>
              </div>
            </div>
            """
            await send_email(
                to=u["email"],
                subject="🏆 Votre Starter Pro vous attend — JAPAP",
                html=html,
                text=(
                    f"Bravo {first} ! Vous avez débloqué votre Pack Starter Pro. "
                    f"Réclamez-le avant le {deadline_str} : {cta}"
                ),
            )
    except Exception as e:
        logger.warning(f"reward_pending email failed user={user_id}: {e}")


async def _get_or_create_cycle(conn, user_id: str, *, for_update: bool = False):
    """Return the current in-progress cycle (creating one on first access).

    When ``for_update=True`` the row is locked with ``SELECT ... FOR UPDATE`` so
    that concurrent spins from the same user are strictly serialised (no race
    on the ``points_cycle`` / ``days_played_count`` counters)."""
    today = _today()
    lock_clause = " FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(
        f"""SELECT * FROM wheel_cycles
            WHERE user_id = $1 AND reward_status = $2{lock_clause}""",
        user_id, CYCLE_STATUS_IN_PROGRESS,
    )
    if row:
        # Expire if past cycle_end_date
        if row["cycle_end_date"] < today:
            if (
                row["points_cycle"] >= POINTS_GOAL
                and row["days_played_count"] >= DAYS_GOAL
            ):
                # Transition → reward_pending (claimable during the grace window)
                await conn.execute(
                    """UPDATE wheel_cycles
                       SET reward_status = $1, updated_at = NOW()
                       WHERE id = $2""",
                    CYCLE_STATUS_REWARD_PENDING, row["id"],
                )
                # Fire one-shot notification (idempotent)
                try:
                    deadline = row["cycle_end_date"] + timedelta(days=CLAIM_GRACE_DAYS)
                    await _notify_reward_pending(conn, user_id, int(row["id"]), deadline)
                except Exception as e:
                    logger.warning(f"reward_pending notify failed: {e}")
            else:
                await conn.execute(
                    """UPDATE wheel_cycles
                       SET reward_status = $1, updated_at = NOW()
                       WHERE id = $2""",
                    CYCLE_STATUS_COMPLETED_LOST, row["id"],
                )
            row = None

    if not row:
        start = today
        end = start + timedelta(days=CYCLE_LENGTH_DAYS - 1)
        row = await conn.fetchrow(
            """INSERT INTO wheel_cycles (user_id, cycle_start_date, cycle_end_date)
               VALUES ($1, $2, $3) RETURNING *""",
            user_id, start, end,
        )
    return row


def _remaining_days_in_cycle(cycle) -> int:
    return max(0, (cycle["cycle_end_date"] - _today()).days + 1)


def _weighted_pick(pairs: list[tuple]) -> int:
    """Return the picked element (first of tuple) weighted by second-of-tuple."""
    if not pairs:
        return 0
    total = sum(w for _, w in pairs)
    r = random.uniform(0, total)
    running = 0
    for val, w in pairs:
        running += w
        if r <= running:
            return val
    return pairs[-1][0]


def _compute_streak_bonus(streak: int, cfg: dict) -> int:
    if streak >= cfg.get("streak_15_days", 15):
        return cfg.get("streak_15_bonus", 400)
    if streak >= cfg.get("streak_7_days", 7):
        return cfg.get("streak_7_bonus", 150)
    if streak >= cfg.get("streak_3_days", 3):
        return cfg.get("streak_3_bonus", 50)
    return 0


def _jackpot_eligible(cycle, phase: int) -> bool:
    """Jackpot peut être déclenché UNIQUEMENT si :
       - phase 3
       - utilisateur proche de 10k (≥ 8000)
       - jours joués ≥ 20 (trajectoire crédible)
       - il reste au moins 1 jour dans le cycle"""
    return (
        phase == 3
        and cycle["points_cycle"] >= 8_000
        and cycle["days_played_count"] >= 20
        and _remaining_days_in_cycle(cycle) >= 1
    )


# ══════════════════════════════════════════════════════════════════════════
#  iter114 — Wheel Boost Event (admin-piloted retention spike)
# ══════════════════════════════════════════════════════════════════════════
async def get_active_wheel_boost() -> dict:
    """Return the currently-active Wheel Boost Event (or {active: False}).

    A boost is active when `wheel_boost_enabled=True` AND the current UTC
    time falls within [wheel_boost_starts_at, wheel_boost_ends_at]. Empty
    timestamps mean "no limit" on that side. Invalid ISO strings → absent.

    Effects available (applied in wheel_spin):
      • gain_multiplier (float >= 1.0) : base_points + streak_bonus × N
      • perdu_reduction_percent (0..95) : reduces weight of slot 1 (Perdu)
      • unlock_jackpot_all_phases (bool) : allows jackpot draw before phase 3
    """
    from services.settings_service import get_setting, get_bool, get_float
    if not await get_bool("wheel_boost_enabled", False):
        return {"active": False}
    now = datetime.now(timezone.utc)
    start_raw = (await get_setting("wheel_boost_starts_at") or "").strip()
    end_raw = (await get_setting("wheel_boost_ends_at") or "").strip()

    def _parse(s: str):
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        # iter134 — Coerce naive → UTC-aware so comparisons with `now`
        # (always tz-aware) never crash. Admin-set values via the UI may
        # be saved as `YYYY-MM-DDTHH:MM:SS` without offset.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    start = _parse(start_raw)
    end = _parse(end_raw)
    if start and now < start:
        return {"active": False, "starts_at": start.isoformat()}
    if end and now > end:
        return {"active": False, "ended_at": end.isoformat()}

    multiplier = await get_float("wheel_boost_gain_multiplier", 1.5) or 1.5
    if multiplier < 1.0:
        multiplier = 1.0
    if multiplier > 5.0:
        multiplier = 5.0
    perdu_red = int(await get_setting("wheel_boost_perdu_reduction_percent") or 50)
    perdu_red = max(0, min(95, perdu_red))
    return {
        "active": True,
        "id": (await get_setting("wheel_boost_id")) or "boost",
        "name": (await get_setting("wheel_boost_name")) or "Wheel Boost Event",
        "starts_at": start.isoformat() if start else None,
        "ends_at": end.isoformat() if end else None,
        "gain_multiplier": float(multiplier),
        "perdu_reduction_percent": perdu_red,
        "unlock_jackpot_all_phases": await get_bool(
            "wheel_boost_unlock_jackpot_all_phases", False),
        "jackpot_odds_during_boost": int(
            await get_setting("wheel_boost_jackpot_odds") or 25),
    }


def _apply_boost_to_distribution(distribution: list, perdu_reduction_percent: int) -> list:
    """Reduces the weight of slot 1 ('Perdu') by `perdu_reduction_percent` %.
    If the slot drops to 0, it is removed from the list. Other slots keep
    their relative weights so the distribution shape is preserved."""
    if perdu_reduction_percent <= 0:
        return distribution
    out = []
    for slot, weight in distribution:
        if slot == 1:
            new_w = int(round(weight * (100 - perdu_reduction_percent) / 100))
            if new_w > 0:
                out.append((slot, new_w))
        else:
            out.append((slot, weight))
    return out or distribution  # never return empty


def _near_miss_eligible(cycle, phase: int) -> bool:
    """Effet 'presque gagné' : user proche du jackpot mais pas encore prêt."""
    return (
        phase >= 2
        and cycle["points_cycle"] >= 5_000
        and cycle["days_played_count"] >= 15
        and cycle["points_cycle"] < 8_000
    )


# iter83 P2 — Emotional feedback & dynamic messages
_MILESTONE_THRESHOLDS = [5_000, 8_000, 10_000]


def _milestones_reached(points: int) -> list[int]:
    return [t for t in _MILESTONE_THRESHOLDS if points >= t]


def _build_progression_message(cycle) -> str:
    """Dynamic, motivational one-liner visible above the wheel."""
    pts = int(cycle["points_cycle"])
    days = int(cycle["days_played_count"])
    days_left = _remaining_days_in_cycle(cycle)
    pts_left = max(0, POINTS_GOAL - pts)
    days_needed = max(0, DAYS_GOAL - days)

    if cycle["reward_status"] != "in_progress":
        return "Cycle terminé — un nouveau cycle a commencé."
    if pts >= POINTS_GOAL and days >= DAYS_GOAL:
        return "🏆 Objectif atteint ! Réclamez votre Starter Pro maintenant."
    if days_left <= 0:
        return "Le cycle est terminé — pas de récompense cette fois."

    parts: list[str] = []
    if days_needed > 0:
        parts.append(f"{days_needed} jour{'s' if days_needed > 1 else ''} de jeu")
    if pts_left > 0:
        parts.append(f"{pts_left:,}".replace(",", " ") + " points")

    if not parts:
        return "🎉 Objectif atteint !"
    core = " et ".join(parts)
    return f"Encore {core} pour débloquer votre Starter Pro 🎁"


def _build_urgency_level(cycle) -> str:
    """low / medium / high / critical — drives the countdown colour."""
    if cycle["reward_status"] != "in_progress":
        return "low"
    days_left = _remaining_days_in_cycle(cycle)
    pts_left = max(0, POINTS_GOAL - int(cycle["points_cycle"]))
    days_needed = max(0, DAYS_GOAL - int(cycle["days_played_count"]))

    if days_left <= 1:
        return "critical"
    if days_left <= 3:
        return "high"
    # Medium if user is lagging vs a linear-progress pace
    expected_pts = POINTS_GOAL * (30 - days_left) / 30
    if pts_left > 0 and int(cycle["points_cycle"]) < expected_pts * 0.75:
        return "medium"
    if days_needed > days_left:
        return "high"
    return "low"


# ══════════════════════════════════════════════════════════════════════════
#  CORE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@router.get("/status")
async def wheel_status(request: Request):
    """Return the user's current cycle state — points, days, phase, eligibility."""
    user = await get_current_user(request)
    pool = await get_pool()
    today = _today()
    import asyncio as _asyncio

    # Settings are cached in-memory (60s TTL) → no DB roundtrip for these.
    cfg_task = _asyncio.create_task(get_json("wheel_config_json", {}))
    turnstile_task = _asyncio.create_task(get_bool("wheel_turnstile_enabled", False))

    # Acquire the cycle first (sequential — we need cycle["id"] for played_today).
    async with pool.acquire() as conn:
        cycle = await _get_or_create_cycle(conn, user["user_id"])
    cycle_id = cycle["id"]
    uid = user["user_id"]

    # Now parallelise the 3 remaining DB reads on distinct pool connections.
    async def _pending():
        async with pool.acquire() as c:
            return await c.fetchrow(
                """SELECT id, cycle_end_date, points_cycle, days_played_count
                   FROM wheel_cycles
                   WHERE user_id = $1 AND reward_status = $2
                   ORDER BY cycle_end_date DESC LIMIT 1""",
                uid, CYCLE_STATUS_REWARD_PENDING,
            )

    async def _played_today():
        async with pool.acquire() as c:
            return await c.fetchval(
                """SELECT COUNT(*) FROM wheel_spins
                   WHERE user_id = $1 AND cycle_id = $2 AND spin_date = $3""",
                uid, cycle_id, today,
            )

    async def _last_spin():
        async with pool.acquire() as c:
            return await c.fetchval(
                "SELECT MAX(spin_at) FROM wheel_spins WHERE user_id = $1", uid,
            )

    pending_row, played_today, last_spin, cfg, turnstile_enabled = await _asyncio.gather(
        _pending(), _played_today(), _last_spin(), cfg_task, turnstile_task,
    )
    cfg = cfg or {}
    phase = _compute_phase(int(cycle["days_played_count"]))

    pending_claim = None
    if pending_row:
        deadline = pending_row["cycle_end_date"] + timedelta(days=CLAIM_GRACE_DAYS)
        days_remaining = max(0, (deadline - today).days)
        if days_remaining >= 0:
            pending_claim = {
                "cycle_id": int(pending_row["id"]),
                "points_cycle": int(pending_row["points_cycle"]),
                "days_played_count": int(pending_row["days_played_count"]),
                "claim_deadline": deadline.isoformat(),
                "claim_days_remaining": days_remaining,
            }

    max_spins_per_day = int(cfg.get("max_spins_per_day", 5))
    cooldown = int(cfg.get("cooldown_seconds", 30))
    cooldown_remaining = 0
    if last_spin:
        elapsed = (datetime.now(timezone.utc) - last_spin).total_seconds()
        cooldown_remaining = max(0, int(cooldown - elapsed))

    return {
        "cycle": {
            "points_cycle": int(cycle["points_cycle"]),
            "days_played_count": int(cycle["days_played_count"]),
            "streak_days": int(cycle["streak_days"]),
            "cycle_start_date": cycle["cycle_start_date"].isoformat(),
            "cycle_end_date": cycle["cycle_end_date"].isoformat(),
            "remaining_days_in_cycle": _remaining_days_in_cycle(cycle),
            "reward_status": cycle["reward_status"],
            "suspicious_flag": bool(cycle["suspicious_flag"]),
        },
        "progress": {
            "phase": phase,
            "points_goal": POINTS_GOAL,
            "days_goal": DAYS_GOAL,
            "points_percent": min(100, round(100 * int(cycle["points_cycle"]) / POINTS_GOAL)),
            "days_percent": min(100, round(100 * int(cycle["days_played_count"]) / DAYS_GOAL)),
            # iter83 — Quiz performance (cycle-level), 3rd condition to claim Starter Pro
            "quiz_accuracy_current": (
                round(int(cycle["quiz_answers_correct"] or 0) / int(cycle["quiz_answers_total"]), 3)
                if int(cycle["quiz_answers_total"] or 0) > 0 else 0.0
            ),
            "quiz_accuracy_goal": 0.75,
            "quiz_accuracy_percent": min(100, round(
                100 * int(cycle["quiz_answers_correct"] or 0) / max(1, int(cycle["quiz_answers_total"] or 1))
            )) if int(cycle["quiz_answers_total"] or 0) > 0 else 0,
            "quiz_answers_correct": int(cycle["quiz_answers_correct"] or 0),
            "quiz_answers_total": int(cycle["quiz_answers_total"] or 0),
            "quiz_answers_needed": 50,  # QUIZ_MIN_ANSWERS_FOR_ELIGIBILITY
            "quiz_answers_remaining": max(0, 50 - int(cycle["quiz_answers_total"] or 0)),
            "quiz_performance_met": (
                int(cycle["quiz_answers_total"] or 0) >= 50
                and (int(cycle["quiz_answers_correct"] or 0) / max(1, int(cycle["quiz_answers_total"] or 1))) >= 0.75
            ),
            "can_claim": (
                cycle["reward_status"] in (CYCLE_STATUS_IN_PROGRESS, CYCLE_STATUS_REWARD_PENDING)
                and int(cycle["points_cycle"]) >= POINTS_GOAL
                and int(cycle["days_played_count"]) >= DAYS_GOAL
                and int(cycle["quiz_answers_total"] or 0) >= 50
                and (int(cycle["quiz_answers_correct"] or 0) / max(1, int(cycle["quiz_answers_total"] or 1))) >= 0.75
            ),
            "points_remaining": max(0, POINTS_GOAL - int(cycle["points_cycle"])),
            "days_remaining": max(0, DAYS_GOAL - int(cycle["days_played_count"])),
            "cycle_days_remaining": _remaining_days_in_cycle(cycle),
            "progression_message": _build_progression_message(cycle),
            "urgency_level": _build_urgency_level(cycle),
            "milestones_reached": _milestones_reached(int(cycle["points_cycle"])),
        },
        "wheel_slots": WHEEL_SLOTS,
        "rules": {
            "max_spins_per_day": max_spins_per_day,
            "cooldown_seconds": cooldown,
            "plays_today": int(played_today or 0),
            "cooldown_remaining": cooldown_remaining,
            "turnstile_enabled": turnstile_enabled,
            "turnstile_site_key": os.environ.get("TURNSTILE_SITE_KEY", ""),
        },
        "pending_claim": pending_claim,
        # iter114 — Wheel Boost Event (admin-piloted retention spike)
        "boost": await get_active_wheel_boost(),
    }


@router.post("/spin")
async def wheel_spin(req: SpinRequest, request: Request):
    user = await get_current_user(request)
    if not await get_bool("wheel_enabled", True):
        raise HTTPException(status_code=503, detail="Roue désactivée par l'admin.")

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")[:512]

    # iter82 — piggyback on device_fingerprint helper
    from services.security_service import device_fingerprint as _fp
    fp = req.device_fingerprint or _fp(ip, ua)

    # Turnstile pre-check (non-blocking in dev when flag OFF)
    if not await _verify_turnstile(req.turnstile_token, ip):
        raise HTTPException(status_code=403, detail="Vérification anti-bot échouée.")

    cfg = await get_json("wheel_config_json", {}) or {}
    max_daily = int(cfg.get("max_spins_per_day", 5))
    cooldown_s = int(cfg.get("cooldown_seconds", 30))

    pool = await get_pool()
    today = _today()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # FIX P0 — lock the cycle row to serialise concurrent spins
            cycle = await _get_or_create_cycle(conn, user["user_id"], for_update=True)

            # ─ Cooldown guard
            last_spin = await conn.fetchval(
                "SELECT MAX(spin_at) FROM wheel_spins WHERE user_id = $1",
                user["user_id"],
            )
            if last_spin:
                elapsed = (datetime.now(timezone.utc) - last_spin).total_seconds()
                if elapsed < cooldown_s:
                    raise HTTPException(
                        status_code=429,
                        detail=f"Patientez {int(cooldown_s - elapsed)}s avant le prochain tour.",
                    )

            # ─ Daily play cap
            plays_today = await conn.fetchval(
                """SELECT COUNT(*) FROM wheel_spins
                   WHERE user_id = $1 AND cycle_id = $2 AND spin_date = $3""",
                user["user_id"], cycle["id"], today,
            )
            if plays_today >= max_daily:
                raise HTTPException(
                    status_code=429,
                    detail=f"Limite quotidienne atteinte ({max_daily}/jour).",
                )

            # ─ Suspicious flag → dégradation silencieuse
            suspicious = bool(cycle["suspicious_flag"])
            if not suspicious:
                # Detect burst: >3 spins same device_fp last 60s
                burst = await conn.fetchval(
                    """SELECT COUNT(*) FROM wheel_spins
                       WHERE device_fingerprint = $1
                         AND spin_at > NOW() - INTERVAL '60 seconds'""",
                    fp,
                )
                if burst and int(burst) > 3:
                    suspicious = True
                    await conn.execute(
                        "UPDATE wheel_cycles SET suspicious_flag = TRUE WHERE id = $1",
                        cycle["id"],
                    )
                    try:
                        from services.security_service import log_security_event
                        await log_security_event(
                            user["user_id"], "wheel.suspicious_burst",
                            severity="warning", ip=ip, ua=ua,
                            details={"spins_60s": int(burst), "fp": fp},
                        )
                    except Exception:
                        pass

            # ─ Days-played bookkeeping
            new_days = int(cycle["days_played_count"])
            new_streak = int(cycle["streak_days"])
            today_already_counted = (cycle["last_played_date"] == today)
            if not today_already_counted:
                new_days += 1
                if cycle["last_played_date"] == today - timedelta(days=1):
                    new_streak += 1
                else:
                    new_streak = 1

            phase = _compute_phase(new_days)

            # iter114 — Wheel Boost Event override
            boost = await get_active_wheel_boost()
            boost_id = boost.get("id") if boost.get("active") else None

            # ─ Decide winning slot (BACKEND-CONTROLLED)
            distribution = PHASE_DISTRIBUTIONS[phase]
            # Boost: reduce Perdu weight in current phase
            if boost.get("active") and boost.get("perdu_reduction_percent", 0) > 0:
                distribution = _apply_boost_to_distribution(
                    distribution, int(boost["perdu_reduction_percent"]))
            near_miss = False
            jackpot = False

            # Jackpot window — admin-allowed & user-eligible
            # iter114: boost can unlock jackpot for all phases when active.
            jackpot_eligible_now = _jackpot_eligible(cycle, phase)
            if (boost.get("active")
                    and boost.get("unlock_jackpot_all_phases")
                    and not suspicious):
                jackpot_eligible_now = True

            if jackpot_eligible_now and not suspicious:
                jackpot_odds = int(cfg.get("jackpot_odds_in_window", 30))
                # iter114: during boost, jackpot odds use boost-controlled value.
                if boost.get("active") and boost.get("unlock_jackpot_all_phases"):
                    jackpot_odds = int(boost.get("jackpot_odds_during_boost", 25))
                if random.randint(1, 100) <= jackpot_odds:
                    slot_idx = 0  # Jackpot
                    jackpot = True
                else:
                    slot_idx = _weighted_pick([(s, w) for s, w in distribution if s != 0])
            elif _near_miss_eligible(cycle, phase):
                # Roue ralentit sur jackpot PUIS tombe sur le slot voisin (#1 = "Perdu"
                # ou #7 selon disposition visuelle) → effet "presque gagné"
                near_miss_odds = int(cfg.get("near_miss_odds", 20))
                if random.randint(1, 100) <= near_miss_odds:
                    slot_idx = 1 if random.random() < 0.5 else 7
                    near_miss = True
                else:
                    slot_idx = _weighted_pick(distribution)
            else:
                slot_idx = _weighted_pick(distribution)

            slot = WHEEL_SLOTS[slot_idx]
            base_points = int(slot["base_points"])
            if suspicious:
                # Hard throttle: cap to 25pts, disable jackpot
                base_points = min(base_points, 25)
                if slot_idx == 0:
                    slot_idx = 1
                    base_points = 0

            # Cap per-day by phase (barrière mathématique)
            day_max = MAX_POINTS_PER_DAY_BY_PHASE[phase]
            points_today = int(await conn.fetchval(
                """SELECT COALESCE(SUM(points_awarded), 0) FROM wheel_spins
                   WHERE user_id = $1 AND cycle_id = $2 AND spin_date = $3""",
                user["user_id"], cycle["id"], today,
            ) or 0)
            points_room = max(0, day_max - points_today)
            base_points = min(base_points, points_room)

            # ─ Streak bonus (one-shot, on day-rollover only)
            streak_bonus = 0
            if not today_already_counted:
                streak_bonus = _compute_streak_bonus(new_streak, cfg)

            # iter114 — Wheel Boost Event: gain multiplier (after caps to keep
            # phase/day enforcement primary, but before the 25-day clamp).
            if (boost.get("active") and not suspicious
                    and not jackpot  # jackpot already maxes the slot value
                    and slot_idx != 1  # never "boost" Perdu
                    and (boost.get("gain_multiplier") or 1.0) > 1.0):
                mult = float(boost["gain_multiplier"])
                base_points = int(round(base_points * mult))
                streak_bonus = int(round(streak_bonus * mult))
                # Re-cap to per-day room AFTER multiplier so we never bypass.
                base_points = min(base_points, max(0, day_max - points_today))

            total_points = base_points + streak_bonus

            # ─ FIX P0 — Barrière mathématique SOUVERAINE
            # Règle métier : il est IMPOSSIBLE d'atteindre 10 000 points
            # tant que l'utilisateur n'a pas joué au moins 25 jours distincts.
            # Ce clamp est la garantie ultime et non contournable : même si
            # tous les autres plafonds (phase cap, streak, jackpot) étaient
            # défaillants, la somme ne peut pas franchir POINTS_GOAL - 1 avant
            # le 25e jour distinct. Le jour 25 est le premier jour où
            # `new_days >= DAYS_GOAL`, ce qui lève le clamp.
            new_total_uncapped = int(cycle["points_cycle"]) + total_points
            if new_days < DAYS_GOAL:
                new_total = min(new_total_uncapped, POINTS_GOAL - 1)
            else:
                new_total = new_total_uncapped
            # Re-align the awarded points to match the clamp so logs stay honest.
            if new_total < new_total_uncapped:
                clamped_delta = new_total - int(cycle["points_cycle"])
                # Reduce streak_bonus first, then base_points.
                if clamped_delta <= base_points:
                    base_points = max(0, clamped_delta)
                    streak_bonus = 0
                else:
                    streak_bonus = max(0, clamped_delta - base_points)
                total_points = base_points + streak_bonus

            # ─ Persist
            # iter113 — VISUAL/LOGIC COHERENCE GUARANTEE.
            # If the per-day cap or the 25-day mathematical clamp reduced the
            # winning slot's base_points to 0, the wheel must NOT visually land
            # on a "winning" slot while the user is told "Pas cette fois".
            # Reassign the slot to the visible "Perdu" cell (idx=1) so the
            # animation, the toast, and the DB record all agree.
            if base_points == 0 and not jackpot and slot_idx not in (1,):
                # Prefer the canonical "Perdu" slot (idx 1, label "Perdu").
                slot_idx = 1
                near_miss = False
            await conn.execute(
                """UPDATE wheel_cycles
                   SET points_cycle = $1, days_played_count = $2,
                       last_played_date = $3, streak_days = $4, updated_at = NOW()
                   WHERE id = $5""",
                new_total, new_days, today, new_streak, cycle["id"],
            )
            spin_id = await conn.fetchval(
                """INSERT INTO wheel_spins
                   (cycle_id, user_id, spin_date, prize_slot, points_awarded,
                    phase, near_miss, jackpot_triggered, streak_bonus,
                    ip_address, device_fingerprint, user_agent, turnstile_passed,
                    boost_active, boost_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                   RETURNING id""",
                cycle["id"], user["user_id"], today, slot_idx, total_points,
                phase, near_miss, jackpot, streak_bonus, ip, fp, ua, True,
                bool(boost.get("active")), boost_id,
            )

    # iter83 P2 — detect milestone crossings during this spin (for celebration UI)
    prev_points = int(cycle["points_cycle"])
    crossed_milestones = [
        t for t in _MILESTONE_THRESHOLDS
        if prev_points < t <= new_total
    ]

    return {
        "spin_id": int(spin_id),
        "prize_slot": slot_idx,
        "points_awarded": total_points,
        "base_points": base_points,
        "streak_bonus": streak_bonus,
        "phase": phase,
        "near_miss": near_miss,
        "jackpot": jackpot,
        "new_total_points": new_total,
        "days_played": new_days,
        "streak_days": new_streak,
        "crossed_milestones": crossed_milestones,
        "boost_active": bool(boost.get("active")),
        "boost_id": boost_id,
        "boost_name": boost.get("name") if boost.get("active") else None,
    }


@router.post("/claim-reward")
async def claim_reward(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # FIX P0 — accept both active cycles (goal reached in-flight) and
            # cycles already flipped to reward_pending by _get_or_create_cycle
            # on expiry. Priority: reward_pending FIRST (the user already won
            # on a past cycle — we must claim that before any fresh in_progress
            # cycle that /status may have just spawned).
            cycle = await conn.fetchrow(
                """SELECT * FROM wheel_cycles
                   WHERE user_id = $1
                     AND reward_status = ANY($2::text[])
                   ORDER BY (CASE WHEN reward_status = $3 THEN 0 ELSE 1 END),
                            cycle_end_date DESC
                   LIMIT 1
                   FOR UPDATE""",
                user["user_id"],
                [CYCLE_STATUS_IN_PROGRESS, CYCLE_STATUS_REWARD_PENDING],
                CYCLE_STATUS_REWARD_PENDING,
            )
            if not cycle:
                raise HTTPException(status_code=404, detail="Aucun cycle éligible.")

            # Grace window check for expired (reward_pending) cycles
            if cycle["reward_status"] == CYCLE_STATUS_REWARD_PENDING:
                deadline = cycle["cycle_end_date"] + timedelta(days=CLAIM_GRACE_DAYS)
                if _today() > deadline:
                    await conn.execute(
                        """UPDATE wheel_cycles SET reward_status = $1, updated_at = NOW()
                           WHERE id = $2""",
                        CYCLE_STATUS_COMPLETED_WON, cycle["id"],
                    )
                    raise HTTPException(
                        status_code=410,
                        detail=f"Délai de réclamation dépassé ({CLAIM_GRACE_DAYS} jours après la fin du cycle).",
                    )

            if int(cycle["points_cycle"]) < POINTS_GOAL:
                raise HTTPException(status_code=400,
                                    detail=f"Il vous manque {POINTS_GOAL - int(cycle['points_cycle'])} points.")
            if int(cycle["days_played_count"]) < DAYS_GOAL:
                raise HTTPException(status_code=400,
                                    detail=f"Il vous faut {DAYS_GOAL} jours distincts (actuellement {cycle['days_played_count']}).")

            # iter83 — Cycle-level quiz performance rule (≥50 answers + ≥75%)
            from services.points_service import (
                is_quiz_performance_met, quiz_accuracy,
                QUIZ_MIN_ANSWERS_FOR_ELIGIBILITY, QUIZ_ACCURACY_THRESHOLD,
            )
            if not is_quiz_performance_met(dict(cycle)):
                total = int(cycle["quiz_answers_total"] or 0)
                if total < QUIZ_MIN_ANSWERS_FOR_ELIGIBILITY:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Répondez à au moins {QUIZ_MIN_ANSWERS_FOR_ELIGIBILITY} questions du Quiz JAPAP (actuellement {total}).",
                    )
                acc = quiz_accuracy(dict(cycle))
                raise HTTPException(
                    status_code=400,
                    detail=f"Performance quiz insuffisante : {acc*100:.0f}% (requis {int(QUIZ_ACCURACY_THRESHOLD*100)}%).",
                )

            # Award: 30 days Pro starting next month (1st of next month UTC)
            today = _today()
            # first day of next month
            first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
            expire_at = first_next + timedelta(days=30)

            # iter83 — activate Starter Pro (pro_type=1) scheduled for next month.
            # If the user is already Pro and the current expiry is later than
            # `expire_at`, we DON'T shorten it — we stack the remaining days by
            # bumping expiry to max(current, expire_at).
            current = await conn.fetchrow(
                "SELECT is_pro, pro_expires_at FROM users WHERE user_id = $1",
                user["user_id"],
            )
            # Turn expire_at into timezone-aware datetime
            _expire_dt = datetime.combine(expire_at, datetime.min.time(), tzinfo=timezone.utc)
            final_expiry = _expire_dt
            if current and current["pro_expires_at"] and current["pro_expires_at"] > _expire_dt:
                final_expiry = current["pro_expires_at"]
            # pro_type=1 = starter
            await conn.execute(
                """UPDATE users SET is_pro = TRUE, pro_type = 1,
                       pro_expires_at = $1, updated_at = NOW()
                   WHERE user_id = $2""",
                final_expiry, user["user_id"],
            )

            await conn.execute(
                """UPDATE wheel_cycles SET reward_status = $1,
                       reward_claimed_at = NOW(), updated_at = NOW()
                   WHERE id = $2""",
                CYCLE_STATUS_REWARD_CLAIMED, cycle["id"],
            )
    return {
        "status": "ok",
        "message": "Pack Starter Pro attribué ! Activation au 1er du mois suivant pour 30 jours.",
        "plan": "starter",
        "activation_date": first_next.isoformat(),
        "expire_date": expire_at.isoformat(),
    }


@router.get("/history")
async def wheel_history(request: Request, limit: int = 20):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, spin_date, prize_slot, points_awarded, phase,
                      near_miss, jackpot_triggered, streak_bonus, spin_at
               FROM wheel_spins
               WHERE user_id = $1
               ORDER BY spin_at DESC LIMIT $2""",
            user["user_id"], min(max(limit, 1), 100),
        )
    return {
        "history": [
            {
                "id": int(r["id"]),
                "spin_date": r["spin_date"].isoformat(),
                "prize_slot": int(r["prize_slot"]),
                "points_awarded": int(r["points_awarded"]),
                "phase": int(r["phase"]),
                "near_miss": bool(r["near_miss"]),
                "jackpot": bool(r["jackpot_triggered"]),
                "streak_bonus": int(r["streak_bonus"]),
                "spin_at": r["spin_at"].isoformat() if r["spin_at"] else None,
            }
            for r in rows
        ]
    }


# ══════════════════════════════════════════════════════════════════════════
#  Cycle notifications scheduler (iter83 P2)
#
#  Runs once a day from the messaging_worker loop. For every user whose
#  cycle ends in 7/3/1/0 days AND who hasn't yet claimed, push a targeted
#  OneSignal notification. We de-duplicate via a marker row in
#  `wheel_notifications_sent` so the same trigger never fires twice.
# ══════════════════════════════════════════════════════════════════════════

_NOTIF_DDL = """
    CREATE TABLE IF NOT EXISTS wheel_notifications_sent (
        cycle_id BIGINT NOT NULL,
        trigger_tag VARCHAR(16) NOT NULL,
        sent_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (cycle_id, trigger_tag)
    )
"""


def _cycle_trigger_for_days_left(days_left: int) -> Optional[str]:
    if days_left == 7:  return "j7"
    if days_left == 3:  return "j3"
    if days_left == 1:  return "j1"
    if days_left == 0:  return "j0"
    return None


def _cycle_notification_body(points: int, days: int, days_left: int) -> tuple[str, str]:
    pts_left = max(0, POINTS_GOAL - points)
    days_needed = max(0, DAYS_GOAL - days)
    if days_left == 0:
        return (
            "Dernier jour pour votre Starter Pro !",
            f"Il vous manque {pts_left:,} points".replace(",", " ")
            + f" et {days_needed} jour{'s' if days_needed > 1 else ''}. Faites tourner la roue maintenant.",
        )
    if days_left == 1:
        return (
            "Plus qu'1 jour pour votre Starter Pro",
            "Ne laissez pas votre progression s'envoler. Tournez la roue !",
        )
    if days_left == 3:
        return (
            "Plus que 3 jours pour débloquer votre Starter Pro",
            f"Il vous reste {pts_left:,} points et {days_needed} jour{'s' if days_needed > 1 else ''} à valider.".replace(",", " "),
        )
    return (
        "Plus que 7 jours pour votre Starter Pro",
        f"Vous êtes à {pts_left:,} points du bonus — ne perdez pas votre progression.".replace(",", " "),
    )


async def run_cycle_notifications_job() -> dict:
    """Scan in_progress cycles whose end is at d+7/d+3/d+1/d+0 and push
    OneSignal notifications once per (cycle, trigger)."""
    from services.push_service import configured as _push_configured, send_push_to_user, build_payload
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_NOTIF_DDL)
    if not _push_configured():
        return {"sent": 0, "skipped_reason": "onesignal_not_configured"}

    today = _today()
    sent = 0
    skipped = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, user_id, cycle_end_date, points_cycle, days_played_count
               FROM wheel_cycles
               WHERE reward_status = 'in_progress'"""
        )
        for r in rows:
            days_left = (r["cycle_end_date"] - today).days
            tag = _cycle_trigger_for_days_left(days_left)
            if not tag:
                continue
            # Skip users already at target (no need to nudge a winner)
            if int(r["points_cycle"]) >= POINTS_GOAL and int(r["days_played_count"]) >= DAYS_GOAL:
                continue
            # Idempotency guard
            existing = await conn.fetchval(
                "SELECT 1 FROM wheel_notifications_sent WHERE cycle_id = $1 AND trigger_tag = $2",
                r["id"], tag,
            )
            if existing:
                skipped += 1
                continue
            title, body = _cycle_notification_body(
                int(r["points_cycle"]),
                int(r["days_played_count"]),
                max(0, days_left),
            )
            try:
                await send_push_to_user(
                    r["user_id"],
                    build_payload(
                        title=title, body=body,
                        url="/games/wheel", tag=f"wheel-{tag}",
                        type_="wheel_cycle_reminder",
                    ),
                )
                await conn.execute(
                    """INSERT INTO wheel_notifications_sent (cycle_id, trigger_tag)
                       VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                    r["id"], tag,
                )
                sent += 1
            except Exception as e:
                logger.warning(f"wheel notif failed user={r['user_id']} tag={tag}: {e}")
    return {"sent": sent, "skipped_already": skipped}


@router.post("/admin/send-cycle-reminders")
async def admin_trigger_cycle_reminders(request: Request):
    """Admin-only manual trigger of the daily cycle-reminder job."""
    from routes.auth import require_admin as _ra
    await _ra(request)
    result = await run_cycle_notifications_job()
    return {"status": "ok", **result}



# ══════════════════════════════════════════════════════════════════════════
#  Observability & anomaly detection (iter83 — phase d'observation contrôlée)
#
#  Sans Turnstile, nous devons observer en temps réel :
#    1. le comportement des utilisateurs (spins, jours, vitesse d'atteinte)
#    2. les anomalies (24/7, patterns bots, multi-comptes même IP/fingerprint,
#       progression trop rapide)
#    3. la distribution des récompenses (combien atteignent 10k, conversion)
#
#  Les endpoints ci-dessous sont réservés aux admins.
# ══════════════════════════════════════════════════════════════════════════

# Seuils d'anomalie (documentés, admin-tunables plus tard si besoin)
ANOMALY_THRESHOLD_SPINS_PER_HOUR = 30         # > 30 spins/heure sur 24h = bot-like
ANOMALY_THRESHOLD_SAME_IP_USERS = 4           # ≥ 4 users différents sur même IP
ANOMALY_THRESHOLD_SAME_FP_USERS = 3           # ≥ 3 users différents sur même fingerprint
ANOMALY_THRESHOLD_RAPID_POINTS_PER_DAY = 1800  # > 1800 pts/jour distinct = trop rapide
ANOMALY_THRESHOLD_NIGHT_RATIO = 0.55           # > 55% des spins en nuit (00h-06h UTC) = suspect


@router.get("/admin/observability")
async def wheel_observability(request: Request):
    """Tableau de bord observabilité — phase d'observation contrôlée.

    Retourne en un appel toutes les métriques nécessaires au monitoring :
      - engagement global (users actifs, cycles, spins, moyennes)
      - distribution des récompenses (atteintes 10k, jours médians, claim rate)
      - anomalies détectées (bots, multi-comptes, progression trop rapide)
      - protections actives (cooldown, cap quotidien, suspicious_flag count)
    """
    from routes.auth import require_admin as _ra
    await _ra(request)

    pool = await get_pool()
    async with pool.acquire() as conn:
        # ── 1. Engagement global
        engagement = await conn.fetchrow(
            """SELECT
                 COUNT(DISTINCT user_id) FILTER (WHERE reward_status = 'in_progress') AS active_users,
                 COUNT(*) FILTER (WHERE reward_status = 'in_progress')                 AS active_cycles,
                 COUNT(*) FILTER (WHERE reward_status = 'reward_pending')              AS pending_claims,
                 COUNT(*) FILTER (WHERE reward_status = 'reward_claimed')              AS claimed_rewards,
                 COUNT(*) FILTER (WHERE reward_status = 'completed_won')               AS expired_unclaimed,
                 COUNT(*) FILTER (WHERE reward_status = 'completed_lost')              AS completed_lost,
                 COUNT(*)                                                              AS total_cycles,
                 COUNT(*) FILTER (WHERE suspicious_flag = TRUE)                        AS suspicious_cycles
               FROM wheel_cycles"""
        )

        spins_totals = await conn.fetchrow(
            """SELECT
                 COUNT(*)                                                 AS total_spins,
                 COUNT(*) FILTER (WHERE spin_at > NOW() - INTERVAL '24h') AS spins_24h,
                 COUNT(*) FILTER (WHERE spin_at > NOW() - INTERVAL '7d')  AS spins_7d,
                 COUNT(DISTINCT user_id) FILTER (WHERE spin_at > NOW() - INTERVAL '24h') AS dau_24h,
                 COUNT(*) FILTER (WHERE jackpot_triggered = TRUE)         AS jackpots_total,
                 COUNT(*) FILTER (WHERE near_miss = TRUE)                 AS near_miss_total,
                 AVG(points_awarded)::FLOAT                               AS avg_points_per_spin
               FROM wheel_spins"""
        )

        # ── 2. Distribution des récompenses
        conversion = await conn.fetchrow(
            """SELECT
                 COUNT(*) FILTER (WHERE reward_status IN ('reward_claimed','completed_won','reward_pending'))
                   AS winners_total,
                 COUNT(*) FILTER (WHERE reward_status = 'reward_claimed') AS claimed_total,
                 AVG(days_played_count) FILTER (WHERE reward_status = 'reward_claimed')::FLOAT
                   AS avg_days_to_win,
                 AVG(points_cycle) FILTER (WHERE reward_status = 'reward_claimed')::FLOAT
                   AS avg_points_at_claim,
                 PERCENTILE_CONT(0.5) WITHIN GROUP (
                   ORDER BY days_played_count) FILTER (WHERE reward_status = 'reward_claimed')
                   AS median_days_to_win
               FROM wheel_cycles"""
        )
        claim_rate = 0.0
        if conversion and int(conversion["winners_total"] or 0) > 0:
            claim_rate = float(conversion["claimed_total"] or 0) / float(conversion["winners_total"])

        # ── 3. Anomalies — détection automatique sur 24h glissantes

        # 3a. Utilisateurs avec > 30 spins/heure moyenné sur 24h
        bot_like_rate = await conn.fetch(
            """SELECT user_id, COUNT(*) AS spins_24h
               FROM wheel_spins
               WHERE spin_at > NOW() - INTERVAL '24h'
               GROUP BY user_id
               HAVING COUNT(*) >= $1 * 24
               ORDER BY spins_24h DESC
               LIMIT 20""",
            ANOMALY_THRESHOLD_SPINS_PER_HOUR,
        )

        # 3b. Multi-comptes sur même IP (≥4 users différents en 7j)
        same_ip = await conn.fetch(
            """SELECT ip_address, COUNT(DISTINCT user_id) AS distinct_users,
                      COUNT(*) AS spins
               FROM wheel_spins
               WHERE spin_at > NOW() - INTERVAL '7 days'
                 AND ip_address IS NOT NULL AND ip_address <> ''
               GROUP BY ip_address
               HAVING COUNT(DISTINCT user_id) >= $1
               ORDER BY distinct_users DESC
               LIMIT 20""",
            ANOMALY_THRESHOLD_SAME_IP_USERS,
        )

        # 3c. Multi-comptes sur même fingerprint (≥3)
        same_fp = await conn.fetch(
            """SELECT device_fingerprint, COUNT(DISTINCT user_id) AS distinct_users,
                      COUNT(*) AS spins
               FROM wheel_spins
               WHERE spin_at > NOW() - INTERVAL '7 days'
                 AND device_fingerprint IS NOT NULL AND device_fingerprint <> ''
               GROUP BY device_fingerprint
               HAVING COUNT(DISTINCT user_id) >= $1
               ORDER BY distinct_users DESC
               LIMIT 20""",
            ANOMALY_THRESHOLD_SAME_FP_USERS,
        )

        # 3d. Progression trop rapide (points/jour_joué > seuil)
        rapid = await conn.fetch(
            """SELECT user_id, points_cycle, days_played_count,
                      (points_cycle::FLOAT / NULLIF(days_played_count, 0)) AS pts_per_day
               FROM wheel_cycles
               WHERE reward_status IN ('in_progress','reward_pending','reward_claimed')
                 AND days_played_count >= 3
                 AND (points_cycle::FLOAT / NULLIF(days_played_count, 0)) > $1
               ORDER BY pts_per_day DESC
               LIMIT 20""",
            ANOMALY_THRESHOLD_RAPID_POINTS_PER_DAY,
        )

        # 3e. Night-owl pattern (>55% des spins entre 00h-06h UTC sur 7j)
        night_users = await conn.fetch(
            """SELECT user_id,
                      COUNT(*) AS total_spins,
                      COUNT(*) FILTER (
                        WHERE EXTRACT(HOUR FROM spin_at AT TIME ZONE 'UTC') < 6) AS night_spins
               FROM wheel_spins
               WHERE spin_at > NOW() - INTERVAL '7 days'
               GROUP BY user_id
               HAVING COUNT(*) >= 20
                  AND COUNT(*) FILTER (
                        WHERE EXTRACT(HOUR FROM spin_at AT TIME ZONE 'UTC') < 6)::FLOAT / COUNT(*) > $1
               ORDER BY night_spins DESC
               LIMIT 20""",
            ANOMALY_THRESHOLD_NIGHT_RATIO,
        )

        # ── 4. Protections actives — snapshot config
        cfg = await get_json("wheel_config_json", {}) or {}
        protections = {
            "cooldown_seconds": int(cfg.get("cooldown_seconds", 30)),
            "max_spins_per_day": int(cfg.get("max_spins_per_day", 5)),
            "turnstile_enabled": await get_bool("wheel_turnstile_enabled", False),
            "wheel_enabled": await get_bool("wheel_enabled", True),
            "suspicious_cycles": int(engagement["suspicious_cycles"] or 0),
        }

    # ── Recommandation automatique d'activation Turnstile
    n_bots = len(bot_like_rate)
    n_multi_ip = len(same_ip)
    n_multi_fp = len(same_fp)
    n_rapid = len(rapid)
    n_night = len(night_users)
    anomaly_score = (
        n_bots * 3 + n_multi_ip * 2 + n_multi_fp * 2 + n_rapid * 2 + n_night
    )
    if anomaly_score >= 15 or n_bots >= 5:
        turnstile_recommendation = "activate_now"
        recommendation_reason = (
            "Score d'anomalie élevé : l'activation Turnstile est fortement recommandée."
        )
    elif anomaly_score >= 5:
        turnstile_recommendation = "monitor_closely"
        recommendation_reason = "Anomalies modérées — surveillance rapprochée conseillée."
    else:
        turnstile_recommendation = "not_needed_yet"
        recommendation_reason = "Aucun abus majeur détecté sur la fenêtre observée."

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engagement": {
            "active_users": int(engagement["active_users"] or 0),
            "active_cycles": int(engagement["active_cycles"] or 0),
            "pending_claims": int(engagement["pending_claims"] or 0),
            "claimed_rewards": int(engagement["claimed_rewards"] or 0),
            "expired_unclaimed": int(engagement["expired_unclaimed"] or 0),
            "completed_lost": int(engagement["completed_lost"] or 0),
            "total_cycles": int(engagement["total_cycles"] or 0),
            "total_spins": int(spins_totals["total_spins"] or 0),
            "spins_24h": int(spins_totals["spins_24h"] or 0),
            "spins_7d": int(spins_totals["spins_7d"] or 0),
            "dau_24h": int(spins_totals["dau_24h"] or 0),
            "jackpots_total": int(spins_totals["jackpots_total"] or 0),
            "near_miss_total": int(spins_totals["near_miss_total"] or 0),
            "avg_points_per_spin": round(float(spins_totals["avg_points_per_spin"] or 0), 2),
        },
        "reward_distribution": {
            "winners_total": int(conversion["winners_total"] or 0),
            "claimed_total": int(conversion["claimed_total"] or 0),
            "claim_rate": round(claim_rate, 3),
            "avg_days_to_win": round(float(conversion["avg_days_to_win"] or 0), 1),
            "median_days_to_win": float(conversion["median_days_to_win"] or 0),
            "avg_points_at_claim": round(float(conversion["avg_points_at_claim"] or 0), 0),
        },
        "anomalies": {
            "thresholds": {
                "spins_per_hour_24h": ANOMALY_THRESHOLD_SPINS_PER_HOUR,
                "same_ip_distinct_users_7d": ANOMALY_THRESHOLD_SAME_IP_USERS,
                "same_fp_distinct_users_7d": ANOMALY_THRESHOLD_SAME_FP_USERS,
                "rapid_points_per_day": ANOMALY_THRESHOLD_RAPID_POINTS_PER_DAY,
                "night_ratio_7d": ANOMALY_THRESHOLD_NIGHT_RATIO,
            },
            "bot_like_accounts": [
                {"user_id": r["user_id"], "spins_24h": int(r["spins_24h"])}
                for r in bot_like_rate
            ],
            "same_ip_clusters": [
                {"ip_address": r["ip_address"],
                 "distinct_users": int(r["distinct_users"]),
                 "spins": int(r["spins"])}
                for r in same_ip
            ],
            "same_fingerprint_clusters": [
                {"device_fingerprint": r["device_fingerprint"],
                 "distinct_users": int(r["distinct_users"]),
                 "spins": int(r["spins"])}
                for r in same_fp
            ],
            "rapid_progression": [
                {"user_id": r["user_id"],
                 "points_cycle": int(r["points_cycle"]),
                 "days_played": int(r["days_played_count"]),
                 "pts_per_day": round(float(r["pts_per_day"] or 0), 1)}
                for r in rapid
            ],
            "night_owls": [
                {"user_id": r["user_id"],
                 "total_spins": int(r["total_spins"]),
                 "night_spins": int(r["night_spins"]),
                 "ratio": round(float(r["night_spins"]) / float(r["total_spins"]), 2)}
                for r in night_users
            ],
            "anomaly_score": int(anomaly_score),
        },
        "protections": protections,
        "recommendation": {
            "action": turnstile_recommendation,  # activate_now | monitor_closely | not_needed_yet
            "reason": recommendation_reason,
        },
    }


@router.post("/admin/flag-suspicious")
async def admin_flag_suspicious(request: Request):
    """Apply automatic suspicious_flag on cycles matching the strictest
    anomaly patterns (bot_like + same_fingerprint clusters).

    Called manually (or via cron) from the admin panel when the
    observability dashboard shows a spike. The flag triggers the
    silent-degradation logic inside /spin (cap 25 pts, no jackpot)."""
    from routes.auth import require_admin as _ra
    await _ra(request)

    pool = await get_pool()
    flagged = []
    async with pool.acquire() as conn:
        # Strategy: flag any active cycle whose user shows bot-like rate OR
        # shares a fingerprint with ≥3 distinct users in the last 7d.
        bot_users = [
            r["user_id"] for r in await conn.fetch(
                """SELECT user_id FROM wheel_spins
                   WHERE spin_at > NOW() - INTERVAL '24h'
                   GROUP BY user_id
                   HAVING COUNT(*) >= $1 * 24""",
                ANOMALY_THRESHOLD_SPINS_PER_HOUR,
            )
        ]
        fp_users = [
            r["user_id"] for r in await conn.fetch(
                """SELECT DISTINCT s.user_id
                   FROM wheel_spins s
                   WHERE s.spin_at > NOW() - INTERVAL '7 days'
                     AND s.device_fingerprint IN (
                       SELECT device_fingerprint
                       FROM wheel_spins
                       WHERE spin_at > NOW() - INTERVAL '7 days'
                         AND device_fingerprint IS NOT NULL AND device_fingerprint <> ''
                       GROUP BY device_fingerprint
                       HAVING COUNT(DISTINCT user_id) >= $1
                     )""",
                ANOMALY_THRESHOLD_SAME_FP_USERS,
            )
        ]
        suspects = list({*bot_users, *fp_users})
        if suspects:
            rows = await conn.fetch(
                """UPDATE wheel_cycles
                   SET suspicious_flag = TRUE, updated_at = NOW()
                   WHERE user_id = ANY($1::text[])
                     AND reward_status = 'in_progress'
                     AND suspicious_flag = FALSE
                   RETURNING user_id, id""",
                suspects,
            )
            flagged = [{"user_id": r["user_id"], "cycle_id": int(r["id"])} for r in rows]
            # Log every flag
            try:
                from services.security_service import log_security_event
                for f in flagged:
                    await log_security_event(
                        f["user_id"], "wheel.admin_flagged_suspicious",
                        severity="warning", ip="", ua="",
                        details={"cycle_id": f["cycle_id"], "source": "observability_autoflag"},
                    )
            except Exception:
                pass
    return {
        "status": "ok",
        "flagged_count": len(flagged),
        "flagged": flagged,
    }


@router.get("/admin/cycles")
async def admin_list_cycles(
    request: Request,
    status: str = "all",       # all | in_progress | reward_pending | reward_claimed | suspicious | near_goal
    limit: int = 50,
    offset: int = 0,
):
    """Paginated cycles table for the admin "Cycles utilisateurs" panel.

    Returns one row per cycle with the owning user's identity, progression
    against the 10 000 / 25 days goal, suspicious_flag, and the computed
    percentages that drive the progress bars.

    `status=near_goal` filters to users who are close to claiming (points ≥ 7 000
    OR days ≥ 20) — useful to anticipate Starter Pro attributions this month.
    """
    from routes.auth import require_admin as _ra
    await _ra(request)

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    # Build WHERE depending on filter
    where_parts = ["1=1"]
    if status == "in_progress":
        where_parts.append("wc.reward_status = 'in_progress'")
    elif status == "reward_pending":
        where_parts.append("wc.reward_status = 'reward_pending'")
    elif status == "reward_claimed":
        where_parts.append("wc.reward_status = 'reward_claimed'")
    elif status == "suspicious":
        where_parts.append("wc.suspicious_flag = TRUE")
    elif status == "near_goal":
        where_parts.append(
            "wc.reward_status = 'in_progress' "
            "AND (wc.points_cycle >= 7000 OR wc.days_played_count >= 20)"
        )
    # "all" = no extra filter
    where_sql = " AND ".join(where_parts)

    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM wheel_cycles wc WHERE {where_sql}"
        )
        rows = await conn.fetch(
            f"""SELECT
                  wc.id, wc.user_id, wc.cycle_start_date, wc.cycle_end_date,
                  wc.points_cycle, wc.days_played_count, wc.streak_days,
                  wc.suspicious_flag, wc.reward_status, wc.reward_claimed_at,
                  wc.last_played_date, wc.updated_at,
                  u.username, u.first_name, u.last_name, u.email,
                  u.avatar, u.is_pro
                FROM wheel_cycles wc
                LEFT JOIN users u ON u.user_id = wc.user_id
                WHERE {where_sql}
                ORDER BY wc.updated_at DESC
                LIMIT $1 OFFSET $2""",
            limit, offset,
        )

    today = _today()
    items = []
    for r in rows:
        days_left = max(0, (r["cycle_end_date"] - today).days + 1)
        pts_pct = min(100, round(100 * int(r["points_cycle"]) / POINTS_GOAL))
        days_pct = min(100, round(100 * int(r["days_played_count"]) / DAYS_GOAL))
        items.append({
            "cycle_id": int(r["id"]),
            "user_id": r["user_id"],
            "username": r["username"],
            "display_name": (
                f"{(r['first_name'] or '').strip()} {(r['last_name'] or '').strip()}".strip()
                or r["username"] or r["user_id"]
            ),
            "email": r["email"],
            "avatar": r["avatar"],
            "is_pro": bool(r["is_pro"]),
            "cycle_start_date": r["cycle_start_date"].isoformat(),
            "cycle_end_date": r["cycle_end_date"].isoformat(),
            "remaining_days_in_cycle": days_left,
            "points_cycle": int(r["points_cycle"]),
            "points_goal": POINTS_GOAL,
            "points_percent": pts_pct,
            "days_played_count": int(r["days_played_count"]),
            "days_goal": DAYS_GOAL,
            "days_percent": days_pct,
            "streak_days": int(r["streak_days"]),
            "suspicious_flag": bool(r["suspicious_flag"]),
            "reward_status": r["reward_status"],
            "reward_claimed_at": r["reward_claimed_at"].isoformat() if r["reward_claimed_at"] else None,
            "last_played_date": r["last_played_date"].isoformat() if r["last_played_date"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "can_claim": (
                r["reward_status"] in (CYCLE_STATUS_IN_PROGRESS, CYCLE_STATUS_REWARD_PENDING)
                and int(r["points_cycle"]) >= POINTS_GOAL
                and int(r["days_played_count"]) >= DAYS_GOAL
            ),
        })

    return {
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "status_filter": status,
        "items": items,
    }


class WheelConfigUpdateRequest(BaseModel):
    wheel_enabled: Optional[bool] = None
    quiz_enabled: Optional[bool] = None
    tap_enabled: Optional[bool] = None
    duel_enabled: Optional[bool] = None
    wheel_turnstile_enabled: Optional[bool] = None
    max_spins_per_day: Optional[int] = Field(default=None, ge=1, le=100)
    cooldown_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    jackpot_odds_in_window: Optional[int] = Field(default=None, ge=0, le=100)
    near_miss_odds: Optional[int] = Field(default=None, ge=0, le=100)
    streak_3_bonus: Optional[int] = Field(default=None, ge=0, le=10_000)
    streak_7_bonus: Optional[int] = Field(default=None, ge=0, le=10_000)
    streak_15_bonus: Optional[int] = Field(default=None, ge=0, le=10_000)
    quiz_timer_seconds: Optional[int] = Field(default=None, ge=5, le=60)
    duel_winner_bonus: Optional[int] = Field(default=None, ge=0, le=500)
    duel_loser_bonus: Optional[int] = Field(default=None, ge=0, le=500)
    duel_accepts_per_day: Optional[int] = Field(default=None, ge=1, le=20)


@router.get("/admin/config")
async def admin_get_config(request: Request):
    """Return the current wheel configuration for the admin settings form."""
    from routes.auth import require_admin as _ra
    await _ra(request)
    cfg = await get_json("wheel_config_json", {}) or {}
    return {
        "wheel_enabled": await get_bool("wheel_enabled", True),
        "quiz_enabled": await get_bool("quiz_enabled", True),
        "tap_enabled": await get_bool("tap_enabled", True),
        "duel_enabled": await get_bool("duel_enabled", True),
        "wheel_turnstile_enabled": await get_bool("wheel_turnstile_enabled", False),
        "max_spins_per_day": int(cfg.get("max_spins_per_day", 5)),
        "cooldown_seconds": int(cfg.get("cooldown_seconds", 30)),
        "jackpot_odds_in_window": int(cfg.get("jackpot_odds_in_window", 30)),
        "near_miss_odds": int(cfg.get("near_miss_odds", 20)),
        "streak_3_bonus": int(cfg.get("streak_3_bonus", 50)),
        "streak_7_bonus": int(cfg.get("streak_7_bonus", 150)),
        "streak_15_bonus": int(cfg.get("streak_15_bonus", 400)),
        "quiz_timer_seconds": int(cfg.get("quiz_timer_seconds", 10)),
        "duel_winner_bonus": int(cfg.get("duel_winner_bonus", 50)),
        "duel_loser_bonus": int(cfg.get("duel_loser_bonus", 10)),
        "duel_accepts_per_day": int(cfg.get("duel_accepts_per_day", 3)),
        "constants": {
            "points_goal": POINTS_GOAL,
            "days_goal": DAYS_GOAL,
            "cycle_length_days": CYCLE_LENGTH_DAYS,
            "max_points_per_day_by_phase": MAX_POINTS_PER_DAY_BY_PHASE,
            "claim_grace_days": CLAIM_GRACE_DAYS,
        },
    }


@router.put("/admin/config")
async def admin_update_config(req: WheelConfigUpdateRequest, request: Request):
    """Update wheel configuration. Writes to admin_settings (wheel_enabled,
    wheel_turnstile_enabled, wheel_config_json)."""
    from routes.auth import require_admin as _ra
    from services.settings_service import set_setting
    await _ra(request)

    updated: dict = {}
    # Top-level booleans
    if req.wheel_enabled is not None:
        await set_setting("wheel_enabled", bool(req.wheel_enabled))
        updated["wheel_enabled"] = bool(req.wheel_enabled)
    if req.quiz_enabled is not None:
        await set_setting("quiz_enabled", bool(req.quiz_enabled))
        updated["quiz_enabled"] = bool(req.quiz_enabled)
    if req.tap_enabled is not None:
        await set_setting("tap_enabled", bool(req.tap_enabled))
        updated["tap_enabled"] = bool(req.tap_enabled)
    if req.duel_enabled is not None:
        await set_setting("duel_enabled", bool(req.duel_enabled))
        updated["duel_enabled"] = bool(req.duel_enabled)
    if req.wheel_turnstile_enabled is not None:
        await set_setting("wheel_turnstile_enabled", bool(req.wheel_turnstile_enabled))
        updated["wheel_turnstile_enabled"] = bool(req.wheel_turnstile_enabled)

    # Merge-update wheel_config_json so we never lose keys we don't expose
    cfg = await get_json("wheel_config_json", {}) or {}
    for k in (
        "max_spins_per_day", "cooldown_seconds",
        "jackpot_odds_in_window", "near_miss_odds",
        "streak_3_bonus", "streak_7_bonus", "streak_15_bonus",
        "quiz_timer_seconds",
        "duel_winner_bonus", "duel_loser_bonus", "duel_accepts_per_day",
    ):
        v = getattr(req, k)
        if v is not None:
            cfg[k] = int(v)
            updated[k] = int(v)
    # Keep streak day thresholds (3/7/15) at their defaults — they match
    # the product spec and aren't tunable from the admin form.
    cfg.setdefault("streak_3_days", 3)
    cfg.setdefault("streak_7_days", 7)
    cfg.setdefault("streak_15_days", 15)
    await set_setting("wheel_config_json", cfg)

    # Audit log
    try:
        from services.security_service import log_security_event
        user = await get_current_user(request)
        await log_security_event(
            user["user_id"], "wheel.admin_config_update",
            severity="info", ip="", ua="",
            details={"updated": updated},
        )
    except Exception:
        pass

    return {"status": "ok", "updated": updated}


# ══════════════════════════════════════════════════════════════════════════
#  iter114 — Wheel Boost Event admin (retention spike piloté admin)
# ══════════════════════════════════════════════════════════════════════════
class WheelBoostUpdateRequest(BaseModel):
    """All fields optional — partial update."""
    enabled: Optional[bool] = None
    name: Optional[str] = Field(default=None, max_length=80)
    starts_at: Optional[str] = Field(default=None, max_length=40)  # ISO 8601 or empty
    ends_at: Optional[str] = Field(default=None, max_length=40)
    gain_multiplier: Optional[float] = Field(default=None, ge=1.0, le=5.0)
    perdu_reduction_percent: Optional[int] = Field(default=None, ge=0, le=95)
    unlock_jackpot_all_phases: Optional[bool] = None
    jackpot_odds_during_boost: Optional[int] = Field(default=None, ge=0, le=100)


@router.get("/admin/boost")
async def admin_get_boost(request: Request):
    """Read the current Wheel Boost Event settings + live status."""
    from routes.auth import require_admin as _ra
    from services.settings_service import get_setting
    await _ra(request)
    return {
        "settings": {
            "enabled": await get_bool("wheel_boost_enabled", False),
            "name": (await get_setting("wheel_boost_name")) or "Wheel Boost Event",
            "starts_at": (await get_setting("wheel_boost_starts_at")) or "",
            "ends_at": (await get_setting("wheel_boost_ends_at")) or "",
            "gain_multiplier": float(
                (await get_setting("wheel_boost_gain_multiplier")) or 1.5),
            "perdu_reduction_percent": int(
                (await get_setting("wheel_boost_perdu_reduction_percent")) or 50),
            "unlock_jackpot_all_phases": await get_bool(
                "wheel_boost_unlock_jackpot_all_phases", False),
            "jackpot_odds_during_boost": int(
                (await get_setting("wheel_boost_jackpot_odds")) or 25),
            "id": (await get_setting("wheel_boost_id")) or "",
        },
        "live": await get_active_wheel_boost(),
    }


@router.put("/admin/boost")
async def admin_update_boost(req: WheelBoostUpdateRequest, request: Request):
    """Update one or more boost settings. When `enabled` flips True, a fresh
    `boost_id` is generated so analytics group spins per boost window."""
    from routes.auth import require_admin as _ra
    from services.settings_service import set_setting, get_setting
    user = await _ra(request)

    updated: dict = {}
    if req.name is not None:
        await set_setting("wheel_boost_name", str(req.name).strip()[:80])
        updated["name"] = req.name
    # Validate ISO datetimes (empty string clears the bound).
    for field, key in (("starts_at", "wheel_boost_starts_at"),
                       ("ends_at", "wheel_boost_ends_at")):
        raw = getattr(req, field)
        if raw is None:
            continue
        cleaned = str(raw).strip()
        if cleaned:
            try:
                datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field} doit être au format ISO 8601 (ex: 2026-05-01T20:00:00Z)")
        await set_setting(key, cleaned)
        updated[field] = cleaned
    if req.gain_multiplier is not None:
        await set_setting("wheel_boost_gain_multiplier", float(req.gain_multiplier))
        updated["gain_multiplier"] = float(req.gain_multiplier)
    if req.perdu_reduction_percent is not None:
        await set_setting("wheel_boost_perdu_reduction_percent",
                          int(req.perdu_reduction_percent))
        updated["perdu_reduction_percent"] = int(req.perdu_reduction_percent)
    if req.unlock_jackpot_all_phases is not None:
        await set_setting("wheel_boost_unlock_jackpot_all_phases",
                          bool(req.unlock_jackpot_all_phases))
        updated["unlock_jackpot_all_phases"] = bool(req.unlock_jackpot_all_phases)
    if req.jackpot_odds_during_boost is not None:
        await set_setting("wheel_boost_jackpot_odds",
                          int(req.jackpot_odds_during_boost))
        updated["jackpot_odds_during_boost"] = int(req.jackpot_odds_during_boost)
    if req.enabled is not None:
        was_enabled = await get_bool("wheel_boost_enabled", False)
        await set_setting("wheel_boost_enabled", bool(req.enabled))
        updated["enabled"] = bool(req.enabled)
        # iter115 — When admin manually toggles the boost (via this PUT, not
        # the scheduler), claim ownership for "manual" so the scheduler won't
        # touch it. When disabling, also clear the owner.
        if req.enabled:
            await set_setting("wheel_boost_owner", "manual")
            updated["owner"] = "manual"
        else:
            await set_setting("wheel_boost_owner", "")
            updated["owner"] = ""
        # Mint a new boost_id whenever the toggle flips False -> True so we
        # can attribute spins to a specific boost window.
        if req.enabled and not was_enabled:
            new_id = f"boost_{uuid.uuid4().hex[:10]}"
            await set_setting("wheel_boost_id", new_id)
            updated["id"] = new_id

    # Audit
    try:
        from services.security_service import log_security_event
        await log_security_event(
            user["user_id"], "wheel.admin_boost_update",
            severity="warning", ip="", ua="",
            details={"updated": updated},
        )
    except Exception:
        pass

    return {"status": "ok", "updated": updated,
            "live": await get_active_wheel_boost()}


@router.get("/admin/boost/stats")
async def admin_boost_stats(request: Request, boost_id: Optional[str] = None):
    """Performance metrics for the current (or specified) boost window:
    DAU, total spins, distribution by slot, total points awarded, jackpot
    triggers. Used by the admin tab to gauge ROI."""
    from routes.auth import require_admin as _ra
    from services.settings_service import get_setting
    await _ra(request)
    target_id = boost_id or (await get_setting("wheel_boost_id")) or None
    if not target_id:
        return {"boost_id": None, "stats": None,
                "message": "Aucun boost actif ou récent."}

    pool = await get_pool()
    async with pool.acquire() as conn:
        agg = await conn.fetchrow(
            """SELECT COUNT(*)            AS total_spins,
                      COUNT(DISTINCT user_id) AS dau,
                      COUNT(*) FILTER (WHERE jackpot_triggered=TRUE) AS jackpots,
                      COUNT(*) FILTER (WHERE near_miss=TRUE)         AS near_misses,
                      COUNT(*) FILTER (WHERE prize_slot=1)           AS perdu_count,
                      COUNT(*) FILTER (WHERE points_awarded>0)       AS win_count,
                      COALESCE(SUM(points_awarded), 0)               AS total_points,
                      COALESCE(AVG(points_awarded), 0)::FLOAT        AS avg_points,
                      MIN(spin_at)                                    AS first_spin_at,
                      MAX(spin_at)                                    AS last_spin_at
                 FROM wheel_spins
                WHERE boost_id = $1""",
            target_id,
        )
        per_slot = await conn.fetch(
            """SELECT prize_slot, COUNT(*) AS n
                 FROM wheel_spins
                WHERE boost_id = $1
                GROUP BY prize_slot ORDER BY prize_slot""",
            target_id,
        )

    total = int(agg["total_spins"] or 0)
    win_rate = round(100 * int(agg["win_count"] or 0) / total, 1) if total else 0.0
    perdu_rate = round(100 * int(agg["perdu_count"] or 0) / total, 1) if total else 0.0
    return {
        "boost_id": target_id,
        "stats": {
            "total_spins": total,
            "dau": int(agg["dau"] or 0),
            "jackpots": int(agg["jackpots"] or 0),
            "near_misses": int(agg["near_misses"] or 0),
            "perdu_count": int(agg["perdu_count"] or 0),
            "win_count": int(agg["win_count"] or 0),
            "total_points": int(agg["total_points"] or 0),
            "avg_points": round(float(agg["avg_points"] or 0), 2),
            "win_rate_percent": win_rate,
            "perdu_rate_percent": perdu_rate,
            "first_spin_at": agg["first_spin_at"].isoformat() if agg["first_spin_at"] else None,
            "last_spin_at": agg["last_spin_at"].isoformat() if agg["last_spin_at"] else None,
            "by_slot": [
                {"prize_slot": int(r["prize_slot"]),
                 "label": next((s["label"] for s in WHEEL_SLOTS if s["idx"] == int(r["prize_slot"])), "?"),
                 "count": int(r["n"])}
                for r in per_slot
            ],
        },
        "live": await get_active_wheel_boost(),
    }


# ══════════════════════════════════════════════════════════════════════════
#  iter115 — Wheel Boost Schedules CRUD (recurring + dated)
# ══════════════════════════════════════════════════════════════════════════
class BoostScheduleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    kind: str = Field("recurring", pattern="^(recurring|dated)$")
    dow_start: Optional[int] = Field(default=None, ge=0, le=6)
    time_start: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    dow_end: Optional[int] = Field(default=None, ge=0, le=6)
    time_end: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    date_start: Optional[str] = Field(default=None, max_length=40)
    date_end: Optional[str] = Field(default=None, max_length=40)
    gain_multiplier: float = Field(1.5, ge=1.0, le=5.0)
    perdu_reduction_percent: int = Field(50, ge=0, le=95)
    unlock_jackpot_all_phases: bool = False
    jackpot_odds_during_boost: int = Field(25, ge=0, le=100)
    enabled: bool = True


def _serialize_schedule(r: dict) -> dict:
    return {
        "id": int(r["id"]),
        "name": r["name"],
        "kind": r["kind"],
        "dow_start": r["dow_start"],
        "time_start": r["time_start"].strftime("%H:%M") if r["time_start"] else None,
        "dow_end": r["dow_end"],
        "time_end": r["time_end"].strftime("%H:%M") if r["time_end"] else None,
        "date_start": r["date_start"].isoformat() if r["date_start"] else None,
        "date_end": r["date_end"].isoformat() if r["date_end"] else None,
        "gain_multiplier": float(r["gain_multiplier"]),
        "perdu_reduction_percent": int(r["perdu_reduction_percent"]),
        "unlock_jackpot_all_phases": bool(r["unlock_jackpot_all_phases"]),
        "jackpot_odds_during_boost": int(r["jackpot_odds_during_boost"]),
        "enabled": bool(r["enabled"]),
        "last_triggered_at": r["last_triggered_at"].isoformat() if r["last_triggered_at"] else None,
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


def _validate_schedule_payload(req: BoostScheduleRequest) -> tuple:
    from datetime import time as _time
    if req.kind == "recurring":
        if (req.dow_start is None or req.time_start is None
                or req.dow_end is None or req.time_end is None):
            raise HTTPException(status_code=400,
                detail="Pour kind='recurring', dow_start/time_start/dow_end/time_end sont obligatoires.")
        try:
            h1, m1 = (int(x) for x in req.time_start.split(":"))
            h2, m2 = (int(x) for x in req.time_end.split(":"))
            if not (0 <= h1 < 24 and 0 <= m1 < 60 and 0 <= h2 < 24 and 0 <= m2 < 60):
                raise ValueError("hour/min out of range")
            ts = _time(h1, m1)
            te = _time(h2, m2)
        except Exception:
            raise HTTPException(status_code=400, detail="Format time HH:MM invalide.")
        return (req.dow_start, ts, req.dow_end, te, None, None)
    if not req.date_start or not req.date_end:
        raise HTTPException(status_code=400,
            detail="Pour kind='dated', date_start et date_end sont obligatoires (ISO 8601).")
    try:
        ds = datetime.fromisoformat(req.date_start.replace("Z", "+00:00"))
        de = datetime.fromisoformat(req.date_end.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400,
            detail="date_start/date_end doivent être au format ISO 8601 (ex: 2026-05-01T00:00:00Z).")
    if ds >= de:
        raise HTTPException(status_code=400,
                            detail="date_end doit être strictement après date_start.")
    return (None, None, None, None, ds, de)


@router.get("/admin/boost/schedules")
async def admin_list_boost_schedules(request: Request):
    from routes.auth import require_admin as _ra
    from services.wheel_boost_scheduler import ensure_schedules_ddl
    await _ra(request)
    await ensure_schedules_ddl()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM wheel_boost_schedules ORDER BY enabled DESC, id ASC")
    return {"schedules": [_serialize_schedule(dict(r)) for r in rows]}


@router.post("/admin/boost/schedules")
async def admin_create_boost_schedule(req: BoostScheduleRequest, request: Request):
    from routes.auth import require_admin as _ra
    from services.wheel_boost_scheduler import ensure_schedules_ddl
    user = await _ra(request)
    dow_s, t_s, dow_e, t_e, ds, de = _validate_schedule_payload(req)
    await ensure_schedules_ddl()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wheel_boost_schedules
                 (name, kind, dow_start, time_start, dow_end, time_end,
                  date_start, date_end, gain_multiplier, perdu_reduction_percent,
                  unlock_jackpot_all_phases, jackpot_odds_during_boost, enabled)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
               RETURNING *""",
            req.name.strip(), req.kind, dow_s, t_s, dow_e, t_e, ds, de,
            float(req.gain_multiplier), int(req.perdu_reduction_percent),
            bool(req.unlock_jackpot_all_phases),
            int(req.jackpot_odds_during_boost), bool(req.enabled),
        )
    try:
        from services.security_service import log_security_event
        await log_security_event(user["user_id"], "wheel.admin_boost_schedule_create",
            severity="info", ip="", ua="",
            details={"id": int(row["id"]), "name": req.name, "kind": req.kind})
    except Exception:
        pass
    return {"status": "ok", "schedule": _serialize_schedule(dict(row))}


@router.put("/admin/boost/schedules/{sched_id}")
async def admin_update_boost_schedule(sched_id: int,
                                       req: BoostScheduleRequest, request: Request):
    from routes.auth import require_admin as _ra
    user = await _ra(request)
    dow_s, t_s, dow_e, t_e, ds, de = _validate_schedule_payload(req)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE wheel_boost_schedules
                  SET name=$1, kind=$2, dow_start=$3, time_start=$4,
                      dow_end=$5, time_end=$6, date_start=$7, date_end=$8,
                      gain_multiplier=$9, perdu_reduction_percent=$10,
                      unlock_jackpot_all_phases=$11,
                      jackpot_odds_during_boost=$12, enabled=$13,
                      updated_at=NOW()
                WHERE id=$14
                RETURNING *""",
            req.name.strip(), req.kind, dow_s, t_s, dow_e, t_e, ds, de,
            float(req.gain_multiplier), int(req.perdu_reduction_percent),
            bool(req.unlock_jackpot_all_phases),
            int(req.jackpot_odds_during_boost), bool(req.enabled),
            sched_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Schedule introuvable")
    try:
        from services.security_service import log_security_event
        await log_security_event(user["user_id"], "wheel.admin_boost_schedule_update",
            severity="info", ip="", ua="", details={"id": sched_id})
    except Exception:
        pass
    return {"status": "ok", "schedule": _serialize_schedule(dict(row))}


@router.delete("/admin/boost/schedules/{sched_id}")
async def admin_delete_boost_schedule(sched_id: int, request: Request):
    from routes.auth import require_admin as _ra
    user = await _ra(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM wheel_boost_schedules WHERE id=$1 RETURNING id", sched_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule introuvable")
    try:
        from services.security_service import log_security_event
        await log_security_event(user["user_id"], "wheel.admin_boost_schedule_delete",
            severity="warning", ip="", ua="", details={"id": sched_id})
    except Exception:
        pass
    return {"status": "ok", "deleted_id": int(sched_id)}



# ══════════════════════════════════════════════════════════════════════════
#  Admin actions & strategic KPIs (iter83 — mode pilotage business)
# ══════════════════════════════════════════════════════════════════════════

@router.post("/admin/cycles/{cycle_id}/force-claim")
async def admin_force_claim(cycle_id: int, request: Request):
    """Grant the Starter Pro immediately for a reward_pending cycle, without
    requiring the user to click. Only admin-triggered. Fails if the cycle
    hasn't actually reached both goals (10 000 pts + 25 days)."""
    from routes.auth import require_admin as _ra
    admin = await _ra(request)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            cycle = await conn.fetchrow(
                """SELECT * FROM wheel_cycles WHERE id = $1 FOR UPDATE""",
                cycle_id,
            )
            if not cycle:
                raise HTTPException(status_code=404, detail="Cycle introuvable.")
            if cycle["reward_status"] not in (
                CYCLE_STATUS_IN_PROGRESS, CYCLE_STATUS_REWARD_PENDING,
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cycle dans un état non claimable ({cycle['reward_status']}).",
                )
            if int(cycle["points_cycle"]) < POINTS_GOAL:
                raise HTTPException(
                    status_code=400,
                    detail=f"Points insuffisants : {cycle['points_cycle']}/{POINTS_GOAL}.",
                )
            if int(cycle["days_played_count"]) < DAYS_GOAL:
                raise HTTPException(
                    status_code=400,
                    detail=f"Jours insuffisants : {cycle['days_played_count']}/{DAYS_GOAL}.",
                )
            # Quiz performance is also enforced for admin force-claim — no bypass.
            from services.points_service import is_quiz_performance_met, quiz_accuracy
            if not is_quiz_performance_met(dict(cycle)):
                raise HTTPException(
                    status_code=400,
                    detail=f"Performance quiz insuffisante ({quiz_accuracy(dict(cycle))*100:.0f}% sur "
                           f"{cycle['quiz_answers_total']} réponses) — requis 75% sur ≥50.",
                )

            today = _today()
            first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
            expire_at = first_next + timedelta(days=30)
            _expire_dt = datetime.combine(expire_at, datetime.min.time(), tzinfo=timezone.utc)

            user_row = await conn.fetchrow(
                "SELECT is_pro, pro_expires_at FROM users WHERE user_id = $1",
                cycle["user_id"],
            )
            final_expiry = _expire_dt
            if user_row and user_row["pro_expires_at"] and user_row["pro_expires_at"] > _expire_dt:
                final_expiry = user_row["pro_expires_at"]
            await conn.execute(
                """UPDATE users SET is_pro = TRUE, pro_type = 1,
                       pro_expires_at = $1, updated_at = NOW()
                   WHERE user_id = $2""",
                final_expiry, cycle["user_id"],
            )
            await conn.execute(
                """UPDATE wheel_cycles SET reward_status = $1,
                       reward_claimed_at = NOW(), updated_at = NOW()
                   WHERE id = $2""",
                CYCLE_STATUS_REWARD_CLAIMED, cycle_id,
            )

    # Audit log
    try:
        from services.security_service import log_security_event
        await log_security_event(
            cycle["user_id"], "wheel.admin_force_claim",
            severity="info", ip="", ua="",
            details={"cycle_id": cycle_id, "by_admin": admin.get("user_id")},
        )
    except Exception:
        pass

    return {
        "status": "ok",
        "cycle_id": cycle_id,
        "user_id": cycle["user_id"],
        "activation_date": first_next.isoformat(),
        "expire_date": expire_at.isoformat(),
    }


@router.post("/admin/cycles/{cycle_id}/reset-suspicious")
async def admin_reset_suspicious(cycle_id: int, request: Request):
    """Clear the suspicious_flag on a cycle after manual review. Removes the
    silent degradation (25pts cap, no jackpot) so the user can play normally
    again."""
    from routes.auth import require_admin as _ra
    admin = await _ra(request)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE wheel_cycles
               SET suspicious_flag = FALSE, updated_at = NOW()
               WHERE id = $1 RETURNING user_id, suspicious_flag""",
            cycle_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Cycle introuvable.")
    try:
        from services.security_service import log_security_event
        await log_security_event(
            row["user_id"], "wheel.admin_reset_suspicious",
            severity="info", ip="", ua="",
            details={"cycle_id": cycle_id, "by_admin": admin.get("user_id")},
        )
    except Exception:
        pass
    return {"status": "ok", "cycle_id": cycle_id, "suspicious_flag": False}


@router.get("/admin/strategic-kpis")
async def admin_strategic_kpis(request: Request):
    """Business-level indicators that answer in 5 seconds :
      - Does the engine work? (completion rate cycles → Starter Pro)
      - Is it too hard? (% blocked before 25 days, avg days played)
      - Is it being exploited? (avg points at j+10 / j+20)
    """
    from routes.auth import require_admin as _ra
    await _ra(request)

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Completion rate : claimed / all cycles that ended (any terminal state)
        completion = await conn.fetchrow(
            """SELECT
                 COUNT(*) FILTER (WHERE reward_status = 'reward_claimed') AS claimed,
                 COUNT(*) FILTER (WHERE reward_status IN ('reward_claimed','completed_won','completed_lost')) AS terminal_total,
                 COUNT(*) FILTER (WHERE reward_status = 'completed_lost') AS lost,
                 COUNT(*) FILTER (WHERE reward_status = 'completed_lost' AND days_played_count < $1) AS blocked_below_goal
               FROM wheel_cycles""",
            DAYS_GOAL,
        )
        terminal = int(completion["terminal_total"] or 0)
        completion_rate = (int(completion["claimed"] or 0) / terminal) if terminal else 0.0
        blocked_pct = (int(completion["blocked_below_goal"] or 0) / terminal) if terminal else 0.0

        # Average days played (claimed cycles only — full signal)
        avg_days = await conn.fetchval(
            """SELECT AVG(days_played_count)::FLOAT FROM wheel_cycles
               WHERE reward_status IN ('reward_claimed','completed_won')"""
        )

        # Average points at day 10 / day 20 of the cycle — proxy for difficulty
        # We compute SUM(points_awarded) for each user/cycle across spins that
        # happened on/before the Nth day of the cycle. Then average across cycles.
        pts_j10 = await conn.fetchval(
            """SELECT AVG(pts)::FLOAT FROM (
                 SELECT c.id,
                        COALESCE(SUM(s.points_awarded) FILTER (
                           WHERE s.spin_date <= c.cycle_start_date + INTERVAL '10 days'
                        ), 0) AS pts
                 FROM wheel_cycles c
                 LEFT JOIN wheel_spins s ON s.cycle_id = c.id
                 WHERE c.reward_status IN ('in_progress','reward_claimed','reward_pending','completed_won','completed_lost')
                 GROUP BY c.id
                 HAVING COUNT(s.id) > 0
               ) t"""
        )
        pts_j20 = await conn.fetchval(
            """SELECT AVG(pts)::FLOAT FROM (
                 SELECT c.id,
                        COALESCE(SUM(s.points_awarded) FILTER (
                           WHERE s.spin_date <= c.cycle_start_date + INTERVAL '20 days'
                        ), 0) AS pts
                 FROM wheel_cycles c
                 LEFT JOIN wheel_spins s ON s.cycle_id = c.id
                 WHERE c.reward_status IN ('in_progress','reward_claimed','reward_pending','completed_won','completed_lost')
                 GROUP BY c.id
                 HAVING COUNT(s.id) > 0
               ) t"""
        )

    # Health verdict
    if terminal == 0:
        verdict = "insufficient_data"
        verdict_msg = "Pas assez de cycles terminés pour juger — patientez."
    elif completion_rate < 0.02 and terminal >= 50:
        verdict = "too_hard"
        verdict_msg = "Taux de complétion très bas : la roue est probablement trop difficile."
    elif completion_rate > 0.4:
        verdict = "too_easy_or_exploited"
        verdict_msg = "Taux de complétion anormalement élevé : vérifiez les anomalies."
    else:
        verdict = "healthy"
        verdict_msg = "Balance engagement / difficulté saine."

    return {
        "completion_rate": round(completion_rate, 3),          # 0.0..1.0
        "blocked_below_goal_pct": round(blocked_pct, 3),       # part des échecs dûs < 25j
        "avg_days_played": round(float(avg_days or 0), 1),     # jours moyens des gagnants/expirés gagnants
        "avg_points_j10": round(float(pts_j10 or 0), 0),        # points moyens après 10j
        "avg_points_j20": round(float(pts_j20 or 0), 0),        # points moyens après 20j
        "terminal_cycles": terminal,
        "claimed_cycles": int(completion["claimed"] or 0),
        "verdict": verdict,
        "verdict_message": verdict_msg,
    }


@router.get("/admin/cycles/export.csv")
async def admin_export_cycles_csv(
    request: Request,
    status: str = "all",
    date_from: str = "",
    date_to: str = "",
    suspicious_only: bool = False,
):
    """Stream a CSV export of cycles, filterable by status + date range +
    suspicious. Usable in Excel / Numbers / Airtable."""
    from routes.auth import require_admin as _ra
    from fastapi.responses import StreamingResponse
    import csv
    import io
    await _ra(request)

    where_parts = ["1=1"]
    if status == "in_progress":
        where_parts.append("wc.reward_status = 'in_progress'")
    elif status == "reward_pending":
        where_parts.append("wc.reward_status = 'reward_pending'")
    elif status == "reward_claimed":
        where_parts.append("wc.reward_status = 'reward_claimed'")
    elif status in ("completed_won", "completed_lost"):
        where_parts.append(f"wc.reward_status = '{status}'")
    if suspicious_only:
        where_parts.append("wc.suspicious_flag = TRUE")
    if date_from:
        where_parts.append(f"wc.cycle_start_date >= '{date_from}'::date")
    if date_to:
        where_parts.append(f"wc.cycle_start_date <= '{date_to}'::date")
    where_sql = " AND ".join(where_parts)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT
                  wc.id, wc.user_id, wc.cycle_start_date, wc.cycle_end_date,
                  wc.points_cycle, wc.days_played_count, wc.streak_days,
                  wc.suspicious_flag, wc.reward_status, wc.reward_claimed_at,
                  wc.last_played_date, wc.updated_at,
                  u.username, u.email, u.is_pro
                FROM wheel_cycles wc
                LEFT JOIN users u ON u.user_id = wc.user_id
                WHERE {where_sql}
                ORDER BY wc.updated_at DESC
                LIMIT 10000""",
        )

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "cycle_id", "user_id", "username", "email", "is_pro",
        "cycle_start_date", "cycle_end_date",
        "points_cycle", "points_goal",
        "days_played_count", "days_goal",
        "streak_days", "suspicious_flag",
        "reward_status", "reward_claimed_at",
        "last_played_date", "updated_at",
    ])
    for r in rows:
        w.writerow([
            r["id"], r["user_id"], r["username"] or "", r["email"] or "",
            bool(r["is_pro"]),
            r["cycle_start_date"].isoformat() if r["cycle_start_date"] else "",
            r["cycle_end_date"].isoformat() if r["cycle_end_date"] else "",
            int(r["points_cycle"] or 0), POINTS_GOAL,
            int(r["days_played_count"] or 0), DAYS_GOAL,
            int(r["streak_days"] or 0),
            bool(r["suspicious_flag"]),
            r["reward_status"],
            r["reward_claimed_at"].isoformat() if r["reward_claimed_at"] else "",
            r["last_played_date"].isoformat() if r["last_played_date"] else "",
            r["updated_at"].isoformat() if r["updated_at"] else "",
        ])
    buf.seek(0)
    filename = f"japap-wheel-cycles-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )



# ══════════════════════════════════════════════════════════════════════════
#  Phase finale (J+7) — timeseries + rapport décisionnel
# ══════════════════════════════════════════════════════════════════════════

@router.get("/admin/timeseries")
async def admin_timeseries(request: Request, days: int = 7):
    """Daily time-series for the admin chart. For each of the last N days
    (default 7) returns :
      - date                          (ISO YYYY-MM-DD, UTC)
      - dau_24h                       (distinct users who spun that day)
      - total_spins                   (all spins that day)
      - cycles_ended                  (cycles that flipped to any terminal state that day)
      - cycles_claimed                (sub-set that were actually claimed)
      - completion_rate               (cycles_claimed / cycles_ended — 0..1)
      - avg_points_cycle              (avg points_cycle across in_progress cycles "as of" EOD)

    Sufficient to detect the two failure modes the product team cares about :
      • DAU en chute + completion_rate qui monte  → churn post-reward
      • DAU constant mais completion_rate qui explose → trop facile / exploité
    """
    from routes.auth import require_admin as _ra
    await _ra(request)
    days = max(1, min(int(days), 90))

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Daily spins & DAU
        spins = await conn.fetch(
            """SELECT spin_date::text AS day,
                      COUNT(*) AS total_spins,
                      COUNT(DISTINCT user_id) AS dau
               FROM wheel_spins
               WHERE spin_date >= CURRENT_DATE - ($1::int - 1)
               GROUP BY spin_date""",
            days,
        )
        spins_map = {r["day"]: r for r in spins}

        # Cycles ended each day (terminal transitions detected via updated_at
        # on non-in_progress rows). Completion rate = claimed / ended.
        terminals = await conn.fetch(
            """SELECT DATE(updated_at AT TIME ZONE 'UTC')::text AS day,
                      COUNT(*) FILTER (
                        WHERE reward_status IN ('reward_claimed','completed_won','completed_lost')
                      ) AS cycles_ended,
                      COUNT(*) FILTER (WHERE reward_status = 'reward_claimed') AS cycles_claimed
               FROM wheel_cycles
               WHERE updated_at >= CURRENT_DATE - ($1::int - 1)
                 AND reward_status IN ('reward_claimed','completed_won','completed_lost')
               GROUP BY DATE(updated_at AT TIME ZONE 'UTC')""",
            days,
        )
        terminals_map = {r["day"]: r for r in terminals}

        # Avg points_cycle across in_progress (snapshot aujourd'hui seulement :
        # on n'a pas d'historique par jour, mais on retourne la valeur actuelle
        # sur chaque ligne pour que la courbe reste tracée). C'est un proxy
        # acceptable et honnête que nous documentons dans le tooltip frontend.
        current_avg = await conn.fetchval(
            """SELECT AVG(points_cycle)::FLOAT FROM wheel_cycles
               WHERE reward_status = 'in_progress'"""
        )
        current_avg = float(current_avg or 0)

    today = _today()
    series = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        key = d.isoformat()
        s = spins_map.get(key)
        t = terminals_map.get(key)
        ended = int(t["cycles_ended"]) if t else 0
        claimed = int(t["cycles_claimed"]) if t else 0
        completion_rate = round(claimed / ended, 3) if ended else 0.0
        series.append({
            "date": key,
            "dau_24h": int(s["dau"]) if s else 0,
            "total_spins": int(s["total_spins"]) if s else 0,
            "cycles_ended": ended,
            "cycles_claimed": claimed,
            "completion_rate": completion_rate,
            "avg_points_cycle": round(current_avg, 0),
        })
    return {"days": days, "series": series}


@router.get("/admin/j7-report")
async def admin_j7_report(request: Request):
    """Generate the decision-ready J+7 report in one shot. Returns a
    structured JSON reflecting the 6 sections required by product :
       1. activity    → DAU, cycles launched, cycles ended
       2. performance → completion_rate, blocked%, time to 10k, pts@j10/j20/j25
       3. behaviour   → suspicious accounts (with user list for outreach)
       4. winners     → Pro conversions (normal vs suspicious) with user list
       5. analysis    → one-line verdict (too_hard / too_easy / exploited / healthy)
       6. reco        → keep / adjust / turnstile_on (argumented decision)
    """
    from routes.auth import require_admin as _ra
    await _ra(request)

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. Activity
        activity = await conn.fetchrow(
            """SELECT
                 (SELECT COUNT(DISTINCT user_id) FROM wheel_spins
                  WHERE spin_at > NOW() - INTERVAL '24h')                    AS dau_24h,
                 (SELECT COUNT(DISTINCT user_id) FROM wheel_spins
                  WHERE spin_at > NOW() - INTERVAL '7 days')                  AS wau,
                 (SELECT COUNT(*) FROM wheel_cycles)                          AS cycles_total,
                 (SELECT COUNT(*) FROM wheel_cycles
                  WHERE reward_status IN ('reward_claimed','completed_won','completed_lost'))
                                                                              AS cycles_ended"""
        )

        # 2. Performance
        perf = await conn.fetchrow(
            """SELECT
                 COUNT(*) FILTER (WHERE reward_status = 'reward_claimed')   AS claimed,
                 COUNT(*) FILTER (
                   WHERE reward_status IN ('reward_claimed','completed_won','completed_lost')
                 )                                                          AS terminal,
                 COUNT(*) FILTER (
                   WHERE reward_status = 'completed_lost'
                     AND days_played_count < $1
                 )                                                          AS blocked,
                 AVG(days_played_count) FILTER (WHERE reward_status = 'reward_claimed')::FLOAT
                                                                            AS avg_days_to_win
               FROM wheel_cycles""",
            DAYS_GOAL,
        )
        terminal = int(perf["terminal"] or 0)
        completion_rate = round(int(perf["claimed"] or 0) / terminal, 3) if terminal else 0.0
        blocked_pct = round(int(perf["blocked"] or 0) / terminal, 3) if terminal else 0.0

        # Avg points at j10/j20/j25
        async def _pts_at_day(n: int) -> float:
            v = await conn.fetchval(
                f"""SELECT AVG(pts)::FLOAT FROM (
                      SELECT c.id,
                             COALESCE(SUM(s.points_awarded) FILTER (
                               WHERE s.spin_date <= c.cycle_start_date + INTERVAL '{n} days'
                             ), 0) AS pts
                      FROM wheel_cycles c
                      LEFT JOIN wheel_spins s ON s.cycle_id = c.id
                      GROUP BY c.id
                      HAVING COUNT(s.id) > 0
                    ) t"""
            )
            return round(float(v or 0), 0)

        pts_j10 = await _pts_at_day(10)
        pts_j20 = await _pts_at_day(20)
        pts_j25 = await _pts_at_day(25)

        # 3. Behaviour — suspicious accounts with outreach info
        suspects = await conn.fetch(
            """SELECT c.user_id, c.id AS cycle_id, c.points_cycle, c.days_played_count,
                      c.reward_status, c.updated_at,
                      u.email, u.username,
                      (u.first_name || ' ' || u.last_name) AS display_name
               FROM wheel_cycles c
               LEFT JOIN users u ON u.user_id = c.user_id
               WHERE c.suspicious_flag = TRUE
               ORDER BY c.updated_at DESC
               LIMIT 50"""
        )

        # Patterns (re-use observability heuristics)
        patterns = await conn.fetchrow(
            """SELECT
                 (SELECT COUNT(*) FROM (
                   SELECT user_id FROM wheel_spins
                   WHERE spin_at > NOW() - INTERVAL '24h'
                   GROUP BY user_id HAVING COUNT(*) >= $1 * 24
                 ) t)                                                   AS bot_like_count,
                 (SELECT COUNT(*) FROM (
                   SELECT ip_address FROM wheel_spins
                   WHERE spin_at > NOW() - INTERVAL '7 days' AND ip_address <> ''
                   GROUP BY ip_address HAVING COUNT(DISTINCT user_id) >= $2
                 ) t)                                                   AS multi_ip_count,
                 (SELECT COUNT(*) FROM (
                   SELECT device_fingerprint FROM wheel_spins
                   WHERE spin_at > NOW() - INTERVAL '7 days' AND device_fingerprint <> ''
                   GROUP BY device_fingerprint HAVING COUNT(DISTINCT user_id) >= $3
                 ) t)                                                   AS multi_fp_count""",
            ANOMALY_THRESHOLD_SPINS_PER_HOUR,
            ANOMALY_THRESHOLD_SAME_IP_USERS,
            ANOMALY_THRESHOLD_SAME_FP_USERS,
        )

        # 4. Winners — Pro conversions, separated by clean vs suspicious
        winners = await conn.fetch(
            """SELECT c.user_id, c.id AS cycle_id, c.points_cycle, c.days_played_count,
                      c.suspicious_flag, c.reward_claimed_at,
                      u.email, u.username, u.is_pro, u.pro_expires_at,
                      (u.first_name || ' ' || u.last_name) AS display_name
               FROM wheel_cycles c
               LEFT JOIN users u ON u.user_id = c.user_id
               WHERE c.reward_status = 'reward_claimed'
               ORDER BY c.reward_claimed_at DESC
               LIMIT 100"""
        )
        clean_winners = [w for w in winners if not w["suspicious_flag"]]
        suspect_winners = [w for w in winners if w["suspicious_flag"]]

    # 5. Analysis verdict (ONE line, no alternatives)
    if terminal < 20:
        verdict = "insufficient_data"
        verdict_line = "Données insuffisantes pour conclure — attendre un échantillon de ≥20 cycles terminés."
    elif completion_rate > 0.35:
        verdict = "too_easy_or_exploited"
        if int(patterns["bot_like_count"] or 0) + int(patterns["multi_fp_count"] or 0) > 0:
            verdict_line = f"Le système est exploité (≥{int(patterns['bot_like_count'])} bot-like + {int(patterns['multi_fp_count'])} multi-FP)."
        else:
            verdict_line = "Le système est trop facile — les gagnants sortent trop vite."
    elif completion_rate < 0.03 and blocked_pct > 0.6:
        verdict = "too_hard"
        verdict_line = "Le système est trop difficile — la majorité des utilisateurs échoue avant 25 jours."
    else:
        verdict = "healthy"
        verdict_line = "Le système fonctionne dans les bornes attendues."

    # 6. Recommendation
    if verdict == "too_easy_or_exploited":
        if int(patterns["bot_like_count"] or 0) + int(patterns["multi_fp_count"] or 0) > 0:
            reco = "turnstile_on"
            reco_reason = "Patterns de bot ou multi-comptes détectés — activer Turnstile immédiatement pour bloquer l'exploitation."
        else:
            reco = "adjust_harder"
            reco_reason = "Trop facile : augmenter `cooldown_seconds` à 60s, baisser `jackpot_odds_in_window` à 15%."
    elif verdict == "too_hard":
        reco = "adjust_easier"
        reco_reason = "Trop difficile : relever les bonus de streak (3j/7j/15j) ou ajouter un événement boost week-end."
    elif verdict == "insufficient_data":
        reco = "keep_as_is"
        reco_reason = "Patienter jusqu'à un échantillon exploitable."
    else:
        reco = "keep_as_is"
        reco_reason = "Le moteur converge. Maintenir la configuration et continuer la collecte."

    def _u_row(r):
        return {
            "user_id": r["user_id"],
            "cycle_id": int(r["cycle_id"]),
            "email": r["email"] or "",
            "username": r["username"] or "",
            "display_name": (r["display_name"] or "").strip() or (r["username"] or r["user_id"]),
            "points_cycle": int(r["points_cycle"] or 0),
            "days_played_count": int(r["days_played_count"] or 0),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": "rolling_since_start",
        # 1
        "activity": {
            "dau_24h": int(activity["dau_24h"] or 0),
            "wau": int(activity["wau"] or 0),
            "cycles_total": int(activity["cycles_total"] or 0),
            "cycles_ended": int(activity["cycles_ended"] or 0),
        },
        # 2
        "performance": {
            "completion_rate": completion_rate,
            "blocked_below_goal_pct": blocked_pct,
            "avg_days_to_win": round(float(perf["avg_days_to_win"] or 0), 1),
            "avg_points_j10": pts_j10,
            "avg_points_j20": pts_j20,
            "avg_points_j25": pts_j25,
        },
        # 3
        "behaviour": {
            "suspicious_count": len(suspects),
            "suspicious_users": [_u_row(s) | {"reward_status": s["reward_status"]}
                                 for s in suspects],
            "patterns": {
                "bot_like_accounts": int(patterns["bot_like_count"] or 0),
                "multi_ip_clusters": int(patterns["multi_ip_count"] or 0),
                "multi_fingerprint_clusters": int(patterns["multi_fp_count"] or 0),
            },
        },
        # 4
        "winners": {
            "clean_winners_count": len(clean_winners),
            "suspect_winners_count": len(suspect_winners),
            "clean_winners": [_u_row(w) for w in clean_winners],
            "suspect_winners": [_u_row(w) for w in suspect_winners],
        },
        # 5
        "analysis": {
            "verdict": verdict,
            "verdict_line": verdict_line,
        },
        # 6
        "recommendation": {
            "action": reco,                     # keep_as_is | adjust_harder | adjust_easier | turnstile_on
            "reason": reco_reason,
        },
    }

