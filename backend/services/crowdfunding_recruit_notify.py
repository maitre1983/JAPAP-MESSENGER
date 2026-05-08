"""
JAPAP — Crowdfunding recruiter tier notifications (iter170)
============================================================
Dispatches in-app + email notifications when a user earns a new
recruiter tier badge (Bronze, Silver, Gold, Platinum).

CEO rules respected:
  • Badges + XP only — copy never mentions cash.
  • Idempotent: duplicate dispatch is silently dropped via the
    UNIQUE (user_id, cycle_id, tier) constraint on the badges table
    *upstream* of this function — the caller passes only NEWLY
    awarded tiers from `award_tier_badges()`.
"""
import logging
import uuid

from utils.public_url import public_url
from services.email_service import send_email

logger = logging.getLogger(__name__)


# Mirrors RECRUITER_TIERS from crowdfunding_recruit_service.py — kept in
# sync manually because importing the constant would create a circular
# dependency (service ← notify ← service).
TIER_COPY = {
    "bronze":   {"label": "Bronze",   "emoji": "🥉",
                 "next": ("silver", 10),  "xp": 50},
    "silver":   {"label": "Silver",   "emoji": "🥈",
                 "next": ("gold", 25),    "xp": 150},
    "gold":     {"label": "Gold",     "emoji": "🥇",
                 "next": ("platinum", 50),"xp": 400},
    "platinum": {"label": "Platine",  "emoji": "💎",
                 "next": (None, None),    "xp": 1000},
}


def _email_html(first_name: str, tier_key: str, recruits_count: int,
                cta_url: str) -> str:
    info = TIER_COPY.get(tier_key, TIER_COPY["bronze"])
    next_key, next_threshold = info["next"]
    next_block = ""
    if next_key:
        missing = max(0, next_threshold - recruits_count)
        next_label = TIER_COPY[next_key]["label"]
        next_block = (
            f"<p style='color:#555;font-size:14px;margin-top:16px;'>"
            f"Encore <b>{missing}</b> recrue{'s' if missing > 1 else ''} pour passer "
            f"<b>{next_label}</b> et débloquer encore plus de XP.</p>"
        )
    else:
        next_block = (
            "<p style='color:#a16207;font-size:14px;margin-top:16px;font-weight:600;'>"
            "🏆 Palier maximum atteint. Tu es une légende JAPAP.</p>"
        )

    return f"""
    <div style="font-family:Manrope,Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;background:#fafafa;">
      <div style="background:linear-gradient(135deg,#4338ca,#a21caf,#e11d48);color:white;padding:32px 24px;border-radius:16px 16px 0 0;text-align:center;">
        <div style="font-size:48px;margin-bottom:8px;">{info['emoji']}</div>
        <h1 style="margin:0;font-size:24px;font-family:'Outfit',Arial,sans-serif;">
          Palier {info['label']} débloqué !
        </h1>
      </div>
      <div style="background:#fff;padding:28px 24px;border-radius:0 0 16px 16px;border:1px solid #eee;border-top:none;">
        <p style="color:#111;font-size:16px;margin-top:0;">
          Bravo {first_name or 'champion'} 🎉
        </p>
        <p style="color:#444;font-size:14px;line-height:1.6;">
          Tu viens d'atteindre le palier <b>{info['label']}</b> du Programme
          Recruteur Crowdfunding avec <b>{recruits_count}</b> recrue{'s' if recruits_count > 1 else ''}
          ce cycle. <b>+{info['xp']} XP</b> ajoutés à ton profil.
        </p>
        <div style="background:#fef3c7;border-left:4px solid #f59e0b;padding:12px 16px;border-radius:8px;margin:16px 0;color:#78350f;font-size:13px;">
          <b>Rappel :</b> les badges et XP sont des récompenses symboliques.
          Aucun cash n'est versé pour le parrainage — c'est ce qui garde
          l'écosystème sain.
        </div>
        {next_block}
        <div style="text-align:center;margin-top:24px;">
          <a href="{cta_url}" style="display:inline-block;background:#e11d48;color:white;
                  text-decoration:none;font-weight:700;padding:12px 24px;
                  border-radius:9999px;font-size:14px;">
            Voir le classement
          </a>
        </div>
        <p style="color:#999;font-size:11px;text-align:center;margin-top:24px;">
          JAPAP Messenger · Programme Recruteur Crowdfunding
        </p>
      </div>
    </div>
    """


def _email_text(first_name: str, tier_key: str, recruits_count: int,
                cta_url: str) -> str:
    info = TIER_COPY.get(tier_key, TIER_COPY["bronze"])
    return (
        f"Bravo {first_name or ''} ! Tu viens de débloquer le palier "
        f"{info['label']} {info['emoji']} sur JAPAP avec {recruits_count} "
        f"recrues ce cycle. +{info['xp']} XP. "
        f"Rappel : badges/XP uniquement, pas de cash. "
        f"Classement : {cta_url}"
    )


async def notify_tier_awarded(conn, *, user_id: str, cycle_id: str,
                              tier_key: str) -> dict:
    """Dispatch in-app + email notif. Idempotent against double-dispatch
    via a unique key on `notifications.notif_id` (we derive a stable id
    from user+cycle+tier so retries collapse).

    Returns: {sent_inapp: bool, sent_email: bool, email_to: str|None}
    """
    info = TIER_COPY.get(tier_key)
    if not info:
        return {"sent_inapp": False, "sent_email": False, "email_to": None}

    # Idempotent in-app notif id: hash of stable triple.
    notif_id = f"cfrec_{user_id[:16]}_{tier_key}_{str(cycle_id)[:18]}"

    # Pull recipient info (email, first_name, recruits count for body).
    user = await conn.fetchrow(
        "SELECT email, first_name FROM users WHERE user_id = $1", user_id)
    if not user:
        return {"sent_inapp": False, "sent_email": False, "email_to": None}

    count = int(await conn.fetchval(
        "SELECT COUNT(*) FROM crowdfunding_recruit_credits "
        "WHERE inviter_id = $1 AND cycle_id = $2",
        user_id, cycle_id,
    ) or 0)

    title = f"{info['emoji']} Palier {info['label']} débloqué"
    msg = (f"Bravo ! Tu es maintenant recruteur {info['label']} sur JAPAP "
           f"({count} recrue{'s' if count > 1 else ''} ce cycle, +{info['xp']} XP).")

    # In-app notif (idempotent via UNIQUE notif_id).
    sent_inapp = False
    try:
        result = await conn.execute(
            """INSERT INTO notifications
                 (notif_id, user_id, type, title, message, data)
               VALUES ($1, $2, 'crowdfunding_tier_awarded', $3, $4, $5)
               ON CONFLICT (notif_id) DO NOTHING""",
            notif_id, user_id, title, msg,
            f'{{"tier":"{tier_key}","cycle_id":"{cycle_id}","recruits_count":{count},"xp":{info["xp"]}}}',
        )
        sent_inapp = not result.endswith(" 0")
    except Exception as e:
        logger.warning(f"[recruit-notif] in-app insert failed user={user_id} tier={tier_key}: {e}")

    # Email — best-effort (do not fail caller on Resend errors).
    sent_email = False
    cta = public_url("/crowdfunding/leaderboard")
    try:
        if user["email"] and "@" in user["email"]:
            html = _email_html(user["first_name"] or "", tier_key, count, cta)
            text = _email_text(user["first_name"] or "", tier_key, count, cta)
            sent_email = await send_email(
                to=user["email"],
                subject=f"{info['emoji']} JAPAP — Palier {info['label']} débloqué !",
                html=html, text=text, kind="crowdfunding_tier",
            )
    except Exception as e:
        logger.warning(f"[recruit-notif] email failed user={user_id} tier={tier_key}: {e}")

    return {
        "sent_inapp": sent_inapp,
        "sent_email": sent_email,
        "email_to": user["email"],
    }
