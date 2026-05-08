/**
 * CampaignsPane — list + edit + send flow.
 */
import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { Plus, PaperPlaneTilt, PencilSimple, TrashSimple, Eye } from '@phosphor-icons/react';
import { msgApi, StatusBadge } from './messagingApi';
import CampaignEditorModal from './CampaignEditorModal';
import ConfirmSendModal from './ConfirmSendModal';

export default function CampaignsPane() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);      // null | 'new' | campaign object
  const [confirming, setConfirming] = useState(null); // campaign to confirm-send

  const reload = useCallback(async () => {
    setLoading(true);
    try { setItems(await msgApi.campaigns()); }
    catch (e) { toast.error(e?.response?.data?.detail || 'Chargement impossible'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const onDelete = async (c) => {
    if (c.status !== 'draft') return toast.error('Seules les campagnes en brouillon peuvent être supprimées.');
    if (!window.confirm(`Supprimer la campagne "${c.name}" ?`)) return;
    try {
      await msgApi.deleteCampaign(c.campaign_id);
      toast.success('Supprimée.');
      reload();
    } catch (e) { toast.error(e?.response?.data?.detail || 'Suppression refusée.'); }
  };

  return (
    <div data-testid="messaging-campaigns-pane">
      <div className="mb-4 flex justify-between items-center">
        <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
          {items.length} campagne{items.length > 1 ? 's' : ''}
        </p>
        <button className="jp-btn jp-btn-primary text-xs flex items-center gap-1"
                onClick={() => setEditing('new')}
                data-testid="messaging-campaign-create">
          <Plus size={14} weight="bold" /> Nouvelle campagne
        </button>
      </div>

      {loading && <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>}

      {!loading && items.length === 0 && (
        <div className="p-8 text-center rounded-2xl"
             style={{ background: 'var(--jp-surface-secondary)', border: '1px dashed var(--jp-border)' }}>
          <p className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>
            Aucune campagne. Créez-en une pour commencer.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-2">
        {items.map((c) => (
          <div key={c.campaign_id} data-testid={`campaign-row-${c.campaign_id}`}
               className="flex items-center gap-3 p-3 rounded-xl transition-colors"
               style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold text-sm truncate">{c.name}</span>
                <StatusBadge status={c.status} />
              </div>
              <div className="text-[11px] opacity-70 mt-0.5 truncate">
                {c.subject} · {c.segment_id || 'destinataires custom'}
                {c.sent_count > 0 && ` · ${c.sent_count} envoyés`}
                {c.opened_count > 0 && ` · ${c.opened_count} ouvertures`}
                {c.clicked_count > 0 && ` · ${c.clicked_count} clics`}
              </div>
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <button onClick={() => setEditing(c)}
                      className="p-2 rounded-full hover:bg-white/5"
                      data-testid={`campaign-edit-${c.campaign_id}`}
                      title="Éditer / Prévisualiser">
                {c.status === 'draft' ? <PencilSimple size={16} /> : <Eye size={16} />}
              </button>
              {c.status === 'draft' && (
                <button onClick={() => setConfirming(c)}
                        className="p-2 rounded-full hover:bg-white/5"
                        style={{ color: '#047857' }}
                        data-testid={`campaign-send-${c.campaign_id}`}
                        title="Envoyer (test + bulk)">
                  <PaperPlaneTilt size={16} weight="fill" />
                </button>
              )}
              {c.status === 'draft' && (
                <button onClick={() => onDelete(c)}
                        className="p-2 rounded-full hover:bg-white/5"
                        style={{ color: '#b91c1c' }}
                        data-testid={`campaign-delete-${c.campaign_id}`}>
                  <TrashSimple size={16} />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {editing && (
        <CampaignEditorModal
          campaign={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }}
        />
      )}
      {confirming && (
        <ConfirmSendModal
          campaign={confirming}
          onClose={() => setConfirming(null)}
          onSent={() => { setConfirming(null); reload(); }}
        />
      )}
    </div>
  );
}
