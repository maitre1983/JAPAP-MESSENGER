/**
 * ConfirmSendModal — strong double-confirmation before bulk send.
 * Shows audience count, requires typing "ENVOYER" to unlock the send button.
 */
import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { X, Warning, PaperPlaneTilt } from '@phosphor-icons/react';
import { msgApi } from './messagingApi';
import { useTranslation } from 'react-i18next';

const CONFIRM_WORD = 'ENVOYER';

export default function ConfirmSendModal({ campaign, onClose, onSent }) {
  const { t } = useTranslation();
  const [audience, setAudience] = useState(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!campaign.segment_id) {
      // Individual targeting — count from the stored list
      let count = 0;
      try {
        const raw = campaign.individual_user_ids;
        if (Array.isArray(raw)) count = raw.length;
        else if (typeof raw === 'string') count = (JSON.parse(raw) || []).length;
      } catch { count = 0; }
      setAudience({ count });
      return;
    }
    msgApi.segmentPreview({ segment_id: campaign.segment_id, sample_size: 0 })
      .then((d) => setAudience(d))
      .catch(() => setAudience({ count: 0 }));
  }, [campaign.segment_id, campaign.individual_user_ids]);

  const canSend = input.trim().toUpperCase() === CONFIRM_WORD && !busy;

  const onConfirm = async () => {
    setBusy(true);
    try {
      const r = await msgApi.sendCampaign(campaign.campaign_id);
      toast.success(`${r.enqueued} emails en file d'attente.`);
      onSent();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Envoi refusé.');
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.75)' }}
         onClick={onClose}
         data-testid="campaign-confirm-modal">
      <div className="w-full max-w-md rounded-2xl overflow-hidden"
           style={{ background: 'var(--jp-surface)' }}
           onClick={(e) => e.stopPropagation()}>
        <header className="px-5 py-4 flex items-center justify-between"
                style={{ borderBottom: '1px solid var(--jp-border)' }}>
          <div className="flex items-center gap-2">
            <Warning size={22} weight="fill" style={{ color: '#f59e0b' }} />
            <h2 className="font-['Outfit'] font-extrabold text-lg">Confirmer l'envoi</h2>
          </div>
          <button onClick={onClose} className="p-2 rounded-full hover:bg-white/10">
            <X size={18} />
          </button>
        </header>

        <div className="p-5 space-y-4">
          <div className="text-sm" style={{ color: 'var(--jp-text)' }}>
            Vous êtes sur le point d'envoyer la campagne <strong>« {campaign.name} »</strong>.
          </div>
          <div className="p-3 rounded-xl text-xs"
               style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
            <div className="flex justify-between">
              <span style={{ color: 'var(--jp-text-muted)' }}>{t('confirm_send_modal.sujet')}</span>
              <span className="font-semibold">{campaign.subject}</span>
            </div>
            <div className="flex justify-between mt-1">
              <span style={{ color: 'var(--jp-text-muted)' }}>{t('confirm_send_modal.segment')}</span>
              <span className="font-semibold">{campaign.segment_id || 'Ciblage individuel'}</span>
            </div>
            <div className="flex justify-between mt-1">
              <span style={{ color: 'var(--jp-text-muted)' }}>{t('confirm_send_modal.audience')}</span>
              <span className="font-bold" style={{ color: '#F7931A' }}>
                {audience === null ? '…' : `${audience.count} destinataire${audience.count > 1 ? 's' : ''}`}
              </span>
            </div>
          </div>
          <div>
            <p className="text-xs mb-2" style={{ color: 'var(--jp-text-muted)' }}>
              Tapez <strong className="font-mono">{CONFIRM_WORD}</strong> pour confirmer :
            </p>
            <input value={input} onChange={(e) => setInput(e.target.value)}
                   autoFocus
                   placeholder={CONFIRM_WORD}
                   className="jp-input text-center font-mono tracking-widest"
                   data-testid="campaign-confirm-input" />
          </div>
        </div>

        <footer className="px-5 py-3 flex justify-end gap-2"
                style={{ borderTop: '1px solid var(--jp-border)' }}>
          <button onClick={onClose} className="jp-btn jp-btn-ghost text-xs">Annuler</button>
          <button onClick={onConfirm}
                  disabled={!canSend}
                  className="jp-btn jp-btn-primary text-xs flex items-center gap-1 disabled:opacity-40"
                  data-testid="campaign-confirm-btn">
            <PaperPlaneTilt size={13} weight="fill" />
            {busy ? 'Envoi en cours…' : 'Envoyer maintenant'}
          </button>
        </footer>
      </div>
    </div>
  );
}
