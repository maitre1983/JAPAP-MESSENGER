/**
 * CallHistoryPage — /calls
 *
 * Persistent call history — communication memory layer.
 * Shows past audio/video/group calls with status, duration, participants,
 * direct callback and access to the AI summary (if already generated).
 *
 * Data source: GET /api/calls/history (unified legacy + LiveKit sessions).
 * Realtime: refetch on Socket.io `call_ended` for the current user.
 *
 * Mobile-first:
 *   - Single scrollable list grouped by date (Aujourd'hui / Hier / plus ancien)
 *   - Each row: avatar + name + direction icon + type badge + duration +
 *     relative timestamp + inline actions (☎️, 📄, 🔗)
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Phone, PhoneIncoming, PhoneOutgoing, PhoneX, VideoCamera,
  UsersThree, ArrowLeft, FileText, ShareNetwork, ArrowCounterClockwise,
  Sparkle,
} from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import { useCall } from '@/context/CallContext';
import CallSummaryModal from '@/components/calls/CallSummaryModal';
import ReshareSummaryModal from '@/components/calls/ReshareSummaryModal';

const API = process.env.REACT_APP_BACKEND_URL;

export default function CallHistoryPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { startCall } = useCall();
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [openSummaryId, setOpenSummaryId] = useState(null);
  const [reshareSummaryId, setReshareSummaryId] = useState(null);

  const fetchHistory = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/calls/history?limit=100`, { withCredentials: true });
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      toast.error('Impossible de charger l\'historique des appels.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  const grouped = useMemo(() => groupByDay(items), [items]);

  const handleCallback = (item) => {
    if (item.kind === 'group') {
      if (item.conv_id) navigate(`/chat?c=${item.conv_id}`);
      return;
    }
    const peer = item.other;
    if (!peer?.user_id) return;
    startCall({
      user_id: peer.user_id,
      name: peer.name,
      avatar: peer.avatar,
      type: item.type === 'video' ? 'video' : 'audio',
    });
  };

  return (
    <div className="min-h-screen pb-24" style={{ background: 'var(--jp-bg)', color: 'var(--jp-text)' }}
         data-testid="call-history-page">
      {/* Header */}
      <header className="sticky top-0 z-20 px-4 pt-safe pt-4 pb-3 flex items-center gap-3"
              style={{ background: 'var(--jp-bg)', borderBottom: '1px solid var(--jp-border)' }}>
        <button onClick={() => navigate(-1)}
                className="p-2 -ml-2 rounded-full hover:bg-white/5"
                data-testid="call-history-back" aria-label="Retour">
          <ArrowLeft size={20} />
        </button>
        <div className="flex-1">
          <h1 className="font-['Outfit'] font-extrabold text-lg leading-tight">Historique</h1>
          <p className="text-[11px] opacity-60">Vos appels récents</p>
        </div>
        <button onClick={fetchHistory}
                className="p-2 rounded-full hover:bg-white/5"
                data-testid="call-history-refresh" aria-label="Actualiser">
          <ArrowCounterClockwise size={18} />
        </button>
      </header>

      <main className="max-w-2xl mx-auto px-3 py-4">
        {loading && items.length === 0 && <ListSkeleton />}
        {!loading && items.length === 0 && <EmptyState onNewCall={() => navigate('/chat')} />}
        {grouped.map((section) => (
          <section key={section.label} className="mb-5" data-testid={`history-section-${section.key}`}>
            <h2 className="px-2 mb-2 text-[11px] font-bold uppercase tracking-[0.2em] opacity-60">
              {section.label}
            </h2>
            <ul className="flex flex-col gap-1">
              {section.items.map((it) => (
                <CallRow key={it.id} item={it}
                         meId={user?.user_id}
                         onCallback={() => handleCallback(it)}
                         onOpenSummary={() => setOpenSummaryId(it.session_id)}
                         onReshareSummary={() => setReshareSummaryId(it.session_id)} />
              ))}
            </ul>
          </section>
        ))}
      </main>

      {openSummaryId && (
        <CallSummaryModal sessionId={openSummaryId} onClose={() => setOpenSummaryId(null)} />
      )}
      {reshareSummaryId && (
        <ReshareSummaryModal sessionId={reshareSummaryId} onClose={() => setReshareSummaryId(null)} />
      )}
    </div>
  );
}

/* ───────────────── Sub-components ───────────────── */

function CallRow({ item, meId, onCallback, onOpenSummary, onReshareSummary }) {
  const { t } = useTranslation();
  const isGroup = item.kind === 'group';
  const isMissed = item.status === 'missed';
  const isRejected = item.status === 'rejected';
  const isVideo = item.type === 'video';
  const peer = item.other;
  const label = isGroup
    ? `Groupe · ${item.participants.length} personnes`
    : (peer?.name || 'Inconnu');
  const avatar = isGroup ? null : peer?.avatar;

  const DirIcon = isMissed ? PhoneX
    : isRejected ? PhoneX
    : item.direction === 'incoming' ? PhoneIncoming
    : PhoneOutgoing;
  const dirColor = isMissed || isRejected ? '#EF4444'
    : item.direction === 'incoming' ? '#10B981' : '#3B82F6';

  const canCallback = !isGroup && peer?.user_id && peer.user_id !== meId;

  return (
    <li className="flex items-center gap-3 p-3 rounded-xl transition-colors hover:bg-white/[0.04]"
        style={{ border: '1px solid var(--jp-border)' }}
        data-testid={`call-row-${item.id}`}>
      {/* Avatar */}
      {isGroup ? (
        <div className="w-11 h-11 rounded-full flex items-center justify-center shrink-0"
             style={{ background: 'rgba(247,147,26,0.15)', color: '#F7931A' }}>
          <UsersThree size={22} weight="fill" />
        </div>
      ) : avatar ? (
        <img src={avatar} alt=""
             className="w-11 h-11 rounded-full object-cover shrink-0"
             style={{ border: '1px solid var(--jp-border)' }} />
      ) : (
        <div className="w-11 h-11 rounded-full flex items-center justify-center shrink-0 font-bold text-sm"
             style={{ background: 'var(--jp-surface-secondary)' }}>
          {(peer?.name || '?').slice(0, 1).toUpperCase()}
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <DirIcon size={14} weight="bold" style={{ color: dirColor }} />
          <span className="font-semibold text-sm truncate" style={{ color: isMissed ? '#FCA5A5' : 'var(--jp-text)' }}>
            {label}
          </span>
          {isVideo && <VideoCamera size={13} weight="fill" style={{ color: '#A78BFA' }} />}
          {item.has_summary && (
            <span className="inline-flex items-center gap-0.5 text-[10px] font-bold px-1.5 py-0.5 rounded-full"
                  style={{ background: 'rgba(16,185,129,0.15)', color: '#6EE7B7' }}
                  data-testid={`call-row-summary-badge-${item.id}`}>
              <Sparkle size={9} weight="fill" /> IA
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[11px] opacity-60 mt-0.5">
          <span>{statusLabel(item)}</span>
          {item.duration > 0 && <span>· {formatDuration(item.duration)}</span>}
          <span>· {relativeTime(item.started_at)}</span>
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 shrink-0">
        {item.has_summary && (
          <>
            <button onClick={onOpenSummary}
                    className="p-2 rounded-full transition-colors hover:bg-white/10"
                    data-testid={`call-row-summary-${item.id}`}
                    aria-label={t('call_history.voir_le_resume')}>
              <FileText size={18} style={{ color: '#10B981' }} />
            </button>
            <button onClick={onReshareSummary}
                    className="p-2 rounded-full transition-colors hover:bg-white/10"
                    data-testid={`call-row-reshare-${item.id}`}
                    aria-label={t('call_history.repartager_le_resume')}>
              <ShareNetwork size={18} style={{ color: '#3B82F6' }} />
            </button>
          </>
        )}
        {canCallback && (
          <button onClick={onCallback}
                  className="p-2 rounded-full transition-colors hover:bg-white/10"
                  data-testid={`call-row-callback-${item.id}`}
                  aria-label={t('call_history.rappeler')}>
            {isVideo
              ? <VideoCamera size={20} weight="fill" style={{ color: '#F7931A' }} />
              : <Phone size={20} weight="fill" style={{ color: '#F7931A' }} />}
          </button>
        )}
      </div>
    </li>
  );
}

function ListSkeleton() {
  return (
    <ul className="flex flex-col gap-2" data-testid="call-history-loading">
      {[...Array(5)].map((_, i) => (
        <li key={i} className="flex items-center gap-3 p-3 rounded-xl"
            style={{ border: '1px solid var(--jp-border)' }}>
          <div className="w-11 h-11 rounded-full animate-pulse" style={{ background: 'var(--jp-surface-secondary)' }} />
          <div className="flex-1 flex flex-col gap-1.5">
            <div className="h-3 w-2/3 rounded animate-pulse" style={{ background: 'var(--jp-surface-secondary)' }} />
            <div className="h-2.5 w-1/3 rounded animate-pulse" style={{ background: 'var(--jp-surface-secondary)' }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function EmptyState({ onNewCall }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center" data-testid="call-history-empty">
      <div className="w-16 h-16 rounded-full flex items-center justify-center mb-4"
           style={{ background: 'rgba(247,147,26,0.15)' }}>
        <Phone size={30} weight="fill" style={{ color: '#F7931A' }} />
      </div>
      <h3 className="font-['Outfit'] font-bold text-lg mb-1">Aucun appel pour le moment</h3>
      <p className="text-sm opacity-60 max-w-xs mb-5">
        Vos appels audio, vidéo et de groupe apparaîtront ici avec leurs résumés IA.
      </p>
      <button onClick={onNewCall}
              className="px-5 py-2.5 rounded-full font-bold text-sm"
              style={{ background: 'linear-gradient(135deg, #F7931A 0%, #EC4899 100%)', color: 'white' }}
              data-testid="call-history-empty-cta">
        Lancer un appel
      </button>
    </div>
  );
}

/* ───────────────── Helpers ───────────────── */

function statusLabel(item) {
  if (item.status === 'missed') return 'Manqué';
  if (item.status === 'rejected') return 'Refusé';
  if (item.status === 'failed') return 'Échec';
  if (item.status === 'live') return 'En cours';
  if (item.status === 'ringing') return 'Sonnerie';
  return item.direction === 'outgoing' ? 'Sortant' : 'Entrant';
}

function formatDuration(sec) {
  const s = Math.max(0, Math.floor(sec));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return `${m}:${String(r).padStart(2, '0')}`;
  const h = Math.floor(m / 60);
  return `${h}h${String(m % 60).padStart(2, '0')}`;
}

function relativeTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return 'à l\'instant';
  if (diff < 3600) return `il y a ${Math.floor(diff / 60)}min`;
  if (diff < 86400) return `il y a ${Math.floor(diff / 3600)}h`;
  if (diff < 86400 * 7) return `il y a ${Math.floor(diff / 86400)}j`;
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' });
}

function groupByDay(items) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);

  const buckets = { today: [], yesterday: [], week: [], older: [] };
  for (const it of items) {
    const d = it.started_at ? new Date(it.started_at) : new Date(0);
    if (d >= today) buckets.today.push(it);
    else if (d >= yesterday) buckets.yesterday.push(it);
    else if (d >= weekAgo) buckets.week.push(it);
    else buckets.older.push(it);
  }
  const out = [];
  if (buckets.today.length) out.push({ key: 'today', label: "Aujourd'hui", items: buckets.today });
  if (buckets.yesterday.length) out.push({ key: 'yesterday', label: 'Hier', items: buckets.yesterday });
  if (buckets.week.length) out.push({ key: 'week', label: 'Cette semaine', items: buckets.week });
  if (buckets.older.length) out.push({ key: 'older', label: 'Plus ancien', items: buckets.older });
  return out;
}
