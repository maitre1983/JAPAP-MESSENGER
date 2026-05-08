/**
 * AudiencePane — list segments, preview count, view sample users,
 * create custom segment.
 */
import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { Plus, TrashSimple, UsersThree, Eye } from '@phosphor-icons/react';
import { msgApi } from './messagingApi';

export default function AudiencePane() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [previewing, setPreviewing] = useState(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try { setItems(await msgApi.segments()); }
    catch { toast.error('Chargement segments impossible.'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { reload(); }, [reload]);

  const onDelete = async (s) => {
    if (s.is_system) return toast.error('Segment système non supprimable.');
    if (!window.confirm(`Supprimer le segment "${s.name}" ?`)) return;
    try { await msgApi.deleteSegment(s.segment_id); toast.success('Supprimé.'); reload(); }
    catch (e) { toast.error(e?.response?.data?.detail || 'Suppression refusée.'); }
  };

  return (
    <div data-testid="messaging-audience-pane">
      <div className="mb-3 flex justify-between items-center">
        <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
          {items.length} segment{items.length > 1 ? 's' : ''} disponible{items.length > 1 ? 's' : ''}
        </p>
      </div>
      {loading && <div className="text-xs opacity-60">Chargement…</div>}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {items.map((s) => (
          <div key={s.segment_id}
               className="p-3 rounded-xl flex items-center gap-3"
               style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
               data-testid={`segment-row-${s.segment_id}`}>
            <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0"
                 style={{ background: 'rgba(247,147,26,0.15)', color: '#F7931A' }}>
              <UsersThree size={18} weight="fill" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm truncate">{s.name}</span>
                {s.is_system && (
                  <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full"
                        style={{ background: 'rgba(16,185,129,0.15)', color: '#059669' }}>
                    SYSTÈME
                  </span>
                )}
              </div>
              <div className="text-[11px] opacity-70 truncate">{s.description || '—'}</div>
              <div className="text-[11px] mt-0.5" style={{ color: '#F7931A' }}>
                {s.estimated_count} destinataire{s.estimated_count > 1 ? 's' : ''}
              </div>
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <button onClick={() => setPreviewing(s)}
                      className="p-2 rounded-full hover:bg-white/10"
                      data-testid={`segment-preview-${s.segment_id}`}>
                <Eye size={16} />
              </button>
              {!s.is_system && (
                <button onClick={() => onDelete(s)} className="p-2 rounded-full hover:bg-white/10"
                        style={{ color: '#b91c1c' }}>
                  <TrashSimple size={16} />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {previewing && (
        <SegmentPreviewModal segment={previewing} onClose={() => setPreviewing(null)} />
      )}
    </div>
  );
}

function SegmentPreviewModal({ segment, onClose }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    msgApi.segmentPreview({ segment_id: segment.segment_id, sample_size: 20 })
      .then(setData).catch(() => setData({ count: 0, sample: [] }));
  }, [segment.segment_id]);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-3"
         style={{ background: 'rgba(0,0,0,0.7)' }}
         onClick={onClose}
         data-testid="segment-preview-modal">
      <div className="w-full max-w-lg rounded-2xl overflow-hidden max-h-[80vh] flex flex-col"
           style={{ background: 'var(--jp-surface)' }}
           onClick={(e) => e.stopPropagation()}>
        <header className="px-5 py-3 flex items-center justify-between"
                style={{ borderBottom: '1px solid var(--jp-border)' }}>
          <div>
            <h2 className="font-['Outfit'] font-extrabold text-base">{segment.name}</h2>
            <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
              {data ? `${data.count} destinataires — aperçu (20 premiers)` : '…'}
            </p>
          </div>
          <button onClick={onClose} className="jp-btn jp-btn-ghost text-xs">Fermer</button>
        </header>
        <div className="overflow-y-auto p-4">
          {!data && <div className="text-xs opacity-60">Chargement…</div>}
          {data?.sample?.length === 0 && (
            <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Aucun destinataire.</p>
          )}
          <ul className="space-y-1">
            {(data?.sample || []).map((u) => (
              <li key={u.user_id} className="text-xs py-1.5 px-2 rounded"
                  style={{ background: 'var(--jp-surface-secondary)' }}>
                <span className="font-semibold">{u.name}</span>
                <span className="opacity-60 ml-2">{u.email}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
