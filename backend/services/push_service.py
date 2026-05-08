"""
JAPAP — Web Push service (iter71, OneSignal REST API).

Design note
───────────
Iter70 shipped a self-hosted Web Push stack built on VAPID + pywebpush.
Iter71 fully replaces it with OneSignal so JAPAP has a single system for
both transactional pushes (tips, money, messages) AND marketing campaigns
— all manageable from the OneSignal dashboard.

What happened to VAPID / pywebpush / the `push_subscriptions` table?
  • `pywebpush` is no longer imported or called anywhere.
  • The `push_subscriptions` table is left in place but unused (no new
    rows are written). It can be dropped manually later; leaving it
    avoids a destructive migration on live DB.
  • The browser subscription is now managed entirely by the OneSignal SDK
    on the client. Our backend only speaks to OneSignal's REST API and
    targets users by our internal `user_id`, which the frontend passes to
    OneSignal as the *External ID* via `OneSignal.login(user_id)`.

Public API
──────────
  • send_push_to_user(user_id, payload)            — fan-out entry point
  • build_payload(title=..., body=..., url=...)    — shape helper
  • configured() / get_public_key()                — configuration status
  • ensure_table()                                 — legacy no-op shim
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

ONESIGNAL_BASE = "https://api.onesignal.com"


def _app_id() -> str:
    return os.environ.get("ONESIGNAL_APP_ID", "").strip()


def _rest_key() -> str:
    return os.environ.get("ONESIGNAL_REST_API_KEY", "").strip()


def configured() -> bool:
    return bool(_app_id() and _rest_key())


def get_public_key() -> str:
    """Back-compat shim (iter70 endpoint name). Returns the OneSignal App ID
    — the only "public key" a OneSignal client needs. Kept so the public
    endpoint name `/api/push/public-key` doesn't break old clients during
    a rolling deploy."""
    return _app_id()


async def ensure_table():
    """Legacy no-op — the old VAPID `push_subscriptions` table is no
    longer written to. OneSignal tracks subscriptions on its side.
    Kept as a symbol so server.py's startup path doesn't crash."""
    return None


def _build_body(
    *,
    title: str,
    body: str = "",
    url: str = "/feed",
    tag: str | None = None,
    type_: str = "message",
    extra: dict[str, Any] | None = None,
) -> dict:
    """Shape a OneSignal REST payload for a push. Targets a single user
    by External ID (our internal `user_id`). Deep-link data rides in
    `url` (OneSignal launches it on click) AND in `data` so our custom
    `notificationclick` handler in sw.js can also read it."""
    data: dict[str, Any] = {
        "type": type_,
        "url": url,
    }
    if tag:
        data["tag"] = tag
    if extra:
        data["extra"] = extra

    doc: dict[str, Any] = {
        "app_id": _app_id(),
        "target_channel": "push",
        "headings": {"en": title[:80], "fr": title[:80]},
        "contents": {"en": body[:240] or title[:240], "fr": body[:240] or title[:240]},
        "data": data,
        # Launches the URL in the PWA / the user's browser when clicked
        "web_url": url,
        "chrome_web_icon": "/pwa-icon-192.png",
        "chrome_web_badge": "/favicon-32.png",
    }
    if tag:
        doc["web_push_topic"] = tag  # browser coalesce key
    return doc


def build_payload(
    *,
    title: str,
    body: str = "",
    url: str = "/feed",
    tag: str | None = None,
    type_: str = "message",
    extra: dict[str, Any] | None = None,
) -> dict:
    """Back-compat helper kept from iter70 — some realtime.py call sites
    use it to build a payload dict BEFORE handing off. We still accept
    the same keyword args, but we now return a shape that matches what
    `send_push_to_user` expects (same top-level keys as iter70's VAPID
    payload: title / body / url / tag / type / extra)."""
    out: dict[str, Any] = {
        "title": title[:80],
        "body": body[:240],
        "url": url,
        "type": type_,
    }
    if tag:
        out["tag"] = tag
    if extra:
        out["extra"] = extra
    return out


async def send_push_to_user(user_id: str, payload: dict) -> dict:
    """
    Send a OneSignal push to a single user, targeted by External ID.

    Payload shape (same as iter70, so realtime.py keeps working unchanged):
        {
          "title": "Bob t'a envoyé 1000 XAF 💸",
          "body":  "Merci pour hier",
          "url":   "/chat/conv_abc",
          "tag":   "money:conv_abc",   (optional; used as web_push_topic)
          "type":  "money" | "tip" | "message" | ...
          "extra": {...}               (optional; forwarded to data.extra)
        }

    Safe no-op when OneSignal isn't configured or the HTTP client is
    missing — never raises, never blocks the caller.
    """
    if not user_id:
        return {"sent": 0, "skipped": "no_user_id"}

    if not configured():
        return {"sent": 0, "skipped": "onesignal_not_configured"}

    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("httpx not available — skipping OneSignal push")
        return {"sent": 0, "skipped": "httpx_not_installed"}

    doc = _build_body(
        title=payload.get("title", "JAPAP"),
        body=payload.get("body", ""),
        url=payload.get("url", "/feed"),
        tag=payload.get("tag"),
        type_=payload.get("type", "message"),
        extra=payload.get("extra"),
    )
    # Target a single user by External ID (the user_id we `login()` with
    # on the frontend). This hits every device that user is subscribed on.
    doc["include_aliases"] = {"external_id": [user_id]}

    headers = {
        "Authorization": f"Key {_rest_key()}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                f"{ONESIGNAL_BASE}/notifications",
                headers=headers,
                json=doc,
            )
        if r.status_code in (200, 201):
            data = r.json() if r.content else {}
            recipients = data.get("recipients") or 0
            return {"sent": int(recipients), "notification_id": data.get("id")}
        # Common OneSignal 400 "All included players are not subscribed"
        # just means the user hasn't opted in on any device yet. That's not
        # an error — just silent.
        detail = (r.text or "")[:200]
        if r.status_code == 400 and "All included players are not subscribed" in detail:
            return {"sent": 0, "skipped": "user_not_subscribed"}
        logger.warning(
            f"OneSignal push failed user={user_id} status={r.status_code} detail={detail}"
        )
        return {"sent": 0, "failed": 1, "status": r.status_code, "detail": detail}
    except Exception as e:
        logger.warning(f"OneSignal push exception user={user_id}: {e}")
        return {"sent": 0, "failed": 1, "error": str(e)[:200]}


# ── Fire-and-forget helper used by realtime.py ──────────────────────────
def schedule_push(user_id: str, payload: dict) -> None:
    """Schedule a push without awaiting — drop-in replacement for the
    `_fire_and_forget_push` helper in routes/realtime.py (iter70).

    Exceptions are swallowed to the warning log; we never crash the
    caller's realtime flow on a push hiccup."""
    try:
        asyncio.create_task(send_push_to_user(user_id, payload))
    except Exception as e:
        logger.warning(f"push schedule failed user={user_id}: {e}")
