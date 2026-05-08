import { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { X as CloseIcon, PaperPlaneRight, MagnifyingGlass, UsersThree } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * ForwardModal — select 1..N conversations to forward a message to.
 * Mobile-first: bottom-sheet on phones, centered dialog on md+.
 */
export default function ForwardModal({ message, conversations, onClose, onDone }) {
  const [selected, setSelected] = useState(new Set());
  const [query, setQuery] = useState('');
  const [sending, setSending] = useState(false);

  const filtered = useMemo(() => {
    if (!query.trim()) return conversations;
    const q = query.trim().toLowerCase();
    return conversations.filter((c) => {
      const name = c.type === 'group'
        ? (c.title || '')
        : `${c.participants?.[0]?.first_name || ''} ${c.participants?.[0]?.last_name || ''}`.trim();
      return name.toLowerCase().includes(q);
    });
  }, [conversations, query]);

  useEffect(() => {
    const onEsc = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [onClose]);

  const toggle = (convId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(convId)) next.delete(convId);
      else next.add(convId);
      return next;
    });
  };

  const handleForward = async () => {
    if (selected.size === 0 || !message?.msg_id) return;
    setSending(true);
    try {
      const { data } = await axios.post(
        `${API}/api/messages/${message.msg_id}/forward`,
        { target_conv_ids: Array.from(selected) },
        { withCredentials: true },
      );
      onDone?.(data);
    } catch (e) {
      console.error('Forward failed', e);
      toast.error(e?.response?.data?.detail || 'Erreur lors du transfert');
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-end sm:items-center justify-center"
      style={{ background: 'rgba(11,5,66,0.55)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}
      data-testid="forward-modal"
    >
      <div
        className="w-full sm:max-w-md rounded-t-3xl sm:rounded-3xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh]"
        style={{ background: 'var(--jp-surface)' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: 'var(--jp-border)' }}>
          <div>
            <h3 className="font-['Outfit'] font-bold text-lg" style={{ color: 'var(--jp-text)' }}>
              Transférer vers…
            </h3>
            <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
              {selected.size === 0 ? 'Sélectionnez une ou plusieurs conversations' : `${selected.size} sélectionnée${selected.size > 1 ? 's' : ''}`}
            </p>
          </div>
          <button onClick={onClose} data-testid="forward-modal-close" className="p-1.5 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}>
            <CloseIcon size={20} weight="bold" />
          </button>
        </div>
        {/* Preview of forwarded message */}
        {message && (
          <div className="px-5 py-3 border-b" style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-surface-secondary)' }}>
            <p className="text-[10px] font-['Manrope'] font-bold uppercase tracking-wider mb-1" style={{ color: 'var(--jp-text-muted)' }}>
              Message transféré
            </p>
            <p className="text-sm line-clamp-3" style={{ color: 'var(--jp-text)' }}>
              {message.text?.slice(0, 200) || '[média]'}
            </p>
          </div>
        )}
        {/* Search */}
        <div className="px-5 py-3 border-b" style={{ borderColor: 'var(--jp-border)' }}>
          <div className="relative">
            <MagnifyingGlass size={16} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--jp-text-muted)' }} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Rechercher une conversation…"
              data-testid="forward-search-input"
              className="jp-input jp-input-with-icon text-sm w-full"
              style={{ paddingLeft: '36px' }}
            />
          </div>
        </div>
        {/* Conversation list */}
        <div className="flex-1 overflow-y-auto jp-scrollbar">
          {filtered.length === 0 && (
            <div className="px-5 py-8 text-center text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
              Aucune conversation trouvée
            </div>
          )}
          {filtered.map((conv) => {
            const isSelected = selected.has(conv.conv_id);
            const isGroup = conv.type === 'group';
            const other = conv.participants?.[0] || {};
            const name = isGroup ? (conv.title || 'Groupe') : `${other.first_name || ''} ${other.last_name || ''}`.trim();
            const initial = (isGroup ? conv.title?.[0] : other.first_name?.[0])?.toUpperCase() || '?';
            return (
              <button
                key={conv.conv_id}
                onClick={() => toggle(conv.conv_id)}
                data-testid={`forward-target-${conv.conv_id}`}
                className="w-full flex items-center gap-3 px-5 py-3 border-b text-left transition-colors"
                style={{
                  borderColor: 'rgba(229,228,226,0.5)',
                  background: isSelected ? 'var(--jp-primary-subtle)' : 'transparent',
                }}
              >
                <div className={`jp-avatar jp-avatar-md ${isGroup ? 'jp-avatar-secondary' : 'jp-avatar-primary'} flex-shrink-0`}>
                  {initial}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <p className="text-sm font-semibold font-['Manrope'] truncate" style={{ color: 'var(--jp-text)' }}>{name}</p>
                    {isGroup && <UsersThree size={12} style={{ color: 'var(--jp-text-muted)' }} />}
                  </div>
                </div>
                <div
                  className="w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0"
                  style={{
                    borderColor: isSelected ? 'var(--jp-primary)' : 'var(--jp-border)',
                    background: isSelected ? 'var(--jp-primary)' : 'transparent',
                  }}
                >
                  {isSelected && <span className="text-white text-xs leading-none">✓</span>}
                </div>
              </button>
            );
          })}
        </div>
        {/* Footer action */}
        <div className="p-4 border-t" style={{ borderColor: 'var(--jp-border)' }}>
          <button
            onClick={handleForward}
            disabled={selected.size === 0 || sending}
            data-testid="forward-confirm-button"
            className="w-full py-3 rounded-xl font-['Outfit'] font-bold text-white disabled:opacity-50 flex items-center justify-center gap-2"
            style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}
          >
            <PaperPlaneRight size={18} weight="fill" />
            {sending ? 'Envoi…' : `Transférer${selected.size > 0 ? ` (${selected.size})` : ''}`}
          </button>
        </div>
      </div>
    </div>
  );
}
