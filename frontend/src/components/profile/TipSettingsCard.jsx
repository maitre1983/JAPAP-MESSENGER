import { useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { HandCoins, Plus, X, FloppyDisk } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import MoneyDisplay from '@/components/common/MoneyDisplay';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * iter141nineF — "Pay-as-you-Tip" settings card on the profile page.
 *
 * Lets a creator configure:
 *   • whether tips are accepted on their content
 *   • up to 6 suggested amounts displayed as quick-tap chips on each post
 *   • a short thank-you message rendered inside the TipModal
 *
 * Combined with the OG-rich payment links, every public post becomes a
 * frictionless tip-jar — major monetisation lever for creators.
 */
export default function TipSettingsCard({ userId }) {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(true);
  const [presets, setPresets] = useState([100, 500, 1000]);
  const [message, setMessage] = useState('');
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await axios.get(`${API}/api/users/${userId}/tip-settings`);
        if (cancelled) return;
        setEnabled(!!r.data.enabled);
        setPresets(Array.isArray(r.data.presets) ? r.data.presets : [100, 500, 1000]);
        setMessage(r.data.message || '');
      } catch {
        // ignore — keep defaults
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [userId]);

  const addPreset = () => {
    const n = parseInt(draft, 10);
    if (!n || n < 50) {
      toast.error('Montant minimum 50 XAF.');
      return;
    }
    if (presets.includes(n)) {
      toast.error('Ce montant est déjà dans la liste.');
      return;
    }
    if (presets.length >= 6) {
      toast.error('Maximum 6 montants.');
      return;
    }
    setPresets([...presets, n].sort((a, b) => a - b));
    setDraft('');
  };

  const removePreset = (n) => setPresets(presets.filter(p => p !== n));

  const save = async () => {
    setSaving(true);
    try {
      await axios.put(`${API}/api/users/me/tip-settings`,
        { enabled, presets, message },
        { withCredentials: true }
      );
      toast.success('Préférences de tip enregistrées.');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Sauvegarde impossible.');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;

  return (
    <div
      className="jp-card-elevated p-5 mb-5"
      data-testid="tip-settings-card"
      style={{ background: 'var(--jp-surface)' }}
    >
      <div className="flex items-start gap-3 mb-4">
        <div
          className="rounded-xl p-2.5 flex-shrink-0"
          style={{ background: 'rgba(224,28,46,0.10)' }}
        >
          <HandCoins size={22} weight="fill" style={{ color: '#E01C2E' }} />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-['Outfit'] text-base font-bold" style={{ color: 'var(--jp-text)' }}>
            Pay-as-you-Tip
          </h3>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            Tes posts deviennent des tip-jars. Les fans tappent un montant suggéré et te soutiennent en 1 clic.
          </p>
        </div>
      </div>

      <label className="flex items-center justify-between gap-3 py-2 mb-3 cursor-pointer">
        <span className="text-sm font-medium" style={{ color: 'var(--jp-text)' }}>
          Accepter les tips sur mon contenu
        </span>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          data-testid="tip-settings-enabled"
          className="w-5 h-5 cursor-pointer accent-current"
          style={{ accentColor: '#E01C2E' }}
        />
      </label>

      {enabled && (
        <>
          <div className="mb-4">
            <label className="jp-label">Montants suggérés (max 6)</label>
            <div className="flex flex-wrap gap-2 mb-2" data-testid="tip-settings-presets">
              {presets.map((n) => (
                <span
                  key={n}
                  className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-bold"
                  style={{ background: 'var(--jp-surface-secondary)', color: 'var(--jp-text)' }}
                >
                  {/* iter240g — presets stored as legacy XAF integers;
                      MoneyDisplay converts to USD-first with local hint. */}
                  <MoneyDisplay amountUsd={n} legacyCurrency="XAF" short />
                  <button
                    onClick={() => removePreset(n)}
                    data-testid={`tip-preset-remove-${n}`}
                    className="opacity-60 hover:opacity-100"
                    aria-label={`Retirer ${n}`}
                  >
                    <X size={13} />
                  </button>
                </span>
              ))}
              {presets.length === 0 && (
                <span className="text-xs italic" style={{ color: 'var(--jp-text-muted)' }}>
                  Aucun préset — un montant par défaut sera utilisé.
                </span>
              )}
            </div>
            <div className="flex gap-2">
              <input
                type="number"
                min={50}
                step={50}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder={t('tip_settings_card.ex_200')}
                className="jp-input text-sm flex-1"
                data-testid="tip-preset-input"
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addPreset(); } }}
              />
              <button
                onClick={addPreset}
                disabled={!draft || presets.length >= 6}
                className="jp-btn jp-btn-secondary jp-btn-sm"
                data-testid="tip-preset-add"
              >
                <Plus size={14} /> Ajouter
              </button>
            </div>
          </div>

          <div className="mb-4">
            <label className="jp-label">Message de remerciement (optionnel)</label>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value.slice(0, 280))}
              placeholder={t('tip_settings_card.ex_merci_de_soutenir_mon_travail_ch')}
              rows={2}
              className="jp-input text-sm resize-none"
              data-testid="tip-settings-message"
              maxLength={280}
            />
            <div className="text-[10px] text-right mt-1" style={{ color: 'var(--jp-text-muted)' }}>
              {message.length}/280
            </div>
          </div>
        </>
      )}

      <button
        onClick={save}
        disabled={saving}
        className="jp-btn jp-btn-primary disabled:opacity-50"
        data-testid="tip-settings-save"
      >
        <FloppyDisk size={16} /> {saving ? 'Enregistrement…' : 'Enregistrer'}
      </button>
    </div>
  );
}
