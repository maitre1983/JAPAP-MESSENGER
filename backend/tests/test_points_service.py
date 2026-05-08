"""
iter83 — points_service unit tests (Phase 1 invariants).

These tests lock the unified points engine before Phase 2/3 build on top of it.
They are PURE (no DB) — they exercise the helper functions with dict-shaped
cycle stand-ins.
"""
import pytest

from services.points_service import (
    POINTS_GOAL, DAYS_GOAL, QUIZ_ACCURACY_THRESHOLD,
    QUIZ_MIN_ANSWERS_FOR_ELIGIBILITY, VALID_GAMES, VALID_SOURCES,
    is_quiz_performance_met, is_starter_pro_eligible, quiz_accuracy, _phase,
)


# ── Constants must match the wheel ──────────────────────────────────────

def test_constants_aligned_with_wheel_spec():
    assert POINTS_GOAL == 10_000
    assert DAYS_GOAL == 25
    assert QUIZ_ACCURACY_THRESHOLD == 0.75
    assert QUIZ_MIN_ANSWERS_FOR_ELIGIBILITY >= 1
    assert set(VALID_GAMES) == {"wheel", "quiz", "tap"}
    assert "wheel" in VALID_SOURCES and "quiz" in VALID_SOURCES and "tap" in VALID_SOURCES


# ── Quiz accuracy ───────────────────────────────────────────────────────

def test_quiz_accuracy_zero_when_no_answers():
    assert quiz_accuracy({"quiz_answers_correct": 0, "quiz_answers_total": 0}) == 0.0


def test_quiz_accuracy_computes_ratio():
    c = {"quiz_answers_correct": 45, "quiz_answers_total": 60}
    assert quiz_accuracy(c) == pytest.approx(0.75)


# ── Cycle-level performance rule ────────────────────────────────────────

def test_performance_refused_when_under_min_answers():
    c = {"quiz_answers_correct": 40, "quiz_answers_total": 40}     # 100 % but only 40
    assert is_quiz_performance_met(c) is False


def test_performance_refused_when_accuracy_below_threshold():
    c = {"quiz_answers_correct": 37, "quiz_answers_total": 50}     # 74 %
    assert is_quiz_performance_met(c) is False


def test_performance_accepted_at_threshold():
    c = {"quiz_answers_correct": 38, "quiz_answers_total": 50}     # 76 %, ≥50 answers
    assert is_quiz_performance_met(c) is True


# ── Starter Pro eligibility (the ONE rule) ──────────────────────────────

def _cycle(points=POINTS_GOAL, days=DAYS_GOAL, qc=40, qt=50):
    return {
        "points_cycle": points, "days_played_count": days,
        "quiz_answers_correct": qc, "quiz_answers_total": qt,
    }


def test_eligible_all_three_conditions_met():
    assert is_starter_pro_eligible(_cycle()) is True


def test_eligibility_fails_on_points():
    assert is_starter_pro_eligible(_cycle(points=9_999)) is False


def test_eligibility_fails_on_days():
    assert is_starter_pro_eligible(_cycle(days=24)) is False


def test_eligibility_fails_on_quiz_accuracy():
    assert is_starter_pro_eligible(_cycle(qc=30, qt=50)) is False  # 60 %


def test_eligibility_fails_on_too_few_quiz_answers():
    # 100 % accuracy but only 40 answers (<50)
    assert is_starter_pro_eligible(_cycle(qc=40, qt=40)) is False


# ── Phase helper mirrors wheel ──────────────────────────────────────────

@pytest.mark.parametrize("days,expected", [
    (0, 1), (10, 1), (11, 2), (20, 2), (21, 3), (30, 3),
])
def test_phase_boundaries(days, expected):
    assert _phase(days) == expected
