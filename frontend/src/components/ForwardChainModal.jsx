import { useState, useEffect } from 'react';
import axios from 'axios';
import { X as CloseIcon, ArrowBendUpRight, Eye } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * ForwardChainModal — displays the full chain of re-shares upstream from a
 * forwarded message. Each hop shows conv, sender, preview, and a 'Voir'
 * button when the caller is a participant of the hop's conversation.
 */
export default function ForwardChainModal({ msgId, onClose, onViewHop }) {
  const [hops, setHops] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { data } = await axios.get(`${API}/api/messages/${msgId}/forward-chain`, { withCredentials: true });
        if (!alive) return;
        setHops(Array.isArray(data?.hops) ? data.hops : []);
      } catch (e) {
        if (!alive) return;
        setError(e?.response?.data?.detail || 'Erreur lors du chargement de la chaîne');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [msgId]);

  useEffect(() => {
    const onEsc = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-end sm:items-center justify-center"
      style={{ background: 'rgba(11,5,66,0.55)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}
      data-testid="forward-chain-modal"
    >
      <div
        className="w-full sm:max-w-md rounded-t-3xl sm:rounded-3xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh]"
        style={{ background: 'var(--jp-surface)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: 'var(--jp-border)' }}>
          <div>
            <h3 className="font-['Outfit'] font-bold text-lg flex items-center gap-2" style={{ color: 'var(--jp-text)' }}>
              <ArrowBendUpRight size={18} weight="bold" />
              Chaîne de transferts
            </h3>
            <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
              {loading ? 'Chargement…' : `${hops.length} étape${hops.length > 1 ? 's' : ''} · du plus ancien au plus récent`}
            </p>
          </div>
          <button onClick={onClose} data-testid="forward-chain-close" className="p-1.5 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}>
            <CloseIcon size={20} weight="bold" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto jp-scrollbar px-5 py-3">
          {error && (
            <div className="text-sm p-3 rounded-lg" style={{ background: 'rgba(224,28,46,0.08)', color: '#E01C2E' }}>
              {error}
            </div>
          )}
          {!loading && hops.length === 0 && !error && (
            <p className="text-sm text-center py-6" style={{ color: 'var(--jp-text-muted)' }}>
              Aucune étape trouvée.
            </p>
          )}
          <ol className="relative border-l-2 ml-3" style={{ borderColor: 'var(--jp-border)' }}>
            {hops.map((hop, idx) => {
              const isOrigin = idx === 0;
              const isCurrent = idx === hops.length - 1;
              return (
                <li key={hop.msg_id} className="pl-4 py-3 relative" data-testid={`chain-hop-${idx}`}>
                  <span
                    className="absolute -left-[9px] top-4 w-4 h-4 rounded-full border-2"
                    style={{
                      background: isOrigin ? 'var(--jp-secondary)' : (isCurrent ? 'var(--jp-primary)' : 'var(--jp-surface)'),
                      borderColor: isOrigin ? 'var(--jp-secondary)' : 'var(--jp-primary)',
                    }}
                  />
                  <div className="flex items-start justify-between gap-2 mb-1">
                    <div className="min-w-0 flex-1">
                      <p className="text-[10px] font-bold uppercase tracking-wider font-['Manrope']" style={{ color: isOrigin ? 'var(--jp-secondary)' : 'var(--jp-primary)' }}>
                        {isOrigin ? 'Origine' : (isCurrent ? 'Ce message' : `Relai ${idx}`)}
                        {hop.forward_depth > 0 && ` · profondeur ${hop.forward_depth}`}
                      </p>
                      <p className="text-sm font-semibold font-['Manrope']" style={{ color: 'var(--jp-text)' }}>
                        {hop.sender_name || 'Inconnu'}
                      </p>
                      <p className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                        {hop.created_at ? new Date(hop.created_at).toLocaleString('fr-FR') : '—'}
                      </p>
                    </div>
                    {hop.viewable && !isCurrent && (
                      <button
                        type="button"
                        onClick={() => onViewHop?.(hop)}
                        data-testid={`chain-hop-view-${idx}`}
                        className="jp-btn jp-btn-soft jp-btn-sm flex items-center gap-1 flex-shrink-0"
                      >
                        <Eye size={14} /> Voir
                      </button>
                    )}
                  </div>
                  <p className="text-sm italic" style={{ color: 'var(--jp-text-secondary)' }}>
                    {hop.viewable
                      ? (hop.text || (hop.has_media ? '[média]' : '[vide]'))
                      : '🔒 Contenu privé (vous n\'êtes pas membre)'}
                  </p>
                </li>
              );
            })}
          </ol>
        </div>
        <div className="p-4 border-t text-[10px] font-['Manrope']" style={{ borderColor: 'var(--jp-border)', color: 'var(--jp-text-muted)' }}>
          💡 Les messages transférés plusieurs fois peuvent véhiculer de la désinformation. Vérifiez toujours la source originale.
        </div>
      </div>
    </div>
  );
}
