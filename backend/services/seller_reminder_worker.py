"""
iter189 — Marketplace buyer-intent reminders worker
====================================================
Two cascading reminders for sellers who didn't answer a buyer intent :

    +15 min  → push notification  "⏰ {Alice} attend toujours ta réponse pour « Produit X »."
    +24 h    → email digest (top 3 unanswered intents, grouped per seller)

A seller is considered "responded" when they've posted a message in the
conversation AFTER the intent's `created_at`. Idempotent via the
`buyer_intents_reminders_sent(intent_id, kind)` table (PK composite).

Tick every 5 minutes. Zero hardcode :
    • `seller_reminder_push_minutes`      (default 15)
    • `seller_reminder_email_hours`       (default 24)
    • `seller_reminder_email_max_intents` (default 3)
    • `seller_reminders_enabled`          (toggle)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None
_stop_flag = asyncio.Event()
WORKER_ID = f"smr-{os.getpid()}"
TICK_SECONDS = 300  # 5 min


async def _send_push_reminder(conn, intent) -> bool:
    """Send the 15-min push reminder. Returns True on success."""
    from services.notifications import send_social_notification
    # Buyer + product info
    buyer = await conn.fetchrow(
        "SELECT user_id, first_name, username FROM users WHERE user_id = $1",
        intent["buyer_id"])
    product = await conn.fetchrow(
        "SELECT title FROM products WHERE product_id = $1",
        intent["product_id"])
    buyer_name = (buyer["first_name"] if buyer and buyer["first_name"]
                  else (buyer["username"] if buyer and buyer["username"]
                        else "Un acheteur"))[:40]
    title = f"⏰ {buyer_name} attend toujours ta réponse"
    body = (f"« {product['title'] if product else 'un produit'} » — Ne rate "
            f"pas cette vente. Réponds maintenant.")
    try:
        await send_social_notification(
            event_type="marketplace_seller_reminder_15min",
            actor=dict(buyer) if buyer else None,
            target_user_id=intent["seller_id"],
            title=title, body=body,
            deep_link=f"/chat?conv={intent['conv_id']}",
            extra_data={"intent_id": intent["id"],
                         "product_id": intent["product_id"]},
        )
        return True
    except Exception as e:  # pragma: no cover
        logger.warning(f"[smr] push send failed for intent={intent['id']}: {e}")
        return False


async def _send_email_digest(conn, seller_id: str, intents: list) -> bool:
    """Send the 24h email digest (up to 3 unanswered intents to the seller)."""
    seller = await conn.fetchrow(
        "SELECT email, first_name FROM users WHERE user_id = $1", seller_id)
    if not seller or not seller["email"]:
        return False

    # Build context rows
    rows = []
    for it in intents[:3]:
        buyer = await conn.fetchrow(
            "SELECT first_name, username FROM users WHERE user_id = $1",
            it["buyer_id"])
        product = await conn.fetchrow(
            "SELECT title, price, currency FROM products WHERE product_id = $1",
            it["product_id"])
        rows.append({
            "buyer_name": (buyer["first_name"] or buyer["username"] or "Un acheteur")
                          if buyer else "Un acheteur",
            "product_title": product["title"] if product else "—",
            "product_price": f"{product['price']} {product['currency']}" if product else "",
            "conv_id": it["conv_id"],
            "created_at": it["created_at"],
        })
    first_name = seller["first_name"] or ""
    html_rows = "".join(
        f'<tr><td style="padding:8px 12px;border-bottom:1px solid #eee">'
        f'<b>{r["buyer_name"]}</b> — {r["product_title"]}<br>'
        f'<span style="color:#666">{r["product_price"]}</span></td></tr>'
        for r in rows)
    html = f"""\
<html><body style="font-family:Arial,sans-serif;color:#111">
<h2 style="color:#0F056B">Hé {first_name}, tu as {len(rows)} acheteur(s) qui attendent ta réponse sur JAPAP.</h2>
<p>Répondre en moins de 5 min booste ton taux de conversion de +30%. Là tu as laissé filer plus de 24h — il n'est pas trop tard.</p>
<table style="width:100%;border-collapse:collapse;margin:16px 0">{html_rows}</table>
<p><a href="{os.environ.get('PUBLIC_APP_URL', 'https://japapmessenger.com')}/chat"
   style="display:inline-block;background:#0F056B;color:#fff;padding:12px 20px;
          border-radius:8px;text-decoration:none;font-weight:bold">
   Répondre maintenant
 </a></p>
<p style="color:#999;font-size:12px">— L'équipe JAPAP Marketplace</p>
</body></html>"""
    text = (f"Hé {first_name}, tu as {len(rows)} acheteur(s) qui attendent "
            f"ta réponse sur JAPAP Marketplace.\n\n" +
            "\n".join(f"• {r['buyer_name']} — {r['product_title']} ({r['product_price']})"
                      for r in rows) +
            "\n\nRéponds sur https://japapmessenger.com/chat")
    try:
        from services.email_service import send_email
        await send_email(
            to=seller["email"],
            subject=f"🛍️ {len(rows)} acheteur(s) attendent ta réponse sur JAPAP",
            html=html, text=text)
        return True
    except Exception as e:  # pragma: no cover
        logger.warning(f"[smr] email send failed for seller={seller_id}: {e}")
        return False


async def _tick(pool):
    from services.settings_service import get_bool, get_int
    if not await get_bool("seller_reminders_enabled", True):
        return
    push_min = max(5, await get_int("seller_reminder_push_minutes", 15))
    email_h = max(1, await get_int("seller_reminder_email_hours", 24))
    max_digest = max(1, await get_int("seller_reminder_email_max_intents", 3))

    async with pool.acquire() as conn:
        # ── 15-MIN PUSH reminders ──
        # Select intents where:
        #   • older than `push_min` minutes
        #   • no messages from the seller in that conv after intent creation
        #   • reminder kind='push_15min' NOT sent yet
        push_candidates = await conn.fetch("""
            SELECT i.id, i.buyer_id, i.seller_id, i.product_id, i.conv_id, i.created_at
            FROM marketplace_buyer_intents i
            WHERE i.created_at < NOW() - ($1::int || ' minutes')::interval
              AND i.created_at > NOW() - INTERVAL '48 hours'
              AND NOT EXISTS (
                  SELECT 1 FROM messages m
                  WHERE m.conv_id = i.conv_id
                    AND m.sender_id = i.seller_id
                    AND m.created_at > i.created_at
              )
              AND NOT EXISTS (
                  SELECT 1 FROM buyer_intents_reminders_sent r
                  WHERE r.intent_id = i.id AND r.kind = 'push_15min'
              )
            ORDER BY i.created_at ASC
            LIMIT 100
        """, push_min)

        for intent in push_candidates:
            ok = await _send_push_reminder(conn, intent)
            if ok:
                await conn.execute("""
                    INSERT INTO buyer_intents_reminders_sent (intent_id, kind)
                    VALUES ($1, 'push_15min') ON CONFLICT DO NOTHING
                """, intent["id"])

        if push_candidates:
            logger.info(f"[smr] sent {len(push_candidates)} push_15min reminders")

        # ── 24H EMAIL DIGEST (grouped per seller) ──
        email_rows = await conn.fetch("""
            SELECT i.id, i.buyer_id, i.seller_id, i.product_id, i.conv_id, i.created_at
            FROM marketplace_buyer_intents i
            WHERE i.created_at < NOW() - ($1::int || ' hours')::interval
              AND i.created_at > NOW() - INTERVAL '7 days'
              AND NOT EXISTS (
                  SELECT 1 FROM messages m
                  WHERE m.conv_id = i.conv_id
                    AND m.sender_id = i.seller_id
                    AND m.created_at > i.created_at
              )
              AND NOT EXISTS (
                  SELECT 1 FROM buyer_intents_reminders_sent r
                  WHERE r.intent_id = i.id AND r.kind = 'email_24h'
              )
            ORDER BY i.seller_id, i.created_at DESC
        """, email_h)

        # Group per seller
        groups: dict[str, list] = {}
        for r in email_rows:
            groups.setdefault(r["seller_id"], []).append(r)

        digests_sent = 0
        for seller_id, intents in groups.items():
            batch = intents[:max_digest]
            ok = await _send_email_digest(conn, seller_id, batch)
            if ok:
                digests_sent += 1
                # Mark only the emailed ones (up to max_digest) — leftovers
                # will be picked up on the next tick.
                for it in batch:
                    await conn.execute("""
                        INSERT INTO buyer_intents_reminders_sent (intent_id, kind)
                        VALUES ($1, 'email_24h') ON CONFLICT DO NOTHING
                    """, it["id"])
        if digests_sent:
            logger.info(f"[smr] sent {digests_sent} email digests "
                         f"({len(email_rows)} intents)")


async def _loop():
    from database import get_pool
    logger.info(f"[SellerReminder {WORKER_ID}] loop started (tick={TICK_SECONDS}s)")
    await asyncio.sleep(15)  # wait for DB pool at boot
    while not _stop_flag.is_set():
        try:
            pool = await get_pool()
            await _tick(pool)
        except Exception as e:
            logger.warning(f"[smr] tick failed: {e}")
        try:
            await asyncio.wait_for(_stop_flag.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
    logger.info(f"[SellerReminder {WORKER_ID}] loop stopped")


def start_worker(app):
    @app.on_event("startup")
    async def _start():
        global _task
        if _task is None or _task.done():
            _task = asyncio.create_task(_loop())

    @app.on_event("shutdown")
    async def _stop():
        _stop_flag.set()
