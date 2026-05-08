/**
 * CallSummaryMessageBubble — Rendered inside ChatPage when a message has
 * `message_type === 'call_summary'`.
 *
 * Displays:
 *   - Header: "Résumé d'appel" + sender
 *   - Summary text
 *   - Decisions (inline chips)
 *   - Action items as an interactive checklist
 *       • checkbox (toggle done)
 *       • assignee avatar + name (auto-matched user_id OR raw text)
 *       • "what" + optional "due"
 *       • small reassign menu (⋯) visible only to call participants
 *
 * Access control (enforced server-side):
 *   - Assignee  → can toggle THEIR items
 *   - Call participant (was in the call) → can toggle ANY item
 *   - Other conv members → read-only
 *
 * Live updates arrive via Socket.io event `message_updated` → parent updates the
 * message in state and re-renders this component with the new structured_data.
 */
import { useState, useMemo } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useAuth } from '@/context/AuthContext';
import {
  Sparkle, Check, CheckCircle, Circle, DotsThreeVertical, UserCircle,
  CalendarBlank, Phone, ListChecks,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function CallSummaryMessageBubble({ message, onPatched }) {
  const { user } = useAuth();
  const [reassignOpen, setReassignOpen] = useState(null); // item_id currently open
  const [busyItemId, setBusyItemId] = useState(null);

  const sd = message.structured_data || {};
  const actionItems = sd.action_items || [];
  const decisions = sd.decisions || [];
  const keyPoints = sd.key_points || [];
  const participantIds = sd.participant_ids || [];
  const wasCallParticipant = useMemo(
    () => participantIds.includes(user?.user_id),
    [participantIds, user?.user_id],
  );
  const total = actionItems.length;
  const done = actionItems.filter((it) => it.done).length;

  const canToggle = (item) =>
    item.who_user_id === user?.user_id || wasCallParticipant;

  const patchLocal = (updatedItem) => {
    const nextItems = actionItems.map((it) => (it.id === updatedItem.id ? updatedItem : it));
    onPatched?.(message.msg_id, { ...sd, action_items: nextItems });
  };

  const toggle = async (item) => {
    if (!canToggle(item) || busyItemId) return;
    setBusyItemId(item.id);
    const next = !item.done;
    // Optimistic update
    patchLocal({ ...item, done: next, done_by_user_id: next ? user.user_id : null,
                 done_at: next ? new Date().toISOString() : null });
    try {
      const { data } = await axios.patch(
        `${API}/api/calls/summary/action-items/${message.msg_id}/${item.id}`,
        { done: next }, { withCredentials: true });
      patchLocal(data.item);
    } catch (e) {
      // Rollback
      patchLocal(item);
      toast.error(e?.response?.data?.detail || 'Action impossible.');
    } finally {
      setBusyItemId(null);
    }
  };

  const reassignTo = async (item, newUserId) => {
    setBusyItemId(item.id);
    try {
      const { data } = await axios.patch(
        `${API}/api/calls/summary/action-items/${message.msg_id}/${item.id}/assign`,
        { user_id: newUserId }, { withCredentials: true });
      patchLocal(data.item);
      toast.success('Tâche réassignée');
      setReassignOpen(null);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Réassignation impossible.');
    } finally {
      setBusyItemId(null);
    }
  };

  return (
    <div
      data-testid={`call-summary-${message.msg_id}`}
      className="rounded-2xl overflow-hidden max-w-[380px] w-full"
      style={{
        background: 'var(--jp-surface, #fff)',
        border: '1px solid rgba(15,5,107,0.15)',
        boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
      }}
    >
      {/* Header band */}
      <div className="px-4 py-2.5 flex items-center gap-2"
           style={{ background: 'linear-gradient(90deg, rgba(15,5,107,0.06), rgba(224,28,46,0.06))' }}>
        <Sparkle size={16} weight="fill" style={{ color: '#E01C2E' }} />
        <span className="font-['Outfit'] font-bold text-sm tracking-tight"
              style={{ color: 'var(--jp-text)' }}>
          Résumé d'appel
        </span>
        {total > 0 && (
          <span className="ml-auto text-[11px] px-2 py-0.5 rounded-full inline-flex items-center gap-1 font-semibold"
                style={{ background: done === total
                  ? 'rgba(16,185,129,0.18)'
                  : 'rgba(15,5,107,0.1)',
                         color: done === total ? '#059669' : '#0F056B' }}
                data-testid={`call-summary-${message.msg_id}-progress`}>
            <ListChecks size={11} weight="bold" /> {done}/{total}
          </span>
        )}
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-3">
        {sd.summary && (
          <p className="text-sm font-['Manrope'] leading-relaxed"
             style={{ color: 'var(--jp-text)' }}
             data-testid={`call-summary-${message.msg_id}-text`}>
            {sd.summary}
          </p>
        )}

        {keyPoints.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {keyPoints.slice(0, 4).map((kp, i) => (
              <span key={i}
                    className="text-[11px] px-2 py-0.5 rounded-full font-medium truncate max-w-[160px]"
                    style={{ background: 'var(--jp-surface-secondary, #F1F5F9)',
                             color: 'var(--jp-text-secondary)' }}>
                {kp}
              </span>
            ))}
          </div>
        )}

        {decisions.length > 0 && (
          <div className="space-y-1">
            <p className="text-[10px] font-bold uppercase tracking-wider"
               style={{ color: 'var(--jp-text-muted)' }}>
              Décisions
            </p>
            {decisions.map((d, i) => (
              <p key={i} className="text-[13px] pl-3 border-l-2"
                 style={{ borderColor: '#0F056B', color: 'var(--jp-text)' }}>
                {d}
              </p>
            ))}
          </div>
        )}

        {actionItems.length > 0 && (
          <div className="space-y-1 pt-1">
            <p className="text-[10px] font-bold uppercase tracking-wider"
               style={{ color: 'var(--jp-text-muted)' }}>
              Tâches
            </p>
            <ul className="space-y-1.5" data-testid={`call-summary-${message.msg_id}-items`}>
              {actionItems.map((item) => (
                <ActionItemRow key={item.id}
                               item={item}
                               msgId={message.msg_id}
                               canToggle={canToggle(item)}
                               canReassign={wasCallParticipant}
                               isBusy={busyItemId === item.id}
                               onToggle={() => toggle(item)}
                               onOpenReassign={() => setReassignOpen(
                                 reassignOpen === item.id ? null : item.id)}
                               reassignOpen={reassignOpen === item.id}
                               participants={sd.participants || []}
                               participantIds={participantIds}
                               onReassignTo={(uid) => reassignTo(item, uid)} />
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-4 py-2 text-[10px] font-['Manrope'] flex items-center gap-1"
           style={{ borderTop: '1px solid var(--jp-border)',
                    color: 'var(--jp-text-muted)' }}>
        <Phone size={11} weight="fill" /> Issu d'un appel vocal · résumé IA
      </div>
    </div>
  );
}

function ActionItemRow({ item, msgId, canToggle, canReassign, isBusy, onToggle,
                        onOpenReassign, reassignOpen, participantIds, onReassignTo }) {
  const { user } = useAuth();
  const isMine = item.who_user_id === user?.user_id;

  return (
    <li className="relative">
      <div className={`flex items-start gap-2 py-1 px-1 rounded-lg ${canToggle ? 'hover:bg-black/[0.03] cursor-pointer' : ''}`}
           onClick={canToggle ? onToggle : undefined}
           data-testid={`call-summary-${msgId}-item-${item.id}`}>
        <button
          type="button"
          disabled={!canToggle || isBusy}
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          className="shrink-0 mt-0.5 transition-transform active:scale-90"
          data-testid={`call-summary-${msgId}-item-${item.id}-toggle`}
          aria-label={item.done ? 'Décocher' : 'Cocher'}
          style={{ opacity: canToggle ? 1 : 0.4 }}>
          {item.done
            ? <CheckCircle size={20} weight="fill" style={{ color: '#10B981' }} />
            : <Circle size={20} weight="bold" style={{ color: 'var(--jp-text-muted)' }} />}
        </button>
        <div className="flex-1 min-w-0">
          <p className={`text-[13px] leading-snug ${item.done ? 'line-through opacity-50' : ''}`}
             style={{ color: 'var(--jp-text)' }}>
            {item.what || '—'}
          </p>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            {item.who_text && (
              <span
                className={`inline-flex items-center gap-1 text-[11px] ${isMine ? 'font-bold' : 'font-medium'}`}
                style={{ color: isMine ? '#E01C2E' : 'var(--jp-text-secondary)' }}
                data-testid={`call-summary-${msgId}-item-${item.id}-who`}>
                <UserCircle size={12} weight={item.who_user_id ? 'fill' : 'regular'} />
                {isMine ? 'Vous' : item.who_text}
                {!item.who_user_id && (
                  <span className="text-[10px] opacity-60 italic">(non assigné)</span>
                )}
              </span>
            )}
            {item.due && (
              <span className="inline-flex items-center gap-1 text-[11px]"
                    style={{ color: 'var(--jp-text-muted)' }}>
                <CalendarBlank size={11} /> {item.due}
              </span>
            )}
            {item.done && item.done_by_user_id && item.done_by_user_id !== item.who_user_id && (
              <span className="text-[10px] italic" style={{ color: 'var(--jp-text-muted)' }}>
                terminée par un participant
              </span>
            )}
          </div>
        </div>
        {canReassign && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onOpenReassign(); }}
            className="shrink-0 p-1 rounded-full hover:bg-black/5 opacity-60 hover:opacity-100"
            data-testid={`call-summary-${msgId}-item-${item.id}-menu`}
            aria-label="Réassigner">
            <DotsThreeVertical size={16} weight="bold" />
          </button>
        )}
      </div>
      {reassignOpen && (
        <div
          className="absolute right-0 top-8 z-10 rounded-xl shadow-lg overflow-hidden"
          data-testid={`call-summary-${msgId}-item-${item.id}-reassign-menu`}
          style={{ background: 'var(--jp-surface, #fff)',
                   border: '1px solid var(--jp-border)',
                   minWidth: 180 }}>
          <div className="px-3 py-2 text-[10px] font-bold uppercase tracking-wider"
               style={{ background: 'var(--jp-surface-secondary, #F1F5F9)',
                        color: 'var(--jp-text-muted)' }}>
            Réassigner à
          </div>
          {participantIds.length === 0 && (
            <div className="px-3 py-2 text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
              Aucun autre participant
            </div>
          )}
          {participantIds.map((pid) => (
            <button key={pid}
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onReassignTo(pid); }}
                    className="w-full text-left px-3 py-2 text-[13px] hover:bg-black/[0.04] flex items-center gap-2"
                    data-testid={`call-summary-${msgId}-reassign-to-${pid}`}>
              <UserCircle size={14} weight="fill" style={{ color: '#0F056B' }} />
              {pid === user?.user_id ? 'Moi' : pid.slice(0, 12) + '…'}
              {item.who_user_id === pid && (
                <Check size={12} weight="bold" className="ml-auto" style={{ color: '#10B981' }} />
              )}
            </button>
          ))}
          <button type="button"
                  onClick={(e) => { e.stopPropagation(); onReassignTo(null); }}
                  className="w-full text-left px-3 py-2 text-[12px] italic hover:bg-black/[0.04]"
                  style={{ color: 'var(--jp-text-muted)' }}
                  data-testid={`call-summary-${msgId}-item-${item.id}-unassign`}>
            Retirer l'assignation
          </button>
        </div>
      )}
    </li>
  );
}
