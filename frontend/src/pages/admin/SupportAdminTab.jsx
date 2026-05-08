/**
 * SupportAdminTab — iter90
 *
 * Admin-side view of the support tickets. Filterable by status, paginated (50/page),
 * each ticket expands to show the full message + AI transcript + quick status update.
 */
import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Ticket, Robot, ArrowsClockwise, CaretDown, CaretUp,
  CheckCircle, Clock as ClockIcon, Circle,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const getStatuses = (t) => ([
  { id: '', label: 'Tous' },
  { id: 'open', label: 'Ouverts' },
  { id: 'in_progress', label: 'En cours' },
  { id: 'resolved', label: t('support_admin.resolus') },
  { id: 'closed', label: t('support_admin.fermes') },
]);

const getStatus_style = (t) => ({
  open:        { bg: '#FEF3C7', fg: '#9A6700', label: 'Ouvert' },
  in_progress: { bg: '#DBEAFE', fg: '#1E40AF', label: 'En cours' },
  resolved:    { bg: '#D1FAE5', fg: '#047857', label: t('support_admin.resolu') },
  closed:      { bg: '#E5E7EB', fg: '#374151', label: t('support_admin.ferme') },
});

export default function SupportAdminTab({ onAction }) {
  const { t } = useTranslation();
  const STATUSES = getStatuses(t);
  const STATUS_STYLE = getStatus_style(t);
  const [status, setStatus] = useState('open');
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const qs = status ? `?status=${status}&limit=100` : '?limit=100';
      const r = await axios.get(`${API}/api/support/admin/tickets${qs}`, { withCredentials: true });
      setItems(r.data.items || []);
      setTotal(r.data.total || 0);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Chargement impossible');
    } finally { setLoading(false); }
  }, [status]);

  useEffect(() => { load(); }, [load]);

  const updateStatus = async (ticketId, next) => {
    try {
      await axios.patch(
        `${API}/api/support/admin/tickets/${ticketId}/status`,
        { status: next },
        { withCredentials: true },
      );
      toast.success(`Ticket ${ticketId} → ${next}`);
      load();
      onAction?.();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Échec MAJ statut');
    }
  };

  return (
    <div className="space-y-4" data-testid="support-admin-tab">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 className="font-['Outfit'] text-xl font-bold" style={{ color: 'var(--jp-text)' }}>
            Support — Tickets
          </h2>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            {total} ticket(s) {status ? `· filtre : ${STATUSES.find(s => s.id === status)?.label}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            data-testid="support-admin-status-filter"
            className="jp-input text-xs"
          >
            {STATUSES.map((s) => <option key={s.id || 'all'} value={s.id}>{s.label}</option>)}
          </select>
          <button
            onClick={load}
            data-testid="support-admin-refresh"
            className="jp-btn jp-btn-ghost jp-btn-sm"
          >
            <ArrowsClockwise size={14} /> Rafraîchir
          </button>
        </div>
      </div>

      {loading && <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>}

      {!loading && items.length === 0 && (
        <div className="jp-card-elevated p-10 text-center" data-testid="support-admin-empty">
          <Ticket size={40} weight="duotone" style={{ color: 'var(--jp-text-muted)' }} className="mx-auto mb-2" />
          <div className="text-sm font-semibold">Aucun ticket pour ce filtre</div>
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="space-y-2">
          {items.map((ticket) => {
            const s = STATUS_STYLE[ticket.status] || STATUS_STYLE.open;
            const isExp = expanded === ticket.ticket_id;
            return (
              <div
                key={ticket.ticket_id}
                className="jp-card-elevated p-4"
                data-testid={`support-admin-ticket-${ticket.ticket_id}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div
                    className="flex-1 min-w-0 cursor-pointer"
                    onClick={() => setExpanded(isExp ? null : ticket.ticket_id)}
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
                        #{ticket.ticket_id}
                      </span>
                      <span className="jp-badge" style={{ background: s.bg, color: s.fg }}>
                        {s.label}
                      </span>
                      <span
                        className="jp-badge"
                        style={{ background: 'var(--jp-primary-subtle)', color: 'var(--jp-primary)' }}
                      >
                        {ticket.category}
                      </span>
                      {ticket.ai_tried && (
                        <span title={t('support_admin.ia_consultee_avant')}>
                          <Robot size={14} weight="fill" style={{ color: '#B45309' }} />
                        </span>
                      )}
                    </div>
                    <div className="font-bold text-sm mt-1">{ticket.subject}</div>
                    <div className="text-[11px] mt-0.5 flex items-center gap-2" style={{ color: 'var(--jp-text-muted)' }}>
                      <span>{ticket.user_email}</span>
                      <span>·</span>
                      <ClockIcon size={11} />
                      <span>{new Date(ticket.created_at).toLocaleString('fr-FR')}</span>
                    </div>
                  </div>
                  <button
                    onClick={() => setExpanded(isExp ? null : ticket.ticket_id)}
                    className="jp-btn jp-btn-ghost jp-btn-sm"
                    data-testid={`support-admin-toggle-${ticket.ticket_id}`}
                  >
                    {isExp ? <CaretUp size={14} /> : <CaretDown size={14} />}
                  </button>
                </div>

                {isExp && (
                  <div
                    className="mt-3 pt-3 border-t space-y-3"
                    style={{ borderColor: 'var(--jp-border)' }}
                  >
                    <div>
                      <div className="text-[11px] uppercase font-bold tracking-wider" style={{ color: 'var(--jp-text-secondary)' }}>
                        Message utilisateur
                      </div>
                      <div
                        className="mt-1 p-3 rounded-xl text-sm whitespace-pre-wrap"
                        style={{ background: 'var(--jp-surface-subtle)', lineHeight: 1.55 }}
                      >
                        {ticket.message}
                      </div>
                    </div>

                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[11px] uppercase font-bold tracking-wider mr-2" style={{ color: 'var(--jp-text-secondary)' }}>
                        Changer le statut :
                      </span>
                      {['open', 'in_progress', 'resolved', 'closed']
                        .filter((x) => x !== ticket.status)
                        .map((next) => (
                          <button
                            key={next}
                            onClick={() => updateStatus(ticket.ticket_id, next)}
                            data-testid={`support-admin-set-${ticket.ticket_id}-${next}`}
                            className="jp-btn jp-btn-ghost jp-btn-sm"
                            style={{
                              borderColor: STATUS_STYLE[next].fg,
                              color: STATUS_STYLE[next].fg,
                            }}
                          >
                            {STATUS_STYLE[next].label}
                          </button>
                        ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
