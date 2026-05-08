/**
 * iter209 — WalletDepositCurrencySelector
 *
 * UX: shows a currency dropdown next to the USD amount input, then live-displays
 * the local-currency equivalent (debounced 400 ms). Default currency is
 * resolved from user.country_code → /api/geo/detect → fallback USD.
 *
 * Design: backend stays the source of truth — frontend just previews.
 *   • POST /api/payments/hubtel/initiate is always called with USD amount
 *     (caller's responsibility to pass amount_usd; this component is purely
 *     presentational + emits onChange({ currency, rate, amount_local })).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { useTranslation } from 'react-i18next';
import { CaretDown, ArrowsLeftRight } from '@phosphor-icons/react';
import * as WS from '../../utils/walletSecurity';

const API = process.env.REACT_APP_BACKEND_URL;

const FALLBACK_CURRENCIES = [
  { code: 'USD', name: 'US Dollar',     symbol: '$',    flag: '🇺🇸' },
  { code: 'GHS', name: 'Ghana Cedi',    symbol: '₵',    flag: '🇬🇭' },
  { code: 'XOF', name: 'CFA West',      symbol: 'FCFA', flag: '🇨🇮' },
  { code: 'XAF', name: 'CFA Central',   symbol: 'FCFA', flag: '🇨🇲' },
  { code: 'NGN', name: 'Naira',         symbol: '₦',    flag: '🇳🇬' },
  { code: 'EUR', name: 'Euro',          symbol: '€',    flag: '🇪🇺' },
];


export default function WalletDepositCurrencySelector({
  amountUsd,
  onChange,
  defaultCurrency,
  countryCode,
}) {
  const { t } = useTranslation();
  const [currencies, setCurrencies] = useState(FALLBACK_CURRENCIES);
  const [countryMap, setCountryMap] = useState({});
  const [currency, setCurrency] = useState(defaultCurrency || 'USD');
  const [rate, setRate] = useState(null);
  const [amountLocal, setAmountLocal] = useState(null);
  const [loadingRate, setLoadingRate] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const debRef = useRef(null);

  // Load supported currencies once
  useEffect(() => {
    let alive = true;
    axios.get(`${API}/api/payments/hubtel/currencies`).then(({ data }) => {
      if (!alive) return;
      const safe = WS.safeCurrencyList(data?.currencies);
      if (safe.length) {
        setCurrencies(safe);
      }
      if (data?.country_to_currency) {
        setCountryMap(data.country_to_currency);
      }
    }).catch(() => {
      // keep fallback list
    });
    return () => { alive = false; };
  }, []);

  // Resolve default currency once we know the country
  useEffect(() => {
    if (defaultCurrency) {
      setCurrency(defaultCurrency);
      return;
    }
    let alive = true;
    const resolve = async () => {
      let cc = (countryCode || '').toUpperCase();
      if (!cc) {
        try {
          const { data } = await axios.get(`${API}/api/geo/detect`);
          cc = (data?.country_code || '').toUpperCase();
        } catch {
          // ignore
        }
      }
      if (!alive) return;
      const map = countryMap || {};
      const cur = map[cc] || 'USD';
      setCurrency(cur);
    };
    resolve();
    return () => { alive = false; };
  }, [countryCode, defaultCurrency, countryMap]);

  // Live rate fetch on amount/currency change (debounced 400ms)
  useEffect(() => {
    const n = parseFloat(amountUsd);
    if (!n || n <= 0) {
      setRate(null);
      setAmountLocal(null);
      onChange?.({ currency, rate: null, amount_local: null });
      return;
    }
    if (debRef.current) clearTimeout(debRef.current);
    setLoadingRate(true);
    setError('');
    debRef.current = setTimeout(async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/payments/hubtel/exchange-rate`,
          { params: { currency, amount_usd: n } },
        );
        setRate(data.rate);
        setAmountLocal(data.amount_local);
        onChange?.({
          currency: data.currency,
          rate: data.rate,
          amount_local: data.amount_local,
        });
      } catch (e) {
        setError(t('wallet.deposit_currency.rate_error') || 'Taux indisponible');
        setRate(null);
        setAmountLocal(null);
        onChange?.({ currency, rate: null, amount_local: null });
      } finally {
        setLoadingRate(false);
      }
    }, 400);
    return () => { if (debRef.current) clearTimeout(debRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [amountUsd, currency]);

  const selected = useMemo(
    () => currencies.find(c => c.code === currency) || currencies[0],
    [currency, currencies],
  );

  const formatLocal = (n) => {
    if (n == null) return '—';
    const num = Number(n);
    return num.toLocaleString('fr-FR', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  };

  return (
    <div data-testid="wallet-deposit-currency-selector" className="space-y-2">
      {/* Currency dropdown */}
      <div>
        <label className="jp-label">
          {t('wallet.deposit_currency.local_currency') || 'Devise locale'}
        </label>
        <div className="relative">
          <button
            type="button"
            data-testid="currency-selector-trigger"
            onClick={() => setOpen(o => !o)}
            className="jp-input text-sm w-full flex items-center justify-between gap-2"
            aria-haspopup="listbox"
            aria-expanded={open}
          >
            <span className="flex items-center gap-2">
              <span aria-hidden="true">{selected?.flag || '🌍'}</span>
              <span className="font-bold">{selected?.code}</span>
              <span style={{ opacity: 0.6 }}>· {selected?.name}</span>
            </span>
            <CaretDown size={14} weight="bold" />
          </button>
          {open && (
            <div
              role="listbox"
              data-testid="currency-selector-list"
              className="absolute z-30 mt-1 w-full rounded-xl shadow-lg max-h-64 overflow-auto"
              style={{
                background: 'var(--jp-bg)',
                border: '1px solid var(--jp-border)',
              }}
            >
              {currencies.map(c => (
                <button
                  key={c.code}
                  type="button"
                  data-testid={`currency-option-${c.code}`}
                  onClick={() => { setCurrency(c.code); setOpen(false); }}
                  className="w-full text-left px-3 py-2 text-sm hover:opacity-80 flex items-center gap-2"
                  style={{
                    background: c.code === currency ? 'rgba(15,5,107,0.08)' : 'transparent',
                  }}
                >
                  <span aria-hidden="true">{c.flag}</span>
                  <span className="font-bold w-12">{c.code}</span>
                  <span style={{ opacity: 0.7 }}>{c.name}</span>
                  <span className="ml-auto" style={{ opacity: 0.5, fontSize: 11 }}>{c.symbol}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Conversion display */}
      <div
        data-testid="currency-conversion-display"
        className="rounded-xl px-4 py-3 text-sm font-['Manrope']"
        style={{
          background: 'rgba(15,5,107,0.06)',
          border: '1px solid rgba(15,5,107,0.1)',
          color: 'var(--jp-text)',
        }}
      >
        <div className="flex items-center gap-2">
          <ArrowsLeftRight size={16} weight="bold" style={{ opacity: 0.55 }} />
          <span style={{ opacity: 0.6 }}>
            {t('wallet.deposit_currency.local_equivalent') || 'Équivalent local'}
          </span>
        </div>
        {loadingRate && (
          <div className="mt-1.5" style={{ opacity: 0.55 }} data-testid="currency-conv-loading">
            {t('wallet.deposit_currency.rate_loading') || 'Calcul du taux…'}
          </div>
        )}
        {!loadingRate && error && (
          <div className="mt-1.5 text-red-500" data-testid="currency-conv-error">{error}</div>
        )}
        {!loadingRate && !error && amountLocal != null && rate != null && WS.isValidRate(rate) && WS.isValidLocalAmount(amountLocal) && (
          <div className="mt-1.5">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="font-bold text-base">
                {parseFloat(amountUsd).toLocaleString('fr-FR', { minimumFractionDigits: 2 })} USD
              </span>
              <span style={{ opacity: 0.4 }}>=</span>
              <span
                className="font-extrabold text-xl"
                style={{ color: 'var(--jp-primary)' }}
                data-testid="currency-conv-amount"
              >
                {formatLocal(amountLocal)} {currency}
              </span>
            </div>
            {currency !== 'USD' && (
              <div style={{ opacity: 0.6, fontSize: 11, marginTop: 4 }}>
                1 USD = {Number(rate).toLocaleString('fr-FR', { maximumFractionDigits: 4 })} {currency}
              </div>
            )}
          </div>
        )}
        {!loadingRate && !error && amountLocal == null && (
          <div className="mt-1.5" style={{ opacity: 0.45 }}>
            {t('wallet.deposit_currency.enter_amount_to_preview') || 'Saisissez un montant pour voir la conversion'}
          </div>
        )}
      </div>
    </div>
  );
}
