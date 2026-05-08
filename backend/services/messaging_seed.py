"""
Messaging seed — inserts canonical system templates and the pre-built
"Migration 1.0 → 4.0" draft campaign the user asked to pre-build for review.

Called once at backend startup. Safe to re-run (INSERT ... ON CONFLICT DO NOTHING).

5 system templates are seeded :
    tpl_sys_migration_1_to_4
    tpl_sys_welcome
    tpl_sys_inactive_reactivation
    tpl_sys_pro_upgrade
    tpl_sys_referral_motivation

1 draft campaign is seeded :
    cmp_sys_migration_draft  (status=draft, segment=seg_legacy_migrated)
"""
from __future__ import annotations
import json
import logging

logger = logging.getLogger(__name__)


SYSTEM_TEMPLATES: list[dict] = [
    {
        "template_id": "tpl_sys_migration_1_to_4",
        "name": "Migration JAPAP 1.0 → 4.0",
        "language": "fr",
        "category": "migration",
        "subject": "Votre compte JAPAP a été mis à jour – action requise",
        "preview_text": "Reconnectez-vous en toute sécurité à la nouvelle version de JAPAP.",
        "body_html": (
            "<p>Bonjour <strong>{{first_name}}</strong>,</p>"
            "<p>Vous aviez déjà un compte sur JAPAP, et nous tenions à vous en informer personnellement.</p>"
            "<p>Nous avons récemment effectué une évolution majeure de la plateforme, passant de "
            "<strong>JAPAP 1.0</strong> à <strong>JAPAP 4.0</strong> — une version entièrement "
            "repensée, plus rapide, plus sécurisée et enrichie de nouvelles fonctionnalités.</p>"
            "<p>Dans ce cadre, pour garantir la sécurité de vos données, votre ancien accès a été "
            "réinitialisé.</p>"
            "<p>👉 Vous devez simplement définir un nouveau mot de passe pour retrouver votre compte.</p>"
            "<div style=\"text-align:center;margin:28px 0;\">"
            "<a href=\"{{app_url}}/forgot-password\" "
            "style=\"display:inline-block;padding:14px 30px;background:#F7931A;color:#ffffff;"
            "text-decoration:none;border-radius:999px;font-weight:700;"
            "font-family:'Outfit',Arial,sans-serif;font-size:15px;\">"
            "Créer mon nouveau mot de passe</a>"
            "</div>"
            "<p>Cette nouvelle version vous permet désormais de :</p>"
            "<ul>"
            "<li>communiquer en temps réel (messagerie + appels audio/vidéo)</li>"
            "<li>gérer vos activités et tâches automatiquement</li>"
            "<li>accéder à un écosystème plus intelligent et connecté</li>"
            "</ul>"
            "<p>Nous sommes sincèrement désolés pour la gêne occasionnée par cette transition, "
            "mais elle était nécessaire pour vous offrir une expérience bien plus fiable et "
            "performante.</p>"
            "<p>Si vous avez la moindre difficulté, notre équipe reste disponible pour vous "
            "accompagner.</p>"
            "<p>À très bientôt sur JAPAP,<br/><em>L'équipe JAPAP</em></p>"
        ),
        "body_text": (
            "Bonjour {{first_name}},\n\n"
            "Vous aviez déjà un compte sur JAPAP, et nous tenions à vous en informer personnellement.\n\n"
            "Nous avons récemment effectué une évolution majeure de la plateforme, passant de "
            "JAPAP 1.0 à JAPAP 4.0 — une version entièrement repensée, plus rapide, plus sécurisée "
            "et enrichie de nouvelles fonctionnalités.\n\n"
            "Dans ce cadre, pour garantir la sécurité de vos données, votre ancien accès a été réinitialisé.\n\n"
            "Vous devez simplement définir un nouveau mot de passe pour retrouver votre compte :\n"
            "{{app_url}}/forgot-password\n\n"
            "Cette nouvelle version vous permet désormais de :\n"
            "- communiquer en temps réel (messagerie + appels audio/vidéo)\n"
            "- gérer vos activités et tâches automatiquement\n"
            "- accéder à un écosystème plus intelligent et connecté\n\n"
            "Nous sommes sincèrement désolés pour la gêne occasionnée par cette transition, mais "
            "elle était nécessaire pour vous offrir une expérience bien plus fiable et performante.\n\n"
            "Si vous avez la moindre difficulté, notre équipe reste disponible pour vous accompagner.\n\n"
            "À très bientôt sur JAPAP,\n"
            "L'équipe JAPAP\n"
        ),
        "cta_label": "",
        "cta_url": "",
    },
    {
        "template_id": "tpl_sys_welcome",
        "name": "Bienvenue sur JAPAP",
        "language": "fr",
        "category": "welcome",
        "subject": "Bienvenue {{first_name}} 👋",
        "preview_text": "On vous souhaite la bienvenue sur JAPAP.",
        "body_html": (
            "<p>Salut <strong>{{first_name}}</strong> !</p>"
            "<p>Heureux de te voir sur JAPAP. Voici par où commencer :</p>"
            "<ul>"
            "<li>💬 <strong>Messenger</strong> : discute avec tes proches, sécurisé de bout en bout</li>"
            "<li>📞 <strong>Appels audio & vidéo</strong> avec résumés IA automatiques</li>"
            "<li>📶 <strong>JAPAP Connect</strong> : partage du Wi-Fi via QR dynamique</li>"
            "<li>💸 <strong>Wallet</strong> : envoie et reçois des paiements instantanément</li>"
            "</ul>"
            "<p>Ton code de parrainage : <strong>{{first_name}}…</strong> — invite tes amis et gagne ensemble.</p>"
        ),
        "body_text": "Bienvenue {{first_name}}! Commence par configurer ton profil.",
        "cta_label": "Ouvrir JAPAP",
        "cta_url": "{{app_url}}/feed",
    },
    {
        "template_id": "tpl_sys_inactive_reactivation",
        "name": "Réactivation utilisateur inactif",
        "language": "fr",
        "category": "reactivation",
        "subject": "{{first_name}}, on vous attend depuis {{last_active_days}} jours",
        "preview_text": "Votre solde, vos filleuls et vos contacts vous attendent.",
        "body_html": (
            "<p>Bonjour <strong>{{first_name}}</strong>,</p>"
            "<p>Ça fait <strong>{{last_active_days}} jours</strong> qu'on ne vous a pas vu sur JAPAP.</p>"
            "<p>Depuis votre dernière visite :</p>"
            "<ul>"
            "<li>💬 Vos conversations sont intactes</li>"
            "<li>💰 Solde wallet : <strong>{{wallet_balance}} USD</strong></li>"
            "<li>🎁 Vous avez <strong>{{referral_count}}</strong> filleul(s) actifs</li>"
            "</ul>"
            "<p>Revenez jeter un œil — rien n'a été perdu.</p>"
        ),
        "body_text": "On vous attend depuis {{last_active_days}} jours sur JAPAP.",
        "cta_label": "Revenir sur JAPAP",
        "cta_url": "{{app_url}}/feed",
    },
    {
        "template_id": "tpl_sys_pro_upgrade",
        "name": "Invitation upgrade Pro",
        "language": "fr",
        "category": "pro",
        "subject": "{{first_name}}, débloquez tout JAPAP en passant Pro",
        "preview_text": "Connect Premium, résumés IA illimités, badge Pro.",
        "body_html": (
            "<p>Bonjour <strong>{{first_name}}</strong>,</p>"
            "<p>Vous utilisez JAPAP activement — on l'a remarqué. Passez Pro pour débloquer :</p>"
            "<ul>"
            "<li>✨ <strong>Résumés IA illimités</strong> sur tous vos appels</li>"
            "<li>📶 <strong>Connect Premium</strong> : accès aux hotspots sponsorisés</li>"
            "<li>💎 <strong>Badge Pro</strong> visible par votre réseau</li>"
            "<li>🚀 <strong>Support prioritaire</strong></li>"
            "</ul>"
            "<p>À partir de <strong>4,99 USD/mois</strong>. Annulable à tout moment.</p>"
        ),
        "body_text": "Passez Pro sur JAPAP pour débloquer les fonctionnalités avancées.",
        "cta_label": "Découvrir Pro",
        "cta_url": "{{app_url}}/pro",
    },
    {
        "template_id": "tpl_sys_referral_motivation",
        "name": "Motivation parrainage",
        "language": "fr",
        "category": "referral",
        "subject": "{{first_name}}, gagnez jusqu'à 10 USD par ami invité",
        "preview_text": "Chaque ami qui rejoint JAPAP vous rapporte directement.",
        "body_html": (
            "<p>Bonjour <strong>{{first_name}}</strong>,</p>"
            "<p>Votre réseau peut devenir une source de revenu. Pour chaque "
            "ami qui rejoint JAPAP avec votre code :</p>"
            "<ul>"
            "<li>🎁 <strong>+5 USD</strong> pour vous dès son premier login</li>"
            "<li>💫 <strong>+5 USD</strong> pour votre ami</li>"
            "<li>📈 <strong>2% à vie</strong> sur ses transactions wallet</li>"
            "</ul>"
            "<p>Vous avez déjà <strong>{{referral_count}}</strong> filleul(s). "
            "Doublez cela ce mois-ci 🚀</p>"
        ),
        "body_text": "Invitez vos amis et gagnez 5 USD par inscription.",
        "cta_label": "Inviter mes amis",
        "cta_url": "{{app_url}}/referral",
    },
]


MIGRATION_CAMPAIGN = {
    "campaign_id": "cmp_sys_migration_draft",
    "name": "Migration 1.0 → 4.0 (prêt à revue)",
    "template_id": "tpl_sys_migration_1_to_4",
    "segment_id": "seg_legacy_migrated",
    "language": "fr",
}


async def seed_system_templates_and_campaign():
    """Idempotent: ON CONFLICT DO NOTHING on both tables."""
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Templates
        for t in SYSTEM_TEMPLATES:
            await conn.execute(
                """INSERT INTO email_templates
                     (template_id, name, language, subject, preview_text, body_html,
                      body_text, cta_label, cta_url, category, source)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'system')
                   ON CONFLICT (template_id) DO NOTHING""",
                t["template_id"], t["name"], t["language"], t["subject"],
                t["preview_text"], t["body_html"], t["body_text"],
                t["cta_label"], t["cta_url"], t["category"],
            )
        # Migration campaign — only created if migration template + segment both exist
        tpl = await conn.fetchrow(
            "SELECT subject, preview_text, body_html, body_text, cta_label, cta_url, language "
            "FROM email_templates WHERE template_id = $1", MIGRATION_CAMPAIGN["template_id"])
        seg = await conn.fetchrow(
            "SELECT 1 FROM email_segments WHERE segment_id = $1", MIGRATION_CAMPAIGN["segment_id"])
        if tpl and seg:
            await conn.execute(
                """INSERT INTO email_campaigns
                     (campaign_id, name, status, template_id, subject, preview_text,
                      body_html, body_text, cta_label, cta_url, language, segment_id)
                   VALUES ($1,$2,'draft',$3,$4,$5,$6,$7,$8,$9,$10,$11)
                   ON CONFLICT (campaign_id) DO NOTHING""",
                MIGRATION_CAMPAIGN["campaign_id"], MIGRATION_CAMPAIGN["name"],
                MIGRATION_CAMPAIGN["template_id"], tpl["subject"], tpl["preview_text"],
                tpl["body_html"], tpl["body_text"], tpl["cta_label"], tpl["cta_url"],
                MIGRATION_CAMPAIGN["language"], MIGRATION_CAMPAIGN["segment_id"],
            )
    logger.info("Messaging seed complete: %d system templates + migration draft.", len(SYSTEM_TEMPLATES))
