"""
iter239h — Vendor Health Service.

In-process registry that periodically pings every critical external
dependency and surfaces the status to an admin dashboard. Strictly
read-only — never touches the dependency's data, only checks reachability
+ latency.

Vendors monitored:
    • Hubtel MoMo (status API + auth)
    • Paystack (status check via /transaction/verify with a fake ref)
    • Tronscan (USDT-TRC20 blockchain explorer)
    • BSC RPC (Binance Smart Chain node)
    • FX API (exchange rate source)
    • Fixie outbound proxy

The cron loop runs every 5 min and stashes the latest result in
`_vendor_state[vendor_name]`. Admins can also force an immediate refresh
via `POST /api/admin/vendor-health/refresh`.

All HTTP calls are through `httpx.AsyncClient` with a short timeout —
the goal is reachability, not deep functional testing.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from services.proxy_config import get_proxy_url

logger = logging.getLogger(__name__)

PING_INTERVAL_SECONDS = 300  # 5 min
SLOW_THRESHOLD_MS = 1500     # > 1.5s = SLOW
TIMEOUT_SECONDS = 8.0

# Status enum (string for JSON-friendliness).
STATUS_OK   = "ok"
STATUS_SLOW = "slow"
STATUS_DOWN = "down"

# Initial vendor template — values are overwritten by `_run_check`.
_VENDOR_TEMPLATE: dict[str, Any] = {
    "status": "unknown",
    "latency_ms": None,
    "http_code": None,
    "error": None,
    "last_check": None,
    "url_probed": None,
}

_vendor_state: dict[str, dict[str, Any]] = {}
_loop_started = False
_loop_lock = asyncio.Lock()


# ───────────────────────── ping implementations ────────────────────────
async def _ping_hubtel(client: httpx.AsyncClient) -> tuple[str, dict]:
    """Ping the Hubtel txn-status endpoint with a bogus client reference.
    A 4xx with a JSON body = service reachable + auth valid. A 5xx /
    network error = down."""
    from services.hubtel_momo import get_collection_account, get_hubtel_auth
    auth = await get_hubtel_auth()
    account = await get_collection_account()
    if not auth or not account:
        return "Hubtel MoMo", {"error": "credentials missing",
                               "url_probed": "https://api-txnstatus.hubtel.com/transactions/.../status"}
    url = (f"https://api-txnstatus.hubtel.com/transactions/{account}/status"
           f"?clientReference=japap_health_check_ping")
    r = await client.get(url, headers={"Authorization": f"Basic {auth}"})
    # Hubtel responds 4xx for unknown refs — that's expected & means auth OK.
    code = r.status_code
    ok = code in (200, 400, 404, 422)
    return "Hubtel MoMo", {"http_code": code,
                            "error": None if ok else f"HTTP {code}",
                            "url_probed": url}


async def _ping_paystack(client: httpx.AsyncClient) -> tuple[str, dict]:
    """Ping Paystack's verify endpoint with a bogus reference."""
    from services.settings_service import get_setting
    key = (await get_setting("paystack_secret_key")) \
          or os.environ.get("PAYSTACK_SECRET_KEY", "")
    if not key:
        return "Paystack", {"error": "secret key missing",
                            "url_probed": "https://api.paystack.co/transaction/verify/..."}
    url = "https://api.paystack.co/transaction/verify/japap_health_ping"
    r = await client.get(url, headers={"Authorization": f"Bearer {key}"})
    # Paystack returns 404 for unknown ref + 401 for bad key. Either way
    # the network/TLS handshake worked, so SERVICE is reachable.
    code = r.status_code
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    ok = code in (200, 404)
    return "Paystack", {"http_code": code,
                        "error": None if ok else f"{code}: {body.get('message') or 'auth failure'}",
                        "url_probed": url}


async def _ping_tronscan(client: httpx.AsyncClient) -> tuple[str, dict]:
    url = "https://apilist.tronscanapi.com/api/system/status"
    r = await client.get(url)
    return "Tronscan (USDT-TRC20)", {"http_code": r.status_code,
                                      "error": None if r.status_code == 200 else f"HTTP {r.status_code}",
                                      "url_probed": url}


async def _ping_bsc(client: httpx.AsyncClient) -> tuple[str, dict]:
    url = "https://bsc-dataseed1.binance.org/"
    payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [],
               "id": 1}
    r = await client.post(url, json=payload)
    try:
        block = int(r.json().get("result", "0x0"), 16)
    except Exception:
        block = None
    return "BSC RPC (USDT-BEP20)", {"http_code": r.status_code,
                                     "error": None if r.status_code == 200 and block
                                              else f"HTTP {r.status_code} block={block}",
                                     "url_probed": url,
                                     "extra": {"latest_block": block}}


async def _ping_fx(client: httpx.AsyncClient) -> tuple[str, dict]:
    """Ping the FX rate source through our `fx_service` to validate the
    full chain (network + cache + parsing)."""
    try:
        from services.fx_service import get_usd_to_ghs_info
        t0 = asyncio.get_event_loop().time()
        info = await get_usd_to_ghs_info()
        latency = int((asyncio.get_event_loop().time() - t0) * 1000)
        rate = info.get("rate") if isinstance(info, dict) else None
        return "FX rate API", {"http_code": 200,
                                "error": None if rate else "no rate returned",
                                "extra": {"USD_GHS": rate, "source": info.get("source") if isinstance(info, dict) else None,
                                          "latency_ms_inner": latency},
                                "url_probed": "fx_service.get_usd_to_ghs_info()"}
    except Exception as e:  # noqa: BLE001
        return "FX rate API", {"http_code": None,
                                "error": f"{type(e).__name__}: {e}",
                                "url_probed": "fx_service.get_usd_to_ghs_info"}


async def _ping_fixie(client: httpx.AsyncClient) -> tuple[str, dict]:
    """Verify the Fixie outbound proxy is reachable AND returns the
    expected dedicated egress IP. Uses a tiny `httpbin.org/ip` request."""
    proxy = get_proxy_url()
    if not proxy:
        return "Fixie proxy", {"error": "no FIXIE_URL configured",
                               "url_probed": None}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS,
                                      proxy=proxy) as proxied:
            r = await proxied.get("https://api.ipify.org?format=json")
        ip = (r.json() or {}).get("ip") if r.status_code == 200 else None
        return "Fixie proxy", {"http_code": r.status_code,
                                "error": None if ip else f"HTTP {r.status_code}",
                                "extra": {"egress_ip": ip},
                                "url_probed": "https://api.ipify.org via Fixie"}
    except Exception as e:  # noqa: BLE001
        return "Fixie proxy", {"http_code": None,
                                "error": f"{type(e).__name__}: {e}",
                                "url_probed": "https://api.ipify.org via Fixie"}


VENDORS: list[Callable[[httpx.AsyncClient], Awaitable[tuple[str, dict]]]] = [
    _ping_hubtel,
    _ping_paystack,
    _ping_tronscan,
    _ping_bsc,
    _ping_fx,
    _ping_fixie,
]


# ─────────────────────────── orchestration ─────────────────────────────
async def _run_check(ping_fn: Callable[[httpx.AsyncClient], Awaitable[tuple[str, dict]]]) -> None:
    """Execute one ping and update `_vendor_state` accordingly."""
    name = None
    t0 = asyncio.get_event_loop().time()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS,
                                      follow_redirects=False) as client:
            name, result = await ping_fn(client)
        latency_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
        result["latency_ms"] = latency_ms
        result["last_check"] = datetime.now(timezone.utc).isoformat()
        if result.get("error"):
            result["status"] = STATUS_DOWN
        elif latency_ms > SLOW_THRESHOLD_MS:
            result["status"] = STATUS_SLOW
        else:
            result["status"] = STATUS_OK
        if name:
            _vendor_state[name] = result
    except Exception as e:  # noqa: BLE001
        # If even the wrapper crashed, attribute to the function name.
        n = (name or ping_fn.__name__).removeprefix("_ping_")
        _vendor_state[n] = {
            "status": STATUS_DOWN,
            "error": f"{type(e).__name__}: {e}",
            "latency_ms": int((asyncio.get_event_loop().time() - t0) * 1000),
            "last_check": datetime.now(timezone.utc).isoformat(),
        }


async def run_all_checks() -> dict[str, dict]:
    """Run every vendor ping in parallel and return the fresh state."""
    await asyncio.gather(*[_run_check(fn) for fn in VENDORS],
                         return_exceptions=True)
    return get_vendor_state()


def get_vendor_state() -> dict:
    """Snapshot suitable for JSON serialisation."""
    return {name: dict(state) for name, state in _vendor_state.items()}


async def vendor_health_loop() -> None:
    """Background task: refresh every PING_INTERVAL_SECONDS forever."""
    global _loop_started
    async with _loop_lock:
        if _loop_started:
            return
        _loop_started = True
    logger.info("[vendor-health] loop started (interval=%ss)", PING_INTERVAL_SECONDS)
    while True:
        try:
            await run_all_checks()
        except Exception as e:  # noqa: BLE001
            logger.error("[vendor-health] loop iteration failed: %s", e)
        await asyncio.sleep(PING_INTERVAL_SECONDS)


__all__ = ["run_all_checks", "get_vendor_state", "vendor_health_loop"]
