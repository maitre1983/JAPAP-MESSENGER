"""
JAPAP — Centralised Admin Settings
==================================
Key/value store backed by the `admin_settings` table. Used across the system
to gate features (withdrawals, games, KYC requirement, integrations...).

All values are stored as TEXT. Helpers coerce to bool/int/float/json on read.

Performance: get_setting() results are cached in-memory for 60 seconds.
This divides the per-request DB load by 10-20× for hot endpoints like
/wheel/status that read multiple settings on every call.
set_setting() invalidates the cache entry on write, so admin changes
propagate within the same request.
"""
import json
import logging
import time
from typing import Any, Optional
from database import get_pool

logger = logging.getLogger(__name__)

# ── In-memory TTL cache ─────────────────────────────────────────────────
# {key: (value, expires_at_epoch)}
_SETTINGS_CACHE: dict[str, tuple[Optional[str], float]] = {}
_SETTINGS_CACHE_TTL = 60.0  # seconds

def _cache_get(key: str) -> tuple[bool, Optional[str]]:
    entry = _SETTINGS_CACHE.get(key)
    if entry is None:
        return False, None
    value, expires_at = entry
    if time.time() >= expires_at:
        _SETTINGS_CACHE.pop(key, None)
        return False, None
    return True, value

def _cache_set(key: str, value: Optional[str]) -> None:
    _SETTINGS_CACHE[key] = (value, time.time() + _SETTINGS_CACHE_TTL)

def _cache_invalidate(key: str) -> None:
    _SETTINGS_CACHE.pop(key, None)

# Default settings seeded on boot. New keys must be added here.
DEFAULTS: dict[str, str] = {
    # Withdraw gating
    "withdraw_enabled": "true",
    "withdraw_disabled_message": "Les retraits sont momentanément suspendus. Réessayez plus tard.",
    "kyc_required_for_withdraw": "true",
    "withdraw_min_amount_usd": "1.00",
    # KYC gating for games
    "kyc_required_for_paid_games": "true",
    # Currency display
    "currency_detection_enabled": "true",
    "currency_force": "",  # If set (e.g. "USD"), overrides all IP detection
    "base_currency": "USD",
    # JAPAP Spin
    "spin_enabled": "true",
    "spin_is_paid": "false",
    "spin_cost_xaf": "0",
    "spin_max_daily_plays": "3",
    "spin_rewards_json": '[{"amount":0,"weight":40},{"amount":10,"weight":20},{"amount":25,"weight":15},{"amount":50,"weight":12},{"amount":100,"weight":8},{"amount":250,"weight":4},{"amount":500,"weight":1}]',
    "spin_daily_cap_xaf": "2000",
    # External payment APIs (keys stored elsewhere; these are toggles)
    "nowpayments_enabled": "false",
    "hubtel_enabled": "false",
    "metamask_enabled": "false",
    "trustwallet_enabled": "false",
    # System-wide module toggles
    "marketplace_enabled": "true",
    "crowdfunding_enabled": "true",
    "games_enabled": "true",
    "referral_enabled": "true",
    "crypto_enabled": "false",        # admin requested OFF for now
    "ads_enabled": "true",            # NEW: Publicités (sponsored posts/reels/banners)
    "offers_enabled": "true",         # NEW: Offres d'emploi (Jobs extension)
    "verified_seller_badge_enabled": "true",  # iter176 — Marketplace badge KYC vendor
    # iter176 — Marketplace Escrow (canonical wallet USD only, NO PSP)
    "mkt_escrow_enabled": "true",
    "mkt_escrow_commission_percent": "2",       # seller-side fee (% of order amount)
    "mkt_escrow_auto_release_days": "7",        # auto-release if buyer silent
    "mkt_escrow_dispute_enabled": "true",
    "mkt_escrow_treasury_account": "japap_treasury",  # virtual ledger account
    # iter176 — Marketplace Sponsored Boosts (wallet USD only)
    "mkt_boost_enabled": "true",
    "mkt_boost_price_24h": "1",                 # USD
    "mkt_boost_price_7d": "5",
    "mkt_boost_price_homepage": "10",
    "mkt_boost_homepage_days": "30",
    # iter179 — Audience targeting toggles
    "targeting_enabled":      "true",
    "allow_country_filter":   "true",
    "allow_age_filter":       "true",
    # iter237x — Per-module label badges (free text, empty = no badge).
    # Editable from the admin Modules tab; surfaced via /api/settings/public.
    # Keep ads default empty since the module is now LIVE.
    "module_ads_badge":         "",
    "module_offers_badge":      "Nouveau",
    "module_jobs_badge":        "",
    "module_crypto_badge":      "",
    "module_transport_badge":   "Active",
    # iter180 — Ads Console
    "ads_enabled":            "true",
    "default_cpm_rate":       "2.0",        # USD per 1000 impressions
    "default_cpc_rate":       "0.10",       # USD per click
    "min_campaign_budget":    "5.0",
    "max_campaign_duration_days": "30",
    "ads_feed_slot_every":    "5",          # inject sponsored every N products
    # iter181 — Marketplace AI Image Generation/Enhancement (Nano Banana)
    "mkt_ai_images_enabled":       "true",
    "mkt_ai_images_daily_quota":   "3",             # free generations per user per day
    "mkt_ai_images_model":         "gemini-3.1-flash-image-preview",
    "mkt_ai_bg_presets":           "studio_white,studio_black,lifestyle,outdoor,luxury,marble",
    "mkt_ai_auto_photo_enabled":   "true",
    "mkt_ai_auto_photo_presets":   "studio_white,lifestyle,marble,luxury",   # 4 angles fiche premium
    # iter185 — Viral share loop (UTM tracking + JAPAP points reward)
    "viral_share_enabled":             "true",
    "viral_share_points_per_visit":    "50",       # pts JAPAP per unique referred visitor
    "viral_share_daily_cap_per_sharer": "20",      # max rewarded visits / sharer / day
    "viral_share_dedup_hours":         "24",       # window for "unique visitor" by IP+UA
    # iter186 — Push notifications aux paliers viraux (Pinterest playbook)
    "viral_milestones_enabled":   "true",
    "viral_milestones_thresholds": "1,5,10,25,50,100,250,500,1000",
    # iter189 — Seller reminders (Vinted playbook : x2 conversion)
    "seller_reminders_enabled":         "true",
    "seller_reminder_push_minutes":     "15",
    "seller_reminder_email_hours":      "24",
    "seller_reminder_email_max_intents": "3",
    "transport_enabled": "true",
    "transport_driver_kyc_required": "true",
    "transport_driver_emergency_phone_required": "true",
    # iter133 — Rider cancel grace period after a driver has ACCEPTED.
    # Set to 0 to disable. Recommended: 60s so drivers get a fair chance
    # to start moving toward the pickup.
    "transport_rider_cancel_after_seconds": "60",
    # Pro — conversion optimisation (iter31)
    "pro_urgency_enabled": "false",
    "pro_urgency_title": "🔥 Offre limitée — -25% sur l'annuel",
    "pro_urgency_subtitle": "Passez Pro avant la fin du mois, verrouillez le tarif à vie",
    "pro_urgency_ends_at": "",          # ISO datetime, vide = pas de countdown
    "pro_urgency_discount_pct": "25",
    "pro_ab_variant": "A",              # "A" (prix standards) | "B" (-20%)
    "pro_smart_upsell_enabled": "true",
    "pro_smart_upsell_threshold": "3",  # Nombre d'actions bloquées avant upsell
    # FOMO launch combo (iter32)
    "pro_first_1000_enabled": "true",
    "pro_first_1000_cap": "1000",
    "pro_first_1000_title": "🔒 Prix bloqué à vie — pour les 1000 premiers abonnés",
    "pro_first_1000_subtitle": "Souscrivez maintenant, conservez ce tarif pour toujours même si nous augmentons les prix.",
    "top_hosts_seed_enabled": "true",
    "pro_launch_mode_enabled": "true",
    "pro_launch_threshold": "2",        # Upsell threshold for new users (first week)
    "pro_launch_new_user_days": "7",
    # Email branding (iter66-logo) — empty string falls back to backend-served logo
    "email_logo_url": "",
    # Messaging Batch Scale (iter82) — hard kill-switch stays FALSE by default
    # until an admin explicitly turns it on in the Batch & Safety panel.
    "messaging_real_send_enabled": "false",  # iter66-safeguards kill-switch
    "messaging_max_audience_per_campaign": "1000",  # iter66-controlled-release: hard cap
    "messaging_worker_rate_per_minute": "60",     # worker throughput (emails/min)
    "messaging_batch_size": "25",                  # max rows drained per poll
    # iter83 — Roue de la fortune v2
    "wheel_enabled": "true",
    "wheel_turnstile_enabled": "false",
    "wheel_config_json": (
        '{"max_spins_per_day": 5, "cooldown_seconds": 30, '
        '"jackpot_odds_in_window": 30, "near_miss_odds": 20, '
        '"streak_3_days": 3, "streak_3_bonus": 50, '
        '"streak_7_days": 7, "streak_7_bonus": 150, '
        '"streak_15_days": 15, "streak_15_bonus": 400}'
    ),
    # Wallet — deposits / withdrawals (iter32)
    "deposit_usdt_trc20_enabled": "true",
    "deposit_usdt_bep20_enabled": "true",
    "deposit_hubtel_card_enabled": "true",
    "deposit_address_usdt_trc20": "",        # admin fills the official wallet addresses
    "deposit_address_usdt_bep20": "",
    "deposit_min_amount_usd": "1",
    "deposits_enabled": "true",               # master switch for all deposits
    "deposit_disabled_message": "Les dépôts sont temporairement suspendus. Réessayez plus tard.",
    "withdraw_usdt_trc20_enabled": "true",
    "withdraw_usdt_bep20_enabled": "true",
    # Withdraw mode: admin chooses manual, auto, or both (iter36)
    "manual_withdraw_enabled": "true",
    "auto_withdraw_enabled": "false",         # auto requires configured SDK (NowPayments later)
    "auto_withdraw_unavailable_message": "Le retrait automatique n'est pas encore configuré. Il sera traité manuellement.",
    # Payment gateway credentials (iter41 — admin-configurable)
    # Stored as plain strings in admin_settings but filtered out of the public
    # /api/settings endpoint and exposed masked in the admin UI.
    "hubtel_client_id": "",
    "hubtel_client_secret": "",
    "hubtel_merchant_account": "",
    "hubtel_webhook_secret": "",
    "hubtel_environment": "sandbox",     # "sandbox" | "production"
    # iter195 — CEO : URLs de rappel Hubtel configurables côté admin (clone EAA).
    # Le backend injecte ces URLs dans POST /items/initiate à chaque dépôt.
    # - callback : backend webhook qui crédite le wallet (POST)
    # - return   : page frontend où Hubtel redirige l'utilisateur après paiement (GET)
    "hubtel_callback_url_override": "https://japapmessenger.com/api/payments/hubtel/callback",
    "hubtel_return_url_override":   "https://japapmessenger.com/api/payments/hubtel/return/success",
    "hubtel_cancel_url_override":   "https://japapmessenger.com/api/payments/hubtel/return/cancelled",
    # iter207 — EAA-style config: min/max deposit + fee. Per CEO clone directive.
    "hubtel_min_deposit_usd": "1",
    "hubtel_max_deposit_usd": "10000",
    "hubtel_fee_percent":     "1.5",
    "nowpayments_api_key": "",
    "nowpayments_ipn_secret": "",
    "nowpayments_environment": "sandbox",  # "sandbox" | "production"
    # LiveKit Cloud (Sprint B/C/D — audio/video/group calls + recording)
    "livekit_api_key": "",
    "livekit_api_secret": "",
    "livekit_ws_url": "",                  # e.g. wss://japap-xxx.livekit.cloud
    # Cloudflare R2 (Sprint D — call recordings storage, S3-compatible)
    "r2_account_id": "",
    "r2_access_key_id": "",
    "r2_secret_access_key": "",
    "r2_bucket": "japap-recordings",
    "r2_public_base_url": "",              # optional CDN base (https://recordings.japap.com)
    "withdraw_fee_mode": "percent",           # "percent" | "flat"
    "withdraw_fee_value": "2",                # 2% OR 2 USDT depending on mode
    # Per-plan fee overrides (iter33). Free users fall back to withdraw_fee_*.
    # Each entry: {"mode": "percent"|"flat", "value": number}.
    "withdraw_fee_by_plan_json": '{"starter":{"mode":"percent","value":5},"creator":{"mode":"percent","value":3},"business":{"mode":"percent","value":1}}',
    "withdraw_min_amount_usd": "5",
    "withdraw_enabled": "true",
    # JAPAP PRO
    "pro_enabled": "true",
    "pro_trial_enabled": "true",
    "pro_trial_days": "30",
    "pro_trial_plans": "all",  # "all" or comma-separated plan_ids like "starter,creator"
    "pro_duration_1m_enabled": "true",
    "pro_duration_3m_enabled": "true",
    "pro_duration_12m_enabled": "true",
    "pro_discount_3m_pct": "5",
    "pro_discount_12m_pct": "25",
    # REFERRALS (advanced)
    "referral_referrer_bonus_usd": "0.50",
    "referral_referee_bonus_usd": "0.25",
    "referral_activation_requires_otp": "true",
    "referral_activation_requires_action": "true",
    "referral_tiers_json": '[{"count":3,"reward_type":"pro","reward_value":30,"label":"1 mois Pro"},{"count":10,"reward_type":"pro","reward_value":90,"label":"3 mois Pro"},{"count":25,"reward_type":"wallet","reward_value":20,"label":"20 USD bonus"}]',
    "referral_tier_plan_id": "starter",  # which Pro plan to grant for tier rewards of type "pro"
    "referral_leaderboard_enabled": "true",
    "referral_leaderboard_window": "weekly",  # weekly | all_time
    "referral_gamification_enabled": "true",
    "referral_max_per_ip_per_day": "3",
    "referral_max_per_device_per_day": "3",
    "referral_reminder_enabled": "true",
    "referral_reminder_delay_days": "7",
    # REFERRAL BOOST EVENTS
    "boost_enabled": "false",
    "boost_name": "Weekend Boost",
    "boost_start_at": "",  # ISO datetime; empty = disabled window
    "boost_end_at": "",
    "boost_multiplier": "2.0",
    "boost_applies_to_referrer": "true",
    "boost_applies_to_referee": "true",
    "boost_applies_to_tiers": "false",
    # JAPAP CONNECT — WiFi Rewards
    "connect_enabled": "true",
    "connect_reward_per_connection_usd": "0.05",
    "connect_reward_per_minute_usd": "0.002",
    "connect_min_session_seconds": "60",
    "connect_max_reward_per_session_usd": "0.50",
    "connect_max_connections_per_ip_per_day": "5",
    "connect_max_connections_per_device_per_day": "5",
    "connect_max_connections_per_user_per_hotspot_per_day": "1",
    "connect_pro_required_to_share": "false",
    "connect_pro_reward_multiplier": "1.5",
    "connect_gamification_enabled": "true",
    "connect_search_radius_km": "5",
    # JAPAP INTERNET NETWORK — tier gating & revenue share
    "connect_allow_public": "true",
    "connect_min_pro_to_access": "starter",
    "connect_min_pro_to_share": "business",
    "connect_pro_bypass_user_caps": "true",
    "connect_pro_bypass_cap_per_day": "20",
    "connect_points_per_connection": "10",
    "connect_points_per_minute": "1",
    "connect_revshare_pro_enabled": "true",
    "connect_revshare_pct": "2.0",
    "connect_revshare_attribution_hours": "720",
    "connect_revshare_cap_per_month_usd": "100",
    "connect_badge_connector_threshold": "5",
    "connect_badge_provider_threshold": "10",
    "connect_badge_ambassador_threshold": "500",
}

# Keys that are safe to expose publicly (no secrets). Explicit allow-list.
PUBLIC_KEYS = {
    "withdraw_enabled", "withdraw_disabled_message", "kyc_required_for_withdraw",
    "withdraw_min_amount_usd", "kyc_required_for_paid_games",
    "currency_detection_enabled", "currency_force", "base_currency",
    "spin_enabled", "spin_is_paid", "spin_cost_xaf", "spin_max_daily_plays",
    "spin_rewards_json", "spin_daily_cap_xaf",
    "nowpayments_enabled", "hubtel_enabled", "metamask_enabled", "trustwallet_enabled",
    "marketplace_enabled", "crowdfunding_enabled", "games_enabled", "referral_enabled",
    "crypto_enabled", "ads_enabled", "offers_enabled", "verified_seller_badge_enabled",
    # iter237x — admin-editable badges
    "module_ads_badge", "module_offers_badge", "module_jobs_badge",
    "module_crypto_badge", "module_transport_badge",
    "mkt_escrow_enabled", "mkt_escrow_commission_percent", "mkt_escrow_auto_release_days",
    "mkt_escrow_dispute_enabled",
    "mkt_boost_enabled", "mkt_boost_price_24h", "mkt_boost_price_7d",
    "mkt_boost_price_homepage", "mkt_boost_homepage_days",
    "targeting_enabled", "allow_country_filter", "allow_age_filter",
    "ads_enabled", "default_cpm_rate", "default_cpc_rate",
    "min_campaign_budget", "max_campaign_duration_days", "ads_feed_slot_every",
    "mkt_ai_images_enabled", "mkt_ai_images_daily_quota", "mkt_ai_bg_presets",
    "mkt_ai_auto_photo_enabled", "mkt_ai_auto_photo_presets",
    "viral_share_enabled", "viral_share_points_per_visit",
    "viral_share_daily_cap_per_sharer", "viral_share_dedup_hours",
    "viral_milestones_enabled", "viral_milestones_thresholds",
    "seller_reminders_enabled", "seller_reminder_push_minutes",
    "seller_reminder_email_hours", "seller_reminder_email_max_intents",
    "transport_enabled", "transport_driver_kyc_required", "transport_driver_emergency_phone_required",
    "pro_urgency_enabled", "pro_urgency_title", "pro_urgency_subtitle", "pro_urgency_ends_at",
    "pro_urgency_discount_pct", "pro_ab_variant",
    "pro_smart_upsell_enabled", "pro_smart_upsell_threshold",
    "pro_first_1000_enabled", "pro_first_1000_cap", "pro_first_1000_title", "pro_first_1000_subtitle",
    "top_hosts_seed_enabled", "pro_launch_mode_enabled", "pro_launch_threshold",
    "pro_enabled", "pro_trial_enabled", "pro_trial_days", "pro_trial_plans",
    "pro_duration_1m_enabled", "pro_duration_3m_enabled", "pro_duration_12m_enabled",
    "pro_discount_3m_pct", "pro_discount_12m_pct",
    "referral_referrer_bonus_usd", "referral_referee_bonus_usd",
    "referral_tiers_json", "referral_leaderboard_enabled", "referral_leaderboard_window",
    "referral_gamification_enabled", "referral_reminder_enabled",
    "boost_enabled", "boost_name", "boost_start_at", "boost_end_at",
    "boost_multiplier", "boost_applies_to_referrer", "boost_applies_to_referee",
    "boost_applies_to_tiers",
    "connect_enabled", "connect_reward_per_connection_usd", "connect_reward_per_minute_usd",
    "connect_min_session_seconds", "connect_max_reward_per_session_usd",
    "connect_pro_required_to_share", "connect_pro_reward_multiplier",
    "connect_gamification_enabled", "connect_search_radius_km",
    "connect_allow_public", "connect_min_pro_to_access", "connect_min_pro_to_share",
    "connect_pro_bypass_user_caps", "connect_revshare_pro_enabled", "connect_revshare_pct",
    "connect_badge_connector_threshold", "connect_badge_provider_threshold", "connect_badge_ambassador_threshold",
}


async def seed_defaults() -> None:
    """Insert any missing default settings. Idempotent."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        for k, v in DEFAULTS.items():
            await conn.execute("""
                INSERT INTO admin_settings (key, value) VALUES ($1, $2)
                ON CONFLICT (key) DO NOTHING
            """, k, v)


async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    # Fast path: in-memory cache (60s TTL).
    hit, cached = _cache_get(key)
    if hit:
        return cached if cached is not None else (DEFAULTS.get(key, default))
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM admin_settings WHERE key = $1", key)
    value = row["value"] if row is not None else None
    _cache_set(key, value)
    if value is not None:
        return value
    if key in DEFAULTS:
        return DEFAULTS[key]
    return default


async def get_bool(key: str, default: bool = False) -> bool:
    v = await get_setting(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("true", "1", "yes", "on")


async def get_int(key: str, default: int = 0) -> int:
    v = await get_setting(key)
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


async def get_float(key: str, default: float = 0.0) -> float:
    v = await get_setting(key)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


async def get_json(key: str, default: Any = None) -> Any:
    v = await get_setting(key)
    if v is None:
        return default
    try:
        return json.loads(v)
    except Exception:
        return default


async def get_all() -> dict[str, str]:
    """Fetch every setting. Missing defaults are merged in."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM admin_settings")
    out = dict(DEFAULTS)
    for r in rows:
        out[r["key"]] = r["value"]
    return out


async def get_public() -> dict[str, str]:
    """Only settings safe to expose to unauthenticated callers."""
    all_s = await get_all()
    return {k: v for k, v in all_s.items() if k in PUBLIC_KEYS}


async def set_setting(key: str, value: Any) -> None:
    # Coerce any type to string for storage
    if isinstance(value, bool):
        str_val = "true" if value else "false"
    elif isinstance(value, (dict, list)):
        str_val = json.dumps(value)
    else:
        str_val = str(value)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO admin_settings (key, value, updated_at) VALUES ($1, $2, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, key, str_val)
    # Invalidate cache so the new value is visible on the very next read.
    _cache_invalidate(key)
