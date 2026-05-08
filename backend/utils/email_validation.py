"""
JAPAP — Email validation + tier classification (iter155)
=========================================================
Smart per-address classification used at every send-side path:

    Tier 1 — Mainstream providers (Gmail, Outlook, Yahoo, iCloud, Proton…).
             Highest deliverability. Always allowed.
    Tier 2 — Any other syntactically valid domain. Allowed unless the
             address itself previously hard-bounced.
    Tier 3 — At-risk: malformed address, true disposable / throwaway
             service, OR previously hard-bounced / spam-complained.
             Blocked from on-demand sends AND from any future broadcast
             targeting.

Design intent (iter155):
  • Don't blacklist by domain reputation gut-feel ("looks weird" ≠ bad).
  • Trust **observed** bounces (Resend webhook → `email_logs`) instead.
  • Keep the disposable list tight — only universally-recognised throwaway
    services. Anything else gets a chance.
"""
from __future__ import annotations

import re
from typing import Optional


_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
)

# True disposable / throwaway providers — universally recognised.
# Removed iter155: vague "looks suspicious" entries (advarm.com,
# hidingmail.com, quickemailinbox.shop, emailsystems.shop, *.epac.to).
# We now rely on actual hard-bounce evidence for those instead of
# guessing by domain name.
_DISPOSABLE_DOMAINS = frozenset({
    "10minutemail.com", "10minutemail.net", "20minutemail.com",
    "guerrillamail.com", "guerrillamail.net", "guerrillamail.org",
    "guerrillamail.biz", "guerrillamailblock.com",
    "mailinator.com", "mailinator.net", "mailinator.org",
    "tempmail.com", "tempmail.net", "tempmail.org", "temp-mail.org",
    "tempr.email", "throwawaymail.com", "throwaway.email",
    "yopmail.com", "yopmail.fr", "yopmail.net",
    "fakeinbox.com", "trashmail.com", "trashmail.net", "trashmail.de",
    "dispostable.com", "maildrop.cc", "sharklasers.com", "spam4.me",
    "getairmail.com", "spambox.us", "anonymousemail.me",
    "mohmal.com", "mailnesia.com", "fakemail.net", "fakemailgenerator.com",
    "emailondeck.com", "burnermail.io", "harakirimail.com",
    "guerrillamail.de", "mintemail.com", "minutemail.com",
})

# Mainstream providers — Tier 1. Mostly anglo + EU + global webmail.
TIER1_DOMAINS = frozenset({
    # Google
    "gmail.com", "googlemail.com",
    # Microsoft
    "outlook.com", "outlook.fr", "outlook.de", "outlook.es", "outlook.co.uk",
    "hotmail.com", "hotmail.fr", "hotmail.co.uk", "hotmail.de", "hotmail.es",
    "hotmail.it", "live.com", "live.fr", "live.co.uk", "msn.com",
    # Yahoo
    "yahoo.com", "yahoo.fr", "yahoo.co.uk", "yahoo.de", "yahoo.es",
    "yahoo.it", "yahoo.ca", "ymail.com", "rocketmail.com",
    # Apple
    "icloud.com", "me.com", "mac.com",
    # Privacy-first
    "proton.me", "protonmail.com", "pm.me", "tutanota.com", "tutanota.de",
    # Other widely-used
    "aol.com", "aol.fr", "gmx.com", "gmx.fr", "gmx.de",
    "mail.com", "zoho.com", "fastmail.com", "fastmail.fm",
    # FR / Africa-relevant ISPs (high reach in JAPAP user base)
    "orange.fr", "wanadoo.fr", "free.fr", "laposte.net", "sfr.fr",
    "bbox.fr", "neuf.fr",
})


def is_valid_email_format(email: str) -> bool:
    """RFC-5322-lite syntax check. Returns False on empty / malformed."""
    if not email or "@" not in email:
        return False
    e = email.strip().lower()
    if len(e) > 254:
        return False
    return bool(_EMAIL_RE.match(e))


def domain_of(email: str) -> str:
    return email.strip().lower().split("@", 1)[1] if "@" in email else ""


def is_disposable_domain(email: str) -> bool:
    """True only for universally-recognised throwaway providers."""
    d = domain_of(email)
    if not d:
        return False
    return d in _DISPOSABLE_DOMAINS


async def is_hard_bounced(conn, email: str) -> bool:
    """True if this address previously hard-bounced or filed a spam
    complaint, attested by `email_logs.event` populated by the Resend
    webhook.
    """
    row = await conn.fetchrow(
        """SELECT 1 FROM email_logs
            WHERE LOWER(email) = $1
              AND event IN ('bounced', 'complained')
            LIMIT 1""",
        email.strip().lower(),
    )
    return row is not None


# ── iter155 — Tier classification ──────────────────────────────────────
async def classify_email(conn, email: str) -> dict:
    """Return the tier + reason for an email address.

    Returns: {
        "tier":       1 | 2 | 3,
        "reason":     ""  | "invalid_format" | "disposable_domain" | "hard_bounced",
        "tier_label": "mainstream" | "other_valid" | "risky",
        "domain":     str (lowercased; empty if invalid),
    }
    """
    if not is_valid_email_format(email):
        return {"tier": 3, "reason": "invalid_format",
                "tier_label": "risky", "domain": ""}
    d = domain_of(email)
    if d in _DISPOSABLE_DOMAINS:
        return {"tier": 3, "reason": "disposable_domain",
                "tier_label": "risky", "domain": d}
    if await is_hard_bounced(conn, email):
        return {"tier": 3, "reason": "hard_bounced",
                "tier_label": "risky", "domain": d}
    if d in TIER1_DOMAINS:
        return {"tier": 1, "reason": "", "tier_label": "mainstream",
                "domain": d}
    return {"tier": 2, "reason": "", "tier_label": "other_valid",
            "domain": d}


async def gating_reason(conn, email: str) -> Optional[str]:
    """Convenience wrapper around `classify_email` for the on-demand
    auth flow. Returns the Tier-3 reason code, or None if the address
    is allowed to receive mail (Tier 1 or 2).
    """
    info = await classify_email(conn, email)
    return info["reason"] if info["tier"] == 3 else None
