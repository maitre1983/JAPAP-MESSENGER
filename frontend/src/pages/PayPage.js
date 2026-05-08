import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { CheckCircle, Warning, Wallet, ArrowRight, Clock, X } from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * iter141nine — Public landing page for a payment request.
 *
 * Flow:
 *   1. Fetch the request preview (public endpoint, no auth needed).
 *   2. If the visitor isn't authenticated → CTA "Se connecter pour payer"
 *      with `?redirect=/pay/<id>` so they come back here after login.
 *   3. If authenticated → "Payer X XAF à Alice" button that calls
 *      `POST /api/wallet/payment-requests/:id/fulfill` (idempotent through
 *      the existing send_money idempotency_key system).
 *   4. Status-aware rendering: paid / cancelled / expired show clear
 *      receipts and prevent duplicate payments.
 *
 * Combined with the Recruteur reward system, every shared request is a
 * potential viral acquisition lever — visitors who sign up to pay become
 * tracked recruits of the requester.
 */
export default function PayPage() {
  const { t } = useTranslation();
  const { requestId } = useParams();
  const navigate = useNavigate();
  const { user, loading: authLoading } = useAuth();
  const [req, setReq] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [paying, setPaying] = useState(false);
  const [paidResult, setPaidResult] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await axios.get(`${API}/api/wallet/payment-requests/${requestId}`);
        if (!cancelled) setReq(data);
      } catch (err) {
        if (!cancelled) setError(err.response?.data?.detail || 'Demande introuvable.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [requestId]);

  const pay = async () => {
    setPaying(true);
    try {
      const { data } = await axios.post(
        `${API}/api/wallet/payment-requests/${requestId}/fulfill`,
        {},
        { withCredentials: true }
      );
      setPaidResult(data);
      toast.success(`Paiement de ${data.amount} ${req.currency} effectué.`);
    } catch (err) {
      const detail = err.response?.data?.detail || 'Paiement impossible. Réessaie.';
      toast.error(detail);
      // Refresh request state in case it became paid/cancelled by someone else
      try {
        const refreshed = await axios.get(`${API}/api/wallet/payment-requests/${requestId}`);
        setReq(refreshed.data);
      } catch {}
    } finally {
      setPaying(false);
    }
  };

  if (loading || authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--jp-bg)' }}>
        <div className="font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>
      </div>
    );
  }

  if (error || !req) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4" style={{ background: 'var(--jp-bg)' }}>
        <div className="jp-card-elevated p-6 max-w-md w-full text-center" data-testid="pay-error">
          <Warning size={40} weight="fill" style={{ color: 'var(--jp-error, #ef4444)' }} className="mx-auto mb-3" />
          <h1 className="font-['Outfit'] text-xl font-bold mb-2" style={{ color: 'var(--jp-text)' }}>
            Demande introuvable
          </h1>
          <p className="text-sm mb-4" style={{ color: 'var(--jp-text-muted)' }}>
            {error || 'Ce lien semble invalide ou la demande a été supprimée.'}
          </p>
          <Link to="/" className="jp-btn jp-btn-primary">Retour à l'accueil</Link>
        </div>
      </div>
    );
  }

  const status = req.status;
  const isPaid = status === 'paid' || !!paidResult;
  const isCancelled = status === 'cancelled';
  const isExpired = status === 'expired';
  const isPending = status === 'pending' && !paidResult;
  const isMine = user && user.user_id === req.requester.user_id;

  return (
    <div className="min-h-screen p-4 flex items-center justify-center" style={{ background: 'var(--jp-bg)' }}>
      <div
        data-testid="pay-card"
        className="jp-card-elevated p-6 max-w-md w-full"
        style={{ background: 'var(--jp-surface)' }}
      >
        {/* Requester header */}
        <div className="flex items-center gap-3 mb-5">
          <div className="jp-avatar jp-avatar-md jp-avatar-primary text-lg">
            {req.requester.avatar
              ? <img src={req.requester.avatar} alt="" className="w-full h-full rounded-full object-cover" />
              : (req.requester.name?.[0] || '?').toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Demande de paiement de</div>
            <div className="font-['Outfit'] text-base font-bold truncate" style={{ color: 'var(--jp-text)' }}>
              {req.requester.name}
            </div>
          </div>
        </div>

        {/* Amount */}
        <div
          className="rounded-2xl p-5 text-center mb-5"
          style={{
            background: 'linear-gradient(135deg, var(--jp-primary, #4338ca) 0%, var(--jp-primary-dark, #3730a3) 100%)',
            color: 'white',
          }}
        >
          <div className="text-xs uppercase tracking-wider opacity-80 mb-1">Montant demandé</div>
          <div className="font-['Outfit'] text-4xl font-bold tracking-tight" data-testid="pay-amount">
            {parseFloat(req.amount).toLocaleString('fr-FR', { minimumFractionDigits: 2 })}
            {' '}
            <span className="text-xl ml-2 opacity-70">{req.currency}</span>
          </div>
          {req.note && (
            <div className="mt-3 text-sm opacity-90" data-testid="pay-note">« {req.note} »</div>
          )}
        </div>

        {/* Status-specific content */}
        {isPaid && (
          <div
            className="p-4 rounded-xl mb-4 flex items-start gap-3"
            data-testid="pay-status-paid"
            style={{
              background: 'var(--jp-success-subtle, rgba(34,197,94,0.10))',
              border: '1px solid var(--jp-success-muted, rgba(34,197,94,0.25))',
            }}
          >
            <CheckCircle size={24} weight="fill" style={{ color: 'var(--jp-success, #16a34a)' }} />
            <div className="text-sm">
              <div className="font-bold" style={{ color: 'var(--jp-text)' }}>Paiement effectué</div>
              <div className="text-xs mt-0.5" style={{ color: 'var(--jp-text-muted)' }}>
                {paidResult
                  ? `Référence : ${paidResult.tx_id}`
                  : `Réglé le ${req.fulfilled_at ? new Date(req.fulfilled_at).toLocaleDateString('fr-FR') : '—'}`}
              </div>
            </div>
          </div>
        )}

        {isCancelled && (
          <div
            className="p-4 rounded-xl mb-4 flex items-center gap-3"
            data-testid="pay-status-cancelled"
            style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
          >
            <X size={20} style={{ color: 'var(--jp-text-muted)' }} />
            <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>
              Cette demande a été <strong>annulée</strong> par son auteur.
            </div>
          </div>
        )}

        {isExpired && (
          <div
            className="p-4 rounded-xl mb-4 flex items-center gap-3"
            data-testid="pay-status-expired"
            style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
          >
            <Clock size={20} style={{ color: 'var(--jp-text-muted)' }} />
            <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>
              Cette demande a <strong>expiré</strong>. Demande à son auteur d'en créer une nouvelle.
            </div>
          </div>
        )}

        {/* Actions */}
        {isPending && !user && (
          <div className="space-y-3">
            <div className="text-xs text-center" style={{ color: 'var(--jp-text-muted)' }}>
              Connecte-toi pour payer en 1 clic — ou crée un compte gratuit (les nouveaux comptes recevront aussi un bonus de bienvenue).
            </div>
            <button
              data-testid="pay-login-cta"
              onClick={() => navigate(`/login?redirect=${encodeURIComponent(`/pay/${requestId}`)}`)}
              className="jp-btn jp-btn-primary w-full"
            >
              Se connecter pour payer <ArrowRight size={16} />
            </button>
            <button
              data-testid="pay-register-cta"
              onClick={() => navigate(`/register?redirect=${encodeURIComponent(`/pay/${requestId}`)}`)}
              className="jp-btn jp-btn-ghost w-full"
            >
              Créer un compte
            </button>
          </div>
        )}

        {isPending && user && isMine && (
          <div
            className="p-4 rounded-xl text-center text-sm"
            data-testid="pay-self-warning"
            style={{ background: 'var(--jp-warning-subtle, rgba(245,158,11,0.1))', color: 'var(--jp-text)' }}
          >
            Tu ne peux pas payer ta propre demande. Partage-la avec un ami pour qu'il la règle.
          </div>
        )}

        {isPending && user && !isMine && (
          <div className="space-y-3">
            <button
              data-testid="pay-confirm-button"
              onClick={pay}
              disabled={paying}
              className="jp-btn jp-btn-primary w-full disabled:opacity-50"
            >
              <Wallet size={18} />
              {paying ? 'Paiement…' : `Payer ${parseFloat(req.amount).toLocaleString('fr-FR')} ${req.currency}`}
            </button>
            <p className="text-[11px] text-center" style={{ color: 'var(--jp-text-muted)' }}>
              Le montant sera débité de ton solde JAPAP. Des frais peuvent s'appliquer selon ton profil.
            </p>
          </div>
        )}

        {/* Footer */}
        <div className="mt-6 pt-4 text-center text-[11px]" style={{ color: 'var(--jp-text-muted)', borderTop: '1px solid var(--jp-border)' }}>
          Demande sécurisée par <strong>{t('pay.japap_wallet')}</strong>
        </div>
      </div>
    </div>
  );
}
