"""
Quiz Champion par Pays — REST API (iter123, Phase 3.A — FREE mode only).
========================================================================

Endpoints (all under /api/quiz/champion):

  Public:
    GET  /api/quiz/champion/{country_code}   → champion details (or 404)
    GET  /api/quiz/champion                  → my country's champion (auth)

  Player (auth):
    POST /api/quiz/champion/challenge
         body {country_code, mode='free'}    → create challenge (challenger)
    POST /api/quiz/champion/challenge/{id}/accept   → champion only
    POST /api/quiz/champion/challenge/{id}/refuse   → champion only
    POST /api/quiz/champion/challenge/{id}/play     → either player plays segment
                                                       (returns the shared 5Q session)
    POST /api/quiz/champion/challenge/{id}/submit
         body {answers:[5]}                   → submit segment (resolves winner if both done)
    GET  /api/quiz/champion/challenges/me     → list my pending+history (sent+received)
    GET  /api/quiz/champion/challenges/{id}   → detail + can_play flags

  Admin (admin/superadmin):
    POST /api/quiz/champion/admin/promote-all → re-run the auto top-1 promotion
    POST /api/quiz/champion/admin/{country}/set      body {user_id} → manual promote
    POST /api/quiz/champion/admin/{country}/demote   body {reason}  → manual demote
    GET  /api/quiz/champion/admin/list?include_demoted=… → all current champions
    GET  /api/quiz/champion/admin/challenges?status=…&limit=&offset= → filtered list

Strict rules:
  - Challenger != champion (no self-challenge).
  - Country must HAVE a champion to be challenged.
  - At most ONE open challenge per (challenger, champion) pair (DB unique idx).
  - Challenger must NOT be the current champion (different rule than self-challenge).
  - Only the champion can accept/refuse.
  - Each side plays exactly ONCE; second submit → 409.
  - Server-authoritative scoring (re-uses /api/quiz internal scoring).
"""
from __future__ import annotations
import json as _json
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field

from database import get_pool
from routes.auth import get_current_user
from services.quiz_champion import (
    ensure_ddl, get_country_champion, _new_challenge_id,
    record_refusal_and_maybe_demote,
    promote_champions, admin_set_champion, admin_demote_champion,
    DEFAULT_CHALLENGE_EXPIRY_HOURS, DEFAULT_WINDOW_DAYS,
    STATUS_PENDING, STATUS_ACCEPTED, STATUS_REFUSED, STATUS_EXPIRED,
    STATUS_CHALLENGER_PLAYED, STATUS_CHAMPION_PLAYED, STATUS_COMPLETED,
    STATUS_CANCELLED, STATUS_AWAITING_ACCEPTOR, OPEN_STATUSES,
)
from routes.quiz import _parse_options, SESSION_SIZE, SESSION_TIME_NETWORK_GRACE_SECONDS
from services.points_service import add_points
from services.games_settings import get_quiz_config
from services.quiz_champion_escrow import (
    lock_stake, lock_stake_double, release_to_winner, refund_player, log_bonus,
)
from decimal import Decimal

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/quiz/champion", tags=["quiz_champion"])


# Minimal notification helper (mirrors the inline pattern used in
# routes/transport.py — best-effort, never blocks the request).
async def _notify(user_id: str, kind: str, title: str, body: str,
                  data: dict | None = None,
                  email_html: str | None = None,
                  email_subject: str | None = None):
    """Persist a notification row + fire-and-forget OneSignal push + Resend
    email. iter126/127 — Phase 3.C: fan-out beyond the DB row so the
    recipient actually gets nudged on their device or inbox.

    iter127 perf hardening — push and email are dispatched via
    asyncio.create_task() so the calling request handler does NOT wait
    for the third-party HTTP round-trip. The `notifications` DB row is
    still written synchronously (it's the source-of-truth for the
    in-app inbox).
    """
    import asyncio
    import uuid
    safe_data = data or {}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO notifications (notif_id, user_id, type, title, message, data)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                f"notif_{uuid.uuid4().hex[:12]}",
                user_id, kind, title, body,
                _json.dumps(safe_data),
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("notify (%s) DB row skipped: %s", kind, e)

    # Fire-and-forget push + email (do NOT block the caller).
    asyncio.create_task(_fanout_push_email(user_id, kind, title, body, safe_data,
                                            email_html, email_subject))


async def _fanout_push_email(user_id: str, kind: str, title: str, body: str,
                              safe_data: dict,
                              email_html: str | None,
                              email_subject: str | None) -> None:
    """Background-task entry point: do the OneSignal push + Resend email
    in parallel without blocking the request that triggered the notify.
    Failures are logged at DEBUG so they never spam INFO."""
    # OneSignal push — open the relevant in-app route on click.
    try:
        from services.push_service import send_push_to_user
        cid = safe_data.get("challenge_id")
        url = "/games/quiz/challenges" + (f"/{cid}" if cid else "")
        await send_push_to_user(user_id, {
            "title": title, "body": body, "url": url,
            "data": {"kind": kind, **safe_data},
            "tag": kind,
        })
    except Exception as e:  # noqa: BLE001
        logger.debug("notify (%s) push skipped: %s", kind, e)

    # Resend email — only for "important" champion events (avoid spam).
    EMAIL_KINDS = {
        "quiz_champion_challenge",   # received a challenge
        "quiz_champion_completed",   # win/lose/tie
    }
    if kind in EMAIL_KINDS:
        try:
            from services.email_service import send_email
            pool2 = await get_pool()
            async with pool2.acquire() as conn:
                u = await conn.fetchrow(
                    "SELECT email, first_name FROM users WHERE user_id = $1", user_id,
                )
            if u and (u["email"] or "").strip():
                subject = email_subject or f"JAPAP — {title}"
                first_name = (u["first_name"] or "").strip() or "Champion"
                cid = safe_data.get("challenge_id", "")
                # iter168 — Use centralised public_url() helper. Was hardcoded
                # to https://japap.app/... which damaged user trust + looked
                # like phishing because the brand domain is japapmessenger.com.
                from utils.public_url import public_url
                cta = (public_url(f"/games/quiz/challenges/{cid}") if cid
                       else public_url("/games/quiz"))
                html = email_html or f"""
                <div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:auto;padding:24px;background:#0F056B;color:#fff;border-radius:14px">
                  <h2 style="margin:0 0 8px">{title}</h2>
                  <p style="opacity:0.85;line-height:1.5;font-size:15px">Salut {first_name},<br>{body}</p>
                  <p style="margin:24px 0 8px"><a href="{cta}" style="display:inline-block;padding:12px 22px;background:#FFD700;color:#111;border-radius:999px;font-weight:700;text-decoration:none">Voir le défi →</a></p>
                  <p style="opacity:0.5;font-size:11px;margin-top:24px">Vous recevez cet email parce que vous participez à la compétition Quiz Champion JAPAP.</p>
                </div>
                """
                await send_email(u["email"], subject, html, body)
        except Exception as e:  # noqa: BLE001
            logger.debug("notify (%s) email skipped: %s", kind, e)


# ─────────────────────────────────────────────────────────────────────
# Public read
# ─────────────────────────────────────────────────────────────────────

@router.get("/{country_code}")
async def get_champion(country_code: str):
    # iter124 — Validate raw input BEFORE truncating, otherwise 'XYZ' would
    # silently become 'XY' and 404 instead of 400.
    if len(country_code) != 2 or not country_code.isalpha():
        raise HTTPException(status_code=400, detail="country_code invalide (ISO 2 lettres)")
    cc = country_code.upper()
    champ = await get_country_champion(cc)
    if not champ:
        raise HTTPException(status_code=404, detail="Aucun champion pour ce pays.")
    return champ


@router.get("/leaderboard/doublers")
async def leaderboard_doublers(limit: int = Query(default=10, ge=1, le=50),
                                window_days: int = Query(default=30, ge=1, le=365)):
    """iter233 — Mission 2: Top players who have won ×4 pots via the
    "Doubler la mise" mechanic. Public endpoint — drives aspirational
    engagement (Doubleurs Légendaires).

    Source-of-truth = quiz_challenge_release ledger rows on completed
    challenges where `doubled = TRUE`. We aggregate USD-canonical net
    payouts per winner over the rolling window.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        rows = await conn.fetch(
            """SELECT
                  t.to_user_id              AS user_id,
                  COUNT(DISTINCT c.challenge_id) AS wins_doubled,
                  COALESCE(SUM(t.amount), 0)     AS total_won
                FROM transactions t
                JOIN quiz_champion_challenges c
                  ON c.challenge_id = t.reference
                 AND c.status = 'completed'
                 AND c.doubled = TRUE
                 AND c.winner_user_id = t.to_user_id
               WHERE t.type = 'quiz_challenge_release'
                 AND t.created_at > NOW() - ($1 || ' days')::interval
            GROUP BY t.to_user_id
            ORDER BY total_won DESC, wins_doubled DESC
               LIMIT $2""",
            str(window_days), int(limit),
        )
        if not rows:
            return {"window_days": window_days, "leaders": []}
        users = await conn.fetch(
            """SELECT user_id, username, first_name, last_name, avatar
                 FROM users WHERE user_id = ANY($1::text[])""",
            [r["user_id"] for r in rows],
        )
    by_id = {u["user_id"]: u for u in users}
    leaders = []
    for r in rows:
        u = by_id.get(r["user_id"])
        if not u:
            continue
        name = (u["first_name"] or u["username"] or "Joueur").strip()
        if u["last_name"]:
            name = f"{name} {u['last_name']}".strip()
        leaders.append({
            "user_id":      r["user_id"],
            "name":         name,
            "username":     u["username"],
            "avatar_url":   u["avatar"],
            "wins_doubled": int(r["wins_doubled"]),
            "total_won_usd": round(float(r["total_won"] or 0), 2),
        })
    return {"window_days": window_days, "leaders": leaders}


@router.get("/leaderboard/challengers")
async def leaderboard_challengers(country_code: str = Query(default="", max_length=2),
                                   window_days: int = Query(default=7, ge=1, le=90),
                                   limit: int = Query(default=3, ge=1, le=20)):
    """iter126 — "Challengers de la semaine" leaderboard. Returns the top N
    players by NUMBER OF DEFI WINS (free + paid separately) over the rolling
    window. Public endpoint — drives social/competitive engagement.

    Optional `country_code` filter (defaults to global). Includes user public
    info (avatar, name, username).
    """
    pool = await get_pool()
    cc = country_code.upper()[:2] if country_code else None
    where_country = ""
    params: list = [str(window_days)]
    if cc and len(cc) == 2 and cc.isalpha():
        params.append(cc)
        where_country = f"AND country_code = ${len(params)}"
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        rows = await conn.fetch(
            f"""SELECT winner_user_id AS user_id, mode, COUNT(*) AS wins
                  FROM quiz_champion_challenges
                 WHERE status = 'completed'
                   AND winner_user_id IS NOT NULL
                   AND completed_at > NOW() - ($1 || ' days')::interval
                   {where_country}
              GROUP BY winner_user_id, mode""",
            *params,
        )
        # iter128 — Source-of-truth earnings derived from ledger rows
        # (`quiz_challenge_release`) instead of an approximation. This stays
        # accurate even if the admin changes commission_pct mid-period.
        # iter129 — Use `transactions.reference` (challenge_id) for the
        # JOIN to drop the brittle escrow_payout_tx_id round-trip.
        earnings_rows = await conn.fetch(
            f"""SELECT t.to_user_id AS user_id, COALESCE(SUM(t.amount), 0) AS earned
                  FROM transactions t
                  JOIN quiz_champion_challenges c
                    ON c.challenge_id = t.reference
                 WHERE t.type = 'quiz_challenge_release'
                   AND c.completed_at > NOW() - ($1 || ' days')::interval
                   {where_country.replace('country_code', 'c.country_code')}
              GROUP BY t.to_user_id""",
            *params,
        )
        earnings_by_user = {r["user_id"]: float(r["earned"] or 0) for r in earnings_rows}
        # Aggregate per user_id × mode → split arrays
        by_user: dict[str, dict] = {}
        for r in rows:
            uid = r["user_id"]
            d = by_user.setdefault(uid, {"user_id": uid, "wins_free": 0, "wins_paid": 0, "earnings": 0.0})
            if r["mode"] == "free":
                d["wins_free"] = int(r["wins"])
            else:
                d["wins_paid"] = int(r["wins"])
                d["earnings"] = earnings_by_user.get(uid, 0.0)
        # Hydrate user public info
        if not by_user:
            return {"window_days": window_days, "country_code": cc,
                    "free": [], "paid": []}
        u_rows = await conn.fetch(
            """SELECT user_id, first_name, last_name, username, avatar, is_pro,
                      country_code AS user_country
                 FROM users WHERE user_id = ANY($1::text[])""",
            list(by_user.keys()),
        )
        users_by_id = {u["user_id"]: u for u in u_rows}
        items = []
        for d in by_user.values():
            u = users_by_id.get(d["user_id"])
            if not u:
                continue
            d["user"] = {
                "user_id": d["user_id"],
                "first_name": u["first_name"] or "",
                "last_name": u["last_name"] or "",
                "username": u["username"] or "",
                "avatar": u["avatar"] or "",
                "is_pro": bool(u["is_pro"]),
                "country_code": u["user_country"] or "",
            }
            items.append(d)
        free_sorted = sorted(items, key=lambda x: (-x["wins_free"], -x["wins_paid"]))[:limit]
        paid_sorted = sorted(items, key=lambda x: (-x["wins_paid"], -x["earnings"]))[:limit]
        # Drop entries with 0 wins in their respective list
        free_sorted = [x for x in free_sorted if x["wins_free"] > 0]
        paid_sorted = [x for x in paid_sorted if x["wins_paid"] > 0]
    return {"window_days": window_days, "country_code": cc,
            "free": free_sorted, "paid": paid_sorted}


# ─────────────────────────────────────────────────────────────────────
# Challenge: create
# ─────────────────────────────────────────────────────────────────────

class CreateChallengeRequest(BaseModel):
    country_code: str = Field(..., min_length=2, max_length=2)
    mode: str = Field(default="free")  # free | paid (paid added in 3.B)
    stake_amount: float = 0.0          # ignored in free mode
    notes: Optional[str] = Field(default=None, max_length=200)


async def _lazy_expire(conn, ch) -> Optional[dict]:
    """iter125 — If a challenge is past its expires_at and still open, mark it
    expired, refund stakes, and award the challenger bonus. Idempotent.
    Caller MUST hold the row lock on `ch`.
    iter232 — Mission 2 (Doubler la mise): also covers `awaiting_acceptor`
    (open challenges that nobody claimed) and refunds A's pre-locked
    `allow_double` slice if applicable."""
    if ch["status"] not in (STATUS_PENDING, STATUS_AWAITING_ACCEPTOR,
                            STATUS_ACCEPTED, STATUS_CHALLENGER_PLAYED,
                            STATUS_CHAMPION_PLAYED):
        return None
    if not ch["expires_at"] or ch["expires_at"] >= datetime.now(timezone.utc):
        return None
    cfg = await get_quiz_config()
    refund_on_expiry = bool(cfg.get("quiz_challenge_refund_on_expiry", True))
    bonus_pts = int(cfg.get("quiz_challenge_challenger_bonus_points") or 0)
    info: dict = {"refund": None, "bonus": None}

    await conn.execute(
        "UPDATE quiz_champion_challenges SET status='expired' WHERE challenge_id = $1",
        ch["challenge_id"],
    )
    if ch["mode"] == "paid" and ch["escrow_locked"] and refund_on_expiry:
        stake = Decimal(str(ch["stake_amount"] or 0))
        currency = str(ch["stake_currency"])
        # iter232 — A's locked amount = 2× stake if allow_double was set.
        a_locked = stake * Decimal("2") if bool(ch.get("allow_double")) else stake
        b_locked = stake * Decimal("2") if bool(ch.get("doubled")) else stake
        # Refund A.
        t1 = await refund_player(
            conn, user_id=ch["challenger_user_id"], amount=a_locked,
            currency=currency, challenge_id=ch["challenge_id"],
            reason="expiry",
        )
        info["refund"] = {"challenger_tx": t1, "amount": str(a_locked)}
        # Refund B if any.
        if ch["status"] in (STATUS_ACCEPTED, STATUS_CHALLENGER_PLAYED, STATUS_CHAMPION_PLAYED) \
           and ch["champion_user_id"]:
            t2 = await refund_player(
                conn, user_id=ch["champion_user_id"], amount=b_locked,
                currency=currency, challenge_id=ch["challenge_id"],
                reason="expiry",
            )
            info["refund"]["champion_tx"] = t2
    if bonus_pts > 0:
        await log_bonus(
            conn, user_id=ch["challenger_user_id"], amount_pts=bonus_pts,
            challenge_id=ch["challenge_id"], reason="expiry",
        )
        try:
            await add_points(
                conn, ch["challenger_user_id"], bonus_pts,
                source="champion_expired",
                metadata={"challenge_id": ch["challenge_id"]},
            )
        except Exception:
            pass
        info["bonus"] = {"points": bonus_pts}
    return info


@router.post("/challenge")
async def create_challenge(req: CreateChallengeRequest, request: Request):
    user = await get_current_user(request)
    if req.mode not in ("free", "paid"):
        raise HTTPException(status_code=400, detail="mode doit être 'free' ou 'paid'.")

    cc = req.country_code
    if len(cc) != 2 or not cc.isalpha():
        raise HTTPException(status_code=400, detail="country_code invalide (ISO 2 lettres)")
    cc = cc.upper()

    champ = await get_country_champion(cc)
    if not champ:
        raise HTTPException(status_code=404, detail="Aucun champion pour ce pays.")
    if champ["user_id"] == user["user_id"]:
        raise HTTPException(
            status_code=400,
            detail="Vous êtes le champion de ce pays — vous ne pouvez pas vous défier vous-même.",
        )

    # iter125 — Read challenge config (admin-tunable).
    cfg = await get_quiz_config()
    expiry_hours = int(cfg.get("quiz_challenge_expiry_hours") or DEFAULT_CHALLENGE_EXPIRY_HOURS)
    commission_pct = Decimal(str(cfg.get("quiz_challenge_commission_pct") or 10))
    stake_min = Decimal(str(cfg.get("quiz_challenge_stake_min") or 1))
    stake_max = Decimal(str(cfg.get("quiz_challenge_stake_max") or 10000))

    # ── PAID MODE preconditions ────────────────────────────────────────
    paid_mode = (req.mode == "paid")
    stake_amount: Decimal = Decimal("0")
    stake_currency = "USD"
    if paid_mode:
        if not bool(cfg.get("quiz_challenge_paid_enabled", False)):
            raise HTTPException(
                status_code=503,
                detail="Mode payant désactivé par l'administrateur.",
            )
        try:
            stake_amount = Decimal(str(req.stake_amount or 0)).quantize(Decimal("0.01"))
        except Exception:
            raise HTTPException(status_code=400, detail="Montant de mise invalide.")
        if stake_amount < stake_min:
            raise HTTPException(status_code=400,
                detail=f"Mise minimale : {stake_min} USD.")
        if stake_amount > stake_max:
            raise HTTPException(status_code=400,
                detail=f"Mise maximale : {stake_max} USD.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_ddl(conn)
            # Anti-duplicate: reject if an open challenge exists between this pair.
            existing = await conn.fetchrow(
                """SELECT challenge_id FROM quiz_champion_challenges
                    WHERE challenger_user_id = $1 AND champion_user_id = $2
                      AND status IN ('pending','accepted','challenger_played','champion_played')""",
                user["user_id"], champ["user_id"],
            )
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail="Un défi ouvert existe déjà avec ce champion.",
                )
            # Pick a shared session — iter130: build per-pair using the
            # anti-repetition picker so neither player sees a question they
            # already saw recently. Picker excludes both users separately
            # by combining their seen sets.
            from services.quiz_question_picker import (
                pick_question_ids_for_user, SESSION_SIZE as _PICK_SIZE,
            )
            # Combine both users' seen sets — easiest way: insert a
            # synthetic "user_id" into history that is both, OR more
            # simply: query both, take union, and feed into the picker
            # via raw helper. The picker exposes `pick_question_ids_for_user`
            # — we run it for the challenger first (50/20/15/15 still
            # respected), then verify none are in the champion's seen set
            # via a second filter. If overlap → swap from the bucket.
            qids_pre, fb1 = await pick_question_ids_for_user(
                conn, user["user_id"], size=_PICK_SIZE,
            )
            if len(qids_pre) < _PICK_SIZE:
                raise HTTPException(status_code=503,
                                    detail="Banque de questions épuisée.")
            # Filter against champion's recent history, swap any clash.
            champ_seen = await conn.fetch(
                """SELECT question_id FROM user_quiz_question_history
                    WHERE user_id = $1
                      AND seen_at > NOW() - INTERVAL '7 days'
                      AND question_id = ANY($2::bigint[])""",
                champ["user_id"], qids_pre,
            )
            champ_seen_ids = {int(r["question_id"]) for r in champ_seen}
            if champ_seen_ids:
                # Swap each clashing qid for a fresh one.
                replacement = await conn.fetch(
                    """SELECT id FROM quiz_questions
                        WHERE active=TRUE AND obsolete=FALSE
                          AND id <> ALL($1::bigint[])
                     ORDER BY random() LIMIT $2""",
                    list(champ_seen_ids) + qids_pre, len(champ_seen_ids),
                )
                rep_ids = [int(r["id"]) for r in replacement]
                qids_final = [
                    (rep_ids.pop(0) if (q in champ_seen_ids and rep_ids) else q)
                    for q in qids_pre
                ]
            else:
                qids_final = qids_pre
            cat_rows = await conn.fetch(
                "SELECT id, category FROM quiz_questions WHERE id = ANY($1::bigint[])",
                qids_final,
            )
            cats_by_id = {int(r["id"]): r["category"] for r in cat_rows}
            session_id = await conn.fetchval(
                """INSERT INTO quiz_sessions (question_ids, categories)
                   VALUES ($1::bigint[], $2::text[]) RETURNING id""",
                qids_final,
                [cats_by_id.get(q, "unknown") for q in qids_final],
            )
            # Mark BOTH users' history so future picks honour anti-repetition.
            await conn.executemany(
                """INSERT INTO user_quiz_question_history (user_id, question_id, source, seen_at)
                   VALUES ($1, $2, 'champion_challenge', NOW())""",
                [(user["user_id"], q) for q in qids_final]
                + [(champ["user_id"], q) for q in qids_final],
            )
            session = {"id": session_id}
            if not session:
                raise HTTPException(
                    status_code=503,
                    detail="Aucune session quiz disponible. L'admin doit lancer la génération IA.",
                )

            # iter125 — PAID: lock challenger's stake atomically. Reads the
            # challenger's wallet currency and uses it as stake_currency.
            escrow_locked = False
            if paid_mode:
                w = await conn.fetchrow(
                    "SELECT currency FROM wallets WHERE user_id = $1",
                    user["user_id"],
                )
                if not w:
                    raise HTTPException(status_code=404, detail="Portefeuille introuvable.")
                stake_currency = (w["currency"] or "USD").upper()
                # The lock_stake helper acquires the wallet row lock + debits
                # + inserts the audit row.
                cid_preview = _new_challenge_id()  # we'll use this ID below
                await lock_stake(
                    conn, user_id=user["user_id"], amount=stake_amount,
                    currency=stake_currency, challenge_id=cid_preview,
                )
                escrow_locked = True
                cid = cid_preview
            else:
                cid = _new_challenge_id()

            expires_at = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
            await conn.execute(
                """INSERT INTO quiz_champion_challenges
                     (challenge_id, challenger_user_id, champion_user_id,
                      country_code, session_id, mode, stake_amount, stake_currency,
                      commission_pct, status, escrow_locked,
                      created_at, expires_at, notes)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                           'pending', $10, NOW(), $11, $12)""",
                cid, user["user_id"], champ["user_id"], cc,
                int(session["id"]), req.mode, stake_amount, stake_currency,
                commission_pct, escrow_locked,
                expires_at, (req.notes or "")[:200],
            )
    # Best-effort notification (P3.C will plug the real Resend + OneSignal hooks).
    await _notify(
        user_id=champ["user_id"],
        kind="quiz_champion_challenge",
        title="Nouveau défi Quiz",
        body=(f"Un joueur vous défie pour le titre de Champion {cc}"
              + (f" — mise {stake_amount} {stake_currency}." if paid_mode else ".")),
        data={"challenge_id": cid, "country_code": cc, "mode": req.mode,
              "stake_amount": str(stake_amount), "stake_currency": stake_currency},
    )
    return {"challenge_id": cid, "status": STATUS_PENDING,
            "country_code": cc, "mode": req.mode,
            "stake_amount": float(stake_amount),
            "stake_currency": stake_currency,
            "commission_pct": float(commission_pct),
            "escrow_locked": escrow_locked,
            "expires_at": expires_at.isoformat()}


# ─────────────────────────────────────────────────────────────────────
# Champion accept / refuse
# ─────────────────────────────────────────────────────────────────────

@router.post("/challenge/{cid}/accept")
async def accept_challenge(cid: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_ddl(conn)
            ch = await conn.fetchrow(
                "SELECT * FROM quiz_champion_challenges WHERE challenge_id = $1 FOR UPDATE",
                cid,
            )
            if not ch:
                raise HTTPException(status_code=404, detail="Défi introuvable.")
            if ch["champion_user_id"] != user["user_id"]:
                raise HTTPException(status_code=403, detail="Réservé au champion.")
            if ch["status"] != STATUS_PENDING:
                raise HTTPException(status_code=409,
                                    detail=f"Statut actuel : {ch['status']}")
            if ch["expires_at"] and ch["expires_at"] < datetime.now(timezone.utc):
                # Auto-expire + refund challenger if paid (handled by lazy expiry path).
                await _lazy_expire(conn, ch)
                raise HTTPException(status_code=410, detail="Défi expiré.")

            # iter125 — PAID: lock champion's stake before flipping to accepted.
            if ch["mode"] == "paid":
                stake = Decimal(str(ch["stake_amount"] or 0))
                if stake > 0:
                    await lock_stake(
                        conn, user_id=user["user_id"], amount=stake,
                        currency=str(ch["stake_currency"]), challenge_id=cid,
                    )
            await conn.execute(
                """UPDATE quiz_champion_challenges
                      SET status = 'accepted', accepted_at = NOW()
                    WHERE challenge_id = $1""",
                cid,
            )
    try:
        await _notify(
            user_id=ch["challenger_user_id"],
            kind="quiz_champion_accepted",
            title="Défi accepté",
            body="Le champion a accepté votre défi. À vous de jouer !",
            data={"challenge_id": cid},
        )
    except Exception:
        pass
    return {"challenge_id": cid, "status": STATUS_ACCEPTED}


@router.post("/challenge/{cid}/refuse")
async def refuse_challenge(cid: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    refund_info = None
    bonus_info = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_ddl(conn)
            ch = await conn.fetchrow(
                "SELECT * FROM quiz_champion_challenges WHERE challenge_id = $1 FOR UPDATE",
                cid,
            )
            if not ch:
                raise HTTPException(status_code=404, detail="Défi introuvable.")
            if ch["champion_user_id"] != user["user_id"]:
                raise HTTPException(status_code=403, detail="Réservé au champion.")
            if ch["status"] != STATUS_PENDING:
                raise HTTPException(status_code=409,
                                    detail=f"Statut actuel : {ch['status']}")
            await conn.execute(
                """UPDATE quiz_champion_challenges
                      SET status = 'refused', refused_at = NOW()
                    WHERE challenge_id = $1""",
                cid,
            )
            # Record refusal + maybe demote
            outcome = await record_refusal_and_maybe_demote(
                conn,
                champion_user_id=ch["champion_user_id"],
                country_code=ch["country_code"],
                challenge_id=cid,
            )
            # iter125 — PAID: refund the challenger's stake atomically, AND
            # award the engagement bonus (challenger's prime de consolation).
            if ch["mode"] == "paid" and ch["escrow_locked"]:
                stake = Decimal(str(ch["stake_amount"] or 0))
                tx_id = await refund_player(
                    conn, user_id=ch["challenger_user_id"], amount=stake,
                    currency=str(ch["stake_currency"]),
                    challenge_id=cid, reason="refused",
                )
                refund_info = {"tx_id": tx_id, "amount": str(stake)}
            # iter125 — Engagement bonus to challenger on refuse (free OR paid).
            cfg = await get_quiz_config()
            bonus_pts = int(cfg.get("quiz_challenge_challenger_bonus_points") or 0)
            if bonus_pts > 0:
                await log_bonus(
                    conn, user_id=ch["challenger_user_id"], amount_pts=bonus_pts,
                    challenge_id=cid, reason="refused_by_champion",
                )
                try:
                    await add_points(
                        conn, ch["challenger_user_id"], bonus_pts,
                        source="champion_refused",
                        metadata={"challenge_id": cid, "country_code": ch["country_code"]},
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("challenger bonus add_points failed: %s", e)
                bonus_info = {"points": bonus_pts}
    try:
        await _notify(
            user_id=ch["challenger_user_id"],
            kind="quiz_champion_refused",
            title="Défi refusé",
            body=("Le champion a refusé votre défi."
                  + (f" Mise remboursée : {ch['stake_amount']} {ch['stake_currency']}." if refund_info else "")
                  + (f" Bonus +{bonus_info['points']} pts engagement." if bonus_info else "")),
            data={"challenge_id": cid, "outcome": outcome,
                  "refund": refund_info, "bonus": bonus_info},
        )
    except Exception:
        pass
    return {"challenge_id": cid, "status": STATUS_REFUSED,
            "refund": refund_info, "bonus": bonus_info, **outcome}


# ─────────────────────────────────────────────────────────────────────
# Play / submit
# ─────────────────────────────────────────────────────────────────────

class ChallengeSubmitRequest(BaseModel):
    answers: List[int] = Field(..., min_length=5, max_length=5)


def _is_challenger(user_id: str, ch) -> bool:
    return ch["challenger_user_id"] == user_id


def _is_champion(user_id: str, ch) -> bool:
    return ch["champion_user_id"] == user_id


@router.post("/challenge/{cid}/play")
async def play_challenge(cid: str, request: Request):
    """Returns the same 5Q session (with shuffled options PER PLAYER) so each
    side gets a fair, server-authoritative quiz of identical questions."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        ch = await conn.fetchrow(
            "SELECT * FROM quiz_champion_challenges WHERE challenge_id = $1",
            cid,
        )
        if not ch:
            raise HTTPException(status_code=404, detail="Défi introuvable.")
        if not (_is_challenger(user["user_id"], ch) or _is_champion(user["user_id"], ch)):
            raise HTTPException(status_code=403, detail="Vous ne participez pas à ce défi.")
        # Status guard: challenger can play if pending OR accepted; champion only after accepted.
        is_challenger = _is_challenger(user["user_id"], ch)
        is_champion   = _is_champion(user["user_id"], ch)
        if ch["status"] in (STATUS_REFUSED, STATUS_EXPIRED, STATUS_COMPLETED, STATUS_CANCELLED):
            raise HTTPException(status_code=409, detail=f"Défi {ch['status']}.")
        if is_champion and ch["status"] not in (STATUS_ACCEPTED, STATUS_CHALLENGER_PLAYED):
            raise HTTPException(status_code=409,
                                detail="Le champion doit d'abord accepter le défi.")
        # iter124 — Symmetric anti-replay guard. Each side may call /play at
        # most ONCE per challenge. Without this, the challenger could call
        # /play repeatedly to reroll the option permutation, then submit
        # answers based on the FIRST shuffle — breaking scoring fairness.
        if is_challenger and ch["challenger_run_id"]:
            raise HTTPException(status_code=409, detail="Vous avez déjà joué.")
        if is_champion and ch["champion_run_id"]:
            raise HTTPException(status_code=409, detail="Vous avez déjà joué.")

        # Load the shared 5 questions
        session = await conn.fetchrow(
            "SELECT id, question_ids FROM quiz_sessions WHERE id = $1",
            ch["session_id"],
        )
        q_rows = await conn.fetch(
            "SELECT id, text, options, category FROM quiz_questions WHERE id = ANY($1::bigint[])",
            list(session["question_ids"]),
        )
        by_id = {r["id"]: r for r in q_rows}
        questions = []
        options_order: List[List[int]] = []
        for qid in session["question_ids"]:
            q = by_id.get(qid)
            if not q:
                continue
            original = _parse_options(q["options"])
            perm = [0, 1, 2, 3]
            random.shuffle(perm)
            shuffled = [original[i] for i in perm]
            options_order.append(perm)
            questions.append({
                "id": int(q["id"]),
                "text": q["text"],
                "options": shuffled,
                "category": q["category"],
            })

        # Use a 5×15s effective budget (mirroring per-question Quiz default).
        time_limit = 75
        # Create a quiz_user_runs row scoped to the challenge (no daily-limit check)
        now = datetime.now(timezone.utc)
        run_id = await conn.fetchval(
            """INSERT INTO quiz_user_runs
                 (user_id, session_id, started_at, options_order, time_limit_s)
               VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING id""",
            user["user_id"], ch["session_id"], now,
            _json.dumps(options_order), time_limit,
        )
        # Bind the run_id to the challenge slot
        if is_challenger:
            await conn.execute(
                "UPDATE quiz_champion_challenges SET challenger_run_id = $1 WHERE challenge_id = $2",
                int(run_id), cid,
            )
        else:
            await conn.execute(
                "UPDATE quiz_champion_challenges SET champion_run_id = $1 WHERE challenge_id = $2",
                int(run_id), cid,
            )

    return {
        "challenge_id": cid,
        "run_id": int(run_id),
        "session_id": int(ch["session_id"]),
        "time_limit_seconds": time_limit,
        "timer_mode": "per_question",
        "timer_per_question_seconds": 15,
        "auto_advance_enabled": True,
        "auto_advance_delay_ms": 900,
        "auto_advance_delays_ms": [900, 800, 700, 550, 400],
        "questions": questions,
    }


async def _resolve_winner_if_both_played(conn, cid: str) -> Optional[dict]:
    """If both runs are scored, set the winner + bonus points + status=completed.
    iter125 — PAID mode: release pot to winner (minus commission) OR refund
    both on tie. All ledger entries inserted atomically.
    """
    ch = await conn.fetchrow(
        "SELECT * FROM quiz_champion_challenges WHERE challenge_id = $1 FOR UPDATE",
        cid,
    )
    if not ch or ch["status"] == STATUS_COMPLETED:
        return None
    if ch["challenger_score"] is None or ch["champion_score"] is None:
        return None
    cs, ms = int(ch["challenger_score"]), int(ch["champion_score"])
    if cs > ms:
        winner = ch["challenger_user_id"]
    elif ms > cs:
        winner = ch["champion_user_id"]
    else:
        winner = None  # tie

    payout_info: Optional[dict] = None
    refund_info: Optional[dict] = None

    # iter125 — PAID settlement.
    if ch["mode"] == "paid" and ch["escrow_locked"]:
        stake = Decimal(str(ch["stake_amount"] or 0))
        commission_pct = Decimal(str(ch["commission_pct"] or 0))
        currency = str(ch["stake_currency"])
        # iter232 — Mission 2 (Doubler la mise): when both sides locked 2×
        # stake (allow_double + doubled), each side refunds 2× on a tie and
        # the gross pot is 4× stake on a winner.
        per_side = stake * Decimal("2") if bool(ch.get("doubled")) else stake
        if winner is None:
            # Tie: refund both stakes (no commission charged).
            t1 = await refund_player(
                conn, user_id=ch["challenger_user_id"], amount=per_side,
                currency=currency, challenge_id=cid, reason="tie",
            )
            t2 = await refund_player(
                conn, user_id=ch["champion_user_id"], amount=per_side,
                currency=currency, challenge_id=cid, reason="tie",
            )
            refund_info = {"challenger_tx": t1, "champion_tx": t2,
                           "amount": str(per_side), "currency": currency}
        else:
            gross_pot = per_side * Decimal("2")
            payout_info = await release_to_winner(
                conn, winner_user_id=winner, gross_pot=gross_pot,
                commission_pct=commission_pct, currency=currency,
                challenge_id=cid,
            )

    await conn.execute(
        """UPDATE quiz_champion_challenges
              SET status = 'completed',
                  winner_user_id = $1,
                  completed_at = NOW(),
                  escrow_payout_tx_id = $2,
                  commission_tx_id = $3
            WHERE challenge_id = $4""",
        winner,
        (payout_info or {}).get("payout_tx_id"),
        (payout_info or {}).get("commission_tx_id"),
        cid,
    )
    # Free mode: award engagement bonus to the winner (admin-tunable later)
    if winner and ch["mode"] == "free":
        try:
            await add_points(
                conn, winner, 30, source="champion_win",
                metadata={"challenge_id": cid,
                          "loser": (ch["champion_user_id"] if winner == ch["challenger_user_id"] else ch["challenger_user_id"])},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("champion_win points bonus failed: %s", e)
    return {
        "winner_user_id": winner,
        "challenger_score": cs,
        "champion_score": ms,
        "payout": payout_info,
        "refund": refund_info,
    }


@router.post("/challenge/{cid}/submit")
async def submit_challenge(cid: str, req: ChallengeSubmitRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_ddl(conn)
            ch = await conn.fetchrow(
                "SELECT * FROM quiz_champion_challenges WHERE challenge_id = $1 FOR UPDATE",
                cid,
            )
            if not ch:
                raise HTTPException(status_code=404, detail="Défi introuvable.")
            is_challenger = _is_challenger(user["user_id"], ch)
            is_champion = _is_champion(user["user_id"], ch)
            if not (is_challenger or is_champion):
                raise HTTPException(status_code=403, detail="Vous ne participez pas à ce défi.")
            # Pick the right run
            run_id = ch["challenger_run_id"] if is_challenger else ch["champion_run_id"]
            if not run_id:
                raise HTTPException(status_code=409,
                                    detail="Aucune partie en cours — appelez d'abord /play.")
            run = await conn.fetchrow(
                "SELECT * FROM quiz_user_runs WHERE id = $1 FOR UPDATE", run_id,
            )
            if not run:
                raise HTTPException(status_code=500, detail="Partie corrompue.")
            if run["submitted_at"]:
                raise HTTPException(status_code=409, detail="Partie déjà soumise.")

            # Score it (mirror /api/quiz/submit logic minus daily-points side effects).
            now = datetime.now(timezone.utc)
            elapsed = (now - run["started_at"]).total_seconds()
            effective_limit = int(run["time_limit_s"] or 75)
            timed_out = elapsed > (effective_limit + SESSION_TIME_NETWORK_GRACE_SECONDS)

            session = await conn.fetchrow(
                "SELECT question_ids FROM quiz_sessions WHERE id = $1", run["session_id"],
            )
            q_rows = await conn.fetch(
                "SELECT id, correct_index FROM quiz_questions WHERE id = ANY($1::bigint[])",
                list(session["question_ids"]),
            )
            correct_map = {int(r["id"]): int(r["correct_index"]) for r in q_rows}
            order = [int(qid) for qid in session["question_ids"]]
            raw_perm = run["options_order"]
            if isinstance(raw_perm, str):
                try:
                    perms = _json.loads(raw_perm)
                except (ValueError, TypeError):
                    perms = []
            else:
                perms = raw_perm or []
            while len(perms) < SESSION_SIZE:
                perms.append([0, 1, 2, 3])

            answers = list(req.answers)
            correct_count = 0
            for i, qid in enumerate(order):
                original_correct = correct_map.get(qid, -1)
                perm = perms[i] if i < len(perms) else [0, 1, 2, 3]
                given = answers[i] if i < len(answers) else -1
                given_original = perm[given] if 0 <= given < len(perm) else -1
                if given_original >= 0 and given_original == original_correct:
                    correct_count += 1

            await conn.execute(
                """UPDATE quiz_user_runs
                     SET submitted_at = $1, answers = $2, correct_count = $3,
                         points_awarded = 0, timed_out = $4
                   WHERE id = $5""",
                now, answers, correct_count, timed_out, run_id,
            )
            # Persist score on the challenge
            if is_challenger:
                await conn.execute(
                    """UPDATE quiz_champion_challenges
                          SET challenger_score = $1,
                              status = CASE WHEN status = 'champion_played' THEN status ELSE 'challenger_played' END
                        WHERE challenge_id = $2""",
                    correct_count, cid,
                )
            else:
                await conn.execute(
                    """UPDATE quiz_champion_challenges
                          SET champion_score = $1,
                              status = CASE WHEN status = 'challenger_played' THEN status ELSE 'champion_played' END
                        WHERE challenge_id = $2""",
                    correct_count, cid,
                )
            resolved = await _resolve_winner_if_both_played(conn, cid)

    if resolved:
        try:
            for uid in (ch["challenger_user_id"], ch["champion_user_id"]):
                won = (resolved["winner_user_id"] == uid)
                tied = resolved["winner_user_id"] is None
                await _notify(
                    user_id=uid,
                    kind="quiz_champion_completed",
                    title=("Défi gagné 🏆" if won else ("Défi égalité" if tied else "Défi terminé")),
                    body=f"Score final : {resolved['challenger_score']} – {resolved['champion_score']}",
                    data={"challenge_id": cid, **resolved},
                )
        except Exception:
            pass

    return {
        "challenge_id": cid,
        "your_correct": correct_count,
        "total": SESSION_SIZE,
        "timed_out": timed_out,
        "resolved": resolved,
    }


# ─────────────────────────────────────────────────────────────────────
# Listing / detail
# ─────────────────────────────────────────────────────────────────────

@router.get("/challenges/me")
async def my_challenges(request: Request, status: str = Query(default="", max_length=24),
                        limit: int = Query(default=30, ge=1, le=200),
                        offset: int = Query(default=0, ge=0)):
    user = await get_current_user(request)
    pool = await get_pool()
    where = ["(challenger_user_id = $1 OR champion_user_id = $1)"]
    params: list = [user["user_id"]]
    if status:
        params.append(status)
        where.append(f"status = ${len(params)}")
    params_lim = [*params, limit, offset]
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        rows = await conn.fetch(
            f"""SELECT challenge_id, challenger_user_id, champion_user_id,
                       country_code, mode, stake_amount, stake_currency,
                       status, challenger_score, champion_score, winner_user_id,
                       created_at, accepted_at, refused_at, expires_at, completed_at
                  FROM quiz_champion_challenges
                 WHERE {' AND '.join(where)}
              ORDER BY created_at DESC
                 LIMIT ${len(params)+1} OFFSET ${len(params)+2}""",
            *params_lim,
        )
    return {
        "items": [
            {
                "challenge_id": r["challenge_id"],
                "role": "challenger" if r["challenger_user_id"] == user["user_id"] else "champion",
                "challenger_user_id": r["challenger_user_id"],
                "champion_user_id": r["champion_user_id"],
                "country_code": r["country_code"],
                "mode": r["mode"],
                "stake_amount": float(r["stake_amount"] or 0),
                "stake_currency": r["stake_currency"],
                "status": r["status"],
                "challenger_score": r["challenger_score"],
                "champion_score": r["champion_score"],
                "winner_user_id": r["winner_user_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "accepted_at": r["accepted_at"].isoformat() if r["accepted_at"] else None,
                "refused_at": r["refused_at"].isoformat() if r["refused_at"] else None,
                "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
                "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            } for r in rows
        ],
    }


@router.get("/challenges/{cid}")
async def get_challenge(cid: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        r = await conn.fetchrow(
            "SELECT * FROM quiz_champion_challenges WHERE challenge_id = $1", cid,
        )
        if not r:
            raise HTTPException(status_code=404, detail="Défi introuvable.")
        is_part = (r["challenger_user_id"] == user["user_id"]
                   or r["champion_user_id"] == user["user_id"])
        if not is_part and user.get("role") not in ("admin", "superadmin"):
            raise HTTPException(status_code=403, detail="Accès refusé.")
        is_challenger = r["challenger_user_id"] == user["user_id"]
        is_champion = r["champion_user_id"] == user["user_id"]
        can_play = False
        # iter231 — Include awaiting_acceptor so the challenger of an open
        # challenge (just created via /challenge/open) can immediately play
        # their 5 questions before sharing the link. Without this branch,
        # the GET /challenges/{cid} response returned can_play=false right
        # after creation → empty page on /games/quiz/challenges/{cid}.
        if r["status"] in (STATUS_AWAITING_ACCEPTOR, STATUS_ACCEPTED,
                           STATUS_CHALLENGER_PLAYED, STATUS_CHAMPION_PLAYED):
            if is_challenger and not r["challenger_run_id"]:
                can_play = True
            elif is_champion and not r["champion_run_id"]:
                can_play = True
        elif r["status"] == STATUS_PENDING and is_challenger and not r["challenger_run_id"]:
            # The challenger may pre-play before the champion accepts? In our MVP
            # we keep the symmetric flow: challenger waits for accept. So no.
            can_play = False
        # iter225 — Enrich with both players' display name + avatar (so the
        # Champion can see who is challenging him before accepting).
        users_info = {}
        for uid in (r["challenger_user_id"], r["champion_user_id"]):
            urow = await conn.fetchrow(
                "SELECT user_id, first_name, last_name, username, avatar FROM users WHERE user_id = $1",
                uid,
            )
            if urow:
                disp = (urow["first_name"] or urow["username"] or "Joueur").strip()
                if urow["last_name"]:
                    disp = f"{disp} {urow['last_name']}".strip()
                users_info[uid] = {
                    "user_id": uid,
                    "name": disp,
                    "avatar_url": urow["avatar"],
                }
            else:
                users_info[uid] = {"user_id": uid, "name": "Joueur", "avatar_url": None}
        # iter225 — Viewer wallet snapshot (only USD canonical, no FX leak).
        viewer_wallet = await conn.fetchrow(
            "SELECT balance, currency FROM wallets WHERE user_id = $1",
            user["user_id"],
        )
        # iter225 — commission_pct from games_settings so the UI can show the
        # exact pot/commission/winnings breakdown for the Champion.
        cfg = await get_quiz_config()
        commission_pct = float(cfg.get("quiz_challenge_commission_pct") or 10)
    return {
        "challenge_id": r["challenge_id"],
        "role": "challenger" if is_challenger else ("champion" if is_champion else "viewer"),
        "challenger_user_id": r["challenger_user_id"],
        "champion_user_id": r["champion_user_id"],
        "challenger": users_info.get(r["challenger_user_id"]),
        "champion":   users_info.get(r["champion_user_id"]),
        "country_code": r["country_code"],
        "session_id": int(r["session_id"]),
        "mode": r["mode"],
        "stake_amount": float(r["stake_amount"] or 0),
        "stake_currency": r["stake_currency"],
        "commission_pct": commission_pct,
        "status": r["status"],
        "challenger_score": r["challenger_score"],
        "champion_score": r["champion_score"],
        "winner_user_id": r["winner_user_id"],
        "challenger_run_id": int(r["challenger_run_id"]) if r["challenger_run_id"] else None,
        "champion_run_id": int(r["champion_run_id"]) if r["champion_run_id"] else None,
        "can_play": can_play,
        "viewer_balance":  float(viewer_wallet["balance"]) if viewer_wallet else 0.0,
        "viewer_currency": (viewer_wallet["currency"] if viewer_wallet else "USD"),
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
        "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
        # iter233 — Mission 2 (Doubler la mise)
        "allow_double": bool(r["allow_double"]),
        "doubled":      bool(r["doubled"]),
    }


# ─────────────────────────────────────────────────────────────────────
# Admin
# ─────────────────────────────────────────────────────────────────────

async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Accès admin requis")
    return user


@router.post("/admin/promote-all")
async def admin_promote_all(request: Request, window_days: int = Query(default=DEFAULT_WINDOW_DAYS, ge=1, le=90)):
    await _require_admin(request)
    return await promote_champions(window_days=window_days)


@router.post("/admin/expire-stale")
async def admin_expire_stale(request: Request, limit: int = Query(default=100, ge=1, le=1000)):
    """iter125 — Bulk-expire challenges past their expires_at, refunding all
    locked stakes and emitting challenger bonuses (idempotent)."""
    await _require_admin(request)
    pool = await get_pool()
    expired = []
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        rows = await conn.fetch(
            """SELECT challenge_id FROM quiz_champion_challenges
                WHERE status IN ('pending','accepted','challenger_played','champion_played')
                  AND expires_at IS NOT NULL AND expires_at < NOW()
             ORDER BY expires_at LIMIT $1""",
            limit,
        )
        for r in rows:
            cid = r["challenge_id"]
            async with conn.transaction():
                ch = await conn.fetchrow(
                    "SELECT * FROM quiz_champion_challenges WHERE challenge_id = $1 FOR UPDATE",
                    cid,
                )
                info = await _lazy_expire(conn, ch)
            expired.append({"challenge_id": cid, "info": info})
    return {"expired_count": len(expired), "items": expired}


@router.get("/admin/kpis")
async def admin_kpis(request: Request,
                     window_days: int = Query(default=30, ge=1, le=365)):
    """iter128 — Admin KPI dashboard for the Champion subsystem.
    All earnings/commissions sourced from the LEDGER (transactions table)
    — no approximations.
    """
    await _require_admin(request)
    pool = await get_pool()
    win = str(window_days)
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        # Headline counters
        challenges_total = await conn.fetchval(
            "SELECT COUNT(*) FROM quiz_champion_challenges "
            "WHERE created_at > NOW() - ($1 || ' days')::interval", win,
        ) or 0
        by_mode = await conn.fetch(
            """SELECT mode, status, COUNT(*) AS n FROM quiz_champion_challenges
                WHERE created_at > NOW() - ($1 || ' days')::interval
             GROUP BY mode, status""", win,
        )
        # GMV (paid only) = sum(stake * 2) over completed paid challenges
        gmv = await conn.fetchval(
            """SELECT COALESCE(SUM(stake_amount * 2), 0)
                 FROM quiz_champion_challenges
                WHERE mode = 'paid' AND status = 'completed'
                  AND completed_at > NOW() - ($1 || ' days')::interval""", win,
        ) or 0
        # Revenue = sum of quiz_challenge_commission rows
        revenue = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) FROM transactions
                WHERE type = 'quiz_challenge_commission'
                  AND created_at > NOW() - ($1 || ' days')::interval""", win,
        ) or 0
        refunds = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) FROM transactions
                WHERE type = 'quiz_challenge_refund'
                  AND created_at > NOW() - ($1 || ' days')::interval""", win,
        ) or 0
        bonus_pts = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) FROM transactions
                WHERE type = 'quiz_challenge_bonus'
                  AND created_at > NOW() - ($1 || ' days')::interval""", win,
        ) or 0
        # Top countries by volume + revenue
        top_countries = await conn.fetch(
            """SELECT c.country_code,
                      COUNT(*) AS challenges,
                      COALESCE(SUM(c.stake_amount * 2)
                               FILTER (WHERE c.status='completed' AND c.mode='paid'), 0) AS gmv,
                      COALESCE(SUM(t.amount)
                               FILTER (WHERE t.type='quiz_challenge_commission'), 0) AS revenue
                 FROM quiz_champion_challenges c
                 LEFT JOIN transactions t ON t.tx_id = c.commission_tx_id
                WHERE c.created_at > NOW() - ($1 || ' days')::interval
             GROUP BY c.country_code
             ORDER BY challenges DESC
                LIMIT 10""", win,
        )
        # Active champions
        active_champions = await conn.fetchval(
            "SELECT COUNT(*) FROM quiz_country_champions WHERE demoted_at IS NULL",
        ) or 0
        # Refusal hot-list
        top_refusers = await conn.fetch(
            """SELECT c.user_id, c.country_code,
                      c.refusal_count_consecutive,
                      (SELECT COUNT(*) FROM quiz_champion_refusals r
                        WHERE r.champion_user_id = c.user_id
                          AND r.country_code = c.country_code
                          AND r.refused_at > NOW() - INTERVAL '30 days'
                          AND r.refused_at >= c.promoted_at) AS refusals_30d,
                      u.first_name, u.username, u.avatar
                 FROM quiz_country_champions c
                 LEFT JOIN users u ON u.user_id = c.user_id
                WHERE c.demoted_at IS NULL
                  AND (c.refusal_count_consecutive > 0
                       OR EXISTS (SELECT 1 FROM quiz_champion_refusals r
                                   WHERE r.champion_user_id = c.user_id
                                     AND r.country_code = c.country_code))
             ORDER BY c.refusal_count_consecutive DESC
                LIMIT 10""",
        )
        return {
            "window_days": window_days,
            "challenges_total": int(challenges_total),
            "by_mode": [dict(r) for r in by_mode],
            "gmv": float(gmv),
            "revenue_japap": float(revenue),
            "refunds_total": float(refunds),
            "engagement_bonus_pts": float(bonus_pts),
            "active_champions": int(active_champions),
            "top_countries": [
                {
                    "country_code": r["country_code"],
                    "challenges": int(r["challenges"]),
                    "gmv": float(r["gmv"]),
                    "revenue": float(r["revenue"]),
                } for r in top_countries
            ],
            "top_refusers": [
                {
                    "user_id": r["user_id"],
                    "country_code": r["country_code"],
                    "refusal_count_consecutive": int(r["refusal_count_consecutive"]),
                    "refusals_30d": int(r["refusals_30d"] or 0),
                    "first_name": r["first_name"] or "",
                    "username": r["username"] or "",
                    "avatar": r["avatar"] or "",
                } for r in top_refusers
            ],
        }


class AdminSetChampionRequest(BaseModel):
    user_id: str = Field(..., min_length=4, max_length=64)


@router.post("/admin/{country_code}/set")
async def admin_set(country_code: str, req: AdminSetChampionRequest, request: Request):
    await _require_admin(request)
    cc = country_code.upper()[:2]
    try:
        return await admin_set_champion(cc, req.user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class AdminDemoteRequest(BaseModel):
    reason: str = Field(default="admin_demote", max_length=64)


@router.post("/admin/{country_code}/demote")
async def admin_demote(country_code: str, req: AdminDemoteRequest, request: Request):
    await _require_admin(request)
    return await admin_demote_champion(country_code, req.reason)


@router.get("/admin/list")
async def admin_list(request: Request, include_demoted: bool = False):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        where = "" if include_demoted else "WHERE c.demoted_at IS NULL"
        rows = await conn.fetch(
            f"""SELECT c.country_code, c.user_id, c.promoted_at, c.source,
                       c.refusal_count_consecutive, c.last_refusal_at,
                       c.demoted_at, c.demoted_reason,
                       u.first_name, u.last_name, u.username, u.avatar
                  FROM quiz_country_champions c
                  LEFT JOIN users u ON u.user_id = c.user_id
                  {where}
              ORDER BY c.country_code"""
        )
        # Also compute rolling 30d refusals per country, scoped to current
        # promotion window (refusals before promoted_at don't count).
        items = []
        for r in rows:
            rolling = await conn.fetchval(
                """SELECT COUNT(*) FROM quiz_champion_refusals
                    WHERE champion_user_id = $1
                      AND country_code = $2
                      AND refused_at >= $3
                      AND refused_at > NOW() - INTERVAL '30 days'""",
                r["user_id"], r["country_code"], r["promoted_at"],
            ) or 0
            items.append({
                "country_code": r["country_code"],
                "user_id": r["user_id"],
                "promoted_at": r["promoted_at"].isoformat() if r["promoted_at"] else None,
                "source": r["source"],
                "refusal_count_consecutive": int(r["refusal_count_consecutive"] or 0),
                "refusal_count_30d": int(rolling),
                "last_refusal_at": r["last_refusal_at"].isoformat() if r["last_refusal_at"] else None,
                "demoted_at": r["demoted_at"].isoformat() if r["demoted_at"] else None,
                "demoted_reason": r["demoted_reason"],
                "user": {
                    "user_id": r["user_id"],
                    "first_name": r["first_name"] or "",
                    "last_name": r["last_name"] or "",
                    "username": r["username"] or "",
                    "avatar": r["avatar"] or "",
                },
            })
        return {"items": items}


@router.get("/admin/challenges")
async def admin_challenges(request: Request,
                           status: str = Query(default="", max_length=24),
                           country_code: str = Query(default="", max_length=2),
                           limit: int = Query(default=50, ge=1, le=500),
                           offset: int = Query(default=0, ge=0)):
    await _require_admin(request)
    pool = await get_pool()
    where = ["1=1"]
    params: list = []
    if status:
        params.append(status)
        where.append(f"status = ${len(params)}")
    if country_code:
        params.append(country_code.upper()[:2])
        where.append(f"country_code = ${len(params)}")
    params_lim = [*params, limit, offset]
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM quiz_champion_challenges WHERE {' AND '.join(where)}", *params,
        )
        rows = await conn.fetch(
            f"""SELECT challenge_id, challenger_user_id, champion_user_id,
                       country_code, mode, stake_amount, stake_currency,
                       status, challenger_score, champion_score, winner_user_id,
                       created_at, completed_at
                  FROM quiz_champion_challenges
                 WHERE {' AND '.join(where)}
              ORDER BY created_at DESC
                 LIMIT ${len(params)+1} OFFSET ${len(params)+2}""",
            *params_lim,
        )
    return {
        "total": int(total or 0),
        "items": [
            {
                "challenge_id": r["challenge_id"],
                "challenger_user_id": r["challenger_user_id"],
                "champion_user_id": r["champion_user_id"],
                "country_code": r["country_code"],
                "mode": r["mode"],
                "stake_amount": float(r["stake_amount"] or 0),
                "stake_currency": r["stake_currency"],
                "status": r["status"],
                "challenger_score": r["challenger_score"],
                "champion_score": r["champion_score"],
                "winner_user_id": r["winner_user_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            } for r in rows
        ],
    }



# ════════════════════════════════════════════════════════════════════
# iter228 — Open Challenge flow ("A creates → A plays → A shares link →
# B claims → B plays → auto-resolve").
#
# Differences with the existing /challenge endpoint:
#   • champion_user_id is NULL until B claims (status='awaiting_acceptor')
#   • A's stake is locked AT CREATION (so the link is always backed)
#   • Public preview endpoint is auth-free so the share link can be
#     opened from WhatsApp / iMessage without logging in first
#   • Score is HIDDEN from the public preview (anti-cheat)
# ════════════════════════════════════════════════════════════════════

class _OpenChallengeReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "free"  # 'free' | 'paid'
    stake_amount: Optional[Decimal] = None
    country_code: str = "CM"
    notes: Optional[str] = None
    allow_double: bool = False  # iter232 — allow B to ×2 (locks 2× for A)


@router.post("/challenge/open")
async def create_open_challenge(req: _OpenChallengeReq, request: Request):
    """A creates an OPEN challenge. The challenge has no champion yet —
    a public link will be issued and any logged-in user can claim it.

    iter232 — Mission 2 (Doubler la mise): if `allow_double=True` and the
    challenge is paid, A's wallet is debited by 2× the stake at creation
    time so a future B who chooses to double has nothing extra to settle
    on A's side. If A's wallet can't cover 2× stake, the call returns
    HTTP 402 and the UI is expected to grey out the toggle.
    """
    user = await get_current_user(request)
    cfg = await get_quiz_config()
    paid_mode = req.mode == "paid"
    expiry_hours = int(cfg.get("quiz_challenge_expiry_hours") or 24)

    if paid_mode and not bool(cfg.get("quiz_challenge_paid_enabled", False)):
        raise HTTPException(status_code=403, detail="Le mode payant est désactivé.")

    stake_amount = Decimal("0")
    stake_currency = "USD"
    allow_double = bool(req.allow_double) and paid_mode  # only meaningful in paid
    if paid_mode:
        try:
            stake_amount = Decimal(str(req.stake_amount or 0))
        except Exception as e:
            raise HTTPException(status_code=400, detail="Mise invalide.") from e
        stake_min = int(cfg.get("quiz_challenge_stake_min") or 1)
        stake_max = int(cfg.get("quiz_challenge_stake_max") or 200)
        if stake_amount < stake_min:
            raise HTTPException(status_code=400, detail=f"Mise minimale : {stake_min} USD.")
        if stake_amount > stake_max:
            raise HTTPException(status_code=400, detail=f"Mise maximale : {stake_max} USD.")

    cc = (req.country_code or "CM").upper()[:2]
    commission_pct = float(cfg.get("quiz_challenge_commission_pct") or 10)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        # Pick the next quiz session for this challenge — both A and B will
        # answer the SAME 5 questions for fairness.
        session = await conn.fetchrow(
            "SELECT id FROM quiz_sessions ORDER BY RANDOM() LIMIT 1",
        )
        if not session:
            raise HTTPException(
                status_code=503,
                detail="Aucune session quiz disponible.",
            )
        cid = _new_challenge_id()
        escrow_locked = False
        if paid_mode:
            w = await conn.fetchrow(
                "SELECT currency FROM wallets WHERE user_id = $1",
                user["user_id"],
            )
            if not w:
                raise HTTPException(status_code=404, detail="Portefeuille introuvable.")
            stake_currency = (w["currency"] or "USD").upper()
            # iter232 — pre-lock 2× stake when the challenger opts in to
            # being doubled, so the pot can never be unbalanced later.
            await lock_stake(
                conn, user_id=user["user_id"], amount=stake_amount,
                currency=stake_currency, challenge_id=cid,
            )
            if allow_double:
                # Lock the second slice with type 'quiz_challenge_lock_double'
                # for cleaner audit (Mission 2 Q2.3).
                await lock_stake_double(
                    conn, user_id=user["user_id"], amount=stake_amount,
                    currency=stake_currency, challenge_id=cid,
                    notes=f"Pre-lock for double (creator pre-authorised) {cid}",
                )
            escrow_locked = True

        expires_at = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
        await conn.execute(
            """INSERT INTO quiz_champion_challenges
                 (challenge_id, challenger_user_id, champion_user_id,
                  country_code, session_id, mode, stake_amount, stake_currency,
                  commission_pct, status, escrow_locked, allow_double,
                  created_at, expires_at, notes)
               VALUES ($1, $2, NULL, $3, $4, $5, $6, $7, $8,
                       'awaiting_acceptor', $9, $10, NOW(), $11, $12)""",
            cid, user["user_id"], cc, int(session["id"]), req.mode,
            stake_amount, stake_currency, commission_pct, escrow_locked,
            allow_double, expires_at, (req.notes or "")[:200],
        )
    return {
        "challenge_id":   cid,
        "status":         "awaiting_acceptor",
        "session_id":     int(session["id"]),
        "mode":           req.mode,
        "stake_amount":   float(stake_amount),
        "stake_currency": stake_currency,
        "commission_pct": commission_pct,
        "allow_double":   allow_double,
        "expires_at":     expires_at.isoformat(),
    }


@router.get("/challenge/public/{cid}")
async def get_open_challenge_public(cid: str):
    """No-auth preview of an open challenge — used by the public landing
    page accessible from WhatsApp / iMessage. NEVER returns scores."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        r = await conn.fetchrow(
            "SELECT * FROM quiz_champion_challenges WHERE challenge_id = $1", cid,
        )
        if not r:
            raise HTTPException(status_code=404, detail="Défi introuvable.")
        creator = await conn.fetchrow(
            "SELECT first_name, last_name, username, avatar FROM users WHERE user_id = $1",
            r["challenger_user_id"],
        )
        creator_name = "Joueur"
        if creator:
            creator_name = (creator["first_name"] or creator["username"] or "Joueur").strip()
            if creator["last_name"]:
                creator_name = f"{creator_name} {creator['last_name']}".strip()
    challenger_played = r["challenger_run_id"] is not None
    return {
        "challenge_id":      r["challenge_id"],
        "status":            r["status"],
        "mode":              r["mode"],
        "stake_amount":      float(r["stake_amount"] or 0),
        "stake_currency":    r["stake_currency"],
        "commission_pct":    float(r["commission_pct"] or 10),
        "challenger_name":   creator_name,
        "challenger_avatar": creator["avatar"] if creator else None,
        "challenger_played": challenger_played,
        "expires_at":        r["expires_at"].isoformat() if r["expires_at"] else None,
        "country_code":      r["country_code"],
        "is_open":           r["champion_user_id"] is None and r["status"] in ("awaiting_acceptor", "challenger_played"),
        # iter232 — Mission 2 (Doubler la mise)
        "allow_double":      bool(r["allow_double"]),
        "doubled":           bool(r["doubled"]),
    }


class _ClaimReq(BaseModel):
    double: bool = False  # iter232 — B opts to double the stake


@router.post("/challenge/{cid}/claim")
async def claim_open_challenge(cid: str, request: Request,
                                req: Optional[_ClaimReq] = None):
    """B (any logged-in user) claims an open challenge. Locks B's stake
    if paid, then transitions the challenge to 'accepted' so B can play.

    iter232 — Mission 2 (Doubler la mise): if `req.double=True` AND the
    challenge has `allow_double=True`, B's lock is 2× the stake and the
    challenge is flagged `doubled=True`. A's second slice was pre-locked
    at challenge creation, so the pot becomes 4× stake on the spot.
    """
    user = await get_current_user(request)
    want_double = bool(req.double) if req else False
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        async with conn.transaction():
            r = await conn.fetchrow(
                "SELECT * FROM quiz_champion_challenges WHERE challenge_id = $1 FOR UPDATE",
                cid,
            )
            if not r:
                raise HTTPException(status_code=404, detail="Défi introuvable.")
            if r["champion_user_id"] is not None:
                raise HTTPException(status_code=409, detail="Ce défi a déjà été accepté.")
            if r["challenger_user_id"] == user["user_id"]:
                raise HTTPException(status_code=403, detail="Tu ne peux pas accepter ton propre défi.")
            if r["status"] not in ("awaiting_acceptor", "challenger_played"):
                raise HTTPException(status_code=409, detail="Ce défi n'est plus disponible.")
            if r["expires_at"] and r["expires_at"] < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Ce défi a expiré.")
            # iter232 — gate the double request.
            if want_double and not bool(r["allow_double"]):
                raise HTTPException(status_code=400,
                    detail="Le doublement n'a pas été autorisé par le créateur.")
            doubled = bool(want_double and r["allow_double"]) and r["mode"] == "paid"

            if r["mode"] == "paid":
                base = Decimal(str(r["stake_amount"] or 0))
                required = base * 2 if doubled else base
                w = await conn.fetchrow(
                    "SELECT currency, balance FROM wallets WHERE user_id = $1",
                    user["user_id"],
                )
                if not w:
                    raise HTTPException(status_code=404, detail="Portefeuille introuvable.")
                if (w["currency"] or "USD").upper() != r["stake_currency"]:
                    raise HTTPException(status_code=400,
                        detail="Devise du wallet incompatible avec celle du défi.")
                if Decimal(str(w["balance"] or 0)) < required:
                    gap = (required - Decimal(str(w["balance"] or 0))).quantize(Decimal("0.01"))
                    raise HTTPException(status_code=402,
                        detail=(f"Solde insuffisant : disponible {w['balance']} {r['stake_currency']}, "
                                f"requis {required} {r['stake_currency']} (manque {gap} {r['stake_currency']})."))
                # First slice — base stake.
                await lock_stake(
                    conn, user_id=user["user_id"], amount=base,
                    currency=r["stake_currency"], challenge_id=cid,
                )
                # Second slice — only if B doubles.
                if doubled:
                    await lock_stake_double(
                        conn, user_id=user["user_id"], amount=base,
                        currency=r["stake_currency"], challenge_id=cid,
                        notes=f"Lock (×2) by acceptor on {cid}",
                    )
                elif bool(r["allow_double"]):
                    # iter232 — A pre-locked 2× at creation. B accepted but
                    # chose not to double → release A's extra slice back.
                    await refund_player(
                        conn, user_id=r["challenger_user_id"], amount=base,
                        currency=r["stake_currency"], challenge_id=cid,
                        reason="double_unused",
                    )
            new_status = "challenger_played" if r["challenger_run_id"] else "accepted"
            await conn.execute(
                "UPDATE quiz_champion_challenges "
                "   SET champion_user_id = $1, status = $2, accepted_at = NOW(), "
                "       doubled = $3 "
                " WHERE challenge_id = $4",
                user["user_id"], new_status, doubled, cid,
            )
    return {"challenge_id": cid, "status": "accepted",
            "session_id": int(r["session_id"]), "doubled": doubled}
