/**
 * NowPaymentsDepositCard — iter106 (P0 fix).
 *
 * After a successful POST /api/wallet/deposit using a `nowpayments_*`
 * method, the API returns { pay_address, pay_amount, pay_currency,
 * payment_status, payment_id, ... }. This card renders the full deposit
 * UX inside our own UI so the user does NOT need to leave JAPAP:
 *
 *   • QR code (built locally with `qrcode` library — no extra round-trip)
 *   • copy-to-clipboard buttons for address + amount
 *   • token / network badges (USDT TRC20 / BEP20)
 *   • live polling of the payment status (every 8s)
 *   • "J'ai payé" CTA which forces a fresh status probe
 *
 * Replaces the previous "redirect to invoice_url" flow that left users
 * stranded with no QR / no address visible.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import QRCode from 'qrcode';
import { useTranslation } from 'react-i18next';
import {
  Copy, ArrowsClockwise, CheckCircle, Clock, X, WarningCircle,
  Wallet, Hash,
} from '@phosphor-icons/react';
import * as WS from '../../utils/walletSecurity';

const API = process.env.REACT_APP_BACKEND_URL;

const NETWORK_META = {
  usdttrc20: { label: 'USDT', chain: 'TRON (TRC20)', color: '#26A17B' },
  usdtbsc:   { label: 'USDT', chain: 'BSC (BEP20)',  color: '#F0B90B' },
};

const getStatus_meta = (t) => ({
  waiting:        { label: 'En attente du paiement',  color: '#F59E0B', icon: Clock },
  confirming:     { label: 'Confirmation en cours',    color: '#0F056B', icon: ArrowsClockwise },
  confirmed:      { label: t('now_payments_deposit_card.paiement_confirme'),         color: '#10b981', icon: CheckCircle },
  finished:       { label: t('now_payments_deposit_card.credite_avec_succes'),       color: '#10b981', icon: CheckCircle },
  failed:         { label: t('now_payments_deposit_card.paiement_echoue'),           color: '#E01C2E', icon: WarningCircle },
  expired:        { label: t('now_payments_deposit_card.paiement_expire'),           color: '#7f1d1d', icon: WarningCircle },
  partially_paid: { label: t('now_payments_deposit_card.paiement_partiel_detecte'),  color: '#F7931A', icon: WarningCircle },
});


export default function NowPaymentsDepositCard({ deposit, onDone, onClose }) {
  const { t } = useTranslation();
  const STATUS_META = getStatus_meta(t);
  const [qrDataUrl, setQrDataUrl] = useState('');
  const [qrError, setQrError] = useState(null);
  const [status, setStatus] = useState(deposit.payment_status || 'waiting');
  const [actuallyPaid, setActuallyPaid] = useState(null);
  const [polling, setPolling] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Build QR locally — encodes the wallet address (some wallets accept
  // amount via URI scheme, but raw address is universally compatible).
  // iter116 — Track QR generation failures via the AI Error Monitor so we
  // know when users are stuck without a scannable code.
  useEffect(() => {
    if (!deposit?.pay_address) return;
    setQrError(null);
    QRCode.toDataURL(deposit.pay_address, {
      width: 320, margin: 2,
      color: { dark: '#0F056B', light: '#ffffff' },
    })
      .then((url) => { setQrDataUrl(url); setQrError(null); })
      .catch((err) => {
        setQrDataUrl('');
        setQrError(err?.message || 'Erreur QR');
        // Best-effort report — never blocks the user.
        try {
          axios.post(`${API}/api/errors/report`, WS.sanitizeErrorReport({
            module: 'wallet.nowpayments.qr',
            severity: 'high',
            message: `QR generation failed for tx=${deposit.tx_id}: ${err?.message || 'unknown'}`,
            stack: String(err?.stack || ''),
          }), { withCredentials: true }).catch(() => {});
        } catch (_) { /* silent */ }
      });
  }, [deposit?.pay_address, deposit?.tx_id]);

  const refresh = useCallback(async (showSpinner = true) => {
    if (!deposit?.tx_id) return;
    if (showSpinner) setRefreshing(true);
    try {
      const r = await axios.get(`${API}/api/wallet/deposit/${deposit.tx_id}/status`,
        { withCredentials: true });
      setStatus(r.data.payment_status || 'waiting');
      // iter221 — only trust positive values from the provider.
      const paid = parseFloat(r.data.actually_paid);
      setActuallyPaid(Number.isFinite(paid) && paid >= 0 ? paid : null);
      if (r.data.is_paid) {
        toast.success('Dépôt crédité ! Solde mis à jour.');
        setPolling(false);
        onDone?.();
      }
    } catch (e) {
      // Don't toast on every poll failure
    } finally {
      if (showSpinner) setRefreshing(false);
    }
  }, [deposit?.tx_id, onDone]);

  // iter119 — "J'ai payé" button : forces an authoritative verify on the
  // provider API and credits the wallet immediately if confirmed. Last-resort
  // safety net if the IPN webhook is delayed (preview env, mobile data, etc.).
  const [forceVerifying, setForceVerifying] = useState(false);
  const [lastForceAt, setLastForceAt] = useState(0);
  const forceVerify = useCallback(async () => {
    if (!deposit?.tx_id) return;
    // Client-side rate-limit: 1 click / 10s
    if (Date.now() - lastForceAt < 10_000) {
      toast.info('Patientez 10 secondes avant de réessayer.');
      return;
    }
    setLastForceAt(Date.now());
    setForceVerifying(true);
    try {
      const r = await axios.post(
        `${API}/api/wallet/deposit/${deposit.tx_id}/force-verify`, {},
        { withCredentials: true });
      if (r.data.credited) {
        toast.success(r.data.already
          ? t('now_payments_deposit_card.deja_credite_solde_a_jour')
          : '✅ Paiement confirmé — votre solde a été crédité.');
        setStatus('finished');
        setPolling(false);
        onDone?.();
      } else {
        toast.info(r.data.reason || `Statut provider : ${r.data.status}. Patientez quelques secondes.`);
      }
    } catch (e) {
      const msg = e.response?.data?.detail || 'Vérification impossible';
      toast.error(msg);
    } finally {
      setForceVerifying(false);
    }
  }, [deposit?.tx_id, lastForceAt, onDone]);

  // Poll every 8 seconds
  useEffect(() => {
    if (!polling) return;
    const id = setInterval(() => refresh(false), 8000);
    return () => clearInterval(id);
  }, [polling, refresh]);

  const copy = (text, label) => {
    if (!navigator.clipboard) {
      toast.error('Presse-papier indisponible');
      return;
    }
    navigator.clipboard.writeText(text)
      .then(() => toast.success(`${label} copiée`))
      .catch(() => toast.error('Copie impossible'));
  };

  if (!deposit) return null;
  const net = NETWORK_META[deposit.pay_currency] || { label: deposit.pay_currency?.toUpperCase(), chain: '', color: '#666' };
  const statusMeta = STATUS_META[status] || STATUS_META.waiting;
  const isFinished = status === 'finished' || status === 'confirmed';
  const isFailed = status === 'failed' || status === 'expired';

  return (
    <div className="jp-card-elevated p-5 mb-6 jp-animate-scaleIn"
         data-testid="nowpayments-deposit-card">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full flex items-center justify-center"
               style={{ background: net.color + '20' }}>
            <Wallet size={16} weight="fill" style={{ color: net.color }} />
          </div>
          <div>
            <div className="font-bold text-sm">{net.label} — {net.chain}</div>
            <div className="text-[10px] opacity-60">Dépôt crypto via NowPayments</div>
          </div>
        </div>
        {onClose && (
          <button onClick={onClose}
                  className="p-1 rounded-lg hover:bg-white/10"
                  data-testid="np-deposit-close"
                  style={{ color: 'var(--jp-text-muted)' }}>
            <X size={16} />
          </button>
        )}
      </div>

      {/* Status banner */}
      <div className="flex items-center gap-2 p-2.5 rounded-xl mb-4"
           style={{ background: statusMeta.color + '14',
                    border: `1px solid ${statusMeta.color}40` }}
           data-testid="np-deposit-status">
        <statusMeta.icon size={14} weight="fill"
                          className={status === 'confirming' ? 'animate-spin' : ''}
                          style={{ color: statusMeta.color }} />
        <span className="text-xs font-bold" style={{ color: statusMeta.color }}>
          {statusMeta.label}
        </span>
        {actuallyPaid !== null && actuallyPaid > 0 && (
          <span className="text-[10px] opacity-70 ml-auto">
            Reçu : {actuallyPaid} {deposit.pay_currency?.toUpperCase()}
          </span>
        )}
      </div>

      {!isFinished && !isFailed && (
        <>
          {/* QR */}
          {qrDataUrl ? (
            <div className="flex flex-col items-center gap-2 p-4 rounded-2xl mb-4"
                 style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
              <img src={qrDataUrl} alt={t('now_payments_deposit_card.qr_adresse_de_paiement')}
                   className="w-[240px] h-[240px]"
                   data-testid="np-deposit-qr" />
              <p className="text-[10px] opacity-60 text-center">
                Scannez avec votre wallet ou copiez l'adresse ci-dessous
              </p>
            </div>
          ) : qrError ? (
            <div className="p-4 mb-4 rounded-2xl text-center"
                 style={{ background: 'rgba(224, 28, 46, 0.08)',
                          border: '1px solid rgba(224, 28, 46, 0.3)' }}
                 data-testid="np-deposit-qr-fallback">
              <div className="text-xs font-bold mb-2" style={{ color: '#E01C2E' }}>
                ⚠️ Le QR code n'a pas pu être généré
              </div>
              <p className="text-[11px] opacity-80 mb-3">
                Pas d'inquiétude — votre paiement reste actif. Copiez simplement
                l'adresse ci-dessous et collez-la dans votre wallet pour envoyer
                le montant exact.
              </p>
              <button onClick={() => window.location.reload()}
                      data-testid="np-deposit-qr-retry"
                      className="text-[11px] underline opacity-70 hover:opacity-100">
                Réessayer
              </button>
            </div>
          ) : (
            <div className="p-4 mb-4 rounded-2xl text-center text-xs opacity-60"
                 style={{ background: 'var(--jp-surface-secondary)' }}>
              Génération du QR code en cours…
            </div>
          )}

          {/* Address */}
          <div className="mb-3">
            <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">
              Adresse de paiement ({net.chain})
            </label>
            <div className="flex items-center gap-1.5 p-2 rounded-lg font-mono text-[11px]"
                 style={{ background: 'var(--jp-surface-secondary)',
                          border: '1px solid var(--jp-border)' }}>
              <code className="flex-1 break-all" data-testid="np-deposit-address">
                {deposit.pay_address}
              </code>
              <button onClick={() => copy(deposit.pay_address, 'Adresse')}
                      className="p-1.5 rounded-md hover:bg-white/10"
                      data-testid="np-deposit-copy-address"
                      title={t('now_payments_deposit_card.copier_l_adresse')}>
                <Copy size={12} weight="bold" />
              </button>
            </div>
          </div>

          {/* Amount */}
          <div className="mb-3">
            <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">
              Montant exact à envoyer
            </label>
            <div className="flex items-center gap-1.5 p-2 rounded-lg"
                 style={{ background: 'var(--jp-surface-secondary)',
                          border: '1px solid var(--jp-border)' }}>
              <Hash size={12} className="opacity-60" />
              <code className="flex-1 font-mono text-sm font-extrabold"
                    data-testid="np-deposit-amount">
                {deposit.pay_amount} {deposit.pay_currency?.toUpperCase()}
              </code>
              <span className="text-[10px] opacity-50">
                ≈ {deposit.price_amount} {deposit.price_currency?.toUpperCase()}
              </span>
              <button onClick={() => copy(String(deposit.pay_amount), 'Montant')}
                      className="p-1.5 rounded-md hover:bg-white/10"
                      data-testid="np-deposit-copy-amount"
                      title={t('now_payments_deposit_card.copier_le_montant')}>
                <Copy size={12} weight="bold" />
              </button>
            </div>
          </div>

          <div className="jp-alert jp-alert-warning text-xs mb-3"
               data-testid="np-deposit-warning">
            <strong>{t('now_payments_deposit_card.important')}</strong> envoyez exactement <strong>{deposit.pay_amount} {deposit.pay_currency?.toUpperCase()}</strong> sur le réseau <strong>{net.chain}</strong>. Tout autre token / réseau sera perdu.
          </div>

          <div className="grid grid-cols-2 gap-2">
            <button onClick={() => refresh(true)}
                    disabled={refreshing}
                    className="jp-btn jp-btn-ghost text-xs flex items-center justify-center gap-1.5"
                    data-testid="np-deposit-refresh">
              <ArrowsClockwise size={12}
                                className={refreshing ? 'animate-spin' : ''} />
              Actualiser le statut
            </button>
            <button onClick={forceVerify}
                    disabled={forceVerifying || refreshing}
                    className="jp-btn jp-btn-primary text-xs flex items-center justify-center gap-1.5 font-bold"
                    data-testid="np-deposit-confirm">
              <CheckCircle size={12} weight="fill" className={forceVerifying ? 'animate-pulse' : ''} />
              {forceVerifying ? t('now_payments_deposit_card.verification') : t('now_payments_deposit_card.j_ai_paye')}
            </button>
          </div>
        </>
      )}

      {isFinished && (
        <div className="text-center py-4" data-testid="np-deposit-success">
          <CheckCircle size={48} weight="fill" className="mx-auto mb-2"
                       style={{ color: '#10b981' }} />
          <h4 className="font-bold text-base mb-1">Dépôt réussi !</h4>
          <p className="text-xs opacity-70">Votre solde a été mis à jour.</p>
          <button onClick={onClose} className="jp-btn jp-btn-primary text-xs mt-3 font-bold"
                  data-testid="np-deposit-done">
            Terminer
          </button>
        </div>
      )}

      {isFailed && (
        <div className="text-center py-4" data-testid="np-deposit-failed">
          <WarningCircle size={48} weight="fill" className="mx-auto mb-2"
                          style={{ color: '#E01C2E' }} />
          <h4 className="font-bold text-base mb-1">Paiement non reçu</h4>
          <p className="text-xs opacity-70 mb-3">
            Aucun versement n'a été détecté avant l'expiration. Si vous avez
            envoyé les fonds, contactez <a href="mailto:depot@japapmessenger.com"
            className="underline font-bold">depot@japapmessenger.com</a> avec
            votre tx_id <code>{deposit.tx_id}</code>.
          </p>
          <button onClick={onClose} className="jp-btn jp-btn-ghost text-xs">
            Fermer
          </button>
        </div>
      )}

      <p className="text-[10px] opacity-50 text-center mt-3">
        TX : <code>{WS.maskId(deposit.tx_id)}</code>{deposit.payment_id ? <> · Payment ID : <code>{WS.maskId(deposit.payment_id)}</code></> : null}
      </p>
    </div>
  );
}
