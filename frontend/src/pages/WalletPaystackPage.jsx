/**
 * iter238 — Paystack Ghana page (STRICTLY ADDITIVE).
 * Route: /wallet/paystack
 */
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft } from '@phosphor-icons/react';
import { PaystackWidget } from '../components/wallet/PaystackWidget';

export default function WalletPaystackPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen" style={{ background: 'var(--jp-background)' }}
         data-testid="paystack-page">
      <div className="max-w-md mx-auto p-4">
        <button
          type="button"
          onClick={() => navigate('/wallet')}
          className="flex items-center gap-2 text-sm mb-4"
          style={{ color: 'var(--jp-text-secondary)' }}
          data-testid="paystack-back-btn">
          <ArrowLeft size={16} /> {t('common.back')}
        </button>

        <h1 className="font-['Outfit'] text-2xl font-bold mb-2"
            style={{ color: 'var(--jp-text)' }}>
          {t('paystack.method_label')}
        </h1>
        <p className="text-sm mb-6" style={{ color: 'var(--jp-text-secondary)' }}>
          {t('paystack.intro')}
        </p>

        <div className="jp-card-elevated p-4">
          <PaystackWidget onCancel={() => navigate('/wallet')} />
        </div>
      </div>
    </div>
  );
}
