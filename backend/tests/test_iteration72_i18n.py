"""
JAPAP — i18n coverage regression (iter72).

Makes sure:
  • The backend SUPPORTED_LANGS set matches the frontend bundle files.
  • Every frontend bundle has the exact same key tree as en.json.
  • PUT /api/auth/preferences accepts every supported lang and rejects garbage.
"""
import os
import json
from pathlib import Path

import pytest
import httpx

BASE = os.environ.get("PYTEST_BASE", "http://localhost:8001")
USER_EMAIL = "bob@japap.com"
USER_PASSWORD = "Test1234!"

LOCALES_DIR = Path("/app/frontend/src/locales")
EN_BUNDLE = LOCALES_DIR / "en.json"


def _keys(obj, prefix=""):
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out |= _keys(v, f"{prefix}{k}.")
    else:
        out.add(prefix.rstrip("."))
    return out


def _login(client, email, password):
    r = client.post(
        f"{BASE}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    return {"Authorization": f"Bearer {data.get('token') or data.get('access_token')}"}


@pytest.fixture
def client():
    with httpx.Client(timeout=15) as c:
        yield c


# ─── Bundle shape ────────────────────────────────────────────────────────

def test_all_bundles_share_the_same_key_tree():
    """Every locale file must have exactly the same set of keys as en.json —
    no missing strings, no leftover stale keys. Catches LLM drift."""
    ref = _keys(json.loads(EN_BUNDLE.read_text()))
    assert ref, "en.json is empty?!"
    for f in sorted(LOCALES_DIR.glob("*.json")):
        if f.name == "en.json":
            continue
        data = json.loads(f.read_text())
        ks = _keys(data)
        missing = ref - ks
        extra = ks - ref
        assert not missing, f"{f.name}: missing keys {list(missing)[:5]}"
        assert not extra, f"{f.name}: unexpected extra keys {list(extra)[:5]}"


def test_bundle_count_matches_backend_supported_langs():
    """Every backend-supported lang must ship a frontend bundle, and
    vice-versa — otherwise a user could set a preferred_lang that
    the UI can't render."""
    import sys
    sys.path.insert(0, "/app/backend")
    from constants import SUPPORTED_LANGS  # type: ignore

    bundles = {f.stem for f in LOCALES_DIR.glob("*.json")}
    assert bundles == SUPPORTED_LANGS, (
        f"Bundle/supported-lang drift: bundles={bundles} "
        f"supported={SUPPORTED_LANGS}"
    )


# ─── Backend /preferences acceptance ─────────────────────────────────────

@pytest.mark.parametrize(
    "code",
    ["en", "fr", "pt", "es", "ar", "sw", "ln", "yo", "hi", "bn", "ta"],
)
def test_preferences_accepts_every_supported_lang(client, code):
    headers = _login(client, USER_EMAIL, USER_PASSWORD)
    r = client.put(
        f"{BASE}/api/auth/preferences",
        json={"preferred_lang": code},
        headers=headers,
    )
    assert r.status_code == 200, f"{code}: {r.text}"


def test_preferences_rejects_unknown_lang(client):
    headers = _login(client, USER_EMAIL, USER_PASSWORD)
    r = client.put(
        f"{BASE}/api/auth/preferences",
        json={"preferred_lang": "klingon"},
        headers=headers,
    )
    assert r.status_code == 400


def test_preferences_disable_auto_translate(client):
    """Empty string must be accepted and disable auto-translation."""
    headers = _login(client, USER_EMAIL, USER_PASSWORD)
    r = client.put(
        f"{BASE}/api/auth/preferences",
        json={"preferred_lang": ""},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("preferred_lang") in (None, "", False)
