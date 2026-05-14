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
  showEquivalent: true,
  refresh: () => {},
  setForcedCurrency: () => {},
  setShowEquivalent: () => {},
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

  // iter240g — Explicit user toggle in Profile. When set, overrides the
  // default "hide if same currency" logic globally for this user.
  const [showEquivalent, setShowEquivalent] = useState(() => {
    try { return localStorage.getItem('japap_show_local_equivalent') !== '0'; }
    catch { return true; }
  });
  const persistShowEquivalent = useCallback((v) => {
    setShowEquivalent(!!v);
    try { localStorage.setItem('japap_show_local_equivalent', v ? '1' : '0'); } catch { /* ignore */ }
  }, []);

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
      value={{ ...state, refresh: loadRates, setForcedCurrency: setForced,
               showEquivalent, setShowEquivalent: persistShowEquivalent }}
    >
      {children}
    </CurrencyContext.Provider>
  );
}

/**
 * Format a USD amount into the user's local currency.
 *
 * Default behavior (iter83): "₦12,500 (~$8.02)" — locale-first, USD hint.
 * iter240g: opt `usdFirst: true` inverts to "$8.02 (~₦12,500)" so amounts
 * stay readable globally while showing the regional equivalent inline.
 *
 * @param amountUsd number — amount stored in USD
 * @param ctx       result of useCurrency()
 * @param opts      { showUsdHint?: boolean, short?: boolean, usdFirst?: boolean,
 *                    showLocalEquivalent?: boolean }
 */
export function formatMoney(amountUsd, ctx, opts = {}) {
  const { local = 'USD', symbol = '$', rateVsUsd = 1 } = ctx || {};
  const {
    showUsdHint = true, short = false, usdFirst = false,
    // iter240g — explicit override. Defaults to: hide equivalent if local===USD.
    showLocalEquivalent = local !== 'USD',
  } = opts;
  const usd = Number(amountUsd) || 0;
  const localAmt = usd * (Number(rateVsUsd) || 1);
  const decimals = localAmt >= 1000 ? 0 : 2;
  const formatted = localAmt.toLocaleString('fr-FR', {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  });
  // Most African symbols are suffix (FCFA, CFA, KSh, ₦, etc.), USD/EUR/GBP are prefix.
  const prefixSymbols = new Set(['$', '€', '£', 'C$', 'A$', 'S$', 'R$', '₹', '¥', '₩', '₱', '₫', '₺', '₽', '₪', '₴', '₮', '₦']);
  const isPrefix = prefixSymbols.has(symbol);
  const mainLocal = isPrefix ? `${symbol}${formatted}` : `${formatted} ${symbol}`;
  const usdFormatted = usd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const mainUsd = `$${usdFormatted}`;

  // iter240g — USD-first mode (the "$1.00 (≈ 615 FCFA)" pattern requested
  // by product). When the user's local currency IS USD, just return $X.
  if (usdFirst) {
    if (short || !showLocalEquivalent) return mainUsd;
    return `${mainUsd} (≈ ${mainLocal})`;
  }

  if (short || local === 'USD' || !showUsdHint) return mainLocal;
  return `${mainLocal} (~$${usdFormatted})`;
}

export function formatLocal(amountUsd, ctx) {
  return formatMoney(amountUsd, ctx, { showUsdHint: false });
}
