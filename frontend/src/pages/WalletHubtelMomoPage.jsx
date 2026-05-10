/**
 * iter237af — Hubtel Mobile Money page (Ghana 🇬🇭).
 *
 * Strictly ADDITIVE — accessible via /wallet/hubtel-momo. Mounts the
 * HubtelMomoWidget in both deposit and withdraw modes. Does NOT touch
 * the legacy WalletPage UI; users navigate here from a dedicated CTA
 * (or by directly entering the URL).
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft } from '@phosphor-icons/react';
import { HubtelMomoWidget } from '../components/wallet/HubtelMomoWidget';

export default function WalletHubtelMomoPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [mode, setMode] = useState('deposit');

  return (
    <div className="min-h-screen" style={{ background: 'var(--jp-background)' }}
         data-testid="hubtel-momo-page">
      <div className="max-w-md mx-auto p-4">
        <button
          type="button"
          onClick={() => navigate('/wallet')}
          className="flex items-center gap-2 text-sm mb-4"
          style={{ color: 'var(--jp-text-secondary)' }}
          data-testid="hubtel-momo-back">
          <ArrowLeft size={16} /> {t('common.back')}
        </button>

        <h1 className="font-['Outfit'] text-2xl font-bold mb-2"
            style={{ color: 'var(--jp-text)' }}>
          {t('hubtelMomo.method_label')}
        </h1>
        <p className="text-sm mb-6" style={{ color: 'var(--jp-text-secondary)' }}>
          {t(`hubtelMomo.${mode}.intro`)}
        </p>

        <div className="flex gap-2 mb-4 rounded-xl p-1"
             style={{ background: 'rgba(15,5,107,0.04)' }}
             data-testid="hubtel-momo-mode-switch">
          <button
            type="button"
            onClick={() => setMode('deposit')}
            data-testid="hubtel-momo-mode-deposit"
            className="flex-1 py-2 rounded-lg text-sm font-semibold transition-all"
            style={{
              background: mode === 'deposit' ? 'var(--jp-primary)' : 'transparent',
              color: mode === 'deposit' ? 'white' : 'var(--jp-text-secondary)',
            }}>
            {t('wallet.deposit')}
          </button>
          <button
            type="button"
            onClick={() => setMode('withdraw')}
            data-testid="hubtel-momo-mode-withdraw"
            className="flex-1 py-2 rounded-lg text-sm font-semibold transition-all"
            style={{
              background: mode === 'withdraw' ? 'var(--jp-primary)' : 'transparent',
              color: mode === 'withdraw' ? 'white' : 'var(--jp-text-secondary)',
            }}>
            {t('wallet.withdraw')}
          </button>
        </div>

        <div className="jp-card-elevated p-4">
          <HubtelMomoWidget
            key={mode}
            mode={mode}
            onSuccess={() => navigate('/wallet')}
            onCancel={() => navigate('/wallet')}
          />
        </div>
      </div>
    </div>
  );
}
