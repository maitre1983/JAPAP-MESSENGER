/**
 * MyTasksPage — /tasks
 *
 * Global cross-conversation action layer. Each task is a single action_item
 * extracted from a `call_summary` message where `who_user_id == me`.
 *
 * Data is derived live from `messages.structured_data.action_items[]`, so
 * there's no secondary "tasks" table to stay in sync. Real-time updates
 * arrive via Socket.io `message_updated` and we re-fetch (cheap query).
 *
 * Mobile-first:
 *   - Single scrollable list, no sidebar
 *   - Sticky filters (All / Pending / Done)
 *   - Tap any row to open the source conversation
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { useAuth } from '@/context/AuthContext';
import { useTranslation } from 'react-i18next';
import {
  CheckCircle, Circle, ListChecks, ChatCircle, CalendarBlank,
  Phone, Sparkle, FunnelSimple, ArrowRight,
} from '@phosphor-icons/react';
import io from 'socket.io-client';

const API = process.env.REACT_APP_BACKEND_URL;

export default function MyTasksPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [counts, setCounts] = useState({ pending: 0, done: 0, total: 0 });
  const [filter, setFilter] = useState('pending');     // 'all' | 'pending' | 'done'
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);

  const fetchTasks = useCallback(async (status = filter) => {
    setLoading(true);
    try {
      const [listResp, countResp] = await Promise.all([
        axios.get(`${API}/api/tasks/my?status=${status}`, { withCredentials: true }),
        axios.get(`${API}/api/tasks/my/count`, { withCredentials: true }),
      ]);
      setItems(listResp.data.items || []);
      setCounts(countResp.data || { pending: 0, done: 0, total: 0 });
    } catch (e) {
      toast.error('Impossible de charger vos tâches.');
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { fetchTasks(filter); }, [fetchTasks, filter]);

  // Realtime: refetch on any relevant message_updated — the Socket already
  // broadcasts structured_data updates to conv members.
  useEffect(() => {
    if (!user) return;
    const sock = io(API, {
      path: '/api/socket.io',
      reconnection: true,
    });
    const onUpdate = (data) => {
      // Fast path: patch the local list if the msg is already rendered
      const patchFn = (updated) => {
        setItems((prev) => {
          const sd = updated.structured_data;
          if (!sd?.action_items) return prev;
          const map = new Map(sd.action_items.map((it) => [it.id, it]));
          return prev.map((row) => {
            if (row.msg_id !== updated.msg_id) return row;
            const it = map.get(row.item_id);
            if (!it) return row;
            return {
              ...row,
              what: it.what,
              done: !!it.done,
              due: it.due || '',
              done_by_user_id: it.done_by_user_id || null,
              done_at: it.done_at || null,
              who_user_id: it.who_user_id || null,
              who_text: it.who_text || '',
            };
          });
        });
      };
      patchFn(data);
      // Also refresh counters (cheap)
      axios.get(`${API}/api/tasks/my/count`, { withCredentials: true })
        .then((r) => setCounts(r.data || counts)).catch(() => {});
    };
    sock.on('message_updated', onUpdate);
    sock.on('new_message', (msg) => {
      // A new call_summary assigned to me → refresh the list
      if (msg?.message_type === 'call_summary') fetchTasks(filter);
    });
    return () => { try { sock.disconnect(); } catch {} };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, filter]);

  const toggle = useCallback(async (task) => {
    if (busyId) return;
    setBusyId(task.item_id);
    const next = !task.done;
    // Optimistic
    setItems((prev) => prev.map((t) => t.item_id === task.item_id
      ? { ...t, done: next, done_by_user_id: next ? user.user_id : null }
      : t));
    setCounts((prev) => ({
      ...prev,
      pending: prev.pending + (next ? -1 : +1),
      done: prev.done + (next ? +1 : -1),
    }));
    try {
      await axios.patch(
        `${API}/api/calls/summary/action-items/${task.msg_id}/${task.item_id}`,
        { done: next }, { withCredentials: true });
    } catch (e) {
      // Rollback
      setItems((prev) => prev.map((t) => t.item_id === task.item_id
        ? { ...t, done: !next } : t));
      setCounts((prev) => ({
        ...prev,
        pending: prev.pending + (next ? +1 : -1),
        done: prev.done + (next ? -1 : +1),
      }));
      toast.error(e?.response?.data?.detail || 'Action impossible.');
    } finally {
      setBusyId(null);
    }
  }, [busyId, user?.user_id]);

  const visible = useMemo(() => items, [items]);

  return (
    <div className="min-h-full" style={{ background: 'var(--jp-bg)' }} data-testid="my-tasks-page">
      {/* Header */}
      <header className="sticky top-0 z-10 px-4 sm:px-6 py-4 border-b"
              style={{ background: 'var(--jp-surface, #fff)',
                       borderColor: 'var(--jp-border)' }}>
        <div className="flex items-center gap-2 mb-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center"
               style={{ background: 'linear-gradient(135deg, #0F056B 0%, #E01C2E 100%)' }}>
            <ListChecks size={18} weight="fill" color="white" />
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="font-['Outfit'] font-extrabold text-lg leading-tight" data-testid="my-tasks-title">
              Mes tâches
            </h1>
            <p className="text-[11px] opacity-60 font-['Manrope']">
              Toutes les tâches assignées issues de vos appels
            </p>
          </div>
          {counts.pending > 0 && (
            <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-bold"
                  style={{ background: 'rgba(224,28,46,0.12)', color: '#E01C2E' }}
                  data-testid="my-tasks-pending-badge">
              {counts.pending} en cours
            </span>
          )}
        </div>

        {/* Filter pills */}
        <div className="flex items-center gap-2" data-testid="my-tasks-filters">
          <FunnelSimple size={14} className="opacity-60" />
          {[
            { k: 'all', label: 'Tout', count: counts.total },
            { k: 'pending', label: t('my_tasks.a_faire'), count: counts.pending },
            { k: 'done', label: t('my_tasks.terminees'), count: counts.done },
          ].map(({ k, label, count }) => (
            <button key={k}
                    onClick={() => setFilter(k)}
                    data-testid={`my-tasks-filter-${k}`}
                    className="px-3 py-1.5 rounded-full text-xs font-bold transition-colors"
                    style={{
                      background: filter === k ? '#0F056B' : 'var(--jp-surface-secondary, #F1F5F9)',
                      color: filter === k ? 'white' : 'var(--jp-text-secondary)',
                    }}>
              {label}{count > 0 && (
                <span className="ml-1 opacity-80">· {count}</span>
              )}
            </button>
          ))}
        </div>
      </header>

      {/* Body */}
      <main className="max-w-2xl mx-auto px-3 sm:px-6 py-4 space-y-2">
        {loading && items.length === 0 && (
          <div className="flex justify-center py-10"><div className="jp-spinner" /></div>
        )}
        {!loading && visible.length === 0 && (
          <EmptyState filter={filter} />
        )}
        {visible.map((task) => (
          <TaskRow key={task.item_id}
                   task={task}
                   isBusy={busyId === task.item_id}
                   onToggle={() => toggle(task)}
                   onOpen={() => navigate(`/chat?conv=${task.conv_id}&msg=${task.msg_id}`)} />
        ))}
      </main>
    </div>
  );
}

/* ─────────── Row ─────────── */

function TaskRow({ task, isBusy, onToggle, onOpen }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-xl border overflow-hidden"
         style={{ background: 'var(--jp-surface, #fff)',
                  borderColor: 'var(--jp-border)' }}
         data-testid={`my-task-${task.item_id}`}>
      <div className="flex items-start gap-3 px-3 py-3">
        <button
          type="button"
          disabled={isBusy}
          onClick={onToggle}
          data-testid={`my-task-${task.item_id}-toggle`}
          aria-label={task.done ? t('my_tasks.marquer_comme_a_faire') : t('my_tasks.marquer_comme_terminee')}
          className="shrink-0 mt-0.5 transition-transform active:scale-90">
          {task.done
            ? <CheckCircle size={22} weight="fill" style={{ color: '#10B981' }} />
            : <Circle size={22} weight="bold" style={{ color: 'var(--jp-text-muted)' }} />}
        </button>
        <button type="button"
                onClick={onOpen}
                data-testid={`my-task-${task.item_id}-open`}
                className="flex-1 min-w-0 text-left">
          <p className={`text-sm font-['Manrope'] leading-snug ${task.done ? 'line-through opacity-50' : 'font-semibold'}`}
             style={{ color: 'var(--jp-text)' }}>
            {task.what || '—'}
          </p>
          <div className="flex items-center gap-2 mt-1 flex-wrap text-[11px]"
               style={{ color: 'var(--jp-text-muted)' }}>
            <span className="inline-flex items-center gap-1 truncate max-w-[180px]"
                  title={task.conv_title}>
              <ChatCircle size={12} weight="fill" />
              <span className="truncate">{task.conv_title || 'Conversation'}</span>
            </span>
            {task.due && (
              <span className="inline-flex items-center gap-1">
                <CalendarBlank size={12} /> {task.due}
              </span>
            )}
            {task.call_session_id && (
              <span className="inline-flex items-center gap-1">
                <Phone size={11} weight="fill" />
                depuis un appel
              </span>
            )}
          </div>
          {task.summary_preview && !task.done && (
            <p className="text-[11px] mt-1 opacity-60 line-clamp-1">
              <Sparkle size={10} className="inline mr-1" />
              {task.summary_preview}
            </p>
          )}
        </button>
        <button type="button"
                onClick={onOpen}
                className="shrink-0 p-1.5 rounded-full hover:bg-black/5 opacity-50 hover:opacity-100"
                aria-label={t('my_tasks.ouvrir_la_conversation')}
                data-testid={`my-task-${task.item_id}-goto`}>
          <ArrowRight size={14} weight="bold" />
        </button>
      </div>
    </div>
  );
}

/* ─────────── Empty state ─────────── */

function EmptyState({ filter }) {
  const { t } = useTranslation();
  const msg = filter === 'done'
    ? t('my_tasks.aucune_tache_terminee_pour_l_instan')
    : filter === 'pending'
      ? t('my_tasks.tout_est_a_jour_pas_de_tache_en_cou')
      : t('my_tasks.aucune_tache_activez_l_enregistreme');
  return (
    <div className="text-center py-14 px-4" data-testid="my-tasks-empty">
      <div className="w-16 h-16 rounded-full mx-auto flex items-center justify-center mb-3"
           style={{ background: 'var(--jp-primary-subtle)' }}>
        <ListChecks size={28} weight="duotone" style={{ color: '#0F056B' }} />
      </div>
      <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>
        {msg}
      </p>
    </div>
  );
}
