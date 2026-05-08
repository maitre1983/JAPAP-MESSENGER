import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Clock, CheckCircle, ShareNetwork, Trash, Copy, WhatsappLogo, X, ArrowClockwise } from '@phosphor-icons/react';
import { useCurrency, formatMoney } from '@/utils/currency';
import * as WS from '../../utils/walletSecurity';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * iter192 — If the request was created under the USD-canonical flow,
 * `amount_usd` is filled and we format it with the user's display
 * currency. Falls back to the legacy `amount + currency` display for
 * records created before the USD canonicalisation (keeps historical
 * data readable instead of silently converting with a stale rate).
 */
function displayAmount(req, ctx) {
  if (req && req.amount_usd != null && Number.isFinite(+req.amount_usd)) {
    return formatMoney(req.amount_usd, ctx, { short: false });
  }
  const amount = parseFloat(req?.amount ?? 0);
  const cur = req?.currency || 'USD';
  return `${amount.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} ${cur}`;
}

/**
 * iter141nineC — "Mes demandes en cours" widget.
 *
 * Lists the calling user's last N pending + recently-paid payment_requests
 * with single-tap re-share (WhatsApp / Copy / Native share) and Cancel.
 *
 * Closes the virality loop: even after a request is created, the user can
 * one-tap re-share the link to hesitating contacts ("relance" pattern).
 */
function formatRelative(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return 'à l\'instant';
  if (mins < 60) return `il y a ${mins} min`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `il y a ${hrs} h`;
  const days = Math.round(hrs / 24);
  return `il y a ${days} j`;
}

function formatExpiresIn(iso) {
  if (!iso) return '';
  const diff = new Date(iso).getTime() - Date.now();
  if (diff <= 0) return 'expirée';
  const hrs = Math.round(diff / 3600000);
  if (hrs < 24) return `expire dans ${hrs} h`;
  const days = Math.round(hrs / 24);
  return `expire dans ${days} j`;
}

function buildPayUrl(requestId) {
  const origin = window.location.origin;
  return `${origin}/pay/${requestId}`;
}

// iter141nineE — OG-rich share URL. WhatsApp / iMessage / SMS scrapers
// fetch this URL and render a preview card with the requester name +
// amount + note. Real users hit it and meta-refresh redirects to the
// SPA at /pay/<id> within ~50ms.
function buildShareUrl(requestId) {
  return `${window.location.origin}/api/og/pay/${requestId}`;
}

function buildWhatsAppUrl(requestId, amount, currency, note) {
  const url = buildShareUrl(requestId);
  const safeNote = WS.sanitizeNote(note);
  const text = `Salut 👋, je te demande ${amount} ${currency}${safeNote ? ' pour ' + safeNote : ''}. Paie-moi en 1 clic sur JAPAP : ${url}`;
  return `https://wa.me/?text=${encodeURIComponent(text)}`;
}

export default function PaymentRequestsWidget() {
  const currencyCtx = useCurrency();
  const [pending, setPending] = useState([]);
  const [recentPaid, setRecentPaid] = useState([]);
  const [loading, setLoading] = useState(true);
  const [shareOpen, setShareOpen] = useState(null);    // request_id currently in re-share popup

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, paid] = await Promise.all([
        axios.get(`${API}/api/wallet/payment-requests?status=pending&limit=10`, { withCredentials: true }),
        axios.get(`${API}/api/wallet/payment-requests?status=paid&limit=5`, { withCredentials: true }),
      ]);
      setPending(p.data || []);
      // Only keep last 24h paid requests so the widget stays focused.
      const cutoff = Date.now() - 24 * 3600_000;
      setRecentPaid((paid.data || []).filter(r =>
        r.fulfilled_at && new Date(r.fulfilled_at).getTime() > cutoff
      ));
    } catch {
      // silent — widget is optional
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const cancel = async (rid) => {
    if (!window.confirm('Annuler cette demande de paiement ?')) return;
    try {
      await axios.delete(`${API}/api/wallet/payment-requests/${rid}`, { withCredentials: true });
      toast.success('Demande annulée.');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Annulation impossible.');
    }
  };

  const copy = async (rid) => {
    try {
      await navigator.clipboard.writeText(buildShareUrl(rid));
      toast.success('Lien copié.');
    } catch {
      toast.error('Impossible de copier.');
    }
  };

  const shareNative = async (req) => {
    const url = buildShareUrl(req.request_id);
    const text = `Salut 👋, je te demande ${req.amount} ${req.currency}${req.note ? ' pour ' + req.note : ''}. Paie-moi en 1 clic sur JAPAP.`;
    if (!navigator.share) {
      copy(req.request_id);
      return;
    }
    try {
      await navigator.share({ title: 'Demande de paiement JAPAP', text, url });
    } catch {}
  };

  if (loading) return null;
  if (pending.length === 0 && recentPaid.length === 0) return null;

  return (
    <div className="jp-card-elevated p-4 sm:p-5 mb-6" data-testid="payment-requests-widget">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-['Outfit'] text-base font-bold" style={{ color: 'var(--jp-text)' }}>
          Mes demandes en cours
        </h3>
        <button
          data-testid="payment-requests-refresh"
          onClick={load}
          className="p-1.5 rounded-lg"
          style={{ color: 'var(--jp-text-muted)' }}
          aria-label="Rafraîchir"
        >
          <ArrowClockwise size={16} />
        </button>
      </div>

      {/* PENDING list */}
      {pending.length > 0 && (
        <ul className="space-y-2 mb-3">
          {pending.map(req => (
            <li
              key={req.request_id}
              data-testid={`pending-request-${req.request_id}`}
              className="rounded-xl p-3 flex items-center gap-3"
              style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
            >
              <Clock size={18} style={{ color: 'var(--jp-warning, #F59E0B)' }} />
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-1.5">
                  <span className="font-['Outfit'] font-bold" style={{ color: 'var(--jp-text)' }}>
                    {parseFloat(req.amount).toLocaleString('fr-FR')} {req.currency}
                  </span>
                  {req.note && (
                    <span className="text-[11px] truncate" style={{ color: 'var(--jp-text-muted)' }}>
                      · {WS.sanitizeNote(req.note)}
                    </span>
                  )}
                </div>
                <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                  {formatRelative(req.created_at)} · {formatExpiresIn(req.expires_at)}
                </div>
              </div>
              <button
                data-testid={`reshare-button-${req.request_id}`}
                onClick={() => setShareOpen(req)}
                className="jp-btn jp-btn-xs jp-btn-secondary"
              >
                <ShareNetwork size={13} /> Re-partager
              </button>
              <button
                data-testid={`cancel-button-${req.request_id}`}
                onClick={() => cancel(req.request_id)}
                className="p-1.5 rounded-lg"
                style={{ color: 'var(--jp-text-muted)' }}
                aria-label="Annuler"
              >
                <Trash size={15} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* RECENTLY PAID — celebratory chips */}
      {recentPaid.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-wider mb-1.5" style={{ color: 'var(--jp-text-muted)' }}>
            Récemment payées (24h)
          </div>
          <ul className="space-y-1.5">
            {recentPaid.map(req => (
              <li
                key={req.request_id}
                data-testid={`paid-request-${req.request_id}`}
                className="rounded-lg px-3 py-2 flex items-center gap-2 text-sm"
                style={{
                  background: 'var(--jp-success-subtle, rgba(34,197,94,0.08))',
                  border: '1px solid var(--jp-success-muted, rgba(34,197,94,0.18))',
                }}
              >
                <CheckCircle size={16} weight="fill" style={{ color: 'var(--jp-success, #16a34a)' }} />
                <span className="font-bold" style={{ color: 'var(--jp-text)' }}>
                  +{displayAmount(req, currencyCtx)}
                </span>
                {req.note && (
                  <span className="text-[11px] truncate flex-1" style={{ color: 'var(--jp-text-muted)' }}>
                    · {WS.sanitizeNote(req.note)}
                  </span>
                )}
                <span className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                  {formatRelative(req.fulfilled_at)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Re-share popup */}
      {shareOpen && (
        <div
          data-testid="reshare-popup-overlay"
          className="fixed inset-0 z-[80] flex items-end sm:items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(6px)' }}
          onClick={() => setShareOpen(null)}
        >
          <div
            data-testid="reshare-popup"
            onClick={(e) => e.stopPropagation()}
            className="w-full sm:max-w-md jp-card-elevated p-5 sm:rounded-2xl rounded-t-2xl"
            style={{ background: 'var(--jp-surface)' }}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-['Outfit'] font-bold" style={{ color: 'var(--jp-text)' }}>
                Re-partager la demande
              </h3>
              <button onClick={() => setShareOpen(null)} className="p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}>
                <X size={18} />
              </button>
            </div>
            <div className="text-sm mb-3" style={{ color: 'var(--jp-text-muted)' }}>
              <strong style={{ color: 'var(--jp-text)' }}>
                {displayAmount(shareOpen, currencyCtx)}
              </strong>
              {shareOpen.note ? ` · ${WS.sanitizeNote(shareOpen.note)}` : ''}
            </div>
            <div
              className="p-2.5 rounded-lg text-[11px] break-all font-mono mb-3"
              style={{
                background: 'var(--jp-surface-secondary)',
                color: 'var(--jp-text-secondary)',
                border: '1px solid var(--jp-border)',
              }}
            >
              {buildPayUrl(shareOpen.request_id)}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <a
                data-testid="reshare-whatsapp"
                href={buildWhatsAppUrl(shareOpen.request_id, shareOpen.amount, shareOpen.currency, shareOpen.note)}
                target="_blank"
                rel="noopener noreferrer"
                className="jp-btn jp-btn-sm"
                style={{ background: '#25D366', color: 'white' }}
                onClick={() => setShareOpen(null)}
              >
                <WhatsappLogo size={16} weight="fill" /> WhatsApp
              </a>
              <button
                data-testid="reshare-native"
                onClick={() => { shareNative(shareOpen); setShareOpen(null); }}
                className="jp-btn jp-btn-sm jp-btn-secondary"
              >
                <ShareNetwork size={16} /> Partager
              </button>
              <button
                data-testid="reshare-copy"
                onClick={() => { copy(shareOpen.request_id); setShareOpen(null); }}
                className="jp-btn jp-btn-sm jp-btn-ghost col-span-2"
              >
                <Copy size={16} /> Copier le lien
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
