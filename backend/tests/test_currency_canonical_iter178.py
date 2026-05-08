"""
iter178 — Currency canonical + Revenue widget E2E
=================================================
"""
import asyncio, os, sys
sys.path.insert(0, "/app/backend")
import asyncpg, httpx
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

API = "http://localhost:8001"
DB_URL = os.environ["DATABASE_URL"]
BYPASS = "JAPAP_E2E_BYPASS_2026"


async def login(c, email, password):
    r = await c.post(f"{API}/api/auth/login", json={
        "email": email, "password": password,
        "captcha_id": BYPASS, "captcha_answer": "0",
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    return r.cookies.get("access_token")


def H(t):
    return {"Authorization": f"Bearer {t}", "X-Requested-With": "XMLHttpRequest"}


async def main():
    async with httpx.AsyncClient(timeout=30) as cli:
        admin_t = await login(cli, "admin@japap.com", "JapapAdmin2024!")
        alice_t = await login(cli, "alice@japap.com", "Alice2026!")

        # ── [1] All wallets are USD canonical now
        c = await asyncpg.connect(DB_URL)
        non_usd = await c.fetchval(
            "SELECT COUNT(*) FROM wallets WHERE currency IS DISTINCT FROM 'USD'")
        await c.close()
        assert non_usd == 0, f"non-USD wallets remain: {non_usd}"
        print(f"[1] all wallets canonical USD ✓")

        # ── [2] migration logs exist (from boot/worker sweep)
        c = await asyncpg.connect(DB_URL)
        n = await c.fetchval("SELECT COUNT(*) FROM currency_migration_logs")
        await c.close()
        assert n > 0, f"no migration log rows: {n}"
        print(f"[2] currency_migration_logs has {n} entries ✓")

        # ── [3] /api/wallet/balance exposes balance_local + currency_local + fx_rate
        r = await cli.get(f"{API}/api/wallet/balance", headers=H(alice_t))
        r.raise_for_status()
        bal = r.json()
        for k in ("balance_usd", "currency", "balance_local", "currency_local", "fx_rate"):
            assert k in bal, f"missing key {k} in {bal.keys()}"
        assert bal["currency"] == "USD"
        # FX should be a numeric string > 0
        assert float(bal["fx_rate"]) > 0
        print(f"[3] /wallet/balance has all 5 canonical keys ✓ (USD={bal['balance_usd']} / "
              f"{bal['balance_local']} {bal['currency_local']} @ FX={bal['fx_rate']})")

        # ── [4] /admin/stats returns total_balance_usd + non_usd_wallet_count
        r = await cli.get(f"{API}/api/admin/stats", headers=H(admin_t))
        r.raise_for_status()
        s = r.json()
        for k in ("total_balance_usd", "non_usd_wallet_count",
                  "total_balance_xaf_equivalent", "currency_canonical"):
            assert k in s, f"missing {k}"
        assert s["currency_canonical"] == "USD"
        assert s["non_usd_wallet_count"] == 0, f"got {s['non_usd_wallet_count']}"
        print(f"[4] /admin/stats canonical: USD={s['total_balance_usd']} XAF eq={s['total_balance_xaf_equivalent']} ✓")

        # ── [5] /admin/marketplace/revenue-summary
        r = await cli.get(f"{API}/api/admin/marketplace/revenue-summary?days=30",
                          headers=H(admin_t))
        r.raise_for_status()
        rev = r.json()
        for k in ("commissions_usd", "boosts_usd", "total_usd",
                  "active_disputes", "active_holds", "currency_canonical"):
            assert k in rev, f"missing {k}"
        assert rev["currency_canonical"] == "USD"
        assert float(rev["total_usd"]) >= 0
        print(f"[5] revenue-summary: comm={rev['commissions_usd']}$ boosts={rev['boosts_usd']}$ total={rev['total_usd']}$ ✓")

        # ── [6] Non-admin gets 403 on revenue-summary
        r = await cli.get(f"{API}/api/admin/marketplace/revenue-summary",
                          headers=H(alice_t))
        assert r.status_code == 403, f"expected 403 got {r.status_code}"
        print(f"[6] non-admin blocked on revenue-summary (403) ✓")

    print("\n✅ ALL 6 ITER178 CURRENCY/REVENUE TESTS PASS")


if __name__ == "__main__":
    asyncio.run(main())
