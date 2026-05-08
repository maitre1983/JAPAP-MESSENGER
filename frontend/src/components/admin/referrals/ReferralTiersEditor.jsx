/**
 * ReferralTiersEditor — iter82
 * Visual editor for `referral_tiers_json`. Lets admin add / edit / reorder
 * / delete reward tiers without touching code. Persists to
 * /api/admin/settings/referral_tiers_json as a JSON string.
 *
 * Each tier: { count, reward_type: 'pro'|'wallet', reward_value, label }
 */
import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Plus, Trash, ArrowUp, ArrowDown, FloppyDisk, Gift } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

const EMPTY_TIER = { count: 3, reward_type: 'pro', reward_value: 30, label: '' };

const defaultLabel = (tier) => {
  if (tier.reward_type === 'pro') {
    const months = Math.round((Number(tier.reward_value) || 0) / 30);
    return months >= 2 ? `${months} mois Pro` : '1 mois Pro';
  }
  return `${tier.reward_value} USD bonus`;
};

export default function ReferralTiersEditor() {
  const { t } = useTranslation();
  const [tiers, setTiers] = useState([]);
  const [original, setOriginal] = useState('[]');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/admin/settings`, { withCredentials: true });
      const raw = data.settings?.referral_tiers_json || '[]';
      let parsed = [];
      try { parsed = JSON.parse(raw); } catch { parsed = []; }
      if (!Array.isArray(parsed)) parsed = [];
      setTiers(parsed.map(normaliseTier));
      setOriginal(raw);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement paliers');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const updateTier = (idx, patch) => {
    setTiers(rows => rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };

  const addTier = () => {
    const lastCount = tiers.length ? Number(tiers[tiers.length - 1].count) || 0 : 0;
    const next = { ...EMPTY_TIER, count: lastCount + 3 };
    next.label = defaultLabel(next);
    setTiers(rows => [...rows, next]);
  };

  const removeTier = (idx) => {
    setTiers(rows => rows.filter((_, i) => i !== idx));
  };

  const moveTier = (idx, dir) => {
    setTiers(rows => {
      const target = idx + dir;
      if (target < 0 || target >= rows.length) return rows;
      const copy = [...rows];
      [copy[idx], copy[target]] = [copy[target], copy[idx]];
      return copy;
    });
  };

  const validate = () => {
    const counts = new Set();
    for (const tier of tiers) {
      const c = Number(tier.count);
      const v = Number(tier.reward_value);
      if (!Number.isInteger(c) || c < 1) {
        return `Count invalide (${tier.count}) — doit être un entier ≥ 1.`;
      }
      if (counts.has(c)) return `Doublon de count détecté : ${c}.`;
      counts.add(c);
      if (!['pro', 'wallet'].includes(tier.reward_type)) {
        return `Type de récompense inconnu : ${tier.reward_type}`;
      }
      if (!Number.isFinite(v) || v <= 0) {
        return `Valeur de récompense invalide pour count=${c} (${tier.reward_value}).`;
      }
      if (!tier.label?.trim()) return `Libellé manquant pour count=${c}.`;
    }
    return null;
  };

  const save = async () => {
    const err = validate();
    if (err) { toast.error(err); return; }
    // Sort by count ascending — the business logic relies on that.
    const sorted = [...tiers]
      .map(tier => ({
        count: Number(tier.count),
        reward_type: tier.reward_type,
        reward_value: Number(tier.reward_value),
        label: tier.label.trim(),
      }))
      .sort((a, b) => a.count - b.count);
    setSaving(true);
    try {
      await axios.put(`${API}/api/admin/settings/referral_tiers_json`,
        { value: JSON.stringify(sorted) }, { withCredentials: true });
      toast.success('Paliers parrainage enregistrés');
      setTiers(sorted);
      setOriginal(JSON.stringify(sorted));
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur enregistrement');
    } finally { setSaving(false); }
  };

  const dirty = JSON.stringify(tiers) !== original &&
                JSON.stringify(tiers.map(normaliseTier)) !== JSON.stringify(JSON.parse(original || '[]').map(normaliseTier));

  return (
    <div className="jp-card-elevated p-5" data-testid="referral-tiers-editor">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2">
            <Gift size={18} weight="duotone" style={{ color: 'var(--jp-primary)' }} />
            Paliers de parrainage
          </h3>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            Configurez les récompenses automatiques débloquées par vos parrains
            au fur et à mesure qu'ils activent des filleuls. Aucun redéploiement
            requis — la nouvelle configuration est appliquée en temps réel.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={load} disabled={loading || saving}
                  className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="tiers-reload">
            Recharger
          </button>
          <button onClick={addTier} disabled={saving}
                  className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="tiers-add">
            <Plus size={14} /> Ajouter un palier
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>
      ) : tiers.length === 0 ? (
        <div className="text-sm p-6 text-center rounded-lg"
             style={{ color: 'var(--jp-text-muted)', background: 'var(--jp-surface-secondary)' }}>
          Aucun palier configuré. Cliquez sur <strong>« Ajouter un palier »</strong> pour créer le premier.
        </div>
      ) : (
        <div className="space-y-2">
          {tiers.map((tier, idx) => (
            <div key={idx}
                 className="grid grid-cols-1 md:grid-cols-12 gap-2 items-end p-3 rounded-lg"
                 style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
                 data-testid={`tier-row-${idx}`}>
              <div className="md:col-span-2">
                <label className="jp-label text-[11px]">Filleuls requis</label>
                <input type="number" min="1" className="jp-input text-sm"
                       value={tier.count}
                       onChange={e => updateTier(idx, { count: e.target.value })}
                       data-testid={`tier-count-${idx}`} />
              </div>
              <div className="md:col-span-2">
                <label className="jp-label text-[11px]">Type</label>
                <select className="jp-input text-sm" value={tier.reward_type}
                        onChange={e => {
                          const next = { ...tier, reward_type: e.target.value };
                          updateTier(idx, {
                            reward_type: e.target.value,
                            label: defaultLabel(next),
                          });
                        }}
                        data-testid={`tier-type-${idx}`}>
                  <option value="pro">{t('referral_tiers_editor.pro_jours_offerts')}</option>
                  <option value="wallet">{t('referral_tiers_editor.wallet_usd')}</option>
                </select>
              </div>
              <div className="md:col-span-2">
                <label className="jp-label text-[11px]">
                  {tier.reward_type === 'pro' ? 'Jours Pro' : 'Montant USD'}
                </label>
                <input type="number" min="1" className="jp-input text-sm"
                       value={tier.reward_value}
                       onChange={e => {
                         const next = { ...tier, reward_value: e.target.value };
                         const auto = defaultLabel(next);
                         updateTier(idx, {
                           reward_value: e.target.value,
                           label: tier.label === defaultLabel(tier) ? auto : tier.label,
                         });
                       }}
                       data-testid={`tier-value-${idx}`} />
              </div>
              <div className="md:col-span-4">
                <label className="jp-label text-[11px]">Libellé affiché</label>
                <input className="jp-input text-sm" value={tier.label}
                       placeholder={t('referral_tiers_editor.ex_1_mois_pro')}
                       onChange={e => updateTier(idx, { label: e.target.value })}
                       data-testid={`tier-label-${idx}`} />
              </div>
              <div className="md:col-span-2 flex items-center gap-1 md:justify-end">
                <button onClick={() => moveTier(idx, -1)} disabled={idx === 0 || saving}
                        className="jp-btn jp-btn-ghost jp-btn-sm"
                        title="Monter" data-testid={`tier-up-${idx}`}>
                  <ArrowUp size={12} />
                </button>
                <button onClick={() => moveTier(idx, 1)} disabled={idx === tiers.length - 1 || saving}
                        className="jp-btn jp-btn-ghost jp-btn-sm"
                        title="Descendre" data-testid={`tier-down-${idx}`}>
                  <ArrowDown size={12} />
                </button>
                <button onClick={() => removeTier(idx)} disabled={saving}
                        className="jp-btn jp-btn-ghost jp-btn-sm"
                        style={{ color: 'var(--jp-error)' }}
                        title="Supprimer" data-testid={`tier-delete-${idx}`}>
                  <Trash size={12} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 mt-4">
        <button onClick={save} disabled={saving || loading || !dirty}
                className="jp-btn jp-btn-primary" data-testid="tiers-save">
          <FloppyDisk size={14} /> {saving ? 'Enregistrement…' : 'Enregistrer les paliers'}
        </button>
        {dirty && (
          <span className="text-xs" style={{ color: 'var(--jp-warning)' }}>
            Modifications non enregistrées
          </span>
        )}
      </div>

      <details className="mt-4 text-xs" style={{ color: 'var(--jp-text-muted)' }}>
        <summary className="cursor-pointer font-semibold">Aperçu JSON (lecture seule)</summary>
        <pre className="mt-2 p-2 rounded text-[10px] overflow-x-auto font-mono"
             style={{ background: 'var(--jp-surface)', border: '1px solid var(--jp-border)' }}>
{JSON.stringify(tiers, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function normaliseTier(tier) {
  return {
    count: Number(tier?.count) || 0,
    reward_type: tier?.reward_type === 'wallet' ? 'wallet' : 'pro',
    reward_value: Number(tier?.reward_value) || 0,
    label: tier?.label || defaultLabel({
      reward_type: tier?.reward_type || 'pro',
      reward_value: Number(tier?.reward_value) || 0,
    }),
  };
}
