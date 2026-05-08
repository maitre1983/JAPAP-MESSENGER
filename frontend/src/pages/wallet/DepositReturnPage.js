/**
 * iter195 — /wallet/deposit/return
 * =================================
 * CEO : Page dédiée où Hubtel (Online Checkout) redirige l'utilisateur après
 * un paiement. Clone la UX EAA :
 *   1. Lit `?tx=dep_xxx` + `?cancelled=1` dans l'URL.
 *   2. Polle GET /api/wallet/deposit/{tx_id}/status toutes les 3 s.
 *   3. Affiche l'état (succès / en attente / annulé) avec spinner et CTA
 *      "Retour au wallet".
 *
 * Important : le crédit réel du wallet n'arrive PAS ici — il arrive quand
 * Hubtel POST le webhook sur /api/wallet/hubtel/webhook. Cette page sert de
 * fallback visuel pour l'utilisateur pendant que le webhook arrive.
 */
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams, useNavigate } from 'react-router-dom';
import axios from 'axios';
import { CheckCircle, XCircle, Spinner, ArrowLeft, Wallet } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

export default function DepositReturnPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const txId = params.get('tx') || '';
  const isCancelled = params.get('cancelled') === '1';

  const [status, setStatus] = useState(isCancelled ? 'cancelled' : 'pending');
  const [providerStatus, setProviderStatus] = useState('');
  const [amount, setAmount] = useState(null);
  const [currency, setCurrency] = useState('USD');
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState('');
  const doneRef = useRef(false);

  useEffect(() => {
    if (!txId) {
      setError("Référence de transaction manquante (paramètre ?tx=).");
      return;
    }
    if (isCancelled) return;

    let cancelled = false;
    let interval;

    const poll = async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/wallet/deposit/${txId}/status`,
          { withCredentials: true },
        );
        if (cancelled) return;
        setStatus(data.tx_status || 'pending');
        setProviderStatus(data.payment_status || '');
        if (data.amount != null) setAmount(data.amount);
        if (data.currency) setCurrency(data.currency);
        if ((data.is_paid || data.tx_status === 'completed') && !doneRef.current) {
          doneRef.current = true;
          clearInterval(interval);
          setStatus('completed');
        }
        if (data.tx_status === 'rejected' || data.tx_status === 'expired') {
          doneRef.current = true;
          clearInterval(interval);
          setStatus(data.tx_status);
        }
      } catch (e) {
        // 404 = tx inconnue — probablement un identifiant trafiqué.
        if (e.response?.status === 404) {
          setError("Transaction introuvable.");
          clearInterval(interval);
        }
      }
    };

    poll();
    interval = setInterval(() => {
      setElapsed(s => s + 3);
      poll();
    }, 3000);

    return () => { cancelled = true; clearInterval(interval); };
  }, [txId, isCancelled]);

  const getCard = () => {
    if (error) {
      return {
        icon: <XCircle size={56} weight="duotone" color="#DC2626" />,
        bg: '#FEF2F2', border: '#FECACA', title: 'Erreur', color: '#991B1B',
        message: error,
      };
    }
    if (status === 'completed') {
      return {
        icon: <CheckCircle size={56} weight="duotone" color="#059669" />,
        bg: '#ECFDF5', border: '#A7F3D0', title: '✅ Paiement confirmé', color: '#065F46',
        message: amount ? `Ton wallet a été crédité de ${amount} ${currency}.` : 'Ton wallet vient d\'être crédité.',
      };
    }
    if (status === 'cancelled') {
      return {
        icon: <XCircle size={56} weight="duotone" color="#D97706" />,
        bg: '#FFFBEB', border: '#FDE68A', title: t('deposit_return.paiement_annule'), color: '#92400E',
        message: t('deposit_return.tu_as_annule_le_paiement_aucun_mont'),
      };
    }
    if (status === 'rejected' || status === 'expired') {
      return {
        icon: <XCircle size={56} weight="duotone" color="#DC2626" />,
        bg: '#FEF2F2', border: '#FECACA', title: t('deposit_return.paiement_echoue'), color: '#991B1B',
        message: status === 'expired'
          ? t('deposit_return.la_transaction_a_expire_avant_confi')
          : `Hubtel a rejeté la transaction${providerStatus ? ` (${providerStatus})` : ''}.`,
      };
    }
    // pending
    return {
      icon: (
        <div className="inline-block">
          <Spinner size={56} weight="duotone" color="#2563EB" className="jp-animate-spin" />
        </div>
      ),
      bg: '#EFF6FF', border: '#BFDBFE', title: 'Paiement en cours…', color: '#1E40AF',
      message: `En attente de la confirmation Hubtel${providerStatus ? ` — ${providerStatus}` : ''}${elapsed > 0 ? ` (${elapsed}s)` : ''}.`,
    };
  };

  const card = getCard();
  const isFinal = ['completed', 'cancelled', 'rejected', 'expired'].includes(status) || !!error;

  return (
    <div
      data-testid="deposit-return-page"
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: 'linear-gradient(135deg, #F5F3FF 0%, #EFF6FF 100%)' }}
    >
      <div
        className="max-w-md w-full jp-card-elevated jp-animate-scaleIn"
        style={{ padding: '32px 24px', borderRadius: 20 }}
      >
        <div
          className="flex flex-col items-center gap-3 text-center p-5 rounded-2xl mb-4"
          style={{ background: card.bg, border: `1px solid ${card.border}`, color: card.color }}
        >
          {card.icon}
          <h1 className="font-['Outfit'] text-2xl font-bold" data-testid="deposit-return-title">
            {card.title}
          </h1>
          <p className="text-sm" data-testid="deposit-return-message">
            {card.message}
          </p>
        </div>

        {txId && (
          <div className="text-[11px] text-center mb-4" style={{ color: 'var(--jp-text-muted)' }}>
            Référence : <code data-testid="deposit-return-tx">{txId}</code>
          </div>
        )}

        {!isFinal && (
          <div className="text-[11px] text-center mb-4 px-3" style={{ color: 'var(--jp-text-muted)' }}>
            Si le débit a été confirmé sur ton téléphone (SMS MoMo reçu), le crédit arrive dans quelques secondes. Tu peux fermer cette page sans risque.
          </div>
        )}

        <div className="flex flex-col gap-2">
          <Link
            to="/wallet"
            className="jp-btn jp-btn-primary w-full justify-center"
            data-testid="deposit-return-go-wallet"
          >
            <Wallet size={16} weight="duotone" />
            Retour au wallet
          </Link>
          {status === 'cancelled' && (
            <button
              className="jp-btn jp-btn-ghost w-full justify-center"
              onClick={() => navigate('/wallet?retry=1')}
              data-testid="deposit-return-retry"
            >
              <ArrowLeft size={16} weight="duotone" /> Réessayer le dépôt
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
