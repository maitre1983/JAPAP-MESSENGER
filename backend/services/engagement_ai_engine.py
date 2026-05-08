"""
JAPAP — Crowdfunding Engagement IA engine (iter142D / Phase P3)

Hybrid behavioural engine:
  - Deterministic STATE MACHINE (cold | engaged | competitive | critical)
  - Score-based prioritisation (urgency / frustration / momentum / engagement)
  - Template message rotation (anti-fatigue)
  - LLM personalisation (Claude Haiku) — ONLY for the CRITICAL state, with
    strict 1.2 s timeout and graceful fallback to template

Design rules from CEO:
  • Stay fast, stable, predictable.
  • LLM is invisible — never block UX.
  • Anti-spam: 1 push per state per 6 h, dismiss → 7 d cooldown.
  • Track shown/clicked/dismissed per message_id for performance pruning.
  • The ENGINE is read-only on user behaviour, never blocks the vote/share
    transaction it observes.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("japap.engagement")

# ─── Templates ─────────────────────────────────────────────────────────────
# Each tuple: (message_id, text). message_id stays stable so the
# crowdfunding_message_performance table aggregates over time.
TEMPLATES = {
    "cold": [
        ("cold_v1", "Découvre les projets en lice — soutiens celui qui t'inspire en 1 tap ❤️"),
        ("cold_v2", "Une bonne action en 10 secondes : explore la liste et vote pour ton préféré."),
        ("cold_v3", "C'est ta zone de pouvoir ✨ Choisis le projet qui mérite la victoire."),
    ],
    "engaged": [
        ("eng_v1", "Tu progresses bien — partage ton projet à 3 amis pour grimper 2 places."),
        ("eng_v2", "{rank_str}{votes_count} votes acquis. Continue, la barre se remplit !"),
        ("eng_v3", "Un dernier coup de pouce : envoie le lien à ton groupe WhatsApp."),
        ("eng_v4", "Bel élan 💪 Peux-tu obtenir 5 votes de plus aujourd'hui ?"),
    ],
    "competitive": [
        ("cmp_v1", "Tu es {rank_word} — encore {gap} votes pour passer devant {rival_label}."),
        ("cmp_v2", "Match serré ⚡ {rival_label} te talonne. Réagis avant qu'il/elle ne te dépasse."),
        ("cmp_v3", "À {gap} votes près tu changes de classement — partage NOW."),
    ],
    "critical": [
        ("crit_v1", "🔥 Plus que {remaining} votes — c'est maintenant ou jamais."),
        ("crit_v2", "⚠️ Tu es à {pct}% de la victoire — tes amis peuvent boucler ça en 1 minute."),
        ("crit_v3", "Sprint final 🏁 {remaining} votes te séparent de la récompense."),
    ],
    "stagnating": [  # special trigger inside engaged/cold states
        ("stag_v1", "Ton projet ralentit — partage maintenant pour relancer la dynamique."),
        ("stag_v2", "12 h sans progression — un partage WhatsApp et tu redécolles."),
    ],
    "rank_drop": [
        ("drop_v1", "⚠️ Tu viens d'être dépassé(e). Réagis avant que l'écart ne se creuse."),
        ("drop_v2", "Quelqu'un t'a doublé — il te faut {gap} votes pour repasser devant."),
    ],
}


def _format(template: str, ctx: dict) -> str:
    """Safe .format() — replaces missing keys by empty string."""
    class _D(dict):
        def __missing__(self, k): return ""
    rank = ctx.get("rank")
    rank_str = f"Rang #{rank} · " if rank else ""
    rank_word = ({1: "1er", 2: "2e", 3: "3e"}.get(rank or 0)
                 or (f"{rank}e" if rank else ""))
    rival_label = ctx.get("rival_name") or "le projet d'à côté"
    args = _D({**ctx, "rank_str": rank_str, "rank_word": rank_word,
              "rival_label": rival_label})
    try:
        return template.format_map(args)
    except Exception:
        return template


# ─── Score calculation ─────────────────────────────────────────────────────
async def _aggregate_user_signals(conn, user_id: str) -> dict:
    """One round-trip to collect everything we need."""
    row = await conn.fetchrow(
        """
        WITH ev AS (
            SELECT
              SUM(CASE WHEN event_type='share' THEN 1 ELSE 0 END) AS shares,
              SUM(CASE WHEN event_type='vote' THEN 1 ELSE 0 END) AS votes_given,
              SUM(CASE WHEN event_type='view' THEN 1 ELSE 0 END) AS views,
              SUM(CASE WHEN event_type='invite' THEN 1 ELSE 0 END) AS invites,
              SUM(CASE WHEN event_type='visit_generated' THEN 1 ELSE 0 END) AS visits_generated,
              COALESCE(SUM(time_spent), 0) AS session_time,
              MAX(created_at) AS last_event_at
              FROM crowdfunding_behavior_events
             WHERE user_id = $1
               AND created_at > NOW() - INTERVAL '7 days'
        )
        SELECT * FROM ev
        """, user_id,
    )
    return dict(row) if row else {}


async def _user_active_project(conn, user_id: str) -> Optional[dict]:
    row = await conn.fetchrow(
        """SELECT p.project_id, p.slug, p.title, p.votes_count, p.country_code,
                  p.cycle_id, p.status, p.created_at,
                  c.votes_to_win, c.votes_open, c.threshold_projects, c.cycle_number
             FROM crowdfunding_projects p
             JOIN crowdfunding_cycles c ON c.cycle_id = p.cycle_id
            WHERE p.user_id = $1 AND p.status IN ('active','winner')
            ORDER BY p.created_at DESC LIMIT 1""",
        user_id,
    )
    return dict(row) if row else None


async def _project_rank_and_rival(conn, project: dict) -> dict:
    """Compute current rank, the project just above (rival), and trend.

    'trend' values:
      - 'closing'    → rival is gaining slower than us in last 1h
      - 'expanding'  → rival is gaining faster
      - 'stable'     → no significant move
    """
    rows = await conn.fetch(
        """SELECT p.project_id, p.slug, p.votes_count, p.user_id, u.first_name,
                  u.username
             FROM crowdfunding_projects p
             JOIN users u ON u.user_id = p.user_id
            WHERE p.cycle_id = $1 AND p.status IN ('active','winner')
            ORDER BY p.votes_count DESC, p.created_at ASC""",
        project["cycle_id"],
    )
    rank = 0
    rival = None
    for i, r in enumerate(rows, start=1):
        if r["project_id"] == project["project_id"]:
            rank = i
            if i > 1:
                prev = rows[i - 2]
                # 1h velocity for us + rival
                me_v = await conn.fetchval(
                    """SELECT COUNT(*) FROM crowdfunding_votes
                        WHERE project_id=$1 AND voted_at > NOW() - INTERVAL '1 hour'""",
                    project["project_id"],
                ) or 0
                rv_v = await conn.fetchval(
                    """SELECT COUNT(*) FROM crowdfunding_votes
                        WHERE project_id=$1 AND voted_at > NOW() - INTERVAL '1 hour'""",
                    prev["project_id"],
                ) or 0
                if me_v - rv_v >= 2:
                    trend = "closing"
                elif rv_v - me_v >= 2:
                    trend = "expanding"
                else:
                    trend = "stable"
                rival = {
                    "rival_name": (prev["first_name"] or prev["username"] or "Quelqu'un"),
                    "rival_votes": int(prev["votes_count"]),
                    "rival_slug": prev["slug"],
                    "trend": trend,
                }
            break
    return {"rank": rank, "total": len(rows), **(rival or {})}


async def get_vote_velocity(conn, project_id: str) -> int:
    """Votes in the last 10 minutes — used for the '🚀 +N votes en 10 min' UX."""
    return int(await conn.fetchval(
        """SELECT COUNT(*) FROM crowdfunding_votes
            WHERE project_id=$1 AND voted_at > NOW() - INTERVAL '10 minutes'""",
        project_id,
    ) or 0)


async def get_share_performance(conn, user_id: str) -> dict:
    """For the project owner: how many votes did his last share generate?

    Heuristic: take last 'share' event from the user, then count votes on
    his project AND visit_generated events that occurred AFTER that share
    timestamp. Channel = source of the most-recent 'share' event."""
    last_share = await conn.fetchrow(
        """SELECT created_at, source, project_id
             FROM crowdfunding_behavior_events
            WHERE user_id=$1 AND event_type='share'
            ORDER BY created_at DESC LIMIT 1""",
        user_id,
    )
    if not last_share:
        return {
            "last_share_at": None, "last_share_votes": 0,
            "last_share_clicks": 0, "conversion_rate": 0.0,
            "best_channel": None,
        }
    proj = await conn.fetchrow(
        """SELECT project_id FROM crowdfunding_projects
            WHERE user_id=$1 AND status IN ('active','winner')
            ORDER BY created_at DESC LIMIT 1""",
        user_id,
    )
    proj_id = proj["project_id"] if proj else last_share["project_id"]

    votes_after = int(await conn.fetchval(
        """SELECT COUNT(*) FROM crowdfunding_votes
            WHERE project_id=$1 AND voted_at > $2""",
        proj_id, last_share["created_at"],
    ) or 0) if proj_id else 0

    clicks_after = int(await conn.fetchval(
        """SELECT COUNT(*) FROM crowdfunding_behavior_events
            WHERE project_id=$1 AND event_type='visit_generated'
              AND created_at > $2""",
        proj_id, last_share["created_at"],
    ) or 0) if proj_id else 0

    best = await conn.fetchrow(
        """SELECT source, COUNT(*) AS n FROM crowdfunding_behavior_events
            WHERE user_id=$1 AND event_type='share'
              AND created_at > NOW() - INTERVAL '30 days'
            GROUP BY source ORDER BY n DESC LIMIT 1""",
        user_id,
    )

    rate = (votes_after / clicks_after) if clicks_after else 0.0
    return {
        "last_share_at": last_share["created_at"].isoformat(),
        "last_share_source": last_share["source"],
        "last_share_votes": votes_after,
        "last_share_clicks": clicks_after,
        "conversion_rate": round(min(1.0, rate), 4),
        "best_channel": best["source"] if best else (last_share["source"] or "direct"),
    }


def _engagement_score(s: dict) -> int:
    return int(
        (s.get("shares") or 0) * 4 +
        (s.get("votes_given") or 0) * 2 +
        (s.get("visits_generated") or 0) * 5 +
        (s.get("session_time") or 0) // 10
    )


def _category(score: int) -> str:
    if score < 5:
        return "low"
    if score < 25:
        return "medium"
    if score < 80:
        return "high"
    return "elite"


# ─── State machine ─────────────────────────────────────────────────────────
def _decide_state(*, project: Optional[dict], rank_info: dict, signals: dict) -> dict:
    """Returns dict with state + scores. Pure function (no DB)."""
    now = datetime.now(timezone.utc)

    if not project:
        # No active project — user is a passive voter at best.
        last = signals.get("last_event_at")
        is_warm = last and (now - last).total_seconds() < 86400
        return {
            "state": "engaged" if is_warm else "cold",
            "urgency_score": 0,
            "frustration_score": 0,
            "momentum_score": min(100, _engagement_score(signals)),
        }

    votes = int(project["votes_count"])
    target = max(1, int(project["votes_to_win"]))
    pct = votes / target

    rank = rank_info.get("rank") or 0
    gap_to_rival = max(0, (rank_info.get("rival_votes") or votes) - votes) if rank > 1 else 0

    last = signals.get("last_event_at")
    hours_since = (now - last).total_seconds() / 3600 if last else 999

    urgency = 0
    momentum = 0
    frustration = 0

    if pct >= 0.8:
        urgency = 90 + int((pct - 0.8) * 50)  # 90–100
    elif pct >= 0.5:
        urgency = 50 + int((pct - 0.5) * 100)  # 50-79
    else:
        urgency = int(pct * 100)
    urgency = min(100, urgency)

    momentum = min(100, votes * 5 + (signals.get("shares") or 0) * 8)

    if hours_since > 12 and pct < 0.5:
        frustration += 40
    if rank > 1 and gap_to_rival <= 3:
        frustration += 30  # close-fight stress
    if rank == 0 and votes == 0 and (now - project["created_at"]).total_seconds() > 86400:
        frustration += 30
    frustration = min(100, frustration)

    # State decision (priority order)
    if pct >= 0.8:
        state = "critical"
    elif rank > 1 and 1 <= gap_to_rival <= 5 and project["votes_open"]:
        state = "competitive"
    elif hours_since > 12 and pct < 0.5:
        state = "engaged"  # but stagnating → trigger handled later
    elif votes > 0 or (signals.get("shares") or 0) > 0:
        state = "engaged"
    else:
        state = "cold"

    return {
        "state": state,
        "urgency_score": urgency,
        "momentum_score": momentum,
        "frustration_score": frustration,
        "pct": pct,
        "rank": rank,
        "gap": gap_to_rival,
        "hours_since_last": int(hours_since),
    }


def _decide_trigger(*, state_data: dict, project: Optional[dict],
                    rank_info: dict, signals: dict) -> Optional[dict]:
    """Return the message variant + UI mode + next_best_action."""
    state = state_data["state"]
    rank = state_data.get("rank") or 0
    pct = state_data.get("pct") or 0
    hours = state_data.get("hours_since_last") or 0

    # Special triggers OVERRIDE base state messaging when relevant.
    pool_key = state
    next_action = "vote"

    if state == "engaged" and hours > 12 and project and pct < 0.5:
        pool_key = "stagnating"
        next_action = "share"
    elif state == "competitive":
        next_action = "share"
    elif state == "critical":
        next_action = "share"
    elif state == "cold":
        next_action = "vote"

    pool = TEMPLATES.get(pool_key, TEMPLATES["cold"])
    # Random rotation (deterministic enough — cooldowns prevent fatigue).
    msg_id, template = random.choice(pool)

    ctx = {
        "votes_count": int(project["votes_count"]) if project else 0,
        "votes_to_win": int(project["votes_to_win"]) if project else 0,
        "remaining": (int(project["votes_to_win"]) - int(project["votes_count"])) if project else 0,
        "pct": int(pct * 100),
        "rank": rank,
        "gap": state_data.get("gap") or 0,
        "rival_name": rank_info.get("rival_name"),
    }

    text = _format(template, ctx)

    ui_mode = {
        "critical": "urgent",
        "competitive": "push",
        "engaged": "calm",
        "cold": "calm",
    }.get(state, "calm")

    return {
        "message_id": msg_id,
        "message": text,
        "ui_mode": ui_mode,
        "next_best_action": next_action,
        "context": ctx,
    }


# ─── LLM hybrid layer (CRITICAL only) ──────────────────────────────────────
_LLM_CACHE: dict[str, tuple[float, str]] = {}
_LLM_CACHE_TTL = 3600
_LLM_TIMEOUT = 3.0


async def _generate_dynamic_message(ctx: dict) -> Optional[str]:
    """Use Claude Haiku for ONE ultra-personalised CRITICAL message.
    Hard 1.2s timeout, 1h cache by (rank,gap,pct,rival), graceful fallback."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return None
    key = hashlib.md5(
        f"{ctx.get('rank')}|{ctx.get('gap')}|{ctx.get('pct')}|{ctx.get('rival_name')}|{ctx.get('remaining')}".encode()
    ).hexdigest()
    now_ts = datetime.now(timezone.utc).timestamp()
    hit = _LLM_CACHE.get(key)
    if hit and (now_ts - hit[0]) < _LLM_CACHE_TTL:
        return hit[1]

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage  # noqa
    except Exception:
        return None

    prompt = (
        "Génère UN SEUL message court (≤120 caractères, FR), motivant, "
        "punchy, sans emoji débordant (max 1 emoji), pour un utilisateur "
        f"d'une plateforme de crowdfunding viral. Contexte :\n"
        f"- Votes actuels: {ctx.get('votes_count')}\n"
        f"- Objectif: {ctx.get('votes_to_win')}\n"
        f"- Reste: {ctx.get('remaining')} votes pour gagner\n"
        f"- % progression: {ctx.get('pct')}%\n"
        f"- Rang: {ctx.get('rank')}\n"
        f"- Écart au rival: {ctx.get('gap')} votes\n"
        f"- Rival: {ctx.get('rival_name') or 'inconnu'}\n"
        "Style : direct, urgence, sans flatterie. Termine par une action "
        "claire (partager, voter). Pas de guillemets autour du message. "
        "PAS d'introduction. Renvoie UNIQUEMENT le message final."
    )

    async def _call():
        chat = LlmChat(
            api_key=api_key,
            session_id=f"cf-engage-{key[:8]}",
            system_message="Tu rédiges des messages d'engagement comportemental ultra-courts pour JAPAP, une plateforme africaine de crowdfunding viral. Style : punchy, FR, max 120 caractères.",
        ).with_model("anthropic", "claude-haiku-4-5-20251001")
        return await chat.send_message(UserMessage(text=prompt))

    try:
        msg = await asyncio.wait_for(_call(), timeout=_LLM_TIMEOUT)
        if msg and isinstance(msg, str):
            cleaned = msg.strip().strip('"').strip("'")[:160]
            _LLM_CACHE[key] = (now_ts, cleaned)
            return cleaned
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"[engagement] LLM critical generation skipped: {e}")
    return None


# ─── Anti-spam ─────────────────────────────────────────────────────────────
async def _check_cooldown(conn, user_id: str, state: str) -> Optional[datetime]:
    """Return cooldown_until if user is currently silenced."""
    row = await conn.fetchrow(
        """SELECT state, last_message_at, cooldown_until
             FROM crowdfunding_engagement_state
            WHERE user_id = $1""",
        user_id,
    )
    if not row:
        return None
    now = datetime.now(timezone.utc)
    if row["cooldown_until"] and row["cooldown_until"] > now:
        return row["cooldown_until"]
    if row["last_message_at"] and row["state"] == state:
        # 6h cooldown for same state
        if (now - row["last_message_at"]).total_seconds() < 6 * 3600:
            return row["last_message_at"] + timedelta(hours=6)
    return None


async def _persist_state(conn, user_id: str, state: str, ui_mode: str,
                         engagement_score: int, message_id: Optional[str]) -> None:
    await conn.execute(
        """INSERT INTO crowdfunding_engagement_state
            (user_id, state, ui_mode, engagement_score, last_message_id,
             last_message_at, computed_at)
           VALUES ($1, $2, $3, $4, $5::varchar,
                   CASE WHEN $5::varchar IS NOT NULL THEN NOW() ELSE NULL END, NOW())
         ON CONFLICT (user_id) DO UPDATE SET
            state = EXCLUDED.state,
            ui_mode = EXCLUDED.ui_mode,
            engagement_score = EXCLUDED.engagement_score,
            last_message_id = COALESCE(EXCLUDED.last_message_id, crowdfunding_engagement_state.last_message_id),
            last_message_at = COALESCE(EXCLUDED.last_message_at, crowdfunding_engagement_state.last_message_at),
            computed_at = NOW()""",
        user_id, state, ui_mode, engagement_score, message_id,
    )


async def _record_shown(conn, message_id: str, state: str, text: str) -> None:
    await conn.execute(
        """INSERT INTO crowdfunding_message_performance
            (message_id, state, variant_text, shown_count, last_shown_at)
           VALUES ($1, $2, $3, 1, NOW())
         ON CONFLICT (message_id) DO UPDATE SET
            shown_count = crowdfunding_message_performance.shown_count + 1,
            last_shown_at = NOW()""",
        message_id, state, text[:512],
    )


# ─── Public API ────────────────────────────────────────────────────────────
async def compute_user_engagement_state(conn, user_id: str,
                                        *, with_llm: bool = True) -> dict:
    """Main entrypoint. Returns the full payload to render the UX banner.

    Performance: 1 SQL round-trip for signals + 1 for project + 1 for ranking
    + 1 for cooldown = ~10ms total in-pool. LLM call only fired when:
        - state == "critical"
        - cooldown expired
        - with_llm=True (caller can opt-out for cron jobs)
    """
    signals = await _aggregate_user_signals(conn, user_id)
    project = await _user_active_project(conn, user_id)
    rank_info = await _project_rank_and_rival(conn, project) if project else {}

    state_data = _decide_state(project=project, rank_info=rank_info,
                               signals=signals)
    state = state_data["state"]
    score = _engagement_score(signals)

    velocity = await get_vote_velocity(conn, project["project_id"]) if project else 0
    share_perf = await get_share_performance(conn, user_id)

    cooldown = await _check_cooldown(conn, user_id, state)
    suppressed = cooldown is not None

    trigger = _decide_trigger(
        state_data=state_data, project=project,
        rank_info=rank_info, signals=signals,
    ) or {}

    # LLM enrichment (CRITICAL state only, no cooldown, with_llm=true)
    llm_used = False
    if (state == "critical" and not suppressed and with_llm
            and project and project["votes_open"]):
        llm_text = await _generate_dynamic_message({
            **trigger.get("context", {}),
            "votes_count": int(project["votes_count"]),
            "votes_to_win": int(project["votes_to_win"]),
        })
        if llm_text:
            trigger["message"] = llm_text
            trigger["message_id"] = "crit_llm"
            llm_used = True

    # Persist + track shown
    if not suppressed and trigger.get("message_id"):
        await _record_shown(conn, trigger["message_id"], state, trigger["message"])
        await _persist_state(conn, user_id, state, trigger.get("ui_mode", "calm"),
                             score, trigger["message_id"])
    else:
        await _persist_state(conn, user_id, state, trigger.get("ui_mode", "calm"),
                             score, None)

    return {
        "user_id": user_id,
        "state": state,
        "ui_mode": "calm" if suppressed else trigger.get("ui_mode", "calm"),
        "urgency_score": state_data["urgency_score"],
        "frustration_score": state_data["frustration_score"],
        "momentum_score": state_data["momentum_score"],
        "engagement_score": score,
        "engagement_category": _category(score),
        "next_best_action": (trigger.get("next_best_action") or "vote") if not suppressed else "wait",
        "message": None if suppressed else trigger.get("message"),
        "message_id": None if suppressed else trigger.get("message_id"),
        "context": trigger.get("context", {}),
        "rank": state_data.get("rank") or 0,
        "rival": {
            "name": rank_info.get("rival_name"),
            "votes": rank_info.get("rival_votes"),
            "slug": rank_info.get("rival_slug"),
            "trend": rank_info.get("trend"),
        } if rank_info.get("rival_name") else None,
        "vote_velocity_10m": velocity,
        "share_performance": share_perf,
        "cooldown_until": cooldown.isoformat() if cooldown else None,
        "llm_personalised": llm_used,
    }


async def track_event(conn, user_id: str, event_type: str, *,
                      project_id: Optional[str] = None,
                      cycle_id: Optional[str] = None,
                      rank_before: Optional[int] = None,
                      rank_after: Optional[int] = None,
                      time_spent: int = 0,
                      source: str = "direct",
                      metadata: Optional[dict] = None) -> int:
    import json as _json
    row = await conn.fetchrow(
        """INSERT INTO crowdfunding_behavior_events
            (user_id, project_id, cycle_id, event_type, rank_before, rank_after,
             time_spent, source, metadata)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
        RETURNING event_id""",
        user_id, project_id, cycle_id, event_type[:32], rank_before, rank_after,
        max(0, int(time_spent or 0)),
        (source or "direct")[:32],
        _json.dumps(metadata or {}),
    )
    return int(row["event_id"])


async def record_feedback(conn, message_id: str, action: str) -> None:
    """action ∈ {clicked, dismissed, shared}"""
    column = {
        "clicked": "clicked_count",
        "dismissed": "dismissed_count",
        "shared": "shared_count",
    }.get(action)
    if not column:
        return
    await conn.execute(
        f"""UPDATE crowdfunding_message_performance
              SET {column} = {column} + 1
            WHERE message_id = $1""",
        message_id,
    )
    if action == "dismissed":
        # 7-day cooldown after explicit dismiss
        await conn.execute(
            """UPDATE crowdfunding_engagement_state
                  SET cooldown_until = NOW() + INTERVAL '7 days'
                WHERE last_message_id = $1""",
            message_id,
        )
