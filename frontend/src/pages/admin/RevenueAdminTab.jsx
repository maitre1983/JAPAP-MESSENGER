/**
 * RevenueAdminTab — iter91 (monetization phase 1)
 *
 * Business pilot dashboard: total revenue, by-source breakdown,
 * PRO plans (Starter/Creator/Business) breakdown, MRR, top payers, timeseries.
 */
import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  ResponsiveContainer, BarChart, Bar, LineChart, Line,
  CartesianGrid, XAxis, YAxis, Tooltip, Legend,
} from 'recharts';
import {
  CurrencyDollar, Crown, TrendUp, ArrowsClockwise, Wallet, Receipt,
  ChartLineUp, Star, Users,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const PLAN_COLORS = {
  starter:   '#F59E0B',
  creator:   '#8B5CF6',
  business:  '#10B981',
};
const PLAN_LABELS = {
  starter:  'Starter Pro',
  creator:  'Creator Pro',
  business: 'Business Pro',
};

export default function RevenueAdminTab() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(
        `${API}/api/admin/revenue/overview?days=${days}`,
        { withCredentials: true },
      );
      setData(r.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Chargement impossible');
    } finally { setLoading(false); }
  }, [days]);
  useEffect(() => { load(); }, [load]);

  if (loading && !data) return <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;
  if (!data) return null;

  const k = data.kpis;
  const fmt = (v) => (v || 0).toLocaleString('fr-FR', { maximumFractionDigits: 2 });

  return (
    <div className="space-y-6 jp-animate-fadeIn" data-testid="revenue-admin-tab">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 className="font-['Outfit'] text-xl font-bold" style={{ color: 'var(--jp-text)' }}>
            Revenus — Pilotage business
          </h2>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            Fenêtre glissante {days} jours · USD
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select className="jp-input text-xs"
                  value={days}
                  onChange={(e) => setDays(parseInt(e.target.value))}
                  data-testid="revenue-admin-window">
            {[7, 14, 30, 60, 90, 180, 365].map(d => <option key={d} value={d}>{d} jours</option>)}
          </select>
          <button onClick={load} disabled={loading}
                  data-testid="revenue-admin-refresh"
                  className="jp-btn jp-btn-ghost jp-btn-sm">
            <ArrowsClockwise size={14} /> Rafraîchir
          </button>
        </div>
      </div>

      {/* TOTAL KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <BigKpi icon={CurrencyDollar} color="#FFD700"
                label="Revenus totaux"
                value={`$${fmt(k.total_revenue_usd)}`}
                sub={`${(k.counts.send_fees + k.counts.withdraw_fees + k.counts.subscriptions)} transactions`} />
        <BigKpi icon={TrendUp} color="#10B981"
                label="MRR"
                value={`$${fmt(k.mrr_usd)}`}
                sub={`${k.counts.subscriptions} subs · ${k.active_trials} essais`} />
        <BigKpi icon={Wallet} color="#8B5CF6"
                label="Dépôts (brut)"
                value={`$${fmt(k.deposits_gross_usd)}`}
                sub={`${k.counts.deposits} dépôts`} />
        <BigKpi icon={Star} color="#E01C2E"
                label="Revenus PRO"
                value={`$${fmt(k.subscription_usd)}`}
                sub={`${k.counts.subscriptions} abonnements`} />
      </div>

      {/* SOURCES BREAKDOWN */}
      <div className="jp-card-elevated p-4">
        <h4 className="font-['Outfit'] text-sm font-bold mb-3 flex items-center gap-2">
          <Receipt size={16} weight="fill" /> Revenus par source
        </h4>
        <div className="grid grid-cols-3 gap-3">
          <SourceChip label="Frais transferts" value={k.send_fees_usd} color="#F59E0B" n={k.counts.send_fees} />
          <SourceChip label="Frais retraits" value={k.withdraw_fees_usd} color="#E01C2E" n={k.counts.withdraw_fees} />
          <SourceChip label="Abonnements PRO" value={k.subscription_usd} color="#8B5CF6" n={k.counts.subscriptions} />
        </div>
      </div>

      {/* PRO PACKS */}
      <div className="jp-card-elevated p-4">
        <h4 className="font-['Outfit'] text-sm font-bold mb-3 flex items-center gap-2">
          <Crown size={16} weight="fill" style={{ color: '#F59E0B' }} />
          Ventes de packs PRO — {data.by_plan.length} plan(s) actifs
        </h4>
        {data.by_plan.length === 0 ? (
          <div className="py-6 text-center text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            Aucune vente PRO dans la fenêtre.
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-3">
              {['starter', 'creator', 'business'].map((plan) => {
                const row = data.by_plan.find((p) => p.plan_type === plan) || { count: 0, revenue_usd: 0 };
                return (
                  <div key={plan}
                       className="p-4 rounded-xl border"
                       style={{ background: `${PLAN_COLORS[plan]}10`, borderColor: `${PLAN_COLORS[plan]}33` }}
                       data-testid={`revenue-pack-${plan}`}>
                    <div className="flex items-center gap-2 mb-2">
                      <Crown size={18} weight="fill" style={{ color: PLAN_COLORS[plan] }} />
                      <span className="font-bold text-sm">{PLAN_LABELS[plan]}</span>
                    </div>
                    <div className="font-['Outfit'] text-2xl font-extrabold" style={{ color: PLAN_COLORS[plan] }}>
                      {row.count}
                    </div>
                    <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                      pack(s) vendu(s) · ${fmt(row.revenue_usd)} revenus
                    </div>
                  </div>
                );
              })}
            </div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={data.by_plan}>
                <CartesianGrid stroke="var(--jp-border)" strokeDasharray="3 3" />
                <XAxis dataKey="plan_type" tickFormatter={(x) => PLAN_LABELS[x] || x} fontSize={11} />
                <YAxis fontSize={11} />
                <Tooltip formatter={(v) => `$${fmt(v)}`} />
                <Legend />
                <Bar dataKey="revenue_usd" name="Revenu (USD)" fill="#F59E0B" radius={[6, 6, 0, 0]} />
                <Bar dataKey="count" name="Ventes" fill="#8B5CF6" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* TIMESERIES */}
      <div className="jp-card-elevated p-4">
        <h4 className="font-['Outfit'] text-sm font-bold mb-3 flex items-center gap-2">
          <ChartLineUp size={16} weight="fill" /> Revenus quotidiens (USD)
        </h4>
        {data.timeseries.length === 0 ? (
          <div className="py-6 text-center text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            Aucun revenu dans la fenêtre.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={data.timeseries}>
              <CartesianGrid stroke="var(--jp-border)" strokeDasharray="3 3" />
              <XAxis dataKey="day" fontSize={10} />
              <YAxis fontSize={10} />
              <Tooltip formatter={(v) => `$${fmt(v)}`} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line type="monotone" dataKey="fee_send"     name="Frais send" stroke="#F59E0B" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="fee_withdraw" name="Frais retrait" stroke="#E01C2E" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="subscription" name="Abonnements"  stroke="#8B5CF6" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="total"        name="Total" stroke="#10B981" strokeWidth={3} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* TOP PAYERS */}
      <div className="jp-card-elevated p-4">
        <h4 className="font-['Outfit'] text-sm font-bold mb-3 flex items-center gap-2">
          <Users size={16} weight="fill" /> Top 10 contributeurs
        </h4>
        {data.top_payers.length === 0 ? (
          <div className="py-6 text-center text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            Aucun contributeur dans la fenêtre.
          </div>
        ) : (
          <table className="jp-table">
            <thead><tr><th>#</th><th>Utilisateur</th><th>Email</th><th>Plan</th><th>Contribution</th></tr></thead>
            <tbody>
              {data.top_payers.map((p, i) => (
                <tr key={p.user_id} data-testid={`revenue-top-${i}`}>
                  <td><strong>{i + 1}</strong></td>
                  <td>{p.name}</td>
                  <td className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>{p.email}</td>
                  <td>{p.is_pro ? <span className="jp-badge" style={{ background: '#FFD70033', color: '#9A6700' }}>PRO</span> : '—'}</td>
                  <td><strong style={{ color: '#FFD700' }}>${fmt(p.total_usd)}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function BigKpi({ icon: Icon, label, value, sub, color }) {
  return (
    <div className="jp-stat-card" data-testid={`revenue-kpi-${label.toLowerCase().replace(/\s+/g, '-')}`}>
      <div className="jp-stat-icon" style={{ background: `${color}1a` }}>
        <Icon size={16} weight="duotone" style={{ color }} />
      </div>
      <div className="jp-stat-label">{label}</div>
      <div className="jp-stat-value" style={{ color }}>{value}</div>
      {sub && <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{sub}</div>}
    </div>
  );
}

function SourceChip({ label, value, color, n }) {
  return (
    <div className="p-3 rounded-xl text-center"
         style={{ background: `${color}10`, border: `1px solid ${color}33` }}>
      <div className="font-['Outfit'] text-2xl font-extrabold" style={{ color }}>
        ${(value || 0).toLocaleString('fr-FR', { maximumFractionDigits: 2 })}
      </div>
      <div className="text-xs font-semibold mt-0.5" style={{ color: 'var(--jp-text)' }}>{label}</div>
      <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{n || 0} opération(s)</div>
    </div>
  );
}
