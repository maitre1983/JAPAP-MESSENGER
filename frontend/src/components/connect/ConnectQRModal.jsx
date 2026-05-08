/**
 * ConnectQRModal — Host-side: display a live QR code with a 60s countdown.
 *
 * When the owner clicks "Afficher QR" on one of their hotspots, this modal
 * shows a refreshing QR + a 6-digit fallback code + a copy-deeplink button.
 * The nonce auto-refreshes 1s before expiry so the display is always valid.
 *
 * Mobile-first : fills the screen on phones, centered on desktop.
 */
import { useEffect, useState, useRef, useCallback } from 'react';
import { toast } from 'sonner';
import axios from 'axios';
import QRCode from 'qrcode';
import { X, ArrowClockwise, Copy, QrCode, Timer } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ConnectQRModal({ hotspot, onClose }) {
  const [nonceData, setNonceData] = useState(null);   // {nonce, expires_at, deeplink, redeem_url}
  const [qrDataUrl, setQrDataUrl] = useState('');
  const [remaining, setRemaining] = useState(60);
  const [loading, setLoading] = useState(false);
  const refreshTimerRef = useRef(null);

  const mintNonce = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.post(
        `${API}/api/connect/hotspots/${hotspot.hotspot_id}/qr`,
        {},
        { withCredentials: true },
      );
      setNonceData(data);
      // Encode the deeplink into a QR PNG dataURL
      const dataUrl = await QRCode.toDataURL(data.deeplink, {
        width: 320, margin: 1, color: { dark: '#1a1a1a', light: '#ffffff' },
      });
      setQrDataUrl(dataUrl);
      setRemaining(data.ttl_seconds || 60);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur lors de la génération du QR.');
      onClose?.();
    } finally {
      setLoading(false);
    }
  }, [hotspot.hotspot_id, onClose]);

  // First mint + auto-renew every 60s (refresh 2s before expiry to avoid dead windows)
  useEffect(() => {
    mintNonce();
    const schedule = () => {
      refreshTimerRef.current = setTimeout(() => { mintNonce(); schedule(); }, 58_000);
    };
    schedule();
    return () => { if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current); };
  }, [mintNonce]);

  // Live countdown (1s tick)
  useEffect(() => {
    if (!nonceData?.expires_at) return;
    const exp = new Date(nonceData.expires_at).getTime();
    const iv = setInterval(() => {
      const delta = Math.max(0, Math.floor((exp - Date.now()) / 1000));
      setRemaining(delta);
    }, 1_000);
    return () => clearInterval(iv);
  }, [nonceData]);

  const copyLink = () => {
    if (!nonceData?.redeem_url) return;
    navigator.clipboard.writeText(nonceData.redeem_url).then(
      () => toast.success('Lien copié'),
      () => toast.error("Copie impossible"),
    );
  };
  // First 6 hex chars of the nonce, uppercased — shown as a fallback manual code
  const shortCode = nonceData?.nonce?.slice(0, 6).toUpperCase() || '';

  return (
    <div
      className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
      data-testid="connect-qr-modal"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full sm:w-[420px] max-h-[92vh] overflow-y-auto rounded-t-3xl sm:rounded-3xl p-6 flex flex-col items-center gap-4"
        style={{ background: 'var(--jp-surface, #fff)', color: 'var(--jp-text)' }}
      >
        <div className="w-full flex items-center justify-between">
          <div className="flex items-center gap-2">
            <QrCode size={20} weight="bold" style={{ color: 'var(--jp-primary)' }} />
            <h2 className="font-['Outfit'] font-bold text-lg">Scanner pour se connecter</h2>
          </div>
          <button onClick={onClose} className="p-2 rounded-full hover:bg-black/5" data-testid="connect-qr-close">
            <X size={20} />
          </button>
        </div>

        <p className="text-xs text-center px-2" style={{ color: 'var(--jp-text-muted)' }}>
          <strong>{hotspot.alias}</strong> · l'invité scanne ce QR avec l'app JAPAP pour débloquer l'accès WiFi.
        </p>

        <div className="relative w-64 h-64 rounded-2xl overflow-hidden flex items-center justify-center"
             style={{ background: '#ffffff', border: '1px solid rgba(0,0,0,0.06)' }}>
          {loading && !qrDataUrl && <div className="jp-spinner" />}
          {qrDataUrl && (
            <img src={qrDataUrl} alt="QR code" className="w-full h-full p-3" data-testid="connect-qr-image" />
          )}
          {remaining === 0 && (
            <div className="absolute inset-0 flex items-center justify-center bg-white/80 backdrop-blur-sm">
              <span className="text-xs font-bold" style={{ color: 'var(--jp-error)' }}>Expiré — rafraîchir</span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-semibold"
             style={{
               background: remaining <= 10 ? 'rgba(239,68,68,0.12)' : 'rgba(16,185,129,0.12)',
               color: remaining <= 10 ? '#DC2626' : '#059669',
             }}
             data-testid="connect-qr-countdown">
          <Timer size={14} weight="fill" />
          Valide encore {remaining}s · renouvellement auto
        </div>

        <div className="w-full flex flex-col items-center gap-2 pt-2 border-t"
             style={{ borderColor: 'var(--jp-border)' }}>
          <span className="text-[11px] uppercase tracking-wider font-bold mt-2"
                style={{ color: 'var(--jp-text-muted)' }}>
            Ou tapez ce code à 6 caractères
          </span>
          <span className="font-mono text-2xl font-extrabold tracking-[0.3em]"
                data-testid="connect-qr-short-code">
            {shortCode || '······'}
          </span>
        </div>

        <div className="w-full grid grid-cols-2 gap-2">
          <button onClick={copyLink}
                  className="jp-btn jp-btn-ghost text-sm"
                  data-testid="connect-qr-copy-link">
            <Copy size={14} /> Copier le lien
          </button>
          <button onClick={mintNonce} disabled={loading}
                  className="jp-btn jp-btn-primary text-sm"
                  data-testid="connect-qr-refresh">
            <ArrowClockwise size={14} /> Rafraîchir
          </button>
        </div>
      </div>
    </div>
  );
}
