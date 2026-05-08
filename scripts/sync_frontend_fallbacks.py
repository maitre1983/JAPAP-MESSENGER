#!/usr/bin/env python3
"""Sync the frontend static fallbacks from the canonical backend sources.

Regenerates:
    /app/frontend/src/data/countries.json      (from backend.routes.geo.COUNTRIES)
    /app/frontend/src/data/currency_rates.json (from a live /api/currency/rates call)

Run locally before every release:

    python3 scripts/sync_frontend_fallbacks.py [--api http://localhost:8001]

Exit code 0 on success, 1 on any failure (for CI pre-commit hooks).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FE_DATA = ROOT / "frontend" / "src" / "data"
FE_DATA.mkdir(parents=True, exist_ok=True)


def sync_countries() -> int:
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from routes.geo import COUNTRIES  # type: ignore
    except Exception as e:
        print(f"[countries] FAIL — cannot import backend.routes.geo.COUNTRIES: {e}")
        return 1
    sorted_c = sorted(COUNTRIES, key=lambda c: c["name"])
    out = FE_DATA / "countries.json"
    out.write_text(json.dumps(sorted_c, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"[countries] OK — {len(sorted_c)} rows → {out.relative_to(ROOT)}")
    return 0


def sync_rates(api_base: str) -> int:
    import urllib.request
    import urllib.error

    url = api_base.rstrip("/") + "/api/currency/rates"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f"[rates] FAIL — {url}: {e}")
        return 1

    rates = data.get("rates") or {}
    symbols = data.get("symbols") or {}
    if len(rates) < 20:
        print(f"[rates] FAIL — only {len(rates)} rates, refusing to overwrite snapshot")
        return 1

    snapshot = {
        "base": data.get("base", "USD"),
        "rates": rates,
        "symbols": symbols,
        "snapshot_at": data.get("updated_at") or "",
    }
    out = FE_DATA / "currency_rates.json"
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"[rates] OK — {len(rates)} rates + {len(symbols)} symbols → {out.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("SYNC_API_BASE", "http://localhost:8001"))
    args = ap.parse_args()
    rc1 = sync_countries()
    rc2 = sync_rates(args.api)
    return rc1 or rc2


if __name__ == "__main__":
    sys.exit(main())
