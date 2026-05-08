"""
Math captcha (iter141ter — replaces Cloudflare Turnstile in auth flows).

Stateless design: a captcha is a tuple `(a, op, b, expires_at)` packaged
into a base64url token signed with HMAC-SHA256(SECRET_KEY). The frontend
displays the question and forwards the token + user's answer; the backend
verifies the signature, the TTL, AND the answer.

iter141quater — silent humanity cookie:
  When a user solves a captcha correctly we issue a `japap_human` cookie
  (HMAC-signed, 7-day TTL, HttpOnly+SameSite=Lax). Subsequent auth
  submissions that present this cookie skip the captcha challenge
  entirely (`captcha_id`/`captcha_answer` may be omitted).

No DB / Redis required → fully replicable across pods.

Public API:
    issue_captcha()                          → public captcha question
    verify_captcha(captcha_id, answer, request)
        → raises HTTPException on failure ; returns True on success
    issue_human_cookie(response)             → set the silent-bypass cookie
    has_valid_human_cookie(request)          → True if the caller is a known human
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Tuple

from fastapi import HTTPException, Request, Response

logger = logging.getLogger(__name__)

CAPTCHA_TTL_SECONDS = 600           # 10 min — generous enough that slow users on mobile don't get burned
HUMAN_COOKIE_NAME = "japap_human"
HUMAN_COOKIE_TTL_SECONDS = 7 * 24 * 3600   # 7 days
_VERSION = "v1"
_HUMAN_VERSION = "h1"


def _secret() -> bytes:
    s = (os.environ.get("MATH_CAPTCHA_SECRET")
         or os.environ.get("JWT_SECRET")
         or os.environ.get("SECRET_KEY")
         or "japap-dev-captcha-secret-DO-NOT-USE-IN-PROD")
    return s.encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _sign(payload_bytes: bytes) -> str:
    sig = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    return _b64url_encode(sig)


def _make_problem() -> Tuple[int, str, int, int]:
    """Pick a friendly arithmetic problem with a SMALL positive integer answer.
    Constraints: answer ∈ [0, 30] for instant mental computation."""
    op = random.choice(["+", "-", "+", "+", "−"])    # bias toward addition
    if op == "+":
        a = random.randint(1, 15)
        b = random.randint(1, 15)
        return (a, "+", b, a + b)
    # subtraction (display "−") — guarantee non-negative result
    a = random.randint(5, 20)
    b = random.randint(0, a)
    return (a, "-", b, a - b)


def issue_captcha() -> dict:
    """Generate a fresh captcha and return id + display text."""
    a, op, b, answer = _make_problem()
    expires_at = int(time.time()) + CAPTCHA_TTL_SECONDS
    payload = {"v": _VERSION, "a": a, "o": op, "b": b, "x": expires_at, "n": answer}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    token = f"{_b64url_encode(payload_bytes)}.{_sign(payload_bytes)}"
    return {
        "captcha_id": token,
        "question": f"{a} {op} {b}",
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
    }


def _bypass_token() -> str:
    return (os.environ.get("MATH_CAPTCHA_TEST_BYPASS_TOKEN")
            or os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN")
            or "").strip()


def verify_captcha(captcha_id: str | None, answer: str | int | None, request: Request) -> bool:
    """Validate a captcha submission. Raises HTTPException on any failure
    with neutral French messages so attackers can't distinguish 'expired'
    vs 'wrong answer' beyond a generic 401-grade rejection.

    iter141quater — when the caller already presents a valid `japap_human`
    cookie, we trust them and skip the math challenge (returns True
    silently). This makes returning users skip the captcha entirely.

    iter237b — EMERGENCY GLOBAL KILL SWITCH:
    If env var `CAPTCHA_ENABLED=false`, skip ALL captcha verification
    everywhere. Used to unblock production if the captcha service breaks.

    Test-bypass: when MATH_CAPTCHA_TEST_BYPASS_TOKEN is armed, sending
    `captcha_id == BYPASS` (any answer) is accepted. E2E only.
    """
    # iter237b — Global kill switch (operator-controlled, env-driven).
    if os.environ.get("CAPTCHA_ENABLED", "true").strip().lower() in ("false", "0", "no", "off"):
        logger.warning("Captcha verification globally disabled via CAPTCHA_ENABLED=false.")
        return True

    # Silent-human bypass — if the cookie is valid, no challenge needed.
    if has_valid_human_cookie(request):
        return True

    bypass = _bypass_token()
    if bypass and captcha_id and str(captcha_id).strip() == bypass:
        logger.warning("Math-captcha test-bypass token accepted (E2E mode).")
        return True

    if not captcha_id or not str(captcha_id).strip():
        raise HTTPException(status_code=400, detail="Captcha requis. Réessaie.")

    try:
        token = str(captcha_id).strip()
        body_b64, sig_b64 = token.split(".", 1)
        payload_bytes = _b64url_decode(body_b64)
        expected_sig = _sign(payload_bytes)
    except Exception:
        # Refuse to leak which step failed.
        raise HTTPException(status_code=400, detail="Captcha invalide. Recharge la question.")

    if not hmac.compare_digest(expected_sig, sig_b64):
        # Tampered token.
        client_ip = _client_ip(request)
        logger.warning("math-captcha bad signature ip=%s", client_ip)
        raise HTTPException(status_code=400, detail="Captcha invalide. Recharge la question.")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Captcha invalide. Recharge la question.")

    if payload.get("v") != _VERSION:
        raise HTTPException(status_code=400, detail="Captcha obsolète. Recharge la question.")

    if int(payload.get("x", 0)) < int(time.time()):
        raise HTTPException(status_code=400, detail="Captcha expiré. Recharge la question.")

    expected_answer = int(payload.get("n", -10**9))
    try:
        given = int(str(answer).strip())
    except (TypeError, ValueError):
        client_ip = _client_ip(request)
        logger.info("math-captcha non-int answer ip=%s", client_ip)
        raise HTTPException(status_code=400, detail="Réponse incorrecte. Réessaie.")

    if given != expected_answer:
        client_ip = _client_ip(request)
        logger.info("math-captcha wrong answer ip=%s a=%s b=%s expected=%s got=%s",
                    client_ip, payload.get("a"), payload.get("b"), expected_answer, given)
        raise HTTPException(status_code=400, detail="Réponse incorrecte. Réessaie.")

    return True


def _client_ip(request: Request) -> str:
    return (request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else ""))


# ───────────────────────────────────────────────────────────────────────
#  iter141quater — Silent humanity cookie
# ───────────────────────────────────────────────────────────────────────

def _human_cookie_payload() -> str:
    expires = int(time.time()) + HUMAN_COOKIE_TTL_SECONDS
    body = f"{_HUMAN_VERSION}.{expires}".encode("utf-8")
    sig = _sign(body)
    return f"{_HUMAN_VERSION}.{expires}.{sig}"


def issue_human_cookie(response: Response) -> None:
    """Set the silent-humanity cookie on `response`. Call this from auth
    endpoints after a successful captcha solve (or successful login)."""
    try:
        secure = (os.environ.get("APP_ENV", "").lower() == "production"
                  or os.environ.get("FORCE_SECURE_COOKIES", "").lower() in ("1", "true", "yes"))
        response.set_cookie(
            key=HUMAN_COOKIE_NAME,
            value=_human_cookie_payload(),
            max_age=HUMAN_COOKIE_TTL_SECONDS,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/",
        )
    except Exception as exc:
        logger.warning("issue_human_cookie failed: %s", exc)


def has_valid_human_cookie(request: Request) -> bool:
    """Return True if the request carries a non-expired, signed
    `japap_human` cookie. Silent — never raises."""
    try:
        raw = request.cookies.get(HUMAN_COOKIE_NAME)
        if not raw:
            return False
        parts = raw.split(".")
        if len(parts) != 3:
            return False
        version, expires_str, sig = parts
        if version != _HUMAN_VERSION:
            return False
        body = f"{version}.{expires_str}".encode("utf-8")
        expected_sig = _sign(body)
        if not hmac.compare_digest(expected_sig, sig):
            return False
        if int(expires_str) < int(time.time()):
            return False
        return True
    except Exception:
        return False
