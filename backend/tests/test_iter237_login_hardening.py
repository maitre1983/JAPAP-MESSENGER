"""
iter237 — Login page hardening : Se souvenir de moi (remember_me) +
captcha resilience.

Tests:
  P0  remember_me=False → cookies are SESSION cookies (no Max-Age) →
      browser drops them on close.
  P0  remember_me=True  → cookies have Max-Age (8h access + days refresh).
  P0  remember_me unset → defaults to False (backwards-compat).
  P0  GET /api/auth/captcha → 200 with {captcha_id, question, expires_at}.
  P0  Captcha math is solvable (e.g., "14 + 4 = 18").
"""
from __future__ import annotations

import os
import re

import pytest
import requests

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://japap-refactor.preview.emergentagent.com",
).rstrip("/")
CAPTCHA = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!", **CAPTCHA}


def _login_raw(remember_me=None):
    body = dict(ALICE)
    if remember_me is not None:
        body["remember_me"] = remember_me
    last = None
    for _ in range(3):
        try:
            r = requests.post(f"{BASE}/api/auth/login", json=body, timeout=60)
            return r
        except Exception as e:
            last = e
    raise last  # type: ignore[misc]


def _cookie_attrs(set_cookie_lines, name):
    """Return a dict of attributes for the cookie named `name`."""
    for line in set_cookie_lines:
        if line.lstrip().startswith(f"{name}="):
            attrs = {}
            for part in line.split(";")[1:]:
                k, _, v = part.strip().partition("=")
                attrs[k.lower()] = v or True
            return attrs
    return None


def test_login_remember_me_false_session_cookies():
    r = _login_raw(remember_me=False)
    assert r.status_code == 200, r.text[:200]
    raw = r.headers.get("set-cookie", "") or ""
    # requests collapses multiple Set-Cookie into a single comma-joined string.
    # We need the raw header values; use raw=True via cookies.
    lines = [str(c) for c in r.raw.headers.getlist("set-cookie")] if hasattr(r.raw, "headers") else raw.split("\n")
    access = _cookie_attrs(lines, "access_token")
    refresh = _cookie_attrs(lines, "refresh_token")
    assert access is not None and refresh is not None, lines
    # Session cookies → NO Max-Age, NO Expires.
    assert "max-age" not in access, f"access has max-age: {access}"
    assert "max-age" not in refresh, f"refresh has max-age: {refresh}"


def test_login_remember_me_true_persistent_cookies():
    r = _login_raw(remember_me=True)
    assert r.status_code == 200, r.text[:200]
    lines = [str(c) for c in r.raw.headers.getlist("set-cookie")] if hasattr(r.raw, "headers") else (r.headers.get("set-cookie", "") or "").split("\n")
    access = _cookie_attrs(lines, "access_token")
    refresh = _cookie_attrs(lines, "refresh_token")
    assert access is not None and refresh is not None
    assert "max-age" in access, f"access missing max-age: {access}"
    assert int(access["max-age"]) == 28800, access
    assert "max-age" in refresh, refresh
    # Refresh TTL: 7 days (default) or 90 days (trusted device). Both are > 1 day.
    assert int(refresh["max-age"]) >= 7 * 86400, refresh


def test_login_remember_me_omitted_defaults_false():
    """Missing `remember_me` → backward-compat default is False (session cookies)."""
    r = _login_raw(remember_me=None)
    assert r.status_code == 200, r.text[:200]
    lines = [str(c) for c in r.raw.headers.getlist("set-cookie")] if hasattr(r.raw, "headers") else (r.headers.get("set-cookie", "") or "").split("\n")
    access = _cookie_attrs(lines, "access_token")
    assert access is not None
    assert "max-age" not in access, f"omitted remember_me should default to session cookie: {access}"


def test_captcha_endpoint_returns_solvable_math():
    r = requests.get(f"{BASE}/api/auth/captcha", timeout=15)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert "captcha_id" in body and "question" in body and "expires_at" in body
    # iter237b — `disabled` field always present, false in default state.
    assert body.get("disabled") is False, body
    # Question pattern: "<a> [+-] <b>"
    m = re.match(r"^\s*(-?\d+)\s*([+\-])\s*(-?\d+)\s*$", body["question"])
    assert m, f"unexpected question shape: {body['question']!r}"
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    expected = a + b if op == "+" else a - b
    assert isinstance(expected, int)


def test_kill_switch_field_present_in_payload():
    """iter237b — The captcha endpoint MUST always advertise the `disabled`
    flag so the frontend can short-circuit rendering without guessing."""
    r = requests.get(f"{BASE}/api/auth/captcha", timeout=15)
    assert r.status_code == 200
    assert "disabled" in r.json(), r.text[:200]
