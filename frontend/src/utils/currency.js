/**
 * JAPAP — Currency formatter
 * ==========================
 * Backend stores amounts in USD by default (or per-wallet currency).
 * Frontend shows the user's local currency via CurrencyContext but ALWAYS
 * emits the USD equivalent as a muted hint next to the local amount.
 *
 * iter83 P0 hardening — static bundle fallback: if the `/api/currency/rates`
 * or `/api/currency/detect` endpoint is unreachable / returns garbage, we
 * still ship a last-known-good snapshot so every amount on the app keeps
 * rendering in the user's local currency. The live call is kept as a
 * progressive refresh only.
 */
import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { useAuth } from '@/context/AuthContext';
import RATES_FALLBACK from '@/data/currency_rates.json';

const API = process.env.REACT_APP_BACKEND_URL;

const CurrencyContext = createContext({
  local: 'USD',
  symbol: '$',
  rates: RATES_FALLBACK.rates,
  symbols: RATES_FALLBACK.symbols,
  rateVsUsd: 1,
  country: null,
  refresh: () => {},
  setForcedCurrency: () => {},
});

export const useCurrency = () => useContext(CurrencyContext);

export function CurrencyProvider({ children, userCountry }) {
  const { user } = useAuth();
  // iter83 — seed with the static bundle so formatMoney() works on first
  // paint even when the backend is down or still warming up.
  const [state, setState] = useState({
    local: 'USD', symbol: '$',
    rates: RATES_FALLBACK.rates,
    symbols: RATES_FALLBACK.symbols,
    rateVsUsd: 1, country: null,
  });
  const [forced, setForced] = useState(null);

  // iter74: if the authenticated user has a `preferred_currency` stored
  // server-side (auto-detected at signup or manually set in Profile), it
  // takes precedence over IP-based detection. Anonymous visitors keep
  // the /detect IP flow so they see local prices before signing up.
  const userPref = user?.preferred_currency || null;

  const loadRates = useCallback(async () => {
    try {
      const [ratesRes, detectRes] = await Promise.all([
        axios.get(`${API}/api/currency/rates`).catch(() => null),
        axios.get(`${API}/api/currency/detect`, {
          params: userCountry ? { country: userCountry } : {},
        }).catch(() => null),
      ]);
      // Never let an empty / broken response wipe the bundle snapshot.
      const apiRates = ratesRes?.data?.rates;
      const apiSymbols = ratesRes?.data?.symbols;
      const rates = (apiRates && Object.keys(apiRates).length >= 20)
        ? apiRates : RATES_FALLBACK.rates;
      const symbols = (apiSymbols && Object.keys(apiSymbols).length >= 10)
        ? apiSymbols : RATES_FALLBACK.symbols;
      const d = detectRes?.data || {};
      // Precedence: explicit forced (UI toggle) > user.preferred_currency > /detect
      const targetCode = forced || userPref || d.currency || 'USD';
      const targetRate = rates[targetCode] ?? d.rate_vs_usd ?? 1;
      setState({
        local: targetCode,
        symbol: symbols?.[targetCode] || d.symbol || targetCode,
        rates, symbols, rateVsUsd: targetRate, country: d.country || null,
      });
    } catch {
      // Defensive catch-all — keep whatever fallback we already had.
    }
  }, [userCountry, forced, userPref]);

  useEffect(() => { loadRates(); }, [loadRates]);

  return (
    <CurrencyContext.Provider
      value={{ ...state, refresh: loadRates, setForcedCurrency: setForced }}
    >
      {children}
    </CurrencyContext.Provider>
  );
}

/**
 * Format a USD amount into the user's local currency.
 * Returns "₦12,500 (~$8.02)" style string.
 *
 * @param amountUsd number — amount stored in USD
 * @param ctx       result of useCurrency()
 * @param opts      { showUsdHint?: boolean, short?: boolean }
 */
export function formatMoney(amountUsd, ctx, opts = {}) {
  const { local = 'USD', symbol = '$', rateVsUsd = 1 } = ctx || {};
  const { showUsdHint = true, short = false } = opts;
  const usd = Number(amountUsd) || 0;
  const localAmt = usd * (Number(rateVsUsd) || 1);
  const decimals = localAmt >= 1000 ? 0 : 2;
  const formatted = localAmt.toLocaleString('fr-FR', {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  });
  // Most African symbols are suffix (FCFA, CFA, KSh, ₦, etc.), USD/EUR/GBP are prefix.
  const prefixSymbols = new Set(['$', '€', '£', 'C$', 'A$', 'S$', 'R$', '₹', '¥', '₩', '₱', '₫', '₺', '₽', '₪', '₴', '₮', '₦']);
  const isPrefix = prefixSymbols.has(symbol);
  const main = isPrefix ? `${symbol}${formatted}` : `${formatted} ${symbol}`;
  if (short || local === 'USD' || !showUsdHint) return main;
  const usdFormatted = usd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${main} (~$${usdFormatted})`;
}

export function formatLocal(amountUsd, ctx) {
  return formatMoney(amountUsd, ctx, { showUsdHint: false });
}
