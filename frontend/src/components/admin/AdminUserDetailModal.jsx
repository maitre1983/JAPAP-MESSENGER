// iter240k — Fiche Admin complète (7 onglets)
// 100% additif. Branché sur /api/admin/users/:user_id/detail.
import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  X, User, GameController, Receipt, IdentificationCard,
  ChatCircle, Prohibit, NotePencil, Bell, ArrowsClockwise,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const TABS = [
  { id: 'overview',     icon: User,                key: 'tab_overview' },
  { id: 'games',        icon: GameController,      key: 'tab_games' },
  { id: 'transactions', icon: Receipt,             key: 'tab_transactions' },
  { id: 'kyc',          icon: IdentificationCard,  key: 'tab_kyc' },
  { id: 'posts',        icon: ChatCircle,          key: 'tab_posts' },
  { id: 'restrictions', icon: Prohibit,            key: 'tab_restrictions' },
  { id: 'notes',        icon: NotePencil,          key: 'tab_notes' },
];

const fmtDate = (s) => { try { return s ? new Date(s).toLocaleString() : '—'; } catch { return '—'; } };
const fmtNum  = (n) => Number(n || 0).toLocaleString();

export default function AdminUserDetailModal({ userId, onClose }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState('overview');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/admin/users/${userId}/detail`, { withCredentials: true });
      setData(data);
    } catch (e) {
      toast.error(t('admin.user_detail.load_failed', { defaultValue: 'Échec du chargement' }));
    } finally { setLoading(false); }
  }, [userId, t]);
  useEffect(() => { load(); }, [load]);

  if (!userId) return null;

  return (
    <div className="app-modal-overlay" style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.6)', zIndex:60, display:'flex', alignItems:'center', justifyContent:'center', padding:12 }}>
      <div className="app-modal-shell" data-testid="admin-user-detail-modal" style={{ background:'var(--jp-bg)', borderRadius:16, width:'100%', maxWidth:980, maxHeight:'min(92vh, 92dvh)', display:'flex', flexDirection:'column', overflow:'hidden' }}>
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b" style={{ borderColor:'var(--jp-border)' }}>
          <div className="flex items-center gap-3">
            <div className="jp-avatar jp-avatar-md jp-avatar-primary">
              {data?.user?.first_name?.[0]?.toUpperCase() || '?'}
            </div>
            <div>
              <h2 className="text-lg font-semibold" data-testid="aud-user-name">
                {data?.user?.first_name || ''} {data?.user?.last_name || ''}
              </h2>
              <p className="text-xs" style={{ color:'var(--jp-text-muted)' }}>
                @{data?.user?.username || '…'} · {data?.user?.email || '—'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button className="jp-btn jp-btn-ghost jp-btn-sm" onClick={load} title={t('common.reload', { defaultValue:'Actualiser' })} data-testid="aud-reload">
              <ArrowsClockwise size={16} />
            </button>
            <button className="jp-btn jp-btn-ghost jp-btn-sm" onClick={onClose} data-testid="aud-close">
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Tab bar */}
        <div className="flex gap-1 overflow-x-auto px-3 py-2 border-b" style={{ borderColor:'var(--jp-border)' }}>
          {TABS.map(({ id, icon:Icon, key }) => (
            <button key={id} onClick={() => setTab(id)} data-testid={`aud-tab-${id}`}
              className={`jp-btn jp-btn-sm ${tab===id ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
              style={{ whiteSpace:'nowrap', display:'flex', alignItems:'center', gap:6 }}>
              <Icon size={14} /> {t(`admin.user_detail.${key}`)}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="app-modal-body" style={{ flex:1, overflow:'auto', padding:16 }}>
          {loading && !data && <div className="text-center text-sm py-8" style={{ color:'var(--jp-text-muted)' }}>{t('common.loading', { defaultValue:'Chargement…' })}</div>}
          {data && tab === 'overview'     && <OverviewTab data={data} t={t} />}
          {data && tab === 'games'        && <GamesTab data={data} t={t} userId={userId} reload={load} />}
          {data && tab === 'transactions' && <TransactionsTab data={data} t={t} />}
          {data && tab === 'kyc'          && <KycTab data={data} t={t} />}
          {data && tab === 'posts'        && <PostsTab data={data} t={t} />}
          {data && tab === 'restrictions' && <RestrictionsTab data={data} t={t} userId={userId} reload={load} />}
          {data && tab === 'notes'        && <NotesTab data={data} t={t} userId={userId} reload={load} />}
        </div>

        {/* Footer: send notif */}
        {data && (
          <SendNotifFooter userId={userId} t={t} />
        )}
      </div>
    </div>
  );
}

/* ──────────────── OVERVIEW ──────────────── */
function OverviewTab({ data, t }) {
  const u = data.user || {}; const w = data.wallet || {};
  const card = (label, value, testid) => (
    <div className="jp-card" style={{ padding:12 }} data-testid={testid}>
      <div className="text-[11px] uppercase tracking-wide" style={{ color:'var(--jp-text-muted)' }}>{label}</div>
      <div className="text-sm font-medium mt-1" style={{ wordBreak:'break-word' }}>{value || '—'}</div>
    </div>
  );
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {card(t('admin.user_detail.user_id'), u.user_id, 'aud-card-uid')}
        {card(t('admin.user_detail.username'), `@${u.username || ''}`)}
        {card(t('admin.user_detail.email'), u.email)}
        {card(t('admin.user_detail.phone'), u.phone_number)}
        {card(t('admin.user_detail.country'), u.country_code || u.country)}
        {card(t('admin.user_detail.language'), u.preferred_lang || u.language)}
        {card(t('admin.user_detail.role'), u.role)}
        {card(t('admin.user_detail.pro_type'), u.is_pro ? `${u.pro_type || ''} (${fmtDate(u.pro_expires_at)})` : '—')}
        {card(t('admin.user_detail.kyc_status'), data.kyc?.status || '—')}
        {card(t('admin.user_detail.is_active'), u.is_active ? t('admin.user_detail.yes', { defaultValue:'Oui' }) : t('admin.user_detail.no', { defaultValue:'Non' }))}
        {card(t('admin.user_detail.wallet_balance'), `${fmtNum(w.balance)} ${w.currency || ''}`)}
        {card(t('admin.user_detail.referrals'), `${data.referrals?.total_referred || 0} (${fmtNum(data.referrals?.total_commission)} USD)`)}
        {card(t('admin.user_detail.created_at'), fmtDate(u.created_at))}
        {card(t('admin.user_detail.last_seen'), fmtDate(u.last_seen))}
        {card(t('admin.user_detail.posts_total'), fmtNum(data.posts?.total_posts))}
      </div>
      {data.flags && data.flags.length > 0 && (
        <div className="jp-card" style={{ padding:12 }}>
          <h3 className="text-sm font-semibold mb-2">{t('admin.user_detail.flags')} ({data.flags.length})</h3>
          <ul className="text-xs space-y-1">
            {data.flags.slice(0,5).map((f, i) => <li key={i}>{fmtDate(f.created_at)} — {f.reason} <span style={{ color:'var(--jp-text-muted)' }}>[{f.status}]</span></li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ──────────────── GAMES ──────────────── */
function GamesTab({ data, t, userId, reload }) {
  const g = data.game_activity || {};
  const row = (key, total, won, last) => (
    <div className="jp-card" style={{ padding:12 }}>
      <div className="flex items-center justify-between">
        <h4 className="font-medium">{t(`admin.user_detail.game_${key}`)}</h4>
        <span className="text-[11px]" style={{ color:'var(--jp-text-muted)' }}>{fmtDate(last)}</span>
      </div>
      <div className="flex gap-6 mt-2 text-sm">
        <div><span style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.played')}: </span><b>{fmtNum(total)}</b></div>
        <div><span style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.won')}: </span><b>{fmtNum(won)}</b></div>
      </div>
    </div>
  );
  const onReset = async () => {
    if (!window.confirm(t('admin.user_detail.confirm_reset_limits'))) return;
    try {
      await axios.post(`${API}/api/admin/users/${userId}/reset-game-limits`, {}, { withCredentials:true });
      toast.success(t('admin.user_detail.reset_limits_done'));
      reload();
    } catch { toast.error(t('admin.user_detail.action_failed')); }
  };
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {row('quiz', g.quiz?.total_played, g.quiz?.total_won, g.quiz?.last_played_at)}
        {row('fortune_wheel', g.fortune_wheel?.total_played, g.fortune_wheel?.total_won, g.fortune_wheel?.last_played_at)}
        {row('mini_spin', g.mini_spin?.total_played, g.mini_spin?.total_won, g.mini_spin?.last_played_at)}
        <div className="jp-card" style={{ padding:12 }}>
          <h4 className="font-medium">{t('admin.user_detail.game_staking')}</h4>
          <div className="flex gap-6 mt-2 text-sm">
            <div><span style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.active_stakes')}: </span><b>{fmtNum(g.staking?.active_stakes)}</b></div>
            <div><span style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.total_staked')}: </span><b>{fmtNum(g.staking?.total_staked)}</b></div>
          </div>
        </div>
      </div>
      <button className="jp-btn jp-btn-secondary jp-btn-sm" onClick={onReset} data-testid="aud-reset-limits">
        {t('admin.user_detail.reset_limits')}
      </button>
    </div>
  );
}

/* ──────────────── TRANSACTIONS ──────────────── */
function TransactionsTab({ data, t }) {
  const txs = data.transactions || [];
  if (!txs.length) return <div className="text-sm text-center py-8" style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.no_transactions')}</div>;
  return (
    <div className="overflow-x-auto">
      <table className="jp-table text-sm w-full">
        <thead><tr>
          <th>{t('admin.user_detail.tx_date')}</th>
          <th>{t('admin.user_detail.tx_type')}</th>
          <th>{t('admin.user_detail.tx_amount')}</th>
          <th>{t('admin.user_detail.tx_status')}</th>
          <th>{t('admin.user_detail.tx_notes')}</th>
        </tr></thead>
        <tbody>
          {txs.map(tx => (
            <tr key={tx.tx_id} data-testid={`aud-tx-${tx.tx_id}`}>
              <td className="text-[11px]">{fmtDate(tx.created_at)}</td>
              <td>{tx.type}</td>
              <td>{fmtNum(tx.amount)} {tx.currency}</td>
              <td><span className={`jp-badge ${tx.status==='completed'?'jp-badge-success':tx.status==='failed'?'jp-badge-error':'jp-badge-neutral'}`}>{tx.status}</span></td>
              <td className="text-[11px]" style={{ color:'var(--jp-text-muted)' }}>{tx.notes || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ──────────────── KYC ──────────────── */
function KycTab({ data, t }) {
  const k = data.kyc || {};
  return (
    <div className="space-y-3">
      <div className="jp-card" style={{ padding:14 }}>
        <div className="flex items-center justify-between">
          <h4 className="font-medium">{t('admin.user_detail.kyc_status')}</h4>
          <span className={`jp-badge ${k.status==='approved'?'jp-badge-success':k.status==='rejected'?'jp-badge-error':'jp-badge-neutral'}`} data-testid="aud-kyc-status">{k.status || 'not_submitted'}</span>
        </div>
        <div className="mt-3 text-sm space-y-1">
          <div><span style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.kyc_submitted')}: </span>{fmtDate(k.submitted_at)}</div>
          <div><span style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.kyc_validated')}: </span>{fmtDate(k.validated_at)}</div>
          {k.rejection_reason && <div><span style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.kyc_reason')}: </span>{k.rejection_reason}</div>}
        </div>
      </div>
      <p className="text-xs" style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.kyc_hint')}</p>
    </div>
  );
}

/* ──────────────── POSTS ──────────────── */
function PostsTab({ data, t }) {
  const p = data.posts || {}; const cf = data.crowdfunding || {};
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <div className="jp-card" style={{ padding:14 }}>
        <h4 className="font-medium">{t('admin.user_detail.posts_section')}</h4>
        <div className="mt-2 text-sm space-y-1">
          <div>{t('admin.user_detail.posts_total')}: <b>{fmtNum(p.total_posts)}</b></div>
          <div>{t('admin.user_detail.posts_likes')}: <b>{fmtNum(p.total_likes_received)}</b></div>
          <div>{t('admin.user_detail.posts_last')}: <b>{fmtDate(p.last_post_at)}</b></div>
        </div>
      </div>
      <div className="jp-card" style={{ padding:14 }}>
        <h4 className="font-medium">{t('admin.user_detail.crowdfunding_section')}</h4>
        <div className="mt-2 text-sm space-y-1">
          <div>{t('admin.user_detail.cf_projects')}: <b>{fmtNum(cf.projects_submitted)}</b></div>
          <div>{t('admin.user_detail.cf_votes')}: <b>{fmtNum(cf.votes_cast)}</b></div>
        </div>
      </div>
    </div>
  );
}

/* ──────────────── RESTRICTIONS ──────────────── */
function RestrictionsTab({ data, t, userId, reload }) {
  const [type, setType] = useState('games');
  const [reason, setReason] = useState('');
  const [days, setDays] = useState('');
  const [busy, setBusy] = useState(false);

  const add = async () => {
    setBusy(true);
    try {
      await axios.post(`${API}/api/admin/users/${userId}/restrict`,
        { type, reason: reason || undefined, duration_days: days ? Number(days) : undefined },
        { withCredentials:true });
      toast.success(t('admin.user_detail.restrict_done'));
      setReason(''); setDays(''); reload();
    } catch { toast.error(t('admin.user_detail.action_failed')); }
    finally { setBusy(false); }
  };
  const lift = async (rType) => {
    setBusy(true);
    try {
      await axios.post(`${API}/api/admin/users/${userId}/unrestrict`,
        { type: rType, reason: 'lifted by admin' }, { withCredentials:true });
      toast.success(t('admin.user_detail.unrestrict_done'));
      reload();
    } catch { toast.error(t('admin.user_detail.action_failed')); }
    finally { setBusy(false); }
  };

  const active = (data.restrictions || []).filter(r => !r.lifted_at);
  const lifted = (data.restrictions || []).filter(r =>  r.lifted_at);

  return (
    <div className="space-y-4">
      <div className="jp-card" style={{ padding:14 }}>
        <h4 className="font-medium mb-2">{t('admin.user_detail.add_restriction')}</h4>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
          <select className="jp-input text-sm" value={type} onChange={e=>setType(e.target.value)} data-testid="aud-restrict-type">
            <option value="games">{t('admin.user_detail.r_games')}</option>
            <option value="wallet">{t('admin.user_detail.r_wallet')}</option>
            <option value="posts">{t('admin.user_detail.r_posts')}</option>
            <option value="all">{t('admin.user_detail.r_all')}</option>
          </select>
          <input className="jp-input text-sm" placeholder={t('admin.user_detail.r_reason')} value={reason} onChange={e=>setReason(e.target.value)} data-testid="aud-restrict-reason" />
          <input className="jp-input text-sm" type="number" min="1" placeholder={t('admin.user_detail.r_days')} value={days} onChange={e=>setDays(e.target.value)} data-testid="aud-restrict-days" />
          <button className="jp-btn jp-btn-primary jp-btn-sm" disabled={busy} onClick={add} data-testid="aud-restrict-submit">{t('admin.user_detail.apply')}</button>
        </div>
      </div>
      <div>
        <h4 className="font-medium text-sm mb-2">{t('admin.user_detail.active_restrictions')} ({active.length})</h4>
        {active.length === 0 && <p className="text-xs" style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.no_active_restrictions')}</p>}
        <ul className="space-y-1 text-xs">
          {active.map(r => (
            <li key={r.id} className="flex items-center justify-between gap-2 p-2 jp-card" data-testid={`aud-restrict-${r.id}`}>
              <span><b>{r.restriction_type}</b> — {r.reason || '—'} <span style={{ color:'var(--jp-text-muted)' }}>{fmtDate(r.created_at)} → {r.expires_at ? fmtDate(r.expires_at) : '∞'}</span></span>
              <button className="jp-btn jp-btn-ghost jp-btn-sm" onClick={()=>lift(r.restriction_type)} disabled={busy}>{t('admin.user_detail.lift')}</button>
            </li>
          ))}
        </ul>
      </div>
      {lifted.length > 0 && (
        <div>
          <h4 className="font-medium text-sm mb-2">{t('admin.user_detail.lifted_restrictions')} ({lifted.length})</h4>
          <ul className="space-y-1 text-xs">
            {lifted.slice(0,10).map(r => (
              <li key={r.id} className="p-2 jp-card" style={{ color:'var(--jp-text-muted)' }}>
                <b>{r.restriction_type}</b> — {r.reason || '—'} · {t('admin.user_detail.lifted_at')} {fmtDate(r.lifted_at)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ──────────────── NOTES ──────────────── */
function NotesTab({ data, t, userId, reload }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const add = async () => {
    const txt = note.trim(); if (!txt) return;
    setBusy(true);
    try {
      await axios.post(`${API}/api/admin/users/${userId}/notes`, { note: txt }, { withCredentials:true });
      setNote(''); toast.success(t('admin.user_detail.note_added')); reload();
    } catch { toast.error(t('admin.user_detail.action_failed')); }
    finally { setBusy(false); }
  };
  return (
    <div className="space-y-3">
      <div className="jp-card" style={{ padding:12 }}>
        <textarea className="jp-input text-sm w-full" rows={3} placeholder={t('admin.user_detail.note_placeholder')}
          value={note} onChange={e=>setNote(e.target.value)} data-testid="aud-note-input" />
        <div className="flex justify-end mt-2">
          <button className="jp-btn jp-btn-primary jp-btn-sm" disabled={busy || !note.trim()} onClick={add} data-testid="aud-note-submit">
            {t('admin.user_detail.add_note')}
          </button>
        </div>
      </div>
      {(data.notes || []).length === 0 && <p className="text-xs text-center py-4" style={{ color:'var(--jp-text-muted)' }}>{t('admin.user_detail.no_notes')}</p>}
      <ul className="space-y-2">
        {(data.notes || []).map(n => (
          <li key={n.id} className="jp-card p-3 text-sm" data-testid={`aud-note-${n.id}`}>
            <div className="text-[11px] mb-1" style={{ color:'var(--jp-text-muted)' }}>
              {n.admin_first_name || ''} {n.admin_last_name || ''} · {fmtDate(n.created_at)}
            </div>
            <div style={{ whiteSpace:'pre-wrap' }}>{n.note}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ──────────────── FOOTER SEND NOTIF ──────────────── */
function SendNotifFooter({ userId, t }) {
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const send = async () => {
    const txt = msg.trim(); if (!txt) return;
    setBusy(true);
    try {
      await axios.post(`${API}/api/admin/users/${userId}/send-notification`, { message: txt, type:'info' }, { withCredentials:true });
      toast.success(t('admin.user_detail.notif_sent'));
      setMsg('');
    } catch { toast.error(t('admin.user_detail.action_failed')); }
    finally { setBusy(false); }
  };
  return (
    <div className="border-t p-3 flex gap-2" style={{ borderColor:'var(--jp-border)' }}>
      <input className="jp-input text-sm flex-1" placeholder={t('admin.user_detail.notif_placeholder')}
        value={msg} onChange={e=>setMsg(e.target.value)} data-testid="aud-notif-input" />
      <button className="jp-btn jp-btn-secondary jp-btn-sm" disabled={busy || !msg.trim()} onClick={send} data-testid="aud-notif-send" style={{ display:'flex', alignItems:'center', gap:6 }}>
        <Bell size={14} /> {t('admin.user_detail.send_notif')}
      </button>
    </div>
  );
}
