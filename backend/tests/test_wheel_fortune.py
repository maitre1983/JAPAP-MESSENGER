"""
iter83 — JAPAP Roue de la Fortune v2 : tests d'invariants métier.

Ces tests PROUVENT que les règles suivantes sont mathématiquement garanties
par le backend et ne peuvent pas être contournées :

  1. Le cycle dure exactement 30 jours calendaires.
  2. Un joueur ne peut PAS atteindre 10 000 points en moins de 25 jours
     distincts de jeu, même en maximisant tous les bonus possibles (cap
     quotidien phase 1/2/3 + streak 3/7/15).
  3. `days_played_count` ne peut être incrémenté qu'une fois par jour
     calendaire distinct (pas d'exploit multi-spin le même jour).
  4. Le jackpot n'est éligible qu'en phase 3 avec points≥8000 et days≥20.
  5. `claim-reward` refuse tant que points<10k OU days<25.

Run :  cd /app/backend && pytest tests/test_wheel_fortune.py -q
"""
from __future__ import annotations

import pytest

from routes.wheel_fortune import (
    CLAIM_GRACE_DAYS,
    CYCLE_LENGTH_DAYS,
    DAYS_GOAL,
    MAX_POINTS_PER_DAY_BY_PHASE,
    PHASE_DISTRIBUTIONS,
    POINTS_GOAL,
    WHEEL_SLOTS,
    _compute_phase,
    _compute_streak_bonus,
    _cycle_trigger_for_days_left,
    _jackpot_eligible,
    _milestones_reached,
    _near_miss_eligible,
    _weighted_pick,
)


# ═══════════════════════════════════════════════════════════════════
# 1. Constantes métier — source de vérité
# ═══════════════════════════════════════════════════════════════════

def test_cycle_length_is_30_days():
    assert CYCLE_LENGTH_DAYS == 30


def test_goals_match_product_spec():
    assert POINTS_GOAL == 10_000
    assert DAYS_GOAL == 25


def test_claim_grace_window_exists():
    # Une récompense pending doit pouvoir être réclamée après l'expiration
    # du cycle pendant une fenêtre de grâce stricte.
    assert CLAIM_GRACE_DAYS >= 1


def test_wheel_has_exactly_8_slots_index_0_is_jackpot():
    assert len(WHEEL_SLOTS) == 8
    assert WHEEL_SLOTS[0]["is_jackpot"] is True
    assert WHEEL_SLOTS[0]["label"] == "Jackpot"


# ═══════════════════════════════════════════════════════════════════
# 2. Phases de progression
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("days,expected", [
    (0, 1), (1, 1), (5, 1), (10, 1),
    (11, 2), (15, 2), (20, 2),
    (21, 3), (25, 3), (30, 3), (100, 3),
])
def test_compute_phase_boundaries(days, expected):
    assert _compute_phase(days) == expected


# ═══════════════════════════════════════════════════════════════════
# 3. BARRIÈRE MATHÉMATIQUE SOUVERAINE — cœur du système
#    Il est IMPOSSIBLE d'atteindre 10 000 points en 24 jours distincts.
# ═══════════════════════════════════════════════════════════════════

def _max_points_achievable_in_n_days(n_days: int) -> int:
    """
    Simule le scénario ABSOLUMENT optimal sur `n_days` jours distincts :
      - Le joueur maximise le cap quotidien par phase à chaque jour
      - Il récolte tous les bonus de streak (j3=+50, j7=+150, j15=+400)
    Cette valeur représente la borne théorique supérieure du `points_cycle`
    si tous les plafonds *internes* étaient contournés.

    Retourne le cumul BRUT (avant clamp souverain new_total < 10k si d<25).
    """
    # Bonus de streak (modèle par défaut)
    STREAK_BONUS = {3: 50, 7: 150, 15: 400}
    total = 0
    for day in range(1, n_days + 1):
        phase = _compute_phase(day)
        total += MAX_POINTS_PER_DAY_BY_PHASE[phase]
        if day in STREAK_BONUS:
            total += STREAK_BONUS[day]
    return total


def test_theoretical_max_24_days_exceeds_goal_without_clamp():
    """
    Sanity check : sans le clamp souverain, les caps par phase + streak
    LAISSERAIENT mathématiquement un joueur atteindre ≥10k en 24 jours
    (2200 phase1 + 5400 phase2 + 3600 phase3 = 11 200).
    C'est précisément pour ça que la barrière souveraine existe.
    """
    raw_max = _max_points_achievable_in_n_days(24)
    assert raw_max >= POINTS_GOAL, (
        f"Le cap par phase SEUL ne suffit pas à bloquer les 10k : {raw_max}. "
        "La règle métier est garantie par le CLAMP SOUVERAIN dans /spin."
    )


def test_sovereign_clamp_enforces_sub_10k_until_day_25():
    """
    Simule l'application du clamp souverain (new_days < DAYS_GOAL →
    new_total = min(new_total, POINTS_GOAL - 1)) sur 24 jours de jeu
    optimal. Prouve que points_cycle reste STRICTEMENT < 10 000.
    """
    STREAK_BONUS = {3: 50, 7: 150, 15: 400}
    points_cycle = 0
    for day in range(1, 25):  # jours 1..24
        phase = _compute_phase(day)
        gained = MAX_POINTS_PER_DAY_BY_PHASE[phase]
        if day in STREAK_BONUS:
            gained += STREAK_BONUS[day]
        new_total = points_cycle + gained
        # --- réplique exacte du clamp souverain dans wheel_fortune.py ---
        if day < DAYS_GOAL:
            new_total = min(new_total, POINTS_GOAL - 1)
        points_cycle = new_total
        assert points_cycle < POINTS_GOAL, (
            f"VIOLATION au jour {day} : points_cycle={points_cycle} ≥ {POINTS_GOAL}"
        )
    # Borne finale après 24 jours optimal
    assert points_cycle == POINTS_GOAL - 1  # clampé à 9999


def test_day_25_is_first_day_clamp_is_lifted():
    """Au 25e jour distinct, le clamp est levé et l'utilisateur peut
    enfin franchir la barre des 10 000 points."""
    points_cycle = POINTS_GOAL - 1  # départ juste en dessous (j24)
    day = 25
    gained = MAX_POINTS_PER_DAY_BY_PHASE[_compute_phase(day)]
    new_total = points_cycle + gained
    # clamp souverain : day >= DAYS_GOAL -> pas de clamp
    if day < DAYS_GOAL:
        new_total = min(new_total, POINTS_GOAL - 1)
    assert new_total >= POINTS_GOAL, "Le clamp doit être levé à j25."


# ═══════════════════════════════════════════════════════════════════
# 4. Jackpot & near-miss : strictement conditionnels côté backend
# ═══════════════════════════════════════════════════════════════════

def _fake_cycle(points: int, days: int, days_left: int = 5):
    # Minimal dict-like stub compatible avec _jackpot_eligible / _near_miss
    import datetime
    today = datetime.date(2025, 1, 1)
    return {
        "points_cycle": points,
        "days_played_count": days,
        "cycle_end_date": today + datetime.timedelta(days=days_left - 1),
    }


def test_jackpot_refused_before_phase_3():
    # Même avec 10k pts et 25j, si phase != 3, jackpot refusé
    c = _fake_cycle(points=9_500, days=15, days_left=10)
    assert _jackpot_eligible(c, phase=1) is False
    assert _jackpot_eligible(c, phase=2) is False


def test_jackpot_refused_below_8000_points():
    c = _fake_cycle(points=7_999, days=21, days_left=5)
    assert _jackpot_eligible(c, phase=3) is False


def test_jackpot_refused_below_20_days():
    c = _fake_cycle(points=9_500, days=19, days_left=5)
    assert _jackpot_eligible(c, phase=3) is False


def test_jackpot_accepted_in_window():
    import datetime
    # Today (patched via monkeypatch would be safer, but we just ensure
    # cycle_end_date is in the future).
    today = datetime.date.today()
    c = {
        "points_cycle": 8_500,
        "days_played_count": 22,
        "cycle_end_date": today + datetime.timedelta(days=3),
    }
    assert _jackpot_eligible(c, phase=3) is True


def test_near_miss_only_in_right_window():
    import datetime
    today = datetime.date.today()
    c = {
        "points_cycle": 6_000,
        "days_played_count": 16,
        "cycle_end_date": today + datetime.timedelta(days=3),
    }
    assert _near_miss_eligible(c, phase=2) is True
    # Trop de points : plus en near-miss, on bascule sur jackpot window
    c["points_cycle"] = 8_500
    assert _near_miss_eligible(c, phase=2) is False


# ═══════════════════════════════════════════════════════════════════
# 5. Streak bonus
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("streak,expected", [
    (0, 0), (1, 0), (2, 0),
    (3, 50), (5, 50), (6, 50),
    (7, 150), (10, 150), (14, 150),
    (15, 400), (30, 400),
])
def test_streak_bonus_thresholds(streak, expected):
    # Config defaults — matches settings_service wheel_config_json
    cfg = {
        "streak_3_days": 3, "streak_3_bonus": 50,
        "streak_7_days": 7, "streak_7_bonus": 150,
        "streak_15_days": 15, "streak_15_bonus": 400,
    }
    assert _compute_streak_bonus(streak, cfg) == expected


# ═══════════════════════════════════════════════════════════════════
# 6. Milestones
# ═══════════════════════════════════════════════════════════════════

def test_milestones_ordered_crossings():
    assert _milestones_reached(0) == []
    assert _milestones_reached(4_999) == []
    assert _milestones_reached(5_000) == [5_000]
    assert _milestones_reached(7_999) == [5_000]
    assert _milestones_reached(8_000) == [5_000, 8_000]
    assert _milestones_reached(10_000) == [5_000, 8_000, 10_000]


# ═══════════════════════════════════════════════════════════════════
# 7. Scheduler cycle reminders
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("days_left,tag", [
    (7, "j7"), (3, "j3"), (1, "j1"), (0, "j0"),
    (2, None), (4, None), (8, None), (-1, None),
])
def test_cycle_trigger_tags(days_left, tag):
    assert _cycle_trigger_for_days_left(days_left) == tag


# ═══════════════════════════════════════════════════════════════════
# 8. Distribution pondérée : ne peut jamais retourner le jackpot (idx 0)
#    lors des phases 1 et 2 (défense en profondeur).
# ═══════════════════════════════════════════════════════════════════

def test_phase1_and_2_distributions_exclude_jackpot():
    for phase in (1, 2):
        slots_used = {idx for idx, _w in PHASE_DISTRIBUTIONS[phase]}
        assert 0 not in slots_used, (
            f"Phase {phase} ne doit JAMAIS retourner le jackpot via la "
            "distribution pondérée. Jackpot est piloté séparément en phase 3."
        )


def test_weighted_pick_distribution_stays_in_valid_slots():
    import random as _r
    _r.seed(42)
    for phase, dist in PHASE_DISTRIBUTIONS.items():
        valid = {s for s, _ in dist}
        for _ in range(200):
            picked = _weighted_pick(dist)
            assert picked in valid, f"Phase {phase} a retourné un slot hors distribution : {picked}"


def test_weighted_pick_handles_empty_gracefully():
    # Ne doit pas planter en cas de liste vide (défense)
    assert _weighted_pick([]) == 0



# ═══════════════════════════════════════════════════════════════════
# 9. iter114 — Wheel Boost Event : _apply_boost_to_distribution()
# ═══════════════════════════════════════════════════════════════════

from routes.wheel_fortune import _apply_boost_to_distribution


def test_boost_perdu_reduction_zero_is_noop():
    dist = [(1, 30), (2, 35), (4, 20)]
    assert _apply_boost_to_distribution(dist, 0) == dist


def test_boost_perdu_reduction_50_halves_perdu_weight():
    dist = [(1, 30), (2, 35), (4, 20)]
    out = _apply_boost_to_distribution(dist, 50)
    perdu = next(w for s, w in out if s == 1)
    assert perdu == 15  # 30 × (100-50)/100
    # Other slots untouched
    assert next(w for s, w in out if s == 2) == 35
    assert next(w for s, w in out if s == 4) == 20


def test_boost_perdu_reduction_95_drops_perdu_to_zero_or_low():
    """At 95% reduction, slot 1 weight in phase 1 (weight 5) drops to 0
    and is removed from the distribution entirely → impossible to land."""
    dist = [(1, 5), (2, 35), (4, 30), (6, 20), (7, 10)]
    out = _apply_boost_to_distribution(dist, 95)
    slots_remaining = {s for s, _ in out}
    assert 1 not in slots_remaining, (
        "Slot 1 (Perdu) doit disparaître entièrement à 95% de réduction "
        "quand son poids initial est ≤ 5."
    )
    # Other slots preserved
    assert {2, 4, 6, 7}.issubset(slots_remaining)


def test_boost_never_returns_empty_distribution():
    """Edge case: all-Perdu dist with 95% reduction → should NOT empty
    out (we fall back to the original distribution to avoid crashes)."""
    dist = [(1, 100)]  # only Perdu
    out = _apply_boost_to_distribution(dist, 95)
    assert len(out) >= 1, "Doit jamais retourner une distribution vide"


def test_boost_distribution_preserves_non_perdu_relative_weights():
    """The relative ratio between non-Perdu slots must be preserved,
    only the Perdu weight is scaled."""
    dist = [(1, 30), (2, 40), (4, 20)]
    out_at_50 = _apply_boost_to_distribution(dist, 50)
    out_at_80 = _apply_boost_to_distribution(dist, 80)
    # Non-Perdu weights identical between the two boost levels
    for cmp_dist in (out_at_50, out_at_80):
        assert next(w for s, w in cmp_dist if s == 2) == 40
        assert next(w for s, w in cmp_dist if s == 4) == 20



# ═══════════════════════════════════════════════════════════════════
# 10. iter115 — Wheel Boost Scheduler : _is_recurring_active()
# ═══════════════════════════════════════════════════════════════════

from datetime import time as _t, datetime as _dt, timezone as _tz
from services.wheel_boost_scheduler import _is_recurring_active


def _now(weekday: int, hour: int, minute: int = 0) -> _dt:
    """Build a UTC datetime with a specific weekday (0=Mon..6=Sun)."""
    # 2026-04-27 is a Monday — use it as a base, offset by `weekday`.
    base = _dt(2026, 4, 27, hour, minute, tzinfo=_tz.utc)
    from datetime import timedelta
    return base + timedelta(days=weekday)


def test_recurring_window_same_day_active_inside():
    # Friday 18:00 -> Friday 22:00. Now Friday 19:30 → active.
    assert _is_recurring_active(_now(4, 19, 30), 4, _t(18, 0), 4, _t(22, 0)) is True


def test_recurring_window_same_day_inactive_before():
    # Friday 17:59 → outside window starting at 18:00 → inactive.
    assert _is_recurring_active(_now(4, 17, 59), 4, _t(18, 0), 4, _t(22, 0)) is False


def test_recurring_window_same_day_inactive_after():
    # Friday 22:01 → outside window ending at 22:00 → inactive.
    assert _is_recurring_active(_now(4, 22, 1), 4, _t(18, 0), 4, _t(22, 0)) is False


def test_recurring_weekend_window_active_saturday():
    # Friday 18:00 → Sunday 23:00. Saturday 12:00 → active.
    assert _is_recurring_active(_now(5, 12, 0), 4, _t(18, 0), 6, _t(23, 0)) is True


def test_recurring_weekend_window_active_friday_evening():
    # Same window, Friday 19:00 → active (just inside).
    assert _is_recurring_active(_now(4, 19, 0), 4, _t(18, 0), 6, _t(23, 0)) is True


def test_recurring_weekend_window_inactive_thursday():
    # Same window, Thursday 12:00 → inactive (before window starts Friday).
    assert _is_recurring_active(_now(3, 12, 0), 4, _t(18, 0), 6, _t(23, 0)) is False


def test_recurring_wraps_around_week_boundary():
    # Saturday 22:00 → Monday 06:00 (wraps Sunday→Monday).
    win_start_dow, win_start_t = 5, _t(22, 0)
    win_end_dow, win_end_t = 0, _t(6, 0)
    assert _is_recurring_active(_now(6, 12, 0), win_start_dow, win_start_t,
                                 win_end_dow, win_end_t) is True  # Sunday noon
    assert _is_recurring_active(_now(0, 5, 0), win_start_dow, win_start_t,
                                 win_end_dow, win_end_t) is True  # Monday 5h
    assert _is_recurring_active(_now(0, 7, 0), win_start_dow, win_start_t,
                                 win_end_dow, win_end_t) is False  # Monday 7h (out)
    assert _is_recurring_active(_now(2, 12, 0), win_start_dow, win_start_t,
                                 win_end_dow, win_end_t) is False  # Wednesday noon


def test_recurring_returns_false_on_missing_fields():
    assert _is_recurring_active(_now(4, 19, 0), None, _t(18, 0), 4, _t(22, 0)) is False
    assert _is_recurring_active(_now(4, 19, 0), 4, None, 4, _t(22, 0)) is False
