import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Users, CurrencyDollar, ShieldCheck, MagnifyingGlass, Check, X, ChartBar,
  Gear, GameController, IdentificationCard, Download, PencilSimple,
  LockKey, Prohibit, Receipt, Warning, Crown, Gift, Confetti, WifiHigh as Wifi,
  Megaphone, Bank, EnvelopeOpen, Coins, Target, Headset, Car, Bug,
} from '@phosphor-icons/react';
import AdsAdminTab from '@/pages/admin/AdsAdminTab';
import AdminErrorMonitorTab from '@/pages/admin/AdminErrorMonitorTab';
import PaymentsAdminTab from '@/pages/admin/PaymentsAdminTab';
import PaymentHealthAdminTab from '@/pages/admin/PaymentHealthAdminTab';
import StatsAdminTab from '@/pages/admin/StatsAdminTab';
import MessagingAdminTab from '@/pages/admin/MessagingAdminTab';
import StakingAdminTab from '@/pages/admin/StakingAdminTab';
import WheelFortuneAdminTab from '@/pages/admin/WheelFortuneAdminTab';
import GamesEngagementAdminTab from '@/pages/admin/GamesEngagementAdminTab';
import RecruitAdminTab from '@/pages/admin/RecruitAdminTab';
import WalletOverviewAdminTab from '@/pages/admin/WalletOverviewAdminTab';
import SupportAdminTab from '@/pages/admin/SupportAdminTab';
import RevenueAdminTab from '@/pages/admin/RevenueAdminTab';
import TransportAdminTab from '@/pages/admin/TransportAdminTab';
import QuizChampionAdminTab from '@/pages/admin/QuizChampionAdminTab';
import MigrationBroadcastAdminTab from '@/pages/admin/MigrationBroadcastAdminTab';
import ZoomableImage from '@/components/media/ZoomableImage';
import UsersByBalanceTab from '@/pages/admin/UsersByBalanceTab';
import ReferralTiersEditor from '@/components/admin/referrals/ReferralTiersEditor';
import CrowdfundingAdminProjectsTab from '@/components/crowdfunding/CrowdfundingAdminProjectsTab';
import { AdminProjectActions } from '@/pages/CrowdfundingModule';

const API = process.env.REACT_APP_BACKEND_URL;

export default function AdminPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState('dashboard');
  const [stats, setStats] = useState(null);
  const [kycPending, setKycPending] = useState(0);
  const [walletUnread, setWalletUnread] = useState(0);
  const [supportOpen, setSupportOpen] = useState(0);
  const [disputeCount, setDisputeCount] = useState(0);  // iter177
  const [message, setMessage] = useState('');

  const loadStats = useCallback(async () => {
    try {
      const [s, k, wa, st, dis] = await Promise.all([
        axios.get(`${API}/api/admin/stats`, { withCredentials: true }),
        axios.get(`${API}/api/admin/kyc/pending-count`, { withCredentials: true }),
        axios.get(`${API}/api/admin/wallet/alerts?unread_only=true&limit=1`, { withCredentials: true }).catch(() => ({ data: {} })),
        axios.get(`${API}/api/support/admin/tickets?status=open&limit=1`, { withCredentials: true }).catch(() => ({ data: {} })),
        axios.get(`${API}/api/marketplace/admin/orders/disputes?limit=1`, { withCredentials: true }).catch(() => ({ data: {} })),
      ]);
      setStats(s.data); setKycPending(k.data?.pending || 0);
      setWalletUnread(wa.data?.unread_count || 0);
      setSupportOpen(st.data?.total || 0);
      setDisputeCount(dis.data?.total || 0);
    } catch {}
  }, []);

  useEffect(() => {
    loadStats();
    const t = setInterval(loadStats, 30000); // refresh every 30s
    return () => clearInterval(t);
  }, [loadStats]);

  const tabs = [
    { id: 'dashboard', label: 'Dashboard', icon: ChartBar },
    { id: 'users', label: 'Utilisateurs', icon: Users },
    { id: 'transactions', label: 'Transactions', icon: CurrencyDollar },
    { id: 'wallet-overview', label: 'Wallet Overview', icon: Bank, badge: walletUnread },
    { id: 'revenue', label: 'Revenus', icon: CurrencyDollar },
    { id: 'support', label: 'Support', icon: Headset, badge: supportOpen },
    { id: 'kyc', label: `KYC${kycPending > 0 ? ` (${kycPending})` : ''}`, icon: IdentificationCard },
    { id: 'wheel', label: 'Roue Fortune', icon: Target },
    { id: 'engagement', label: 'Quiz & Tap', icon: GameController },
    { id: 'recruit', label: 'Recruteurs (Viral)', icon: Confetti },
    { id: 'crowdfunding', label: 'Crowdfunding', icon: Gift },
    { id: 'quiz-champion', label: 'Quiz Champion', icon: Crown },
    { id: 'spin', label: 'Mini-spin XAF', icon: GameController },
    { id: 'pro', label: 'JAPAP PRO', icon: Crown },
    { id: 'referrals', label: 'Parrainage', icon: Gift },
    { id: 'connect', label: 'Connect', icon: Wifi },
    { id: 'ads', label: 'Publicités', icon: Megaphone },
    { id: 'transport', label: 'Transport JAPAP', icon: Car },
    { id: 'payments', label: 'Paiements', icon: Bank },
    { id: 'mkt-disputes', label: 'Litiges Marketplace', icon: ShieldCheck, badge: disputeCount },
    { id: 'payment-health', label: 'Payment Health', icon: Bank },
    { id: 'messaging', label: 'Messaging', icon: EnvelopeOpen },
    { id: 'migration-broadcast', label: 'Broadcast Legacy', icon: Megaphone },
    { id: 'staking', label: 'Staking', icon: Coins },
    { id: 'stats', label: 'Statistiques', icon: ChartBar },
    { id: 'settings', label: 'Paramètres', icon: Gear },
    { id: 'errors', label: 'Erreurs IA', icon: Bug },
    { id: 'audit', label: 'Audit', icon: ShieldCheck },
  ];

  return (
    <div className="p-6 max-w-7xl mx-auto jp-animate-fadeIn" data-testid="admin-page">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-['Outfit'] text-2xl font-bold" style={{ color: 'var(--jp-text)' }}>Administration</h1>
          <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>Centre de commandement JAPAP</p>
        </div>
      </div>

      <div className="jp-tabs mb-6 flex flex-wrap">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} data-testid={`admin-tab-${t.id}`}
            className={`jp-tab ${tab === t.id ? 'jp-tab-active' : ''}`}
            style={{ position: 'relative' }}>
            <t.icon size={14} weight="bold" className="inline mr-1.5" style={{ marginBottom: '1px' }} />
            {t.label}
            {t.badge > 0 && (
              <span
                data-testid={`admin-tab-${t.id}-badge`}
                style={{
                  position: 'absolute', top: -6, right: -6,
                  minWidth: 18, height: 18, padding: '0 5px',
                  borderRadius: 9, background: '#E01C2E', color: 'white',
                  fontSize: 10, fontWeight: 800, display: 'flex',
                  alignItems: 'center', justifyContent: 'center',
                  boxShadow: '0 2px 6px rgba(224,28,46,.45)',
                  lineHeight: 1,
                }}
              >
                {t.badge > 99 ? '99+' : t.badge}
              </span>
            )}
          </button>
        ))}
      </div>

      {message && <div className="jp-alert jp-alert-info mb-4" data-testid="admin-message">{message}</div>}

      {tab === 'dashboard' && <DashboardTab stats={stats} kycPending={kycPending} />}
      {tab === 'users' && <UsersTab onAction={loadStats} setMessage={setMessage} />}
      {tab === 'transactions' && <TransactionsTab />}
      {tab === 'wallet-overview' && <WalletOverviewAdminTab />}
      {tab === 'revenue' && <RevenueAdminTab />}
      {tab === 'support' && <SupportAdminTab onAction={loadStats} />}
      {tab === 'kyc' && <KycTab onAction={loadStats} setMessage={setMessage} />}
      {tab === 'wheel' && <WheelFortuneAdminTab />}
      {tab === 'engagement' && <GamesEngagementAdminTab />}
      {tab === 'recruit' && <RecruitAdminTab />}
      {tab === 'crowdfunding' && (
        <div className="space-y-4" data-testid="admin-tab-crowdfunding-content">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-bold font-['Outfit']" style={{ color: 'var(--jp-text)' }}>
              {t('crowdfunding.admin_projects_page_title', { defaultValue: 'Crowdfunding — Modération des projets' })}
            </h2>
            <a
              href="/services?view=crowdfunding"
              data-testid="admin-tab-crowdfunding-open-module"
              className="text-xs font-semibold text-rose-600 hover:underline">
              {t('crowdfunding.admin_open_full_module', { defaultValue: 'Ouvrir le module complet ↗' })}
            </a>
          </div>
          <CrowdfundingAdminProjectsTab ActionsComponent={AdminProjectActions} />
        </div>
      )}
      {tab === 'spin' && <SpinAdminTab setMessage={setMessage} />}
      {tab === 'pro' && <ProAdminTab setMessage={setMessage} />}
      {tab === 'referrals' && <ReferralsAdminTab />}
      {tab === 'connect' && <ConnectAdminTab />}
      {tab === 'ads' && <AdsAdminTab />}
      {tab === 'transport' && <TransportAdminTab onAction={loadStats} />}
      {tab === 'payments' && <PaymentsAdminTab />}
      {tab === 'mkt-disputes' && <MarketplaceDisputesAdminTab onAction={loadStats} setMessage={setMessage} />}
      {tab === 'payment-health' && <PaymentHealthAdminTab />}
      {tab === 'quiz-champion' && <QuizChampionAdminTab />}
      {tab === 'messaging' && <MessagingAdminTab />}
      {tab === 'migration-broadcast' && <MigrationBroadcastAdminTab />}
      {tab === 'staking' && <StakingAdminTab />}
      {tab === 'stats' && <StatsAdminTab />}
      {tab === 'settings' && <SettingsTab setMessage={setMessage} />}
      {tab === 'errors' && <AdminErrorMonitorTab />}
      {tab === 'audit' && <AuditTab />}

      {/* iter178 — Sticky admin footer revenue widget (CEO request) */}
      <AdminFooterRevenueWidget />
    </div>
  );
}

/* ============== DASHBOARD ============== */
function DashboardTab({ stats, kycPending }) {
  if (!stats) return <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;
  const cards = [
    { label: 'Utilisateurs', value: stats.total_users, icon: Users, bg: 'var(--jp-primary-subtle)', color: 'var(--jp-primary)' },
    { label: 'En ligne', value: stats.online_users, icon: Users, bg: 'var(--jp-success-light)', color: 'var(--jp-success)' },
    { label: 'Transactions', value: stats.total_transactions, icon: Receipt, bg: 'var(--jp-secondary-subtle)', color: 'var(--jp-secondary)' },
    { label: 'TX en attente', value: stats.pending_transactions, icon: Warning, bg: 'var(--jp-warning-light)', color: '#9A6700' },
    { label: 'Utilisateurs Pro', value: stats.pro_users, icon: ShieldCheck, bg: 'var(--jp-secondary-subtle)', color: 'var(--jp-secondary)' },
    { label: 'KYC en attente', value: kycPending, icon: IdentificationCard, bg: '#FEF3C7', color: '#B45309' },
    { label: 'Solde total', value: `${parseFloat(stats.total_balance).toLocaleString('fr-FR')} XAF`, icon: CurrencyDollar, bg: 'var(--jp-primary-subtle)', color: 'var(--jp-primary)' },
    { label: 'Parties jouées', value: stats.gaming?.total_plays || 0, icon: GameController, bg: '#F0FDF4', color: '#10B981' },
  ];
  return (
    <div data-testid="admin-dashboard">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 jp-stagger mb-6">
        {cards.map((s, i) => (
          <div key={i} className="jp-stat-card jp-card-hover" data-testid={`stat-${s.label.toLowerCase().replace(/\s/g, '-')}`}>
            <div className="jp-stat-icon" style={{ background: s.bg }}>
              <s.icon size={18} weight="duotone" style={{ color: s.color }} />
            </div>
            <div className="jp-stat-label">{s.label}</div>
            <div className="jp-stat-value">{s.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============== USERS ============== */
function UsersTab({ onAction, setMessage }) {
  const { t } = useTranslation();
  // iter154 — sub-tab switcher: "Tous les utilisateurs" / "Par solde".
  const [subTab, setSubTab] = useState('all');
  const [users, setUsers] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [editUser, setEditUser] = useState(null);
  const [suspendUser, setSuspendUser] = useState(null);

  const load = useCallback(async () => {
    try {
      const url = search ? `${API}/api/admin/users?page=${page}&limit=20&search=${search}` : `${API}/api/admin/users?page=${page}&limit=20`;
      const { data } = await axios.get(url, { withCredentials: true });
      setUsers(data.users); setTotal(data.total);
    } catch {}
  }, [page, search]);
  useEffect(() => { if (subTab === 'all') load(); }, [load, subTab]);

  const subTabs = [
    { id: 'all', label: 'Tous les utilisateurs' },
    { id: 'by-balance', label: 'Par solde' },
  ];

  return (
    <div className="space-y-4">
      <div className="flex gap-2 border-b" style={{ borderColor: 'var(--jp-border)' }}>
        {subTabs.map(t => (
          <button key={t.id} onClick={() => setSubTab(t.id)}
            data-testid={`users-subtab-${t.id}`}
            className={`px-4 py-2 text-sm font-semibold font-['Manrope'] border-b-2 -mb-[2px] transition-colors`}
            style={{
              borderColor: subTab === t.id ? 'var(--jp-primary)' : 'transparent',
              color: subTab === t.id ? 'var(--jp-primary)' : 'var(--jp-text-muted)',
            }}>
            {t.label}
          </button>
        ))}
      </div>

      {subTab === 'by-balance' && <UsersByBalanceTab />}
      {subTab === 'all' && (
    <div className="jp-card jp-animate-fadeIn">
      <div className="p-4 border-b flex items-center gap-3" style={{ borderColor: 'var(--jp-border)' }}>
        <div className="relative flex-1">
          <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2" size={16} style={{ color: 'var(--jp-text-muted)' }} />
          <input data-testid="admin-user-search" value={search} onChange={e => { setSearch(e.target.value); setPage(1); }}
            className="jp-input text-sm" style={{ paddingLeft: '36px' }} placeholder="Rechercher email, nom, username..." />
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="jp-table">
          <thead>
            <tr><th>{t('admin.utilisateur')}</th><th>{t('admin.email_phone')}</th><th>Role</th><th>{t('admin.solde')}</th><th>{t('admin.statut')}</th><th>{t('admin.actions')}</th></tr>
          </thead>
          <tbody>
            {users.map(u => (
              <tr key={u.user_id} data-testid={`admin-user-${u.user_id}`}>
                <td><div className="flex items-center gap-2">
                  <div className="jp-avatar jp-avatar-sm jp-avatar-primary">{(u.first_name?.[0] || '?').toUpperCase()}</div>
                  <div><div className="font-medium">{u.first_name} {u.last_name}</div>
                    <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>@{u.username}</div></div>
                </div></td>
                <td style={{ color: 'var(--jp-text-secondary)' }}>
                  <div>{u.email}</div>
                  {u.phone_number && <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{u.phone_number}</div>}
                </td>
                <td><span className={`jp-badge ${u.role === 'admin' ? 'jp-badge-primary' : 'jp-badge-neutral'}`}>{u.role}</span></td>
                <td><span className="font-['Outfit'] font-medium">{parseFloat(u.wallet_balance || 0).toLocaleString('fr-FR')} {u.wallet_currency || 'XAF'}</span></td>
                <td><span className={`jp-badge ${u.is_active ? 'jp-badge-success' : 'jp-badge-error'}`}>{u.is_active ? 'Actif' : 'Suspendu'}</span></td>
                <td>
                  <div className="flex items-center gap-1">
                    <button onClick={() => setEditUser(u)} data-testid={`edit-user-${u.user_id}`}
                      className="jp-btn jp-btn-sm jp-btn-icon" style={{ padding: 4, background: 'transparent', color: 'var(--jp-primary)' }} title="Modifier">
                      <PencilSimple size={14} />
                    </button>
                    <button onClick={() => setEditUser(u)} data-testid={`reset-user-${u.user_id}`}
                      className="jp-btn jp-btn-sm jp-btn-icon" style={{ padding: 4, background: 'transparent', color: 'var(--jp-secondary)' }} title="Reset mot de passe">
                      <LockKey size={14} />
                    </button>
                    {u.is_active ? (
                      <button onClick={() => setSuspendUser(u)} data-testid={`suspend-user-${u.user_id}`}
                        className="jp-btn jp-btn-sm jp-btn-icon" style={{ padding: 4, background: 'transparent', color: 'var(--jp-error)' }} title="Suspendre">
                        <Prohibit size={14} />
                      </button>
                    ) : (
                      <button onClick={async () => {
                        await axios.post(`${API}/api/admin/users/${u.user_id}/reactivate`, {}, { withCredentials: true });
                        toast.success('Utilisateur réactivé'); load(); onAction && onAction();
                      }} data-testid={`reactivate-user-${u.user_id}`}
                        className="jp-btn jp-btn-sm jp-btn-icon" style={{ padding: 4, background: 'transparent', color: 'var(--jp-success)' }} title="Réactiver">
                        <Check size={14} />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > 20 && (
        <div className="flex justify-center gap-2 p-4 border-t" style={{ borderColor: 'var(--jp-border)' }}>
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="users-prev">Préc.</button>
          <span className="px-4 py-1.5 text-sm" style={{ color: 'var(--jp-text-secondary)' }}>Page {page} / {Math.ceil(total / 20)}</span>
          <button disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="users-next">Suiv.</button>
        </div>
      )}

      {editUser && <EditUserModal user={editUser} onClose={() => setEditUser(null)} onSaved={() => { load(); setEditUser(null); toast.success('Profil modifié'); }} />}
      {suspendUser && <SuspendUserModal user={suspendUser} onClose={() => setSuspendUser(null)} onSaved={() => { load(); setSuspendUser(null); toast.success('Utilisateur suspendu'); onAction && onAction(); }} />}
    </div>
      )}
    </div>
  );
}

function EditUserModal({ user, onClose, onSaved }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    email: user.email || '', phone_number: user.phone_number || '',
    username: user.username || '', first_name: user.first_name || '', last_name: user.last_name || '',
  });
  const [err, setErr] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);

  // Credit section
  const [creditAmount, setCreditAmount] = useState('');
  const [creditCurrency, setCreditCurrency] = useState(user.wallet_currency || 'XAF');
  const [creditNote, setCreditNote] = useState('');
  const [creditConfirming, setCreditConfirming] = useState(false);
  const [creditBusy, setCreditBusy] = useState(false);
  const [balance, setBalance] = useState(user.wallet_balance);

  // Password section
  const [pwd, setPwd] = useState('');
  const [pwd2, setPwd2] = useState('');
  const [pwdConfirming, setPwdConfirming] = useState(false);
  const [pwdBusy, setPwdBusy] = useState(false);

  const saveProfile = async (e) => {
    e.preventDefault(); setErr(''); setSavingProfile(true);
    try {
      await axios.put(`${API}/api/admin/users/${user.user_id}/profile`, form, { withCredentials: true });
      onSaved();
    } catch (e) { setErr(e.response?.data?.detail || 'Erreur'); }
    finally { setSavingProfile(false); }
  };

  const runCredit = async () => {
    const n = parseFloat(creditAmount);
    if (!n || Number.isNaN(n) || n === 0) return toast.error('Montant invalide.');
    setCreditBusy(true);
    try {
      // Ensure the wallet currency matches so we never accidentally
      // credit XAF into a USD balance.
      if ((user.wallet_currency || 'XAF') !== creditCurrency) {
        const ok = window.confirm(
          `⚠ La devise du portefeuille (${user.wallet_currency || 'XAF'}) diffère de ${creditCurrency}. Confirmer tout de même ?`,
        );
        if (!ok) { setCreditBusy(false); return; }
      }
      const { data } = await axios.post(`${API}/api/admin/wallet/adjust`,
        { user_id: user.user_id, amount: n, notes: creditNote || `Crédit admin (${creditCurrency})` },
        { withCredentials: true });
      setBalance(data.new_balance);
      setCreditAmount(''); setCreditNote(''); setCreditConfirming(false);
      toast.success(`Solde mis à jour : ${parseFloat(data.new_balance).toLocaleString('fr-FR')} ${user.wallet_currency || 'XAF'}`);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Crédit impossible.');
    } finally { setCreditBusy(false); }
  };

  const runReset = async () => {
    if (pwd.length < 8) return toast.error('Minimum 8 caractères.');
    if (pwd !== pwd2) return toast.error('Les deux champs ne correspondent pas.');
    setPwdBusy(true);
    try {
      await axios.post(`${API}/api/admin/users/${user.user_id}/reset-password`,
        { new_password: pwd }, { withCredentials: true });
      setPwd(''); setPwd2(''); setPwdConfirming(false);
      toast.success('Mot de passe modifié. L\'utilisateur a été déconnecté de toutes ses sessions.');
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur.');
    } finally { setPwdBusy(false); }
  };

  return (
    <ModalShell title={`Modifier — ${user.email}`} onClose={onClose}>
      <div className="space-y-5" data-testid="edit-user-modal">
        {/* ───── Section: Profil ───── */}
        <form onSubmit={saveProfile} className="space-y-2" data-testid="edit-user-form">
          <p className="text-[10px] uppercase tracking-wider font-bold"
             style={{ color: 'var(--jp-text-muted)' }}>{t('admin.profil')}</p>
          {['email', 'phone_number', 'username', 'first_name', 'last_name'].map(k => (
            <div key={k}>
              <label className="jp-label capitalize text-xs">{k.replace('_', ' ')}</label>
              <input className="jp-input text-sm" value={form[k]}
                     onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))}
                     data-testid={`edit-${k}`} />
            </div>
          ))}
          {err && <div className="jp-alert jp-alert-error text-xs">{err}</div>}
          <button type="submit" disabled={savingProfile}
                  className="jp-btn jp-btn-primary w-full" data-testid="edit-user-submit">
            {savingProfile ? 'Enregistrement…' : 'Enregistrer le profil'}
          </button>
        </form>

        <hr style={{ borderColor: 'var(--jp-border)' }} />

        {/* ───── Section: Créditer le compte ───── */}
        <div className="space-y-2" data-testid="admin-credit-section">
          <div className="flex items-center justify-between">
            <p className="text-[10px] uppercase tracking-wider font-bold"
               style={{ color: 'var(--jp-text-muted)' }}>{t('admin.crediter_le_compte')}</p>
            <span className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
              Solde actuel : <strong style={{ color: 'var(--jp-text)' }}
                data-testid="admin-credit-current-balance">
                {parseFloat(balance || 0).toLocaleString('fr-FR')} {user.wallet_currency || 'XAF'}
              </strong>
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="jp-label text-xs">Montant</label>
              <input type="number" step="0.01" className="jp-input text-sm"
                     placeholder="10.00 (négatif pour débiter)"
                     value={creditAmount}
                     onChange={e => setCreditAmount(e.target.value)}
                     data-testid="admin-credit-amount" />
            </div>
            <div>
              <label className="jp-label text-xs">Devise</label>
              <select className="jp-input text-sm"
                      value={creditCurrency}
                      onChange={e => setCreditCurrency(e.target.value)}
                      data-testid="admin-credit-currency">
                {['XAF', 'XOF', 'USD', 'EUR', 'NGN', 'GHS', 'KES', 'ZAR'].map(c =>
                  <option key={c} value={c}>{c}</option>,
                )}
              </select>
            </div>
          </div>
          <input className="jp-input text-sm" placeholder="Note (optionnel — tracée dans l'audit)"
                 value={creditNote} maxLength={240}
                 onChange={e => setCreditNote(e.target.value)}
                 data-testid="admin-credit-note" />
          {!creditConfirming ? (
            <button type="button" onClick={() => setCreditConfirming(true)}
                    disabled={!creditAmount}
                    className="jp-btn w-full"
                    style={{ background: 'var(--jp-success)', color: 'white' }}
                    data-testid="admin-credit-btn">
              Créditer le compte
            </button>
          ) : (
            <div className="p-3 rounded-xl space-y-2"
                 style={{ background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.3)' }}
                 data-testid="admin-credit-confirm">
              <p className="text-xs font-semibold" style={{ color: '#047857' }}>
                ⚠ Confirmer le {parseFloat(creditAmount) < 0 ? 'débit' : 'crédit'} de{' '}
                <strong>{Math.abs(parseFloat(creditAmount) || 0).toLocaleString('fr-FR')} {creditCurrency}</strong>{' '}
                sur le compte de <strong>{user.email}</strong>.
                Cette action est tracée et irréversible.
              </p>
              <div className="flex gap-2">
                <button type="button" onClick={() => setCreditConfirming(false)} disabled={creditBusy}
                        className="jp-btn jp-btn-ghost flex-1"
                        data-testid="admin-credit-cancel">Annuler</button>
                <button type="button" onClick={runCredit} disabled={creditBusy}
                        className="jp-btn flex-1"
                        style={{ background: 'var(--jp-success)', color: 'white' }}
                        data-testid="admin-credit-confirm-btn">
                  {creditBusy ? 'Crédit en cours…' : 'Confirmer'}
                </button>
              </div>
            </div>
          )}
        </div>

        <hr style={{ borderColor: 'var(--jp-border)' }} />

        {/* ───── Section: Réinitialiser mot de passe ───── */}
        <div className="space-y-2" data-testid="admin-reset-pwd-section">
          <p className="text-[10px] uppercase tracking-wider font-bold"
             style={{ color: 'var(--jp-text-muted)' }}>{t('admin.reinitialiser_mot_de_passe')}</p>
          <input type="password" minLength={8} className="jp-input text-sm"
                 placeholder={t('admin.nouveau_mot_de_passe_min_8_car')}
                 value={pwd} onChange={e => setPwd(e.target.value)}
                 data-testid="admin-reset-pwd-new" />
          <input type="password" minLength={8} className="jp-input text-sm"
                 placeholder={t('admin.confirmer_le_mot_de_passe')}
                 value={pwd2} onChange={e => setPwd2(e.target.value)}
                 data-testid="admin-reset-pwd-confirm" />
          {pwd && pwd2 && pwd !== pwd2 && (
            <p className="text-[11px]" style={{ color: 'var(--jp-error)' }}>
              Les deux champs ne correspondent pas.
            </p>
          )}
          <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
            L'utilisateur sera automatiquement <strong>déconnecté de toutes ses sessions</strong> et devra se reconnecter.
          </p>
          {!pwdConfirming ? (
            <button type="button"
                    onClick={() => setPwdConfirming(true)}
                    disabled={!pwd || pwd !== pwd2 || pwd.length < 8}
                    className="jp-btn w-full"
                    style={{ background: 'var(--jp-warning, #F59E0B)', color: 'white' }}
                    data-testid="admin-reset-pwd-btn">
              Réinitialiser le mot de passe
            </button>
          ) : (
            <div className="p-3 rounded-xl space-y-2"
                 style={{ background: 'rgba(245,158,11,0.10)', border: '1px solid rgba(245,158,11,0.3)' }}
                 data-testid="admin-reset-pwd-confirm-step">
              <p className="text-xs font-semibold" style={{ color: '#92400E' }}>
                ⚠ Le mot de passe actuel sera définitivement remplacé et toutes les sessions de {user.email} seront invalidées.
              </p>
              <div className="flex gap-2">
                <button type="button" onClick={() => setPwdConfirming(false)} disabled={pwdBusy}
                        className="jp-btn jp-btn-ghost flex-1"
                        data-testid="admin-reset-pwd-cancel">Annuler</button>
                <button type="button" onClick={runReset} disabled={pwdBusy}
                        className="jp-btn flex-1"
                        style={{ background: 'var(--jp-warning, #F59E0B)', color: 'white' }}
                        data-testid="admin-reset-pwd-confirm-btn">
                  {pwdBusy ? 'En cours…' : 'Confirmer'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </ModalShell>
  );
}

function SuspendUserModal({ user, onClose, onSaved }) {
  const { t } = useTranslation();
  const [reason, setReason] = useState(''); const [ban, setBan] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    await axios.post(`${API}/api/admin/users/${user.user_id}/suspend`, { reason, ban }, { withCredentials: true });
    onSaved();
  };
  return (
    <ModalShell title={`Suspendre — ${user.email}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3" data-testid="suspend-form">
        <textarea rows={3} required className="jp-input text-sm" placeholder="Motif (visible par l'utilisateur)"
          value={reason} onChange={e => setReason(e.target.value)} data-testid="suspend-reason" />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={ban} onChange={e => setBan(e.target.checked)} data-testid="ban-checkbox" />
          <span>{t('admin.bannir_definitivement_le_compte_res')}</span>
        </label>
        <button type="submit" className="jp-btn w-full" style={{ background: 'var(--jp-error)', color: 'white' }} data-testid="suspend-submit">
          {ban ? 'Bannir définitivement' : 'Suspendre'}
        </button>
      </form>
    </ModalShell>
  );
}

/* ============== TRANSACTIONS ============== */
function TransactionsTab() {
  const { t } = useTranslation();
  const [txs, setTxs] = useState([]);
  const [total, setTotal] = useState(0);
  const [volume, setVolume] = useState('0');
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ type: '', status: '', date_from: '', date_to: '', user_id: '' });

  const load = useCallback(async () => {
    const params = new URLSearchParams({ page, limit: 20 });
    Object.entries(filters).forEach(([k, v]) => v && params.append(k, v));
    try {
      const { data } = await axios.get(`${API}/api/admin/transactions?${params}`, { withCredentials: true });
      setTxs(data.transactions); setTotal(data.total); setVolume(data.volume_total || '0');
    } catch {}
  }, [page, filters]);
  useEffect(() => { load(); }, [load]);

  const exportCsv = async () => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => v && params.append(k, v));
    window.open(`${API}/api/admin/transactions/export?${params}`, '_blank');
  };

  return (
    <div className="jp-card jp-animate-fadeIn">
      <div className="p-4 border-b grid grid-cols-2 md:grid-cols-6 gap-2" style={{ borderColor: 'var(--jp-border)' }} data-testid="tx-filters">
        <input className="jp-input text-xs" placeholder="Type (send/deposit...)" value={filters.type} onChange={e => { setFilters(f => ({ ...f, type: e.target.value })); setPage(1); }} data-testid="filter-type" />
        <select className="jp-input text-xs" value={filters.status} onChange={e => { setFilters(f => ({ ...f, status: e.target.value })); setPage(1); }} data-testid="filter-status">
          <option value="">{t('admin.tous_statuts')}</option>
          <option value="completed">{t('admin.termine')}</option>
          <option value="pending">{t('admin.en_attente')}</option>
          <option value="failed">{t('admin.echoue')}</option>
        </select>
        <input type="date" className="jp-input text-xs" value={filters.date_from} onChange={e => { setFilters(f => ({ ...f, date_from: e.target.value })); setPage(1); }} data-testid="filter-date-from" />
        <input type="date" className="jp-input text-xs" value={filters.date_to} onChange={e => { setFilters(f => ({ ...f, date_to: e.target.value })); setPage(1); }} data-testid="filter-date-to" />
        <input className="jp-input text-xs" placeholder="User ID" value={filters.user_id} onChange={e => { setFilters(f => ({ ...f, user_id: e.target.value })); setPage(1); }} data-testid="filter-user" />
        <button onClick={exportCsv} className="jp-btn jp-btn-secondary jp-btn-sm" data-testid="export-tx-csv"><Download size={14} /> Export CSV</button>
      </div>
      <div className="p-3 border-b flex items-center justify-between text-xs font-['Manrope']" style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-surface-secondary)' }}>
        <span><strong>{total}</strong> transaction(s)</span>
        <span>{t('admin.volume_total')}<strong>{parseFloat(volume).toLocaleString('fr-FR')}</strong></span>
      </div>
      <div className="overflow-x-auto">
        <table className="jp-table">
          <thead><tr><th>TX</th><th>Type</th><th>{t('admin.montant')}</th><th>De</th><th>Vers</th><th>{t('admin.statut')}</th><th>Date</th></tr></thead>
          <tbody>
            {txs.map(tx => (
              <tr key={tx.tx_id} data-testid={`admin-tx-${tx.tx_id}`}>
                <td><span className="font-mono text-[11px]">{tx.tx_id?.slice(0, 12)}</span></td>
                <td><span className="jp-badge jp-badge-neutral">{tx.type}</span></td>
                <td><span className="font-['Outfit'] font-medium">{parseFloat(tx.amount).toLocaleString('fr-FR')} {tx.currency}</span></td>
                <td className="text-[11px] font-mono" style={{ color: 'var(--jp-text-muted)' }}>{tx.from_user_id?.slice(0, 14) || '-'}</td>
                <td className="text-[11px] font-mono" style={{ color: 'var(--jp-text-muted)' }}>{tx.to_user_id?.slice(0, 14) || '-'}</td>
                <td><span className={`jp-badge ${tx.status === 'completed' ? 'jp-badge-success' : tx.status === 'pending' ? 'jp-badge-warning' : 'jp-badge-error'}`}>{tx.status}</span></td>
                <td className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{new Date(tx.created_at).toLocaleString('fr-FR')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > 20 && (
        <div className="flex justify-center gap-2 p-4 border-t" style={{ borderColor: 'var(--jp-border)' }}>
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="tx-prev">Préc.</button>
          <span className="px-4 py-1.5 text-sm" style={{ color: 'var(--jp-text-secondary)' }}>Page {page} / {Math.ceil(total / 20)}</span>
          <button disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="tx-next">Suiv.</button>
        </div>
      )}
    </div>
  );
}

/* ============== KYC ============== */
function KycTab({ onAction, setMessage }) {
  const { t } = useTranslation();
  const [subTab, setSubTab] = useState('pending');
  const [pending, setPending] = useState([]);
  const [selected, setSelected] = useState(null);
  const [rejectReason, setRejectReason] = useState('');
  const [zoomUrl, setZoomUrl] = useState(null);

  // iter214 — history sub-tab state
  const [history, setHistory] = useState([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [histPage, setHistPage] = useState(1);
  const [histStatus, setHistStatus] = useState('');
  const [histSearch, setHistSearch] = useState('');
  const [histLoading, setHistLoading] = useState(false);

  const load = async () => {
    try {
      const { data } = await axios.get(`${API}/api/kyc/admin/pending`, { withCredentials: true });
      setPending(data.submissions || []);
    } catch {}
  };
  useEffect(() => { load(); }, []);

  // iter214 — load archived decisions
  const loadHistory = useCallback(async () => {
    setHistLoading(true);
    try {
      const params = new URLSearchParams({ page: histPage, limit: 20 });
      if (histStatus) params.set('status', histStatus);
      if (histSearch.trim()) params.set('search', histSearch.trim());
      const { data } = await axios.get(
        `${API}/api/kyc/admin/history?${params}`, { withCredentials: true });
      setHistory(data.items || []);
      setHistoryTotal(data.total || 0);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement historique');
    } finally { setHistLoading(false); }
  }, [histPage, histStatus, histSearch]);

  useEffect(() => {
    if (subTab === 'history') loadHistory();
  }, [subTab, loadHistory]);

  // iter214 — open the same review modal for an ARCHIVED dossier.
  const openArchive = async (kycId) => {
    try {
      const { data } = await axios.get(
        `${API}/api/kyc/admin/${kycId}`, { withCredentials: true });
      setSelected({ ...data, _readonly: true });
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement dossier');
    }
  };

  const approve = async (id) => {
    try {
      await axios.post(`${API}/api/kyc/admin/${id}/approve`, {}, { withCredentials: true });
      toast.success('KYC approuvé'); setSelected(null); load(); onAction && onAction();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };
  const reject = async (id) => {
    if (!rejectReason.trim()) { toast.error('Motif requis'); return; }
    try {
      await axios.post(`${API}/api/kyc/admin/${id}/reject`, { reason: rejectReason }, { withCredentials: true });
      toast.success('KYC rejeté'); setSelected(null); setRejectReason(''); load(); onAction && onAction();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  const RiskBadge = ({ score }) => {
    const cfg = ({
      low:    { label: 'Faible risque', bg: '#DCFCE7', fg: '#166534' },
      medium: { label: 'À vérifier',    bg: '#FEF3C7', fg: '#854D0E' },
      high:   { label: 'Risque élevé',  bg: '#FEE2E2', fg: '#991B1B' },
    })[score] || { label: 'Pré-vérif IA indisponible', bg: '#E5E7EB', fg: '#374151' };
    return (
      <span data-testid={`kyc-ai-score-${score || 'unknown'}`}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold"
        style={{ background: cfg.bg, color: cfg.fg }}>
        IA: {cfg.label}
      </span>
    );
  };

  const DecisionBadge = ({ status }) => {
    const cfg = status === 'approved'
      ? { label: 'Approuvé', bg: '#DCFCE7', fg: '#166534' }
      : status === 'rejected'
      ? { label: 'Rejeté',   bg: '#FEE2E2', fg: '#991B1B' }
      : { label: status || '?', bg: '#E5E7EB', fg: '#374151' };
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold"
        style={{ background: cfg.bg, color: cfg.fg }}
        data-testid={`kyc-history-status-${status}`}>
        {cfg.label}
      </span>
    );
  };

  // iter239s — DEFENSIVE: KYC images MUST hit the API endpoint directly.
  // NEVER pass them through SmartImage / ZoomableImage / R2 variants. The
  // `previewUrl` or `url` here is always shaped as
  // `/api/kyc/admin/{kyc_id}/image/{id|id_back|selfie}` and the backend
  // returns image/jpeg from PostgreSQL BYTEA + optional legacy-disk fallback.
  // Any attempt to compose responsive variants (.webp / .avif / R2 hash)
  // for KYC would break privacy + return 404 — do not do it.
  const ImgThumb = ({ url, previewUrl, alt, testid, label }) => {
    const [broken, setBroken] = useState(false);
    // Runtime assertion (DEV-only) so accidental regressions surface fast:
    // if a future contributor wraps this with SmartImage or sticks a
    // `.webp` suffix, the assertion below logs a loud warning.
    if (process.env.NODE_ENV !== 'production' && url) {
      const ok = url.startsWith('/api/kyc/admin/') && url.includes('/image/');
      if (!ok) {
        // eslint-disable-next-line no-console
        console.warn('[KYC ImgThumb] url must be /api/kyc/admin/{id}/image/{v}',
                     'got:', url);
      }
    }
    if (!url || broken) {
      return (
        <div data-testid={testid} className="w-full aspect-[3/4] rounded-lg border flex flex-col items-center justify-center text-xs text-center p-2"
          style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-bg-2, #f3f4f6)', color: 'var(--jp-text-muted)' }}>
          <div className="font-semibold">{t('admin.kyc.image_unavailable')}</div>
          <div className="text-[10px] opacity-70 mt-1">{t('admin.kyc.image_unavailable_hint')}</div>
        </div>
      );
    }
    return (
      <button type="button" onClick={() => setZoomUrl(url)} data-testid={testid}
        className="relative block w-full text-left overflow-hidden rounded-lg">
        <img src={`${API}${previewUrl || url}`} alt={alt} loading="lazy"
          onError={() => setBroken(true)}
          className="w-full aspect-[3/4] object-cover rounded-lg border"
          style={{ borderColor: 'var(--jp-border)' }} />
        <div className="absolute bottom-1 left-1 right-1 px-1.5 py-0.5 rounded text-[10px] font-bold text-white text-center"
          style={{ background: 'rgba(0,0,0,0.6)' }}>
          {label} · {t('admin.kyc.tap_to_zoom')}
        </div>
      </button>
    );
  };

  return (
    <div className="jp-card jp-animate-fadeIn" data-testid="kyc-tab">
      {/* iter214 — sub-tab switcher */}
      <div className="flex gap-2 p-3 border-b" style={{ borderColor: 'var(--jp-border)' }}>
        <button onClick={() => setSubTab('pending')}
          data-testid="kyc-subtab-pending"
          className="px-3 py-1.5 text-sm font-semibold rounded-md transition-colors"
          style={{
            background: subTab === 'pending' ? 'var(--jp-primary)' : 'transparent',
            color:      subTab === 'pending' ? '#fff' : 'var(--jp-text-muted)',
          }}>
          En attente ({pending.length})
        </button>
        <button onClick={() => setSubTab('history')}
          data-testid="kyc-subtab-history"
          className="px-3 py-1.5 text-sm font-semibold rounded-md transition-colors"
          style={{
            background: subTab === 'history' ? 'var(--jp-primary)' : 'transparent',
            color:      subTab === 'history' ? '#fff' : 'var(--jp-text-muted)',
          }}>
          Historique
        </button>
      </div>

      {subTab === 'pending' && (<>
        <div className="p-4 border-b" style={{ borderColor: 'var(--jp-border)' }}>
          <h3 className="font-['Outfit'] font-bold">KYC en attente ({pending.length})</h3>
        </div>
        {pending.length === 0 && (
          <div className="p-8 text-center text-sm" style={{ color: 'var(--jp-text-muted)' }}>Aucune soumission en attente.</div>
        )}
        <div className="divide-y" style={{ borderColor: 'var(--jp-border)' }}>
          {pending.map(k => (
            <div key={k.kyc_id} className="p-4 flex items-start gap-4" data-testid={`kyc-${k.kyc_id}`}>
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2 mb-1">
                  <span className="font-semibold">{k.full_name}</span>
                  <span className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>({k.email})</span>
                  <RiskBadge score={k.ai_risk_score} />
                </div>
                <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                  {k.id_type} · {k.id_number} · {k.country_code || '—'} · {k.phone_number || '—'}
                </div>
                {k.ai_alerts && k.ai_alerts.length > 0 && (
                  <div className="text-[11px] mt-1" style={{ color: '#B45309' }}>
                    ⚠ {k.ai_alerts.slice(0, 2).join(' · ')}
                    {k.ai_alerts.length > 2 ? ` +${k.ai_alerts.length - 2}` : ''}
                  </div>
                )}
                <div className="text-[11px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                  Soumis: {new Date(k.created_at).toLocaleString('fr-FR')}
                </div>
              </div>
              <button onClick={() => setSelected(k)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid={`kyc-review-${k.kyc_id}`}>
                Examiner
              </button>
            </div>
          ))}
        </div>
      </>)}

      {subTab === 'history' && (
        <div data-testid="kyc-history-pane">
          <div className="p-4 border-b flex flex-wrap items-center gap-2" style={{ borderColor: 'var(--jp-border)' }}>
            <select
              value={histStatus}
              onChange={(e) => { setHistPage(1); setHistStatus(e.target.value); }}
              className="jp-input text-sm" style={{ maxWidth: 200 }}
              data-testid="kyc-history-filter-status">
              <option value="">Tous les statuts</option>
              <option value="approved">Approuvés</option>
              <option value="rejected">Rejetés</option>
            </select>
            <input
              value={histSearch}
              onChange={(e) => setHistSearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { setHistPage(1); loadHistory(); } }}
              placeholder="Rechercher nom / email / username…"
              className="jp-input text-sm flex-1"
              data-testid="kyc-history-search" />
            <button onClick={() => { setHistPage(1); loadHistory(); }}
              className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="kyc-history-apply">
              Filtrer
            </button>
            <span className="text-xs ml-auto" style={{ color: 'var(--jp-text-muted)' }}>
              {historyTotal} dossier{historyTotal > 1 ? 's' : ''}
            </span>
          </div>
          {histLoading ? (
            <div className="p-8 text-center text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>
          ) : history.length === 0 ? (
            <div className="p-8 text-center text-sm" style={{ color: 'var(--jp-text-muted)' }}>
              Aucun dossier archivé pour ce filtre.
            </div>
          ) : (
            <div className="divide-y" style={{ borderColor: 'var(--jp-border)' }}>
              {history.map(h => (
                <div key={h.kyc_id} className="p-4 flex items-start gap-4" data-testid={`kyc-history-${h.kyc_id}`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <span className="font-semibold">{h.full_name}</span>
                      <span className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>({h.email})</span>
                      <DecisionBadge status={h.status} />
                    </div>
                    <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                      {h.id_type} · {h.id_number} · {h.country_code || '—'}
                    </div>
                    {h.status === 'rejected' && h.rejection_reason && (
                      <div className="text-[11px] mt-1" style={{ color: '#991B1B' }}>
                        Motif : {h.rejection_reason}
                      </div>
                    )}
                    <div className="text-[11px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                      Soumis {new Date(h.created_at).toLocaleString('fr-FR')}
                      {h.reviewed_at && <> · Décidé {new Date(h.reviewed_at).toLocaleString('fr-FR')}</>}
                      {h.reviewer_email && <> · par <strong>{h.reviewer_email}</strong></>}
                    </div>
                  </div>
                  <button onClick={() => openArchive(h.kyc_id)} className="jp-btn jp-btn-ghost jp-btn-sm"
                    data-testid={`kyc-history-view-${h.kyc_id}`}>
                    Voir
                  </button>
                </div>
              ))}
            </div>
          )}
          {historyTotal > 20 && (
            <div className="p-3 flex items-center justify-between border-t" style={{ borderColor: 'var(--jp-border)' }}>
              <button onClick={() => setHistPage(p => Math.max(1, p - 1))}
                disabled={histPage <= 1}
                className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="kyc-history-prev">Précédent</button>
              <span className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                Page {histPage} / {Math.max(1, Math.ceil(historyTotal / 20))}
              </span>
              <button onClick={() => setHistPage(p => p + 1)}
                disabled={histPage >= Math.ceil(historyTotal / 20)}
                className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="kyc-history-next">Suivant</button>
            </div>
          )}
        </div>
      )}

      {selected && (
        <ModalShell title={`KYC — ${selected.full_name}${selected._readonly ? ' (archivé)' : ''}`}
          onClose={() => { setSelected(null); setRejectReason(''); }} wide>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <RiskBadge score={selected.ai_risk_score} />
            {selected._readonly && <DecisionBadge status={selected.status} />}
            {!selected._readonly && (
              <span className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                Décision finale = humaine. L'IA ne fait que pré-trier.
              </span>
            )}
          </div>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <div>
              <div className="jp-label">Recto</div>
              <ImgThumb url={selected.id_photo_url} previewUrl={selected.preview_id_url}
                alt={t('admin.recto')} testid="kyc-id-photo" label="Recto" />
            </div>
            <div>
              <div className="jp-label">Verso</div>
              <ImgThumb url={selected.id_back_photo_url} previewUrl={selected.preview_id_back_url}
                alt={t('admin.verso')} testid="kyc-id-back-photo" label="Verso" />
            </div>
            <div>
              <div className="jp-label">Selfie</div>
              <ImgThumb url={selected.selfie_url} previewUrl={selected.preview_selfie_url}
                alt={t('admin.selfie')} testid="kyc-selfie" label="Selfie" />
            </div>
          </div>
          <div className="text-sm space-y-1 mb-3">
            <div><strong>{t('admin.nom')}</strong> {selected.full_name}</div>
            <div><strong>{t('admin.type')}</strong> {selected.id_type}</div>
            <div><strong>N° ID :</strong> {selected.id_number}</div>
            <div><strong>{t('admin.email')}</strong> {selected.email}</div>
            <div><strong>{t('admin.pays_tel')}</strong> {selected.country_code || '—'} / {selected.phone_number || '—'}</div>
            {selected._readonly && (<>
              <div><strong>Soumis :</strong> {selected.created_at ? new Date(selected.created_at).toLocaleString('fr-FR') : '—'}</div>
              <div><strong>Décidé :</strong> {selected.reviewed_at ? new Date(selected.reviewed_at).toLocaleString('fr-FR') : '—'}</div>
              <div><strong>Par :</strong> {selected.reviewer_email || '—'}</div>
              {selected.status === 'rejected' && selected.rejection_reason && (
                <div><strong>Motif de rejet :</strong> <span style={{ color: '#991B1B' }}>{selected.rejection_reason}</span></div>
              )}
            </>)}
          </div>
          {selected.ai_alerts && selected.ai_alerts.length > 0 && (
            <div className="jp-alert jp-alert-warning mb-3" data-testid="kyc-ai-alerts">
              <div className="font-semibold text-sm mb-1">Alertes IA</div>
              <ul className="text-xs list-disc list-inside space-y-0.5">
                {selected.ai_alerts.map((a, i) => <li key={i}>{a}</li>)}
              </ul>
            </div>
          )}
          {selected.ai_payload?.ai && (
            <details className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
              <summary className="cursor-pointer font-semibold">Détails IA (debug)</summary>
              <pre className="mt-1 p-2 rounded text-[10px] overflow-x-auto"
                style={{ background: 'var(--jp-bg-2, #f3f4f6)' }}>
                {JSON.stringify(selected.ai_payload.ai, null, 2)}
              </pre>
            </details>
          )}
          {!selected._readonly && (<>
            <div className="flex gap-2">
              <button onClick={() => approve(selected.kyc_id)} className="jp-btn jp-btn-primary flex-1" data-testid="kyc-approve">
                <Check size={16} /> Approuver
              </button>
            </div>
            <div className="mt-3">
              <input className="jp-input text-sm mb-2" placeholder="Motif de rejet (obligatoire)" value={rejectReason} onChange={e => setRejectReason(e.target.value)} data-testid="kyc-reject-reason" />
              <button onClick={() => reject(selected.kyc_id)} className="jp-btn w-full" style={{ background: 'var(--jp-error)', color: 'white' }} data-testid="kyc-reject">
                <X size={16} /> Rejeter
              </button>
            </div>
          </>)}
        </ModalShell>
      )}

      {zoomUrl && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center cursor-zoom-out"
          style={{ background: 'rgba(0,0,0,0.92)' }}
          onClick={() => setZoomUrl(null)}
          data-testid="kyc-zoom-overlay">
          <div onClick={(e) => e.stopPropagation()} className="w-full h-full flex items-center justify-center p-4">
            <ZoomableImage src={`${API}${zoomUrl}`} alt={t('admin.zoom')}
              maxHeight="92vh" background="transparent"
              testId="kyc-zoom-image" />
          </div>
          <button
            onClick={() => setZoomUrl(null)}
            className="absolute top-4 right-4 p-2 rounded-full text-white z-10"
            style={{ background: 'rgba(255,255,255,0.15)' }}
            data-testid="kyc-zoom-close">
            <X size={20} />
          </button>
        </div>
      )}
    </div>
  );
}

/* ============== JAPAP SPIN ADMIN ============== */
function SpinAdminTab({ setMessage }) {
  const [stats, setStats] = useState(null);
  const [cfg, setCfg] = useState({ spin_enabled: 'true', spin_is_paid: 'false', spin_cost_xaf: '0', spin_max_daily_plays: '3', spin_daily_cap_xaf: '2000', spin_rewards_json: '' });
  const [rewards, setRewards] = useState([]);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const [s, settings] = await Promise.all([
        axios.get(`${API}/api/admin/games/stats`, { withCredentials: true }),
        axios.get(`${API}/api/admin/settings`, { withCredentials: true }),
      ]);
      setStats(s.data);
      const src = settings.data.settings;
      setCfg({
        spin_enabled: src.spin_enabled, spin_is_paid: src.spin_is_paid,
        spin_cost_xaf: src.spin_cost_xaf, spin_max_daily_plays: src.spin_max_daily_plays,
        spin_daily_cap_xaf: src.spin_daily_cap_xaf, spin_rewards_json: src.spin_rewards_json,
      });
      try { setRewards(JSON.parse(src.spin_rewards_json || '[]')); } catch { setRewards([]); }
    } catch {}
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setSaving(true);
    try {
      await axios.put(`${API}/api/admin/settings`, {
        settings: {
          spin_enabled: cfg.spin_enabled === 'true', spin_is_paid: cfg.spin_is_paid === 'true',
          spin_cost_xaf: parseInt(cfg.spin_cost_xaf || 0), spin_max_daily_plays: parseInt(cfg.spin_max_daily_plays || 3),
          spin_daily_cap_xaf: parseInt(cfg.spin_daily_cap_xaf || 2000),
          spin_rewards_json: JSON.stringify(rewards),
        }
      }, { withCredentials: true });
      toast.success('JAPAP Spin configuré'); load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setSaving(false); }
  };

  const updateReward = (i, field, val) => setRewards(r => r.map((p, idx) => idx === i ? { ...p, [field]: Number(val) || 0 } : p));
  const addReward = () => setRewards(r => [...r, { amount: 0, weight: 1 }]);
  const removeReward = (i) => setRewards(r => r.filter((_, idx) => idx !== i));

  if (!stats) return <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;

  return (
    <div className="space-y-5 jp-animate-fadeIn" data-testid="spin-admin-tab">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MiniStat label="Parties totales" value={stats.plays_total} />
        <MiniStat label="Parties 24h" value={stats.plays_24h} accent="var(--jp-primary)" />
        <MiniStat label="XAF distribués" value={`${parseFloat(stats.total_rewarded_xaf).toLocaleString('fr-FR')}`} accent="var(--jp-secondary)" />
        <MiniStat label="Joueurs actifs (30j)" value={stats.active_players_30d} accent="#10B981" />
      </div>

      <div className="jp-card-elevated p-5">
        <h3 className="font-['Outfit'] text-lg font-bold mb-4">Configuration du jeu</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <ToggleField label="Spin activé" value={cfg.spin_enabled} onChange={v => setCfg(c => ({ ...c, spin_enabled: v }))} testid="spin-enabled" />
          <ToggleField label="Mode payant" value={cfg.spin_is_paid} onChange={v => setCfg(c => ({ ...c, spin_is_paid: v }))} testid="spin-paid" />
          <div><label className="jp-label">Coût par spin (XAF)</label>
            <input type="number" min="0" className="jp-input text-sm" value={cfg.spin_cost_xaf} onChange={e => setCfg(c => ({ ...c, spin_cost_xaf: e.target.value }))} data-testid="spin-cost" /></div>
          <div><label className="jp-label">Spins max/jour/user</label>
            <input type="number" min="1" className="jp-input text-sm" value={cfg.spin_max_daily_plays} onChange={e => setCfg(c => ({ ...c, spin_max_daily_plays: e.target.value }))} data-testid="spin-max-plays" /></div>
          <div className="md:col-span-2"><label className="jp-label">Plafond journalier gains (XAF/user tous jeux)</label>
            <input type="number" min="0" className="jp-input text-sm" value={cfg.spin_daily_cap_xaf} onChange={e => setCfg(c => ({ ...c, spin_daily_cap_xaf: e.target.value }))} data-testid="spin-daily-cap" /></div>
        </div>

        <div className="mt-5">
          <div className="flex items-center justify-between mb-2">
            <label className="jp-label m-0">Récompenses (weighted random)</label>
            <button onClick={addReward} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="add-reward">+ Ajouter</button>
          </div>
          <div className="space-y-2">
            {rewards.map((p, i) => (
              <div key={i} className="flex items-center gap-2" data-testid={`reward-${i}`}>
                <input type="number" className="jp-input text-sm flex-1" placeholder="Montant XAF" value={p.amount} onChange={e => updateReward(i, 'amount', e.target.value)} />
                <input type="number" className="jp-input text-sm w-24" placeholder="Poids" value={p.weight} onChange={e => updateReward(i, 'weight', e.target.value)} />
                <button onClick={() => removeReward(i)} className="p-1.5 rounded" style={{ color: 'var(--jp-error)' }}><X size={14} /></button>
              </div>
            ))}
          </div>
          <p className="text-[11px] mt-2" style={{ color: 'var(--jp-text-muted)' }}>
            Probabilité = poids / somme_totale_des_poids. Ex : {JSON.stringify(rewards)}
          </p>
        </div>

        <button onClick={save} disabled={saving} className="jp-btn jp-btn-primary mt-5" data-testid="spin-save">
          {saving ? 'Enregistrement…' : 'Enregistrer la configuration'}
        </button>
      </div>

      {stats.top_winners?.length > 0 && (
        <div className="jp-card-elevated p-5">
          <h3 className="font-['Outfit'] text-lg font-bold mb-3">Top 10 gagnants</h3>
          <div className="space-y-2">
            {stats.top_winners.map((w, i) => (
              <div key={w.user_id} className="flex items-center gap-3 text-sm" data-testid={`top-winner-${i}`}>
                <span className="w-6 text-center font-bold" style={{ color: 'var(--jp-primary)' }}>{i + 1}</span>
                <div className="jp-avatar jp-avatar-sm jp-avatar-primary">{w.name?.[0]}</div>
                <span className="flex-1">{w.name}</span>
                <span className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>{w.plays} parties</span>
                <span className="font-bold" style={{ color: '#10B981' }}>{parseFloat(w.total_won).toLocaleString('fr-FR')} XAF</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ============== SETTINGS ============== */
function SettingsTab({ setMessage }) {
  const [settings, setSettings] = useState({});
  const [loading, setLoading] = useState(false);

  const load = async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/settings`, { withCredentials: true });
      setSettings(data.settings || {});
    } catch {}
  };
  useEffect(() => { load(); }, []);

  const save = async (partial) => {
    setLoading(true);
    try {
      const coerced = Object.fromEntries(Object.entries(partial).map(([k, v]) => {
        if (v === 'true' || v === 'false') return [k, v === 'true'];
        return [k, v];
      }));
      await axios.put(`${API}/api/admin/settings`, { settings: coerced }, { withCredentials: true });
      toast.success('Paramètres enregistrés'); load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setLoading(false); }
  };

  const groups = [
    {
      title: 'Transferts P2P (send) — Frais & Plafonds', key: 'snd',
      fields: [
        { key: 'send_fee_enabled', type: 'bool', label: 'Activer les frais de transfert' },
        { key: 'send_fee_mode', type: 'select', label: 'Mode de frais', options: [
          { value: 'percent', label: 'Pourcentage (%)' },
          { value: 'flat', label: 'Montant fixe' },
        ]},
        { key: 'send_fee_value', type: 'text', label: 'Frais standard (ex: 1 = 1% ou 100 XAF)' },
        { key: 'send_fee_pro_enabled', type: 'bool', label: 'Appliquer des frais aux PRO (sinon PRO = 0)' },
        { key: 'send_fee_pro_value', type: 'text', label: 'Frais spécifique PRO (ex: 0.5 = 0.5%)' },
        { key: 'send_fee_min', type: 'text', label: 'Frais minimum (absolu, 0 = désactivé)' },
        { key: 'send_fee_max', type: 'text', label: 'Frais maximum (cap, 0 = désactivé)' },
        { key: 'send_daily_cap_amount', type: 'text', label: 'Plafond journalier par utilisateur (0 = illimité)' },
      ],
    },
    {
      title: 'Retraits Wallet — Frais & Méthodes', key: 'w',
      fields: [
        { key: 'withdraw_enabled', type: 'bool', label: 'Retraits activés (master switch)' },
        { key: 'withdraw_disabled_message', type: 'text', label: 'Message si désactivés' },
        { key: 'kyc_required_for_withdraw', type: 'bool', label: 'KYC requis pour retirer' },
        { key: 'withdraw_min_amount_usd', type: 'text', label: 'Retrait minimum (USD)' },
        { key: 'withdraw_fee_mode', type: 'select', label: 'Mode de frais', options: [
          { value: 'percent', label: 'Pourcentage (%)' },
          { value: 'flat', label: 'Montant fixe (USDT)' },
        ]},
        { key: 'withdraw_fee_value', type: 'text', label: 'Frais standard (ex: 2 = 2% ou 2 USDT)' },
        { key: 'withdraw_fee_value_pro', type: 'text', label: 'Frais PRO (remise — ex: 1 = 1%)' },
        { key: 'withdraw_fee_value_trc20', type: 'text', label: 'Override TRC20 (0 = utilise standard)' },
        { key: 'withdraw_fee_value_bep20', type: 'text', label: 'Override BEP20 (0 = utilise standard)' },
        { key: 'withdraw_usdt_trc20_enabled', type: 'bool', label: 'Activer USDT TRC20 (TRON)' },
        { key: 'withdraw_usdt_bep20_enabled', type: 'bool', label: 'Activer USDT BEP20 (BSC)' },
      ],
    },
    {
      title: 'Dépôts Wallet — Méthodes & Adresses', key: 'd',
      fields: [
        { key: 'deposit_min_amount_usd', type: 'text', label: 'Dépôt minimum (USD)' },
        { key: 'deposit_usdt_trc20_enabled', type: 'bool', label: 'Activer dépôt USDT TRC20' },
        { key: 'deposit_usdt_bep20_enabled', type: 'bool', label: 'Activer dépôt USDT BEP20' },
        { key: 'deposit_hubtel_card_enabled', type: 'bool', label: 'Activer dépôt par carte (Hubtel)' },
        { key: 'deposit_address_usdt_trc20', type: 'text', label: 'Adresse dépôt USDT TRC20 (T...)' },
        { key: 'deposit_address_usdt_bep20', type: 'text', label: 'Adresse dépôt USDT BEP20 (0x...)' },
      ],
    },
    {
      title: 'Devises', key: 'c',
      fields: [
        { key: 'base_currency', type: 'text', label: 'Devise de base système' },
        { key: 'currency_detection_enabled', type: 'bool', label: 'Détection IP' },
        { key: 'currency_force', type: 'text', label: 'Forcer une devise (ex: USD — laisser vide pour auto)' },
      ],
    },
    {
      title: 'APIs de paiement (Phase 2)', key: 'p',
      fields: [
        { key: 'nowpayments_enabled', type: 'bool', label: 'NowPayments' },
        { key: 'hubtel_enabled', type: 'bool', label: 'Hubtel (carte bancaire)' },
        { key: 'metamask_enabled', type: 'bool', label: 'MetaMask' },
        { key: 'trustwallet_enabled', type: 'bool', label: 'Trust Wallet' },
      ],
    },
    {
      title: 'Modules système', key: 'm',
      fields: [
        { key: 'marketplace_enabled', type: 'bool', label: 'Marketplace' },
        { key: 'crowdfunding_enabled', type: 'bool', label: 'Crowdfunding' },
        { key: 'games_enabled', type: 'bool', label: 'Jeux' },
        { key: 'referral_enabled', type: 'bool', label: 'Parrainage' },
        { key: 'kyc_required_for_paid_games', type: 'bool', label: 'KYC requis pour jeux payants' },
        // iter237x — visibility toggles for the cards on /services.
        { key: 'transport_enabled', type: 'bool', label: 'Transport JAPAP' },
        { key: 'ads_enabled',       type: 'bool', label: 'Advertising' },
        { key: 'offers_enabled',    type: 'bool', label: 'Offres' },
        { key: 'crypto_enabled',    type: 'bool', label: 'Crypto / Wallet' },
      ],
    },
    {
      title: 'Modules — Badges affichés sur /services', key: 'mb',
      fields: [
        { key: 'module_transport_badge', type: 'text', label: 'Badge Transport (vide = aucun)' },
        { key: 'module_ads_badge',       type: 'text', label: 'Badge Advertising (vide = aucun)' },
        { key: 'module_offers_badge',    type: 'text', label: 'Badge Offres (vide = aucun)' },
        { key: 'module_jobs_badge',      type: 'text', label: 'Badge Jobs (vide = aucun)' },
        { key: 'module_crypto_badge',    type: 'text', label: 'Badge Crypto (vide = aucun)' },
      ],
    },
    {
      title: 'Marketplace — Escrow & Boost (zero hardcode)', key: 'mkt',
      fields: [
        { key: 'verified_seller_badge_enabled', type: 'bool', label: 'Afficher badge "Vendeur vérifié"' },
        { key: 'mkt_escrow_enabled',            type: 'bool', label: 'Escrow Marketplace activé' },
        { key: 'mkt_escrow_commission_percent', type: 'text', label: 'Commission JAPAP (% côté vendeur)' },
        { key: 'mkt_escrow_auto_release_days',  type: 'text', label: 'Auto-release (jours sans confirmation)' },
        { key: 'mkt_escrow_dispute_enabled',    type: 'bool', label: 'Litiges acheteur activés' },
        { key: 'mkt_escrow_treasury_account',   type: 'text', label: 'Compte Treasury (ledger interne)' },
        { key: 'mkt_boost_enabled',             type: 'bool', label: 'Boost Marketplace activé' },
        { key: 'mkt_boost_price_24h',           type: 'text', label: 'Prix Boost 24h (USD)' },
        { key: 'mkt_boost_price_7d',            type: 'text', label: 'Prix Boost 7 jours (USD)' },
        { key: 'mkt_boost_price_homepage',      type: 'text', label: 'Prix Boost Homepage (USD)' },
        { key: 'mkt_boost_homepage_days',       type: 'text', label: 'Durée Boost Homepage (jours)' },
        { key: 'targeting_enabled',             type: 'bool', label: 'Ciblage audience activé (Ads + Boost)' },
        { key: 'allow_country_filter',          type: 'bool', label: 'Autoriser filtrage par pays' },
        { key: 'allow_age_filter',              type: 'bool', label: 'Autoriser filtrage par âge' },
      ],
    },
    {
      title: 'JAPAP PRO — Abonnements', key: 'pro',
      fields: [
        { key: 'pro_enabled', type: 'bool', label: 'Module Pro activé' },
        { key: 'pro_trial_enabled', type: 'bool', label: 'Essais gratuits activés' },
        { key: 'pro_trial_days', type: 'text', label: 'Durée de l\'essai (jours)' },
        { key: 'pro_trial_plans', type: 'text', label: 'Plans éligibles (all ou starter,creator)' },
        { key: 'pro_duration_1m_enabled', type: 'bool', label: 'Durée 1 mois disponible' },
        { key: 'pro_duration_3m_enabled', type: 'bool', label: 'Durée 3 mois disponible' },
        { key: 'pro_duration_12m_enabled', type: 'bool', label: 'Durée 12 mois disponible' },
        { key: 'pro_discount_3m_pct', type: 'text', label: 'Remise 3 mois (%)' },
        { key: 'pro_discount_12m_pct', type: 'text', label: 'Remise 12 mois (%)' },
      ],
    },
    {
      title: 'Parrainage — Bonus & Anti-fraude', key: 'ref',
      fields: [
        { key: 'referral_enabled', type: 'bool', label: 'Module Parrainage activé' },
        { key: 'referral_referrer_bonus_usd', type: 'text', label: 'Bonus parrain (USD)' },
        { key: 'referral_referee_bonus_usd', type: 'text', label: 'Bonus filleul (USD)' },
        { key: 'referral_activation_requires_otp', type: 'bool', label: 'Exiger OTP pour activation' },
        { key: 'referral_activation_requires_action', type: 'bool', label: 'Exiger action (post/tx) pour activation' },
        { key: 'referral_tiers_json', type: 'textarea', label: 'Paliers (JSON)' },
        { key: 'referral_tier_plan_id', type: 'text', label: 'Plan Pro utilisé pour récompenses palier' },
        { key: 'referral_leaderboard_enabled', type: 'bool', label: 'Leaderboard activé' },
        { key: 'referral_leaderboard_window', type: 'text', label: 'Fenêtre (weekly | all_time)' },
        { key: 'referral_gamification_enabled', type: 'bool', label: 'Gamification (badges, progression)' },
        { key: 'referral_max_per_ip_per_day', type: 'text', label: 'Limite IP / jour' },
        { key: 'referral_max_per_device_per_day', type: 'text', label: 'Limite Device / jour' },
        { key: 'referral_reminder_enabled', type: 'bool', label: 'Relances auto activées' },
        { key: 'referral_reminder_delay_days', type: 'text', label: 'Délai rappel (jours)' },
      ],
    },
    {
      title: 'JAPAP Connect — WiFi Rewards', key: 'connect',
      fields: [
        { key: 'connect_enabled', type: 'bool', label: 'Module Connect activé' },
        { key: 'connect_reward_per_connection_usd', type: 'text', label: 'Récompense par connexion (USD)' },
        { key: 'connect_reward_per_minute_usd', type: 'text', label: 'Récompense par minute (USD)' },
        { key: 'connect_min_session_seconds', type: 'text', label: 'Durée minimum de session (s)' },
        { key: 'connect_max_reward_per_session_usd', type: 'text', label: 'Plafond par session (USD)' },
        { key: 'connect_pro_required_to_share', type: 'bool', label: 'Pro requis pour partager' },
        { key: 'connect_pro_reward_multiplier', type: 'text', label: 'Multiplicateur Pro (ex: 1.5)' },
        { key: 'connect_max_connections_per_ip_per_day', type: 'text', label: 'Limite IP/jour' },
        { key: 'connect_max_connections_per_device_per_day', type: 'text', label: 'Limite Device/jour' },
        { key: 'connect_max_connections_per_user_per_hotspot_per_day', type: 'text', label: 'Limite User/Hotspot/jour' },
        { key: 'connect_search_radius_km', type: 'text', label: 'Rayon de recherche par défaut (km)' },
        { key: 'connect_gamification_enabled', type: 'bool', label: 'Gamification' },
      ],
    },
    {
      title: 'Referral Boost Events', key: 'boost',
      fields: [
        { key: 'boost_enabled', type: 'bool', label: 'Boost activé' },
        { key: 'boost_name', type: 'text', label: 'Nom du boost (ex: Weekend Boost)' },
        { key: 'boost_multiplier', type: 'text', label: 'Multiplicateur (ex: 2.0)' },
        { key: 'boost_start_at', type: 'text', label: 'Début (ISO ex: 2026-03-01T00:00:00Z — vide = immédiat)' },
        { key: 'boost_end_at', type: 'text', label: 'Fin (ISO — vide = illimité)' },
        { key: 'boost_applies_to_referrer', type: 'bool', label: 'Appliquer au parrain' },
        { key: 'boost_applies_to_referee', type: 'bool', label: 'Appliquer au filleul' },
        { key: 'boost_applies_to_tiers', type: 'bool', label: 'Appliquer aux paliers (récompenses wallet)' },
      ],
    },
  ];

  return (
    <div className="space-y-4 jp-animate-fadeIn" data-testid="settings-tab">
      {groups.map(g => (
        <div key={g.key} className="jp-card-elevated p-5">
          <h3 className="font-['Outfit'] text-lg font-bold mb-4">{g.title}</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {g.fields.map(f => (
              <div key={f.key} data-testid={`setting-${f.key}`} className={f.type === 'textarea' ? 'md:col-span-2' : ''}>
                {f.type === 'bool' ? (
                  <ToggleField label={f.label} value={settings[f.key] || 'false'} onChange={v => setSettings(s => ({ ...s, [f.key]: v }))} testid={`toggle-${f.key}`} />
                ) : f.type === 'select' ? (
                  <>
                    <label className="jp-label">{f.label}</label>
                    <select className="jp-input text-sm" value={settings[f.key] || f.options?.[0]?.value || ''}
                      data-testid={`select-${f.key}`}
                      onChange={e => setSettings(s => ({ ...s, [f.key]: e.target.value }))}>
                      {(f.options || []).map(opt => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </>
                ) : f.type === 'textarea' ? (
                  <>
                    <label className="jp-label">{f.label}</label>
                    <textarea rows={4} className="jp-input text-xs font-mono" value={settings[f.key] || ''} onChange={e => setSettings(s => ({ ...s, [f.key]: e.target.value }))} />
                  </>
                ) : (
                  <>
                    <label className="jp-label">{f.label}</label>
                    <input className="jp-input text-sm" value={settings[f.key] || ''} onChange={e => setSettings(s => ({ ...s, [f.key]: e.target.value }))} />
                  </>
                )}
              </div>
            ))}
          </div>
          {g.key === 'w' && (
            <WithdrawFeeByPlanEditor
              value={settings.withdraw_fee_by_plan_json || ''}
              onChange={v => setSettings(s => ({ ...s, withdraw_fee_by_plan_json: v }))}
            />
          )}
        </div>
      ))}
      <button disabled={loading} onClick={() => save(settings)} className="jp-btn jp-btn-primary" data-testid="save-settings">
        {loading ? 'Enregistrement…' : 'Enregistrer tous les paramètres'}
      </button>
    </div>
  );
}

/* ---- Per-plan withdraw fee editor (iter33) ---------------------------- */
function WithdrawFeeByPlanEditor({ value, onChange }) {
  const { t } = useTranslation();
  const [plans, setPlans] = useState([]);
  const [rows, setRows] = useState({});

  useEffect(() => {
    // Parse existing JSON once
    try { setRows(value ? JSON.parse(value) : {}); } catch { setRows({}); }
  }, [value]);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await axios.get(`${API}/api/pro/plans`, { withCredentials: true });
        setPlans(data?.plans || []);
      } catch {}
    })();
  }, []);

  const updateRow = (planId, patch) => {
    const next = { ...rows, [planId]: { ...(rows[planId] || { mode: 'percent', value: 0 }), ...patch } };
    setRows(next);
    onChange(JSON.stringify(next));
  };

  const removeRow = (planId) => {
    const next = { ...rows };
    delete next[planId];
    setRows(next);
    onChange(JSON.stringify(next));
  };

  if (!plans.length) {
    return (
      <div className="mt-5 pt-5 border-t text-xs" style={{ borderColor: 'var(--jp-border)', color: 'var(--jp-text-muted)' }}>
        Chargement des plans Pro…
      </div>
    );
  }

  return (
    <div className="mt-5 pt-5 border-t" style={{ borderColor: 'var(--jp-border)' }} data-testid="withdraw-fee-by-plan">
      <div className="flex items-center gap-2 mb-1">
        <Crown size={16} weight="duotone" style={{ color: '#F59E0B' }} />
        <h4 className="font-['Outfit'] text-base font-bold">Frais de retrait par plan JAPAP Pro</h4>
      </div>
      <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
        Les abonnés d'un plan Pro bénéficient de frais réduits. Les utilisateurs non-Pro utilisent la config par défaut ci-dessus.
      </p>
      <div className="rounded-xl overflow-hidden border" style={{ borderColor: 'var(--jp-border)' }}>
        <table className="jp-table w-full text-sm">
          <thead>
            <tr>
              <th className="text-left">Plan</th>
              <th className="text-left">Prix</th>
              <th className="text-left">Mode</th>
              <th className="text-left">Valeur</th>
              <th className="text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {plans.map(p => {
              const row = rows[p.plan_id];
              const mode = row?.mode || 'percent';
              const val = row?.value ?? '';
              return (
                <tr key={p.plan_id} data-testid={`fee-plan-row-${p.plan_id}`}>
                  <td>
                    <div className="flex items-center gap-2">
                      <Crown size={14} weight="fill" style={{ color: '#F59E0B' }} />
                      <strong>{p.name}</strong>
                    </div>
                    <div className="text-[10px] opacity-70">{p.plan_id}</div>
                  </td>
                  <td className="text-xs">${p.price_usd}</td>
                  <td>
                    <select className="jp-input text-xs" value={mode}
                      data-testid={`fee-plan-mode-${p.plan_id}`}
                      onChange={e => updateRow(p.plan_id, { mode: e.target.value, value: row?.value ?? 0 })}>
                      <option value="percent">{t('admin.pourcentage')}</option>
                      <option value="flat">{t('admin.fixe_usdt')}</option>
                    </select>
                  </td>
                  <td>
                    <input type="number" min="0" step="0.01" className="jp-input text-xs" value={val}
                      data-testid={`fee-plan-value-${p.plan_id}`}
                      placeholder={mode === 'percent' ? 'ex: 1' : 'ex: 0.5'}
                      onChange={e => updateRow(p.plan_id, { mode, value: e.target.value === '' ? '' : parseFloat(e.target.value) })} />
                  </td>
                  <td className="text-right">
                    {row !== undefined && (
                      <button type="button" onClick={() => removeRow(p.plan_id)}
                        className="jp-btn jp-btn-ghost jp-btn-sm text-xs"
                        data-testid={`fee-plan-remove-${p.plan_id}`}>
                        Retirer
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] mt-2" style={{ color: 'var(--jp-text-muted)' }}>
        Laisser un plan vide = pas de surcharge → les frais par défaut s'appliquent à ses abonnés.
      </p>
    </div>
  );
}

/* ============== JAPAP PRO ADMIN ============== */
function ProAdminTab({ setMessage }) {
  const { t } = useTranslation();
  const [stats, setStats] = useState(null);
  const [plans, setPlans] = useState([]);
  const [subs, setSubs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState({ plan_id: '', status: '', is_trial: '', search: '' });
  const [editPlan, setEditPlan] = useState(null);
  const [grantForm, setGrantForm] = useState({ user_id: '', plan_id: 'starter', days: 30, note: '' });
  const [showGrant, setShowGrant] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([
        axios.get(`${API}/api/admin/pro/stats`, { withCredentials: true }),
        axios.get(`${API}/api/admin/pro/plans`, { withCredentials: true }),
      ]);
      setStats(s.data); setPlans(p.data);
    } catch {}
  }, []);

  const loadSubs = useCallback(async () => {
    const params = new URLSearchParams({ page, limit: 20 });
    Object.entries(filter).forEach(([k, v]) => { if (v !== '') params.append(k, v); });
    try {
      const { data } = await axios.get(`${API}/api/admin/pro/subscribers?${params}`, { withCredentials: true });
      setSubs(data.subscribers); setTotal(data.total);
    } catch {}
  }, [page, filter]);

  useEffect(() => { loadAll(); }, [loadAll]);
  useEffect(() => { loadSubs(); }, [loadSubs]);

  const grant = async (e) => {
    e.preventDefault();
    try {
      await axios.post(`${API}/api/admin/pro/grant`, { ...grantForm, days: parseInt(grantForm.days) }, { withCredentials: true });
      toast.success('Abonnement offert'); setShowGrant(false); setGrantForm({ user_id: '', plan_id: 'starter', days: 30, note: '' });
      loadAll(); loadSubs();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  const revoke = async (userId) => {
    if (!window.confirm('Révoquer cet abonnement ?')) return;
    try {
      await axios.post(`${API}/api/admin/pro/revoke/${userId}`, {}, { withCredentials: true });
      toast.success('Révoqué'); loadAll(); loadSubs();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  const extend = async (userId) => {
    const days = parseInt(window.prompt('Prolonger de combien de jours ?', '30') || 0);
    if (!days || days <= 0) return;
    try {
      await axios.post(`${API}/api/admin/pro/extend/${userId}`, { days }, { withCredentials: true });
      toast.success(`Prolongé de ${days} jours`); loadAll(); loadSubs();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  if (!stats) return <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;

  return (
    <div className="space-y-5 jp-animate-fadeIn" data-testid="pro-admin-tab">
      {/* Stats row */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <MiniStat label="Abonnés actifs" value={stats.active_total} accent="var(--jp-primary)" />
        <MiniStat label="Essais actifs" value={stats.active_trials} accent="#F59E0B" />
        <MiniStat label="Revenus 30j" value={`$${parseFloat(stats.revenue_30d_usd).toLocaleString('en-US')}`} accent="#10B981" />
        <MiniStat label="Revenus total" value={`$${parseFloat(stats.revenue_all_time_usd).toLocaleString('en-US')}`} accent="var(--jp-secondary)" />
        <MiniStat label="Trial → Paid" value={`${stats.conversion_pct}%`} accent="#8B5CF6" />
      </div>

      {/* Plans management */}
      <div className="jp-card-elevated p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-['Outfit'] text-lg font-bold">Plans</h3>
          <button onClick={() => axios.post(`${API}/api/admin/pro/expire-now`, {}, { withCredentials: true }).then(r => toast.success(`${r.data.expired_count} expirations synchronisées`))}
            className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="pro-sync-expirations">
            Synchroniser expirations
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="jp-table">
            <thead><tr><th>Plan</th><th>{t('admin.prix_usd')}</th><th>{t('admin.duree')}</th><th>{t('admin.trial')}</th><th>{t('admin.actif')}</th><th>{t('admin.actions')}</th></tr></thead>
            <tbody>
              {plans.map(p => (
                <tr key={p.plan_id} data-testid={`admin-plan-${p.plan_id}`}>
                  <td><strong>{p.name}</strong> <span className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>({p.plan_id})</span></td>
                  <td><span className="font-['Outfit'] font-bold">${parseFloat(p.price_usd).toFixed(2)}</span></td>
                  <td>{p.duration_days}j</td>
                  <td><span className={`jp-badge ${p.trial_eligible ? 'jp-badge-success' : 'jp-badge-neutral'}`}>{p.trial_eligible ? 'Oui' : 'Non'}</span></td>
                  <td><span className={`jp-badge ${p.is_active ? 'jp-badge-success' : 'jp-badge-error'}`}>{p.is_active ? 'Actif' : 'Inactif'}</span></td>
                  <td><button onClick={() => setEditPlan(p)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid={`edit-plan-${p.plan_id}`}><PencilSimple size={14} /> Modifier</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Subscribers list */}
      <div className="jp-card-elevated p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-['Outfit'] text-lg font-bold">Abonnés ({total})</h3>
          <button onClick={() => setShowGrant(true)} className="jp-btn jp-btn-primary jp-btn-sm" data-testid="grant-button">
            <Crown size={14} /> Offrir un abonnement
          </button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3" data-testid="sub-filters">
          <input className="jp-input text-xs" placeholder="Email/username" value={filter.search} onChange={e => { setFilter(f => ({ ...f, search: e.target.value })); setPage(1); }} />
          <select className="jp-input text-xs" value={filter.plan_id} onChange={e => { setFilter(f => ({ ...f, plan_id: e.target.value })); setPage(1); }}>
            <option value="">{t('admin.tous_plans')}</option>
            {plans.map(p => <option key={p.plan_id} value={p.plan_id}>{p.name}</option>)}
          </select>
          <select className="jp-input text-xs" value={filter.status} onChange={e => { setFilter(f => ({ ...f, status: e.target.value })); setPage(1); }}>
            <option value="">{t('admin.tous_statuts')}</option>
            <option value="active">{t('admin.actif')}</option>
            <option value="cancelled">{t('admin.annule')}</option>
            <option value="expired">{t('admin.expire')}</option>
            <option value="revoked">{t('admin.revoque')}</option>
          </select>
          <select className="jp-input text-xs" value={filter.is_trial} onChange={e => { setFilter(f => ({ ...f, is_trial: e.target.value })); setPage(1); }}>
            <option value="">{t('admin.essai_tous')}</option>
            <option value="true">{t('admin.essai_uniquement')}</option>
            <option value="false">{t('admin.payants_uniquement')}</option>
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="jp-table">
            <thead><tr><th>{t('admin.utilisateur')}</th><th>Plan</th><th>{t('admin.source')}</th><th>{t('admin.paye_usd')}</th><th>{t('admin.statut')}</th><th>{t('admin.expire_2')}</th><th>{t('admin.actions')}</th></tr></thead>
            <tbody>
              {subs.map(s => (
                <tr key={s.id} data-testid={`sub-${s.id}`}>
                  <td><div className="font-medium">{s.name}</div><div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{s.email}</div></td>
                  <td><span className="jp-badge jp-badge-primary">{s.plan_id}</span> {s.is_trial && <span className="ml-1 jp-badge jp-badge-warning">trial</span>}</td>
                  <td className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>{s.source}</td>
                  <td>${parseFloat(s.paid_usd).toFixed(2)} {s.discount_pct > 0 && <span className="text-[10px] ml-1" style={{ color: '#10B981' }}>(−{s.discount_pct}%)</span>}</td>
                  <td><span className={`jp-badge ${s.status === 'active' ? 'jp-badge-success' : 'jp-badge-neutral'}`}>{s.status}</span></td>
                  <td className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>{s.expires_at ? new Date(s.expires_at).toLocaleDateString('fr-FR') : '-'}</td>
                  <td>
                    {s.status === 'active' && (
                      <div className="flex gap-1">
                        <button onClick={() => extend(s.user_id)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid={`extend-${s.user_id}`} title="Prolonger">+jours</button>
                        <button onClick={() => revoke(s.user_id)} className="jp-btn jp-btn-ghost jp-btn-sm" style={{ color: 'var(--jp-error)' }} data-testid={`revoke-${s.user_id}`} title="Révoquer"><X size={12} /></button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {total > 20 && (
          <div className="flex justify-center gap-2 pt-3">
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="jp-btn jp-btn-ghost jp-btn-sm">Préc.</button>
            <span className="px-4 py-1.5 text-sm" style={{ color: 'var(--jp-text-secondary)' }}>Page {page} / {Math.ceil(total / 20)}</span>
            <button disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)} className="jp-btn jp-btn-ghost jp-btn-sm">Suiv.</button>
          </div>
        )}
      </div>

      {showGrant && (
        <ModalShell title={t('admin.offrir_un_abonnement')} onClose={() => setShowGrant(false)}>
          <form onSubmit={grant} className="space-y-3" data-testid="grant-form">
            <div><label className="jp-label">User ID</label>
              <input className="jp-input text-sm" required value={grantForm.user_id} onChange={e => setGrantForm(f => ({ ...f, user_id: e.target.value }))} data-testid="grant-user-id" /></div>
            <div><label className="jp-label">Plan</label>
              <select className="jp-input text-sm" value={grantForm.plan_id} onChange={e => setGrantForm(f => ({ ...f, plan_id: e.target.value }))} data-testid="grant-plan">
                {plans.map(p => <option key={p.plan_id} value={p.plan_id}>{p.name}</option>)}
              </select></div>
            <div><label className="jp-label">Durée (jours)</label>
              <input type="number" className="jp-input text-sm" value={grantForm.days} min="1" onChange={e => setGrantForm(f => ({ ...f, days: e.target.value }))} data-testid="grant-days" /></div>
            <div><label className="jp-label">Note (optionnel)</label>
              <input className="jp-input text-sm" value={grantForm.note} onChange={e => setGrantForm(f => ({ ...f, note: e.target.value }))} data-testid="grant-note" /></div>
            <button type="submit" className="jp-btn jp-btn-primary w-full" data-testid="grant-submit">Offrir</button>
          </form>
        </ModalShell>
      )}

      {editPlan && <EditPlanModal plan={editPlan} onClose={() => setEditPlan(null)} onSaved={() => { loadAll(); setEditPlan(null); toast.success('Plan modifié'); }} />}
    </div>
  );
}

function EditPlanModal({ plan, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: plan.name || '', tagline: plan.tagline || '',
    price_usd: plan.price_usd || '0', duration_days: plan.duration_days || 30,
    features: (plan.features || []).join('\n'),
    trial_eligible: !!plan.trial_eligible, is_active: !!plan.is_active,
  });
  const save = async (e) => {
    e.preventDefault();
    try {
      await axios.put(`${API}/api/admin/pro/plans/${plan.plan_id}`, {
        plan_id: plan.plan_id,
        name: form.name, tagline: form.tagline,
        price_usd: parseFloat(form.price_usd),
        duration_days: parseInt(form.duration_days),
        features: form.features.split('\n').map(s => s.trim()).filter(Boolean),
        trial_eligible: form.trial_eligible, is_active: form.is_active,
      }, { withCredentials: true });
      onSaved();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };
  return (
    <ModalShell title={`Modifier ${plan.name}`} onClose={onClose} wide>
      <form onSubmit={save} className="space-y-3" data-testid="edit-plan-form">
        <div className="grid grid-cols-2 gap-3">
          <div><label className="jp-label">Nom</label><input className="jp-input text-sm" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} data-testid="plan-name" /></div>
          <div><label className="jp-label">Prix USD</label><input type="number" step="0.01" className="jp-input text-sm" value={form.price_usd} onChange={e => setForm(f => ({ ...f, price_usd: e.target.value }))} data-testid="plan-price" /></div>
        </div>
        <div><label className="jp-label">Tagline</label><input className="jp-input text-sm" value={form.tagline} onChange={e => setForm(f => ({ ...f, tagline: e.target.value }))} data-testid="plan-tagline" /></div>
        <div><label className="jp-label">Durée (jours)</label><input type="number" min="1" className="jp-input text-sm" value={form.duration_days} onChange={e => setForm(f => ({ ...f, duration_days: e.target.value }))} data-testid="plan-duration" /></div>
        <div><label className="jp-label">Avantages (1 par ligne)</label>
          <textarea rows={6} className="jp-input text-sm" value={form.features} onChange={e => setForm(f => ({ ...f, features: e.target.value }))} data-testid="plan-features" /></div>
        <div className="flex gap-6">
          <ToggleField label="Éligible à l'essai" value={form.trial_eligible ? 'true' : 'false'} onChange={v => setForm(f => ({ ...f, trial_eligible: v === 'true' }))} testid="plan-trial" />
          <ToggleField label="Actif" value={form.is_active ? 'true' : 'false'} onChange={v => setForm(f => ({ ...f, is_active: v === 'true' }))} testid="plan-active" />
        </div>
        <button type="submit" className="jp-btn jp-btn-primary w-full" data-testid="plan-save">Enregistrer</button>
      </form>
    </ModalShell>
  );
}

/* ============== REFERRALS ADMIN ============== */
function ReferralsAdminTab() {
  const { t } = useTranslation();
  const [stats, setStats] = useState(null);
  const [refs, setRefs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState({ status: '', blocked: '', search: '' });

  const loadStats = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/referrals/stats`, { withCredentials: true });
      setStats(data);
    } catch {}
  }, []);
  const loadList = useCallback(async () => {
    const params = new URLSearchParams({ page, limit: 20 });
    Object.entries(filter).forEach(([k, v]) => { if (v !== '') params.append(k, v); });
    try {
      const { data } = await axios.get(`${API}/api/admin/referrals/list?${params}`, { withCredentials: true });
      setRefs(data.referrals); setTotal(data.total);
    } catch {}
  }, [page, filter]);
  useEffect(() => { loadStats(); }, [loadStats]);
  useEffect(() => { loadList(); }, [loadList]);

  const toggleBlock = async (r) => {
    try {
      if (r.blocked) {
        await axios.post(`${API}/api/admin/referrals/${r.id}/unblock`, {}, { withCredentials: true });
        toast.success('Débloqué');
      } else {
        const reason = window.prompt('Motif du blocage :', 'Fraude suspectée');
        if (!reason) return;
        await axios.post(`${API}/api/admin/referrals/${r.id}/block`, { reason }, { withCredentials: true });
        toast.success('Bloqué');
      }
      loadStats(); loadList();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  const sendReminders = async () => {
    try {
      const { data } = await axios.post(`${API}/api/admin/referrals/send-reminders`, {}, { withCredentials: true });
      toast.success(`${data.sent} rappel(s) envoyé(s)`);
      loadStats(); loadList();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  if (!stats) return <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;

  return (
    <div className="space-y-5 jp-animate-fadeIn" data-testid="referrals-admin-tab">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <MiniStat label="Total" value={stats.total} />
        <MiniStat label="Actifs" value={stats.active + stats.rewarded} accent="var(--jp-success)" />
        <MiniStat label="En attente" value={stats.pending} accent="#F59E0B" />
        <MiniStat label="Bloqués" value={stats.blocked} accent="var(--jp-error)" />
        <MiniStat label="Bonus versés (USD)" value={`$${parseFloat(stats.rewards_30d_usd).toFixed(2)} / 30j`} accent="#8B5CF6" />
      </div>

      {stats.inactive_referees_7d > 0 && (
        <div className="jp-alert jp-alert-info flex items-center gap-3" data-testid="inactive-alert">
          <Confetti size={18} />
          <div className="flex-1 text-sm">
            <strong>{stats.inactive_referees_7d}</strong> filleul(s) inactif(s) depuis +7 jours.
          </div>
          <button onClick={sendReminders} className="jp-btn jp-btn-primary jp-btn-sm" data-testid="send-reminders">
            Envoyer les rappels
          </button>
        </div>
      )}

      {/* Top referrers */}
      {stats.top_referrers?.length > 0 && (
        <div className="jp-card-elevated p-5">
          <h3 className="font-['Outfit'] text-lg font-bold mb-3">Top parrains</h3>
          <div className="space-y-2">
            {stats.top_referrers.map((r, i) => (
              <div key={r.user_id} className="flex items-center gap-3 text-sm" data-testid={`top-ref-${i}`}>
                <span className="w-6 text-center font-bold" style={{ color: 'var(--jp-primary)' }}>{i + 1}</span>
                <div className="jp-avatar jp-avatar-sm jp-avatar-primary">{r.name?.[0] || '?'}</div>
                <div className="flex-1"><strong>{r.name}</strong><div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{r.email}</div></div>
                <span className="jp-badge jp-badge-success">{r.active_count} actifs</span>
                <span className="text-xs font-semibold" style={{ color: '#10B981' }}>${parseFloat(r.earned_usd).toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Paginated list */}
      <div className="jp-card-elevated p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold">Parrainages ({total})</h3>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3" data-testid="ref-filters">
          <input className="jp-input text-xs" placeholder="Email (parrain ou filleul)" value={filter.search} onChange={e => { setFilter(f => ({ ...f, search: e.target.value })); setPage(1); }} />
          <select className="jp-input text-xs" value={filter.status} onChange={e => { setFilter(f => ({ ...f, status: e.target.value })); setPage(1); }}>
            <option value="">{t('admin.tous_statuts')}</option>
            <option value="pending">{t('admin.en_attente')}</option>
            <option value="active">{t('admin.actif')}</option>
            <option value="rewarded">{t('admin.recompense')}</option>
          </select>
          <select className="jp-input text-xs" value={filter.blocked} onChange={e => { setFilter(f => ({ ...f, blocked: e.target.value })); setPage(1); }}>
            <option value="">{t('admin.blocage_tous')}</option>
            <option value="true">{t('admin.bloques')}</option>
            <option value="false">{t('admin.non_bloques')}</option>
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="jp-table">
            <thead><tr><th>{t('admin.parrain')}</th><th>{t('admin.filleul')}</th><th>{t('admin.statut')}</th><th>IP</th><th>{t('admin.bonus_usd')}</th><th>Date</th><th>{t('admin.actions')}</th></tr></thead>
            <tbody>
              {refs.map(r => (
                <tr key={r.id} data-testid={`admin-ref-${r.id}`}>
                  <td><div className="font-medium text-sm">{r.referrer.name}</div><div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{r.referrer.email}</div></td>
                  <td><div className="font-medium text-sm">{r.referee.name}</div><div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{r.referee.email}</div></td>
                  <td>
                    <span className={`jp-badge ${r.blocked ? 'jp-badge-error' : r.status === 'active' || r.status === 'rewarded' ? 'jp-badge-success' : 'jp-badge-warning'}`}>
                      {r.blocked ? 'Bloqué' : r.status}
                    </span>
                    {r.blocked && r.blocked_reason && <div className="text-[10px] mt-0.5" style={{ color: 'var(--jp-error)' }}>{r.blocked_reason}</div>}
                  </td>
                  <td className="font-mono text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{r.ip_address || '-'}</td>
                  <td>${parseFloat(r.referrer_bonus_usd).toFixed(2)}</td>
                  <td className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{new Date(r.created_at).toLocaleString('fr-FR')}</td>
                  <td>
                    <button onClick={() => toggleBlock(r)}
                      className="jp-btn jp-btn-ghost jp-btn-sm"
                      style={{ color: r.blocked ? 'var(--jp-success)' : 'var(--jp-error)' }}
                      data-testid={`block-ref-${r.id}`}>
                      {r.blocked ? <><Check size={12} /> Débloquer</> : <><Prohibit size={12} /> Bloquer</>}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {total > 20 && (
          <div className="flex justify-center gap-2 pt-3">
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="jp-btn jp-btn-ghost jp-btn-sm">Préc.</button>
            <span className="px-4 py-1.5 text-sm" style={{ color: 'var(--jp-text-secondary)' }}>Page {page} / {Math.ceil(total / 20)}</span>
            <button disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)} className="jp-btn jp-btn-ghost jp-btn-sm">Suiv.</button>
          </div>
        )}
      </div>

      <div className="jp-alert jp-alert-info text-xs">
        <strong>{t('admin.configurer_les_paliers_montants_ant')}</strong> utilisez
        l'éditeur visuel ci-dessus pour les paliers, ou l'onglet <em>{t('admin.parametres')}</em>
        → groupe <em>{t('admin.parrainage')}</em> pour les bonus et l'anti-fraude.
      </div>

      {/* Visual tier editor (iter82) — admin can add/edit/reorder tiers
          without touching code. */}
      <ReferralTiersEditor />
    </div>
  );
}

/* ============== CONNECT ADMIN ============== */
function ConnectAdminTab() {
  const { t } = useTranslation();
  const [stats, setStats] = useState(null);
  const [list, setList] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState({ search: '', blocked: '', type: '', sponsored: '' });
  const [sponsorModal, setSponsorModal] = useState(null);

  const loadStats = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/connect/stats`, { withCredentials: true });
      setStats(data);
    } catch {}
  }, []);
  const loadList = useCallback(async () => {
    const params = new URLSearchParams({ page, limit: 20 });
    Object.entries(filter).forEach(([k, v]) => { if (v !== '') params.append(k, v); });
    try {
      const { data } = await axios.get(`${API}/api/admin/connect/hotspots?${params}`, { withCredentials: true });
      setList(data.hotspots); setTotal(data.total);
    } catch {}
  }, [page, filter]);
  useEffect(() => { loadStats(); }, [loadStats]);
  useEffect(() => { loadList(); }, [loadList]);

  const toggleBlock = async (h) => {
    try {
      if (h.is_blocked) {
        await axios.post(`${API}/api/admin/connect/hotspots/${h.hotspot_id}/unblock`, {}, { withCredentials: true });
        toast.success('Débloqué');
      } else {
        const reason = window.prompt('Motif du blocage :', 'Fraude suspectée');
        if (!reason) return;
        await axios.post(`${API}/api/admin/connect/hotspots/${h.hotspot_id}/block`, { reason }, { withCredentials: true });
        toast.success('Bloqué');
      }
      loadStats(); loadList();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  if (!stats) return <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;

  return (
    <div className="space-y-5 jp-animate-fadeIn" data-testid="connect-admin-tab">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <MiniStat label="Hotspots" value={stats.hotspots_total} />
        <MiniStat label="Actifs" value={stats.hotspots_active} accent="var(--jp-success)" />
        <MiniStat label="Bloqués" value={stats.hotspots_blocked} accent="var(--jp-error)" />
        <MiniStat label="Connexions 24h" value={stats.connections_24h} accent="var(--jp-primary)" />
        <MiniStat label="Versés 30j" value={`$${parseFloat(stats.rewarded_30d_usd).toFixed(2)}`} accent="#8B5CF6" />
      </div>

      {stats.top_hotspots?.length > 0 && (
        <div className="jp-card-elevated p-5">
          <h3 className="font-['Outfit'] text-lg font-bold mb-3">Top hotspots</h3>
          <div className="space-y-2">
            {stats.top_hotspots.map((r, i) => (
              <div key={r.hotspot_id} className="flex items-center gap-3 text-sm" data-testid={`top-hs-${i}`}>
                <span className="w-6 text-center font-bold" style={{ color: 'var(--jp-primary)' }}>{i + 1}</span>
                <div className="flex-1"><strong>{r.alias}</strong><div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{r.owner_name} · {r.owner_email}</div></div>
                <span className="jp-badge jp-badge-success">{r.connections} conn.</span>
                <span className="text-xs font-semibold" style={{ color: '#10B981' }}>${parseFloat(r.rewarded_usd).toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="jp-card-elevated p-5">
        <h3 className="font-['Outfit'] text-lg font-bold mb-3">Hotspots ({total})</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3" data-testid="connect-filters">
          <input className="jp-input text-xs" placeholder="Alias / email" value={filter.search}
            onChange={e => { setFilter(f => ({ ...f, search: e.target.value })); setPage(1); }} />
          <select className="jp-input text-xs" value={filter.blocked}
            onChange={e => { setFilter(f => ({ ...f, blocked: e.target.value })); setPage(1); }}>
            <option value="">{t('admin.blocage_tous')}</option>
            <option value="true">{t('admin.bloques')}</option>
            <option value="false">{t('admin.non_bloques')}</option>
          </select>
          <select className="jp-input text-xs" value={filter.type}
            onChange={e => { setFilter(f => ({ ...f, type: e.target.value })); setPage(1); }}>
            <option value="">{t('admin.tous_types')}</option>
            <option value="user">{t('admin.utilisateur')}</option>
            <option value="partner">{t('admin.partenaire')}</option>
          </select>
          <select className="jp-input text-xs" value={filter.sponsored}
            onChange={e => { setFilter(f => ({ ...f, sponsored: e.target.value })); setPage(1); }}>
            <option value="">{t('admin.tous_sponsoring')}</option>
            <option value="true">{t('admin.sponsorises')}</option>
            <option value="false">{t('admin.non_sponsorises')}</option>
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="jp-table">
            <thead><tr><th>{t('admin.alias')}</th><th>{t('admin.owner')}</th><th>Type</th><th>{t('admin.conn')}</th><th>{t('admin.gagne')}</th><th>{t('admin.statut')}</th><th>{t('admin.actions')}</th></tr></thead>
            <tbody>
              {list.map(h => (
                <tr key={h.hotspot_id} data-testid={`admin-hs-${h.hotspot_id}`}>
                  <td><div className="font-medium">{h.alias}</div><div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{h.address}</div></td>
                  <td className="text-xs">{h.owner?.name}</td>
                  <td><span className={`jp-badge ${h.is_sponsored ? '' : h.type === 'partner' ? 'jp-badge-warning' : 'jp-badge-neutral'}`}
                    style={h.is_sponsored ? { background: 'linear-gradient(135deg,#F59E0B,#EC4899)', color: 'white' } : {}}>
                    {h.is_sponsored ? `Partenaire ${h.sponsor_name}` : h.type}
                  </span></td>
                  <td>{h.total_connections}</td>
                  <td className="text-xs">${parseFloat(h.total_rewarded_usd).toFixed(2)}</td>
                  <td><span className={`jp-badge ${h.is_blocked ? 'jp-badge-error' : h.is_active ? 'jp-badge-success' : 'jp-badge-neutral'}`}>
                    {h.is_blocked ? 'Bloqué' : h.is_active ? 'Actif' : 'Inactif'}
                  </span></td>
                  <td>
                    <div className="flex items-center gap-1">
                      <button onClick={() => setSponsorModal(h)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid={`sponsor-${h.hotspot_id}`}>
                        <Crown size={12} /> Sponsor
                      </button>
                      <button onClick={() => toggleBlock(h)} className="jp-btn jp-btn-ghost jp-btn-sm"
                        style={{ color: h.is_blocked ? 'var(--jp-success)' : 'var(--jp-error)' }}
                        data-testid={`block-hs-${h.hotspot_id}`}>
                        {h.is_blocked ? <><Check size={12} /> Débloquer</> : <><Prohibit size={12} /> Bloquer</>}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {total > 20 && (
          <div className="flex justify-center gap-2 pt-3">
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="jp-btn jp-btn-ghost jp-btn-sm">Préc.</button>
            <span className="px-4 py-1.5 text-sm">Page {page} / {Math.ceil(total / 20)}</span>
            <button disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)} className="jp-btn jp-btn-ghost jp-btn-sm">Suiv.</button>
          </div>
        )}
      </div>

      {sponsorModal && <SponsorModal hotspot={sponsorModal} onClose={() => setSponsorModal(null)}
        onSaved={() => { setSponsorModal(null); loadList(); toast.success('Sponsoring mis à jour'); }} />}
    </div>
  );
}

function SponsorModal({ hotspot, onClose, onSaved }) {
  const [name, setName] = useState(hotspot.sponsor_name || '');
  const [active, setActive] = useState(!!hotspot.is_sponsored);
  const submit = async (e) => {
    e.preventDefault();
    await axios.post(`${API}/api/admin/connect/hotspots/${hotspot.hotspot_id}/sponsor`,
      { sponsor_name: name, is_sponsored: active }, { withCredentials: true });
    onSaved();
  };
  return (
    <ModalShell title={`Sponsoring — ${hotspot.alias}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3" data-testid="sponsor-form">
        <div><label className="jp-label">Nom du sponsor (ex: Café XYZ)</label>
          <input className="jp-input text-sm" value={name} onChange={e => setName(e.target.value)} data-testid="sponsor-name" /></div>
        <ToggleField label="Sponsoring actif" value={active ? 'true' : 'false'} onChange={v => setActive(v === 'true')} testid="sponsor-active" />
        <button type="submit" className="jp-btn jp-btn-primary w-full" data-testid="sponsor-submit">Enregistrer</button>
      </form>
    </ModalShell>
  );
}

/* ============== AUDIT ============== */
function AuditTab() {
  const { t } = useTranslation();
  const [logs, setLogs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/audit-logs?page=${page}&limit=50`, { withCredentials: true });
      setLogs(data.logs); setTotal(data.total);
    } catch {}
  }, [page]);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="jp-card jp-animate-fadeIn" data-testid="audit-tab">
      <div className="overflow-x-auto">
        <table className="jp-table">
          <thead><tr><th>{t('admin.action')}</th><th>{t('admin.admin')}</th><th>{t('admin.resource')}</th><th>{t('admin.details')}</th><th>Date</th></tr></thead>
          <tbody>
            {logs.map(log => (
              <tr key={log.id}>
                <td><span className="jp-badge jp-badge-primary">{log.action}</span></td>
                <td className="font-mono text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{log.user_id?.slice(0, 16) || '-'}</td>
                <td>{log.resource}</td>
                <td className="text-[11px] max-w-xs truncate" style={{ color: 'var(--jp-text-muted)' }}>{typeof log.details === 'string' ? log.details : JSON.stringify(log.details)}</td>
                <td className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{new Date(log.created_at).toLocaleString('fr-FR')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > 50 && (
        <div className="flex justify-center gap-2 p-4 border-t" style={{ borderColor: 'var(--jp-border)' }}>
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="jp-btn jp-btn-ghost jp-btn-sm">Préc.</button>
          <span className="px-4 py-1.5 text-sm">Page {page} / {Math.ceil(total / 50)}</span>
          <button disabled={page * 50 >= total} onClick={() => setPage(p => p + 1)} className="jp-btn jp-btn-ghost jp-btn-sm">Suiv.</button>
        </div>
      )}
    </div>
  );
}

/* ============== SHARED ============== */
function ModalShell({ title, children, onClose, wide }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }} data-testid="modal-shell">
      <div className={`jp-card-elevated ${wide ? 'max-w-2xl' : 'max-w-md'} w-full p-6 jp-animate-scaleIn`}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-['Outfit'] text-lg font-bold">{title}</h3>
          <button onClick={onClose} className="p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }} data-testid="modal-close"><X size={18} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

function ToggleField({ label, value, onChange, testid }) {
  const on = value === 'true' || value === true;
  return (
    <div className="flex items-center justify-between">
      <label className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{label}</label>
      <button type="button" onClick={() => onChange(on ? 'false' : 'true')} data-testid={testid}
        className="relative w-12 h-6 rounded-full transition-colors"
        style={{ background: on ? 'var(--jp-success)' : '#D1D5DB' }}>
        <div className="absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform"
          style={{ transform: on ? 'translateX(26px)' : 'translateX(2px)' }} />
      </button>
    </div>
  );
}

function MiniStat({ label, value, accent }) {
  return (
    <div className="p-3 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
      <div className="text-[10px] uppercase tracking-wider font-bold font-['Manrope'] mb-1" style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
      <div className="font-['Outfit'] text-lg font-extrabold" style={{ color: accent || 'var(--jp-text)' }}>{value}</div>
    </div>
  );
}


/* ============== MARKETPLACE DISPUTES (iter177) ============== */
function MarketplaceDisputesAdminTab({ onAction, setMessage }) {
  const { t } = useTranslation();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [resolveOrder, setResolveOrder] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const { data } = await axios.get(
        `${API}/api/marketplace/admin/orders/disputes?limit=50`,
        { withCredentials: true });
      setItems(data.items || []);
    } catch (e) {
      setError(e.response?.data?.detail || 'Erreur de chargement');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-4" data-testid="admin-mkt-disputes-tab">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-['Outfit'] text-xl font-bold"
            style={{ color: 'var(--jp-text)' }}>
            ⚖️ Litiges Marketplace
          </h2>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            File d'attente — chaque litige doit être tranché sous 48h. Décisions :
            libération vendeur, remboursement intégral, ou partage.
          </p>
        </div>
        <button onClick={load} className="jp-btn jp-btn-ghost jp-btn-sm"
          data-testid="admin-mkt-disputes-refresh">
          ↻ Actualiser
        </button>
      </div>

      {error && (
        <div className="p-3 rounded-lg text-sm"
          style={{ background: '#FEE2E2', color: '#991B1B' }}>{error}</div>
      )}
      {loading ? (
        <div className="text-center py-8 text-sm"
          style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>
      ) : items.length === 0 ? (
        <div className="jp-card p-8 text-center">
          <div className="text-3xl mb-2">✅</div>
          <p className="font-['Outfit'] font-bold"
            style={{ color: 'var(--jp-text)' }}>{t('admin.aucun_litige_en_cours')}</p>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            Les commandes en escrow sans litige se libèrent automatiquement.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((d) => (
            <div key={d.order_id} className="jp-card p-4"
              data-testid={`dispute-row-${d.order_id}`}>
              <div className="flex flex-wrap items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-['Outfit'] font-bold"
                      style={{ color: 'var(--jp-text)' }}>
                      {d.product_title}
                    </span>
                    <span className="jp-badge"
                      style={{ background: '#FEF3C7', color: '#92400E', fontSize: '10px' }}>
                      ⚖️ Litige
                    </span>
                  </div>
                  <p className="text-[11px] mt-1"
                    style={{ color: 'var(--jp-text-muted)' }}>
                    Acheteur : <strong>{d.buyer_email}</strong> ·
                    Vendeur : <strong>{d.seller_email}</strong>
                  </p>
                  <p className="text-[11px]"
                    style={{ color: 'var(--jp-text-muted)' }}>
                    Ouvert le {d.dispute_opened_at
                      ? new Date(d.dispute_opened_at).toLocaleString('fr-FR')
                      : '—'}
                    {' · '}Order #{d.order_id}
                  </p>
                  {d.dispute_reason && (
                    <div className="mt-2 p-2 rounded text-xs"
                      style={{ background: '#FEF2F2', color: '#7F1D1D' }}>
                      <strong>{t('admin.motif')}</strong> {d.dispute_reason}
                    </div>
                  )}
                </div>
                <div className="text-right shrink-0">
                  <div className="font-['Outfit'] font-extrabold text-lg"
                    style={{ color: 'var(--jp-primary)' }}>
                    {parseFloat(d.amount).toFixed(2)} USD
                  </div>
                  <div className="text-[10px]"
                    style={{ color: 'var(--jp-text-muted)' }}>
                    Commission {d.commission_pct}%
                  </div>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 justify-end">
                <button onClick={() => setResolveOrder({ ...d, decision: 'release_seller' })}
                  className="jp-btn jp-btn-sm"
                  style={{ background: '#10B981', color: 'white' }}
                  data-testid={`dispute-release-${d.order_id}`}>
                  💸 Libérer vendeur
                </button>
                <button onClick={() => setResolveOrder({ ...d, decision: 'refund_buyer' })}
                  className="jp-btn jp-btn-sm"
                  style={{ background: '#0EA5E9', color: 'white' }}
                  data-testid={`dispute-refund-${d.order_id}`}>
                  ↩️ Rembourser acheteur
                </button>
                <button onClick={() => setResolveOrder({ ...d, decision: 'split' })}
                  className="jp-btn jp-btn-sm"
                  style={{ background: '#8B5CF6', color: 'white' }}
                  data-testid={`dispute-split-${d.order_id}`}>
                  ⚖️ Partage
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {resolveOrder && (
        <DisputeResolveModal
          order={resolveOrder}
          initialDecision={resolveOrder.decision}
          onClose={() => setResolveOrder(null)}
          onResolved={(msg) => {
            setMessage && setMessage(msg);
            setResolveOrder(null);
            load();
            onAction && onAction();
          }} />
      )}
    </div>
  );
}

function DisputeResolveModal({ order, initialDecision, onClose, onResolved }) {
  const { t } = useTranslation();
  const [decision, setDecision] = useState(initialDecision || 'release_seller');
  const [sellerShare, setSellerShare] = useState(
    (parseFloat(order.amount) / 2).toFixed(2));
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const amount = parseFloat(order.amount);
  const pct = parseFloat(order.commission_pct || '2');
  const breakdown = (() => {
    if (decision === 'release_seller') {
      const fee = (amount * pct / 100).toFixed(2);
      return { seller: (amount - parseFloat(fee)).toFixed(2), buyer: '0.00', fee };
    }
    if (decision === 'refund_buyer') {
      return { seller: '0.00', buyer: amount.toFixed(2), fee: '0.00' };
    }
    const s = Math.max(0, Math.min(amount, parseFloat(sellerShare) || 0));
    const fee = (s * pct / 100).toFixed(2);
    return {
      seller: (s - parseFloat(fee)).toFixed(2),
      buyer: (amount - s).toFixed(2),
      fee,
    };
  })();

  const submit = async () => {
    if (submitting) return;
    setSubmitting(true); setError('');
    try {
      const payload = { decision, notes };
      if (decision === 'split') payload.seller_share_usd = parseFloat(sellerShare) || 0;
      const { data } = await axios.post(
        `${API}/api/marketplace/admin/orders/${order.order_id}/resolve`,
        payload, { withCredentials: true });
      onResolved(data.message || 'Litige résolu');
    } catch (e) {
      setError(e.response?.data?.detail || 'Erreur résolution');
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.6)' }} onClick={onClose}
      data-testid="dispute-resolve-modal">
      <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full overflow-hidden"
        onClick={(e) => e.stopPropagation()}>
        <div className="p-5 border-b"
          style={{ background: 'linear-gradient(135deg,#7C3AED,#EC4899)', color: 'white' }}>
          <div className="font-['Outfit'] font-bold text-lg">
            ⚖️ Résolution litige #{order.order_id.slice(0, 10)}
          </div>
          <div className="text-xs opacity-90 mt-1">
            {order.product_title} · {amount.toFixed(2)} USD
          </div>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="text-xs font-bold uppercase tracking-wider"
              style={{ color: 'var(--jp-text-muted)' }}>{t('admin.decision')}</label>
            <div className="grid grid-cols-3 gap-2 mt-2">
              {[
                { v: 'release_seller', l: '💸 Libérer', c: '#10B981' },
                { v: 'refund_buyer',   l: '↩️ Refund',  c: '#0EA5E9' },
                { v: 'split',          l: '⚖️ Split',   c: '#8B5CF6' },
              ].map(o => (
                <button key={o.v}
                  onClick={() => setDecision(o.v)}
                  data-testid={`dispute-decision-${o.v}`}
                  className="px-2 py-2 rounded-lg text-xs font-bold border-2 transition"
                  style={{
                    borderColor: decision === o.v ? o.c : 'var(--jp-border)',
                    background: decision === o.v ? `${o.c}15` : 'white',
                    color: decision === o.v ? o.c : 'var(--jp-text)',
                  }}>
                  {o.l}
                </button>
              ))}
            </div>
          </div>
          {decision === 'split' && (
            <div>
              <label className="text-xs font-bold uppercase tracking-wider"
                style={{ color: 'var(--jp-text-muted)' }}>
                Part vendeur (USD)
              </label>
              <input type="number" step="0.01" min="0" max={amount}
                value={sellerShare}
                onChange={e => setSellerShare(e.target.value)}
                data-testid="dispute-seller-share"
                className="jp-input mt-1 w-full" />
            </div>
          )}
          <div>
            <label className="text-xs font-bold uppercase tracking-wider"
              style={{ color: 'var(--jp-text-muted)' }}>
              Notes internes (admin)
            </label>
            <textarea value={notes} onChange={e => setNotes(e.target.value)}
              data-testid="dispute-notes"
              rows={3} className="jp-input mt-1 w-full text-sm"
              placeholder={t('admin.raison_de_la_decision_visible_dans')} />
          </div>
          <div className="p-3 rounded-lg text-sm"
            style={{ background: '#F9FAFB', color: 'var(--jp-text)' }}>
            <div className="font-bold mb-1">📊 Aperçu</div>
            <div className="text-xs space-y-1">
              <div>{t('admin.net_vendeur')}<strong>{breakdown.seller} USD</strong></div>
              <div>{t('admin.remboursement_acheteur')}<strong>{breakdown.buyer} USD</strong></div>
              <div>Commission JAPAP ({pct}%) : <strong>{breakdown.fee} USD</strong></div>
            </div>
          </div>
          {error && (
            <div className="text-xs p-2 rounded"
              style={{ background: '#FEE2E2', color: '#991B1B' }}>{error}</div>
          )}
        </div>
        <div className="p-4 border-t flex gap-2"
          style={{ background: 'var(--jp-bg-2,#fafafa)' }}>
          <button onClick={onClose}
            className="jp-btn jp-btn-ghost flex-1"
            data-testid="dispute-resolve-cancel">Annuler</button>
          <button onClick={submit} disabled={submitting}
            data-testid="dispute-resolve-submit"
            className="jp-btn jp-btn-primary flex-1">
            {submitting ? 'Résolution…' : 'Confirmer la décision'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ============== ADMIN FOOTER REVENUE WIDGET (iter178) ============== */
function AdminFooterRevenueWidget() {
  const [data, setData] = useState(null);

  useEffect(() => {
    const load = async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/admin/marketplace/revenue-summary?days=30`,
          { withCredentials: true });
        setData(data);
      } catch { /* silent — widget is non-critical */ }
    };
    load();
    const t = setInterval(load, 60_000); // refresh every minute
    return () => clearInterval(t);
  }, []);

  if (!data) return null;
  const total = parseFloat(data.total_usd || 0);
  const comm = parseFloat(data.commissions_usd || 0);
  const boosts = parseFloat(data.boosts_usd || 0);
  const held = parseFloat(data.total_held_usd || 0);

  return (
    <div className="fixed bottom-0 left-0 right-0 z-30 border-t backdrop-blur-md jp-safe-bottom"
      style={{
        background: 'rgba(17,24,39,0.92)',
        borderColor: 'rgba(255,255,255,0.1)',
      }}
      data-testid="admin-footer-revenue">
      <div className="max-w-7xl mx-auto px-4 py-2 flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-4 flex-wrap text-xs">
          <span className="font-['Outfit'] font-bold text-white">
            💰 Revenus JAPAP escrow (30j)
          </span>
          <span className="text-emerald-300">
            commissions: <strong data-testid="footer-rev-commissions">${comm.toFixed(2)}</strong>
            <span className="opacity-60"> · {data.commissions_count}</span>
          </span>
          <span className="text-amber-300">
            boosts: <strong data-testid="footer-rev-boosts">${boosts.toFixed(2)}</strong>
            <span className="opacity-60"> · {data.boosts_count}</span>
          </span>
          <span className="text-violet-300">
            total: <strong data-testid="footer-rev-total">${total.toFixed(2)}</strong>
          </span>
          {data.active_holds > 0 && (
            <span className="text-cyan-300">
              🔒 escrow actif: <strong>${held.toFixed(2)}</strong> ({data.active_holds})
            </span>
          )}
          {data.active_disputes > 0 && (
            <span className="text-red-300 font-bold animate-pulse"
              data-testid="footer-rev-disputes">
              ⚖️ {data.active_disputes} litige(s) à arbitrer
            </span>
          )}
        </div>
        <a href="/admin?tab=revenue"
          data-testid="footer-rev-link"
          className="text-xs text-white/80 hover:text-white underline whitespace-nowrap">
          Voir le tableau de bord →
        </a>
      </div>
    </div>
  );
}

