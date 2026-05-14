"""iter240l — Tests pour le certificat SVG premium + endpoints."""
import os
import pytest
from datetime import datetime, timezone


def test_render_svg_fr_basic():
    from services.jury_certificate_svg import render_jury_certificate_svg
    svg = render_jury_certificate_svg(
        full_name="Alice Dupont", username="alice",
        cycle_number=7, prize_amount=500.0,
        prize_currency="USD",
        issued_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        lang="fr", certificate_id="jury_test123",
    )
    assert "<svg" in svg
    assert "CERTIFICAT D'EXCELLENCE" in svg
    assert "Alice Dupont" in svg
    assert "Cycle #7" in svg
    assert "500 $" in svg
    assert "14/05/2026" in svg
    assert "japapmessenger.com" in svg
    assert "jury_test123" in svg


def test_render_svg_arabic_rtl():
    from services.jury_certificate_svg import render_jury_certificate_svg
    svg = render_jury_certificate_svg(
        full_name="Bob", username="bob",
        cycle_number=4, prize_amount=500.0, prize_currency="USD",
        issued_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        lang="ar",
    )
    assert "شهادة التميز" in svg
    assert 'direction="rtl"' in svg
    assert "500 دولار" in svg


def test_render_svg_all_languages():
    from services.jury_certificate_svg import render_jury_certificate_svg, SUPPORTED_LANGS
    for lang in SUPPORTED_LANGS:
        svg = render_jury_certificate_svg(
            full_name="Carol", username="carol",
            cycle_number=1, prize_amount=100.0,
            prize_currency="USD",
            issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            lang=lang,
        )
        assert "<svg" in svg
        assert "Carol" in svg


def test_render_svg_no_prize_honorary():
    """A juror without monetary prize (manual grant) should show honorary text."""
    from services.jury_certificate_svg import render_jury_certificate_svg
    svg_fr = render_jury_certificate_svg(
        full_name="David", username="david",
        cycle_number=2, prize_amount=None, prize_currency="USD", lang="fr",
    )
    assert "Récompense honorifique" in svg_fr
    svg_en = render_jury_certificate_svg(
        full_name="David", username="david",
        cycle_number=2, prize_amount=0, prize_currency="USD", lang="en",
    )
    assert "Honorary award" in svg_en


def test_render_svg_unknown_lang_falls_back_to_fr():
    from services.jury_certificate_svg import render_jury_certificate_svg
    svg = render_jury_certificate_svg(
        full_name="Eve", cycle_number=3, prize_amount=200, lang="xx",
    )
    assert "CERTIFICAT D'EXCELLENCE" in svg


def test_render_svg_xml_escape():
    """Names with special XML chars must be escaped, not break SVG."""
    from services.jury_certificate_svg import render_jury_certificate_svg
    svg = render_jury_certificate_svg(
        full_name="<script>alert(1)</script>",
        username="hacker", cycle_number=1, prize_amount=10, lang="fr",
    )
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
