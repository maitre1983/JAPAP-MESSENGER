"""
JAPAP — Crowdfunding Recruiter Reminder Worker (iter170, P2)
=============================================================
Daily cron that re-engages two cohorts of users in the *active*
Crowdfunding cycle:

  • visited_no_vote  — clicked an invite link (≥24 h ago) but never
    voted for ANY project of the cycle.
  • voted_no_share   — voted at least once (≥24 h ago) but has not
    yet generated a share-tracked visit (= they're not yet recruiters).

Each user receives at most ONE reminder PER (cycle, kind) thanks to the
UNIQUE constraint on `crowdfunding_recruit_reminders`. The cron skips
users whose email is missing or who opted out of marketing emails
(`notify_crowdfunding_reminders = FALSE`).

Cron : every day at REMINDER_HOUR_UTC (default 09) UTC.

Run window : a visit must be older than 24 h and newer than 7 d to
qualify (we don't want to spam the same person 30 days after a single
click).
"""
import asyncio
import datetime
import logging
import os
import uuid

logger = logging.getLogger(__name__)

REMINDER_HOUR_UTC = int(os.environ.get("CROWDFUNDING_REMINDER_HOUR_UTC", "9"))
WORKER_ID = f"cfr_{uuid.uuid4().hex[:8]}"
RUN_WINDOW_DAYS = 7      # ignore stale clicks/votes older than this
COOLDOWN_HOURS = 24      # skip events fresher than this

_stop_flag = asyncio.Event()
_task: "asyncio.Task | None" = None
_last_run_day = ""


async def _ensure_table(conn) -> None:
    """Idempotent DDL — created on first call so the worker is restart
    safe even if database.py migration is missing the table."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS crowdfunding_recruit_reminders (
            id BIGSERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            cycle_id VARCHAR(40) NOT NULL,
            kind VARCHAR(32) NOT NULL,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, cycle_id, kind)
        )""")


def _current_day_key() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _is_dispatch_window() -> bool:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.hour >= REMINDER_HOUR_UTC


async def _active_cycle(conn) -> dict | None:
    row = await conn.fetchrow(
        """SELECT cycle_id, cycle_number FROM crowdfunding_cycles
           WHERE status = 'active' ORDER BY started_at DESC LIMIT 1""")
    return dict(row) if row else None


def _email_html_visited(first_name: str, leaderboard_url: str,
                         project_url: str | None) -> tuple[str, str]:
    name = (first_name or "").strip() or "ami(e)"
    cta_secondary = (
        f'<p style="margin:8px 0 0;"><a href="{project_url}" '
        f'style="display:inline-block;padding:8px 16px;background:#0F056B;color:#fff;'
        f'text-decoration:none;border-radius:9999px;font-size:13px;font-weight:700;">'
        f'Voter maintenant</a></p>'
        if project_url else ""
    )
    html = f"""<!DOCTYPE html><html><body style="margin:0;padding:24px;background:#f9fafb;font-family:Arial,sans-serif;">
      <table width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:24px;">
        <tr><td>
          <h1 style="margin:0 0 6px;font-size:22px;color:#0F056B;">⏳ Tu as failli soutenir un projet…</h1>
          <p style="color:#374151;font-size:14px;line-height:1.6;">
            Bonjour {name}, tu as cliqué sur un projet JAPAP Crowdfunding
            mais ton vote n'est jamais arrivé. Un vote = 1 sec, et ça
            change la donne pour le créateur.
          </p>
          {cta_secondary}
          <p style="margin:16px 0 0;color:#374151;font-size:13px;">
            Tu peux aussi voir qui mène le classement :
            <a href="{leaderboard_url}" style="color:#0F056B;">Top recruteurs JAPAP</a>.
          </p>
          <p style="margin:24px 0 0;color:#9ca3af;font-size:11px;">
            Tu reçois ce rappel parce que tu as cliqué sur un lien JAPAP.
            Récompense recruteur = badges &amp; XP, jamais de cash.
          </p>
        </td></tr>
      </table>
    </body></html>"""
    text = (f"Tu as cliqué sur un projet JAPAP Crowdfunding mais tu n'as "
            f"pas voté. Un vote = 1 sec.\n"
            f"{('Voter : ' + project_url) if project_url else ''}\n"
            f"Top recruteurs : {leaderboard_url}")
    return html, text


def _email_html_voted(first_name: str, leaderboard_url: str) -> tuple[str, str]:
    name = (first_name or "").strip() or "ami(e)"
    html = f"""<!DOCTYPE html><html><body style="margin:0;padding:24px;background:#f9fafb;font-family:Arial,sans-serif;">
      <table width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:24px;">
        <tr><td>
          <h1 style="margin:0 0 6px;font-size:22px;color:#0F056B;">🚀 Et si tu devenais recruteur ?</h1>
          <p style="color:#374151;font-size:14px;line-height:1.6;">
            Bonjour {name}, tu as voté pour un projet JAPAP — merci ! Sais-tu
            que partager un projet avec tes amis te fait gagner des badges
            recruteur (Bronze, Silver, Gold, Platine) et de l'XP ?
          </p>
          <p style="margin:14px 0 0;">
            <a href="{leaderboard_url}" style="display:inline-block;padding:10px 18px;background:#e11d48;color:#fff;text-decoration:none;border-radius:9999px;font-size:13px;font-weight:700;">
              Voir le classement et commencer
            </a>
          </p>
          <p style="margin:18px 0 0;color:#374151;font-size:13px;">
            <b>Comment ?</b> Va sur n'importe quel projet → bouton "Partager"
            → ton lien personnel embarque ton ID. À chaque vote déclenché
            par ton lien, tu gagnes 1 recrue.
          </p>
          <p style="margin:24px 0 0;color:#9ca3af;font-size:11px;">
            Récompense recruteur = badges &amp; XP, jamais de cash.
          </p>
        </td></tr>
      </table>
    </body></html>"""
    text = (f"Merci pour ton vote ! Partage des projets pour devenir "
            f"recruteur Bronze, Silver, Gold ou Platine.\n"
            f"Classement : {leaderboard_url}")
    return html, text


async def _send_visited_no_vote(conn, cycle_id: str, public_url) -> dict:
    """Find users who clicked an invite ≥24 h ago but never voted in the
    active cycle, then dispatch a reminder."""
    rows = await conn.fetch(
        f"""SELECT DISTINCT v.visitor_user_id AS user_id,
                            MIN(v.project_slug) AS project_slug,
                            MAX(v.created_at) AS last_visit_at,
                            u.email, u.first_name
             FROM crowdfunding_invite_visits v
             JOIN users u ON u.user_id = v.visitor_user_id
             LEFT JOIN crowdfunding_recruit_reminders r
               ON r.user_id = v.visitor_user_id
              AND r.cycle_id = v.cycle_id
              AND r.kind = 'visited_no_vote'
             LEFT JOIN crowdfunding_votes vt
               ON vt.user_id = v.visitor_user_id
              AND vt.cycle_id = v.cycle_id
            WHERE v.cycle_id = $1
              AND v.visitor_user_id IS NOT NULL
              AND v.created_at < NOW() - make_interval(hours => {COOLDOWN_HOURS})
              AND v.created_at > NOW() - make_interval(days  => {RUN_WINDOW_DAYS})
              AND r.id IS NULL
              AND vt.id IS NULL
              AND u.email IS NOT NULL
              AND COALESCE(u.notify_crowdfunding_reminders, TRUE) = TRUE
            GROUP BY v.visitor_user_id, u.email, u.first_name""",
        cycle_id,
    )
    from services.email_service import send_email
    sent = 0
    errors = 0
    leaderboard_url = public_url("/crowdfunding/leaderboard")
    for r in rows:
        try:
            project_url = (public_url(f"/crowdfunding/p/{r['project_slug']}")
                           if r["project_slug"] else None)
            html, text = _email_html_visited(r["first_name"], leaderboard_url, project_url)
            ok = await send_email(
                r["email"],
                "⏳ JAPAP — Tu as failli voter pour un projet",
                html, text, kind="crowdfunding_remind",
            )
            if ok:
                await conn.execute(
                    """INSERT INTO crowdfunding_recruit_reminders
                         (user_id, cycle_id, kind) VALUES ($1, $2, 'visited_no_vote')
                       ON CONFLICT DO NOTHING""",
                    r["user_id"], cycle_id,
                )
                sent += 1
            else:
                errors += 1
        except Exception as e:
            logger.warning(f"[cf-remind] visited_no_vote send {r['email']} failed: {e}")
            errors += 1
    return {"sent": sent, "errors": errors, "candidates": len(rows)}


async def _send_voted_no_share(conn, cycle_id: str, public_url) -> dict:
    """Find users who voted ≥24 h ago in the cycle but haven't generated
    any share-tracked visit, then send the 'become recruiter' nudge."""
    rows = await conn.fetch(
        f"""SELECT DISTINCT vt.user_id,
                            u.email, u.first_name,
                            MAX(vt.voted_at) AS last_vote_at
             FROM crowdfunding_votes vt
             JOIN users u ON u.user_id = vt.user_id
             LEFT JOIN crowdfunding_recruit_reminders r
               ON r.user_id = vt.user_id
              AND r.cycle_id = vt.cycle_id
              AND r.kind = 'voted_no_share'
             LEFT JOIN crowdfunding_invite_visits iv
               ON iv.inviter_id = vt.user_id
              AND iv.cycle_id = vt.cycle_id
            WHERE vt.cycle_id = $1
              AND vt.voted_at < NOW() - make_interval(hours => {COOLDOWN_HOURS})
              AND vt.voted_at > NOW() - make_interval(days  => {RUN_WINDOW_DAYS})
              AND r.id IS NULL
              AND iv.id IS NULL
              AND u.email IS NOT NULL
              AND COALESCE(u.notify_crowdfunding_reminders, TRUE) = TRUE
            GROUP BY vt.user_id, u.email, u.first_name""",
        cycle_id,
    )
    from services.email_service import send_email
    sent = 0
    errors = 0
    leaderboard_url = public_url("/crowdfunding/leaderboard")
    for r in rows:
        try:
            html, text = _email_html_voted(r["first_name"], leaderboard_url)
            ok = await send_email(
                r["email"],
                "🚀 JAPAP — Deviens recruteur Crowdfunding",
                html, text, kind="crowdfunding_remind",
            )
            if ok:
                await conn.execute(
                    """INSERT INTO crowdfunding_recruit_reminders
                         (user_id, cycle_id, kind) VALUES ($1, $2, 'voted_no_share')
                       ON CONFLICT DO NOTHING""",
                    r["user_id"], cycle_id,
                )
                sent += 1
            else:
                errors += 1
        except Exception as e:
            logger.warning(f"[cf-remind] voted_no_share send {r['email']} failed: {e}")
            errors += 1
    return {"sent": sent, "errors": errors, "candidates": len(rows)}


async def run_reminder_pass(force: bool = False) -> dict:
    """Public entry point. Idempotent per (user_id, cycle_id, kind).

    `force` is currently a no-op — it does not bypass the per-user lock
    (CEO rule: never re-spam the same user). It only triggers an out-of-
    window run from the admin endpoint.
    """
    from database import get_pool
    from utils.public_url import public_url

    pool = await get_pool()
    out = {"day_key": _current_day_key(),
           "visited_no_vote": {"sent": 0, "errors": 0, "candidates": 0},
           "voted_no_share":  {"sent": 0, "errors": 0, "candidates": 0},
           "cycle_id": None, "skipped_reason": None}
    async with pool.acquire() as conn:
        await _ensure_table(conn)
        # Best-effort: ensure opt-out column exists (no-op if migrated).
        try:
            await conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "notify_crowdfunding_reminders BOOLEAN NOT NULL DEFAULT TRUE")
        except Exception:
            pass
        cycle = await _active_cycle(conn)
        if not cycle:
            out["skipped_reason"] = "no_active_cycle"
            return out
        out["cycle_id"] = cycle["cycle_id"]
        out["visited_no_vote"] = await _send_visited_no_vote(conn, cycle["cycle_id"], public_url)
        out["voted_no_share"]  = await _send_voted_no_share(conn, cycle["cycle_id"], public_url)
    logger.info(f"[cf-remind] {out}")
    return out


async def _maybe_dispatch():
    global _last_run_day
    day_key = _current_day_key()
    if _last_run_day == day_key:
        return
    if not _is_dispatch_window():
        return
    _last_run_day = day_key
    await run_reminder_pass(force=False)


async def _loop():
    logger.info(f"[CrowdfundingReminder {WORKER_ID}] loop started "
                f"(poll 600s, dispatch daily {REMINDER_HOUR_UTC}h UTC)")
    while not _stop_flag.is_set():
        try:
            await _maybe_dispatch()
        except Exception as e:
            logger.warning(f"[cf-remind] tick failed: {e}")
        try:
            await asyncio.wait_for(_stop_flag.wait(), timeout=600)
        except asyncio.TimeoutError:
            pass
    logger.info(f"[CrowdfundingReminder {WORKER_ID}] loop stopped")


def start_worker(app):
    @app.on_event("startup")
    async def _start():
        global _task
        if _task is None or _task.done():
            _task = asyncio.create_task(_loop())

    @app.on_event("shutdown")
    async def _stop():
        _stop_flag.set()
        if _task and not _task.done():
            try:
                await asyncio.wait_for(_task, timeout=2)
            except asyncio.TimeoutError:
                pass
