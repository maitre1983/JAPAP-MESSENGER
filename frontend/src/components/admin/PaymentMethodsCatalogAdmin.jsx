/**
 * iter237i — Admin : Catalogue des méthodes de paiement (toggles ON/OFF)
 *                  + Dashboard Analytics par méthode (14 jours par défaut).
 * iter239a — Fusion source de vérité : les toggles écrivent et lisent
 *            désormais EXCLUSIVEMENT dans `system_settings.{method}_enabled`.
 *            La table `payment_methods` reste source des métadonnées
 *            (label, icône, pays, restrictions, analytics) — son champ
 *            `enabled` n'est plus la source de vérité runtime.
 */
import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { ChartBar, ToggleRight as ToggleIcon } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const METHOD_PILL = (id) => {
  const map = {
    orange_money_cm:        { bg: '#FFEFE0', fg: '#9A3D00' },
    wave:                   { bg: '#E0F7FE', fg: '#063D4F' },
    hubtel_card:            { bg: '#E5E7EB', fg: '#0F056B' },
    nowpayments_usdttrc20:  { bg: '#D1FAE5', fg: '#065F46' },
    nowpayments_usdtbsc:    { bg: '#FEF3C7', fg: '#92400E' },
    paystack:               { bg: '#D1FAE5', fg: '#065F46' },
    hubtel_momo:            { bg: '#FEF3C7', fg: '#92400E' },
    usdt_trc20:             { bg: '#D1FAE5', fg: '#065F46' },
    usdt_bep20:             { bg: '#FEF3C7', fg: '#92400E' },
  };
  return map[id] || { bg: '#E5E7EB', fg: '#374151' };
};

/**
 * Maps a catalog `method.id` to the `system_settings.{key}_enabled`
 * toggle that controls it at runtime (frontend + backend middleware).
 * Multiple catalog IDs may map to the same toggle (e.g. NowPayments
 * TRC20/BSC share `nowpayments_enabled`).
 */
const METHOD_TO_TOGGLE_KEY = {
  orange_money_cm:        'orange_money_enabled',
  wave:                   'wave_enabled',
  hubtel_card:            'hubtel_card_enabled',
  hubtel_momo:            'hubtel_momo_enabled',
  paystack:               'paystack_enabled',
  nowpayments_usdttrc20:  'nowpayments_enabled',
  nowpayments_usdtbsc:    'nowpayments_enabled',
  usdt_trc20:             'usdt_manual_enabled',
  usdt_bep20:             'usdt_manual_enabled',
};

export default function PaymentMethodsCatalogAdmin() {
  const [methods, setMethods] = useState([]);
  const [statusMap, setStatusMap] = useState({});  // {toggle_key: bool}
  const [analytics, setAnalytics] = useState([]);
  const [days, setDays] = useState(14);
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState(null);

  const loadMethods = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/payment-methods`,
                                        { withCredentials: true });
      setMethods(data.methods || []);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur chargement méthodes');
    }
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/wallet/payment-methods/status`,
                                        { withCredentials: true });
      // Convert {paystack: bool, ...} → {paystack_enabled: bool, ...} for direct key match.
      const out = {};
      for (const [k, v] of Object.entries(data || {})) {
        out[`${k}_enabled`] = !!v;
      }
      setStatusMap(out);
    } catch (e) { /* leave statusMap empty; rows will fallback to method.enabled */ }
  }, []);

  const loadAnalytics = useCallback(async () => {
    try {
      const { data } = await axios.get(
        `${API}/api/admin/payment-methods/analytics?days=${days}`,
        { withCredentials: true });
      setAnalytics(data.rows || []);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur chargement analytics');
    }
  }, [days]);

  useEffect(() => {
    setLoading(true);
    Promise.all([loadMethods(), loadStatus(), loadAnalytics()])
      .finally(() => setLoading(false));
  }, [loadMethods, loadStatus, loadAnalytics]);

  /**
   * Returns the runtime-enabled state from `system_settings` if a mapping
   * exists, otherwise falls back to the legacy `m.enabled` (true by default).
   * This makes the catalog row's status reflect the new source of truth.
   */
  const isMethodEnabled = (m) => {
    const k = METHOD_TO_TOGGLE_KEY[m.id];
    if (k && statusMap[k] !== undefined) return statusMap[k];
    return !!m.enabled;
  };

  const toggleMethod = async (m) => {
    const toggleKey = METHOD_TO_TOGGLE_KEY[m.id];
    if (!toggleKey) {
      toast.error(`Méthode '${m.id}' non mappée — édition impossible.`);
      return;
    }
    setSavingId(m.id);
    const next = !isMethodEnabled(m);
    try {
      // iter239a — writes to system_settings (the new source of truth).
      // Server-side audit log captures who/when/before/after.
      await axios.put(
        `${API}/api/admin/settings/${toggleKey}`,
        { value: next ? 'true' : 'false' },
        { withCredentials: true });
      setStatusMap(prev => ({ ...prev, [toggleKey]: next }));
      toast.success(`${m.label} → ${next ? 'activée' : 'désactivée'}`);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur lors du toggle');
    } finally {
      setSavingId(null);
    }
  };

  // Merge analytics rows into the methods list for a unified table.
  const findRow = (id) => analytics.find(r => r.method_id === id) || {
    checks: 0, eligible_checks: 0, eligible_pct: 0, form_opened: 0, submitted: 0,
  };

  return (
    <div className="space-y-5" data-testid="payment-methods-catalog-admin">
      {/* ======== Toggles activation par méthode ======== */}
      <div className="jp-card-elevated p-5" data-testid="payment-methods-toggles">
        <h3 className="font-['Outfit'] text-lg font-bold mb-1 flex items-center gap-2">
          <ToggleIcon size={18} weight="duotone" style={{ color: '#5B21B6' }} />
          Méthodes de paiement — Activation
        </h3>
        <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
          Active ou désactive une méthode <strong>à chaud</strong>, sans redéploiement.
          Une méthode désactivée disparaît immédiatement du wallet utilisateur.
        </p>
        <div className="rounded-xl overflow-hidden border" style={{ borderColor: 'var(--jp-border)' }}>
          <table className="jp-table w-full text-sm">
            <thead>
              <tr>
                <th className="text-left">Méthode</th>
                <th className="text-left">Pays</th>
                <th className="text-left">Disponibilité</th>
                <th className="text-center">Statut</th>
                <th className="text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={5} className="text-center py-6 text-xs"
                        style={{ color: 'var(--jp-text-muted)' }}>Chargement…</td></tr>
              )}
              {!loading && methods.length === 0 && (
                <tr><td colSpan={5} className="text-center py-6 text-xs"
                        style={{ color: 'var(--jp-text-muted)' }}>Aucune méthode</td></tr>
              )}
              {!loading && methods.map(m => {
                const pill = METHOD_PILL(m.id);
                const countries = (m.restricted_countries || '').toUpperCase();
                const enabled = isMethodEnabled(m);
                const toggleKey = METHOD_TO_TOGGLE_KEY[m.id];
                return (
                  <tr key={m.id} data-testid={`pm-row-${m.id}`}>
                    <td>
                      <span className="text-xs px-2 py-1 rounded-full font-bold"
                            style={{ background: pill.bg, color: pill.fg }}>
                        {m.icon} {m.label}
                      </span>
                      {toggleKey && (
                        <div className="text-[9px] mt-1 font-mono opacity-50"
                             style={{ color: 'var(--jp-text-muted)' }}>
                          {toggleKey}
                        </div>
                      )}
                    </td>
                    <td className="text-xs font-mono">
                      {countries === 'ALL'
                        ? <span style={{ color: '#065F46' }}>🌍 ALL</span>
                        : countries}
                    </td>
                    <td className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                      {m.availability_text}
                    </td>
                    <td className="text-center">
                      <span className="text-[10px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider"
                            style={{
                              background: enabled ? '#D1FAE5' : '#FEE2E2',
                              color: enabled ? '#065F46' : '#991B1B',
                            }}
                            data-testid={`pm-status-${m.id}`}>
                        {enabled ? '✓ Active' : '⏸ Désactivée'}
                      </span>
                    </td>
                    <td className="text-right">
                      <button type="button" onClick={() => toggleMethod(m)}
                              disabled={savingId === m.id || !toggleKey}
                              data-testid={`pm-toggle-${m.id}`}
                              className="relative w-12 h-6 rounded-full transition-colors shrink-0"
                              style={{ background: enabled ? 'var(--jp-success, #10B981)' : '#D1D5DB',
                                       opacity: (savingId === m.id || !toggleKey) ? 0.5 : 1 }}>
                        <div className="absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform"
                             style={{ transform: enabled ? 'translateX(26px)' : 'translateX(2px)' }} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ======== Dashboard Analytics ======== */}
      <div className="jp-card-elevated p-5" data-testid="payment-methods-analytics">
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2">
            <ChartBar size={18} weight="duotone" style={{ color: '#0F056B' }} />
            Analytics Paiements — {days} derniers jours
          </h3>
          <select className="jp-input text-xs" style={{ minWidth: '110px' }}
                  value={days}
                  onChange={e => setDays(parseInt(e.target.value, 10))}
                  data-testid="pma-days-select">
            <option value={7}>7 jours</option>
            <option value={14}>14 jours</option>
            <option value={30}>30 jours</option>
            <option value={90}>90 jours</option>
          </select>
        </div>
        <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
          Suit chaque clic sur <em>"Vérifier mon éligibilité"</em>, l'ouverture
          d'un formulaire de dépôt/retrait et chaque soumission.
          Les non-éligibles permettent de mesurer la friction par pays.
        </p>
        <div className="rounded-xl overflow-hidden border" style={{ borderColor: 'var(--jp-border)' }}>
          <table className="jp-table w-full text-sm">
            <thead>
              <tr>
                <th className="text-left">Méthode</th>
                <th className="text-right">Vérifications</th>
                <th className="text-right">Éligibles</th>
                <th className="text-right">Taux</th>
                <th className="text-right">Formulaires ouverts</th>
                <th className="text-right">Soumissions</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={6} className="text-center py-6 text-xs"
                        style={{ color: 'var(--jp-text-muted)' }}>Chargement…</td></tr>
              )}
              {!loading && methods.length === 0 && (
                <tr><td colSpan={6} className="text-center py-6 text-xs"
                        style={{ color: 'var(--jp-text-muted)' }}>Aucune donnée</td></tr>
              )}
              {!loading && methods.map(m => {
                const r = findRow(m.id);
                const pill = METHOD_PILL(m.id);
                const pct = Number(r.eligible_pct || 0);
                const pctColor = pct >= 70 ? '#065F46'
                                : pct >= 40 ? '#92400E' : '#991B1B';
                return (
                  <tr key={m.id} data-testid={`pma-row-${m.id}`}>
                    <td>
                      <span className="text-xs px-2 py-1 rounded-full font-bold"
                            style={{ background: pill.bg, color: pill.fg }}>
                        {m.icon} {m.label}
                      </span>
                    </td>
                    <td className="text-right font-['Outfit'] font-bold"
                        data-testid={`pma-checks-${m.id}`}>
                      {Number(r.checks || 0).toLocaleString('fr-FR')}
                    </td>
                    <td className="text-right text-xs">
                      {Number(r.eligible_checks || 0).toLocaleString('fr-FR')}
                    </td>
                    <td className="text-right">
                      <span className="text-xs font-bold px-2 py-0.5 rounded-full"
                            style={{ background: 'rgba(0,0,0,0.04)', color: pctColor }}
                            data-testid={`pma-pct-${m.id}`}>
                        {pct.toFixed(1)}%
                      </span>
                    </td>
                    <td className="text-right text-xs">
                      {Number(r.form_opened || 0).toLocaleString('fr-FR')}
                    </td>
                    <td className="text-right font-['Outfit'] font-bold"
                        data-testid={`pma-submitted-${m.id}`}>
                      {Number(r.submitted || 0).toLocaleString('fr-FR')}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="text-[10px] mt-2" style={{ color: 'var(--jp-text-muted)' }}>
          Les méthodes désactivées sont incluses dans le tableau (affichage
          historique). <em>Vérifications</em> = total des clics
          "Vérifier mon éligibilité" sur la période.
        </p>
      </div>
    </div>
  );
}
