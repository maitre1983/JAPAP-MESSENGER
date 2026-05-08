"""
Email renderer — safe variable substitution for campaign bodies.

Placeholders use the double-curly form `{{variable_name}}` (jinja-like but
without any expression support — just name lookup, escaped at render time).

All variable values are HTML-escaped before being injected into the HTML
body. The text body uses the raw (unescaped) value.

Supported variables (resolved from the user + wallet + referrals + tasks):
    • {{first_name}}       — u.first_name or fallback
    • {{last_name}}        — u.last_name
    • {{email}}             — u.email
    • {{country}}           — u.country or country_code
    • {{language}}          — u.language
    • {{plan_name}}         — 'Pro' | 'Free'
    • {{referral_count}}    — COUNT(referrals WHERE referrer=me)
    • {{wallet_balance}}    — wallets.balance_usd (fallback "0")
    • {{pending_tasks}}     — server-side count from tasks service
    • {{last_active_days}} — NOW - u.updated_at days (fallback 0)
    • {{connect_points}}    — u.connect_points
    • {{unsubscribe_url}}   — signed per-user one-click link
    • {{tracking_pixel}}    — invisible 1x1 img tag (added to HTML only)

The renderer NEVER calls the DB itself; callers provide a fully-resolved
`ctx` dict. A helper `build_context()` fetches the DB pieces in one roundtrip.
"""
from __future__ import annotations
import html as htmlmod
import logging
import os
import re
import hmac
import hashlib
import base64

logger = logging.getLogger(__name__)

# Allowed placeholder names (strict whitelist)
_ALLOWED_VARS = {
    "first_name", "last_name", "email", "country", "language", "plan_name",
    "referral_count", "wallet_balance", "pending_tasks", "last_active_days",
    "connect_points", "unsubscribe_url", "tracking_pixel", "cta_url", "cta_label",
    "campaign_id", "app_url",
}

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}", re.IGNORECASE)


def _get_unsub_secret() -> str:
    sec = os.environ.get("EMAIL_UNSUB_SECRET", "").strip()
    if not sec:
        # Auto-generate a per-process secret if missing so unsubscribe doesn't
        # 500 in dev. We log a warning so the admin adds it permanently.
        import secrets
        sec = secrets.token_urlsafe(32)
        os.environ["EMAIL_UNSUB_SECRET"] = sec
        logger.warning(
            "EMAIL_UNSUB_SECRET missing — generated a volatile fallback. "
            "Tokens will invalidate on restart. Set it in backend/.env."
        )
    return sec


def sign_unsub_token(user_id: str) -> str:
    """Return a URL-safe, non-expiring HMAC signature of the user_id."""
    secret = _get_unsub_secret().encode()
    mac = hmac.new(secret, user_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def verify_unsub_token(user_id: str, token: str) -> bool:
    expected = sign_unsub_token(user_id)
    return hmac.compare_digest(expected, token)


def _frontend_url() -> str:
    return (os.environ.get("FRONTEND_URL")
            or os.environ.get("REACT_APP_BACKEND_URL")
            or "").rstrip("/")


async def _resolve_logo_url() -> str:
    """Return the absolute, email-safe URL for the JAPAP header logo.

    Priority order (first non-empty wins):
      1. Admin setting `email_logo_url` — editable from the admin panel so
         ops can swap to a CDN without redeploying.
      2. Env var `EMAIL_LOGO_URL` — for non-DB overrides.
      3. Backend-served public endpoint `{backend}/api/assets/logo.png` —
         always available, correct Content-Type, cacheable 1y.
    """
    try:
        from services.settings_service import get_setting
        admin_val = await get_setting("email_logo_url", None)
        if admin_val and str(admin_val).strip():
            return str(admin_val).strip()
    except Exception:
        pass
    env_override = (os.environ.get("EMAIL_LOGO_URL") or "").strip()
    if env_override:
        return env_override
    base = _frontend_url()
    if not base:
        return ""
    return f"{base}/api/assets/logo.png"


def _homepage_url() -> str:
    return (os.environ.get("PUBLIC_HOMEPAGE_URL") or _frontend_url() or "").rstrip("/")


def build_unsubscribe_url(user_id: str) -> str:
    base = _frontend_url()
    token = sign_unsub_token(user_id)
    # Hits backend route, which then redirects / renders a confirmation page.
    return f"{base}/api/email/unsubscribe?u={user_id}&t={token}"


def build_tracking_pixel(campaign_id: str, user_id: str) -> str:
    base = _frontend_url()
    return (f'<img src="{base}/api/email/track/open?c={campaign_id}&u={user_id}" '
            f'width="1" height="1" alt="" '
            f'style="display:block;width:1px;height:1px;border:0;" />')


def build_click_url(campaign_id: str, user_id: str, url: str) -> str:
    base = _frontend_url()
    enc = base64.urlsafe_b64encode(url.encode()).rstrip(b"=").decode()
    return f"{base}/api/email/track/click?c={campaign_id}&u={user_id}&url={enc}"


def render(template_str: str, ctx: dict, html_safe: bool = True) -> str:
    """Substitute `{{var}}` in `template_str`. HTML-escapes values when
    `html_safe=True` (default for body_html and subject).

    Unknown placeholders are left untouched (silently) so accidental curly
    braces in copy don't break the render.
    """
    if not template_str:
        return ""

    def _sub(m):
        name = m.group(1).lower()
        if name not in _ALLOWED_VARS:
            return m.group(0)
        val = ctx.get(name)
        if val is None:
            val = ""
        s = str(val)
        return htmlmod.escape(s, quote=True) if html_safe else s

    return _PLACEHOLDER_RE.sub(_sub, template_str)


async def build_context(conn, user_row: dict, campaign_id: str | None = None,
                         extra_ctx: dict | None = None) -> dict:
    """Fetch derived values and return a fully-populated ctx dict ready for
    `render()`. Single roundtrip (1 SELECT + inline scalars).
    """
    uid = user_row.get("user_id")
    wallet_balance = "0"
    referral_count = 0
    pending_tasks = 0
    if uid:
        try:
            row = await conn.fetchrow(
                """
                SELECT
                  (SELECT balance_usd FROM wallets WHERE user_id = $1) AS wallet_balance_usd,
                  (SELECT COUNT(*)  FROM referrals WHERE referrer_user_id = $1) AS referral_count
                """,
                uid,
            )
            if row:
                wb = row["wallet_balance_usd"]
                if wb is not None:
                    wallet_balance = f"{float(wb):.2f}"
                referral_count = int(row["referral_count"] or 0)
        except Exception as e:
            logger.debug(f"build_context: aux fetch failed for {uid}: {e}")

    # last_active_days
    from datetime import datetime, timezone
    upd = user_row.get("updated_at")
    last_active_days = 0
    if upd:
        try:
            ts = upd if isinstance(upd, datetime) else datetime.fromisoformat(str(upd))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            last_active_days = max(0, int((datetime.now(timezone.utc) - ts).total_seconds() // 86400))
        except Exception:
            pass

    ctx = {
        "first_name": (user_row.get("first_name") or user_row.get("username") or "Utilisateur"),
        "last_name": user_row.get("last_name") or "",
        "email": user_row.get("email") or "",
        "country": user_row.get("country") or user_row.get("country_code") or "",
        "language": user_row.get("language") or "",
        "plan_name": "Pro" if user_row.get("is_pro") else "Free",
        "referral_count": referral_count,
        "wallet_balance": wallet_balance,
        "pending_tasks": pending_tasks,
        "last_active_days": last_active_days,
        "connect_points": int(user_row.get("connect_points") or 0),
        "unsubscribe_url": build_unsubscribe_url(uid) if uid else "",
        "campaign_id": campaign_id or "",
        "app_url": _frontend_url(),
        "logo_url": await _resolve_logo_url(),
        "homepage_url": _homepage_url(),
    }
    if campaign_id and uid:
        ctx["tracking_pixel"] = build_tracking_pixel(campaign_id, uid)
    else:
        ctx["tracking_pixel"] = ""
    if extra_ctx:
        ctx.update({k: v for k, v in extra_ctx.items() if k in _ALLOWED_VARS})
    return ctx


def wrap_html_for_delivery(body_html: str, ctx: dict,
                           cta_label: str = "", cta_url: str = "") -> str:
    """Take the user-authored body HTML and wrap it with standard JAPAP
    header, footer, unsubscribe link and tracking pixel.
    """
    # Resolve CTA url through click tracker if campaign_id is known
    tracked_cta = cta_url
    if cta_url and ctx.get("campaign_id"):
        tracked_cta = build_click_url(ctx["campaign_id"], _extract_uid(ctx), cta_url)

    unsub = ctx.get("unsubscribe_url", "")
    pixel = ctx.get("tracking_pixel", "")
    logo = ctx.get("logo_url", "") or ""
    home = ctx.get("homepage_url", "") or ""

    # Email-client-safe header with logo. Uses inline styles only (no <style>
    # block — many clients strip it). Works in Gmail (web + app), Outlook,
    # Apple Mail, Yahoo, ProtonMail.
    #   • Absolute HTTPS URL → no mixed-content blocking
    #   • width attribute + inline style → correct sizing on Outlook too
    #   • alt text → shown if image blocked
    #   • wrapped in <a> → clickable returns users to homepage
    #   • padding-bottom on wrapper → breathing room below logo
    if logo:
        logo_img = (
            f'<img src="{htmlmod.escape(logo, quote=True)}" alt="JAPAP" '
            f'width="140" height="auto" '
            f'style="display:block;margin:0 auto;max-width:140px;'
            f'height:auto;border:0;outline:none;text-decoration:none;" />'
        )
        if home:
            logo_block = (
                f'<a href="{htmlmod.escape(home, quote=True)}" target="_blank" '
                f'style="display:inline-block;text-decoration:none;border:0;outline:none;">'
                f'{logo_img}</a>'
            )
        else:
            logo_block = logo_img
    else:
        # Fallback: text wordmark if logo URL is unknown (never in prod, but
        # keeps emails from looking broken during local dev / misconfig).
        logo_block = (
            '<div style="font-family:\'Outfit\',Arial,sans-serif;font-weight:800;'
            'font-size:24px;color:#F7931A;letter-spacing:0.5px;">JAPAP</div>'
        )

    cta_block = ""
    if cta_label and tracked_cta:
        cta_block = f"""
        <div style="text-align:center;margin:28px 0 10px;">
          <a href="{htmlmod.escape(tracked_cta)}" target="_blank"
             style="display:inline-block;padding:14px 30px;background:#F7931A;color:#fff;
                    text-decoration:none;border-radius:999px;font-weight:700;font-family:'Outfit',Arial,sans-serif;">
            {htmlmod.escape(cta_label)}
          </a>
        </div>
        """

    return f"""\
<!doctype html><html><body style="margin:0;padding:0;background:#f4f4f5;font-family:Manrope,Arial,sans-serif;">
  <div style="max-width:560px;margin:0 auto;padding:24px 16px;">
    <div style="background:#fff;padding:28px 24px 24px;border-radius:16px 16px 0 0;text-align:center;border:1px solid #e4e4e7;border-bottom:none;">
      {logo_block}
    </div>
    <div style="background:#fff;padding:28px 24px;border:1px solid #e4e4e7;border-top:none;border-radius:0 0 16px 16px;color:#27272a;line-height:1.55;font-size:15px;">
      {body_html}
      {cta_block}
    </div>
    <p style="text-align:center;color:#71717a;font-size:11px;margin:18px 0 4px;">
      © JAPAP Messenger —
      <a href="{htmlmod.escape(unsub)}" style="color:#71717a;text-decoration:underline;">Se désabonner</a>
    </p>
    {pixel}
  </div>
</body></html>"""


def _extract_uid(ctx: dict) -> str:
    # Unsub URL has shape: ...?u=<uid>&t=... — pull uid back
    unsub = ctx.get("unsubscribe_url", "") or ""
    m = re.search(r"[?&]u=([^&]+)", unsub)
    return m.group(1) if m else ""
