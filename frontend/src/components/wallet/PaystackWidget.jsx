/**
 * iter238 — Paystack Ghana deposit widget (STRICTLY ADDITIVE).
 *
 * Reusable form that:
 *   • Loads admin limits via GET /api/paystack/limits
 *   • Debounces (500ms) USD→GHS conversion via GET /api/paystack/convert
 *   • POSTs to /api/paystack/deposit/initialize and redirects to Paystack
 *
 * Fully i18n via `paystack.*` keys (FR/EN/ES/AR with RTL/RU).
 */
import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;
const CONVERT_DEBOUNCE_MS = 500;
const RTL_LANGS = new Set(['ar', 'he', 'fa', 'ur']);

export function PaystackWidget({ onCancel }) {
  const { t, i18n } = useTranslation();
  const [amount, setAmount] = useState('');
  const [limits, setLimits] = useState(null);
  const [conversion, setConversion] = useState(null);
  const [converting, setConverting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [redirecting, setRedirecting] = useState(false);

  const dir = RTL_LANGS.has(i18n.language?.slice(0, 2)) ? 'rtl' : 'ltr';

  // Load admin limits on mount.
  useEffect(() => {
    (async () => {
      try {
        const { data } = await axios.get(`${API}/api/paystack/limits`,
          { withCredentials: true });
        setLimits(data);
      } catch (_) { /* limits stay null — UI shows nothing */ }
    })();
  }, []);

  // Debounced USD → GHS conversion.
  useEffect(() => {
    const a = parseFloat(amount);
    if (!a || a <= 0) { setConversion(null); return; }
    let cancelled = false;
    setConverting(true);
    const timer = setTimeout(async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/paystack/convert?amount_usd=${a}`,
          { withCredentials: true });
        if (!cancelled) setConversion(data);
      } catch (_) {
        if (!cancelled) setConversion(null);
      } finally {
        if (!cancelled) setConverting(false);
      }
    }, CONVERT_DEBOUNCE_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [amount]);

  const amountWithinLimits = useMemo(() => {
    const a = parseFloat(amount);
    if (!a || a <= 0 || !limits?.deposit) return false;
    return a >= limits.deposit.min && a <= limits.deposit.max;
  }, [amount, limits]);

  const canSubmit = !submitting && !redirecting && amountWithinLimits;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const { data } = await axios.post(
        `${API}/api/paystack/deposit/initialize`,
        { amount_usd: parseFloat(amount) },
        { withCredentials: true },
      );
      if (data?.authorization_url) {
        setRedirecting(true);
        toast.info(t('paystack.redirect_info'));
        setTimeout(() => { window.location.href = data.authorization_url; }, 1500);
      } else {
        toast.error(t('paystack.error.generic'));
      }
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = (typeof detail === 'object' && detail?.message) ? detail.message
                : (typeof detail === 'string' ? detail : t('paystack.error.generic'));
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-3" dir={dir} data-testid="paystack-widget"
         data-paystack-dir={dir}>
      <div className="rounded-xl p-3 text-xs"
           style={{ background: 'rgba(15,5,107,0.04)', color: 'var(--jp-text-secondary)' }}>
        🌍 {t('paystack.intro')}
        {limits?.deposit && (
          <div className="mt-1 font-semibold" data-testid="paystack-limits">
            {t('paystack.limits_info', {
              min: limits.deposit.min.toFixed(2),
              max: limits.deposit.max.toFixed(2),
            })}
          </div>
        )}
      </div>

      <div>
        <label className="jp-label">{t('paystack.amount_label')}</label>
        <input
          type="number"
          min="0.01" step="0.01"
          value={amount}
          onChange={e => setAmount(e.target.value)}
          className="jp-input text-sm"
          placeholder={t('paystack.amount_placeholder')}
          data-testid="paystack-amount-input"
        />
        {converting && (
          <p className="text-[11px] mt-1" style={{ color: 'var(--jp-text-muted)' }}
             data-testid="paystack-converting">
            {t('paystack.converting')}
          </p>
        )}
        {conversion && !converting && (
          <div className="mt-1.5 px-3 py-2 rounded-lg text-xs"
               role="status" aria-live="polite"
               style={{ background: 'rgba(15,5,107,0.06)', color: 'var(--jp-text-secondary)' }}
               data-testid="paystack-fx-preview">
            💱 {t('paystack.you_will_pay')} {conversion.amount_ghs.toFixed(2)} {t('paystack.ghs_suffix')}
            <div className="opacity-70 mt-0.5">
              {t('paystack.rate_label', { rate: conversion.rate.toFixed(4) })}
            </div>
          </div>
        )}
        {limits?.deposit && amount && !amountWithinLimits && (
          <p className="text-[11px] mt-1" style={{ color: 'var(--jp-error)' }}
             data-testid="paystack-amount-error">
            {parseFloat(amount) < limits.deposit.min
              ? t('paystack.amount_too_low', { min: limits.deposit.min.toFixed(2) })
              : t('paystack.amount_too_high', { max: limits.deposit.max.toFixed(2) })}
          </p>
        )}
      </div>

      <div className="flex gap-3 pt-1">
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          data-testid="paystack-submit-btn"
          className="jp-btn jp-btn-primary flex-1"
          style={{ opacity: canSubmit ? 1 : 0.6 }}>
          {redirecting
            ? t('paystack.redirect_info')
            : submitting
              ? t('common.loading')
              : t('paystack.submit_btn')}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting || redirecting}
            className="jp-btn jp-btn-ghost"
            data-testid="paystack-cancel-btn">
            {t('common.cancel')}
          </button>
        )}
      </div>
    </div>
  );
}

export default PaystackWidget;
