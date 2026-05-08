"""
Email tracking & webhook routes — public, unauthenticated endpoints.

    GET  /api/email/unsubscribe            — 1-click unsub, sets users.email_subscribed=false
    GET  /api/email/track/open             — invisible pixel, logs event=opened
    GET  /api/email/track/click            — 302 redirect, logs event=clicked
    POST /api/webhooks/resend              — Resend delivered/bounced/complained events

Every event appends to `email_logs` and updates the parent campaign's
denormalized counters. Abuse-safe: unknown campaign/user pairs are silently
no-op instead of 500, so attackers can't probe structure via errors.
"""
from __future__ import annotations
import base64
import json
import logging
import uuid
from fastapi import APIRouter, Request, HTTPException, Query, Body
from fastapi.responses import Response, RedirectResponse
from database import get_pool
from services.email_renderer import verify_unsub_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["email-tracking"])

# 1x1 transparent GIF (43 bytes)
_PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
    b"\x00\x00\x02\x02D\x01\x00;"
)


async def _log_event(conn, campaign_id, user_id, email, event,
                     provider_msg_id=None, url=None, ip=None, ua=None,
                     meta: dict | None = None):
    log_id = f"log_{uuid.uuid4().hex[:14]}"
    await conn.execute(
        """INSERT INTO email_logs
             (log_id, campaign_id, user_id, email, event,
              provider_message_id, url, ip_address, user_agent, metadata, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, NOW())""",
        log_id, campaign_id, user_id, email, event,
        provider_msg_id, url, ip, ua, json.dumps(meta or {}),
    )


async def _bump_counter(conn, campaign_id: str, event: str):
    col = {
        "delivered": "delivered_count",
        "opened": "opened_count",
        "clicked": "clicked_count",
        "bounced": "bounced_count",
        "unsubscribed": "unsub_count",
    }.get(event)
    if not col:
        return
    await conn.execute(
        f"UPDATE email_campaigns SET {col} = {col} + 1 WHERE campaign_id = $1",
        campaign_id,
    )


# ── 1-click unsubscribe ────────────────────────────────────────────────────

@router.get("/api/email/unsubscribe")
async def unsubscribe(request: Request,
                      u: str = Query(...), t: str = Query(...)):
    if not verify_unsub_token(u, t):
        # Generic 400 — don't leak whether user_id exists
        raise HTTPException(status_code=400, detail="Lien invalide ou expiré.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT email FROM users WHERE user_id = $1", u)
        if not user:
            raise HTTPException(status_code=400, detail="Lien invalide.")
        await conn.execute(
            "UPDATE users SET email_subscribed = FALSE, email_unsubscribed_at = NOW() "
            "WHERE user_id = $1",
            u,
        )
        await _log_event(
            conn, campaign_id=None, user_id=u, email=user["email"],
            event="unsubscribed",
            ip=request.headers.get("cf-connecting-ip") or
               (request.client.host if request.client else None),
            ua=request.headers.get("user-agent", ""),
        )

    html = """
    <!doctype html><html lang="fr"><head><meta charset="utf-8">
    <title>Désabonnement confirmé · JAPAP</title>
    <meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body style="margin:0;padding:0;background:#f4f4f5;font-family:Manrope,Arial,sans-serif;">
      <div style="max-width:420px;margin:80px auto;padding:32px;background:#fff;
                  border-radius:16px;text-align:center;border:1px solid #e4e4e7;">
        <div style="font-size:40px;margin-bottom:12px;">✓</div>
        <h1 style="font-family:'Outfit',Arial,sans-serif;color:#18181B;margin:0 0 8px;">
          Désabonnement confirmé
        </h1>
        <p style="color:#71717a;font-size:14px;line-height:1.55;margin:8px 0 0;">
          Vous ne recevrez plus d'emails promotionnels de JAPAP. Vous continuerez
          à recevoir les emails transactionnels essentiels (connexion, paiement).
        </p>
      </div>
    </body></html>
    """
    return Response(content=html, media_type="text/html")


# ── Open pixel ─────────────────────────────────────────────────────────────

@router.get("/api/email/track/open")
async def track_open(request: Request,
                     c: str = Query(""), u: str = Query("")):
    if c and u:
        pool = await get_pool()
        try:
            async with pool.acquire() as conn:
                user = await conn.fetchrow(
                    "SELECT email FROM users WHERE user_id = $1", u)
                if user:
                    await _log_event(
                        conn, campaign_id=c, user_id=u, email=user["email"],
                        event="opened",
                        ip=request.headers.get("cf-connecting-ip") or
                           (request.client.host if request.client else None),
                        ua=request.headers.get("user-agent", ""),
                    )
                    await _bump_counter(conn, c, "opened")
        except Exception as e:
            logger.debug(f"track_open swallowed: {e}")
    return Response(
        content=_PIXEL_GIF,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# ── Click tracker (redirect) ───────────────────────────────────────────────

@router.get("/api/email/track/click")
async def track_click(request: Request,
                      c: str = Query(""), u: str = Query(""),
                      url: str = Query("")):
    target = "/"
    if url:
        try:
            target = base64.urlsafe_b64decode(url + "===").decode()
        except Exception:
            target = "/"
    if c and u:
        pool = await get_pool()
        try:
            async with pool.acquire() as conn:
                user = await conn.fetchrow(
                    "SELECT email FROM users WHERE user_id = $1", u)
                if user:
                    await _log_event(
                        conn, campaign_id=c, user_id=u, email=user["email"],
                        event="clicked", url=target,
                        ip=request.headers.get("cf-connecting-ip") or
                           (request.client.host if request.client else None),
                        ua=request.headers.get("user-agent", ""),
                    )
                    await _bump_counter(conn, c, "clicked")
        except Exception as e:
            logger.debug(f"track_click swallowed: {e}")
    return RedirectResponse(url=target, status_code=302)


# ── Resend webhook ─────────────────────────────────────────────────────────

@router.post("/api/webhooks/resend")
async def resend_webhook(request: Request):
    """Accept Resend events: email.delivered, email.opened, email.clicked,
    email.bounced, email.complained, email.delivery_delayed.

    We identify the recipient by the email address carried in the event
    payload and map it back to the most recent `email_logs` row (event=sent)
    for that email — which carries our campaign_id / user_id.

    Signature verification:
      If `RESEND_WEBHOOK_SECRET` is set, we verify the Svix signature headers
      (`svix-id`, `svix-timestamp`, `svix-signature`) before processing.
      Rejected payloads return HTTP 401 and are never logged to audit.
    """
    raw_body = await request.body()
    import os as _os
    secret = (_os.environ.get("RESEND_WEBHOOK_SECRET") or "").strip()
    if secret:
        # Svix-style HMAC verification — see https://docs.svix.com/receiving/verifying-payloads/how-manual
        import hmac, hashlib, base64, time
        svix_id = request.headers.get("svix-id", "")
        svix_ts = request.headers.get("svix-timestamp", "")
        svix_sig = request.headers.get("svix-signature", "")
        if not (svix_id and svix_ts and svix_sig):
            raise HTTPException(status_code=401, detail="Missing Svix headers")
        # Reject stale deliveries (> 5 min)
        try:
            if abs(int(time.time()) - int(svix_ts)) > 5 * 60:
                raise HTTPException(status_code=401, detail="Stale webhook")
        except ValueError:
            raise HTTPException(status_code=401, detail="Bad svix-timestamp")
        # Secret format: 'whsec_BASE64' → strip prefix
        key_b64 = secret.replace("whsec_", "")
        try:
            key = base64.b64decode(key_b64)
        except Exception:
            raise HTTPException(status_code=500, detail="Misconfigured RESEND_WEBHOOK_SECRET")
        signed = f"{svix_id}.{svix_ts}.{raw_body.decode('utf-8', errors='replace')}".encode()
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        # svix-signature is comma-separated 'v1,<sig>,v1,<sig>,...' — accept if any matches
        candidates = [p.split(",", 1)[1] for p in svix_sig.split(" ") if p.startswith("v1,")]
        if expected not in candidates:
            logger.warning("Resend webhook signature mismatch — rejected")
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        body = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    etype = (body.get("type") or "").lower()
    data = body.get("data") or {}
    to_list = data.get("to") or []
    provider_msg_id = data.get("email_id") or body.get("email_id") or ""
    if not to_list:
        return {"ignored": True}
    recipient = to_list[0] if isinstance(to_list, list) else str(to_list)

    event_map = {
        "email.delivered": "delivered",
        "email.bounced": "bounced",
        "email.complained": "unsubscribed",
        "email.opened": "opened",
        "email.clicked": "clicked",
        "email.delivery_delayed": "deferred",
    }
    event = event_map.get(etype)
    if not event:
        return {"ignored": True, "type": etype}

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Find the most recent sent-log for this email to attribute the event
        ref = await conn.fetchrow(
            """SELECT campaign_id, user_id FROM email_logs
               WHERE email = $1 AND event = 'sent'
               ORDER BY created_at DESC LIMIT 1""",
            recipient,
        )
        cid = ref["campaign_id"] if ref else None
        uid = ref["user_id"] if ref else None
        await _log_event(conn, campaign_id=cid, user_id=uid, email=recipient,
                         event=event, provider_msg_id=provider_msg_id,
                         meta={"resend_type": etype})
        if cid:
            await _bump_counter(conn, cid, event)
        # For complaints, also unsubscribe the user
        if event == "unsubscribed" and uid:
            await conn.execute(
                "UPDATE users SET email_subscribed = FALSE, "
                "email_unsubscribed_at = NOW() WHERE user_id = $1",
                uid,
            )
        # iter153 — propagate to migration_broadcast_targets so the campaign
        # dashboard shows real delivery / open / click / bounce stats.
        try:
            await _update_broadcast_target(conn, recipient, provider_msg_id, event)
        except Exception as e:
            logger.warning(f"broadcast target update skipped: {e}")
    return {"ok": True, "event": event}


async def _update_broadcast_target(conn, recipient: str, provider_msg_id: str,
                                    event: str) -> None:
    """Update the matching `migration_broadcast_targets` row with the
    Resend event. Match priority:
      1. provider_message_id (strongest, unique per send)
      2. lower(email) on the most-recent sent target (fallback when Resend
         omits email_id, e.g. very old payloads).
    """
    target = None
    if provider_msg_id:
        target = await conn.fetchrow(
            """SELECT id, campaign_id FROM migration_broadcast_targets
                WHERE provider_message_id = $1 LIMIT 1""",
            provider_msg_id,
        )
    if not target:
        target = await conn.fetchrow(
            """SELECT id, campaign_id FROM migration_broadcast_targets
                WHERE LOWER(email) = $1
                  AND status IN ('sent', 'delivered', 'opened', 'clicked', 'bounced')
                ORDER BY id DESC LIMIT 1""",
            recipient.lower(),
        )
    if not target:
        return

    field_map = {
        "delivered": ("delivered_at", "delivered_count", "delivered"),
        "opened":    ("opened_at",    "opened_count",    "opened"),
        "clicked":   ("clicked_at",   "clicked_count",   "clicked"),
        "bounced":   ("bounced_at",   "bounced_count",   "bounced"),
    }
    if event not in field_map:
        return
    ts_col, counter_col, new_status = field_map[event]
    # Only advance state forward — never demote (e.g. once 'clicked',
    # don't roll back to 'opened').
    state_rank = {
        "pending": 0, "sending": 1, "sent": 2, "delivered": 3,
        "opened": 4, "clicked": 5, "bounced": 6, "failed": 6,
    }
    cur = await conn.fetchrow(
        "SELECT status FROM migration_broadcast_targets WHERE id = $1",
        target["id"],
    )
    cur_rank = state_rank.get(cur["status"], 0) if cur else 0
    new_rank = state_rank.get(new_status, 0)
    if new_rank > cur_rank:
        await conn.execute(
            f"""UPDATE migration_broadcast_targets
                   SET status = $1, {ts_col} = COALESCE({ts_col}, NOW())
                 WHERE id = $2""",
            new_status, target["id"],
        )
    else:
        # Just stamp the timestamp without demoting status.
        await conn.execute(
            f"""UPDATE migration_broadcast_targets
                   SET {ts_col} = COALESCE({ts_col}, NOW())
                 WHERE id = $1""",
            target["id"],
        )
    await conn.execute(
        f"""UPDATE migration_broadcast_campaigns
               SET {counter_col} = {counter_col} + 1, updated_at = NOW()
             WHERE campaign_id = $1""",
        target["campaign_id"],
    )



# ── iter152 — Admin email audit ───────────────────────────────────────
@router.get("/api/admin/email-audit")
async def admin_email_audit(request: Request,
                            email: str = Query(..., min_length=3),
                            limit: int = Query(50, ge=1, le=500)):
    """Surface every transactional email send + Resend webhook event for
    the given recipient, newest first. Used by ops to debug
    "the user didn't receive my reset link" cases (which sparked iter152).

    Returns the chronological event timeline:
        sent → delivered | bounced | failed | spam | opened
    so support can tell whether the issue is on our side (`failed`) or
    on the recipient side (`bounced`, `spam`, mailbox full).
    """
    from routes.auth import require_admin as _ra
    await _ra(request)
    pool = await get_pool()
    addr = email.strip().lower()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT log_id, event, provider_message_id, metadata, created_at
                 FROM email_logs
                WHERE LOWER(email) = $1
                ORDER BY created_at DESC
                LIMIT $2""",
            addr, limit,
        )
    events = []
    for r in rows:
        meta = {}
        try:
            if r["metadata"]:
                meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"]
        except Exception:
            meta = {}
        events.append({
            "log_id": r["log_id"],
            "event": r["event"],
            "message_id": r["provider_message_id"],
            "subject": meta.get("subject", ""),
            "kind": meta.get("kind", ""),
            "status_code": meta.get("status_code"),
            "error": meta.get("error", ""),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return {"email": addr, "events": events, "total": len(events)}


# ── iter152 — Admin manual resend ─────────────────────────────────────
@router.post("/api/admin/email/resend-password-reset")
async def admin_resend_password_reset(request: Request,
                                       payload: dict = Body(...)):
    """Ops fallback : resend a fresh password-reset link to a user when
    the original email bounced / disappeared into a spam folder.

    Body:
        { "email": "user@example.com" }

    Returns the structured delivery result (`ok`, `message_id`,
    `status_code`, `error`) so the admin UI can show exactly what
    happened — same audit trail as `/admin/email-audit` reads from."""
    from routes.auth import require_admin as _ra
    await _ra(request)
    addr = (payload.get("email") or "").strip().lower()
    if not addr or "@" not in addr:
        raise HTTPException(status_code=400, detail="Email invalide.")
    import os
    import secrets
    from datetime import datetime, timezone, timedelta
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT user_id FROM users WHERE email = $1", addr)
        if not user:
            raise HTTPException(status_code=404, detail="Aucun compte pour cet email.")
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await conn.execute(
            """INSERT INTO password_reset_tokens (user_id, token, expires_at)
                VALUES ($1, $2, $3)""",
            user["user_id"], token, expires,
        )
    frontend = os.environ.get("FRONTEND_URL") or str(request.base_url).rstrip("/").replace("/api", "")
    reset_url = f"{frontend}/reset-password?token={token}"
    from services.email_service import send_password_reset_email_detailed
    delivery = await send_password_reset_email_detailed(addr, reset_url)
    # Log the admin-initiated action for accountability.
    try:
        from routes.auth import get_current_user
        admin = await get_current_user(request)
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO admin_audit_log
                       (admin_id, action, target_email, target_user_id,
                        details, created_at)
                   VALUES ($1, 'resend_password_reset', $2, $3, $4, NOW())""",
                admin.get("user_id"), addr, user["user_id"],
                json.dumps({
                    "ok": bool(delivery.get("ok")),
                    "message_id": delivery.get("message_id"),
                    "status_code": delivery.get("status_code"),
                }),
            )
    except Exception as e:
        logger.warning(f"admin_audit_log resend insert failed: {e}")
    return {"delivery": delivery, "user_id": user["user_id"]}
