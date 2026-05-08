/**
 * NotificationsPage — iter141fivex
 *
 * Bug-fix CEO :
 *   1) Les messages ne sont plus tronqués (`truncate` retiré, `whitespace-pre-line`
 *      pour préserver les retours à la ligne).
 *   2) Chaque notification est cliquable :
 *      - extraction du `deep_link` depuis le blob JSON `data` (FastAPI le
 *        renvoie comme dict ou string selon la version asyncpg)
 *      - navigation react-router vers /duel/:token, /post/:id, etc.
 *      - ouverture des liens externes via window.open (préfixe http(s)).
 *      - le `mark-as-read` continue de s'exécuter en arrière-plan.
 *   3) Bouton CTA "Ouvrir" visible quand la notif a un deep_link, pour
 *      souligner la zone cliquable.
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import {
  Bell, Check, CheckCircle, Wallet, ChatCircle, ShoppingBag, Crown, Megaphone,
  ArrowRight, Sword, Trophy,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const TYPE_ICONS = {
  money_received: Wallet, order_received: ShoppingBag, pro_activated: Crown,
  push: Megaphone, broadcast: Bell,
  duel_completed: Sword, duel_started: Sword, duel_rematch: Sword,
  duel_won: Trophy, duel_lost: Sword, duel_tie: Sword,
  default: Bell,
};
const TYPE_COLORS = {
  money_received: '#059669', order_received: '#0F056B', pro_activated: '#7C3AED',
  push: '#E01C2E', broadcast: '#4A90E2',
  duel_completed: '#FFD700', duel_started: '#FFD700', duel_rematch: '#E01C2E',
  duel_won: '#22c55e', duel_lost: '#E01C2E', duel_tie: '#9ca3af',
  default: 'var(--jp-text-muted)',
};

// iter141fivex — robust deep_link extractor.
// asyncpg returns JSONB as dict already; older inserts may store str(dict)
// or json.dumps(dict). Handle all three.
function extractDeepLink(notif) {
  const raw = notif?.data;
  if (!raw) return null;
  if (typeof raw === 'object' && raw.deep_link) return String(raw.deep_link);
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      if (parsed?.deep_link) return String(parsed.deep_link);
    } catch (_) { /* str(dict) like "{'deep_link': '/duel/x'}" — best-effort regex */ }
    const m = raw.match(/['"]deep_link['"]\s*:\s*['"]([^'"]+)['"]/);
    if (m) return m[1];
  }
  return null;
}

export default function NotificationsPage() {
  const [notifications, setNotifications] = useState([]);
  const [total, setTotal] = useState(0);
  const [unread, setUnread] = useState(0);
  const [page, setPage] = useState(1);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/push/notifications?page=${page}&limit=30`, { withCredentials: true });
      setNotifications(data.notifications);
      setTotal(data.total);
      setUnread(data.unread);
    } catch {}
  }, [page]);

  useEffect(() => { load(); }, [load]);

  const markRead = async (notifId) => {
    try {
      await axios.put(`${API}/api/push/read/${notifId}`, {}, { withCredentials: true });
      setNotifications(prev => prev.map(n => n.notif_id === notifId ? { ...n, is_read: true } : n));
      setUnread(prev => Math.max(0, prev - 1));
    } catch {}
  };

  const markAllRead = async () => {
    try {
      await axios.put(`${API}/api/push/read-all`, {}, { withCredentials: true });
      setNotifications(prev => prev.map(n => ({ ...n, is_read: true })));
      setUnread(0);
    } catch {}
  };

  const handleNotifClick = (n) => {
    if (!n.is_read) markRead(n.notif_id);
    const link = extractDeepLink(n);
    if (!link) return;
    if (/^https?:\/\//i.test(link)) {
      window.open(link, '_blank', 'noopener,noreferrer');
    } else {
      navigate(link);
    }
  };

  const timeSince = (dateStr) => {
    const s = Math.floor((new Date() - new Date(dateStr)) / 1000);
    if (s < 60) return "À l'instant";
    if (s < 3600) return `${Math.floor(s/60)}min`;
    if (s < 86400) return `${Math.floor(s/3600)}h`;
    return `${Math.floor(s/86400)}j`;
  };

  return (
    <div className="p-4 max-w-2xl mx-auto pb-24" data-testid="notifications-page">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-['Outfit'] font-extrabold" style={{ color: 'var(--jp-text)' }}>Notifications</h1>
          <p className="text-xs mt-1" style={{ color: 'var(--jp-text-muted)' }}>
            {total} au total {unread > 0 && <span style={{ color: '#E01C2E' }}>· {unread} non lues</span>}
          </p>
        </div>
        {unread > 0 && (
          <button onClick={markAllRead}
            className="flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-bold"
            style={{ background: 'var(--jp-primary)', color: '#fff' }}
            data-testid="mark-all-read-btn">
            <CheckCircle size={14} weight="bold" /> Tout lu
          </button>
        )}
      </div>

      <div className="space-y-2">
        {notifications.length === 0 && (
          <div className="text-center py-10 rounded-xl" style={{ background: 'var(--jp-surface-secondary)' }}>
            <Bell size={40} className="mx-auto mb-3" style={{ color: 'var(--jp-text-muted)', opacity: 0.3 }} />
            <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>Aucune notification</p>
          </div>
        )}
        {notifications.map(n => {
          const Icon = TYPE_ICONS[n.type] || TYPE_ICONS.default;
          const color = TYPE_COLORS[n.type] || TYPE_COLORS.default;
          const link = extractDeepLink(n);
          const isDuelLink = !!link && link.startsWith('/duel/');
          return (
            <button key={n.notif_id} onClick={() => handleNotifClick(n)}
              data-testid={`notif-${n.notif_id}`}
              className="w-full flex items-start gap-3 p-3 rounded-xl text-left transition-all hover:shadow-sm"
              style={{
                background: n.is_read ? 'transparent' : 'var(--jp-primary-subtle)',
                border: `1px solid ${n.is_read ? 'var(--jp-border)' : 'var(--jp-primary-muted)'}`,
                cursor: 'pointer',
              }}>
              <div className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0"
                   style={{ background: `${color}15` }}>
                <Icon size={16} weight="duotone" style={{ color }} />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-['Manrope'] font-semibold"
                   style={{ color: 'var(--jp-text)' }} data-testid="notif-title">
                  {n.title}
                </p>
                <p className="text-xs font-['Manrope'] mt-0.5"
                   style={{ color: 'var(--jp-text-secondary)', whiteSpace: 'pre-line', wordBreak: 'break-word' }}
                   data-testid="notif-message">
                  {n.message}
                </p>
                <div className="flex items-center justify-between mt-1.5">
                  <p className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{timeSince(n.created_at)}</p>
                  {link && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full"
                          data-testid="notif-cta"
                          style={{
                            background: isDuelLink ? 'rgba(255,215,0,0.15)' : 'rgba(15,5,107,0.10)',
                            color: isDuelLink ? '#B45309' : 'var(--jp-primary)',
                            border: isDuelLink ? '1px solid rgba(255,215,0,0.45)' : '1px solid rgba(15,5,107,0.20)',
                          }}>
                      {isDuelLink ? '⚔ Relever le défi' : 'Ouvrir'}
                      <ArrowRight size={10} weight="bold" />
                    </span>
                  )}
                </div>
              </div>
              {!n.is_read && <div className="w-2 h-2 rounded-full flex-shrink-0 mt-2" style={{ background: '#E01C2E' }} />}
            </button>
          );
        })}
      </div>
    </div>
  );
}
