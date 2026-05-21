"""iter241a-share-tiers — Tier logic regression.

Locks down the Power-sharer commission boost:
  • Standard tier => 10% (or whatever `referral_commission_percent` is set to).
  • Power-sharer  => 15% (or `power_sharer_commission_percent`) ONCE the
    referrer has more than `power_sharer_threshold` winning referrals in
    the trailing `power_sharer_window_days`.

Uses the unit-level pure helper `_tier_for` so this test runs without
needing the DB or HTTP stack.
"""
from decimal import Decimal

from services.forecast_service import _tier_for


def test_tier_for_standard_below_threshold():
    """Below threshold → standard tier + standard %."""
    tier, pct = _tier_for(
        winning_referrals=0,
        threshold=10,
        standard_pct=Decimal("10"),
        boosted_pct=Decimal("15"),
    )
    assert tier == "standard"
    assert pct == Decimal("10")


def test_tier_for_standard_at_threshold():
    """`> threshold` (not `>=`) → still standard when count == threshold."""
    tier, pct = _tier_for(
        winning_referrals=10,
        threshold=10,
        standard_pct=Decimal("10"),
        boosted_pct=Decimal("15"),
    )
    assert tier == "standard"
    assert pct == Decimal("10")


def test_tier_for_power_sharer_above_threshold():
    """`> threshold` → power_sharer + boosted %."""
    tier, pct = _tier_for(
        winning_referrals=11,
        threshold=10,
        standard_pct=Decimal("10"),
        boosted_pct=Decimal("15"),
    )
    assert tier == "power_sharer"
    assert pct == Decimal("15")


def test_tier_for_custom_thresholds():
    """Admin-configurable threshold + boost values are honoured."""
    tier, pct = _tier_for(
        winning_referrals=6,
        threshold=5,
        standard_pct=Decimal("7.5"),
        boosted_pct=Decimal("20"),
    )
    assert tier == "power_sharer"
    assert pct == Decimal("20")
