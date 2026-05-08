"""
Game settings — admin-controlled configuration for Quiz / Tap engines.
======================================================================

Stores all gameplay tunables in `admin_settings` (key/value JSON) so the
admin can change them on-the-fly without redeploys. Each game has its own
namespaced subset of keys with sensible defaults.

Keys:
  Quiz:
    quiz_enabled                   bool   default True
    quiz_sessions_per_day          int    default 3   (range 1-50)
    quiz_timer_seconds             int    default 10  (range 5-60)
    quiz_points_per_correct        int    default 20
    quiz_perfect_bonus             int    default 30
    quiz_session_size              int    default 5

  Tap:
    tap_enabled                    bool   default True
    tap_sessions_per_day           int    default 1   (range 1-50)
    tap_duration_seconds           int    default 10
    tap_max_taps_per_second        int    default 12  (anti-cheat)
    tap_reward_thresholds          json   default [{"taps":50,"reward":1},
                                                   {"taps":80,"reward":3},
                                                   {"taps":120,"reward":5}]
"""
from __future__ import annotations
import json
import logging
from typing import Any

from services.settings_service import get_setting, set_setting

logger = logging.getLogger(__name__)

QUIZ_DEFAULTS: dict[str, Any] = {
    "quiz_enabled": True,
    "quiz_sessions_per_day": 3,
    "quiz_timer_seconds": 60,           # iter118: total session timer (was 10s — too short)
    "quiz_timer_per_question_seconds": 15,
    "quiz_timer_mode": "per_question",  # "global" or "per_question"
    "quiz_points_per_correct": 20,
    "quiz_perfect_bonus": 30,
    "quiz_session_size": 5,
    "quiz_auto_advance_enabled": True,
    "quiz_auto_advance_delay_ms": 900,  # show ✅/❌ feedback before next question
    # iter120 — Per-question dynamic pacing. Lower delays toward the end =
    # more tension. Falls back to quiz_auto_advance_delay_ms for any index
    # beyond the list length. Each entry clamped to [300, 3000] ms.
    "quiz_auto_advance_delays_ms": [900, 800, 700, 550, 400],
    # iter122 — Learning mode: when the user picks a WRONG answer, /answer
    # also returns the displayed-correct option so the UI can highlight it
    # in green (turns each quiz into a mini-lesson). Disabled by default to
    # preserve maximum challenge — admin enables it for educational gameplay.
    "quiz_show_correct_after_wrong": False,
    # iter125 — Phase 3.B: Quiz Champion paid challenge / escrow settings.
    "quiz_challenge_paid_enabled":          False,    # master kill-switch
    "quiz_challenge_commission_pct":        10,       # 0-50, JAPAP commission
    # iter225 — bornes en USD (canonique iter158/178). Defaults généreux mais
    # ré-ajustables par l'admin depuis /admin/games. 1 USD ≈ 565 XAF, 200 USD ≈ 113k XAF.
    "quiz_challenge_stake_min":             1,        # min stake en USD canonique
    "quiz_challenge_stake_max":             200,      # max stake en USD canonique
    "quiz_challenge_refund_on_expiry":      True,     # auto-refund when expires_at passes
    "quiz_challenge_challenger_bonus_points": 50,     # engagement bonus on refuse/expiry
    "quiz_challenge_expiry_hours":          24,       # window before expiry
    # iter130 — Phase 3.E: Anti-repetition + Daily Challenge + AI distribution.
    "quiz_anti_repeat_days":            7,        # picker exclusion window (days)
    "quiz_dist_africa_pct":             50,       # 0-100, default Africa weight
    "quiz_dist_sport_pct":              20,       # 0-100, default Sport weight
    "quiz_dist_econ_pct":               15,       # 0-100, default Economy/Crypto weight
    "quiz_dist_world_pct":              15,       # 0-100, default General/World weight
    "quiz_daily_challenge_enabled":     True,     # master kill-switch for /daily-challenge
    "quiz_daily_challenge_points_per_correct": 25,    # base points per correct answer
    "quiz_daily_challenge_perfect_bonus":     50,     # bonus on 5/5
    "quiz_daily_challenge_streak_bonus_per_day": 5,   # +5 pts per consecutive day (capped)
    "quiz_daily_challenge_streak_bonus_cap":  150,    # max streak bonus added
}

TAP_DEFAULTS: dict[str, Any] = {
    "tap_enabled": True,
    "tap_sessions_per_day": 1,
    "tap_duration_seconds": 10,
    "tap_max_taps_per_second": 12,
    "tap_reward_thresholds": [
        {"taps": 50,  "reward": 1},
        {"taps": 80,  "reward": 3},
        {"taps": 120, "reward": 5},
    ],
}

# Hard bounds (admin can't lock players out by setting impossible values).
QUIZ_BOUNDS = {
    "quiz_sessions_per_day":     (1, 50),
    "quiz_timer_seconds":        (10, 600),       # iter118: extended to 10min
    "quiz_timer_per_question_seconds": (5, 120),  # iter118
    "quiz_points_per_correct":   (1, 1000),
    "quiz_perfect_bonus":        (0, 5000),
    "quiz_session_size":         (3, 20),
    "quiz_auto_advance_delay_ms": (300, 3000),   # iter118
    # iter125 — bounds for challenge config
    "quiz_challenge_commission_pct":        (0, 50),
    "quiz_challenge_stake_min":             (0, 1000000),
    "quiz_challenge_stake_max":             (0, 10000000),
    "quiz_challenge_challenger_bonus_points": (0, 1000),
    "quiz_challenge_expiry_hours":          (1, 168),
    # iter130 — Phase 3.E bounds
    "quiz_anti_repeat_days":                (1, 60),
    "quiz_dist_africa_pct":                 (0, 100),
    "quiz_dist_sport_pct":                  (0, 100),
    "quiz_dist_econ_pct":                   (0, 100),
    "quiz_dist_world_pct":                  (0, 100),
    "quiz_daily_challenge_points_per_correct": (1, 1000),
    "quiz_daily_challenge_perfect_bonus":   (0, 5000),
    "quiz_daily_challenge_streak_bonus_per_day": (0, 100),
    "quiz_daily_challenge_streak_bonus_cap": (0, 5000),
}
TAP_BOUNDS = {
    "tap_sessions_per_day":      (1, 50),
    "tap_duration_seconds":      (3, 60),
    "tap_max_taps_per_second":   (4, 30),
}


def _parse(raw: Any, expected_type: type, default: Any) -> Any:
    """Coerce a stored setting value to the expected type. Falls back to
    the default if parsing fails (corrupted setting, dev env)."""
    if raw is None or raw == "":
        return default
    try:
        if expected_type is bool:
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
        if expected_type is int:
            return int(raw)
        if expected_type in (list, dict):
            if isinstance(raw, (list, dict)):
                return raw
            return json.loads(raw)
        return raw
    except Exception as e:
        logger.warning("game_setting parse failed (%s): %s", expected_type, e)
        return default


def _clamp(value: int, key: str, bounds: dict) -> int:
    lo, hi = bounds.get(key, (None, None))
    if lo is None:
        return value
    return max(lo, min(value, hi))


async def get_quiz_config() -> dict:
    """Return the merged Quiz config — defaults + admin overrides. All
    values are validated/clamped before being returned."""
    out: dict[str, Any] = {}
    for key, default in QUIZ_DEFAULTS.items():
        raw = await get_setting(key)
        val = _parse(raw, type(default), default)
        if isinstance(val, int) and key in QUIZ_BOUNDS:
            val = _clamp(val, key, QUIZ_BOUNDS)
        out[key] = val
    # iter120 — Sanitize delays array (clamp + ensure list of ints).
    delays = out.get("quiz_auto_advance_delays_ms") or QUIZ_DEFAULTS["quiz_auto_advance_delays_ms"]
    if not isinstance(delays, list) or not delays:
        delays = QUIZ_DEFAULTS["quiz_auto_advance_delays_ms"]
    cleaned: list[int] = []
    for d in delays[:20]:
        try:
            di = int(d)
        except (TypeError, ValueError):
            continue
        cleaned.append(max(300, min(3000, di)))
    out["quiz_auto_advance_delays_ms"] = cleaned or QUIZ_DEFAULTS["quiz_auto_advance_delays_ms"]
    return out


async def get_tap_config() -> dict:
    out: dict[str, Any] = {}
    for key, default in TAP_DEFAULTS.items():
        raw = await get_setting(key)
        val = _parse(raw, type(default), default)
        if isinstance(val, int) and key in TAP_BOUNDS:
            val = _clamp(val, key, TAP_BOUNDS)
        out[key] = val
    # Sanity on tap_reward_thresholds
    thresholds = out.get("tap_reward_thresholds") or TAP_DEFAULTS["tap_reward_thresholds"]
    if not isinstance(thresholds, list) or any(
        not isinstance(t, dict) or "taps" not in t or "reward" not in t for t in thresholds
    ):
        thresholds = TAP_DEFAULTS["tap_reward_thresholds"]
    out["tap_reward_thresholds"] = sorted(thresholds, key=lambda t: int(t["taps"]))
    return out


async def update_quiz_config(updates: dict, admin_id: str = "") -> dict:
    """Persist a partial Quiz config. Validation:
      • unknown keys are rejected
      • numeric values are clamped to bounds
      • booleans are normalised
      • string enums (timer_mode) validated
      • iter130: distribution pcts must sum to 100 when any is updated.
    Returns the merged config after the update.
    """
    for k in updates:
        if k not in QUIZ_DEFAULTS:
            raise ValueError(f"Clé inconnue : {k}")
    # iter130 — Atomic distribution validation: if ANY of the 4 dist_*_pct
    # is being updated, enforce sum == 100. Pull missing values from
    # current config so partial updates remain coherent.
    DIST_KEYS = ("quiz_dist_africa_pct", "quiz_dist_sport_pct",
                 "quiz_dist_econ_pct",   "quiz_dist_world_pct")
    if any(k in updates for k in DIST_KEYS):
        current = await get_quiz_config()
        merged = {k: int(updates.get(k, current.get(k, QUIZ_DEFAULTS[k]))) for k in DIST_KEYS}
        total = sum(merged.values())
        if total != 100:
            raise ValueError(
                f"La distribution doit totaliser exactement 100% (actuel: {total}). "
                f"Afrique={merged['quiz_dist_africa_pct']}, Sport={merged['quiz_dist_sport_pct']}, "
                f"Éco/Crypto={merged['quiz_dist_econ_pct']}, Général={merged['quiz_dist_world_pct']}."
            )
    for k, v in updates.items():
        default = QUIZ_DEFAULTS[k]
        if isinstance(default, bool):
            v = bool(v)
        elif isinstance(default, str):
            # iter118: timer_mode enum
            v = str(v).strip()
            if k == "quiz_timer_mode" and v not in ("global", "per_question"):
                raise ValueError("quiz_timer_mode doit être 'global' ou 'per_question'.")
        elif isinstance(default, list):
            # iter120: per-question delays array
            if not isinstance(v, list):
                raise ValueError(f"{k} doit être une liste.")
            if k == "quiz_auto_advance_delays_ms":
                if len(v) == 0 or len(v) > 20:
                    raise ValueError("quiz_auto_advance_delays_ms : entre 1 et 20 entrées.")
                cleaned: list[int] = []
                for d in v:
                    try:
                        di = int(d)
                    except (TypeError, ValueError):
                        raise ValueError("Chaque délai doit être un entier (ms).")
                    if di < 300 or di > 3000:
                        raise ValueError("Chaque délai doit être entre 300 et 3000 ms.")
                    cleaned.append(di)
                v = cleaned
        elif isinstance(default, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} doit être un entier.")
            if k in QUIZ_BOUNDS:
                lo, hi = QUIZ_BOUNDS[k]
                if v < lo or v > hi:
                    raise ValueError(f"{k} doit être entre {lo} et {hi}.")
        # Setting values are stored as strings (admin_settings table type).
        if isinstance(v, bool):
            await set_setting(k, str(v).lower())
        elif isinstance(v, list):
            await set_setting(k, json.dumps(v))
        else:
            await set_setting(k, str(v))
    return await get_quiz_config()


async def update_tap_config(updates: dict, admin_id: str = "") -> dict:
    for k in updates:
        if k not in TAP_DEFAULTS:
            raise ValueError(f"Clé inconnue : {k}")
    for k, v in updates.items():
        default = TAP_DEFAULTS[k]
        if isinstance(default, bool):
            v = bool(v)
            await set_setting(k, "true" if v else "false")
        elif isinstance(default, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} doit être un entier.")
            if k in TAP_BOUNDS:
                lo, hi = TAP_BOUNDS[k]
                if v < lo or v > hi:
                    raise ValueError(f"{k} doit être entre {lo} et {hi}.")
            await set_setting(k, str(v))
        elif k == "tap_reward_thresholds":
            if not isinstance(v, list):
                raise ValueError("tap_reward_thresholds doit être une liste.")
            for t in v:
                if not isinstance(t, dict) or "taps" not in t or "reward" not in t:
                    raise ValueError("Chaque palier doit avoir {taps, reward}.")
                int(t["taps"])
                int(t["reward"])  # validate numeric
            await set_setting(k, json.dumps(v))
    return await get_tap_config()


__all__ = [
    "QUIZ_DEFAULTS", "TAP_DEFAULTS", "QUIZ_BOUNDS", "TAP_BOUNDS",
    "get_quiz_config", "get_tap_config",
    "update_quiz_config", "update_tap_config",
]
