/**
 * ReshareSummaryModal — Pick a conversation target and re-share an existing
 * AI call summary into it as a structured `call_summary` message.
 *
 * Backend: POST /api/calls/{session_id}/summary/share with `{conv_id}`.
 * The endpoint already handles participant gating + auto-assignee matching.
 */
import { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { X, MagnifyingGlass, PaperPlaneTilt, UsersThree } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ReshareSummaryModal({ sessionId, onClose }) {
  const { t } = useTranslation();
  const [convs, setConvs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [sendingId, setSendingId] = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { data } = await axios.get(`${API}/api/messaging/conversations`, { withCredentials: true });
        if (alive) setConvs(Array.isArray(data) ? data : []);
      } catch {
        if (alive) toast.error('Conversations indisponibles.');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return convs;
    return convs.filter((c) => {
      const title = convTitle(c).toLowerCase();
      return title.includes(needle);
    });
  }, [convs, q]);

  const share = async (conv) => {
    setSendingId(conv.conv_id);
    try {
      await axios.post(`${API}/api/calls/${sessionId}/summary/share`,
        { conv_id: conv.conv_id }, { withCredentials: true });
      toast.success(`Résumé partagé dans « ${convTitle(conv)} »`);
      onClose();
    } catch (e) {
      const detail = e?.response?.data?.detail || 'Impossible de partager le résumé.';
      toast.error(detail);
    } finally {
      setSendingId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center"
         style={{ background: 'rgba(0,0,0,0.7)' }}
         onClick={onClose}
         data-testid="reshare-summary-modal">
      <div className="w-full sm:max-w-md max-h-[85vh] rounded-t-3xl sm:rounded-3xl overflow-hidden flex flex-col"
           style={{ background: 'var(--jp-surface)', color: 'var(--jp-text)' }}
           onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <header className="px-5 py-4 flex items-center justify-between"
                style={{ borderBottom: '1px solid var(--jp-border)' }}>
          <div>
            <h2 className="font-['Outfit'] font-extrabold text-lg leading-tight">Repartager le résumé</h2>
            <p className="text-[11px] opacity-60">Choisissez une conversation</p>
          </div>
          <button onClick={onClose}
                  className="p-2 rounded-full hover:bg-white/10"
                  data-testid="reshare-summary-close">
            <X size={18} />
          </button>
        </header>

        {/* Search */}
        <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--jp-border)' }}>
          <div className="flex items-center gap-2 px-3 py-2 rounded-xl"
               style={{ background: 'var(--jp-surface-secondary)' }}>
            <MagnifyingGlass size={16} style={{ opacity: 0.6 }} />
            <input value={q} onChange={(e) => setQ(e.target.value)}
                   autoFocus
                   placeholder={t('reshare_summary_modal.rechercher')}
                   className="bg-transparent outline-none flex-1 text-sm"
                   data-testid="reshare-summary-search" />
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {loading && <div className="p-8 text-center text-sm opacity-60">Chargement…</div>}
          {!loading && filtered.length === 0 && (
            <div className="p-8 text-center text-sm opacity-60" data-testid="reshare-summary-empty">
              Aucune conversation.
            </div>
          )}
          <ul>
            {filtered.map((c) => (
              <li key={c.conv_id}>
                <button onClick={() => share(c)}
                        disabled={sendingId !== null}
                        className="w-full flex items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-white/[0.05] disabled:opacity-50"
                        data-testid={`reshare-summary-conv-${c.conv_id}`}>
                  <ConvAvatar conv={c} />
                  <div className="flex-1 min-w-0">
                    <p className="font-semibold text-sm truncate">{convTitle(c)}</p>
                    <p className="text-[11px] opacity-60 truncate">{convSubtitle(c)}</p>
                  </div>
                  {sendingId === c.conv_id
                    ? <div className="jp-spinner-sm" />
                    : <PaperPlaneTilt size={18} style={{ color: '#F7931A' }} />}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function ConvAvatar({ conv }) {
  if (conv.type === 'group' || (conv.participants || []).length > 1) {
    return (
      <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0"
           style={{ background: 'rgba(247,147,26,0.15)', color: '#F7931A' }}>
        <UsersThree size={18} weight="fill" />
      </div>
    );
  }
  const p = (conv.participants || [])[0];
  if (p?.avatar) {
    return <img src={p.avatar} alt="" className="w-10 h-10 rounded-full object-cover shrink-0" />;
  }
  return (
    <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0 font-bold text-xs"
         style={{ background: 'var(--jp-surface-secondary)' }}>
      {(convTitle(conv) || '?').slice(0, 1).toUpperCase()}
    </div>
  );
}

function convTitle(c) {
  if (c.title) return c.title;
  const p = (c.participants || [])[0];
  if (p) return `${p.first_name || ''} ${p.last_name || ''}`.trim() || p.username || 'Inconnu';
  return 'Conversation';
}

function convSubtitle(c) {
  const lm = c.last_message;
  if (!lm?.text) return c.type === 'group' ? 'Groupe' : 'Démarrer la conversation';
  return lm.text.length > 64 ? lm.text.slice(0, 64) + '…' : lm.text;
}
