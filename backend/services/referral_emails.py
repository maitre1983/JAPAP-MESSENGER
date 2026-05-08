"""
JAPAP — Referral transactional emails (iter110 Phase C).
========================================================

Three event-driven emails are dispatched to the referrer:
  • REFERRAL_INVITED   — when a friend signs up using their code (pending)
  • REFERRAL_ACTIVATED — when that friend gets activated (parrain credited)
  • REFERRAL_REWARDED  — when a tier reward is unlocked

In-memory + database-backed dedup ensures we never send the same event for
the same (referrer, referred, kind) twice within 24h.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from services.email_service import send_email, _logo_url

logger = logging.getLogger(__name__)

KIND_INVITED = "referral.invited"
KIND_ACTIVATED = "referral.activated"
KIND_REWARDED = "referral.rewarded"
VALID_KINDS = (KIND_INVITED, KIND_ACTIVATED, KIND_REWARDED)
DEDUP_HOURS = 24


async def ensure_referral_email_log_ddl(conn) -> None:
    """Idempotent DDL — referral_email_log table for dedup + audit trail."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS referral_email_log (
            id          BIGSERIAL PRIMARY KEY,
            kind        VARCHAR(40) NOT NULL,
            referrer_id VARCHAR(64) NOT NULL,
            referred_id VARCHAR(64),
            sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            success     BOOLEAN NOT NULL DEFAULT TRUE,
            metadata    JSONB
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_referral_email_log_dedup "
        "ON referral_email_log (kind, referrer_id, referred_id, sent_at DESC)"
    )


async def _was_sent_recently(conn, kind: str, referrer_id: str,
                             referred_id: Optional[str]) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEDUP_HOURS)
    if referred_id:
        return bool(await conn.fetchval(
            """SELECT 1 FROM referral_email_log
                WHERE kind=$1 AND referrer_id=$2 AND referred_id=$3
                  AND sent_at > $4 AND success=TRUE LIMIT 1""",
            kind, referrer_id, referred_id, cutoff,
        ))
    return bool(await conn.fetchval(
        """SELECT 1 FROM referral_email_log
            WHERE kind=$1 AND referrer_id=$2 AND referred_id IS NULL
              AND sent_at > $3 AND success=TRUE LIMIT 1""",
        kind, referrer_id, cutoff,
    ))


def _shell(title: str, body_html: str) -> str:
    logo = _logo_url()
    return f"""
    <div style="font-family:Manrope,Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;background:#F8F9FB;">
      <div style="background:#fff;padding:24px;border-radius:16px 16px 0 0;text-align:center;border:1px solid #eee;border-bottom:none;">
        <img src="{logo}" alt="JAPAP" style="height:48px;width:auto;display:inline-block;" />
      </div>
      <div style="background:#fff;padding:28px 24px;border:1px solid #eee;border-top:none;border-radius:0 0 16px 16px;">
        <h2 style="color:#0F056B;margin-top:0;font-family:'Outfit',Arial,sans-serif;font-size:22px;">{title}</h2>
        {body_html}
      </div>
      <p style="color:#9CA3AF;font-size:11px;text-align:center;margin-top:16px;">
        Vous recevez cet email parce que vous avez activé le programme de parrainage JAPAP.
      </p>
    </div>
    """


def _public_url(path: str) -> str:
    base = (os.environ.get("PUBLIC_APP_URL")
            or os.environ.get("FRONTEND_URL")
            or os.environ.get("REACT_APP_BACKEND_URL")
            or "").rstrip("/")
    if not base:
        base = "https://japap-refactor.preview.emergentagent.com"
    return f"{base}{path}"


async def _record(conn, *, kind: str, referrer_id: str,
                  referred_id: Optional[str], success: bool, meta: dict) -> None:
    import json as _json
    await conn.execute(
        """INSERT INTO referral_email_log (kind, referrer_id, referred_id, success, metadata)
              VALUES ($1, $2, $3, $4, $5::jsonb)""",
        kind, referrer_id, referred_id, success, _json.dumps(meta or {}),
    )


async def send_invited(conn, *, referrer_email: str, referrer_id: str,
                       referrer_first_name: str, referred_first_name: str,
                       referred_id: Optional[str]) -> bool:
    """Filleul vient de s'inscrire (status=pending)."""
    await ensure_referral_email_log_ddl(conn)
    if await _was_sent_recently(conn, KIND_INVITED, referrer_id, referred_id):
        return False
    if not referrer_email:
        return False
    name = (referred_first_name or "Un ami").strip() or "Un ami"
    referral_url = _public_url("/referral")
    body = f"""
        <p style="color:#374151;font-size:15px;line-height:1.6;">
          Bonjour <strong>{(referrer_first_name or '').strip() or 'parrain'}</strong>,
        </p>
        <p style="color:#374151;font-size:15px;line-height:1.6;">
          🎉 <strong>{name}</strong> vient d'utiliser votre code de parrainage pour rejoindre JAPAP.
          Dès qu'il(elle) aura terminé son onboarding, votre bonus sera crédité automatiquement.
        </p>
        <div style="text-align:center;margin:24px 0;">
          <a href="{referral_url}" style="background:#E01C2E;color:white;text-decoration:none;
             padding:14px 28px;border-radius:999px;font-weight:700;display:inline-block;
             font-family:'Outfit',Arial,sans-serif;">Voir mes filleuls</a>
        </div>
        <p style="color:#6B7280;font-size:13px;line-height:1.6;">
          Continuez à partager votre code — chaque filleul actif débloque des récompenses.
        </p>
    """
    html = _shell("Nouveau filleul inscrit !", body)
    text = (f"{name} vient de rejoindre JAPAP avec votre code. "
            f"Le bonus sera crédité dès qu'il(elle) sera actif. "
            f"Voir mes filleuls : {referral_url}")
    ok = await send_email(referrer_email, "🎉 Nouveau filleul JAPAP", html, text)
    await _record(conn, kind=KIND_INVITED, referrer_id=referrer_id,
                  referred_id=referred_id, success=ok,
                  meta={"to": referrer_email, "name": name})
    return ok


async def send_activated(conn, *, referrer_email: str, referrer_id: str,
                         referrer_first_name: str, referred_first_name: str,
                         referred_id: Optional[str], bonus_local: str,
                         bonus_currency: str) -> bool:
    """Filleul activé → bonus crédité."""
    await ensure_referral_email_log_ddl(conn)
    if await _was_sent_recently(conn, KIND_ACTIVATED, referrer_id, referred_id):
        return False
    if not referrer_email:
        return False
    name = (referred_first_name or "Votre filleul").strip() or "Votre filleul"
    wallet_url = _public_url("/wallet")
    body = f"""
        <p style="color:#374151;font-size:15px;line-height:1.6;">
          Bonjour <strong>{(referrer_first_name or '').strip() or 'parrain'}</strong>,
        </p>
        <p style="color:#374151;font-size:15px;line-height:1.6;">
          ✅ <strong>{name}</strong> est désormais actif sur JAPAP.
        </p>
        <div style="background:#ECFDF5;border:1px solid #10B981;border-radius:12px;padding:16px;text-align:center;margin:20px 0;">
          <div style="color:#047857;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;">Bonus crédité</div>
          <div style="color:#047857;font-family:'Outfit',Arial,sans-serif;font-size:28px;font-weight:800;margin-top:4px;">
            +{bonus_local} {bonus_currency}
          </div>
        </div>
        <div style="text-align:center;margin:24px 0;">
          <a href="{wallet_url}" style="background:#10B981;color:white;text-decoration:none;
             padding:14px 28px;border-radius:999px;font-weight:700;display:inline-block;
             font-family:'Outfit',Arial,sans-serif;">Voir mon wallet</a>
        </div>
    """
    html = _shell("Bonus parrainage crédité ✨", body)
    text = (f"{name} est actif. Bonus +{bonus_local} {bonus_currency} crédité. "
            f"Wallet : {wallet_url}")
    ok = await send_email(referrer_email, f"✨ +{bonus_local} {bonus_currency} de bonus parrainage",
                          html, text)
    await _record(conn, kind=KIND_ACTIVATED, referrer_id=referrer_id,
                  referred_id=referred_id, success=ok,
                  meta={"to": referrer_email, "bonus": bonus_local,
                        "currency": bonus_currency, "name": name})
    return ok


async def send_rewarded(conn, *, referrer_email: str, referrer_id: str,
                        referrer_first_name: str, tier_label: str,
                        reward_summary: str) -> bool:
    """Récompense palier débloquée."""
    await ensure_referral_email_log_ddl(conn)
    # No referred_id → dedup at level (kind, referrer, NULL).
    if await _was_sent_recently(conn, KIND_REWARDED, referrer_id, None):
        # Tighten: only suppress if the same tier_label has been sent in the
        # last DEDUP_HOURS. Different tier reward => still send.
        last = await conn.fetchrow(
            """SELECT metadata FROM referral_email_log
                WHERE kind=$1 AND referrer_id=$2 AND success=TRUE
                  AND sent_at > NOW() - INTERVAL '24 hours'
                ORDER BY sent_at DESC LIMIT 1""",
            KIND_REWARDED, referrer_id,
        )
        if last and last["metadata"]:
            try:
                import json as _json
                meta_prev = _json.loads(last["metadata"]) if isinstance(last["metadata"], str) else last["metadata"]
                if meta_prev.get("tier_label") == tier_label:
                    return False
            except Exception:
                return False
    if not referrer_email:
        return False
    referral_url = _public_url("/referral")
    body = f"""
        <p style="color:#374151;font-size:15px;line-height:1.6;">
          Bonjour <strong>{(referrer_first_name or '').strip() or 'parrain'}</strong>,
        </p>
        <p style="color:#374151;font-size:15px;line-height:1.6;">
          🏆 Vous venez de débloquer le palier <strong>{tier_label}</strong> !
        </p>
        <div style="background:#FEF3C7;border:1px solid #F59E0B;border-radius:12px;padding:16px;text-align:center;margin:20px 0;">
          <div style="color:#92400E;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;">Récompense</div>
          <div style="color:#92400E;font-family:'Outfit',Arial,sans-serif;font-size:22px;font-weight:800;margin-top:4px;">
            {reward_summary}
          </div>
        </div>
        <div style="text-align:center;margin:24px 0;">
          <a href="{referral_url}" style="background:#F59E0B;color:white;text-decoration:none;
             padding:14px 28px;border-radius:999px;font-weight:700;display:inline-block;
             font-family:'Outfit',Arial,sans-serif;">Voir mes paliers</a>
        </div>
        <p style="color:#6B7280;font-size:13px;line-height:1.6;">
          Continuez à inviter — d'autres paliers vous attendent.
        </p>
    """
    html = _shell(f"Palier {tier_label} débloqué 🏆", body)
    text = f"Vous avez débloqué : {tier_label} ({reward_summary}). Mes paliers : {referral_url}"
    ok = await send_email(referrer_email, f"🏆 Palier débloqué : {tier_label}",
                          html, text)
    await _record(conn, kind=KIND_REWARDED, referrer_id=referrer_id,
                  referred_id=None, success=ok,
                  meta={"to": referrer_email, "tier_label": tier_label,
                        "reward_summary": reward_summary})
    return ok


__all__ = [
    "ensure_referral_email_log_ddl",
    "send_invited", "send_activated", "send_rewarded",
    "KIND_INVITED", "KIND_ACTIVATED", "KIND_REWARDED",
]
