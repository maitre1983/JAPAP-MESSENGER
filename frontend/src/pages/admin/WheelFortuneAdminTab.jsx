/**
 * WheelFortuneAdminTab — admin dashboard for the v2 engagement engine.
 *
 * Consumes:
 *   GET  /api/wheel/admin/observability   — engagement + anomalies + reco
 *   POST /api/wheel/admin/flag-suspicious — mass-flag bot-like accounts
 *   POST /api/wheel/admin/send-cycle-reminders — trigger push reminders
 *
 * Why a dedicated tab?
 *   Because the OLD "JAPAP Spin" tab only covers the legacy XAF mini-game.
 *   The real engagement engine runs on points + cycles + Starter Pro, and
 *   deserves its own ops dashboard.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Target, Trophy, Users, ChartLineUp, ShieldCheck, Flag, Bell, Warning,
  CheckCircle, XCircle, ArrowClockwise, Crown, Gear, FloppyDisk,
  Lightning, DownloadSimple, ChartPieSlice, ShieldStar,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function WheelFortuneAdminTab() {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actioning, setActioning] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/wheel/admin/observability`, { withCredentials: true });
      setData(data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement observabilité');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const doFlagSuspicious = async () => {
    setActioning('flag');
    try {
      const { data } = await axios.post(`${API}/api/wheel/admin/flag-suspicious`, {}, { withCredentials: true });
      toast.success(`${data.flagged_count} compte(s) flaggé(s)`);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setActioning(''); }
  };

  const doSendReminders = async () => {
    setActioning('reminders');
    try {
      const { data } = await axios.post(`${API}/api/wheel/admin/send-cycle-reminders`, {}, { withCredentials: true });
      toast.success(`Rappels envoyés : ${data.sent || 0}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setActioning(''); }
  };

  if (loading) {
    return <div className="p-6 text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement observabilité…</div>;
  }
  if (!data) return null;

  const { engagement: e, reward_distribution: r, anomalies: a, protections: p, recommendation } = data;
  const recColor = {
    activate_now: '#E01C2E',
    monitor_closely: '#F59E0B',
    not_needed_yet: '#10B981',
  }[recommendation.action] || '#64748b';
  const recIcon = {
    activate_now: <XCircle size={18} weight="fill" />,
    monitor_closely: <Warning size={18} weight="fill" />,
    not_needed_yet: <CheckCircle size={18} weight="fill" />,
  }[recommendation.action] || null;

  return (
    <div className="space-y-5 jp-animate-fadeIn" data-testid="wheel-admin-tab">
      {/* Header + recommendation */}
      <div className="jp-card-elevated p-5">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-2xl flex items-center justify-center"
                 style={{ background: 'linear-gradient(135deg, #FFD700, #F7931A)' }}>
              <Target size={24} color="#111" weight="fill" />
            </div>
            <div>
              <h2 className="font-['Outfit'] text-xl font-bold" style={{ color: 'var(--jp-text)' }}>
                Roue de la Fortune — observabilité
              </h2>
              <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                Cycle 30j · 10 000 pts + 25 jours distincts · Récompense : Starter Pro 30j
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full"
               style={{ background: `${recColor}22`, border: `1px solid ${recColor}55`, color: recColor }}>
            {recIcon}
            <span className="text-xs font-bold">{recommendation.action.replace('_', ' ').toUpperCase()}</span>
          </div>
        </div>
        <p className="text-xs mt-3" style={{ color: 'var(--jp-text-secondary)' }}>{recommendation.reason}</p>
      </div>

      {/* KPIs stratégiques (pilotage business) */}
      <StrategicKpis />

      {/* Graphique temporel (obligatoire) */}
      <TimeseriesChart />

      {/* Engagement KPIs */}
      <div>
        <h3 className="font-['Outfit'] font-bold text-sm mb-3 flex items-center gap-2"
            style={{ color: 'var(--jp-text)' }}>
          <ChartLineUp size={16} /> Engagement
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <KPI label="Users actifs" value={e.active_users} icon={<Users size={16} />} />
          <KPI label="Cycles actifs" value={e.active_cycles} />
          <KPI label="DAU 24h" value={e.dau_24h} accent />
          <KPI label="Spins 24h" value={e.spins_24h} />
          <KPI label="Spins 7j" value={e.spins_7d} />
          <KPI label="Spins total" value={e.total_spins} />
          <KPI label="Jackpots" value={e.jackpots_total} accent />
          <KPI label="Moy. pts/spin" value={e.avg_points_per_spin} />
        </div>
      </div>

      {/* Reward distribution */}
      <div>
        <h3 className="font-['Outfit'] font-bold text-sm mb-3 flex items-center gap-2"
            style={{ color: 'var(--jp-text)' }}>
          <Trophy size={16} /> Distribution des récompenses
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <KPI label="Vainqueurs" value={r.winners_total} />
          <KPI label="Réclamés" value={r.claimed_total} accent />
          <KPI label="Taux claim" value={`${Math.round((r.claim_rate || 0) * 100)}%`} />
          <KPI label="Jours médians" value={r.median_days_to_win || '—'} />
          <KPI label="Jours moy." value={r.avg_days_to_win || '—'} />
          <KPI label="Pts moy. au claim" value={r.avg_points_at_claim || '—'} />
          <KPI label="En attente" value={e.pending_claims} />
          <KPI label="Expirés non réclamés" value={e.expired_unclaimed} />
        </div>
      </div>

      {/* Anomalies */}
      <div>
        <h3 className="font-['Outfit'] font-bold text-sm mb-3 flex items-center gap-2"
            style={{ color: 'var(--jp-text)' }}>
          <Warning size={16} /> Anomalies · score {a.anomaly_score}
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <AnomalyList title="Bot-like (>30 spins/h 24h)" rows={a.bot_like_accounts}
                       cols={['user_id', 'spins_24h']} empty="Aucun" />
          <AnomalyList title={t('wheel_fortune_admin.multi_comptes_meme_ip_4_users_7j')} rows={a.same_ip_clusters}
                       cols={['ip_address', 'distinct_users', 'spins']} empty="Aucun" />
          <AnomalyList title={t('wheel_fortune_admin.multi_comptes_meme_fingerprint_3_us')} rows={a.same_fingerprint_clusters}
                       cols={['device_fingerprint', 'distinct_users', 'spins']} empty="Aucun" />
          <AnomalyList title="Progression trop rapide (>1800 pts/jour)" rows={a.rapid_progression}
                       cols={['user_id', 'points_cycle', 'days_played', 'pts_per_day']} empty="Aucun" />
          <AnomalyList title="Night-owls (>55% spins 00h-06h UTC)" rows={a.night_owls}
                       cols={['user_id', 'total_spins', 'night_spins', 'ratio']} empty="Aucun" />
        </div>
      </div>

      {/* Protections */}
      <div className="jp-card p-4">
        <h3 className="font-['Outfit'] font-bold text-sm mb-3 flex items-center gap-2"
            style={{ color: 'var(--jp-text)' }}>
          <ShieldCheck size={16} /> Protections actives
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
          <Protection label="Roue activée" on={p.wheel_enabled} />
          <Protection label="Turnstile" on={p.turnstile_enabled} warn={!p.turnstile_enabled} />
          <Protection label={`Cooldown ${p.cooldown_seconds}s`} on={p.cooldown_seconds > 0} />
          <Protection label={`Cap ${p.max_spins_per_day}/jour`} on={p.max_spins_per_day > 0} />
          <Protection label={`Flag suspect (${p.suspicious_cycles})`} on={p.suspicious_cycles >= 0} />
        </div>
      </div>

      {/* Actions */}
      <div className="flex flex-wrap gap-3">
        <button onClick={load} disabled={loading} data-testid="wheel-admin-refresh"
                className="jp-btn jp-btn-secondary text-sm">
          Rafraîchir
        </button>
        <button onClick={doFlagSuspicious} disabled={actioning === 'flag'}
                data-testid="wheel-admin-flag-suspicious"
                className="jp-btn jp-btn-danger text-sm flex items-center gap-2">
          <Flag size={14} weight="fill" />
          {actioning === 'flag' ? 'Flaggage…' : 'Flagger comptes suspects'}
        </button>
        <button onClick={doSendReminders} disabled={actioning === 'reminders'}
                data-testid="wheel-admin-send-reminders"
                className="jp-btn jp-btn-primary text-sm flex items-center gap-2">
          <Bell size={14} weight="fill" />
          {actioning === 'reminders' ? 'Envoi…' : 'Envoyer rappels cycle'}
        </button>
      </div>

      {/* Cycles utilisateurs (priorité élevée) */}
      <CyclesTable />

      {/* Configuration admin UI (pas via API seulement) */}
      <WheelConfigForm />

      {/* iter114 — Wheel Boost Event admin form (retention spike) */}
      <WheelBoostForm />

      {/* iter115 — Recurring boost schedules (cron-style automation) */}
      <WheelBoostScheduleManager />

      <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
        Dernière extraction : {new Date(data.generated_at).toLocaleString('fr-FR')}
      </p>
    </div>
  );
}

/* ───────────────── KPI + anomaly helpers ───────────────── */

function KPI({ label, value, accent, icon }) {
  return (
    <div className="jp-card p-3" style={{ borderLeft: accent ? '3px solid #FFD700' : undefined }}>
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-widest font-bold"
           style={{ color: 'var(--jp-text-muted)' }}>
        {icon}{label}
      </div>
      <div className="font-['Outfit'] text-xl font-bold mt-1" style={{ color: 'var(--jp-text)' }}>
        {value ?? '—'}
      </div>
    </div>
  );
}

function AnomalyList({ title, rows, cols, empty }) {
  return (
    <div className="jp-card p-4">
      <div className="text-xs font-bold mb-2" style={{ color: 'var(--jp-text)' }}>{title}</div>
      {(!rows || rows.length === 0) ? (
        <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{empty}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr style={{ color: 'var(--jp-text-muted)' }}>
                {cols.map(c => <th key={c} className="text-left py-1 pr-2 font-semibold">{c}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 8).map((row, i) => (
                <tr key={i} style={{ borderTop: '1px solid var(--jp-border)' }}>
                  {cols.map(c => (
                    <td key={c} className="py-1.5 pr-2 font-mono" style={{ color: 'var(--jp-text)' }}>
                      {String(row[c] ?? '—').slice(0, 24)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Protection({ label, on, warn }) {
  const color = on ? (warn ? '#F59E0B' : '#10B981') : '#E01C2E';
  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg"
         style={{ background: `${color}18`, border: `1px solid ${color}44` }}>
      <span className="w-2 h-2 rounded-full" style={{ background: color }} />
      <span className="text-xs font-semibold" style={{ color }}>{label}</span>
    </div>
  );
}

/* ───────────────── Cycles utilisateurs (priorité élevée) ─────────────────
 * Paginated table of every user's current wheel cycle, with the progression
 * bar towards 10 000 points + 25 jours, suspicious_flag, and a shortcut
 * button to open the user profile. Admin can filter to only see cycles
 * near the goal or already flagged. */

const getStatus_filters = (t) => ([
  { id: 'all', label: 'Tous' },
  { id: 'in_progress', label: 'En cours' },
  { id: 'near_goal', label: 'Proches objectif' },
  { id: 'reward_pending', label: t('wheel_fortune_admin.a_reclamer') },
  { id: 'reward_claimed', label: t('wheel_fortune_admin.reclames') },
  { id: 'suspicious', label: 'Suspects' },
]);

function CyclesTable() {
  const STATUS_FILTERS = getStatus_filters(t);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState('near_goal');
  const [loading, setLoading] = useState(false);
  const LIMIT = 25;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(
        `${API}/api/wheel/admin/cycles?status=${statusFilter}&limit=${LIMIT}&offset=${offset}`,
        { withCredentials: true },
      );
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement cycles');
    } finally { setLoading(false); }
  }, [statusFilter, offset]);

  useEffect(() => { load(); }, [load]);

  return (
    <section className="jp-card-elevated p-5" data-testid="wheel-admin-cycles">
      <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
        <div>
          <h3 className="font-['Outfit'] font-bold text-lg flex items-center gap-2"
              style={{ color: 'var(--jp-text)' }}>
            <Users size={18} /> Cycles utilisateurs
          </h3>
          <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
            Vue par cycle · progression 10 000 pts / 25 jours · suspicious flag · accès profil
          </p>
        </div>
        <div className="flex flex-wrap gap-1 items-center">
          {STATUS_FILTERS.map((f) => (
            <button key={f.id}
                    onClick={() => { setStatusFilter(f.id); setOffset(0); }}
                    data-testid={`cycles-filter-${f.id}`}
                    className="text-[11px] px-2.5 py-1 rounded-full font-semibold"
                    style={{
                      background: statusFilter === f.id ? '#0F056B' : 'var(--jp-surface)',
                      color: statusFilter === f.id ? '#fff' : 'var(--jp-text-muted)',
                      border: '1px solid var(--jp-border)',
                    }}>
              {f.label}
            </button>
          ))}
          <a href={`${API}/api/wheel/admin/cycles/export.csv?status=${statusFilter}`}
             data-testid="cycles-export-csv"
             className="text-[11px] px-2.5 py-1 rounded-full font-semibold flex items-center gap-1 ml-2"
             style={{ background: '#10B981', color: '#fff' }}
             download>
            <DownloadSimple size={12} weight="bold" /> Exporter CSV
          </a>
        </div>
      </div>

      {loading ? (
        <div className="text-sm text-center py-6" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>
      ) : items.length === 0 ? (
        <div className="text-sm text-center py-8" style={{ color: 'var(--jp-text-muted)' }}>
          Aucun cycle pour ce filtre.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs" data-testid="cycles-table">
            <thead>
              <tr style={{ color: 'var(--jp-text-muted)' }}>
                <th className="text-left py-2 pr-3 font-semibold">Utilisateur</th>
                <th className="text-left py-2 pr-3 font-semibold">Points / 10 000</th>
                <th className="text-left py-2 pr-3 font-semibold">Jours / 25</th>
                <th className="text-left py-2 pr-3 font-semibold">Streak</th>
                <th className="text-left py-2 pr-3 font-semibold">Statut</th>
                <th className="text-left py-2 pr-3 font-semibold">Reste cycle</th>
                <th className="text-left py-2 pr-3 font-semibold">Action</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => <CycleRow key={it.cycle_id} row={it} onAction={load} />)}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {total > LIMIT && (
        <div className="flex items-center justify-between mt-4 text-xs"
             style={{ color: 'var(--jp-text-muted)' }}>
          <span>{offset + 1}-{Math.min(offset + LIMIT, total)} sur {total}</span>
          <div className="flex gap-2">
            <button onClick={() => setOffset(Math.max(0, offset - LIMIT))}
                    disabled={offset === 0}
                    className="jp-btn jp-btn-secondary text-xs">Précédent</button>
            <button onClick={() => setOffset(offset + LIMIT)}
                    disabled={offset + LIMIT >= total}
                    className="jp-btn jp-btn-secondary text-xs">Suivant</button>
          </div>
        </div>
      )}
    </section>
  );
}

function CycleRow({ row, onAction }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState('');
  const statusColor = {
    in_progress: '#0F056B',
    reward_pending: '#F7931A',
    reward_claimed: '#10B981',
    completed_won: '#64748b',
    completed_lost: '#94a3b8',
  }[row.reward_status] || '#64748b';
  const statusLabel = {
    in_progress: 'En cours',
    reward_pending: t('wheel_fortune_admin.a_reclamer'),
    reward_claimed: t('wheel_fortune_admin.reclame'),
    completed_won: t('wheel_fortune_admin.expire_gagnant'),
    completed_lost: t('wheel_fortune_admin.expire_perdant'),
  }[row.reward_status] || row.reward_status;

  const forceClaim = async () => {
    if (!window.confirm(`Forcer le claim Starter Pro pour ${row.display_name} ?`)) return;
    setBusy('claim');
    try {
      await axios.post(
        `${API}/api/wheel/admin/cycles/${row.cycle_id}/force-claim`, {},
        { withCredentials: true },
      );
      toast.success('Starter Pro attribué.');
      onAction && onAction();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur claim');
    } finally { setBusy(''); }
  };
  const resetFlag = async () => {
    if (!window.confirm(`Reset suspicious_flag pour ${row.display_name} ?`)) return;
    setBusy('reset');
    try {
      await axios.post(
        `${API}/api/wheel/admin/cycles/${row.cycle_id}/reset-suspicious`, {},
        { withCredentials: true },
      );
      toast.success('Flag suspect retiré.');
      onAction && onAction();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur reset');
    } finally { setBusy(''); }
  };

  const canForceClaim = row.can_claim && row.reward_status === 'reward_pending';
  const canResetFlag = row.suspicious_flag;

  return (
    <tr style={{ borderTop: '1px solid var(--jp-border)' }}>
      {/* Utilisateur */}
      <td className="py-3 pr-3">
        <div className="flex items-center gap-2">
          {row.avatar ? (
            <img src={row.avatar.startsWith('http') ? row.avatar : `${API}${row.avatar}`}
                 alt="" className="w-7 h-7 rounded-full object-cover" />
          ) : (
            <div className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white"
                 style={{ background: '#0F056B' }}>
              {(row.display_name || '?').slice(0, 1).toUpperCase()}
            </div>
          )}
          <div className="min-w-0">
            <div className="font-semibold truncate max-w-[140px]" style={{ color: 'var(--jp-text)' }}>
              {row.display_name}
              {row.is_pro && <Crown size={10} weight="fill" className="inline ml-1" style={{ color: '#FFD700' }} />}
              {row.suspicious_flag && <Warning size={10} weight="fill" className="inline ml-1" style={{ color: '#E01C2E' }} />}
            </div>
            <div className="text-[10px] font-mono truncate max-w-[160px]" style={{ color: 'var(--jp-text-muted)' }}>
              {row.email || row.user_id}
            </div>
          </div>
        </div>
      </td>

      {/* Points */}
      <td className="py-3 pr-3 min-w-[140px]">
        <div className="text-[11px] font-semibold mb-1" style={{ color: 'var(--jp-text)' }}>
          {row.points_cycle.toLocaleString('fr-FR')} / {row.points_goal.toLocaleString('fr-FR')}
        </div>
        <div className="h-1.5 rounded-full overflow-hidden"
             style={{ background: 'var(--jp-surface)' }}>
          <div className="h-full transition-all"
               style={{ width: `${row.points_percent}%`,
                        background: 'linear-gradient(90deg, #FFD700, #E01C2E)' }} />
        </div>
      </td>

      {/* Jours */}
      <td className="py-3 pr-3 min-w-[120px]">
        <div className="text-[11px] font-semibold mb-1" style={{ color: 'var(--jp-text)' }}>
          {row.days_played_count} / {row.days_goal}
        </div>
        <div className="h-1.5 rounded-full overflow-hidden"
             style={{ background: 'var(--jp-surface)' }}>
          <div className="h-full transition-all"
               style={{ width: `${row.days_percent}%`,
                        background: 'linear-gradient(90deg, #10B981, #06B6D4)' }} />
        </div>
      </td>

      {/* Streak */}
      <td className="py-3 pr-3 text-[11px] font-semibold" style={{ color: 'var(--jp-text)' }}>
        {row.streak_days}j
      </td>

      {/* Statut */}
      <td className="py-3 pr-3">
        <span className="text-[10px] px-2 py-0.5 rounded-full font-semibold"
              style={{ background: `${statusColor}22`, color: statusColor, border: `1px solid ${statusColor}55` }}>
          {statusLabel}
        </span>
      </td>

      {/* Cycle restant */}
      <td className="py-3 pr-3 text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
        {row.remaining_days_in_cycle}j
      </td>

      {/* Actions */}
      <td className="py-3 pr-3">
        <div className="flex flex-col gap-1">
          {canForceClaim && (
            <button onClick={forceClaim} disabled={busy === 'claim'}
                    data-testid={`cycle-force-claim-${row.cycle_id}`}
                    className="text-[10px] font-bold px-2 py-1 rounded-md flex items-center gap-1"
                    style={{ background: '#FFD700', color: '#111' }}>
              <Lightning size={10} weight="fill" />
              {busy === 'claim' ? '…' : 'Forcer le claim'}
            </button>
          )}
          {canResetFlag && (
            <button onClick={resetFlag} disabled={busy === 'reset'}
                    data-testid={`cycle-reset-flag-${row.cycle_id}`}
                    className="text-[10px] font-bold px-2 py-1 rounded-md flex items-center gap-1"
                    style={{ background: '#E01C2E', color: '#fff' }}>
              <ShieldStar size={10} weight="fill" />
              {busy === 'reset' ? '…' : 'Reset flag'}
            </button>
          )}
          <a href={`/admin?tab=users&q=${encodeURIComponent(row.user_id)}`}
             data-testid={`cycle-goto-profile-${row.user_id}`}
             className="text-[10px] font-semibold underline"
             style={{ color: '#0F056B' }}>
            Voir profil
          </a>
        </div>
      </td>
    </tr>
  );
}

/* ───────────────── Configuration admin complète ───────────────── */

function WheelConfigForm() {
  const [cfg, setCfg] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/wheel/admin/config`, { withCredentials: true });
      setCfg(data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur config');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    try {
      const payload = {
        wheel_enabled: !!cfg.wheel_enabled,
        quiz_enabled: !!cfg.quiz_enabled,
        tap_enabled: !!cfg.tap_enabled,
        duel_enabled: !!cfg.duel_enabled,
        wheel_turnstile_enabled: !!cfg.wheel_turnstile_enabled,
        max_spins_per_day: parseInt(cfg.max_spins_per_day, 10),
        cooldown_seconds: parseInt(cfg.cooldown_seconds, 10),
        jackpot_odds_in_window: parseInt(cfg.jackpot_odds_in_window, 10),
        near_miss_odds: parseInt(cfg.near_miss_odds, 10),
        streak_3_bonus: parseInt(cfg.streak_3_bonus, 10),
        streak_7_bonus: parseInt(cfg.streak_7_bonus, 10),
        streak_15_bonus: parseInt(cfg.streak_15_bonus, 10),
        quiz_timer_seconds: parseInt(cfg.quiz_timer_seconds, 10),
        duel_winner_bonus: parseInt(cfg.duel_winner_bonus, 10),
        duel_loser_bonus: parseInt(cfg.duel_loser_bonus, 10),
        duel_accepts_per_day: parseInt(cfg.duel_accepts_per_day, 10),
      };
      await axios.put(`${API}/api/wheel/admin/config`, payload, { withCredentials: true });
      toast.success('Configuration Roue sauvegardée');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur sauvegarde');
    } finally { setSaving(false); }
  };

  if (loading || !cfg) {
    return <div className="jp-card p-5 text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement configuration…</div>;
  }

  const set = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  return (
    <section className="jp-card-elevated p-5" data-testid="wheel-admin-config">
      <h3 className="font-['Outfit'] font-bold text-lg flex items-center gap-2 mb-1"
          style={{ color: 'var(--jp-text)' }}>
        <Gear size={18} /> Configuration roue
      </h3>
      <p className="text-[11px] mb-4" style={{ color: 'var(--jp-text-muted)' }}>
        Règles métier verrouillées côté code (lecture seule) : cycle {cfg.constants.cycle_length_days}j · objectif {cfg.constants.points_goal.toLocaleString('fr-FR')} pts + {cfg.constants.days_goal} jours distincts · grâce {cfg.constants.claim_grace_days}j. Les caps par phase sont {JSON.stringify(cfg.constants.max_points_per_day_by_phase)}.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Toggles */}
        <ConfigToggle label="Roue activée"
                      value={cfg.wheel_enabled}
                      onChange={(v) => set('wheel_enabled', v)}
                      testid="cfg-wheel-enabled" />
        <ConfigToggle label="Quiz JAPAP activé"
                      value={cfg.quiz_enabled}
                      helper="Toggle instantané — message utilisateur si OFF"
                      onChange={(v) => set('quiz_enabled', v)}
                      testid="cfg-quiz-enabled" />
        <ConfigToggle label="Tap Challenge activé"
                      value={cfg.tap_enabled}
                      helper="Toggle instantané — message utilisateur si OFF"
                      onChange={(v) => set('tap_enabled', v)}
                      testid="cfg-tap-enabled" />
        <ConfigToggle label="Challenge d'ami activé"
                      value={cfg.duel_enabled}
                      helper="Active la création/acceptation des duels (Quiz + Tap)"
                      onChange={(v) => set('duel_enabled', v)}
                      testid="cfg-duel-enabled" />
        <ConfigToggle label="Protection Turnstile"
                      value={cfg.wheel_turnstile_enabled}
                      onChange={(v) => set('wheel_turnstile_enabled', v)}
                      helper="Nécessite TURNSTILE_SITE_KEY + SECRET_KEY"
                      testid="cfg-turnstile" />

        <ConfigNumber label="Max spins / jour / utilisateur"
                      value={cfg.max_spins_per_day} min={1} max={100}
                      onChange={(v) => set('max_spins_per_day', v)}
                      testid="cfg-max-spins" />
        <ConfigNumber label="Cooldown entre spins (secondes)"
                      value={cfg.cooldown_seconds} min={0} max={3600}
                      onChange={(v) => set('cooldown_seconds', v)}
                      testid="cfg-cooldown" />

        <ConfigNumber label="Probabilité Jackpot en fenêtre (%)"
                      value={cfg.jackpot_odds_in_window} min={0} max={100}
                      onChange={(v) => set('jackpot_odds_in_window', v)}
                      helper="Ne s'applique QUE si points≥8000 + phase 3 + days≥20"
                      testid="cfg-jackpot-odds" />
        <ConfigNumber label="Probabilité Near-miss (%)"
                      value={cfg.near_miss_odds} min={0} max={100}
                      onChange={(v) => set('near_miss_odds', v)}
                      helper="Effet 'presque gagné' si phase≥2 + points∈[5k,8k]"
                      testid="cfg-near-miss-odds" />

        <ConfigNumber label="Bonus streak 3 jours"
                      value={cfg.streak_3_bonus} min={0} max={10000}
                      onChange={(v) => set('streak_3_bonus', v)}
                      testid="cfg-streak-3" />
        <ConfigNumber label="Bonus streak 7 jours"
                      value={cfg.streak_7_bonus} min={0} max={10000}
                      onChange={(v) => set('streak_7_bonus', v)}
                      testid="cfg-streak-7" />
        <ConfigNumber label="Bonus streak 15 jours"
                      value={cfg.streak_15_bonus} min={0} max={10000}
                      onChange={(v) => set('streak_15_bonus', v)}
                      testid="cfg-streak-15" />

        <ConfigNumber label="Timer Quiz (secondes / 5 questions)"
                      value={cfg.quiz_timer_seconds} min={5} max={60}
                      onChange={(v) => set('quiz_timer_seconds', v)}
                      helper="Appliqué à la prochaine session /start"
                      testid="cfg-quiz-timer" />
        <ConfigNumber label="Bonus gagnant d'un défi (pts)"
                      value={cfg.duel_winner_bonus} min={0} max={500}
                      onChange={(v) => set('duel_winner_bonus', v)}
                      testid="cfg-duel-winner-bonus" />
        <ConfigNumber label="Bonus consolation (pts)"
                      value={cfg.duel_loser_bonus} min={0} max={500}
                      onChange={(v) => set('duel_loser_bonus', v)}
                      helper="Attribué au perdant et aux matchs nuls"
                      testid="cfg-duel-loser-bonus" />
        <ConfigNumber label="Défis acceptés max / jour"
                      value={cfg.duel_accepts_per_day} min={1} max={20}
                      onChange={(v) => set('duel_accepts_per_day', v)}
                      helper="Anti-abus côté adversaire"
                      testid="cfg-duel-accepts-per-day" />
      </div>

      <div className="mt-5 flex items-center gap-3">
        <button onClick={save} disabled={saving}
                data-testid="wheel-config-save"
                className="jp-btn jp-btn-primary flex items-center gap-2">
          <FloppyDisk size={14} weight="fill" />
          {saving ? 'Sauvegarde…' : 'Enregistrer'}
        </button>
        <button onClick={load} disabled={loading || saving}
                data-testid="wheel-config-reset"
                className="jp-btn jp-btn-ghost text-sm flex items-center gap-2">
          <ArrowClockwise size={14} /> Recharger
        </button>
      </div>
    </section>
  );
}

function ConfigToggle({ label, value, onChange, helper, testid }) {
  return (
    <div className="flex items-center justify-between p-3 rounded-lg"
         style={{ background: 'var(--jp-surface)', border: '1px solid var(--jp-border)' }}>
      <div className="flex-1 min-w-0 pr-3">
        <div className="text-xs font-semibold" style={{ color: 'var(--jp-text)' }}>{label}</div>
        {helper && <div className="text-[10px] mt-0.5" style={{ color: 'var(--jp-text-muted)' }}>{helper}</div>}
      </div>
      <button type="button" onClick={() => onChange(!value)} data-testid={testid}
              className="relative w-11 h-6 rounded-full transition-colors"
              style={{ background: value ? '#10B981' : '#cbd5e1' }}>
        <span className="absolute top-0.5 w-5 h-5 rounded-full bg-white transition-all"
              style={{ left: value ? '22px' : '2px', boxShadow: '0 2px 4px rgba(0,0,0,0.15)' }} />
      </button>
    </div>
  );
}

function ConfigNumber({ label, value, min, max, onChange, helper, testid }) {
  return (
    <div className="p-3 rounded-lg"
         style={{ background: 'var(--jp-surface)', border: '1px solid var(--jp-border)' }}>
      <div className="text-xs font-semibold mb-1" style={{ color: 'var(--jp-text)' }}>{label}</div>
      <input type="number" min={min} max={max}
             value={value ?? ''}
             onChange={(e) => onChange(e.target.value)}
             data-testid={testid}
             className="jp-input text-sm w-full" />
      {helper && <div className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>{helper}</div>}
    </div>
  );
}

/* ───────────────── KPIs stratégiques (pilotage business) ─────────────────
 * 4 indicateurs critiques pour répondre en 5 secondes :
 *   1. Est-ce que le système fonctionne ? (completion_rate)
 *   2. Est-ce trop dur ? (blocked_below_goal_pct, avg_days_played)
 *   3. Est-ce exploité ? (avg_points_j10, avg_points_j20 vs cap théorique)
 */

function StrategicKpis() {
  const { t } = useTranslation();
  const [k, setK] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/wheel/admin/strategic-kpis`, { withCredentials: true });
      setK(data);
    } catch (e) {
      // silent — non-critical, other sections still work
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading || !k) {
    return <div className="jp-card p-4 text-xs" style={{ color: 'var(--jp-text-muted)' }}>Chargement KPIs…</div>;
  }

  const verdictColor = {
    healthy: '#10B981',
    too_hard: '#E01C2E',
    too_easy_or_exploited: '#F59E0B',
    insufficient_data: '#64748b',
  }[k.verdict] || '#64748b';
  const verdictLabel = {
    healthy: t('wheel_fortune_admin.systeme_sain'),
    too_hard: 'Trop difficile',
    too_easy_or_exploited: t('wheel_fortune_admin.trop_facile_ou_exploite'),
    insufficient_data: t('wheel_fortune_admin.donnees_insuffisantes'),
  }[k.verdict] || k.verdict;

  return (
    <section data-testid="wheel-admin-strategic-kpis">
      <h3 className="font-['Outfit'] font-bold text-sm mb-3 flex items-center gap-2"
          style={{ color: 'var(--jp-text)' }}>
        <ChartPieSlice size={16} /> KPIs stratégiques — pilotage business
        <span className="text-[10px] px-2 py-0.5 rounded-full font-bold"
              style={{ background: `${verdictColor}22`, color: verdictColor,
                       border: `1px solid ${verdictColor}66` }}>
          {verdictLabel}
        </span>
      </h3>
      <p className="text-[11px] mb-3" style={{ color: 'var(--jp-text-muted)' }}>
        {k.verdict_message}
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiBig
          label="Taux de complétion"
          value={`${Math.round(k.completion_rate * 100)}%`}
          sub={`${k.claimed_cycles}/${k.terminal_cycles} cycles`}
          color="#10B981"
        />
        <KpiBig
          label="Bloqués avant 25j"
          value={`${Math.round(k.blocked_below_goal_pct * 100)}%`}
          sub="cycles perdus par manque de jours"
          color="#E01C2E"
        />
        <KpiBig
          label="Jours moyens joués"
          value={k.avg_days_played || '—'}
          sub={`sur 25 requis`}
          color="#0F056B"
        />
        <KpiBig
          label="Points moy. J+10 / J+20"
          value={`${Math.round(k.avg_points_j10 || 0)} / ${Math.round(k.avg_points_j20 || 0)}`}
          sub="exploitable si >2000 / >5000"
          color="#F7931A"
        />
      </div>
    </section>
  );
}

function KpiBig({ label, value, sub, color }) {
  return (
    <div className="jp-card p-4" style={{ borderTop: `3px solid ${color}` }}>
      <div className="text-[10px] uppercase tracking-widest font-bold"
           style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
      <div className="font-['Outfit'] text-2xl font-extrabold mt-1"
           style={{ color }}>{value}</div>
      <div className="text-[10px] mt-0.5" style={{ color: 'var(--jp-text-muted)' }}>{sub}</div>
    </div>
  );
}

/* ───────────────── Graphique temporel (obligatoire) ─────────────────
 * 3 courbes sur 7 jours minimum :
 *   - completion_rate (taux de claim / jour)
 *   - dau_24h (utilisateurs actifs / jour)
 *   - avg_points_cycle (proxy de progression)
 *
 * Alerte visuelle si completion_rate monte ET dau baisse → churn post-reward.
 */

import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';

function TimeseriesChart() {
  const { t } = useTranslation();
  const [data, setData] = useState([]);
  const [period, setPeriod] = useState(7);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(
        `${API}/api/wheel/admin/timeseries?days=${period}`,
        { withCredentials: true },
      );
      setData(data.series || []);
    } catch (e) { /* silent */ }
    finally { setLoading(false); }
  }, [period]);
  useEffect(() => { load(); }, [load]);

  // Churn detection : compare last 3 days to previous 3 days
  const churnSignal = (() => {
    if (data.length < 6) return null;
    const prev = data.slice(-6, -3);
    const recent = data.slice(-3);
    const avg = (arr, k) => arr.reduce((s, d) => s + (d[k] || 0), 0) / Math.max(1, arr.length);
    const crDelta = avg(recent, 'completion_rate') - avg(prev, 'completion_rate');
    const dauDelta = avg(recent, 'dau_24h') - avg(prev, 'dau_24h');
    if (crDelta > 0.05 && dauDelta < -1) {
      return 'churn_post_reward';
    }
    return null;
  })();

  return (
    <section className="jp-card-elevated p-5" data-testid="wheel-admin-timeseries">
      <div className="flex items-center justify-between flex-wrap gap-3 mb-3">
        <div>
          <h3 className="font-['Outfit'] font-bold text-sm flex items-center gap-2"
              style={{ color: 'var(--jp-text)' }}>
            <ChartLineUp size={16} /> Évolution temporelle
          </h3>
          <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
            Completion rate · DAU · Points moyens — {period} derniers jours
          </p>
        </div>
        <div className="flex gap-1">
          {[7, 14, 30].map((d) => (
            <button key={d} onClick={() => setPeriod(d)}
                    data-testid={`timeseries-period-${d}`}
                    className="text-[11px] px-2.5 py-1 rounded-full font-semibold"
                    style={{
                      background: period === d ? '#0F056B' : 'var(--jp-surface)',
                      color: period === d ? '#fff' : 'var(--jp-text-muted)',
                      border: '1px solid var(--jp-border)',
                    }}>
              {d}j
            </button>
          ))}
        </div>
      </div>

      {churnSignal && (
        <div className="mb-3 p-2 rounded-lg text-[11px] flex items-center gap-2"
             style={{ background: '#E01C2E18', color: '#E01C2E', border: '1px solid #E01C2E55' }}
             data-testid="wheel-churn-alert">
          <Warning size={14} weight="fill" />
          <span>
            <strong>{t('wheel_fortune_admin.churn_post_reward_detecte')}</strong> le taux de complétion monte mais la DAU baisse.
            Les gagnants partent après avoir touché leur Starter Pro — à analyser.
          </span>
        </div>
      )}

      {loading ? (
        <div className="h-60 flex items-center justify-center text-xs"
             style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>
      ) : data.length === 0 ? (
        <div className="h-60 flex items-center justify-center text-xs"
             style={{ color: 'var(--jp-text-muted)' }}>{t('wheel_fortune_admin.aucune_donnee')}</div>
      ) : (
        <div style={{ width: '100%', height: 280 }}>
          <ResponsiveContainer>
            <LineChart data={data} margin={{ top: 10, right: 30, left: 0, bottom: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--jp-border)" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: 'var(--jp-text-muted)' }}
                tickFormatter={(v) => v.slice(5)}
              />
              <YAxis yAxisId="left" tick={{ fontSize: 10, fill: 'var(--jp-text-muted)' }} />
              <YAxis yAxisId="right" orientation="right" domain={[0, 1]}
                     tick={{ fontSize: 10, fill: 'var(--jp-text-muted)' }}
                     tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <Tooltip
                contentStyle={{ fontSize: 11, background: 'var(--jp-bg)', border: '1px solid var(--jp-border)' }}
                formatter={(v, k) => k === 'completion_rate' ? `${Math.round(v * 100)}%` : v}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line yAxisId="left" type="monotone" dataKey="dau_24h"
                    name="DAU" stroke="#0F056B" strokeWidth={2} dot={{ r: 3 }} />
              <Line yAxisId="left" type="monotone" dataKey="avg_points_cycle"
                    name="Points moy. cycle" stroke="#F7931A" strokeWidth={2} dot={{ r: 3 }} />
              <Line yAxisId="right" type="monotone" dataKey="completion_rate"
                    name="Taux complétion" stroke="#10B981" strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="text-[10px] mt-2" style={{ color: 'var(--jp-text-muted)' }}>
        Note : "Points moy. cycle" = moyenne des points_cycle des cycles en cours
        (snapshot). Une valeur stable + DAU en baisse indique un essoufflement.
      </p>
    </section>
  );
}



/* ───────────────── iter114 — Wheel Boost Event admin form ───────────────── */

function WheelBoostForm() {
  const { t } = useTranslation();
  const [form, setForm] = useState(null);
  const [live, setLive] = useState(null);
  const [stats, setStats] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/wheel/admin/boost`,
                                       { withCredentials: true });
      setForm(data.settings);
      setLive(data.live);
      try {
        const r = await axios.get(`${API}/api/wheel/admin/boost/stats`,
                                  { withCredentials: true });
        setStats(r.data?.stats || null);
      } catch { /* ignore */ }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement Boost Event');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const update = (k) => (e) => {
    const v = e?.target?.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
  };

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        enabled: !!form.enabled,
        name: (form.name || '').slice(0, 80),
        starts_at: (form.starts_at || '').trim(),
        ends_at: (form.ends_at || '').trim(),
        gain_multiplier: Number(form.gain_multiplier) || 1.5,
        perdu_reduction_percent: Math.max(0, Math.min(95, Number(form.perdu_reduction_percent) || 0)),
        unlock_jackpot_all_phases: !!form.unlock_jackpot_all_phases,
        jackpot_odds_during_boost: Math.max(0, Math.min(100, Number(form.jackpot_odds_during_boost) || 25)),
      };
      const { data } = await axios.put(`${API}/api/wheel/admin/boost`, payload,
                                       { withCredentials: true });
      toast.success('Wheel Boost Event sauvegardé');
      setLive(data.live);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur sauvegarde');
    } finally { setSaving(false); }
  };

  if (!form) {
    return (
      <section className="jp-card p-4" data-testid="wheel-boost-form-loading">
        <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement Boost Event…</div>
      </section>
    );
  }

  const liveActive = !!live?.active;

  return (
    <section className="jp-card p-4" data-testid="wheel-boost-form">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2">
          <Lightning size={18} weight="fill" color="#FFD700" />
          Wheel Boost Event
        </h3>
        {liveActive ? (
          <span className="text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full"
                style={{ background: '#10B98122', color: '#10B981' }}
                data-testid="wheel-boost-live-active">
            🟢 En cours
          </span>
        ) : (
          <span className="text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full"
                style={{ background: 'rgba(0,0,0,0.06)', color: 'var(--jp-text-muted)' }}
                data-testid="wheel-boost-live-inactive">
            Inactif
          </span>
        )}
      </div>

      <p className="text-[12px] mb-3" style={{ color: 'var(--jp-text-muted)' }}>
        Pic de rétention contrôlé par admin (week-end, campagne, lancement Afrique).
        ⚠️ Ne pas casser l'équilibre long terme : limitez à 24-72h max.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="Activer le boost"
               testid="wheel-boost-enabled">
          <input type="checkbox"
                 checked={!!form.enabled}
                 onChange={update('enabled')}
                 data-testid="wheel-boost-enabled-toggle"
                 className="w-5 h-5" />
        </Field>
        <Field label="Nom de l'événement" testid="wheel-boost-name-field">
          <input type="text" maxLength={80}
                 value={form.name || ''}
                 onChange={update('name')}
                 placeholder={t('wheel_fortune_admin.black_friday_africa')}
                 data-testid="wheel-boost-name-input"
                 className="jp-input w-full" />
        </Field>
        <Field label="Début (ISO 8601)" testid="wheel-boost-start-field">
          <input type="text"
                 value={form.starts_at || ''}
                 onChange={update('starts_at')}
                 placeholder="2026-05-01T20:00:00Z"
                 data-testid="wheel-boost-start-input"
                 className="jp-input w-full" />
        </Field>
        <Field label="Fin (ISO 8601)" testid="wheel-boost-end-field">
          <input type="text"
                 value={form.ends_at || ''}
                 onChange={update('ends_at')}
                 placeholder="2026-05-03T20:00:00Z"
                 data-testid="wheel-boost-end-input"
                 className="jp-input w-full" />
        </Field>
        <Field label="Multiplicateur de gains (1.0 - 5.0)" testid="wheel-boost-mult-field">
          <input type="number" step="0.1" min={1} max={5}
                 value={form.gain_multiplier ?? 1.5}
                 onChange={update('gain_multiplier')}
                 data-testid="wheel-boost-mult-input"
                 className="jp-input w-full" />
        </Field>
        <Field label="Réduction des Perdus (%) (0-95)" testid="wheel-boost-perdu-field">
          <input type="number" step="5" min={0} max={95}
                 value={form.perdu_reduction_percent ?? 50}
                 onChange={update('perdu_reduction_percent')}
                 data-testid="wheel-boost-perdu-input"
                 className="jp-input w-full" />
        </Field>
        <Field label="Jackpot ouvert toutes phases" testid="wheel-boost-jackpot-field">
          <input type="checkbox"
                 checked={!!form.unlock_jackpot_all_phases}
                 onChange={update('unlock_jackpot_all_phases')}
                 data-testid="wheel-boost-jackpot-toggle"
                 className="w-5 h-5" />
        </Field>
        <Field label="Probabilité jackpot pendant boost (%)"
               testid="wheel-boost-jackpot-odds-field">
          <input type="number" min={0} max={100}
                 value={form.jackpot_odds_during_boost ?? 25}
                 onChange={update('jackpot_odds_during_boost')}
                 data-testid="wheel-boost-jackpot-odds-input"
                 className="jp-input w-full" />
        </Field>
      </div>

      <div className="flex justify-end mt-3">
        <button onClick={save} disabled={saving}
                data-testid="wheel-boost-save"
                className="jp-btn jp-btn-primary text-sm flex items-center gap-2">
          <FloppyDisk size={14} weight="fill" />
          {saving ? 'Sauvegarde…' : 'Enregistrer le Boost'}
        </button>
      </div>

      {/* Stats du boost actuel/dernier */}
      {stats && stats.total_spins > 0 && (
        <div className="mt-4 pt-3 border-t" style={{ borderColor: 'var(--jp-border)' }}
             data-testid="wheel-boost-stats">
          <div className="text-[11px] uppercase tracking-widest font-bold mb-2"
               style={{ color: 'var(--jp-text-muted)' }}>
            Performance du boost (id: {form.id || '—'})
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <BoostStat label="DAU" value={stats.dau} />
            <BoostStat label="Spins" value={stats.total_spins} />
            <BoostStat label="Win rate" value={`${stats.win_rate_percent}%`} />
            <BoostStat label="Perdu rate" value={`${stats.perdu_rate_percent}%`} />
            <BoostStat label="Jackpots" value={stats.jackpots} />
            <BoostStat label="Near-miss" value={stats.near_misses} />
            <BoostStat label="Total points distribués" value={stats.total_points} />
            <BoostStat label="Points moy./spin" value={stats.avg_points} />
          </div>
        </div>
      )}
    </section>
  );
}

function Field({ label, testid, children }) {
  return (
    <label className="text-xs flex flex-col gap-1" data-testid={testid}>
      <span className="font-semibold" style={{ color: 'var(--jp-text-muted)' }}>{label}</span>
      {children}
    </label>
  );
}

function BoostStat({ label, value }) {
  return (
    <div className="jp-card p-2">
      <div className="text-[9px] uppercase tracking-widest font-bold"
           style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
      <div className="font-['Outfit'] text-base font-bold mt-0.5">{value ?? '—'}</div>
    </div>
  );
}


/* ─────────── iter115 — Recurring Wheel Boost Schedules (cron-style) ───── */

const DOW_LABELS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];

function emptySchedule() {
  return {
    name: '',
    kind: 'recurring',
    dow_start: 4, time_start: '18:00',
    dow_end: 6, time_end: '23:00',
    date_start: '', date_end: '',
    gain_multiplier: 2.0,
    perdu_reduction_percent: 70,
    unlock_jackpot_all_phases: false,
    jackpot_odds_during_boost: 30,
    enabled: true,
  };
}

function describeSchedule(s) {
  if (s.kind === 'recurring') {
    return `${DOW_LABELS[s.dow_start]} ${s.time_start} → ${DOW_LABELS[s.dow_end]} ${s.time_end} (UTC)`;
  }
  return `${(s.date_start || '').slice(0, 16).replace('T', ' ')} → ${(s.date_end || '').slice(0, 16).replace('T', ' ')} UTC`;
}

function WheelBoostScheduleManager() {
  const { t } = useTranslation();
  const [items, setItems] = useState(null);
  const [editing, setEditing] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/wheel/admin/boost/schedules`,
                                       { withCredentials: true });
      setItems(data.schedules || []);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement schedules');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async (form) => {
    try {
      const payload = {
        name: form.name.trim(),
        kind: form.kind,
        dow_start: form.kind === 'recurring' ? Number(form.dow_start) : null,
        time_start: form.kind === 'recurring' ? form.time_start : null,
        dow_end: form.kind === 'recurring' ? Number(form.dow_end) : null,
        time_end: form.kind === 'recurring' ? form.time_end : null,
        date_start: form.kind === 'dated' ? form.date_start.trim() : null,
        date_end: form.kind === 'dated' ? form.date_end.trim() : null,
        gain_multiplier: Number(form.gain_multiplier),
        perdu_reduction_percent: Number(form.perdu_reduction_percent),
        unlock_jackpot_all_phases: !!form.unlock_jackpot_all_phases,
        jackpot_odds_during_boost: Number(form.jackpot_odds_during_boost),
        enabled: !!form.enabled,
      };
      if (form.id) {
        await axios.put(`${API}/api/wheel/admin/boost/schedules/${form.id}`, payload,
                        { withCredentials: true });
        toast.success('Schedule mis à jour');
      } else {
        await axios.post(`${API}/api/wheel/admin/boost/schedules`, payload,
                         { withCredentials: true });
        toast.success('Schedule créé — activation auto sous 60s');
      }
      setEditing(null);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur sauvegarde');
    }
  };

  const remove = async (id) => {
    if (!window.confirm("Supprimer ce schedule ? S'il pilote un boost actif, il sera désactivé sous 60s.")) return;
    try {
      await axios.delete(`${API}/api/wheel/admin/boost/schedules/${id}`,
                         { withCredentials: true });
      toast.success('Schedule supprimé');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur suppression');
    }
  };

  return (
    <section className="jp-card p-4" data-testid="wheel-boost-schedules">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2">
          🗓️ Planification automatique des Boosts
        </h3>
        <button onClick={() => setEditing({ ...emptySchedule(), id: null })}
                data-testid="wheel-boost-schedule-add"
                className="jp-btn jp-btn-primary text-xs flex items-center gap-1">
          + Nouveau
        </button>
      </div>

      <p className="text-[12px] mb-3" style={{ color: 'var(--jp-text-muted)' }}>
        Le worker tourne toutes les 60 s. Un schedule actif active le boost
        avec ses paramètres et le désactive automatiquement à la fin de la
        fenêtre. Les boosts manuels ne sont jamais écrasés.
      </p>

      {!items && <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>}
      {items && items.length === 0 && (
        <div className="text-sm py-4 text-center" style={{ color: 'var(--jp-text-muted)' }}
             data-testid="wheel-boost-schedules-empty">
          Aucune planification. Créez-en une pour automatiser les pics DAU.
        </div>
      )}
      {items && items.length > 0 && (
        <div className="space-y-2">
          {items.map((s) => (
            <div key={s.id} className="jp-card p-3 flex items-start gap-3"
                 data-testid={`wheel-boost-schedule-item-${s.id}`}>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-['Outfit'] font-bold text-sm">{s.name}</span>
                  <span className={`text-[9px] uppercase tracking-widest font-bold px-1.5 py-0.5 rounded ${s.enabled ? '' : 'opacity-50'}`}
                        style={{
                          background: s.enabled ? '#10B98122' : 'rgba(0,0,0,0.06)',
                          color: s.enabled ? '#10B981' : 'var(--jp-text-muted)',
                        }}>
                    {s.enabled ? '🟢 Actif' : t('wheel_fortune_admin.desactive')}
                  </span>
                  <span className="text-[9px] uppercase tracking-widest font-semibold px-1.5 py-0.5 rounded"
                        style={{ background: 'rgba(0,0,0,0.06)' }}>
                    {s.kind === 'recurring' ? t('wheel_fortune_admin.recurrent') : t('wheel_fortune_admin.date')}
                  </span>
                </div>
                <div className="text-[12px]" style={{ color: 'var(--jp-text-muted)' }}>
                  {describeSchedule(s)}
                </div>
                <div className="text-[11px] mt-1 flex flex-wrap gap-1">
                  <Tag color="#FFD700">×{s.gain_multiplier} gains</Tag>
                  <Tag color="#10B981">-{s.perdu_reduction_percent}% perdus</Tag>
                  {s.unlock_jackpot_all_phases && <Tag color="#F7931A">Jackpot ouvert ({s.jackpot_odds_during_boost}%)</Tag>}
                </div>
                {s.last_triggered_at && (
                  <div className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                    Dernier déclenchement : {new Date(s.last_triggered_at).toLocaleString('fr-FR')}
                  </div>
                )}
              </div>
              <div className="flex flex-col gap-1">
                <button onClick={() => setEditing({ ...s })}
                        data-testid={`wheel-boost-schedule-edit-${s.id}`}
                        className="jp-btn text-xs">
                  Modifier
                </button>
                <button onClick={() => remove(s.id)}
                        data-testid={`wheel-boost-schedule-delete-${s.id}`}
                        className="jp-btn text-xs"
                        style={{ color: '#E01C2E' }}>
                  Supprimer
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <ScheduleEditModal value={editing} onCancel={() => setEditing(null)} onSave={save} />
      )}
    </section>
  );
}

function Tag({ color, children }) {
  return (
    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full"
          style={{ background: `${color}22`, color }}>
      {children}
    </span>
  );
}

function ScheduleEditModal({ value, onCancel, onSave }) {
  const { t } = useTranslation();
  const [form, setForm] = useState(value);
  const u = (k) => (e) => {
    const v = e?.target?.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.5)' }}
         data-testid="wheel-boost-schedule-modal">
      <div className="jp-card p-4 w-full max-w-lg max-h-[90vh] overflow-y-auto"
           style={{ background: 'var(--jp-bg-card)' }}>
        <h4 className="font-['Outfit'] text-base font-bold mb-3">
          {form.id ? `Modifier #${form.id}` : 'Nouvelle planification'}
        </h4>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="Nom de l'événement" testid="sched-name-field">
            <input value={form.name} onChange={u('name')} maxLength={80}
                   placeholder={t('wheel_fortune_admin.boost_week_end_independance_camerou')}
                   data-testid="sched-name-input"
                   className="jp-input w-full" />
          </Field>
          <Field label="Type" testid="sched-kind-field">
            <select value={form.kind} onChange={u('kind')}
                    data-testid="sched-kind-select"
                    className="jp-input w-full">
              <option value="recurring">{t('wheel_fortune_admin.recurrent_jour_de_semaine')}</option>
              <option value="dated">{t('wheel_fortune_admin.date_one_shot')}</option>
            </select>
          </Field>

          {form.kind === 'recurring' ? (
            <>
              <Field label="Jour de début" testid="sched-dow-start-field">
                <select value={form.dow_start} onChange={u('dow_start')}
                        data-testid="sched-dow-start-select"
                        className="jp-input w-full">
                  {DOW_LABELS.map((l, i) => <option key={i} value={i}>{l}</option>)}
                </select>
              </Field>
              <Field label="Heure début (UTC HH:MM)" testid="sched-time-start-field">
                <input value={form.time_start} onChange={u('time_start')}
                       placeholder="18:00"
                       data-testid="sched-time-start-input"
                       className="jp-input w-full" />
              </Field>
              <Field label="Jour de fin" testid="sched-dow-end-field">
                <select value={form.dow_end} onChange={u('dow_end')}
                        data-testid="sched-dow-end-select"
                        className="jp-input w-full">
                  {DOW_LABELS.map((l, i) => <option key={i} value={i}>{l}</option>)}
                </select>
              </Field>
              <Field label="Heure fin (UTC HH:MM)" testid="sched-time-end-field">
                <input value={form.time_end} onChange={u('time_end')}
                       placeholder="23:00"
                       data-testid="sched-time-end-input"
                       className="jp-input w-full" />
              </Field>
            </>
          ) : (
            <>
              <Field label="Date début (ISO 8601)" testid="sched-date-start-field">
                <input value={form.date_start || ''} onChange={u('date_start')}
                       placeholder="2026-05-01T00:00:00Z"
                       data-testid="sched-date-start-input"
                       className="jp-input w-full" />
              </Field>
              <Field label="Date fin (ISO 8601)" testid="sched-date-end-field">
                <input value={form.date_end || ''} onChange={u('date_end')}
                       placeholder="2026-05-01T23:59:00Z"
                       data-testid="sched-date-end-input"
                       className="jp-input w-full" />
              </Field>
            </>
          )}

          <Field label="Multiplicateur (1.0-5.0)" testid="sched-mult-field">
            <input type="number" step="0.1" min={1} max={5}
                   value={form.gain_multiplier} onChange={u('gain_multiplier')}
                   data-testid="sched-mult-input"
                   className="jp-input w-full" />
          </Field>
          <Field label="Réduction Perdus % (0-95)" testid="sched-perdu-field">
            <input type="number" min={0} max={95} step={5}
                   value={form.perdu_reduction_percent} onChange={u('perdu_reduction_percent')}
                   data-testid="sched-perdu-input"
                   className="jp-input w-full" />
          </Field>
          <Field label="Jackpot toutes phases" testid="sched-jackpot-field">
            <input type="checkbox" checked={!!form.unlock_jackpot_all_phases}
                   onChange={u('unlock_jackpot_all_phases')}
                   data-testid="sched-jackpot-toggle"
                   className="w-5 h-5" />
          </Field>
          <Field label="Probabilité jackpot %" testid="sched-jackpot-odds-field">
            <input type="number" min={0} max={100}
                   value={form.jackpot_odds_during_boost} onChange={u('jackpot_odds_during_boost')}
                   data-testid="sched-jackpot-odds-input"
                   className="jp-input w-full" />
          </Field>
          <Field label="Activé" testid="sched-enabled-field">
            <input type="checkbox" checked={!!form.enabled} onChange={u('enabled')}
                   data-testid="sched-enabled-toggle"
                   className="w-5 h-5" />
          </Field>
        </div>

        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onCancel} className="jp-btn text-sm"
                  data-testid="sched-cancel">
            Annuler
          </button>
          <button onClick={() => onSave(form)} className="jp-btn jp-btn-primary text-sm"
                  data-testid="sched-save">
            Enregistrer
          </button>
        </div>
      </div>
    </div>
  );
}

