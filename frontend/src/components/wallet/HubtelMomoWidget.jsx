/**
 * iter237af — Hubtel Mobile Money widget (Ghana 🇬🇭).
 *
 * Strictly ADDITIVE — does NOT touch the existing wallet UI for USDT /
 * NowPayments / Orange Money / Wave. Mount this component inside the
 * existing deposit and withdrawal flows when the user selects the new
 * `hubtel_momo` method.
 *
 * All user-facing strings go through i18n (namespace `hubtelMomo.*`).
 * Defensive fallbacks are in English to match the backend.
 */
import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;
const GHANA_PREFIX = '233';
const CONVERT_DEBOUNCE_MS = 500;

/**
 * Ghana mobile-network prefixes (industry-standard, 5-char = `233` + first
 * 2 digits of the subscriber number). Source: NCA Ghana number plan.
 *
 *   • MTN         → 24, 25, 53, 54, 55, 59
 *   • AirtelTigo  → 26, 27, 56, 57
 *   • Telecel     → 20, 50  (ex-Vodafone, rebranded 04/2024)
 *
 * Frontend-only UX hint — the backend re-validates msisdn format and
 * resolves the Hubtel `Channel` independently. Keeping this table here
 * (rather than fetching the backend's mapping) avoids a network call
 * for every keystroke.
 */
const OPERATOR_PREFIXES = [
  { id: 'mtn',         label: 'MTN',        prefixes: ['23324', '23325', '23353', '23354', '23355', '23359'] },
  { id: 'airteltigo',  label: 'AirtelTigo', prefixes: ['23326', '23327', '23356', '23357'] },
  { id: 'telecel',     label: 'Telecel',    prefixes: ['23320', '23350'] },
];

function detectOperator(msisdn) {
  if (!msisdn || msisdn.length < 5) return null;
  for (const op of OPERATOR_PREFIXES) {
    if (op.prefixes.some(p => msisdn.startsWith(p))) return op;
  }
  return null;
}

/**
 * Mode is either 'deposit' or 'withdraw'. The component exposes the
 * same shape for both — only the endpoint and the field labels change.
 */
export function HubtelMomoWidget({ mode, onSuccess, onCancel }) {
  const { t } = useTranslation();
  const [amount, setAmount] = useState('');
  const [msisdn, setMsisdn] = useState('');
  const [name, setName] = useState('');
  const [limits, setLimits] = useState(null);
  const [conversion, setConversion] = useState(null);
  const [converting, setConverting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [eligibilityError, setEligibilityError] = useState(null);

  const isWithdraw = mode === 'withdraw';
  const i18nKey = isWithdraw ? 'withdraw' : 'deposit';

  const operator = useMemo(() => detectOperator(msisdn), [msisdn]);
  const operatorUnknown = useMemo(
    () => msisdn.length === 12 && msisdn.startsWith(GHANA_PREFIX) && !operator,
    [msisdn, operator],
  );

  // Load admin limits on mount.
  useEffect(() => {
    (async () => {
      try {
        const { data } = await axios.get(`${API}/api/wallet/hubtel-momo/limits`,
          { withCredentials: true });
        setLimits(data);
      } catch (_) { /* limits stay null — UI shows nothing */ }
    })();
  }, []);

  // Client-side Ghana eligibility check, fires on every msisdn change.
  // Backend re-validates — this is purely a UX shortcut.
  useEffect(() => {
    const m = (msisdn || '').replace(/[^\d]/g, '');
    if (!m) { setEligibilityError(null); return; }
    if (!m.startsWith(GHANA_PREFIX)) {
      setEligibilityError(t('hubtelMomo.error.non_eligible'));
    } else if (m.length !== 12) {
      setEligibilityError(t('hubtelMomo.error.invalid_msisdn'));
    } else {
      setEligibilityError(null);
    }
  }, [msisdn, t]);

  // Debounced USD → GHS conversion preview while typing.
  useEffect(() => {
    const a = parseFloat(amount);
    if (!a || a <= 0) { setConversion(null); return; }
    let cancelled = false;
    setConverting(true);
    const timer = setTimeout(async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/wallet/hubtel-momo/convert?amount_usd=${a}`,
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

  const limitBlock = useMemo(() => {
    if (!limits) return null;
    const block = isWithdraw ? limits.withdrawal : limits.deposit;
    return block;
  }, [limits, isWithdraw]);

  const amountWithinLimits = useMemo(() => {
    const a = parseFloat(amount);
    if (!a || a <= 0 || !limitBlock) return false;
    return a >= limitBlock.min && a <= limitBlock.max;
  }, [amount, limitBlock]);

  const canSubmit = (
    !submitting
    && amountWithinLimits
    && !eligibilityError
    && msisdn.length === 12
    && name.trim().length > 0
  );

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const a = parseFloat(amount);
      const endpoint = isWithdraw
        ? `${API}/api/wallet/withdraw/hubtel-momo`
        : `${API}/api/wallet/deposit/hubtel-momo`;
      const payload = isWithdraw
        ? { amount: a, recipient_msisdn: msisdn, recipient_name: name.trim() }
        : { amount: a, customer_msisdn: msisdn, customer_name: name.trim() };
      const { data } = await axios.post(endpoint, payload, { withCredentials: true });
      toast.success(data.message || t(`hubtelMomo.${i18nKey}.pending`));
      onSuccess && onSuccess(data);
    } catch (e) {
      const detail = e.response?.data?.detail;
      // Backend returns { error, message } inside `detail`.
      const msg = (typeof detail === 'object' && detail?.message) ? detail.message
                : (typeof detail === 'string' ? detail : t('hubtelMomo.error.generic'));
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-3" data-testid={`hubtel-momo-${mode}-form`}>
      <div className="rounded-xl p-3 text-xs"
           style={{ background: 'rgba(15,5,107,0.04)', color: 'var(--jp-text-secondary)' }}>
        🇬🇭 {t(`hubtelMomo.${i18nKey}.intro`)}
        {limitBlock && (
          <div className="mt-1 font-semibold">
            {t('hubtelMomo.limits_label', {
              min: limitBlock.min.toFixed(2),
              max: limitBlock.max.toFixed(2),
            })}
          </div>
        )}
      </div>

      <div>
        <label className="jp-label">{t('hubtelMomo.amount_label')}</label>
        <input
          type="number"
          min="0.01" step="0.01"
          value={amount}
          onChange={e => setAmount(e.target.value)}
          className="jp-input text-sm"
          placeholder="0.00"
          data-testid={`hubtel-momo-${mode}-amount`}
        />
        {converting && (
          <p className="text-[11px] mt-1" style={{ color: 'var(--jp-text-muted)' }}
             data-testid={`hubtel-momo-${mode}-converting`}>
            {t('hubtelMomo.converting')}
          </p>
        )}
        {conversion && !converting && (
          <div className="mt-1.5 px-3 py-2 rounded-lg text-xs"
               role="status" aria-live="polite"
               style={{ background: 'rgba(15,5,107,0.06)', color: 'var(--jp-text-secondary)' }}
               data-testid={`hubtel-momo-${mode}-fx`}>
            💱 {isWithdraw
                  ? t('hubtelMomo.fx_withdraw', { amount_ghs: conversion.amount_ghs.toFixed(2) })
                  : t('hubtelMomo.fx_deposit',  { amount_ghs: conversion.amount_ghs.toFixed(2) })}
            <div className="opacity-70 mt-0.5">
              {t('hubtelMomo.fx_rate', { rate: conversion.rate.toFixed(4) })}
            </div>
          </div>
        )}
        {limitBlock && amount && !amountWithinLimits && (
          <p className="text-[11px] mt-1" style={{ color: 'var(--jp-error)' }}
             data-testid={`hubtel-momo-${mode}-amount-error`}>
            {t('hubtelMomo.error.out_of_range', {
              min: limitBlock.min.toFixed(2),
              max: limitBlock.max.toFixed(2),
            })}
          </p>
        )}
      </div>

      <div>
        <label className="jp-label">{t('hubtelMomo.msisdn_label')}</label>
        <input
          type="text"
          inputMode="numeric"
          value={msisdn}
          onChange={e => setMsisdn((e.target.value || '').replace(/[^\d]/g, ''))}
          className="jp-input text-sm font-mono"
          placeholder="233XXXXXXXXX"
          maxLength={12}
          data-testid={`hubtel-momo-${mode}-msisdn`}
        />
        {eligibilityError && (
          <p className="text-[11px] mt-1" style={{ color: 'var(--jp-error)' }}
             data-testid={`hubtel-momo-${mode}-msisdn-error`}>
            ⚠️ {eligibilityError}
          </p>
        )}
        {!eligibilityError && operator && (
          <p className="text-[11px] mt-1 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full"
             style={{ background: 'rgba(15,5,107,0.06)', color: 'var(--jp-text-secondary)' }}
             data-testid={`hubtel-momo-${mode}-operator`}
             data-operator-id={operator.id}>
            <span aria-hidden="true">📡</span>
            {t('hubtelMomo.operator_detected', { operator: operator.label })}
          </p>
        )}
        {!eligibilityError && operatorUnknown && (
          <p className="text-[11px] mt-1" style={{ color: 'var(--jp-warning, #b8860b)' }}
             data-testid={`hubtel-momo-${mode}-operator-unknown`}>
            ⚠️ {t('hubtelMomo.operator_unknown')}
          </p>
        )}
      </div>

      <div>
        <label className="jp-label">{t('hubtelMomo.name_label')}</label>
        <input
          type="text"
          value={name}
          onChange={e => setName(e.target.value)}
          className="jp-input text-sm"
          placeholder={t('hubtelMomo.name_placeholder')}
          maxLength={120}
          data-testid={`hubtel-momo-${mode}-name`}
        />
      </div>

      <div className="flex gap-3 pt-1">
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          data-testid={`hubtel-momo-${mode}-submit`}
          className="jp-btn jp-btn-primary flex-1"
          style={{ opacity: canSubmit ? 1 : 0.6 }}>
          {submitting
            ? t('common.loading')
            : t(`hubtelMomo.${i18nKey}.submit`)}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="jp-btn jp-btn-ghost"
            data-testid={`hubtel-momo-${mode}-cancel`}>
            {t('common.cancel')}
          </button>
        )}
      </div>
    </div>
  );
}

export default HubtelMomoWidget;
