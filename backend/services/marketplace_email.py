"""
JAPAP — Marketplace Escrow Email Notifications (iter177)
=========================================================
Resend-based transactional emails for the 4 critical Escrow lifecycle events.
All sends are best-effort: failure to email NEVER blocks the financial flow
(escrow holds/releases are atomic in Postgres; emails are post-commit).
"""
import logging

from services.email_service import send_email_detailed

logger = logging.getLogger(__name__)


def _wrap(subject: str, inner_html: str) -> str:
    """Minimal email shell consistent with kyc_email.py — inline styles only."""
    return f"""
    <!doctype html>
    <html><body style="margin:0;padding:0;background:#F3F4F6;font-family:Arial,sans-serif;color:#1F2937;">
      <div style="max-width:560px;margin:0 auto;padding:32px 16px;">
        <div style="background:white;border-radius:12px;padding:32px;
                    box-shadow:0 4px 6px rgba(0,0,0,0.05);">
          <div style="font-family:'Outfit',Arial,sans-serif;font-weight:800;
                      font-size:24px;color:#F7931A;letter-spacing:0.5px;
                      margin-bottom:24px;">JAPAP</div>
          {inner_html}
        </div>
        <p style="text-align:center;color:#9CA3AF;font-size:11px;margin-top:16px;">
          JAPAP Marketplace · Paiement sécurisé via Wallet USD JAPAP
        </p>
      </div>
    </body></html>
    """


def _btn(url: str, label: str, color: str = "#6366F1") -> str:
    return (
        f'<a href="{url}" style="display:inline-block;padding:12px 24px;'
        f'background:{color};color:white;text-decoration:none;'
        f'border-radius:8px;font-weight:bold;">{label}</a>'
    )


async def send_order_received(seller_email: str, *, seller_first_name: str,
                              buyer_first_name: str, product_title: str,
                              amount_usd: str, auto_release_days: int,
                              order_id: str, app_url: str) -> dict:
    """Sent to SELLER when a buyer places an escrow order."""
    subject = f"🛒 Nouvelle commande : {product_title[:60]}"
    body = f"""
    <h2 style="margin:0 0 16px;color:#111827;">🛒 Nouvelle commande Marketplace</h2>
    <p>Bonjour {seller_first_name or 'cher vendeur'},</p>
    <p><strong>{buyer_first_name or 'Un acheteur'}</strong> vient d'acheter ton produit
    sur JAPAP Marketplace :</p>
    <div style="margin:16px 0;padding:14px;background:#F0FDF4;border-left:4px solid #10B981;border-radius:6px;">
      <strong>{product_title}</strong><br>
      <span style="color:#065F46;font-size:18px;font-weight:bold;">{amount_usd} USD</span>
      en escrow JAPAP
    </div>
    <p>🔒 Les fonds sont <strong>bloqués</strong> dans notre wallet escrow USD.
    Tu seras crédité (montant - commission JAPAP) dès que :</p>
    <ul>
      <li>l'acheteur confirme la réception, OU</li>
      <li>{auto_release_days} jours s'écoulent sans action (auto-release).</li>
    </ul>
    <p>👉 Prépare et expédie le produit dès maintenant pour libérer le paiement plus vite.</p>
    <div style="margin:24px 0;text-align:center;">
      {_btn(f"{app_url}/services?view=marketplace", "Voir mes commandes")}
    </div>
    <p style="color:#6B7280;font-size:12px;">Référence : {order_id}</p>
    """
    return await send_email_detailed(
        to=seller_email, subject=subject,
        html=_wrap(subject, body), kind="marketplace_order_received")


async def send_order_auto_released(seller_email: str, *, seller_first_name: str,
                                   product_title: str, net_usd: str,
                                   commission_usd: str, commission_pct: str,
                                   order_id: str, app_url: str) -> dict:
    """Sent to SELLER when auto-release fires (or buyer confirms)."""
    subject = f"✅ Paiement libéré : {product_title[:60]}"
    body = f"""
    <h2 style="margin:0 0 16px;color:#111827;">✅ Paiement libéré sur ton wallet</h2>
    <p>Bonjour {seller_first_name or 'cher vendeur'},</p>
    <p>Bonne nouvelle : la commande <strong>{product_title}</strong> est terminée.
    Ton wallet JAPAP vient d'être crédité.</p>
    <div style="margin:16px 0;padding:14px;background:#ECFDF5;border-left:4px solid #10B981;border-radius:6px;">
      <span style="font-size:13px;color:#065F46;">Net crédité</span><br>
      <span style="font-size:24px;font-weight:bold;color:#065F46;">{net_usd} USD</span><br>
      <span style="font-size:12px;color:#6B7280;">
        Commission JAPAP {commission_pct}% : {commission_usd} USD
      </span>
    </div>
    <div style="margin:24px 0;text-align:center;">
      {_btn(f"{app_url}/wallet", "Voir mon wallet", "#10B981")}
    </div>
    <p style="color:#6B7280;font-size:12px;">Référence : {order_id}</p>
    """
    return await send_email_detailed(
        to=seller_email, subject=subject,
        html=_wrap(subject, body), kind="marketplace_order_released")


async def send_dispute_opened_seller(seller_email: str, *, seller_first_name: str,
                                     product_title: str, reason: str,
                                     order_id: str, app_url: str) -> dict:
    subject = f"⚖️ Litige ouvert : {product_title[:60]}"
    body = f"""
    <h2 style="margin:0 0 16px;color:#991B1B;">⚖️ L'acheteur a ouvert un litige</h2>
    <p>Bonjour {seller_first_name or 'cher vendeur'},</p>
    <p>L'acheteur de <strong>{product_title}</strong> a ouvert un litige.
    Notre équipe va arbitrer dans les <strong>48h</strong>.</p>
    <div style="margin:16px 0;padding:14px;background:#FEF2F2;border-left:4px solid #EF4444;border-radius:6px;">
      <span style="font-size:13px;color:#991B1B;font-weight:bold;">Motif :</span><br>
      <em style="color:#7F1D1D;">{reason}</em>
    </div>
    <p>Pendant le litige, le paiement reste <strong>bloqué en escrow</strong>.
    Tu peux contacter l'acheteur via la messagerie JAPAP pour tenter une
    résolution amiable. Si tu peux fournir des preuves d'expédition (capture
    bordereau, photo du produit envoyé, tracking), réponds simplement à cet email.</p>
    <div style="margin:24px 0;text-align:center;">
      {_btn(f"{app_url}/services?view=marketplace", "Voir le détail")}
    </div>
    <p style="color:#6B7280;font-size:12px;">Référence : {order_id}</p>
    """
    return await send_email_detailed(
        to=seller_email, subject=subject,
        html=_wrap(subject, body), kind="marketplace_dispute_opened")


async def send_dispute_opened_admin(admin_email: str, *, buyer_email: str,
                                    seller_email: str, product_title: str,
                                    reason: str, order_id: str,
                                    amount_usd: str, app_url: str) -> dict:
    subject = f"[ADMIN] ⚖️ Litige Marketplace #{order_id[:10]} — {amount_usd} USD"
    body = f"""
    <h2 style="margin:0 0 16px;color:#991B1B;">⚖️ Nouveau litige à arbitrer</h2>
    <p><strong>Produit :</strong> {product_title}<br>
    <strong>Montant en escrow :</strong> {amount_usd} USD<br>
    <strong>Acheteur :</strong> {buyer_email}<br>
    <strong>Vendeur :</strong> {seller_email}<br>
    <strong>Order ID :</strong> {order_id}</p>
    <div style="margin:16px 0;padding:14px;background:#FFFBEB;border-left:4px solid #F59E0B;border-radius:6px;">
      <strong>Motif acheteur :</strong><br><em>{reason}</em>
    </div>
    <p>Trois actions possibles depuis l'admin :</p>
    <ol>
      <li><strong>release_seller</strong> — libérer au vendeur (commission appliquée)</li>
      <li><strong>refund_buyer</strong> — rembourser intégralement l'acheteur</li>
      <li><strong>split</strong> — partage avec montant vendeur précis</li>
    </ol>
    <div style="margin:24px 0;text-align:center;">
      {_btn(f"{app_url}/admin?tab=disputes", "Aller à la file de litiges", "#7C3AED")}
    </div>
    <p style="color:#6B7280;font-size:12px;">SLA arbitrage : 48h ouvrées.</p>
    """
    return await send_email_detailed(
        to=admin_email, subject=subject,
        html=_wrap(subject, body), kind="marketplace_dispute_admin")


async def send_dispute_resolved(to_email: str, *, first_name: str,
                                role: str,                # 'buyer' | 'seller'
                                decision: str,            # 'release_seller' | 'refund_buyer' | 'split'
                                product_title: str, amount_usd: str,
                                breakdown: dict,          # {seller_net, buyer_refund, commission}
                                notes: str, order_id: str, app_url: str) -> dict:
    """Sent to BOTH buyer and seller after admin resolution."""
    decision_label = {
        "release_seller": "💸 Paiement libéré au vendeur",
        "refund_buyer":   "↩️ Remboursement intégral de l'acheteur",
        "split":          "⚖️ Résolution en partage",
    }.get(decision, decision)
    color = {"release_seller": "#10B981", "refund_buyer": "#0EA5E9", "split": "#8B5CF6"}.get(decision, "#6366F1")

    seller_net = breakdown.get("seller_net") or breakdown.get("net_seller") or "0.00"
    buyer_refund = breakdown.get("buyer_refund") or "0.00"
    commission = breakdown.get("commission") or "0.00"

    notes_html = f'<p style="background:#FFFBEB;padding:12px;border-radius:6px;color:#78350F;"><strong>Note de l&apos;équipe :</strong> {notes}</p>' if notes else ''
    subject = f"✅ Litige résolu : {product_title[:60]}"
    body = f"""
    <h2 style="margin:0 0 16px;color:#111827;">{decision_label}</h2>
    <p>Bonjour {first_name or ''},</p>
    <p>Notre équipe a tranché le litige sur <strong>{product_title}</strong>.</p>
    <div style="margin:16px 0;padding:14px;background:#F9FAFB;border-left:4px solid {color};border-radius:6px;">
      <table style="width:100%;border-collapse:collapse;">
        <tr><td style="padding:4px 0;color:#6B7280;">Montant initial</td>
            <td style="padding:4px 0;text-align:right;font-weight:bold;">{amount_usd} USD</td></tr>
        <tr><td style="padding:4px 0;color:#6B7280;">Net vendeur</td>
            <td style="padding:4px 0;text-align:right;color:#065F46;">{seller_net} USD</td></tr>
        <tr><td style="padding:4px 0;color:#6B7280;">Remboursement acheteur</td>
            <td style="padding:4px 0;text-align:right;color:#075985;">{buyer_refund} USD</td></tr>
        <tr><td style="padding:4px 0;color:#6B7280;">Commission JAPAP</td>
            <td style="padding:4px 0;text-align:right;color:#92400E;">{commission} USD</td></tr>
      </table>
    </div>
    {notes_html}
    <p>Le wallet JAPAP a été ajusté en conséquence — tu peux le vérifier dès maintenant.</p>
    <div style="margin:24px 0;text-align:center;">
      {_btn(f"{app_url}/wallet", "Voir mon wallet", color)}
    </div>
    <p style="color:#6B7280;font-size:12px;">Référence : {order_id} · Rôle : {role}</p>
    """
    return await send_email_detailed(
        to=to_email, subject=subject,
        html=_wrap(subject, body), kind="marketplace_dispute_resolved")
