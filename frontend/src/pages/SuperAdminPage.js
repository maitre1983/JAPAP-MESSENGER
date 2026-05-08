import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import axios from 'axios';
import { useTranslation } from 'react-i18next';
import {
  Plus, Trash, Key, ShieldCheck, ArrowLeft, Users, Scroll,
  XCircle, CheckCircle, CaretDown, CaretUp, ChartBar,
} from '@phosphor-icons/react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend,
} from 'recharts';
import { useAuth } from '@/context/AuthContext';

const API = process.env.REACT_APP_BACKEND_URL;

const SUB_ROLES = [
  { id: 'content_moderator', label: 'Content Moderator', color: '#8B5CF6' },
  { id: 'wallet_manager',    label: 'Wallet Manager',    color: '#10B981' },
  { id: 'campaign_manager',  label: 'Campaign Manager',  color: '#F59E0B' },
  { id: 'support_agent',     label: 'Support Agent',     color: '#3B82F6' },
  { id: 'wheel_admin',       label: 'Wheel Admin',       color: '#E01C2E' },
];

function formatApiError(detail) {
  if (!detail) return 'Erreur';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(e => e?.msg || JSON.stringify(e)).join(' ');
  return String(detail);
}

const csrf = { headers: { 'X-Requested-With': 'XMLHttpRequest' }, withCredentials: true };

export default function SuperAdminPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const token = (location.pathname.match(/^\/admin(\d{6})/) || [, ''])[1];
  const { user, logout } = useAuth();

  const [tab, setTab] = useState('admins');
  const [admins, setAdmins] = useState([]);
  const [logs, setLogs] = useState([]);
  const [logsTotal, setLogsTotal] = useState(0);
  const [stats, setStats] = useState(null);
  const [statsGranularity, setStatsGranularity] = useState('day');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [resetTarget, setResetTarget] = useState(null);
  const [newPassword, setNewPassword] = useState('');

  // Create form
  const [form, setForm] = useState({
    email: '', password: '', first_name: '', last_name: '',
    sub_roles: [],
  });

  /* Guard: only superadmins can be here */
  useEffect(() => {
    if (!user) return;
    if (user.role !== 'superadmin') navigate(`/admin${token}`, { replace: true });
  }, [user, token, navigate]);

  const flash = (msg, ok = true) => {
    if (ok) { setSuccess(msg); setTimeout(() => setSuccess(''), 3500); }
    else { setError(msg); setTimeout(() => setError(''), 5000); }
  };

  const loadAdmins = async () => {
    try {
      setLoading(true);
      const { data } = await axios.get(`${API}/api/admin/super/admins`, { withCredentials: true });
      setAdmins(data.admins || []);
    } catch (e) {
      flash(formatApiError(e.response?.data?.detail), false);
    } finally { setLoading(false); }
  };

  const loadLogs = async () => {
    try {
      setLoading(true);
      const { data } = await axios.get(`${API}/api/admin/super/audit-log?limit=100`, { withCredentials: true });
      setLogs(data.logs || []);
      setLogsTotal(data.total || 0);
    } catch (e) {
      flash(formatApiError(e.response?.data?.detail), false);
    } finally { setLoading(false); }
  };

  const loadStats = async (granularity = statsGranularity) => {
    try {
      setLoading(true);
      const defaults = { day: 30, month: 12, year: 5 };
      const { data } = await axios.get(
        `${API}/api/admin/super/signup-stats?granularity=${granularity}&limit=${defaults[granularity]}`,
        { withCredentials: true },
      );
      setStats(data);
    } catch (e) {
      flash(formatApiError(e.response?.data?.detail), false);
    } finally { setLoading(false); }
  };

  useEffect(() => {
    if (tab === 'admins') loadAdmins();
    else if (tab === 'audit') loadLogs();
    else if (tab === 'analytics') loadStats(statsGranularity);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, statsGranularity]);

  const toggleSubRole = (sr) => {
    setForm(f => ({
      ...f,
      sub_roles: f.sub_roles.includes(sr)
        ? f.sub_roles.filter(x => x !== sr)
        : [...f.sub_roles, sr],
    }));
  };

  const createAdmin = async (e) => {
    e.preventDefault();
    try {
      setLoading(true);
      await axios.post(`${API}/api/admin/super/admins`, form, csrf);
      flash(`Admin ${form.email} créé avec succès.`);
      setShowCreate(false);
      setForm({ email: '', password: '', first_name: '', last_name: '', sub_roles: [] });
      loadAdmins();
    } catch (e) {
      flash(formatApiError(e.response?.data?.detail), false);
    } finally { setLoading(false); }
  };

  const updateRoles = async (user_id, sub_roles) => {
    try {
      setLoading(true);
      await axios.patch(
        `${API}/api/admin/super/admins/${user_id}/roles`,
        { sub_roles },
        csrf,
      );
      flash('Rôles mis à jour.');
      loadAdmins();
    } catch (e) {
      flash(formatApiError(e.response?.data?.detail), false);
    } finally { setLoading(false); }
  };

  const demoteAdmin = async (user_id, email) => {
    if (!window.confirm(`Retirer les droits admin de ${email} ?`)) return;
    try {
      setLoading(true);
      await axios.delete(`${API}/api/admin/super/admins/${user_id}`, csrf);
      flash(`${email} rétrogradé.`);
      loadAdmins();
    } catch (e) {
      flash(formatApiError(e.response?.data?.detail), false);
    } finally { setLoading(false); }
  };

  const resetPassword = async (e) => {
    e.preventDefault();
    if (!resetTarget || !newPassword) return;
    try {
      setLoading(true);
      await axios.post(
        `${API}/api/admin/super/admins/${resetTarget.user_id}/reset-password`,
        { new_password: newPassword },
        csrf,
      );
      flash(`Mot de passe de ${resetTarget.email} réinitialisé.`);
      setResetTarget(null);
      setNewPassword('');
    } catch (e) {
      flash(formatApiError(e.response?.data?.detail), false);
    } finally { setLoading(false); }
  };

  const doLogout = async () => {
    await logout();
    navigate('/', { replace: true });
  };

  /* ── Render ─────────────────────────────────────────────────────── */
  return (
    <div className="min-h-screen text-white" style={{ background: '#0B0542' }} data-testid="sa-dashboard">
      {/* Header */}
      <header className="px-4 sm:px-6 py-4 flex items-center justify-between border-b"
        style={{ borderColor: 'rgba(255,255,255,0.08)' }}>
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/')} className="opacity-60 hover:opacity-100" data-testid="sa-home">
            <ArrowLeft size={20} />
          </button>
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck size={16} weight="bold" style={{ color: '#FCA5A5' }} />
              <h1 className="text-lg font-['Outfit'] font-extrabold">Superadmin</h1>
            </div>
            <p className="text-[11px] font-['Manrope'] opacity-60">{user?.email}</p>
          </div>
        </div>
        <button
          onClick={doLogout}
          data-testid="sa-logout"
          className="px-3 py-1.5 rounded-full text-xs font-semibold"
          style={{ background: 'rgba(224,28,46,0.15)', color: '#FCA5A5' }}
        >
          Déconnexion
        </button>
      </header>

      {/* Tabs */}
      <nav className="flex gap-1 px-4 sm:px-6 pt-4">
        {[
          { id: 'admins',    label: 'Admins',    Icon: Users },
          { id: 'analytics', label: 'Analytics', Icon: ChartBar },
          { id: 'audit',     label: 'Audit log', Icon: Scroll },
        ].map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            data-testid={`sa-tab-${t.id}`}
            className="flex items-center gap-1.5 px-4 py-2 rounded-t-xl text-sm font-['Manrope'] font-semibold transition-all"
            style={{
              background: tab === t.id ? 'rgba(255,255,255,0.06)' : 'transparent',
              color: tab === t.id ? '#fff' : 'rgba(255,255,255,0.5)',
              borderBottom: tab === t.id ? '2px solid #E01C2E' : '2px solid transparent',
            }}
          >
            <t.Icon size={14} weight="bold" /> {t.label}
          </button>
        ))}
      </nav>

      <main className="px-4 sm:px-6 py-5 space-y-4">
        {/* Flashes */}
        {error && (
          <div className="flex items-start gap-2 text-xs font-['Manrope'] px-3 py-2 rounded-lg"
            data-testid="sa-flash-error"
            style={{ background: 'rgba(224,28,46,0.15)', color: '#FCA5A5' }}>
            <XCircle size={14} weight="bold" className="mt-0.5" /> {error}
          </div>
        )}
        {success && (
          <div className="flex items-start gap-2 text-xs font-['Manrope'] px-3 py-2 rounded-lg"
            data-testid="sa-flash-success"
            style={{ background: 'rgba(16,185,129,0.15)', color: '#6EE7B7' }}>
            <CheckCircle size={14} weight="bold" className="mt-0.5" /> {success}
          </div>
        )}

        {tab === 'admins' && (
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-xs font-['Manrope'] opacity-60">{admins.length} compte(s)</div>
              <button
                onClick={() => setShowCreate(true)}
                data-testid="sa-new-admin"
                className="flex items-center gap-1.5 px-3.5 py-2 rounded-full text-xs font-['Manrope'] font-bold text-white"
                style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}
              >
                <Plus size={14} weight="bold" /> Nouvel admin
              </button>
            </div>

            <div className="space-y-2">
              {admins.map(a => {
                const isMe = a.user_id === user?.user_id;
                const isSuper = a.role === 'superadmin';
                const expandedOpen = expanded === a.user_id;
                return (
                  <div key={a.user_id} className="rounded-xl"
                    data-testid={`sa-admin-row-${a.user_id}`}
                    style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
                    <div className="flex items-center gap-3 p-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-['Manrope'] font-semibold truncate">
                            {a.first_name} {a.last_name}
                          </span>
                          {isSuper && (
                            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                              style={{ background: 'rgba(224,28,46,0.2)', color: '#FCA5A5' }}>
                              SUPER
                            </span>
                          )}
                          {isMe && (
                            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                              style={{ background: 'rgba(255,255,255,0.1)' }}>
                              moi
                            </span>
                          )}
                        </div>
                        <div className="text-xs opacity-60 truncate font-['Manrope']">{a.email}</div>
                        <div className="flex flex-wrap gap-1 mt-1.5">
                          {(a.admin_sub_roles || []).map(sr => {
                            const meta = SUB_ROLES.find(x => x.id === sr);
                            return (
                              <span key={sr} className="text-[10px] px-2 py-0.5 rounded-full font-['Manrope']"
                                style={{ background: `${meta?.color || '#888'}22`, color: meta?.color || '#aaa' }}>
                                {meta?.label || sr}
                              </span>
                            );
                          })}
                          {!isSuper && (!a.admin_sub_roles || a.admin_sub_roles.length === 0) && (
                            <span className="text-[10px] opacity-50 italic">aucun sous-rôle</span>
                          )}
                        </div>
                      </div>
                      {!isSuper && (
                        <button
                          onClick={() => setExpanded(expandedOpen ? null : a.user_id)}
                          data-testid={`sa-admin-expand-${a.user_id}`}
                          className="p-2 opacity-60 hover:opacity-100"
                        >
                          {expandedOpen ? <CaretUp size={14} /> : <CaretDown size={14} />}
                        </button>
                      )}
                    </div>

                    {expandedOpen && !isSuper && (
                      <div className="px-3 pb-3 pt-1 space-y-3 border-t"
                        style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
                        <div>
                          <div className="text-[11px] opacity-60 mb-1.5 font-['Manrope']">Sous-rôles</div>
                          <div className="flex flex-wrap gap-1.5">
                            {SUB_ROLES.map(sr => {
                              const on = (a.admin_sub_roles || []).includes(sr.id);
                              return (
                                <button
                                  key={sr.id}
                                  onClick={() => {
                                    const next = on
                                      ? (a.admin_sub_roles || []).filter(x => x !== sr.id)
                                      : [...(a.admin_sub_roles || []), sr.id];
                                    updateRoles(a.user_id, next);
                                  }}
                                  data-testid={`sa-toggle-${a.user_id}-${sr.id}`}
                                  className="text-[11px] px-2.5 py-1 rounded-full font-['Manrope'] font-semibold transition-all"
                                  style={{
                                    background: on ? sr.color : 'transparent',
                                    color: on ? '#fff' : sr.color,
                                    border: `1px solid ${sr.color}66`,
                                  }}
                                >
                                  {sr.label}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-2 pt-1">
                          <button
                            onClick={() => setResetTarget(a)}
                            data-testid={`sa-reset-pw-${a.user_id}`}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-['Manrope'] font-semibold"
                            style={{ background: 'rgba(255,255,255,0.08)', color: '#fff' }}
                          >
                            <Key size={12} weight="bold" /> Réinitialiser mot de passe
                          </button>
                          <button
                            onClick={() => demoteAdmin(a.user_id, a.email)}
                            data-testid={`sa-demote-${a.user_id}`}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-['Manrope'] font-semibold"
                            style={{ background: 'rgba(224,28,46,0.15)', color: '#FCA5A5' }}
                          >
                            <Trash size={12} weight="bold" /> Rétrograder
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
              {admins.length === 0 && !loading && (
                <div className="text-center text-sm opacity-50 py-12">Aucun admin</div>
              )}
            </div>
          </section>
        )}

        {tab === 'analytics' && (
          <section className="space-y-4" data-testid="sa-analytics">
            {/* KPI cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {[
                { key: 'last_24h', label: '24h' },
                { key: 'last_7d',  label: '7 jours' },
                { key: 'last_30d', label: '30 jours' },
                { key: 'all_time', label: 'Total' },
              ].map(k => {
                const v = stats?.kpis?.[k.key] || { signups: 0, activated: 0, activation_rate: 0 };
                return (
                  <div key={k.key}
                    data-testid={`sa-kpi-${k.key}`}
                    className="rounded-xl p-3 flex flex-col gap-0.5"
                    style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
                    <span className="text-[10px] font-['Manrope'] opacity-60 uppercase tracking-wide">
                      {k.label}
                    </span>
                    <span className="text-2xl font-['Outfit'] font-extrabold">{v.signups.toLocaleString('fr-FR')}</span>
                    <span className="text-[10px] opacity-60 font-['Manrope']">
                      {v.activated} activés · {v.activation_rate}%
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Granularity toggle */}
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <h3 className="text-sm font-['Outfit'] font-bold">Inscriptions dans le temps</h3>
              <div className="inline-flex rounded-full p-1"
                style={{ background: 'rgba(255,255,255,0.06)' }}>
                {[
                  { id: 'day',   label: 'Jour' },
                  { id: 'month', label: 'Mois' },
                  { id: 'year',  label: t('super_admin.annee') },
                ].map(g => (
                  <button
                    key={g.id}
                    onClick={() => setStatsGranularity(g.id)}
                    data-testid={`sa-granularity-${g.id}`}
                    className="px-3.5 py-1.5 rounded-full text-xs font-['Manrope'] font-bold transition-all"
                    style={{
                      background: statsGranularity === g.id
                        ? 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' : 'transparent',
                      color: statsGranularity === g.id ? '#fff' : 'rgba(255,255,255,0.6)',
                    }}
                  >
                    {g.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Chart */}
            <div className="rounded-xl p-3"
              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', height: 320 }}>
              {stats?.series?.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={stats.series} margin={{ top: 16, right: 12, left: -10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="label" tick={{ fill: 'rgba(255,255,255,0.5)', fontSize: 10 }} />
                    <YAxis tick={{ fill: 'rgba(255,255,255,0.5)', fontSize: 10 }} allowDecimals={false} />
                    <Tooltip
                      contentStyle={{
                        background: '#0B0542',
                        border: '1px solid rgba(255,255,255,0.15)',
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                      labelStyle={{ color: '#fff' }}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Bar dataKey="signups" name="Inscrits" fill="#0F056B" radius={[6, 6, 0, 0]} />
                    <Bar dataKey="activated" name="Activés (OTP)" fill="#E01C2E" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-xs opacity-50">
                  {loading ? 'Chargement…' : t('super_admin.aucune_donnee')}
                </div>
              )}
            </div>

            {/* Detail table */}
            <div className="rounded-xl overflow-hidden"
              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <table className="w-full text-xs font-['Manrope']" data-testid="sa-analytics-table">
                <thead>
                  <tr style={{ background: 'rgba(255,255,255,0.04)' }}>
                    <th className="text-left px-3 py-2 font-semibold">Période</th>
                    <th className="text-right px-3 py-2 font-semibold">Inscrits</th>
                    <th className="text-right px-3 py-2 font-semibold">Activés</th>
                    <th className="text-right px-3 py-2 font-semibold">Taux</th>
                  </tr>
                </thead>
                <tbody>
                  {[...(stats?.series || [])].reverse().map(row => {
                    const rate = row.signups ? Math.round((row.activated * 1000) / row.signups) / 10 : 0;
                    return (
                      <tr key={row.bucket} className="border-t"
                        style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
                        <td className="px-3 py-2 opacity-80">{row.label}</td>
                        <td className="px-3 py-2 text-right font-semibold">{row.signups.toLocaleString('fr-FR')}</td>
                        <td className="px-3 py-2 text-right opacity-80">{row.activated.toLocaleString('fr-FR')}</td>
                        <td className="px-3 py-2 text-right"
                          style={{ color: rate >= 50 ? '#6EE7B7' : rate >= 20 ? '#FCD34D' : '#FCA5A5' }}>
                          {rate}%
                        </td>
                      </tr>
                    );
                  })}
                  {!stats?.series?.length && (
                    <tr><td colSpan={4} className="text-center py-10 opacity-50">—</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {tab === 'audit' && (
          <section className="space-y-2">
            <div className="text-xs opacity-60 font-['Manrope']">{logsTotal} actions enregistrées</div>
            <div className="rounded-xl overflow-hidden"
              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <table className="w-full text-xs font-['Manrope']" data-testid="sa-audit-table">
                <thead>
                  <tr style={{ background: 'rgba(255,255,255,0.04)' }}>
                    <th className="text-left px-3 py-2 font-semibold">Quand</th>
                    <th className="text-left px-3 py-2 font-semibold">Acteur</th>
                    <th className="text-left px-3 py-2 font-semibold">Action</th>
                    <th className="text-left px-3 py-2 font-semibold">Cible</th>
                    <th className="text-left px-3 py-2 font-semibold hidden sm:table-cell">IP</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map(l => (
                    <tr key={l.id} className="border-t" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
                      <td className="px-3 py-2 opacity-70">{new Date(l.created_at).toLocaleString()}</td>
                      <td className="px-3 py-2 truncate max-w-[160px]">{l.actor_email}</td>
                      <td className="px-3 py-2 font-semibold">{l.action}</td>
                      <td className="px-3 py-2 opacity-70 truncate max-w-[180px]">{l.target_email || '—'}</td>
                      <td className="px-3 py-2 opacity-50 hidden sm:table-cell">{l.ip_address || ''}</td>
                    </tr>
                  ))}
                  {logs.length === 0 && !loading && (
                    <tr><td colSpan={5} className="text-center py-10 opacity-50">Aucun enregistrement</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </main>

      {/* Create admin modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(6px)' }}
          onClick={() => setShowCreate(false)}>
          <form
            onSubmit={createAdmin}
            onClick={e => e.stopPropagation()}
            className="w-full max-w-md rounded-2xl p-6 space-y-3"
            data-testid="sa-create-modal"
            style={{ background: '#120A58', border: '1px solid rgba(255,255,255,0.1)' }}
          >
            <h3 className="text-lg font-['Outfit'] font-bold">Nouveau compte admin</h3>
            <div className="grid grid-cols-2 gap-2">
              <input required placeholder={t('super_admin.prenom')} value={form.first_name}
                onChange={e => setForm({ ...form, first_name: e.target.value })}
                data-testid="sa-create-first"
                className="px-3 py-2 rounded-lg text-sm outline-none text-white"
                style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.15)' }} />
              <input required placeholder={t('super_admin.nom')} value={form.last_name}
                onChange={e => setForm({ ...form, last_name: e.target.value })}
                data-testid="sa-create-last"
                className="px-3 py-2 rounded-lg text-sm outline-none text-white"
                style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.15)' }} />
            </div>
            <input required type="email" placeholder={t('super_admin.email')} value={form.email}
              onChange={e => setForm({ ...form, email: e.target.value })}
              data-testid="sa-create-email"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none text-white"
              style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.15)' }} />
            <input required type="password" placeholder={t('super_admin.mot_de_passe_8_car')}
              minLength={8}
              value={form.password}
              onChange={e => setForm({ ...form, password: e.target.value })}
              data-testid="sa-create-password"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none text-white"
              style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.15)' }} />
            <div>
              <div className="text-[11px] opacity-60 mb-1.5">Sous-rôles (optionnel)</div>
              <div className="flex flex-wrap gap-1.5">
                {SUB_ROLES.map(sr => {
                  const on = form.sub_roles.includes(sr.id);
                  return (
                    <button
                      key={sr.id}
                      type="button"
                      onClick={() => toggleSubRole(sr.id)}
                      data-testid={`sa-create-sr-${sr.id}`}
                      className="text-[11px] px-2.5 py-1 rounded-full font-semibold"
                      style={{
                        background: on ? sr.color : 'transparent',
                        color: on ? '#fff' : sr.color,
                        border: `1px solid ${sr.color}66`,
                      }}
                    >
                      {sr.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="flex gap-2 pt-2">
              <button type="button" onClick={() => setShowCreate(false)}
                className="flex-1 py-2.5 rounded-lg text-sm font-semibold"
                style={{ background: 'rgba(255,255,255,0.08)' }}>
                Annuler
              </button>
              <button type="submit" disabled={loading}
                data-testid="sa-create-submit"
                className="flex-1 py-2.5 rounded-lg text-sm font-bold"
                style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}>
                {loading ? t('super_admin.creation') : t('super_admin.creer')}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Reset password modal */}
      {resetTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(6px)' }}
          onClick={() => { setResetTarget(null); setNewPassword(''); }}>
          <form
            onSubmit={resetPassword}
            onClick={e => e.stopPropagation()}
            className="w-full max-w-md rounded-2xl p-6 space-y-3"
            data-testid="sa-reset-modal"
            style={{ background: '#120A58', border: '1px solid rgba(255,255,255,0.1)' }}
          >
            <h3 className="text-lg font-['Outfit'] font-bold">Réinitialiser le mot de passe</h3>
            <p className="text-xs opacity-60">
              Utilisateur : <strong>{resetTarget.email}</strong><br />
              La prochaine connexion exigera un changement de mot de passe.
            </p>
            <input required type="password" minLength={8}
              placeholder={t('super_admin.nouveau_mot_de_passe_8_car')}
              value={newPassword}
              onChange={e => setNewPassword(e.target.value)}
              data-testid="sa-reset-input"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none text-white"
              style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.15)' }} />
            <div className="flex gap-2">
              <button type="button" onClick={() => { setResetTarget(null); setNewPassword(''); }}
                className="flex-1 py-2.5 rounded-lg text-sm font-semibold"
                style={{ background: 'rgba(255,255,255,0.08)' }}>{t('super_admin.annuler')}</button>
              <button type="submit" disabled={loading}
                data-testid="sa-reset-submit"
                className="flex-1 py-2.5 rounded-lg text-sm font-bold"
                style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}>
                {loading ? 'Traitement…' : 'Confirmer'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
