/**
 * TransportPricingAdminTab — Phase B (P1.2 AI Pricing Grid).
 *
 * Admin operates the country-by-country tariff grid. Each row holds a base
 * fare + per-km rate for a (country_code, vehicle_type) pair. Rows live in
 * one of four states:
 *   • proposed → awaiting admin validation
 *   • active   → currently applied by /api/transport/estimate + /request
 *   • archived → was active, replaced by a newer validation
 *   • rejected → manually discarded; kept for audit
 *
 * The "Proposer via IA" button mints a fresh proposal via Claude Sonnet 4.5
 * (calling POST /admin/pricing/ai-propose). The admin can then validate,
 * reject, or simply request another proposal to compare.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Sparkle, CheckCircle, XCircle, Archive, ClockCounterClockwise,
  CurrencyCircleDollar, Plus,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

// 14 most-served country presets (matches DriverKycForm). Admin can still
// type a custom code in the form.
const getCountry_presets = (t) => ([
  { code: 'CM', name: 'Cameroun',   currency: 'XAF' },
  { code: 'CI', name: t('transport_pricing_admin.cote_d_ivoire'), currency: 'XOF' },
  { code: 'SN', name: t('transport_pricing_admin.senegal'),    currency: 'XOF' },
  { code: 'BF', name: 'Burkina Faso', currency: 'XOF' },
  { code: 'ML', name: 'Mali',       currency: 'XOF' },
  { code: 'NE', name: 'Niger',      currency: 'XOF' },
  { code: 'BJ', name: t('transport_pricing_admin.benin'),      currency: 'XOF' },
  { code: 'TG', name: 'Togo',       currency: 'XOF' },
  { code: 'CD', name: 'RD Congo',   currency: 'CDF' },
  { code: 'CG', name: 'Congo',      currency: 'XAF' },
  { code: 'GA', name: 'Gabon',      currency: 'XAF' },
  { code: 'NG', name: 'Nigeria',    currency: 'NGN' },
  { code: 'GH', name: 'Ghana',      currency: 'GHS' },
  { code: 'FR', name: 'France',     currency: 'EUR' },
]);

const getStatus_meta = (t) => ({
  proposed: { label: t('transport_pricing_admin.propose'),  color: '#F59E0B', icon: ClockCounterClockwise },
  active:   { label: 'Actif',    color: '#10B981', icon: CheckCircle },
  archived: { label: t('transport_pricing_admin.archive'),  color: '#6B7280', icon: Archive },
  rejected: { label: t('transport_pricing_admin.rejete'),   color: '#E01C2E', icon: XCircle },
});


export default function TransportPricingAdminTab() {
  const { t } = useTranslation();
  const COUNTRY_PRESETS = getCountry_presets(t);
  const STATUS_META = getStatus_meta(t);
  const [items, setItems] = useState([]);
  const [counts, setCounts] = useState({ proposed: 0, active: 0, archived: 0, rejected: 0 });
  const [filterStatus, setFilterStatus] = useState('');
  const [filterCountry, setFilterCountry] = useState('');
  const [filterVehicle, setFilterVehicle] = useState('');
  const [proposing, setProposing] = useState(false);
  const [showProposeModal, setShowProposeModal] = useState(false);
  const [selected, setSelected] = useState(null);
  const [weeklyEnabled, setWeeklyEnabled] = useState(false);
  const [lastRunWeek, setLastRunWeek] = useState('');
  const [batchRunning, setBatchRunning] = useState(false);
  const [cronPending, setCronPending] = useState({ count: 0, week: '', items: [] });
  const [validatingAll, setValidatingAll] = useState(false);

  const load = useCallback(async () => {
    try {
      const params = {};
      if (filterStatus) params.status = filterStatus;
      if (filterCountry) params.country = filterCountry;
      if (filterVehicle) params.vehicle_type = filterVehicle;
      const r = await axios.get(`${API}/api/transport/admin/pricing`, {
        params, withCredentials: true,
      });
      setItems(r.data.items || []);
      setCounts(r.data.counts || {});
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Chargement échoué');
    }
  }, [filterStatus, filterCountry, filterVehicle]);

  // Weekly cron settings
  const loadWeeklySettings = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/api/admin/settings`, { withCredentials: true });
      const s = r.data?.settings || {};
      // settings are stored as strings; treat 'true' / true as enabled.
      const v = s.pricing_ai_weekly_enabled;
      setWeeklyEnabled(v === true || v === 'true' || v === '1');
      setLastRunWeek(s.pricing_ai_last_run_iso_week || '');
    } catch {}
  }, []);

  // Pending cron-weekly proposals available for 1-click validation
  const loadCronPending = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/api/transport/admin/pricing/cron-batch/preview`,
                                { withCredentials: true });
      setCronPending({
        count: r.data?.count || 0,
        week: r.data?.week || '',
        items: r.data?.items || [],
      });
    } catch { setCronPending({ count: 0, week: '', items: [] }); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadWeeklySettings(); }, [loadWeeklySettings]);
  useEffect(() => { loadCronPending(); }, [loadCronPending, items]);

  const toggleWeekly = async () => {
    try {
      await axios.put(
        `${API}/api/admin/settings/pricing_ai_weekly_enabled`,
        { value: !weeklyEnabled },
        { withCredentials: true },
      );
      setWeeklyEnabled(!weeklyEnabled);
      toast.success(`Cron hebdomadaire ${!weeklyEnabled ? 'activé' : 'désactivé'}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Échec');
    }
  };

  const runBatchNow = async () => {
    if (!window.confirm('Déclencher une proposition IA pour tous les tarifs actifs ? Cela peut prendre 30-60s.')) return;
    setBatchRunning(true);
    try {
      const r = await axios.post(
        `${API}/api/transport/admin/pricing/run-ai-batch`,
        {}, { withCredentials: true, timeout: 180000 },
      );
      const d = r.data;
      if (d.skipped) {
        toast.info(`Aucune proposition créée (raison: ${d.reason})`);
      } else {
        toast.success(`${d.proposals_created} nouvelle(s) proposition(s) générée(s)`);
      }
      load();
      loadWeeklySettings();
      loadCronPending();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Échec du batch');
    } finally {
      setBatchRunning(false);
    }
  };

  const validateAllCron = async () => {
    if (cronPending.count === 0) return;
    const msg =
      `Valider ${cronPending.count} proposition(s) IA hebdomadaire(s) de la semaine ${cronPending.week} ?\n\n` +
      `Chaque proposition deviendra le tarif actif pour son couple (pays, véhicule), ` +
      `et le tarif actif précédent sera archivé. Cette action est tracée dans l'audit.`;
    if (!window.confirm(msg)) return;
    setValidatingAll(true);
    try {
      const r = await axios.post(
        `${API}/api/transport/admin/pricing/cron-batch/validate-all`,
        { confirm: true },
        { withCredentials: true },
      );
      const d = r.data;
      const parts = [`${d.validated_count} validé(s)`];
      if (d.skipped_count) parts.push(`${d.skipped_count} ignoré(s)`);
      if (d.conflicts_count) parts.push(`${d.conflicts_count} conflit(s)`);
      toast.success(parts.join(' · '));
      load();
      loadCronPending();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Échec de la validation groupée');
    } finally {
      setValidatingAll(false);
    }
  };

  const validate = async (id) => {
    if (!window.confirm('Valider ce tarif ? Le tarif actif précédent sera archivé.')) return;
    try {
      await axios.post(`${API}/api/transport/admin/pricing/${id}/validate`, {}, { withCredentials: true });
      toast.success('Tarif activé');
      setSelected(null);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Validation échouée');
    }
  };

  const reject = async (id, reason) => {
    try {
      await axios.post(`${API}/api/transport/admin/pricing/${id}/reject`, { reason }, { withCredentials: true });
      toast.success('Tarif rejeté');
      setSelected(null);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Rejet échoué');
    }
  };

  return (
    <div className="space-y-4" data-testid="transport-pricing-admin">
      {/* Weekly AI proposal cron banner */}
      <div className="jp-card-elevated p-4 flex flex-col sm:flex-row items-start sm:items-center gap-3"
           style={{ borderLeft: '3px solid #7c3aed' }}
           data-testid="pricing-cron-banner">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-1">
            <Sparkle size={14} weight="fill" style={{ color: '#7c3aed' }} />
            <span className="text-xs font-bold">Proposition IA hebdomadaire</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold uppercase"
                  style={{
                    background: weeklyEnabled ? '#10b98120' : 'var(--jp-surface-secondary)',
                    color: weeklyEnabled ? '#10b981' : 'var(--jp-text-muted)',
                  }}
                  data-testid="cron-status-chip">
              {weeklyEnabled ? t('transport_pricing_admin.active') : t('transport_pricing_admin.desactive')}
            </span>
          </div>
          <p className="text-[11px] opacity-70 leading-snug">
            Chaque lundi UTC, Claude Sonnet 4.5 génère une mise à jour de tarif
            pour chaque pays actif (en tenant compte de l'inflation et du prix
            de l'essence). L'admin reçoit une notification et n'a qu'à valider.
            {lastRunWeek && <span className="block mt-0.5 opacity-60">Dernière exécution : {lastRunWeek}</span>}
          </p>
        </div>
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-1.5 w-full sm:w-auto">
          <button
            onClick={toggleWeekly}
            className="jp-btn jp-btn-ghost text-xs font-bold whitespace-nowrap"
            data-testid="cron-toggle-btn"
          >
            {weeklyEnabled ? t('transport_pricing_admin.desactiver') : 'Activer le cron'}
          </button>
          <button
            onClick={runBatchNow}
            disabled={batchRunning}
            className="jp-btn jp-btn-primary text-xs font-bold whitespace-nowrap flex items-center justify-center gap-1.5"
            data-testid="cron-run-now-btn"
          >
            <Sparkle size={12} weight="fill" />
            {batchRunning ? t('transport_pricing_admin.execution') : 'Lancer maintenant'}
          </button>
          <button
            onClick={validateAllCron}
            disabled={cronPending.count === 0 || validatingAll}
            className="jp-btn text-xs font-bold whitespace-nowrap flex items-center justify-center gap-1.5"
            style={{
              background: cronPending.count === 0 ? 'var(--jp-surface-secondary)' : '#10b981',
              color: cronPending.count === 0 ? 'var(--jp-text-muted)' : 'white',
              cursor: cronPending.count === 0 ? 'not-allowed' : 'pointer',
              opacity: cronPending.count === 0 ? 0.6 : 1,
            }}
            title={cronPending.count === 0 ? 'Aucune proposition IA en attente' : `Valider ${cronPending.count} propositions de la semaine ${cronPending.week}`}
            data-testid="cron-validate-all-btn"
          >
            <CheckCircle size={12} weight="fill" />
            {validatingAll
              ? 'Validation…'
              : cronPending.count === 0
                ? 'Aucune proposition IA'
                : `Valider tout (${cronPending.count})`}
          </button>
        </div>
      </div>

      {/* Header counters */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {Object.entries(STATUS_META).map(([key, meta]) => (
          <button
            key={key}
            onClick={() => setFilterStatus(filterStatus === key ? '' : key)}
            className="p-3 rounded-xl text-left transition"
            style={{
              background: filterStatus === key ? meta.color + '20' : 'var(--jp-surface-secondary)',
              border: `1px solid ${filterStatus === key ? meta.color : 'var(--jp-border)'}`,
            }}
            data-testid={`pricing-counter-${key}`}
          >
            <div className="flex items-center gap-1.5 mb-1">
              <meta.icon size={14} weight="fill" style={{ color: meta.color }} />
              <span className="text-[10px] uppercase font-bold opacity-70">{meta.label}</span>
            </div>
            <div className="text-xl font-extrabold">{counts[key] || 0}</div>
          </button>
        ))}
      </div>

      {/* Filters + actions */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={filterCountry}
          onChange={(e) => setFilterCountry(e.target.value)}
          className="jp-input text-xs"
          style={{ maxWidth: 180 }}
          data-testid="pricing-filter-country"
        >
          <option value="">{t('transport_pricing_admin.tous_pays')}</option>
          {COUNTRY_PRESETS.map((c) => (
            <option key={c.code} value={c.code}>{c.code} · {c.name}</option>
          ))}
        </select>
        <select
          value={filterVehicle}
          onChange={(e) => setFilterVehicle(e.target.value)}
          className="jp-input text-xs"
          style={{ maxWidth: 140 }}
          data-testid="pricing-filter-vehicle"
        >
          <option value="">{t('transport_pricing_admin.tous_vehicules')}</option>
          <option value="standard">{t('transport_pricing_admin.standard')}</option>
          <option value="premium">{t('transport_pricing_admin.premium')}</option>
        </select>
        <button
          onClick={() => setShowProposeModal(true)}
          disabled={proposing}
          className="jp-btn jp-btn-primary text-xs flex items-center gap-1.5 ml-auto font-bold"
          data-testid="pricing-propose-ai-btn"
        >
          <Sparkle size={14} weight="fill" /> Proposer via IA
        </button>
      </div>

      {/* Table */}
      <div className="jp-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead style={{ background: 'var(--jp-surface-secondary)' }}>
              <tr>
                <th className="text-left p-2.5 font-bold uppercase opacity-60">Pays</th>
                <th className="text-left p-2.5 font-bold uppercase opacity-60">Véh.</th>
                <th className="text-right p-2.5 font-bold uppercase opacity-60">Base</th>
                <th className="text-right p-2.5 font-bold uppercase opacity-60">/km</th>
                <th className="text-left p-2.5 font-bold uppercase opacity-60">Devise</th>
                <th className="text-left p-2.5 font-bold uppercase opacity-60">Source</th>
                <th className="text-left p-2.5 font-bold uppercase opacity-60">Statut</th>
                <th className="text-left p-2.5 font-bold uppercase opacity-60">Date</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((p) => {
                const meta = STATUS_META[p.status];
                return (
                  <tr key={p.pricing_id}
                      style={{ borderTop: '1px solid var(--jp-border)' }}
                      className="hover:bg-white/5 cursor-pointer"
                      onClick={() => setSelected(p)}
                      data-testid={`pricing-row-${p.pricing_id}`}>
                    <td className="p-2.5 font-bold">{p.country_code} <span className="opacity-50 font-normal">{p.country_name}</span></td>
                    <td className="p-2.5 capitalize">{p.vehicle_type}</td>
                    <td className="p-2.5 text-right tabular-nums">{parseFloat(p.base_fare).toLocaleString('fr-FR')}</td>
                    <td className="p-2.5 text-right tabular-nums">{parseFloat(p.per_km).toLocaleString('fr-FR')}</td>
                    <td className="p-2.5">{p.currency}</td>
                    <td className="p-2.5">
                      {p.source === 'ai' ? (
                        <span className="inline-flex items-center gap-1 text-[10px] font-bold"
                              style={{ color: '#7c3aed' }}>
                          <Sparkle size={10} weight="fill" /> IA
                        </span>
                      ) : (
                        <span className="text-[10px] opacity-60">Manuel</span>
                      )}
                    </td>
                    <td className="p-2.5">
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase"
                            style={{ color: meta.color }}>
                        <meta.icon size={10} weight="fill" /> {meta.label}
                      </span>
                    </td>
                    <td className="p-2.5 opacity-60 text-[10px]">
                      {new Date(p.created_at).toLocaleDateString('fr-FR')}
                    </td>
                    <td className="p-2.5">
                      <CurrencyCircleDollar size={14} className="opacity-30" />
                    </td>
                  </tr>
                );
              })}
              {items.length === 0 && (
                <tr><td colSpan={9} className="p-8 text-center opacity-50">
                  Aucun tarif. Cliquez "Proposer via IA" pour démarrer.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showProposeModal && (
        <ProposeModal
          onClose={() => setShowProposeModal(false)}
          onCreated={() => { setShowProposeModal(false); load(); }}
          proposing={proposing}
          setProposing={setProposing}
        />
      )}

      {selected && (
        <PricingDetailModal
          pricing={selected}
          onClose={() => setSelected(null)}
          onValidate={() => validate(selected.pricing_id)}
          onReject={(reason) => reject(selected.pricing_id, reason)}
        />
      )}
    </div>
  );
}


/* ════════════════════════════════════════════════════════════════════════ */
function ProposeModal({ onClose, onCreated, proposing, setProposing }) {
  const { t } = useTranslation();
  const COUNTRY_PRESETS = getCountry_presets(t);
  const [country, setCountry] = useState(COUNTRY_PRESETS[0]);
  const [vehicleType, setVehicleType] = useState('standard');
  const [extraContext, setExtraContext] = useState('');
  const [mode, setMode] = useState('ai'); // ai | manual
  const [manualBase, setManualBase] = useState('');
  const [manualPerKm, setManualPerKm] = useState('');

  const submit = async () => {
    setProposing(true);
    try {
      if (mode === 'ai') {
        await axios.post(`${API}/api/transport/admin/pricing/ai-propose`, {
          country_code: country.code,
          country_name: country.name,
          currency: country.currency,
          vehicle_type: vehicleType,
          extra_context: extraContext,
        }, { withCredentials: true });
        toast.success('Proposition IA générée');
      } else {
        if (!manualBase || !manualPerKm) {
          toast.error('Renseignez base et /km');
          setProposing(false);
          return;
        }
        await axios.post(`${API}/api/transport/admin/pricing/manual`, {
          country_code: country.code,
          country_name: country.name,
          currency: country.currency,
          vehicle_type: vehicleType,
          base_fare: parseFloat(manualBase),
          per_km: parseFloat(manualPerKm),
          rationale: extraContext || 'Saisie manuelle admin',
        }, { withCredentials: true });
        toast.success('Tarif manuel proposé');
      }
      onCreated();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Échec');
    } finally {
      setProposing(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
         onClick={onClose}
         data-testid="pricing-propose-modal">
      <div className="w-full max-w-md rounded-2xl p-5 space-y-4"
           style={{ background: 'var(--jp-surface)' }}
           onClick={(e) => e.stopPropagation()}>
        <div>
          <h3 className="font-['Outfit'] font-extrabold text-base">
            {mode === 'ai' ? 'Proposer une grille via IA' : 'Saisie manuelle'}
          </h3>
          <p className="text-xs opacity-70 mt-1">
            {mode === 'ai'
              ? t('transport_pricing_admin.claude_sonnet_4_5_propose_une_grill')
              : "Saisissez directement les valeurs base et /km."}
          </p>
        </div>

        <div className="flex gap-1.5 p-1 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
          <button onClick={() => setMode('ai')}
                  className={`flex-1 py-1.5 rounded-md text-xs font-bold ${mode === 'ai' ? 'jp-btn-primary' : ''}`}
                  data-testid="pricing-mode-ai">
            <Sparkle size={12} weight="fill" className="inline mr-1" /> IA
          </button>
          <button onClick={() => setMode('manual')}
                  className={`flex-1 py-1.5 rounded-md text-xs font-bold ${mode === 'manual' ? 'jp-btn-primary' : ''}`}
                  data-testid="pricing-mode-manual">
            <Plus size={12} weight="bold" className="inline mr-1" /> Manuel
          </button>
        </div>

        <div>
          <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">Pays</label>
          <select
            value={country.code}
            onChange={(e) => setCountry(COUNTRY_PRESETS.find(c => c.code === e.target.value) || COUNTRY_PRESETS[0])}
            className="jp-input text-sm w-full"
            data-testid="pricing-form-country"
          >
            {COUNTRY_PRESETS.map((c) => (
              <option key={c.code} value={c.code}>{c.code} · {c.name} ({c.currency})</option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">Véhicule</label>
          <div className="grid grid-cols-2 gap-1.5">
            {['standard', 'premium'].map((v) => (
              <button key={v}
                      onClick={() => setVehicleType(v)}
                      className={`py-2 rounded-lg text-xs font-bold capitalize ${vehicleType === v ? 'jp-btn-primary' : ''}`}
                      style={{ background: vehicleType === v ? '' : 'var(--jp-surface-secondary)' }}
                      data-testid={`pricing-form-vehicle-${v}`}>
                {v}
              </button>
            ))}
          </div>
        </div>

        {mode === 'manual' && (
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">Base</label>
              <input type="number" step="0.01" value={manualBase}
                     onChange={(e) => setManualBase(e.target.value)}
                     className="jp-input text-sm w-full"
                     data-testid="pricing-form-base" />
            </div>
            <div>
              <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">/km</label>
              <input type="number" step="0.01" value={manualPerKm}
                     onChange={(e) => setManualPerKm(e.target.value)}
                     className="jp-input text-sm w-full"
                     data-testid="pricing-form-perkm" />
            </div>
          </div>
        )}

        <div>
          <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">
            {mode === 'ai' ? 'Contexte additionnel (optionnel)' : 'Justification (optionnel)'}
          </label>
          <textarea value={extraContext}
                    onChange={(e) => setExtraContext(e.target.value)}
                    rows={2}
                    maxLength={300}
                    placeholder={t('transport_pricing_admin.ex_prix_essence_en_hausse_concurren')}
                    className="jp-input text-sm w-full"
                    data-testid="pricing-form-context" />
        </div>

        <div className="grid grid-cols-2 gap-2 pt-2">
          <button onClick={onClose} disabled={proposing}
                  className="jp-btn jp-btn-ghost text-xs">
            Annuler
          </button>
          <button onClick={submit} disabled={proposing}
                  className="jp-btn jp-btn-primary text-xs font-bold flex items-center justify-center gap-1.5"
                  data-testid="pricing-form-submit">
            {proposing ? '⏳ Génération…' : mode === 'ai' ? <><Sparkle size={12} weight="fill" /> Proposer</> : 'Enregistrer'}
          </button>
        </div>
      </div>
    </div>
  );
}


/* ════════════════════════════════════════════════════════════════════════ */
function PricingDetailModal({ pricing, onClose, onValidate, onReject }) {
  const { t } = useTranslation();
  const STATUS_META = getStatus_meta(t);
  const [reason, setReason] = useState('');
  const meta = STATUS_META[pricing.status];

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
         onClick={onClose}
         data-testid="pricing-detail-modal">
      <div className="w-full max-w-md rounded-2xl p-5 space-y-4"
           style={{ background: 'var(--jp-surface)' }}
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <div>
            <h3 className="font-['Outfit'] font-extrabold text-base">
              {pricing.country_code} · {pricing.vehicle_type}
            </h3>
            <p className="text-xs opacity-70">{pricing.country_name}</p>
          </div>
          <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase px-2 py-1 rounded-full"
                style={{ background: meta.color + '20', color: meta.color }}>
            <meta.icon size={11} weight="fill" /> {meta.label}
          </span>
        </div>

        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="p-3 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
            <div className="text-[10px] opacity-60 uppercase font-bold">Base</div>
            <div className="text-lg font-extrabold tabular-nums">
              {parseFloat(pricing.base_fare).toLocaleString('fr-FR')}
            </div>
          </div>
          <div className="p-3 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
            <div className="text-[10px] opacity-60 uppercase font-bold">/km</div>
            <div className="text-lg font-extrabold tabular-nums">
              {parseFloat(pricing.per_km).toLocaleString('fr-FR')}
            </div>
          </div>
          <div className="p-3 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
            <div className="text-[10px] opacity-60 uppercase font-bold">Devise</div>
            <div className="text-lg font-extrabold">{pricing.currency}</div>
          </div>
        </div>

        {pricing.ai_rationale && (
          <div>
            <div className="text-[10px] uppercase font-bold opacity-60 mb-1 flex items-center gap-1">
              <Sparkle size={10} weight="fill" /> Justification {pricing.source === 'ai' ? 'IA' : 'admin'}
            </div>
            <p className="text-xs leading-relaxed p-3 rounded-lg"
               style={{ background: 'var(--jp-surface-secondary)' }}
               data-testid="pricing-rationale">
              {pricing.ai_rationale}
            </p>
          </div>
        )}

        {pricing.status === 'proposed' && (
          <>
            <div>
              <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">
                Raison de rejet (optionnel)
              </label>
              <textarea value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        rows={2}
                        maxLength={300}
                        className="jp-input text-sm w-full"
                        data-testid="pricing-detail-reason" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <button onClick={() => onReject(reason)}
                      className="jp-btn text-xs font-bold flex items-center justify-center gap-1.5"
                      style={{ background: '#E01C2E', color: 'white' }}
                      data-testid="pricing-detail-reject">
                <XCircle size={12} weight="bold" /> Rejeter
              </button>
              <button onClick={onValidate}
                      className="jp-btn text-xs font-bold flex items-center justify-center gap-1.5"
                      style={{ background: '#10b981', color: 'white' }}
                      data-testid="pricing-detail-validate">
                <CheckCircle size={12} weight="bold" /> Valider
              </button>
            </div>
          </>
        )}

        {pricing.status !== 'proposed' && (
          <button onClick={onClose} className="jp-btn jp-btn-ghost text-xs w-full">
            Fermer
          </button>
        )}
      </div>
    </div>
  );
}
