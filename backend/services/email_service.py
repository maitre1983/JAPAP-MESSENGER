"""
JAPAP — Email service (Resend-ready, logs when RESEND_API_KEY is not set)
========================================================================
Unified interface for transactional emails. When the Resend API key is present
in the environment, emails are sent via Resend's HTTP API. Otherwise, the email
content is LOGGED so the agent can verify codes during development.

iter152 — every transactional email is now persisted in `email_logs` with
event='sent' (or 'failed'), which:
  • lets the Resend webhook attribute deliveries/bounces back to the right
    recipient (was the cause of the silent migration-reset failures —
    webhook lookup had no `sent` row to join);
  • powers the new admin email-audit endpoint;
  • gives ops a way to detect API-key rotation regressions (>5 consecutive
    `failed` rows in 1 minute → dashboard red flag).
"""
import json
import logging
import os
import uuid

import httpx

logger = logging.getLogger(__name__)


async def _audit_email(to: str, subject: str, status: str,
                        provider_msg_id: str = "",
                        status_code: int = 0,
                        error: str = "",
                        kind: str = "transactional") -> None:
    """Persist an email send attempt into `email_logs` for audit + webhook
    join. Best-effort: failures here MUST NOT swallow the actual send
    result, so we wrap everything in a wide try/except.
    """
    try:
        from database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            log_id = f"log_{uuid.uuid4().hex[:14]}"
            meta = {"subject": subject, "kind": kind}
            if status_code:
                meta["status_code"] = status_code
            if error:
                meta["error"] = error[:500]
            await conn.execute(
                """INSERT INTO email_logs
                     (log_id, campaign_id, user_id, email, event,
                      provider_message_id, url, ip_address, user_agent,
                      metadata, created_at)
                   VALUES ($1, NULL, NULL, $2, $3, $4, NULL, NULL, NULL, $5, NOW())""",
                log_id, to.lower(), status,
                provider_msg_id or None, json.dumps(meta),
            )
    except Exception as e:
        logger.warning(f"[email-audit] insert failed for {to}: {e}")


def _logo_url() -> str:
    """Public URL of the JAPAP logo for use in email templates.
    Emails cannot reference `/japap-logo.jpg` directly — they need an absolute URL.
    """
    base = os.environ.get("FRONTEND_URL") or os.environ.get("REACT_APP_BACKEND_URL") or ""
    base = base.rstrip("/")
    return f"{base}/japap-logo.jpg" if base else "https://japap-refactor.preview.emergentagent.com/japap-logo.jpg"


def _otp_html(code: str, purpose: str) -> str:
    logo = _logo_url()
    return f"""
    <div style="font-family:Manrope,Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
      <div style="background:#fff;padding:24px;border-radius:16px 16px 0 0;text-align:center;border:1px solid #eee;border-bottom:none;">
        <img src="{logo}" alt="JAPAP" style="height:56px;width:auto;display:inline-block;" />
      </div>
      <div style="background:#fff;padding:32px 24px;border:1px solid #eee;border-top:none;border-radius:0 0 16px 16px;">
        <h2 style="color:#0F056B;margin-top:0;font-family:'Outfit',Arial,sans-serif;">{purpose}</h2>
        <p style="color:#555;font-size:14px;">Your verification code:</p>
        <div style="font-family:'Outfit',monospace;font-size:36px;font-weight:800;
                    letter-spacing:8px;color:#0F056B;background:#F4F4F5;
                    padding:16px;text-align:center;border-radius:12px;margin:16px 0;">
          {code}
        </div>
        <p style="color:#888;font-size:12px;">Code expires in 10 minutes. If you didn't request this, ignore this email.</p>
      </div>
    </div>
    """


async def send_email(to: str, subject: str, html: str, text: str = "",
                     kind: str = "transactional") -> bool:
    """Send an email via Resend. Returns True on success, False on any failure.
    When RESEND_API_KEY is missing, LOGS the full email body so devs can read
    codes from backend logs during testing.

    iter152 — wraps `send_email_detailed()` for backward compatibility. New
    code wanting the structured outcome (status_code, message_id, error)
    should call `send_email_detailed()` directly.
    """
    res = await send_email_detailed(to, subject, html, text, kind=kind)
    return bool(res.get("ok"))


async def send_email_detailed(to: str, subject: str, html: str, text: str = "",
                              kind: str = "transactional") -> dict:
    """Send an email and return a structured result so callers (auth flows,
    admin actions) can surface the *real* delivery status to the user
    instead of always saying "Email sent".

    Returns:
        {
          "ok": bool,
          "status_code": int,            # HTTP status from Resend (0 on local-mock)
          "message_id": str,             # Resend email_id (empty on failure)
          "error": str,                  # human-readable reason on failure
          "to": str,
          "kind": str,
        }
    """
    api_key = os.environ.get("RESEND_API_KEY")
    sender = os.environ.get("RESEND_FROM_EMAIL", "JAPAP <onboarding@resend.dev>")

    if not api_key:
        logger.info(
            "[EMAIL-MOCK] To: %s | Subject: %s\n---HTML---\n%s\n---TEXT---\n%s",
            to, subject, html, text or ""
        )
        await _audit_email(to, subject, "sent",
                            provider_msg_id="mock", kind=kind)
        return {"ok": True, "status_code": 0, "message_id": "mock",
                "error": "", "to": to, "kind": kind}

    payload = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text

    status_code = 0
    msg_id = ""
    error = ""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            status_code = r.status_code
            if r.status_code in (200, 202):
                try:
                    msg_id = (r.json() or {}).get("id", "") or ""
                except Exception:
                    msg_id = ""
                await _audit_email(to, subject, "sent",
                                   provider_msg_id=msg_id,
                                   status_code=status_code, kind=kind)
                return {"ok": True, "status_code": status_code,
                        "message_id": msg_id, "error": "",
                        "to": to, "kind": kind}
            error = r.text[:300]
            logger.warning("Resend HTTP %s: %s", r.status_code, error)
    except Exception as e:
        error = str(e)[:300]
        logger.warning("Resend send failed: %s", e)
    await _audit_email(to, subject, "failed",
                        status_code=status_code, error=error, kind=kind)
    return {"ok": False, "status_code": status_code, "message_id": "",
            "error": error or "Send failed", "to": to, "kind": kind}


async def send_otp_email(to: str, code: str, purpose: str = "Verify your JAPAP account") -> bool:
    return await send_email(
        to=to,
        subject=f"{purpose} — Code {code}",
        html=_otp_html(code, purpose),
        text=f"Your JAPAP verification code is: {code}\nExpires in 10 minutes.",
    )


async def send_password_reset_email(to: str, reset_url: str) -> bool:
    """Backward-compat wrapper. New code should call
    `send_password_reset_email_detailed()` to get the structured outcome."""
    res = await send_password_reset_email_detailed(to, reset_url)
    return bool(res.get("ok"))


async def send_password_reset_email_detailed(to: str, reset_url: str) -> dict:
    """iter152 — returns the full delivery dict so the auth flow can
    surface real status to the user instead of a blanket success."""
    logo = _logo_url()
    html = f"""
    <div style="font-family:Manrope,Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
      <div style="background:#fff;padding:24px;border-radius:16px 16px 0 0;text-align:center;border:1px solid #eee;border-bottom:none;">
        <img src="{logo}" alt="JAPAP" style="height:56px;width:auto;display:inline-block;" />
      </div>
      <div style="background:#fff;padding:32px 24px;border:1px solid #eee;border-top:none;border-radius:0 0 16px 16px;">
        <h2 style="color:#0F056B;margin-top:0;font-family:'Outfit',Arial,sans-serif;">Réinitialise ton mot de passe</h2>
        <p style="color:#555;font-size:14px;">Clique sur le bouton ci-dessous pour choisir un nouveau mot de passe. Le lien expire dans 1 heure.</p>
        <div style="text-align:center;margin:24px 0;">
          <a href="{reset_url}" style="background:#0F056B;color:#fff;padding:14px 28px;
             border-radius:12px;text-decoration:none;font-weight:600;font-family:'Outfit',Arial,sans-serif;">
            Réinitialiser le mot de passe
          </a>
        </div>
        <p style="color:#888;font-size:12px;">Si tu n'as pas demandé cela, ignore ce mail — ton mot de passe ne sera pas changé.</p>
        <p style="color:#888;font-size:11px;word-break:break-all;">{reset_url}</p>
      </div>
    </div>
    """
    return await send_email_detailed(
        to=to,
        subject="Réinitialise ton mot de passe JAPAP",
        html=html,
        text=f"Réinitialise ton mot de passe JAPAP : {reset_url}\nLe lien expire dans 1 heure.",
        kind="password_reset",
    )
