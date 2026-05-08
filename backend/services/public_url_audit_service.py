"""
JAPAP — Public URL audit service (iter168.2)
=============================================
Shared audit logic used by:
  • GET /api/admin/public-url-audit (admin endpoint, on-demand)
  • Weekly cron in `public_url_audit_worker.py` (autonomous monitor)

Returns a structured findings report. Side-effect-free — callers decide
whether to email/alert based on `status`.
"""
import logging
import os
import subprocess
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


async def run_audit(days: int = 30, limit: int = 50) -> dict:
    """Scan email_logs.url + source code for non-canonical JAPAP URLs.

    Args:
        days: lookback window for email_logs.
        limit: max email_logs samples to return (full count is also reported).

    Returns the same shape consumed by the admin endpoint:
        {
          scope_days, config, banned_hosts,
          email_logs: { total_with_url_in_window, scanned_offenders,
                        flagged_count, samples },
          code: { active_legacy_references, findings },
          status: "clean" | "warning",
        }
    """
    from database import get_pool
    from utils.public_url import public_base_url, short_domain

    canonical = urlparse(public_base_url(None)).netloc.lower()
    banned_hosts = {"japap.app", "www.japap.app"}
    if canonical == "japapmessenger.com":
        banned_hosts.add("japap-refactor.preview.emergentagent.com")

    # ── 1. email_logs scan ──────────────────────────────────────────
    pool = await get_pool()
    async with pool.acquire() as conn:
        offending_rows = await conn.fetch(
            """SELECT log_id, email, event, url, created_at
               FROM email_logs
               WHERE url IS NOT NULL
                 AND created_at >= NOW() - make_interval(days => $1)
                 AND (url ILIKE '%japap.app%'
                      OR url ILIKE '%preview.emergentagent.com%')
               ORDER BY created_at DESC
               LIMIT $2""",
            days, limit,
        )
        total_with_url = await conn.fetchval(
            """SELECT COUNT(*) FROM email_logs
               WHERE url IS NOT NULL
                 AND created_at >= NOW() - make_interval(days => $1)""",
            days,
        )

    email_findings = []
    flagged_count = 0
    for r in offending_rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat()
        host = (urlparse(d["url"] or "").netloc or "").lower()
        d["host"] = host
        d["is_banned"] = host in banned_hosts
        if d["is_banned"]:
            flagged_count += 1
        email_findings.append(d)

    # ── 2. code grep ────────────────────────────────────────────────
    code_findings = []
    try:
        proc = subprocess.run(
            ["grep", "-rn", "-E", r"https?://(www\.)?japap\.app",
             "/app/backend/routes", "/app/backend/services",
             "/app/frontend/src",
             "--include=*.py", "--include=*.js", "--include=*.jsx"],
            capture_output=True, text=True, timeout=4,
        )
        for line in proc.stdout.split("\n"):
            if not line:
                continue
            try:
                file_part, lineno_part, content = line.split(":", 2)
            except ValueError:
                continue
            stripped = content.lstrip()
            if stripped.startswith(("#", "//", "*", '"""', "'''")):
                continue
            code_findings.append({
                "file": file_part.replace("/app/", ""),
                "line": int(lineno_part) if lineno_part.isdigit() else 0,
                "content": content.strip()[:200],
            })
    except Exception as e:
        logger.warning(f"public-url-audit grep failed: {e}")

    config = {
        "public_base_url": public_base_url(None),
        "short_domain": short_domain(),
        "PUBLIC_APP_URL": os.environ.get("PUBLIC_APP_URL", ""),
        "FRONTEND_URL": os.environ.get("FRONTEND_URL", ""),
        "is_production_resolved": canonical == "japapmessenger.com",
    }

    return {
        "scope_days": days,
        "config": config,
        "banned_hosts": sorted(banned_hosts),
        "email_logs": {
            "total_with_url_in_window": total_with_url or 0,
            "scanned_offenders": len(email_findings),
            "flagged_count": flagged_count,
            "samples": email_findings,
        },
        "code": {
            "active_legacy_references": len(code_findings),
            "findings": code_findings,
        },
        "status": ("clean" if (flagged_count == 0 and not code_findings)
                   else "warning"),
    }
