"""
iter90 — Operations notification emails.

Sends transactional emails to the business address `liportalmerchand@gmail.com`
on three critical events:
  • deposit  (invoice created / credited)
  • withdraw (request submitted)
  • support  (ticket opened)

Also mails an acknowledgment to the end-user when a support ticket is created.

Fire-and-forget — never blocks the caller.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from services.email_service import send_email

logger = logging.getLogger(__name__)

OPS_INBOX = os.environ.get("OPS_INBOX_EMAIL", "liportalmerchand@gmail.com")


def _now_fr() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")


def _wrap(title: str, rows: list[tuple[str, str]], cta: str = "") -> str:
    lines = "".join(
        f"<tr><td style='padding:8px 14px;color:#6b7280;font-size:13px'>{k}</td>"
        f"<td style='padding:8px 14px;color:#111;font-weight:600;font-size:13px'>{v}</td></tr>"
        for k, v in rows
    )
    return f"""<!doctype html><html><body style="font-family:'Inter','Helvetica',Arial,sans-serif;background:#f6f7fb;padding:24px;margin:0">
      <div style="max-width:560px;margin:0 auto;background:white;border-radius:14px;overflow:hidden;border:1px solid #e5e7eb">
        <div style="background:linear-gradient(135deg,#FFD700,#FFB800);padding:18px 22px">
          <div style="font-family:'Outfit',sans-serif;font-weight:800;font-size:18px;color:#0b1020">JAPAP · Ops notification</div>
          <div style="font-size:12px;color:#0b1020;opacity:.75;margin-top:2px">{_now_fr()}</div>
        </div>
        <div style="padding:22px">
          <h2 style="margin:0 0 14px 0;font-family:'Outfit',sans-serif;font-size:17px;color:#111">{title}</h2>
          <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden">{lines}</table>
          {cta}
        </div>
        <div style="padding:14px 22px;border-top:1px solid #e5e7eb;font-size:11px;color:#9ca3af">
          Envoi automatique — ne pas répondre à ce mail. · JAPAP Messenger
        </div>
      </div></body></html>"""


def _fire(coro) -> None:
    try:
        asyncio.create_task(coro)
    except RuntimeError:
        # no running loop (tests) — run synchronously
        asyncio.run(coro)


# ─── Public API ───────────────────────────────────────────────────────────

def notify_deposit(
    *, user_id: str, user_email: str, user_name: str,
    amount: float, method: str, tx_id: str, status: str,
) -> None:
    async def _work():
        try:
            html = _wrap(
                "💰 Nouvelle demande de dépôt",
                [
                    ("Utilisateur", f"{user_name} &lt;{user_email}&gt;"),
                    ("User ID", user_id),
                    ("Montant", f"{amount} USD"),
                    ("Méthode", method),
                    ("Transaction ID", tx_id),
                    ("Statut", status),
                    ("Date", _now_fr()),
                ],
            )
            await send_email(
                to=OPS_INBOX,
                subject=f"[JAPAP · Dépôt] {amount} USD · {user_email}",
                html=html,
                text=f"Deposit request: {user_email} - {amount} USD via {method} (tx_id={tx_id}, status={status})",
            )
        except Exception as e:
            logger.warning("notify_deposit failed: %s", e)
    _fire(_work())


def notify_withdraw(
    *, user_id: str, user_email: str, user_name: str,
    amount: float, fee: float, net: float, method: str, address: str,
    tx_id: str, status: str, processing_mode: str,
) -> None:
    async def _work():
        try:
            html = _wrap(
                "⚠️ Nouvelle demande de retrait",
                [
                    ("Utilisateur", f"{user_name} &lt;{user_email}&gt;"),
                    ("User ID", user_id),
                    ("Montant brut", f"{amount:.2f} USD"),
                    ("Frais", f"{fee:.2f} USD"),
                    ("Montant net", f"{net:.2f} USD"),
                    ("Méthode", method),
                    ("Adresse", address[:60] + ("…" if len(address) > 60 else "")),
                    ("Mode", processing_mode),
                    ("Statut", status),
                    ("Transaction ID", tx_id),
                    ("Date", _now_fr()),
                ],
            )
            await send_email(
                to=OPS_INBOX,
                subject=f"[JAPAP · Retrait] {amount:.2f} USD · {user_email}",
                html=html,
                text=f"Withdraw: {user_email} - {amount} USD via {method} (tx_id={tx_id}, mode={processing_mode})",
            )
        except Exception as e:
            logger.warning("notify_withdraw failed: %s", e)
    _fire(_work())


def notify_support_ticket_to_ops(
    *, ticket_id: str, user_email: str, user_name: str,
    category: str, subject: str, message: str, ai_tried: bool,
) -> None:
    async def _work():
        try:
            html = _wrap(
                f"🎫 Nouveau ticket support — #{ticket_id}",
                [
                    ("Utilisateur", f"{user_name} &lt;{user_email}&gt;"),
                    ("Catégorie", category),
                    ("Sujet", subject),
                    ("IA consultée", "Oui" if ai_tried else "Non"),
                    ("Ticket ID", ticket_id),
                    ("Date", _now_fr()),
                ],
                cta=f"""
                <div style="margin-top:16px;padding:14px;background:#f9fafb;border-radius:10px;border:1px solid #e5e7eb">
                  <div style="font-size:12px;color:#6b7280;margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px">Message utilisateur</div>
                  <div style="white-space:pre-wrap;font-size:14px;color:#111;line-height:1.55">{message}</div>
                </div>""",
            )
            await send_email(
                to=OPS_INBOX,
                subject=f"[JAPAP · Support #{ticket_id}] {category} · {subject[:60]}",
                html=html,
                text=f"Support ticket #{ticket_id} from {user_email} ({category}): {subject}\n\n{message}",
            )
        except Exception as e:
            logger.warning("notify_support_ticket_to_ops failed: %s", e)
    _fire(_work())


def notify_support_ticket_ack_to_user(
    *, to_email: str, user_name: str, ticket_id: str, subject: str,
) -> None:
    async def _work():
        try:
            html = f"""<!doctype html><html><body style="font-family:'Inter',Helvetica,Arial,sans-serif;background:#f6f7fb;padding:24px;margin:0">
              <div style="max-width:540px;margin:0 auto;background:white;border-radius:14px;overflow:hidden;border:1px solid #e5e7eb">
                <div style="background:linear-gradient(135deg,#FFD700,#FFB800);padding:20px 24px">
                  <div style="font-family:'Outfit',sans-serif;font-weight:800;font-size:20px;color:#0b1020">JAPAP Support</div>
                </div>
                <div style="padding:24px;color:#111;font-size:15px;line-height:1.65">
                  <p>Bonjour {user_name},</p>
                  <p>Nous avons bien reçu votre demande :</p>
                  <div style="padding:12px 14px;background:#f9fafb;border-left:3px solid #FFD700;border-radius:6px;margin:14px 0">
                    <div style="font-size:12px;color:#6b7280">Ticket</div>
                    <div style="font-weight:700">#{ticket_id} — {subject}</div>
                  </div>
                  <p>Notre équipe va traiter votre demande dans les plus brefs délais. Vous recevrez une réponse par email.</p>
                  <p style="color:#6b7280;font-size:13px;margin-top:20px">Merci de votre confiance,<br/>L'équipe JAPAP</p>
                </div>
              </div></body></html>"""
            await send_email(
                to=to_email,
                subject=f"[JAPAP] Ticket #{ticket_id} bien reçu",
                html=html,
                text=f"Bonjour {user_name}, votre ticket #{ticket_id} ({subject}) a bien été reçu. Nous vous répondrons rapidement.",
            )
        except Exception as e:
            logger.warning("notify_support_ticket_ack failed: %s", e)
    _fire(_work())
