/**
 * iter238 — Paystack callback result page (STRICTLY ADDITIVE).
 * Route: /wallet/paystack/result
 *
 * The backend redirects here from /api/paystack/callback with:
 *   ?status=success|failed|error[&amount_usd=10.00]
 *
 * Renders the i18n-aware banner and auto-redirects to /wallet after 4s
 * so the user lands back on their balance with the new amount visible.
 */
import { useEffect, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { CheckCircle, XCircle, WarningCircle } from '@phosphor-icons/react';

const AUTO_REDIRECT_MS = 4000;
const RTL_LANGS = new Set(['ar', 'he', 'fa', 'ur']);

export default function WalletPaystackResultPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const status = params.get('status') || 'error';
  const amountUsd = params.get('amount_usd');

  const dir = RTL_LANGS.has(i18n.language?.slice(0, 2)) ? 'rtl' : 'ltr';

  useEffect(() => {
    const t = setTimeout(() => navigate('/wallet'), AUTO_REDIRECT_MS);
    return () => clearTimeout(t);
  }, [navigate]);

  const ui = useMemo(() => {
    if (status === 'success') {
      return {
        Icon: CheckCircle,
        color: '#16a34a',
        bg: 'rgba(22,163,74,0.08)',
        text: amountUsd
          ? t('paystack.success_banner', { usd: parseFloat(amountUsd).toFixed(2) })
          : t('paystack.success_banner', { usd: '—' }),
        testid: 'paystack-result-success',
      };
    }
    if (status === 'failed') {
      return {
        Icon: XCircle,
        color: '#dc2626',
        bg: 'rgba(220,38,38,0.08)',
        text: t('paystack.failed_banner'),
        testid: 'paystack-result-failed',
      };
    }
    return {
      Icon: WarningCircle,
      color: '#b45309',
      bg: 'rgba(180,83,9,0.08)',
      text: t('paystack.error.generic'),
      testid: 'paystack-result-error',
    };
  }, [status, amountUsd, t]);

  const Icon = ui.Icon;
  return (
    <div className="min-h-screen flex items-center justify-center"
         dir={dir}
         style={{ background: 'var(--jp-background)' }}
         data-testid="paystack-result-page"
         data-paystack-result-status={status}>
      <div className="max-w-md w-full mx-auto p-6">
        <div className="jp-card-elevated p-6 text-center"
             data-testid={ui.testid}
             style={{ background: ui.bg }}>
          <Icon size={48} weight="fill" color={ui.color} className="mx-auto mb-3" />
          <p className="text-base font-medium" style={{ color: 'var(--jp-text)' }}>
            {ui.text}
          </p>
          <button
            type="button"
            onClick={() => navigate('/wallet')}
            data-testid="paystack-result-back"
            className="jp-btn jp-btn-primary mt-5 inline-flex">
            {t('common.back')}
          </button>
        </div>
      </div>
    </div>
  );
}
