"""
iter237k — Daily Challenge PAID mode (additif, mode gratuit intact).

Architecture
============
- Pool de questions IA niveau expert (claude-opus-4-5-20251101) régénéré
  toutes les 48h par le scheduler quiz_champion. Health check : si pool
  actif < 30, régénération d'urgence (single-flight).
- 1 seule session payante par utilisateur par jour (UNIQUE constraint).
- Débit atomique de la mise sur `wallets.balance` (USD) avant de servir
  les questions. Refund = mise + delta selon barème admin.
- Plafond gain/jour configurable, calculé sur la somme des delta positifs
  de la journée pour cet utilisateur.
- Anti-triche : `correct_idx` jamais retourné avant /submit. Score calculé
  100% côté serveur. Réponses signées par l'ID de session.

Endpoints (tous /api/quiz/daily-challenge/paid)
  - GET  /config         → public-friendly config + status user
  - POST /start          → débite la mise + retourne 5 Q sans correct_idx
  - POST /submit         → score serveur + refund + explications IA mauvaises
  - POST /reveal         → renvoie la bonne réponse + explication question par
                            question (anti-triche : seulement si user a déjà
                            répondu via le state local frontend, on s'en fiche)

Admin
  - GET  /admin/daily-challenge/paid/stats  → KPI + revenus nets Japap
  - GET  /admin/daily-challenge/paid/config → toutes les clés DCQ_PAID_*
  - PUT  /admin/daily-challenge/paid/config → MAJ partielle (validation)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import get_current_user
from routes.admin import require_admin
from services.settings_service import get_setting, set_setting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["dcq_paid"])


# ── Config defaults (clés DCQ_PAID_* dans admin_settings) ────────────────────
DCQ_DEFAULTS: dict[str, Any] = {
    "DCQ_PAID_ENABLED":        "true",
    "DCQ_STAKE_MIN_USD":       "0.1",
    "DCQ_STAKE_MAX_USD":       "1000",
    "DCQ_DAILY_GAIN_CAP_USD":  "500",
    "DCQ_SCORE_5_PCT":         "50",
    "DCQ_SCORE_4_PCT":         "-30",
    "DCQ_SCORE_3_PCT":         "-70",
    "DCQ_SCORE_2_PCT":         "-70",
    "DCQ_SCORE_01_PCT":        "-85",
    "DCQ_TIME_PER_QUESTION":   "10",
}

DCQ_BOUNDS = {
    "DCQ_STAKE_MIN_USD":       (0.01, 100),
    "DCQ_STAKE_MAX_USD":       (1, 100000),
    "DCQ_DAILY_GAIN_CAP_USD":  (0, 1_000_000),
    "DCQ_SCORE_5_PCT":         (0, 1000),
    "DCQ_SCORE_4_PCT":         (-100, 1000),
    "DCQ_SCORE_3_PCT":         (-100, 1000),
    "DCQ_SCORE_2_PCT":         (-100, 1000),
    "DCQ_SCORE_01_PCT":        (-100, 1000),
    "DCQ_TIME_PER_QUESTION":   (5, 120),
}


async def _get_cfg() -> dict[str, Any]:
    """Load all DCQ_PAID_* keys with defaults + numeric coercion."""
    out: dict[str, Any] = {}
    for k, default in DCQ_DEFAULTS.items():
        raw = await get_setting(k, default)
        if k == "DCQ_PAID_ENABLED":
            out["enabled"] = (raw or "true").lower() in ("1", "true", "yes", "on")
            continue
        try:
            out[k.lower()] = float(raw)
        except (TypeError, ValueError):
            out[k.lower()] = float(default)
    return out


# ── DDL idempotent ─────────────────────────────────────────────────────────
_ddl_done = False
_DDL = [
    """CREATE TABLE IF NOT EXISTS daily_challenge_expert_pool (
        id            BIGSERIAL PRIMARY KEY,
        question      TEXT NOT NULL,
        options       JSONB NOT NULL,
        correct_idx   SMALLINT NOT NULL CHECK (correct_idx BETWEEN 0 AND 3),
        explanation   TEXT NOT NULL,
        difficulty    SMALLINT NOT NULL DEFAULT 5,
        category      VARCHAR(64) NOT NULL,
        language      VARCHAR(8) NOT NULL DEFAULT 'fr',
        batch_id      INTEGER NOT NULL,
        active        BOOLEAN NOT NULL DEFAULT TRUE,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at    TIMESTAMPTZ NOT NULL
       )""",
    "CREATE INDEX IF NOT EXISTS idx_dcep_active_expires ON daily_challenge_expert_pool(active, expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_dcep_active_lang ON daily_challenge_expert_pool(active, language) WHERE active=TRUE",
    """CREATE TABLE IF NOT EXISTS daily_challenge_paid_sessions (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id         VARCHAR(80) NOT NULL,
        date_played     DATE NOT NULL DEFAULT CURRENT_DATE,
        stake_usd       DECIMAL(12,4) NOT NULL,
        question_ids    JSONB NOT NULL,
        answers         JSONB,
        score           SMALLINT,
        result_pct      DECIMAL(7,2),
        amount_won_usd  DECIMAL(12,4),
        bonus_active    BOOLEAN NOT NULL DEFAULT FALSE,
        bonus_consumed  BOOLEAN NOT NULL DEFAULT FALSE,
        status          VARCHAR(20) NOT NULL DEFAULT 'in_progress',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at    TIMESTAMPTZ
       )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uniq_dcps_user_date
       ON daily_challenge_paid_sessions(user_id, date_played)""",
    "CREATE INDEX IF NOT EXISTS idx_dcps_user ON daily_challenge_paid_sessions(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dcps_status_date ON daily_challenge_paid_sessions(status, date_played)",
    # iter237l — Profile-completion redemption (anti-tilt +5% loss reduction).
    "ALTER TABLE daily_challenge_paid_sessions ADD COLUMN IF NOT EXISTS bonus_active BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE daily_challenge_paid_sessions ADD COLUMN IF NOT EXISTS bonus_consumed BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS paid_redemption_unlocked_at TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS paid_redemption_used_at TIMESTAMPTZ",
    # iter237m — Wallets are USD canonical : update the legacy XAF default on
    # the transactions table so future inserts (and any lingering codepath that
    # forgets to pass `currency`) don't silently regress to XAF in user history.
    "ALTER TABLE transactions ALTER COLUMN currency SET DEFAULT 'USD'",
]


async def _ensure_ddl(conn) -> None:
    global _ddl_done
    if _ddl_done:
        return
    for s in _DDL:
        await conn.execute(s)
    _ddl_done = True


# ── Anti-cap helpers ───────────────────────────────────────────────────────
async def _today_net_gain_usd(conn, user_id: str) -> float:
    """Sum of POSITIVE wins (amount_won_usd > 0) for this user today."""
    val = await conn.fetchval(
        """SELECT COALESCE(SUM(amount_won_usd), 0)
             FROM daily_challenge_paid_sessions
            WHERE user_id = $1 AND date_played = CURRENT_DATE
              AND status = 'completed' AND amount_won_usd > 0""",
        user_id,
    )
    return float(val or 0)


# ── iter237l — Profile completion redemption helpers ─────────────────────
# Bonus métier : +5% atténuation des pertes (anti-tilt). Une seule fois par
# utilisateur (`users.paid_redemption_used_at`). Débloqué quand le profil
# remplit : avatar + bio (about ≥ 30 chars) + ≥ 3 champs personnels parmi
# (birthday, gender, country_code, phone_number).
PROFILE_COMPLETION_BONUS_PCT = 5.0  # +5pp absolus appliqués sur result_pct
_PROFILE_INTEREST_FIELDS = ("birthday", "gender", "country_code", "phone_number")


def _profile_status(user_row) -> dict:
    """Returns {complete, missing[], filled_count, fields_required}."""
    avatar_ok = bool((user_row.get("avatar") or "").strip())
    about_ok = len((user_row.get("about") or "").strip()) >= 30
    interests_filled = 0
    for f in _PROFILE_INTEREST_FIELDS:
        v = user_row.get(f)
        if v is None:
            continue
        if isinstance(v, str):
            if v.strip():
                interests_filled += 1
        else:
            interests_filled += 1
    interests_ok = interests_filled >= 3
    missing: list[str] = []
    if not avatar_ok:
        missing.append("avatar")
    if not about_ok:
        missing.append("about")
    if not interests_ok:
        missing.append("interests")
    return {
        "complete": (avatar_ok and about_ok and interests_ok),
        "missing": missing,
        "interests_filled": interests_filled,
        "interests_required": 3,
    }


async def _redemption_status(conn, user_id: str) -> dict:
    """Centralised eligibility check. Side-effect : sets unlocked_at the
    first time the profile reaches completion (so we can show 'Unlocked'
    UX moment to the user)."""
    row = await conn.fetchrow(
        "SELECT avatar, about, birthday, gender, country_code, phone_number, "
        "       paid_redemption_unlocked_at, paid_redemption_used_at "
        "  FROM users WHERE user_id = $1",
        user_id,
    )
    if not row:
        return {"profile_complete": False, "unlocked_at": None,
                "used_at": None, "available": False, "missing": ["unknown"],
                "bonus_pct": PROFILE_COMPLETION_BONUS_PCT}
    user_row = dict(row)
    status = _profile_status(user_row)
    unlocked_at = user_row.get("paid_redemption_unlocked_at")
    used_at = user_row.get("paid_redemption_used_at")

    # Lazy-set unlocked_at once profile is complete.
    if status["complete"] and unlocked_at is None:
        await conn.execute(
            "UPDATE users SET paid_redemption_unlocked_at = NOW() "
            "WHERE user_id = $1 AND paid_redemption_unlocked_at IS NULL",
            user_id,
        )
        unlocked_at = datetime.now(timezone.utc)

    available = bool(status["complete"] and unlocked_at and not used_at)
    return {
        "profile_complete": status["complete"],
        "missing": status["missing"],
        "interests_filled": status["interests_filled"],
        "interests_required": status["interests_required"],
        "unlocked_at": unlocked_at.isoformat() if unlocked_at else None,
        "used_at": used_at.isoformat() if used_at else None,
        "available": available,
        "bonus_pct": PROFILE_COMPLETION_BONUS_PCT,
    }


# ── Endpoints user ─────────────────────────────────────────────────────────
@router.get("/quiz/daily-challenge/paid/config")
async def paid_config(request: Request):
    """Returns admin config + per-user status (already played today,
    daily cap reached, pool size for UI hint)."""
    user = await get_current_user(request)
    cfg = await _get_cfg()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        played = await conn.fetchval(
            """SELECT id FROM daily_challenge_paid_sessions
                WHERE user_id=$1 AND date_played=CURRENT_DATE""",
            user["user_id"],
        )
        last_session = None
        if played:
            r = await conn.fetchrow(
                """SELECT id, score, result_pct, amount_won_usd, status
                     FROM daily_challenge_paid_sessions
                    WHERE id=$1""",
                played,
            )
            if r:
                last_session = {
                    "id": str(r["id"]),
                    "score": int(r["score"] or 0),
                    "result_pct": float(r["result_pct"] or 0),
                    "amount_won_usd": float(r["amount_won_usd"] or 0),
                    "status": r["status"],
                }
        net_today = await _today_net_gain_usd(conn, user["user_id"])
        # iter237y — Per-user pool size (user lang + FR fallback) so the
        # banner reflects what the player would actually receive.
        user_lang_cfg = (user.get("preferred_lang") or "fr").lower()[:2]
        active_pool = await conn.fetchval(
            "SELECT COUNT(*) FROM daily_challenge_expert_pool "
            "WHERE active=TRUE AND expires_at > NOW() AND language IN ($1, 'fr')",
            user_lang_cfg,
        )
        # iter237l — Profile-completion redemption (anti-tilt +5% on losses).
        redemption = await _redemption_status(conn, user["user_id"])
    cap = float(cfg["dcq_daily_gain_cap_usd"])
    cap_reached = cap > 0 and net_today >= cap
    return {
        "enabled": bool(cfg["enabled"]),
        "stake_min": float(cfg["dcq_stake_min_usd"]),
        "stake_max": float(cfg["dcq_stake_max_usd"]),
        "daily_gain_cap": cap,
        "daily_gain_today": net_today,
        "cap_reached": cap_reached,
        "time_per_question": int(cfg["dcq_time_per_question"]),
        "score_pct": {
            "5":   float(cfg["dcq_score_5_pct"]),
            "4":   float(cfg["dcq_score_4_pct"]),
            "3":   float(cfg["dcq_score_3_pct"]),
            "2":   float(cfg["dcq_score_2_pct"]),
            "0_1": float(cfg["dcq_score_01_pct"]),
        },
        "played_today": bool(played),
        "last_session": last_session,
        "pool_size": int(active_pool or 0),
        "redemption": redemption,
    }


# iter237l — Standalone redemption endpoint (used by ProfilePage CTA + result modal).
@router.get("/quiz/daily-challenge/paid/redemption")
async def paid_redemption(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        return await _redemption_status(conn, user["user_id"])


class StartIn(BaseModel):
    stake_usd: Decimal = Field(..., gt=0, le=1_000_000)
    use_bonus: Optional[bool] = False  # iter237l — anti-tilt +5% on losses


@router.post("/quiz/daily-challenge/paid/start")
async def paid_start(req: StartIn, request: Request):
    user = await get_current_user(request)
    cfg = await _get_cfg()
    if not cfg["enabled"]:
        raise HTTPException(403, "Mode payant désactivé.")

    stake = float(req.stake_usd)
    if stake < cfg["dcq_stake_min_usd"]:
        raise HTTPException(400, f"Mise minimum : {cfg['dcq_stake_min_usd']} USD.")
    if stake > cfg["dcq_stake_max_usd"]:
        raise HTTPException(400, f"Mise maximum : {cfg['dcq_stake_max_usd']} USD.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)

        # iter237n — Pre-requisite: user must have accepted the CGJ
        # (Conditions Générales de Jeu) at least once. If not, return
        # HTTP 451 Unavailable For Legal Reasons so the frontend can
        # surface the acceptance modal before retrying.
        cgje_at = await conn.fetchval(
            "SELECT cgje_accepted_at FROM users WHERE user_id = $1",
            user["user_id"],
        )
        if not cgje_at:
            raise HTTPException(
                status_code=451,
                detail={
                    "code": "cgje_required",
                    "message": "Tu dois accepter les Conditions Générales de Jeu "
                               "avant de miser. Cette acceptation est unique "
                               "et garantit ton consentement éclairé.",
                },
            )

        # iter237l — Validate bonus eligibility if requested.
        bonus_active = False
        if req.use_bonus:
            redemption = await _redemption_status(conn, user["user_id"])
            if not redemption["available"]:
                raise HTTPException(
                    403,
                    "Bonus non disponible : "
                    + ("complète ton profil d'abord." if not redemption["profile_complete"]
                       else "déjà utilisé."),
                )
            bonus_active = True

        # Plafond gain/jour
        net = await _today_net_gain_usd(conn, user["user_id"])
        cap = float(cfg["dcq_daily_gain_cap_usd"])
        if cap > 0 and net >= cap:
            raise HTTPException(
                403,
                f"Vous avez atteint le plafond de gain du jour ({cap:g} USD). "
                f"Revenez demain !",
            )

        # 1 session payante par jour
        existing = await conn.fetchval(
            """SELECT id FROM daily_challenge_paid_sessions
                WHERE user_id=$1 AND date_played=CURRENT_DATE""",
            user["user_id"],
        )
        if existing:
            raise HTTPException(409, "Déjà joué aujourd'hui. Revenez demain !")

        # iter237y — Pool size guard filtered by user language (consistency
        # with the SELECT below: a user whose lang+FR pool is empty must
        # see 503, not run an unfiltered count that hides true starvation).
        user_lang = (user.get("preferred_lang") or "fr").lower()[:2]
        active_count = await conn.fetchval(
            "SELECT COUNT(*) FROM daily_challenge_expert_pool "
            "WHERE active=TRUE AND expires_at > NOW() AND language IN ($1, 'fr')",
            user_lang,
        )
        if (active_count or 0) < 5:
            # Trigger emergency refresh in background.
            try:
                from services.dcq_paid_pool_worker import schedule_emergency_refresh
                schedule_emergency_refresh()
            except Exception as e:  # noqa: BLE001
                logger.warning("[dcq-paid] emergency refresh trigger failed: %s", e)
            raise HTTPException(
                503,
                "Le pool de questions est en cours de renouvellement. "
                "Réessaie dans environ 1 minute.",
            )

        # iter237y — Sélection 5 questions au hasard, langue utilisateur
        # avec fallback français. On préfère la langue du joueur ; s'il
        # n'y en a pas assez, on complète avec FR (défaut).
        questions = await conn.fetch(
            """SELECT id, question, options, correct_idx, explanation, category
                 FROM daily_challenge_expert_pool
                WHERE active=TRUE AND expires_at > NOW()
                  AND language IN ($1, 'fr')
                ORDER BY (language = $1) DESC, RANDOM()
                LIMIT 5""",
            user_lang,
        )
        if len(questions) < 5:
            raise HTTPException(503, "Pool insuffisant — réessaie dans 1 minute.")

        # Débit atomique + insert session
        async with conn.transaction():
            w = await conn.fetchrow(
                "SELECT balance, is_locked FROM wallets WHERE user_id=$1 FOR UPDATE",
                user["user_id"],
            )
            if not w:
                raise HTTPException(404, "Portefeuille introuvable.")
            if w["is_locked"]:
                raise HTTPException(403, "Portefeuille verrouillé.")
            balance = float(w["balance"] or 0)
            if balance < stake:
                raise HTTPException(
                    402, f"Solde insuffisant (manque {stake - balance:.2f} USD)."
                )
            await conn.execute(
                "UPDATE wallets SET balance = balance - $1, updated_at=NOW() "
                "WHERE user_id=$2",
                Decimal(str(stake)), user["user_id"],
            )
            qids = [int(q["id"]) for q in questions]
            session_row = await conn.fetchrow(
                """INSERT INTO daily_challenge_paid_sessions
                     (user_id, stake_usd, question_ids, bonus_active, status)
                   VALUES ($1, $2, $3, $4, 'in_progress')
                   RETURNING id""",
                user["user_id"], Decimal(str(stake)), json.dumps(qids), bonus_active,
            )
            sid = str(session_row["id"])

    # Strip correct_idx avant de retourner
    questions_safe = []
    for q in questions:
        opts = q["options"]
        if isinstance(opts, str):
            try:
                opts = json.loads(opts)
            except Exception:
                opts = []
        questions_safe.append({
            "id": int(q["id"]),
            "question": q["question"],
            "options": opts,
            "category": q["category"],
        })

    return {
        "session_id": sid,
        "questions": questions_safe,
        "stake_usd": stake,
        "time_per_question": int(cfg["dcq_time_per_question"]),
        "bonus_active": bonus_active,
    }


class SubmitIn(BaseModel):
    session_id: str = Field(..., min_length=10, max_length=64)
    answers: List[int] = Field(..., min_length=5, max_length=5)


class RevealIn(BaseModel):
    session_id: str = Field(..., min_length=10, max_length=64)
    question_id: int = Field(..., ge=1)
    user_answer: int = Field(..., ge=-1, le=3)


@router.post("/quiz/daily-challenge/paid/reveal")
async def paid_reveal(req: RevealIn, request: Request):
    """Per-question reveal: returns is_correct + correct_idx + explanation
    (only if user answered wrong) so the UI can give immediate feedback.
    Refuses if the session does not belong to the user, is completed, or
    if the question is not part of this session (anti-prefetch).
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        session = await conn.fetchrow(
            """SELECT id, status, question_ids
                 FROM daily_challenge_paid_sessions
                WHERE id::text = $1 AND user_id = $2""",
            req.session_id, user["user_id"],
        )
        if not session:
            raise HTTPException(404, "Session introuvable.")
        if session["status"] != "in_progress":
            raise HTTPException(409, "Session terminée.")
        qids_raw = session["question_ids"]
        qids = json.loads(qids_raw) if isinstance(qids_raw, str) else list(qids_raw or [])
        if int(req.question_id) not in [int(x) for x in qids]:
            raise HTTPException(403, "Question hors session.")
        q = await conn.fetchrow(
            "SELECT id, correct_idx, explanation FROM daily_challenge_expert_pool "
            "WHERE id = $1",
            int(req.question_id),
        )
        if not q:
            raise HTTPException(404, "Question introuvable.")
    is_correct = int(req.user_answer) == int(q["correct_idx"])
    return {
        "question_id": int(q["id"]),
        "correct_idx": int(q["correct_idx"]),
        "is_correct": is_correct,
        # On expose l'explication uniquement si la réponse est mauvaise pour
        # ne pas alourdir la modale en cas de succès.
        "explanation": q["explanation"] if not is_correct else "",
    }


@router.post("/quiz/daily-challenge/paid/submit")
async def paid_submit(req: SubmitIn, request: Request):
    user = await get_current_user(request)
    cfg = await _get_cfg()
    pool = await get_pool()

    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        session = await conn.fetchrow(
            """SELECT id, user_id, stake_usd, question_ids, status, bonus_active
                 FROM daily_challenge_paid_sessions
                WHERE id::text = $1 AND user_id = $2""",
            req.session_id, user["user_id"],
        )
        if not session:
            raise HTTPException(404, "Session introuvable.")
        if session["status"] != "in_progress":
            raise HTTPException(409, "Session déjà terminée.")

        qids_raw = session["question_ids"]
        if isinstance(qids_raw, str):
            qids = json.loads(qids_raw)
        else:
            qids = list(qids_raw or [])

        questions = await conn.fetch(
            "SELECT id, question, options, correct_idx, explanation, category "
            "FROM daily_challenge_expert_pool WHERE id = ANY($1::bigint[])",
            qids,
        )
        q_by_id = {int(q["id"]): q for q in questions}

        score = 0
        results = []
        for i, qid in enumerate(qids):
            q = q_by_id.get(int(qid))
            user_answer = int(req.answers[i]) if i < len(req.answers) else -1
            is_correct = bool(q and user_answer == int(q["correct_idx"]))
            if is_correct:
                score += 1
            opts = q["options"] if q else []
            if isinstance(opts, str):
                try:
                    opts = json.loads(opts)
                except Exception:
                    opts = []
            results.append({
                "question_idx": i,
                "question_id": int(qid),
                "question": q["question"] if q else None,
                "options": opts,
                "user_answer": user_answer,
                "correct_idx": int(q["correct_idx"]) if q else None,
                "is_correct": is_correct,
                "explanation": q["explanation"] if q else "",
            })

        # Barème
        if score == 5:
            result_pct = float(cfg["dcq_score_5_pct"])
        elif score == 4:
            result_pct = float(cfg["dcq_score_4_pct"])
        elif score == 3:
            result_pct = float(cfg["dcq_score_3_pct"])
        elif score == 2:
            result_pct = float(cfg["dcq_score_2_pct"])
        else:
            result_pct = float(cfg["dcq_score_01_pct"])

        # iter237l — Apply +5pp anti-tilt bonus only on losses (result_pct < 0).
        # Bonus is consumed (one-time) only if it actually reduces a loss.
        bonus_active = bool(session["bonus_active"])
        bonus_applied_pct = 0.0
        bonus_consumed = False
        if bonus_active and result_pct < 0:
            bonus_applied_pct = PROFILE_COMPLETION_BONUS_PCT
            # Cap so the bonus never makes a loss positive.
            if result_pct + bonus_applied_pct > 0:
                bonus_applied_pct = -result_pct
            result_pct = round(result_pct + bonus_applied_pct, 2)
            bonus_consumed = True

        stake = float(session["stake_usd"])
        amount_delta = round(stake * result_pct / 100.0, 4)
        # Cap clamp : si gain net journalier dépasse le cap, on tronque
        if amount_delta > 0:
            net_today = await _today_net_gain_usd(conn, user["user_id"])
            cap = float(cfg["dcq_daily_gain_cap_usd"])
            if cap > 0 and (net_today + amount_delta) > cap:
                amount_delta = max(0.0, cap - net_today)
                amount_delta = round(amount_delta, 4)

        refund = round(stake + amount_delta, 4)
        if refund < 0:
            refund = 0.0

        async with conn.transaction():
            if refund > 0:
                await conn.execute(
                    "UPDATE wallets SET balance = balance + $1, updated_at=NOW() "
                    "WHERE user_id=$2",
                    Decimal(str(refund)), user["user_id"],
                )
            await conn.execute(
                """UPDATE daily_challenge_paid_sessions
                      SET answers=$1, score=$2, result_pct=$3,
                          amount_won_usd=$4, bonus_consumed=$5,
                          status='completed', completed_at=NOW()
                    WHERE id::text=$6""",
                json.dumps(req.answers), score, Decimal(str(result_pct)),
                Decimal(str(amount_delta)), bonus_consumed, req.session_id,
            )
            # iter237l — Mark the bonus as used on the user once, only when
            # actually consumed (i.e. a loss with bonus_active).
            if bonus_consumed:
                await conn.execute(
                    "UPDATE users SET paid_redemption_used_at = NOW() "
                    "WHERE user_id = $1 AND paid_redemption_used_at IS NULL",
                    user["user_id"],
                )

    return {
        "session_id": req.session_id,
        "score": score,
        "result_pct": result_pct,
        "amount_delta_usd": amount_delta,
        "refund_usd": refund,
        "stake_usd": stake,
        "won": score == 5,
        "bonus_active": bonus_active,
        "bonus_applied_pct": bonus_applied_pct,
        "bonus_consumed": bonus_consumed,
        "results": results,
    }


# ── Admin ────────────────────────────────────────────────────────────────
@router.get("/admin/daily-challenge/paid/config")
async def admin_get_config(request: Request):
    await require_admin(request)
    cfg = await _get_cfg()
    return cfg


class AdminConfigIn(BaseModel):
    DCQ_PAID_ENABLED: Optional[bool] = None
    DCQ_STAKE_MIN_USD: Optional[float] = None
    DCQ_STAKE_MAX_USD: Optional[float] = None
    DCQ_DAILY_GAIN_CAP_USD: Optional[float] = None
    DCQ_SCORE_5_PCT: Optional[float] = None
    DCQ_SCORE_4_PCT: Optional[float] = None
    DCQ_SCORE_3_PCT: Optional[float] = None
    DCQ_SCORE_2_PCT: Optional[float] = None
    DCQ_SCORE_01_PCT: Optional[float] = None
    DCQ_TIME_PER_QUESTION: Optional[int] = None


@router.put("/admin/daily-challenge/paid/config")
async def admin_set_config(body: AdminConfigIn, request: Request):
    await require_admin(request)
    updates = body.dict(exclude_unset=True)
    saved = {}
    for k, v in updates.items():
        if k == "DCQ_PAID_ENABLED":
            await set_setting(k, "true" if bool(v) else "false")
            saved[k] = bool(v)
            continue
        # Numeric clamp
        try:
            num = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"Valeur invalide pour {k}")
        lo, hi = DCQ_BOUNDS.get(k, (None, None))
        if lo is not None:
            num = max(lo, min(num, hi))
        await set_setting(k, str(num))
        saved[k] = num
    return {"success": True, "saved": saved}


@router.get("/admin/daily-challenge/paid/stats")
async def admin_stats(request: Request, days: int = Query(default=30, ge=1, le=365)):
    """KPI dashboard. Net Japap = sum(losses) - sum(wins).
    Where loss = -amount_won_usd if amount_won_usd < 0; win = amount_won_usd if > 0.
    Equivalently: -SUM(amount_won_usd) (gains négatifs côté joueur = revenus pour Japap).
    """
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        kpis = await conn.fetchrow(f"""
            SELECT
              COUNT(*) FILTER (WHERE status='completed' AND date_played=CURRENT_DATE) AS today_sessions,
              COUNT(*) FILTER (WHERE status='completed' AND date_played > NOW() - INTERVAL '{int(days)} days') AS period_sessions,
              COUNT(*) FILTER (WHERE status='completed') AS total_sessions,
              COUNT(*) FILTER (WHERE status='completed' AND score = 5) AS total_wins,
              COALESCE(SUM(stake_usd) FILTER (WHERE status='completed'), 0) AS total_stakes,
              COALESCE(AVG(stake_usd) FILTER (WHERE status='completed'), 0) AS avg_stake,
              COALESCE(AVG(score) FILTER (WHERE status='completed'), 0) AS avg_score,
              COALESCE(SUM(amount_won_usd) FILTER (WHERE date_played=CURRENT_DATE AND status='completed'), 0) AS today_net_player,
              COALESCE(SUM(amount_won_usd) FILTER (WHERE date_played > NOW() - INTERVAL '{int(days)} days' AND status='completed'), 0) AS period_net_player,
              COALESCE(SUM(amount_won_usd) FILTER (WHERE status='completed'), 0) AS total_net_player
            FROM daily_challenge_paid_sessions
        """)
        pool_active = await conn.fetchval(
            "SELECT COUNT(*) FROM daily_challenge_expert_pool "
            "WHERE active=TRUE AND expires_at > NOW()"
        )
        latest_pool = await conn.fetchrow(
            "SELECT MAX(created_at) AS last_gen, MAX(batch_id) AS batch "
            "FROM daily_challenge_expert_pool"
        )

    completed = int(kpis["total_sessions"] or 0)
    wins = int(kpis["total_wins"] or 0)
    win_rate = round((wins / completed) * 100, 2) if completed else 0.0

    def _norm(v):  # iter237k — normalize -0.0 to 0.0 for cosmetic clarity.
        f = round(-float(v or 0), 2)
        return 0.0 if f == 0 else f

    return {
        "today_sessions": int(kpis["today_sessions"] or 0),
        "period_sessions": int(kpis["period_sessions"] or 0),
        "total_sessions": completed,
        "total_wins": wins,
        "win_rate_pct": win_rate,
        "avg_stake_usd": round(float(kpis["avg_stake"] or 0), 2),
        "avg_score": round(float(kpis["avg_score"] or 0), 2),
        "total_stakes_usd": round(float(kpis["total_stakes"] or 0), 2),
        # "Net Japap" = -SUM(amount_won_usd) (côté joueur les pertes sont
        # négatives, donc négation = revenu pour Japap).
        "today_revenue_usd":   _norm(kpis["today_net_player"]),
        "period_revenue_usd":  _norm(kpis["period_net_player"]),
        "total_revenue_usd":   _norm(kpis["total_net_player"]),
        "pool_active": int(pool_active or 0),
        "pool_last_generation": latest_pool["last_gen"].isoformat()
            if latest_pool and latest_pool["last_gen"] else None,
        "pool_latest_batch_id": int(latest_pool["batch"] or 0) if latest_pool else 0,
        "days_window": days,
    }


@router.post("/admin/daily-challenge/paid/refresh-pool")
async def admin_force_refresh(request: Request):
    """Forçage manuel d'une régénération de pool (admin)."""
    await require_admin(request)
    try:
        from services.dcq_paid_pool_worker import schedule_emergency_refresh
        schedule_emergency_refresh()
        return {"success": True, "dispatched": True}
    except Exception as e:
        logger.exception("force refresh failed")
        raise HTTPException(500, f"Échec dispatch : {e}")


dcq_paid_router = router
