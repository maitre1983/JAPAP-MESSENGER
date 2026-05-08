"""
JAPAP — KYC email notifications (iter173)
==========================================
Branded French emails sent when admin approves or rejects a KYC.
Uses the centralised public_url helper so links never leak the
preview-environment domain in production.
"""
import logging
from html import escape

from services.email_service import send_email
from utils.public_url import public_url

logger = logging.getLogger(__name__)


def _wallet_url() -> str:
    return public_url("/wallet")


def _kyc_url() -> str:
    return public_url("/wallet#kyc")


def _logo_block(title: str, gradient: str) -> str:
    return f"""
    <div style="background:{gradient};color:white;padding:32px 24px;
                border-radius:16px 16px 0 0;text-align:center;">
      <h1 style="margin:0;font-size:22px;font-family:'Outfit',Arial,sans-serif;">
        {escape(title)}
      </h1>
    </div>
    """


def _approved_html(first_name: str, wallet_url: str) -> str:
    name = escape(first_name.strip()) if first_name and first_name.strip() else "champion"
    return f"""<!DOCTYPE html><html><body style="margin:0;padding:24px;background:#f9fafb;font-family:Arial,sans-serif;">
      <div style="max-width:480px;margin:auto;">
        {_logo_block("✅ Identité vérifiée — Bienvenue !",
                     "linear-gradient(135deg,#3B82F6 0%,#22C55E 100%)")}
        <div style="background:#fff;padding:28px 24px;border:1px solid #eee;
                    border-top:none;border-radius:0 0 16px 16px;">
          <p style="color:#111;font-size:16px;margin-top:0;">
            Bonjour {name} 👋
          </p>
          <p style="color:#374151;font-size:14px;line-height:1.6;">
            Votre vérification d'identité (KYC) vient d'être <b>approuvée</b>
            par notre équipe. Vous pouvez désormais :
          </p>
          <ul style="color:#374151;font-size:14px;line-height:1.7;padding-left:20px;">
            <li>Effectuer des <b>retraits</b> depuis votre wallet JAPAP</li>
            <li>Afficher le badge <b>« ✅ Identité vérifiée »</b> sur votre profil
                public — un vrai gain de confiance pour vos acheteurs</li>
            <li>Profiter des fonctionnalités fintech avancées (jeux payants,
                investissements crypto…)</li>
          </ul>
          <p style="text-align:center;margin:28px 0 12px;">
            <a href="{wallet_url}" style="display:inline-block;padding:12px 28px;
                  background:#22C55E;color:white;text-decoration:none;
                  border-radius:9999px;font-weight:700;font-size:14px;">
              Aller à mon wallet
            </a>
          </p>
          <p style="color:#9ca3af;font-size:11px;text-align:center;margin-top:24px;">
            JAPAP Messenger · Une équipe est dédiée à la conformité de votre compte.
          </p>
        </div>
      </div>
    </body></html>"""


def _approved_text(first_name: str, wallet_url: str) -> str:
    name = first_name.strip() or "champion"
    return (f"Bonjour {name},\n\n"
            "Votre vérification d'identité (KYC) JAPAP a été APPROUVÉE.\n"
            "Vous pouvez maintenant effectuer des retraits et afficher le "
            "badge « ✅ Identité vérifiée » sur votre profil.\n\n"
            f"Wallet : {wallet_url}\n\n"
            "L'équipe JAPAP")


def _rejected_html(first_name: str, reason: str, kyc_url: str) -> str:
    name = escape(first_name.strip()) if first_name and first_name.strip() else "ami(e)"
    safe_reason = escape(reason or "").replace("\n", "<br>")
    return f"""<!DOCTYPE html><html><body style="margin:0;padding:24px;background:#f9fafb;font-family:Arial,sans-serif;">
      <div style="max-width:480px;margin:auto;">
        {_logo_block("Vérification d'identité refusée",
                     "linear-gradient(135deg,#F97316 0%,#DC2626 100%)")}
        <div style="background:#fff;padding:28px 24px;border:1px solid #eee;
                    border-top:none;border-radius:0 0 16px 16px;">
          <p style="color:#111;font-size:16px;margin-top:0;">
            Bonjour {name},
          </p>
          <p style="color:#374151;font-size:14px;line-height:1.6;">
            Nous n'avons malheureusement pas pu approuver votre demande de
            vérification d'identité (KYC). Cela ne remet pas en cause votre
            compte — vous pouvez resoumettre une nouvelle demande dès maintenant.
          </p>
          <div style="background:#FEF2F2;border-left:4px solid #DC2626;
                      padding:12px 14px;border-radius:8px;margin:16px 0;
                      color:#991B1B;font-size:13px;">
            <b>Motif :</b><br>{safe_reason}
          </div>
          <p style="color:#374151;font-size:14px;line-height:1.6;">
            <b>Conseils pour la prochaine soumission :</b>
          </p>
          <ul style="color:#374151;font-size:13px;line-height:1.7;padding-left:20px;">
            <li>Photo nette, sans flou ni reflet</li>
            <li>Bonne lumière naturelle, pas de contre-jour</li>
            <li>Toutes les informations lisibles : nom, numéro, date d'expiration</li>
            <li>Selfie : visage ET pièce d'identité dans le même cadre</li>
            <li>CNI / Permis : recto <b>ET</b> verso obligatoires</li>
          </ul>
          <p style="text-align:center;margin:28px 0 12px;">
            <a href="{kyc_url}" style="display:inline-block;padding:12px 28px;
                  background:#0F056B;color:white;text-decoration:none;
                  border-radius:9999px;font-weight:700;font-size:14px;">
              Resoumettre mon KYC
            </a>
          </p>
          <p style="color:#9ca3af;font-size:11px;text-align:center;margin-top:24px;">
            JAPAP Messenger · Si vous pensez qu'il s'agit d'une erreur,
            contactez le support depuis l'application.
          </p>
        </div>
      </div>
    </body></html>"""


def _rejected_text(first_name: str, reason: str, kyc_url: str) -> str:
    name = first_name.strip() or "ami(e)"
    return (f"Bonjour {name},\n\n"
            "Votre demande de vérification d'identité JAPAP a été REFUSÉE.\n\n"
            f"Motif : {reason}\n\n"
            "Vous pouvez resoumettre une nouvelle demande dès maintenant.\n"
            f"Lien : {kyc_url}\n\n"
            "L'équipe JAPAP")


async def send_kyc_approved_email(*, to: str, first_name: str = "") -> bool:
    if not to or "@" not in to:
        return False
    wallet_url = _wallet_url()
    return await send_email(
        to,
        "✅ JAPAP — Identité vérifiée, vous pouvez retirer",
        _approved_html(first_name, wallet_url),
        _approved_text(first_name, wallet_url),
        kind="kyc",
    )


async def send_kyc_rejected_email(*, to: str, first_name: str = "",
                                   reason: str = "") -> bool:
    if not to or "@" not in to:
        return False
    kyc_url = _kyc_url()
    return await send_email(
        to,
        "JAPAP — Vérification d'identité refusée",
        _rejected_html(first_name, reason, kyc_url),
        _rejected_text(first_name, reason, kyc_url),
        kind="kyc",
    )
