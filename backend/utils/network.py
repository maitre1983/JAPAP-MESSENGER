"""
iter146/iter147 — Network utilities.

Shared helpers for resolving the *real* client identity behind the
kubernetes ingress / Cloudflare proxy. Use these everywhere instead of
`request.client.host` so that load-balanced upstream pod IPs (10.208.x.x)
don't leak into login_attempts, audit logs, trusted-device fingerprints,
or anti-abuse counters.
"""
from __future__ import annotations

from fastapi import Request


def client_ip(request: Request) -> str:
    """Resolve the *real* client IP behind the kubernetes ingress.

    Resolution order (most-trusted last to first hop):
        1. `cf-connecting-ip` — Cloudflare-set, never spoofable past CF.
        2. `x-forwarded-for` — first hop is the original client per RFC 7239.
        3. `request.client.host` — socket peer (in-cluster pod IP fallback).

    Returns the literal string ``"unknown"`` only when none of the layers
    expose anything (which essentially never happens in production).
    """
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
