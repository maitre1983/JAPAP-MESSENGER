"""
JAPAP — Public URL audit worker (iter168.2)
============================================
Weekly autonomous monitor. Runs every Monday 07:00 UTC (after the
scholarship digest). When `run_audit()` returns `status='warning'`,
emails ALL superadmins with the full report.

Idempotency:
  • Table `public_url_audit_alerts` (week_key UNIQUE) — one alert per
    ISO week even if the worker restarts.
  • If `flagged_count == 0` and `code findings == 0`, no email is sent.

Test harness:
  • `POST /api/admin/public-url-audit/send-alert?force=true` (admin) —
    runs the audit + sends regardless of idempotency / status.
"""
import asyncio
import datetime
import logging
import os
import uuid

logger = logging.getLogger(__name__)

ALERT_HOUR_UTC = int(os.environ.get("PUBLIC_URL_AUDIT_HOUR_UTC", "7"))
ALERT_WEEKDAY = 0  # Monday
WORKER_ID = f"pua_{uuid.uuid4().hex[:8]}"

_stop_flag = asyncio.Event()
_task: "asyncio.Task | None" = None
_last_run_week = ""


def _current_week_key() -> str:
    today = datetime.datetime.now(datetime.timezone.utc).date()
    iso = today.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _is_dispatch_window() -> bool:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.weekday() == ALERT_WEEKDAY and now.hour >= ALERT_HOUR_UTC


def _render_alert_html(report: dict) -> tuple[str, str]:
    """Build the alert email HTML + plaintext fallback."""
    flagged = report["email_logs"]["flagged_count"]
    code_count = report["code"]["active_legacy_references"]
    cfg = report["config"]
    samples = report["email_logs"]["samples"][:10]
    findings = report["code"]["findings"][:10]
    week_key = _current_week_key()

    samples_html = "".join(
        f"""<tr style="border-top:1px solid #e5e7eb;">
              <td style="padding:8px;font-family:monospace;font-size:12px;">{s.get('event','')}</td>
              <td style="padding:8px;font-family:monospace;font-size:12px;color:#dc2626;">{s.get('host','')}</td>
              <td style="padding:8px;font-size:12px;">{s.get('email','')}</td>
              <td style="padding:8px;font-family:monospace;font-size:11px;color:#374151;word-break:break-all;">{(s.get('url') or '')[:80]}</td>
              <td style="padding:8px;font-size:11px;color:#6b7280;white-space:nowrap;">{(s.get('created_at') or '')[:10]}</td>
            </tr>"""
        for s in samples
    ) or '<tr><td colspan="5" style="padding:8px;color:#10b981;font-size:12px;">Aucun email flaggé.</td></tr>'

    code_html = "".join(
        f"""<tr style="border-top:1px solid #e5e7eb;">
              <td style="padding:8px;font-family:monospace;font-size:11px;color:#374151;">{f.get('file','')}:{f.get('line','')}</td>
              <td style="padding:8px;font-family:monospace;font-size:11px;color:#111;word-break:break-all;">{f.get('content','')[:120]}</td>
            </tr>"""
        for f in findings
    ) or '<tr><td colspan="2" style="padding:8px;color:#10b981;font-size:12px;">Code source propre.</td></tr>'

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px 12px;background:#f9fafb;font-family:Arial,sans-serif;">
  <table width="100%" cellspacing="0" cellpadding="0" style="max-width:760px;margin:auto;background:#fff;border-radius:12px;padding:24px;">
    <tr><td>
      <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:14px 16px;margin-bottom:18px;">
        <p style="margin:0;color:#991b1b;font-weight:700;font-size:15px;">⚠ Audit URL — Anomalie détectée (semaine {week_key})</p>
        <p style="margin:6px 0 0;color:#7f1d1d;font-size:13px;">
          {flagged} email(s) cliqué(s) sur un domaine hors-canon · {code_count} référence(s) active(s) en code source.
        </p>
      </div>

      <h2 style="margin:0 0 6px;font-size:16px;color:#111;">Configuration actuelle</h2>
      <table cellspacing="0" cellpadding="6" style="width:100%;font-size:13px;border-collapse:collapse;margin-bottom:18px;">
        <tr><td style="color:#6b7280;width:200px;">PUBLIC_APP_URL</td><td style="font-family:monospace;color:{'#10b981' if cfg.get('PUBLIC_APP_URL') else '#dc2626'};">{cfg.get('PUBLIC_APP_URL') or '(unset — fallback en cours)'}</td></tr>
        <tr><td style="color:#6b7280;">FRONTEND_URL</td><td style="font-family:monospace;">{cfg.get('FRONTEND_URL') or '(unset)'}</td></tr>
        <tr><td style="color:#6b7280;">public_base_url() résolu</td><td style="font-family:monospace;font-weight:700;">{cfg.get('public_base_url')}</td></tr>
        <tr><td style="color:#6b7280;">Production résolue ?</td><td style="font-weight:700;color:{'#10b981' if cfg.get('is_production_resolved') else '#dc2626'};">{'Oui' if cfg.get('is_production_resolved') else 'NON — env preview ou mal configuré'}</td></tr>
      </table>

      <h2 style="margin:0 0 6px;font-size:16px;color:#111;">Click events Resend (30 derniers jours)</h2>
      <table cellspacing="0" cellpadding="0" style="width:100%;border:1px solid #e5e7eb;border-radius:8px;border-collapse:collapse;margin-bottom:18px;">
        <thead><tr style="background:#f3f4f6;font-size:11px;color:#374151;text-align:left;">
          <th style="padding:8px;">Event</th><th style="padding:8px;">Host fautif</th>
          <th style="padding:8px;">Email destinataire</th><th style="padding:8px;">URL cliquée</th><th style="padding:8px;">Date</th>
        </tr></thead>
        <tbody>{samples_html}</tbody>
      </table>

      <h2 style="margin:0 0 6px;font-size:16px;color:#111;">Code source — refs actives</h2>
      <table cellspacing="0" cellpadding="0" style="width:100%;border:1px solid #e5e7eb;border-radius:8px;border-collapse:collapse;">
        <thead><tr style="background:#f3f4f6;font-size:11px;color:#374151;text-align:left;">
          <th style="padding:8px;">Fichier:Ligne</th><th style="padding:8px;">Contenu</th>
        </tr></thead>
        <tbody>{code_html}</tbody>
      </table>

      <p style="margin:18px 0 0;color:#6b7280;font-size:12px;">
        Audit JAPAP automatique — chaque lundi à {ALERT_HOUR_UTC}h UTC. Endpoint manuel :
        <code>GET /api/admin/public-url-audit?days=30</code>
      </p>
    </td></tr>
  </table>
</body></html>"""

    text = (
        f"⚠ Audit URL JAPAP — Anomalie détectée (semaine {week_key})\n\n"
        f"Email cliqués sur domaine hors-canon : {flagged}\n"
        f"Refs actives en code source : {code_count}\n\n"
        f"PUBLIC_APP_URL = {cfg.get('PUBLIC_APP_URL') or '(unset)'}\n"
        f"public_base_url résolu = {cfg.get('public_base_url')}\n"
        f"Production résolue = {cfg.get('is_production_resolved')}\n\n"
        f"Voir le rapport complet via GET /api/admin/public-url-audit"
    )
    return html, text


async def _ensure_alerts_table(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS public_url_audit_alerts (
            id SERIAL PRIMARY KEY,
            week_key VARCHAR(20) UNIQUE NOT NULL,
            flagged_count INT NOT NULL DEFAULT 0,
            code_count INT NOT NULL DEFAULT 0,
            superadmin_count INT NOT NULL DEFAULT 0,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


async def run_alert_pass(force: bool = False) -> dict:
    """Run the audit and send an alert email to ALL superadmins if
    `status='warning'`.

    Args:
        force: bypass idempotency lock AND send even when status='clean'.
               Used by the manual admin endpoint for testing.

    Returns dict with stats {week_key, status, flagged_count, code_count,
                              superadmins, alerts_sent, skipped_reason}.
    """
    from database import get_pool
    from services.email_service import send_email
    from services.public_url_audit_service import run_audit

    week_key = _current_week_key()
    report = await run_audit(days=30, limit=50)
    flagged = report["email_logs"]["flagged_count"]
    code_count = report["code"]["active_legacy_references"]
    status = report["status"]
    stats = {
        "week_key": week_key,
        "status": status,
        "flagged_count": flagged,
        "code_count": code_count,
        "superadmins": 0,
        "alerts_sent": 0,
        "skipped_reason": None,
    }

    if status == "clean" and not force:
        stats["skipped_reason"] = "status_clean"
        logger.info(f"[public-url-audit] {week_key} — clean, no alert sent")
        return stats

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_alerts_table(conn)
        if not force:
            existing = await conn.fetchval(
                "SELECT 1 FROM public_url_audit_alerts WHERE week_key = $1",
                week_key)
            if existing:
                stats["skipped_reason"] = "already_sent_this_week"
                logger.info(f"[public-url-audit] {week_key} — already alerted")
                return stats

        # Recipient list: all superadmins with email.
        recipients = await conn.fetch(
            """SELECT email, first_name FROM users
               WHERE role IN ('superadmin', 'admin')
                 AND email IS NOT NULL AND email <> ''
                 AND email_verified = TRUE"""
        )
        # Prioritise superadmin to keep the alert focused; fallback to admin.
        sa = [r for r in recipients if (r.get("role") or "") == "superadmin"]
        recipient_list = sa or list(recipients)
        stats["superadmins"] = len(recipient_list)

        if not recipient_list:
            stats["skipped_reason"] = "no_superadmin_with_verified_email"
            logger.warning(f"[public-url-audit] {week_key} — no recipient")
            return stats

        html, text = _render_alert_html(report)
        subject = f"⚠ JAPAP — Audit URL : {flagged} email(s) hors-canon ({week_key})"
        for r in recipient_list:
            try:
                ok = await send_email(r["email"], subject, html, text)
                if ok:
                    stats["alerts_sent"] += 1
            except Exception as e:
                logger.warning(f"alert send to {r['email']} failed: {e}")

        # Mark this week as alerted (idempotency lock).
        await conn.execute(
            """INSERT INTO public_url_audit_alerts
                 (week_key, flagged_count, code_count, superadmin_count)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (week_key) DO UPDATE
                 SET flagged_count = EXCLUDED.flagged_count,
                     code_count = EXCLUDED.code_count,
                     superadmin_count = EXCLUDED.superadmin_count,
                     sent_at = NOW()""",
            week_key, flagged, code_count, stats["alerts_sent"])

    return stats


async def _maybe_run():
    global _last_run_week
    week_key = _current_week_key()
    if _last_run_week == week_key:
        return
    if not _is_dispatch_window():
        return
    _last_run_week = week_key
    await run_alert_pass(force=False)


async def _loop():
    logger.info(f"[PublicUrlAudit {WORKER_ID}] loop started "
                f"(poll 600s, dispatch every Mon {ALERT_HOUR_UTC}h UTC)")
    while not _stop_flag.is_set():
        try:
            await _maybe_run()
        except Exception as e:
            logger.warning(f"[public-url-audit] tick failed: {e}")
        try:
            await asyncio.wait_for(_stop_flag.wait(), timeout=600)
        except asyncio.TimeoutError:
            pass
    logger.info(f"[PublicUrlAudit {WORKER_ID}] loop stopped")


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
