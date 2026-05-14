// iter240g — Toggle utilisateur "Afficher l'équivalent en devise locale".
// Persisté en localStorage via CurrencyContext (japap_show_local_equivalent).
// Aucune logique de stockage backend touchée.
import { useTranslation } from 'react-i18next';
import { CurrencyCircleDollar } from '@phosphor-icons/react';
import { useCurrency } from '@/utils/currency';

export default function CurrencyDisplayToggleCard() {
  const { t } = useTranslation();
  const { showEquivalent, setShowEquivalent, local, symbol } = useCurrency();

  return (
    <div className="jp-card p-4" data-testid="currency-toggle-card">
      <div className="flex items-start gap-3 mb-2">
        <div
          className="w-10 h-10 rounded-xl flex items-center justify-center"
          style={{ background: 'rgba(224, 28, 46, 0.12)', color: '#E01C2E' }}>
          <CurrencyCircleDollar size={20} weight="duotone" />
        </div>
        <div className="flex-1">
          <h3 className="text-base font-bold" style={{ color: 'var(--jp-text)' }}>
            {t('common.currency_toggle_show_equivalent', {
              defaultValue: 'Afficher l\'équivalent en devise locale',
            })}
          </h3>
          <p className="text-xs mt-1" style={{ color: 'var(--jp-text-muted)' }}>
            {t('common.currency_toggle_explain', {
              defaultValue:
                'Les montants sont affichés en $ (USD). Activez pour voir aussi la valeur dans votre monnaie locale (ex : « ≈ 615 FCFA »).',
            })}
          </p>
        </div>
      </div>
      <label className="flex items-center justify-between gap-3 py-2 cursor-pointer">
        <span className="text-sm font-medium" style={{ color: 'var(--jp-text)' }}>
          {local && local !== 'USD'
            ? `${symbol} · ${local}`
            : t('common.currency_local_unknown', { defaultValue: 'Devise locale auto-détectée' })}
        </span>
        <input
          type="checkbox"
          checked={!!showEquivalent}
          onChange={(e) => setShowEquivalent(e.target.checked)}
          data-testid="currency-toggle-show-equivalent"
          className="w-5 h-5 cursor-pointer"
          style={{ accentColor: '#E01C2E' }}
        />
      </label>
    </div>
  );
}
