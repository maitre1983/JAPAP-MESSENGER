/**
 * CallSummaryModal — Sprint D post-call AI summary display.
 * Fetches /api/calls/{session_id}/summary and renders:
 *   - Transcript (collapsible)
 *   - AI summary (2-4 sentences)
 *   - Key points (bullets)
 *   - Decisions
 *   - Action items (who / what / due)
 *   - Playback link to the stored R2 recording
 *
 * Mobile-first : bottom-sheet on phones, centered dialog on md+.
 * Polls the endpoint every 5s while status='pending|transcribing|summarizing'.
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { X as CloseIcon, Sparkle, CaretDown, CaretUp, Record as RecordIcon, PaperPlaneRight } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

const STATUS_LABELS = {
  pending: 'En file d\'attente…',
  transcribing: 'Transcription en cours…',
  summarizing: 'Synthèse IA en cours…',
  ready: 'Prêt',
  failed: 'Échec',
  none: 'Pas de résumé disponible',
};

export default function CallSummaryModal({ sessionId, convId = null, onClose }) {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState('');
  const [sharing, setSharing] = useState(false);
  const [shared, setShared] = useState(false);

  const shareToChat = async () => {
    if (!sessionId || sharing) return;
    setSharing(true);
    try {
      const { data: resp } = await axios.post(
        `${API}/api/calls/${sessionId}/summary/share`,
        { conv_id: convId || undefined, include_transcript: false },
        { withCredentials: true },
      );
      setShared(true);
      const nb = resp?.action_items || 0;
      toast.success(
        nb > 0
          ? `Résumé partagé — ${nb} tâche${nb > 1 ? 's' : ''} ajoutée${nb > 1 ? 's' : ''} au chat.`
          : t('call_summary_modal.resume_partage_dans_la_conversation'),
      );
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Partage impossible.';
      toast.error(msg);
    } finally {
      setSharing(false);
    }
  };

  useEffect(() => {
    if (!sessionId) return;
    let alive = true;
    let iv = null;
    const fetchOnce = async () => {
      try {
        const { data: resp } = await axios.get(`${API}/api/calls/${sessionId}/summary`, { withCredentials: true });
        if (!alive) return;
        setData(resp);
        const st = resp?.status || 'none';
        // Stop polling on terminal states. When status='none' AND a recording
        // exists, keep polling — the summary row might still be about to appear
        // (race between record/stop returning and the BG pipeline writing the row).
        const hasRecording = !!resp?.recording;
        const terminal = ['ready', 'failed'].includes(st) || (st === 'none' && !hasRecording);
        if (terminal && iv) clearInterval(iv);
      } catch (e) {
        if (!alive) return;
        setError(e?.response?.data?.detail || 'Impossible de récupérer le résumé');
      }
    };
    fetchOnce();
    iv = setInterval(fetchOnce, 5000);
    return () => { alive = false; if (iv) clearInterval(iv); };
  }, [sessionId]);

  useEffect(() => {
    const onEsc = (e) => e.key === 'Escape' && onClose?.();
    window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [onClose]);

  const status = data?.status || 'pending';
  const isProgress = ['pending', 'transcribing', 'summarizing'].includes(status);

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-end sm:items-center justify-center"
      style={{ background: 'rgba(11,5,66,0.55)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}
      data-testid="call-summary-modal"
    >
      <div
        className="w-full sm:max-w-lg rounded-t-3xl sm:rounded-3xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]"
        style={{ background: 'var(--jp-surface)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: 'var(--jp-border)' }}>
          <div>
            <h3 className="font-['Outfit'] font-bold text-lg flex items-center gap-2" style={{ color: 'var(--jp-text)' }}>
              <Sparkle size={18} weight="fill" color="#E01C2E" />
              Résumé IA de l'appel
            </h3>
            <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }} data-testid="summary-status">
              {STATUS_LABELS[status] || status}
            </p>
          </div>
          <button onClick={onClose} data-testid="call-summary-close" className="p-1.5 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}>
            <CloseIcon size={20} weight="bold" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto jp-scrollbar px-5 py-4 space-y-4">
          {error && (
            <div className="p-3 rounded-lg text-sm" style={{ background: 'rgba(224,28,46,0.08)', color: '#E01C2E' }}>
              {error}
            </div>
          )}
          {isProgress && (
            <div className="p-6 text-center" data-testid="summary-loading">
              <div className="w-10 h-10 mx-auto mb-3 rounded-full border-4 border-solid border-gray-200 animate-spin" style={{ borderTopColor: '#0F056B' }} />
              <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>
                {STATUS_LABELS[status]}
              </p>
              <p className="text-xs mt-2" style={{ color: 'var(--jp-text-muted)' }}>Cela peut prendre jusqu'à 2 minutes selon la durée de l'appel.</p>
            </div>
          )}
          {status === 'ready' && (
            <>
              {data?.recording?.public_url && (
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-wider mb-2" style={{ color: 'var(--jp-text-muted)' }}>
                    <RecordIcon size={12} style={{ verticalAlign: 'middle', marginRight: 4, color: '#E01C2E' }} />
                    Enregistrement
                  </p>
                  <audio
                    controls preload="metadata"
                    src={data.recording.public_url}
                    data-testid="summary-audio-player"
                    className="w-full" style={{ borderRadius: 12 }}
                  />
                </div>
              )}
              {data?.summary && (
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: 'var(--jp-text-muted)' }}>Synthèse</p>
                  <p className="text-sm font-['Manrope'] leading-relaxed" style={{ color: 'var(--jp-text)' }} data-testid="summary-text">{data.summary}</p>
                </div>
              )}
              {Array.isArray(data?.key_points) && data.key_points.length > 0 && (
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: 'var(--jp-text-muted)' }}>Points clés</p>
                  <ul className="text-sm font-['Manrope'] list-disc list-inside space-y-0.5" data-testid="summary-key-points" style={{ color: 'var(--jp-text)' }}>
                    {data.key_points.map((p, i) => <li key={i}>{p}</li>)}
                  </ul>
                </div>
              )}
              {Array.isArray(data?.decisions) && data.decisions.length > 0 && (
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: 'var(--jp-text-muted)' }}>Décisions</p>
                  <ul className="text-sm font-['Manrope'] list-disc list-inside space-y-0.5" data-testid="summary-decisions" style={{ color: 'var(--jp-text)' }}>
                    {data.decisions.map((p, i) => <li key={i}>{p}</li>)}
                  </ul>
                </div>
              )}
              {Array.isArray(data?.action_items) && data.action_items.length > 0 && (
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: 'var(--jp-text-muted)' }}>Actions</p>
                  <ul className="text-sm font-['Manrope'] space-y-1.5" data-testid="summary-action-items" style={{ color: 'var(--jp-text)' }}>
                    {data.action_items.map((a, i) => (
                      <li key={i} className="flex gap-2">
                        <span style={{ color: 'var(--jp-primary)', fontWeight: 600, minWidth: 70 }}>
                          {typeof a === 'object' ? (a.who || 'Équipe') : '•'}
                        </span>
                        <span>{typeof a === 'object' ? a.what : a}{a.due ? ` (${a.due})` : ''}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {data?.transcript && (
                <div>
                  <button
                    type="button"
                    onClick={() => setExpanded(!expanded)}
                    data-testid="summary-toggle-transcript"
                    className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider mb-1"
                    style={{ color: 'var(--jp-text-muted)' }}>
                    Transcription brute {expanded ? <CaretUp size={12} /> : <CaretDown size={12} />}
                  </button>
                  {expanded && (
                    <p data-testid="summary-transcript" className="text-xs font-['Manrope'] whitespace-pre-wrap p-3 rounded-lg max-h-56 overflow-y-auto" style={{ background: 'var(--jp-surface-secondary)', color: 'var(--jp-text-secondary)' }}>
                      {data.transcript}
                    </p>
                  )}
                </div>
              )}
            </>
          )}
          {status === 'failed' && (
            <div className="p-3 rounded-lg text-sm" style={{ background: 'rgba(224,28,46,0.08)', color: '#E01C2E' }} data-testid="summary-error">
              Échec du pipeline IA. {data?.error_msg || ''}
            </div>
          )}
          {status === 'none' && (
            <div className="p-6 text-center text-sm" style={{ color: 'var(--jp-text-muted)' }} data-testid="summary-none">
              Cet appel n'a pas été enregistré. Lancez un enregistrement pendant un appel pour bénéficier du résumé IA.
            </div>
          )}
        </div>
        {status === 'ready' && (
          <div className="px-5 py-3 border-t flex items-center gap-2"
               style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-surface-secondary, #FAFAFA)' }}>
            <button
              onClick={shareToChat}
              disabled={sharing || shared}
              data-testid="summary-share-to-chat"
              className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl font-['Outfit'] font-bold text-sm transition-transform active:scale-95"
              style={{
                background: shared
                  ? 'rgba(16,185,129,0.15)'
                  : 'linear-gradient(135deg, #0F056B 0%, #E01C2E 100%)',
                color: shared ? '#059669' : 'white',
                opacity: sharing ? 0.6 : 1,
              }}>
              <PaperPlaneRight size={16} weight="fill" />
              {shared ? t('call_summary_modal.partage') : sharing ? 'Partage en cours…' : 'Partager dans la conversation'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
