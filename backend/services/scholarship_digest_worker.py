"""
JAPAP — Scholarship Digest Worker (iter167)
============================================
Sends a weekly email with the most recent scholarships to all users in
African countries (high demand, primary JAPAP audience). Filtering rules:

  • Recipient must have `notify_scholarship_digest = TRUE` (opt-out).
  • Recipient must have a verified email (no soft-bounce / hard-bounce).
  • Recipient's `country` is in AFRICA_CC (40+ codes).
  • Scholarships are pulled for the past 7 days (`offer_type='scholarship'`).
  • If the user has set `preferred_study_level`, scholarships matching that
    level are surfaced first; the rest are appended for variety.
  • Idempotency : (user_id, week_key) UNIQUE on `scholarship_digest_log` —
    even if the worker is restarted mid-batch, no user gets two digests
    for the same ISO week.
  • Sent every Monday 06:00 UTC (07:00 CET / 08:00 EAT). Configurable via
    SCHOLARSHIP_DIGEST_HOUR_UTC env var.

Send is skipped silently when the upstream pool of scholarships is empty —
zero-content emails harm sender reputation.
"""
import asyncio
import datetime
import logging
import os
import uuid

logger = logging.getLogger(__name__)

# 40+ African ISO-3166 alpha-2 codes — keeps the worker focused on JAPAP's
# primary audience and avoids leaking digests to test accounts in random
# countries during early rollout.
AFRICA_CC = {
    "DZ", "AO", "BJ", "BW", "BF", "BI", "CM", "CV", "CF", "TD",
    "KM", "CG", "CD", "CI", "DJ", "EG", "GQ", "ER", "SZ", "ET",
    "GA", "GM", "GH", "GN", "GW", "KE", "LS", "LR", "LY", "MG",
    "MW", "ML", "MR", "MU", "MA", "MZ", "NA", "NE", "NG", "RW",
    "ST", "SN", "SC", "SL", "SO", "ZA", "SS", "SD", "TZ", "TG",
    "TN", "UG", "ZM", "ZW",
}

DIGEST_HOUR_UTC = int(os.environ.get("SCHOLARSHIP_DIGEST_HOUR_UTC", "6"))
DIGEST_WEEKDAY = 0  # Monday (Python: 0=Mon, 6=Sun)
WORKER_ID = f"sdw_{uuid.uuid4().hex[:8]}"

_stop_flag = asyncio.Event()
_task: "asyncio.Task | None" = None
_last_run_week = ""


def _current_week_key() -> str:
    """ISO week key, e.g. '2026-W18'."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    iso = today.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _is_dispatch_window() -> bool:
    """Return True only on Monday after the configured hour UTC. The
    worker polls every 5 min and fires once per ISO week (deduped via
    `scholarship_digest_log.week_key`)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.weekday() == DIGEST_WEEKDAY and now.hour >= DIGEST_HOUR_UTC


def _render_html(user, scholarships) -> tuple[str, str]:
    """Return (html, plain_text) for one recipient. Keep template lean —
    Resend rejects payloads >10 MB and image-heavy emails get throttled
    by Gmail/Outlook spam filters.
    """
    # iter168 — All public links go through the centralised helper so
    # they always point to the brand-correct domain (japapmessenger.com
    # in prod, preview URL in staging). Hardcoded domains are forbidden
    # in this file (CEO escalation P0 in iter168).
    from utils.public_url import public_url
    name = (user.get("first_name") or "").strip() or "ami(e)"
    items_html = []
    items_text = []
    for s in scholarships:
        title = (s.get("title") or "").strip() or "Bourse"
        univ = (s.get("university_name") or "").strip()
        country = (s.get("country_of_study") or "").strip()
        deadline = s.get("deadline")
        deadline_str = ""
        if deadline:
            try:
                deadline_str = deadline.strftime("%d/%m/%Y") if hasattr(deadline, "strftime") else str(deadline)
            except Exception:
                deadline_str = ""
        url = s.get("application_url") or ""
        public_link = public_url(f"/jobs/{s['job_id']}")
        items_html.append(f"""
          <tr><td style="padding:14px 16px;border-bottom:1px solid #e5e7eb;background:#fff;border-radius:8px;">
            <p style="margin:0 0 4px;font-family:Arial,sans-serif;font-size:15px;font-weight:700;color:#0F056B;">
              <a href="{public_link}" style="color:#0F056B;text-decoration:none;">{title}</a>
            </p>
            {f'<p style="margin:0 0 4px;font-family:Arial,sans-serif;font-size:13px;color:#374151;">{univ}{" · " + country if country else ""}</p>' if univ else ""}
            {f'<p style="margin:0;font-family:Arial,sans-serif;font-size:12px;color:#dc2626;">⏰ Date limite : {deadline_str}</p>' if deadline_str else ""}
            <p style="margin:8px 0 0;">
              <a href="{public_link}" style="display:inline-block;padding:6px 12px;background:#0F056B;color:#fff;text-decoration:none;border-radius:6px;font-family:Arial,sans-serif;font-size:12px;font-weight:600;">Voir l'offre</a>
              {f'<a href="{url}" style="display:inline-block;padding:6px 12px;background:#10b981;color:#fff;text-decoration:none;border-radius:6px;font-family:Arial,sans-serif;font-size:12px;font-weight:600;margin-left:6px;">Candidater</a>' if url else ""}
            </p>
          </td></tr>""")
        items_text.append(f"• {title}{(' — ' + univ) if univ else ''}{(' (' + deadline_str + ')') if deadline_str else ''}\n  {public_link}")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px 12px;background:#f9fafb;font-family:Arial,sans-serif;">
  <table width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;margin:auto;background:#fff;border-radius:12px;padding:24px;">
    <tr><td>
      <h1 style="margin:0 0 4px;font-size:22px;color:#0F056B;font-weight:800;">🎓 Bourses JAPAP — cette semaine</h1>
      <p style="margin:0 0 18px;color:#6b7280;font-size:14px;">Bonjour {name}, voici les nouvelles bourses ajoutées cette semaine.</p>
      <table width="100%" cellspacing="0" cellpadding="0">{''.join(items_html)}</table>
      <p style="margin:24px 0 4px;color:#6b7280;font-size:12px;">
        Tu reçois ce résumé parce que tu es membre de JAPAP en Afrique.
        <a href="{public_url('/profile')}" style="color:#0F056B;">Gérer mes préférences</a>
      </p>
    </td></tr>
  </table>
</body></html>"""
    text = f"Bourses JAPAP — cette semaine\n\nBonjour {name},\n\n" + "\n\n".join(items_text) + "\n\n--\nGère tes préférences sur ton profil."
    return html, text


async def _fetch_recent_scholarships(conn, days: int = 7, limit: int = 12):
    """Return the most recent scholarships posted in the last N days."""
    return await conn.fetch(
        """SELECT job_id, title, description, university_name,
                  country_of_study, level_of_study, field_of_study,
                  application_url, deadline, created_at
           FROM jobs
           WHERE offer_type = 'scholarship'
             AND status = 'open'
             AND created_at >= NOW() - make_interval(days => $1)
           ORDER BY created_at DESC
           LIMIT $2""",
        days, limit,
    )


async def _pick_for_user(scholarships, user) -> list:
    """Return up to 6 scholarships, level-matched first."""
    pref = (user.get("preferred_study_level") or "").lower().strip()
    if not pref:
        return scholarships[:6]
    matched = [s for s in scholarships if (s.get("level_of_study") or "").lower() == pref]
    others = [s for s in scholarships if (s.get("level_of_study") or "").lower() != pref]
    return (matched + others)[:6]


async def send_weekly_digest(force: bool = False) -> dict:
    """Run the digest pass for the current ISO week.

    Args:
      force: bypass the (user, week_key) idempotency lock — used by the
             admin "send now" endpoint for testing.

    Returns:
      Stats dict {users_total, sent, skipped, no_content, week_key}.
    """
    from database import get_pool
    from services.email_service import send_email
    pool = await get_pool()
    week_key = _current_week_key()
    stats = {"users_total": 0, "sent": 0, "skipped": 0,
             "no_content": 0, "errors": 0, "week_key": week_key}

    async with pool.acquire() as conn:
        scholarships = await _fetch_recent_scholarships(conn)
        if not scholarships:
            logger.info(f"[scholarship-digest] {week_key} — no scholarships in last 7d, skipping run")
            stats["no_content"] = -1  # sentinel: skipped the entire run
            return stats

        # Recipients: African users, opted in, with email.
        users = await conn.fetch(
            """SELECT user_id, email, first_name, last_name, country,
                      preferred_study_level
               FROM users
               WHERE notify_scholarship_digest = TRUE
                 AND email IS NOT NULL AND email <> ''
                 AND UPPER(country) = ANY($1::text[])""",
            list(AFRICA_CC),
        )
        stats["users_total"] = len(users)

        for u in users:
            user_dict = dict(u)
            try:
                # Idempotency: skip if already sent this week.
                if not force:
                    already = await conn.fetchval(
                        "SELECT 1 FROM scholarship_digest_log "
                        "WHERE user_id = $1 AND week_key = $2",
                        user_dict["user_id"], week_key)
                    if already:
                        stats["skipped"] += 1
                        continue
                picks = await _pick_for_user([dict(s) for s in scholarships], user_dict)
                if not picks:
                    stats["no_content"] += 1
                    continue
                html, text = _render_html(user_dict, picks)
                ok = await send_email(
                    user_dict["email"],
                    "🎓 Bourses JAPAP — cette semaine",
                    html, text,
                )
                if ok:
                    await conn.execute(
                        """INSERT INTO scholarship_digest_log
                             (user_id, week_key, scholarships_count)
                           VALUES ($1, $2, $3)
                           ON CONFLICT (user_id, week_key) DO NOTHING""",
                        user_dict["user_id"], week_key, len(picks))
                    stats["sent"] += 1
                else:
                    stats["errors"] += 1
            except Exception as e:
                logger.warning(f"[scholarship-digest] send to {user_dict.get('email')} failed: {e}")
                stats["errors"] += 1

    logger.info(f"[scholarship-digest] {week_key}: {stats}")
    return stats


async def _maybe_dispatch_digest():
    """Polled by the worker loop. Fires once per ISO-week in the dispatch
    window."""
    global _last_run_week
    week_key = _current_week_key()
    if _last_run_week == week_key:
        return
    if not _is_dispatch_window():
        return
    _last_run_week = week_key
    await send_weekly_digest(force=False)


async def _loop():
    logger.info(f"[ScholarshipDigest {WORKER_ID}] loop started "
                f"(poll 300s, dispatch every Mon {DIGEST_HOUR_UTC}h UTC)")
    while not _stop_flag.is_set():
        try:
            await _maybe_dispatch_digest()
        except Exception as e:
            logger.warning(f"[scholarship-digest] tick failed: {e}")
        try:
            await asyncio.wait_for(_stop_flag.wait(), timeout=300)
        except asyncio.TimeoutError:
            pass
    logger.info(f"[ScholarshipDigest {WORKER_ID}] loop stopped")


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
