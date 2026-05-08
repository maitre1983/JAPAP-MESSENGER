import { useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { X, Copy, WhatsappLogo, ShareNetwork, QrCode, CheckCircle } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import * as WS from '../../utils/walletSecurity';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * iter141nine — "Demander à recevoir"
 * Two-step bottom modal:
 *   1. Form: amount + optional note
 *   2. Result: pay_url + WhatsApp button + copy button + QR PNG
 *
 * Used as a viral acquisition lever: each request shared on WhatsApp
 * either fulfills (instant top-up) or recruits a new user (combined with
 * the Recruteur reward system, the inviter still earns +50 pts).
 */
export default function RequestPaymentModal({ open, onClose, currency = 'XAF' }) {
  const { t } = useTranslation();
  const [step, setStep] = useState('form');           // 'form' | 'success'
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);

  if (!open) return null;

  const reset = () => {
    setStep('form'); setAmount(''); setNote(''); setResult(null);
  };
  const close = () => { reset(); onClose?.(); };

  const submit = async (e) => {
    e.preventDefault();
    const amt = parseFloat(amount);
    const check = WS.validateAmount(amt, 1, 10_000_000);
    if (!check.valid) {
      toast.error(check.reason);
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await axios.post(
        `${API}/api/wallet/payment-requests`,
        { amount: amt, note: note.trim().slice(0, 200), expires_in_hours: 168 },
        { withCredentials: true }
      );
      setResult(data);
      setStep('success');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Création impossible. Réessaie.');
    } finally {
      setSubmitting(false);
    }
  };

  const copyLink = async () => {
    try {
      // iter141nineE — copy the share_url (OG-rich endpoint) so when the
      // user pastes it on WhatsApp / iMessage / SMS, the recipient sees
      // a rich preview card. pay_url stays in the QR for direct scans.
      await navigator.clipboard.writeText(result.share_url || result.pay_url);
      toast.success('Lien copié — colle-le où tu veux.');
    } catch {
      toast.error('Impossible de copier. Sélectionne le lien manuellement.');
    }
  };

  const shareNative = async () => {
    if (!navigator.share) {
      copyLink();
      return;
    }
    try {
      await navigator.share({
        title: 'Demande de paiement JAPAP',
        text: result.share_text,
        url: result.share_url || result.pay_url,
      });
    } catch (err) {
      // user cancelled — ignore
    }
  };

  return (
    <div
      data-testid="request-modal-overlay"
      className="fixed inset-0 z-[80] flex items-end sm:items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(6px)' }}
      onClick={close}
    >
      <div
        data-testid="request-modal"
        onClick={(e) => e.stopPropagation()}
        className="w-full sm:max-w-md jp-card-elevated p-5 sm:rounded-2xl rounded-t-2xl"
        style={{ background: 'var(--jp-surface)', maxHeight: '92vh', overflowY: 'auto' }}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>
            {step === 'form' ? t('request_payment_modal.demander_a_recevoir') : t('request_payment_modal.demande_prete_a_partager')}
          </h3>
          <button
            data-testid="request-modal-close"
            onClick={close}
            className="p-1 rounded-lg"
            style={{ color: 'var(--jp-text-muted)' }}
          >
            <X size={20} />
          </button>
        </div>

        {step === 'form' && (
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="jp-label">Montant ({currency})</label>
              <input
                data-testid="request-amount-input"
                type="number"
                step="0.01"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="jp-input text-base"
                placeholder="0,00"
                autoFocus
                required
              />
            </div>
            <div>
              <label className="jp-label">Motif (optionnel)</label>
              <input
                data-testid="request-note-input"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="jp-input text-sm"
                placeholder={t('request_payment_modal.ex_pizza_de_samedi')}
                maxLength={200}
              />
            </div>
            <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
              Le lien généré expire dans <strong>7 jours</strong>. Tu pourras le partager sur WhatsApp,
              copier le lien ou afficher un QR à scanner.
            </p>
            <div className="flex gap-3 pt-1">
              <button
                data-testid="request-create-button"
                type="submit"
                disabled={submitting || !amount}
                className="jp-btn jp-btn-primary disabled:opacity-50 flex-1"
              >
                {submitting ? t('request_payment_modal.generation') : t('request_payment_modal.generer_le_lien')}
              </button>
              <button type="button" onClick={close} className="jp-btn jp-btn-ghost">Annuler</button>
            </div>
          </form>
        )}

        {step === 'success' && result && (
          <div className="space-y-4">
            <div
              className="p-4 rounded-2xl flex items-start gap-3"
              style={{
                background: 'var(--jp-success-subtle, rgba(34,197,94,0.10))',
                border: '1px solid var(--jp-success-muted, rgba(34,197,94,0.25))',
              }}
            >
              <CheckCircle size={24} weight="fill" style={{ color: 'var(--jp-success, #16a34a)' }} />
              <div className="text-sm">
                <div className="font-bold" style={{ color: 'var(--jp-text)' }}>
                  {result.amount} {result.currency} demandés
                </div>
                {result.note && (
                  <div className="text-xs mt-0.5" style={{ color: 'var(--jp-text-muted)' }}>
                    « {WS.sanitizeNote(result.note, 200)} »
                  </div>
                )}
              </div>
            </div>

            <div className="flex justify-center">
              {WS.isSafeQrUrl(result.qr_url, API) ? (
                <img
                  data-testid="request-qr-img"
                  src={`${API}${result.qr_url}`}
                  alt={t('request_payment_modal.qr_de_la_demande')}
                  className="rounded-xl"
                  style={{ width: 180, height: 180, background: 'white', padding: 8 }}
                />
              ) : (
                <div
                  data-testid="request-qr-img-fallback"
                  className="rounded-xl flex items-center justify-center text-xs"
                  style={{ width: 180, height: 180, background: 'white', color: '#666', padding: 8 }}
                >
                  QR indisponible
                </div>
              )}
            </div>

            <div
              className="p-3 rounded-xl text-xs break-all font-mono"
              data-testid="request-pay-url"
              style={{
                background: 'var(--jp-surface-secondary)',
                color: 'var(--jp-text-secondary)',
                border: '1px solid var(--jp-border)',
              }}
            >
              {result.pay_url}
            </div>

            <div className="grid grid-cols-2 gap-2">
              <a
                data-testid="request-whatsapp-button"
                href={result.whatsapp_url}
                target="_blank"
                rel="noopener noreferrer"
                className="jp-btn jp-btn-sm"
                style={{ background: '#25D366', color: 'white' }}
              >
                <WhatsappLogo size={16} weight="fill" /> WhatsApp
              </a>
              <button
                data-testid="request-share-button"
                type="button"
                onClick={shareNative}
                className="jp-btn jp-btn-sm jp-btn-secondary"
              >
                <ShareNetwork size={16} /> Partager
              </button>
              <button
                data-testid="request-copy-button"
                type="button"
                onClick={copyLink}
                className="jp-btn jp-btn-sm jp-btn-ghost"
              >
                <Copy size={16} /> Copier le lien
              </button>
              <button
                data-testid="request-qr-fullscreen"
                type="button"
                onClick={() => window.open(`${API}${result.qr_url}`, '_blank')}
                className="jp-btn jp-btn-sm jp-btn-ghost"
              >
                <QrCode size={16} /> Voir le QR
              </button>
            </div>

            <div className="flex gap-2 pt-1">
              <button
                data-testid="request-new-button"
                onClick={reset}
                className="jp-btn jp-btn-ghost flex-1"
              >
                Nouvelle demande
              </button>
              <button onClick={close} className="jp-btn jp-btn-secondary flex-1">Terminer</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
