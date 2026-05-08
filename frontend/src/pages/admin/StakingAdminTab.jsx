/*
 * JAPAP — Admin Staking Tab (iter68)
 *
 * Admin UI surface for the Crypto Staking soft-launch module. Lets ops:
 *   • watch live metrics (totals, positions, per-plan breakdown, last sync)
 *   • edit plan parameters (APY bps, min/max stake MIR, early-withdrawal fee, is_active)
 *   • inspect — but not flip — the hard-locked flags (trading/transfers/…)
 *   • manually trigger the on-chain mirror refresh
 *
 * Backend contract: see /app/backend/routes/staking.py (admin_router).
 * NO smart-contract logic touched: everything here is read/write on our
 * local DB mirror.
 */
import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Coins, ArrowsClockwise, PencilSimple, FloppyDisk, X, LockSimple,
  CheckCircle, XCircle,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;
const authCfg = { withCredentials: true };

const fmtMir = (v) => {
  const n = parseFloat(v || 0);
  if (isNaN(n)) return '0';
  return n.toLocaleString('fr-FR', { maximumFractionDigits: 4 });
};
const bpsToPct = (bps) => `${(Number(bps || 0) / 100).toFixed(2)}%`;
const shortDate = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }); }
  catch { return iso; }
};

const LOCKED_KEYS = [
  'staking_trading_enabled',
  'staking_transfers_enabled',
  'staking_swaps_enabled',
  'staking_deposits_enabled',
  'staking_withdrawals_enabled',
];

export default function StakingAdminTab() {
  const [loading, setLoading] = useState(true);
  const [monitoring, setMonitoring] = useState(null);
  const [plans, setPlans] = useState([]);
  const [settings, setSettings] = useState({});
  const [editingPlan, setEditingPlan] = useState(null); // plan_id being edited
  const [planForm, setPlanForm] = useState({});
  const [syncing, setSyncing] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [m, p, s] = await Promise.all([
        axios.get(`${API}/api/admin/staking/monitoring`, authCfg),
        axios.get(`${API}/api/admin/staking/plans`, authCfg),
        axios.get(`${API}/api/admin/staking/settings`, authCfg),
      ]);
      setMonitoring(m.data);
      setPlans(p.data.items || []);
      setSettings(s.data || {});
    } catch (e) {
      toast.error('Échec du chargement Staking', { description: e?.response?.data?.detail || e.message });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const startEdit = (plan) => {
    setEditingPlan(plan.plan_id);
    setPlanForm({
      name: plan.name || '',
      duration_months: plan.duration_months,
      apy_bps: plan.apy_bps,
      early_withdrawal_fee_bps: plan.early_withdrawal_fee_bps,
      min_stake_mir: parseFloat(plan.min_stake_mir || 0),
      max_stake_mir: plan.max_stake_mir ? parseFloat(plan.max_stake_mir) : '',
      is_active: !!plan.is_active,
      sort_order: plan.sort_order || 0,
    });
  };

  const cancelEdit = () => { setEditingPlan(null); setPlanForm({}); };

  const savePlan = async () => {
    try {
      const payload = { ...planForm };
      // null/empty max_stake_mir = remove cap
      if (payload.max_stake_mir === '' || payload.max_stake_mir === null) {
        payload.max_stake_mir = null;
      } else {
        payload.max_stake_mir = parseFloat(payload.max_stake_mir);
      }
      payload.min_stake_mir = parseFloat(payload.min_stake_mir || 0);
      payload.apy_bps = parseInt(payload.apy_bps);
      payload.early_withdrawal_fee_bps = parseInt(payload.early_withdrawal_fee_bps);
      payload.duration_months = parseInt(payload.duration_months);
      payload.sort_order = parseInt(payload.sort_order || 0);
      await axios.put(`${API}/api/admin/staking/plans/${editingPlan}`, payload, authCfg);
      toast.success('Plan mis à jour', { description: `${editingPlan} — APY ${bpsToPct(payload.apy_bps)}` });
      cancelEdit();
      reload();
    } catch (e) {
      toast.error('Échec de la sauvegarde', { description: e?.response?.data?.detail || e.message });
    }
  };

  const triggerSync = async () => {
    setSyncing(true);
    try {
      const r = await axios.post(`${API}/api/admin/staking/sync`, {}, authCfg);
      if (r.data?.ok) {
        toast.success('Sync on-chain OK', { description: `Bloc #${r.data.block}` });
      } else {
        toast.info('Sync ignorée', { description: r.data?.reason || 'Non disponible' });
      }
      reload();
    } catch (e) {
      toast.error('Échec sync', { description: e?.response?.data?.detail || e.message });
    } finally {
      setSyncing(false);
    }
  };

  const toggleSetting = async (key, currentValue) => {
    if (LOCKED_KEYS.includes(key)) {
      toast.warning('Clé verrouillée', { description: 'Les flags trading/transferts/swaps/deposits/withdrawals sont hard-locked par design produit.' });
      return;
    }
    const next = currentValue === 'true' ? 'false' : 'true';
    try {
      await axios.put(`${API}/api/admin/staking/settings`, { [key]: next }, authCfg);
      toast.success(`${key} → ${next}`);
      reload();
    } catch (e) {
      toast.error('Échec mise à jour setting', { description: e?.response?.data?.detail || e.message });
    }
  };

  if (loading && !monitoring) {
    return <div className="text-sm py-10 text-center" style={{ color: 'var(--jp-text-muted)' }} data-testid="staking-loading">Chargement Staking…</div>;
  }

  const totals = monitoring?.totals || {};
  const byPlan = monitoring?.by_plan || [];
  const recent = monitoring?.recent_positions || [];

  return (
    <div className="space-y-6" data-testid="staking-admin-tab">
      {/* ─── Header + sync ─── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-['Outfit'] text-lg font-bold flex items-center gap-2" style={{ color: 'var(--jp-text)' }}>
            <Coins size={22} weight="duotone" style={{ color: '#F7931A' }} />
            Crypto Staking — MIR
          </h2>
          <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
            Dernière sync on-chain : <strong>{shortDate(monitoring?.last_onchain_sync)}</strong>
          </p>
        </div>
        <button
          data-testid="staking-trigger-sync"
          onClick={triggerSync}
          disabled={syncing}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border hover:bg-gray-50 transition disabled:opacity-50"
          style={{ borderColor: 'var(--jp-border)', color: 'var(--jp-text)' }}
        >
          <ArrowsClockwise size={14} weight="bold" className={syncing ? 'animate-spin' : ''} />
          {syncing ? 'Sync en cours…' : 'Forcer sync on-chain'}
        </button>
      </div>

      {/* ─── Totals cards ─── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <StatCard testId="stat-active-positions" label="Positions actives" value={totals.active_positions || 0} />
        <StatCard testId="stat-total-staked" label="MIR staké" value={fmtMir(totals.total_staked_mir)} suffix="MIR" />
        <StatCard testId="stat-unique-stakers" label="Stakers uniques" value={totals.unique_stakers || 0} />
        <StatCard testId="stat-rewards-paid" label="Récompenses versées" value={fmtMir(totals.total_rewards_paid)} suffix="MIR" />
        <StatCard testId="stat-connected-wallets" label="Wallets connectés" value={totals.connected_wallets || 0} />
      </div>

      {/* ─── Plans edit ─── */}
      <div className="jp-card p-4" data-testid="staking-plans-card">
        <h3 className="font-['Outfit'] text-base font-semibold mb-3" style={{ color: 'var(--jp-text)' }}>
          Plans de staking
        </h3>
        <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
          Modifiez APY (en bps — 100 bps = 1%), stake min/max, ou la pénalité early-withdraw.
          L'activation s'applique immédiatement aux nouveaux staking.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase" style={{ color: 'var(--jp-text-muted)' }}>
                <th className="py-2 pr-3">Plan</th>
                <th className="py-2 pr-3">Durée</th>
                <th className="py-2 pr-3">APY</th>
                <th className="py-2 pr-3">Min</th>
                <th className="py-2 pr-3">Max</th>
                <th className="py-2 pr-3">Early fee</th>
                <th className="py-2 pr-3">Actif</th>
                <th className="py-2 pr-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {plans.map(p => {
                const isEditing = editingPlan === p.plan_id;
                return (
                  <tr key={p.plan_id} className="border-t" style={{ borderColor: 'var(--jp-border)' }} data-testid={`staking-plan-row-${p.plan_id}`}>
                    <td className="py-2 pr-3">
                      {isEditing ? (
                        <input
                          data-testid={`plan-edit-name-${p.plan_id}`}
                          value={planForm.name}
                          onChange={(e) => setPlanForm({ ...planForm, name: e.target.value })}
                          className="w-32 px-2 py-1 border rounded text-xs"
                          style={{ borderColor: 'var(--jp-border)' }}
                        />
                      ) : <><div className="font-medium">{p.name}</div><div className="text-xs text-gray-500">{p.plan_id}</div></>}
                    </td>
                    <td className="py-2 pr-3">
                      {isEditing ? (
                        <input
                          type="number" min="1" max="120"
                          value={planForm.duration_months}
                          onChange={(e) => setPlanForm({ ...planForm, duration_months: e.target.value })}
                          className="w-16 px-2 py-1 border rounded text-xs"
                          style={{ borderColor: 'var(--jp-border)' }}
                        />
                      ) : `${p.duration_months} mois`}
                    </td>
                    <td className="py-2 pr-3">
                      {isEditing ? (
                        <input
                          data-testid={`plan-edit-apy-${p.plan_id}`}
                          type="number" min="0" max="10000"
                          value={planForm.apy_bps}
                          onChange={(e) => setPlanForm({ ...planForm, apy_bps: e.target.value })}
                          className="w-20 px-2 py-1 border rounded text-xs"
                          style={{ borderColor: 'var(--jp-border)' }}
                        />
                      ) : <span className="font-semibold" style={{ color: '#F7931A' }}>{bpsToPct(p.apy_bps)}</span>}
                    </td>
                    <td className="py-2 pr-3">
                      {isEditing ? (
                        <input
                          data-testid={`plan-edit-min-${p.plan_id}`}
                          type="number" min="0" step="0.01"
                          value={planForm.min_stake_mir}
                          onChange={(e) => setPlanForm({ ...planForm, min_stake_mir: e.target.value })}
                          className="w-24 px-2 py-1 border rounded text-xs"
                          style={{ borderColor: 'var(--jp-border)' }}
                        />
                      ) : `${fmtMir(p.min_stake_mir)} MIR`}
                    </td>
                    <td className="py-2 pr-3">
                      {isEditing ? (
                        <input
                          data-testid={`plan-edit-max-${p.plan_id}`}
                          type="number" min="0" step="0.01"
                          placeholder="(sans cap)"
                          value={planForm.max_stake_mir}
                          onChange={(e) => setPlanForm({ ...planForm, max_stake_mir: e.target.value })}
                          className="w-24 px-2 py-1 border rounded text-xs"
                          style={{ borderColor: 'var(--jp-border)' }}
                        />
                      ) : (p.max_stake_mir ? `${fmtMir(p.max_stake_mir)} MIR` : <span className="text-xs text-gray-400">—</span>)}
                    </td>
                    <td className="py-2 pr-3">
                      {isEditing ? (
                        <input
                          type="number" min="0" max="10000"
                          value={planForm.early_withdrawal_fee_bps}
                          onChange={(e) => setPlanForm({ ...planForm, early_withdrawal_fee_bps: e.target.value })}
                          className="w-20 px-2 py-1 border rounded text-xs"
                          style={{ borderColor: 'var(--jp-border)' }}
                        />
                      ) : bpsToPct(p.early_withdrawal_fee_bps)}
                    </td>
                    <td className="py-2 pr-3">
                      {isEditing ? (
                        <input
                          type="checkbox"
                          checked={planForm.is_active}
                          onChange={(e) => setPlanForm({ ...planForm, is_active: e.target.checked })}
                        />
                      ) : (p.is_active
                        ? <CheckCircle size={18} weight="fill" style={{ color: 'var(--jp-success)' }} />
                        : <XCircle size={18} weight="fill" style={{ color: 'var(--jp-danger)' }} />)}
                    </td>
                    <td className="py-2 pr-3 text-right">
                      {isEditing ? (
                        <div className="inline-flex gap-1">
                          <button data-testid={`plan-save-${p.plan_id}`} onClick={savePlan} className="p-1.5 rounded hover:bg-green-50" title="Enregistrer">
                            <FloppyDisk size={16} weight="bold" style={{ color: 'var(--jp-success)' }} />
                          </button>
                          <button data-testid={`plan-cancel-${p.plan_id}`} onClick={cancelEdit} className="p-1.5 rounded hover:bg-gray-50" title="Annuler">
                            <X size={16} weight="bold" />
                          </button>
                        </div>
                      ) : (
                        <button data-testid={`plan-edit-${p.plan_id}`} onClick={() => startEdit(p)} className="p-1.5 rounded hover:bg-gray-50" title="Modifier">
                          <PencilSimple size={16} weight="bold" />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ─── Per-plan breakdown ─── */}
      {byPlan.length > 0 && (
        <div className="jp-card p-4" data-testid="staking-per-plan-card">
          <h3 className="font-['Outfit'] text-base font-semibold mb-3" style={{ color: 'var(--jp-text)' }}>
            Distribution par plan (positions actives)
          </h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase" style={{ color: 'var(--jp-text-muted)' }}>
                <th className="py-2 pr-3">Plan</th>
                <th className="py-2 pr-3">Positions</th>
                <th className="py-2 pr-3">Stakers uniques</th>
                <th className="py-2 pr-3">MIR staké</th>
                <th className="py-2 pr-3">APY</th>
              </tr>
            </thead>
            <tbody>
              {byPlan.map(row => (
                <tr key={row.plan_id} className="border-t" style={{ borderColor: 'var(--jp-border)' }}>
                  <td className="py-2 pr-3 font-medium">{row.name || row.plan_id}</td>
                  <td className="py-2 pr-3">{row.positions}</td>
                  <td className="py-2 pr-3">{row.unique_users}</td>
                  <td className="py-2 pr-3">{fmtMir(row.staked_mir)} MIR</td>
                  <td className="py-2 pr-3" style={{ color: '#F7931A' }}>{bpsToPct(row.apy_bps)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ─── Recent positions ─── */}
      {recent.length > 0 && (
        <div className="jp-card p-4" data-testid="staking-recent-card">
          <h3 className="font-['Outfit'] text-base font-semibold mb-3" style={{ color: 'var(--jp-text)' }}>
            Positions récentes (20 dernières)
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left uppercase" style={{ color: 'var(--jp-text-muted)' }}>
                  <th className="py-2 pr-3">Position</th>
                  <th className="py-2 pr-3">User</th>
                  <th className="py-2 pr-3">Plan</th>
                  <th className="py-2 pr-3">Amount</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Staked</th>
                  <th className="py-2 pr-3">Unlocks</th>
                </tr>
              </thead>
              <tbody>
                {recent.map(r => (
                  <tr key={r.position_id} className="border-t" style={{ borderColor: 'var(--jp-border)' }}>
                    <td className="py-2 pr-3 font-mono">{r.position_id.slice(0, 16)}…</td>
                    <td className="py-2 pr-3">{r.email || r.user_id}</td>
                    <td className="py-2 pr-3">{r.plan_id}</td>
                    <td className="py-2 pr-3">{fmtMir(r.amount_mir)} MIR</td>
                    <td className="py-2 pr-3">
                      <span className={`px-1.5 py-0.5 rounded text-xs ${
                        r.status === 'active' ? 'bg-green-50 text-green-700' :
                        r.status === 'withdrawn' ? 'bg-blue-50 text-blue-700' :
                        'bg-orange-50 text-orange-700'
                      }`}>{r.status}</span>
                    </td>
                    <td className="py-2 pr-3">{shortDate(r.staked_at)}</td>
                    <td className="py-2 pr-3">{shortDate(r.unlocks_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ─── Settings ─── */}
      <div className="jp-card p-4" data-testid="staking-settings-card">
        <h3 className="font-['Outfit'] text-base font-semibold mb-3" style={{ color: 'var(--jp-text)' }}>
          Réglages généraux
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {Object.entries(settings).map(([k, v]) => {
            const isLocked = LOCKED_KEYS.includes(k);
            const isBool = v === 'true' || v === 'false';
            return (
              <div key={k} className="flex items-center justify-between p-2 border rounded" style={{ borderColor: 'var(--jp-border)' }} data-testid={`staking-setting-${k}`}>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold flex items-center gap-1">
                    {isLocked && <LockSimple size={12} weight="bold" style={{ color: 'var(--jp-danger)' }} />}
                    {k}
                  </div>
                  <div className="text-xs font-mono truncate" style={{ color: 'var(--jp-text-muted)' }}>{v}</div>
                </div>
                {isBool && (
                  <button
                    data-testid={`staking-setting-toggle-${k}`}
                    onClick={() => toggleSetting(k, v)}
                    disabled={isLocked}
                    className={`ml-2 px-2 py-1 rounded text-xs font-medium ${
                      isLocked ? 'bg-gray-100 text-gray-400 cursor-not-allowed' :
                      v === 'true' ? 'bg-green-100 text-green-700 hover:bg-green-200' :
                      'bg-gray-100 text-gray-700 hover:bg-gray-200'
                    }`}
                    title={isLocked ? 'Hard-locked par design produit' : 'Toggle'}
                  >
                    {v === 'true' ? 'ON' : 'OFF'}
                  </button>
                )}
              </div>
            );
          })}
        </div>
        <p className="text-xs mt-3" style={{ color: 'var(--jp-text-muted)' }}>
          <LockSimple size={11} weight="bold" className="inline mb-0.5" /> Les 5 clés verrouillées (trading / transfers / swaps / deposits / withdrawals) sont <strong>hard-locked</strong> côté backend — le module reste staking-only par design.
        </p>
      </div>
    </div>
  );
}

function StatCard({ label, value, suffix, testId }) {
  return (
    <div className="jp-stat-card" data-testid={testId}>
      <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
      <div className="font-['Outfit'] text-lg font-bold mt-1" style={{ color: 'var(--jp-text)' }}>
        {value} {suffix && <span className="text-xs font-normal" style={{ color: 'var(--jp-text-muted)' }}>{suffix}</span>}
      </div>
    </div>
  );
}
