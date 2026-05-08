/**
 * WalletOverviewAdminTab — fintech-grade admin dashboard.
 *
 * Displays in ≤ 5 seconds :
 *   - 4 big KPIs (total balance, funded accounts, locked, engagement points)
 *   - Recharts timeseries : daily inflow/outflow
 *   - Recharts bar : volumes by tx type
 *   - Live anomaly banners : large withdrawals, stuck pending > 24h, send-spam
 *   - Admin alert feed (most recent OneSignal pushes)
 *   - Top 10 funded users (with PRO badge)
 *
 * Consumed by /api/admin/wallet/overview + /api/admin/wallet/alerts.
 */
import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  ResponsiveContainer, LineChart, Line, CartesianGrid,
  XAxis, YAxis, Tooltip, Legend,
  BarChart, Bar,
} from 'recharts';
import {
  Wallet, Lock, Users, Trophy, Warning, Bell, ArrowCounterClockwise, Crown,
  TrendUp, TrendDown, Clock as ClockIcon,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function WalletOverviewAdminTab() {
  const { t } = useTranslation();
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, al] = await Promise.all([
        axios.get(`${API}/api/admin/wallet/overview?days=${days}`, { withCredentials: true }),
        axios.get(`${API}/api/admin/wallet/alerts?limit=20`, { withCredentials: true }),
      ]);
      setData(ov.data);
      setAlerts(al.data.items || []);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Chargement impossible');
    } finally { setLoading(false); }
  }, [days]);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) {
    return <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;
  }
  if (!data) return null;

  const totalAnomalies =
    (data.anomalies.large_withdrawals?.length || 0) +
    (data.anomalies.stuck_pending_over_24h?.length || 0) +
    (data.anomalies.send_spam_last_1h?.length || 0);

  return (
    <div className="space-y-6 jp-animate-fadeIn" data-testid="wallet-overview-admin-tab">
      {/* ── Header ── */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 className="font-['Outfit'] text-xl font-bold" style={{ color: 'var(--jp-text)' }}>
            Vue d'ensemble Wallet
          </h2>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            {data.balances.accounts} comptes · fenêtre glissante {days} jours
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select className="jp-input text-xs" value={days} onChange={(e) => setDays(parseInt(e.target.value))}
                  data-testid="wallet-admin-window">
            {[7, 14, 30, 60, 90].map(d => <option key={d} value={d}>{d} jours</option>)}
          </select>
          <button onClick={load} disabled={loading}
                  data-testid="wallet-admin-refresh"
                  className="jp-btn jp-btn-ghost jp-btn-sm">
            <ArrowCounterClockwise size={14} /> Rafraîchir
          </button>
        </div>
      </div>

      {/* ── KPI grid ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi icon={Wallet} label="Solde total" color="#FFD700"
             value={`${data.balances.total_balance.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} XAF`} />
        <Kpi icon={Users} label="Comptes crédités" color="#10B981"
             value={`${data.balances.funded} / ${data.balances.accounts}`}
             sub={`${Math.round(100 * data.balances.funded / Math.max(1, data.balances.accounts))}%`} />
        <Kpi icon={Lock} label="Wallets bloqués" color="#E01C2E"
             value={data.balances.locked}
             sub={data.balances.locked > 0 ? 'revue requise' : 'aucun'} />
        <Kpi icon={Trophy} label="Points distribués" color="#8B5CF6"
             value={data.engagement_points.total_points.toLocaleString('fr-FR')}
             sub={`${data.engagement_points.unique_players} joueurs`} />
      </div>

      {/* ── Anomalies banner ── */}
      {totalAnomalies > 0 && (
        <AnomaliesBanner data={data.anomalies} />
      )}

      {/* ── Charts row ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title={t('wallet_overview_admin.flux_quotidien_xaf')}>
          {data.timeseries.length === 0 ? (
            <Empty>{t('wallet_overview_admin.aucune_transaction_dans_la_fenetre')}</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={data.timeseries}>
                <CartesianGrid stroke="var(--jp-border)" strokeDasharray="3 3" />
                <XAxis dataKey="day" fontSize={10} />
                <YAxis fontSize={10} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="inflow"  name="Entrées" stroke="#10B981" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="outflow" name="Sorties" stroke="#E01C2E" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card title={t('wallet_overview_admin.volumes_par_type')}>
          {data.volumes_by_type.length === 0 ? (
            <Empty>{t('wallet_overview_admin.aucune_donnee')}</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={data.volumes_by_type} layout="vertical" margin={{ left: 30 }}>
                <CartesianGrid stroke="var(--jp-border)" strokeDasharray="3 3" />
                <XAxis type="number" fontSize={10} />
                <YAxis type="category" dataKey="type" fontSize={11} width={90} />
                <Tooltip />
                <Bar dataKey="total" name="Montant" fill="#FFD700" radius={[0, 6, 6, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {/* ── Engagement breakdown ── */}
      <div className="jp-card-elevated p-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="font-['Outfit'] text-sm font-bold">Points d'engagement (par source)</h4>
          <span className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
            {data.engagement_points.total_spins} actions · {data.engagement_points.unique_players} joueurs
          </span>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <SourceChip label="Roue" value={data.engagement_points.by_source.wheel} color="#F59E0B" />
          <SourceChip label="Quiz" value={data.engagement_points.by_source.quiz}  color="#8B5CF6" />
          <SourceChip label="Tap"  value={data.engagement_points.by_source.tap}   color="#E01C2E" />
        </div>
      </div>

      {/* ── Top funded users ── */}
      <Card title={<span className="flex items-center gap-2"><Crown size={16} weight="fill" style={{ color: '#F59E0B' }} /> Top 10 comptes crédités</span>}>
        {data.top_funded.length === 0 ? (
          <Empty>{t('wallet_overview_admin.aucun_compte_credite')}</Empty>
        ) : (
          <table className="jp-table">
            <thead><tr><th>#</th><th>{t('wallet_overview_admin.utilisateur')}</th><th>{t('wallet_overview_admin.solde_xaf')}</th><th>Plan</th></tr></thead>
            <tbody>
              {data.top_funded.map((r, i) => (
                <tr key={r.user_id}>
                  <td><strong>{i + 1}</strong></td>
                  <td>
                    <div className="flex items-center gap-2">
                      <div className="jp-avatar jp-avatar-sm jp-avatar-primary">
                        {(r.name?.[0] || '?').toUpperCase()}
                      </div>
                      <div>
                        <div className="font-medium">{r.name}</div>
                        <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{r.email}</div>
                      </div>
                    </div>
                  </td>
                  <td><strong style={{ color: '#FFD700' }}>{r.balance.toLocaleString('fr-FR', { maximumFractionDigits: 2 })}</strong></td>
                  <td>{r.is_pro ? <span className="jp-badge" style={{ background: '#FFD70033', color: '#9A6700' }}>PRO</span> : <span className="text-xs opacity-50">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* ── Admin alert feed ── */}
      <Card title={<span className="flex items-center gap-2"><Bell size={16} weight="fill" /> Alertes temps réel</span>}>
        {alerts.length === 0 ? (
          <Empty>{t('wallet_overview_admin.aucune_alerte_recente_le_systeme_es')}</Empty>
        ) : (
          <div className="space-y-2" data-testid="wallet-alerts-feed">
            {alerts.map(a => (
              <div key={a.id} className="p-3 rounded-xl"
                   style={{ background: 'rgba(224,28,46,0.06)', border: '1px solid rgba(224,28,46,0.2)' }}>
                <div className="flex items-start gap-2">
                  <Warning size={18} weight="fill" style={{ color: '#E01C2E' }} />
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm">{a.title}</div>
                    <div className="text-xs mt-0.5" style={{ color: 'var(--jp-text-secondary)' }}>{a.body}</div>
                    <div className="flex items-center gap-3 mt-1 text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                      <span>{new Date(a.created_at).toLocaleString('fr-FR')}</span>
                      <span className="font-mono">{a.kind}</span>
                      {a.push_sent
                        ? <span className="px-1.5 py-0.5 rounded" style={{ background: '#10B98133', color: '#10B981' }}>push envoyé</span>
                        : <span className="px-1.5 py-0.5 rounded" style={{ background: '#6B728033', color: '#9CA3AF' }}>loggé</span>}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

/* ───────────── Sub-components ───────────── */

function Kpi({ icon: Icon, label, value, sub, color }) {
  return (
    <div className="jp-stat-card" data-testid={`wallet-kpi-${label.toLowerCase().replace(/\s+/g, '-')}`}>
      <div className="jp-stat-icon" style={{ background: `${color}1a` }}>
        <Icon size={16} weight="duotone" style={{ color }} />
      </div>
      <div className="jp-stat-label">{label}</div>
      <div className="jp-stat-value" style={{ color }}>{value}</div>
      {sub && <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{sub}</div>}
    </div>
  );
}

function Card({ title, children }) {
  return (
    <div className="jp-card-elevated p-4">
      <h4 className="font-['Outfit'] text-sm font-bold mb-3">{title}</h4>
      {children}
    </div>
  );
}

function Empty({ children }) {
  return (
    <div className="py-8 text-center text-xs" style={{ color: 'var(--jp-text-muted)' }}>
      {children}
    </div>
  );
}

function SourceChip({ label, value, color }) {
  return (
    <div className="p-3 rounded-xl text-center"
         style={{ background: `${color}10`, border: `1px solid ${color}33` }}>
      <div className="font-['Outfit'] text-2xl font-extrabold" style={{ color }}>
        +{(value || 0).toLocaleString('fr-FR')}
      </div>
      <div className="text-xs font-semibold mt-0.5" style={{ color: 'var(--jp-text)' }}>{label}</div>
    </div>
  );
}

function AnomaliesBanner({ data }) {
  const sections = [
    { key: 'large_withdrawals', label: 'Retraits élevés (> 500 USD)',
      items: data.large_withdrawals, icon: TrendDown, fmt: (x) => `${x.user_id} · ${x.amount.toFixed(2)} USD · ${x.status}` },
    { key: 'stuck_pending_over_24h', label: 'Transactions pending > 24 h',
      items: data.stuck_pending_over_24h, icon: ClockIcon, fmt: (x) => `${x.type} · ${x.amount.toFixed(2)} · ${x.from || '-'}` },
    { key: 'send_spam_last_1h', label: 'Spam send (dernière heure)',
      items: data.send_spam_last_1h, icon: TrendUp, fmt: (x) => `${x.user_id} · ${x.count} sends (${x.total.toFixed(2)} XAF)` },
  ].filter(s => s.items && s.items.length > 0);
  if (sections.length === 0) return null;
  return (
    <div className="jp-card-elevated p-4"
         style={{ background: 'rgba(224,28,46,0.06)', border: '1px solid rgba(224,28,46,0.25)' }}
         data-testid="wallet-anomalies-banner">
      <div className="flex items-center gap-2 mb-3">
        <Warning size={18} weight="fill" style={{ color: '#E01C2E' }} />
        <h4 className="font-['Outfit'] font-bold text-sm" style={{ color: '#E01C2E' }}>
          {sections.reduce((a, s) => a + s.items.length, 0)} anomalie(s) à revoir
        </h4>
      </div>
      <div className="space-y-3">
        {sections.map(s => (
          <div key={s.key}>
            <div className="text-[11px] uppercase tracking-wider font-bold mb-1"
                 style={{ color: 'var(--jp-text-secondary)' }}>
              {s.label} · {s.items.length}
            </div>
            <ul className="text-xs space-y-0.5 pl-5 list-disc"
                style={{ color: 'var(--jp-text-muted)' }}>
              {s.items.slice(0, 5).map((x, i) => (
                <li key={i} className="font-mono">{s.fmt(x)}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
