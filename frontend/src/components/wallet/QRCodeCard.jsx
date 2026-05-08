/**
 * QRCodeCard — iter91
 *
 * Displays the current user's JAPAP Pay QR code (512×512 PNG served by
 * the backend). Shareable via Web Share API / download / copy link.
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { X, Download, ShareNetwork, QrCode } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function QRCodeCard({ onClose }) {
  const [payload, setPayload] = useState(null);
  const [imgSrc, setImgSrc] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    let blobUrl = '';
    (async () => {
      try {
        // Payload metadata — name, uid, currency.
        const p = await axios.get(`${API}/api/users/me/qr-payload`,
                                  { withCredentials: true });
        if (cancelled) return;
        setPayload(p.data);
        // Image as blob — `<img src>` does NOT carry the Authorization
        // header (only the cookie), so fetch it via axios and convert to
        // a local object URL. Fixes the iter106 P0 "QR vide" bug.
        const r = await axios.get(`${API}/api/users/me/qr-code.png`,
                                  { responseType: 'blob', withCredentials: true });
        if (cancelled) return;
        blobUrl = URL.createObjectURL(r.data);
        setImgSrc(blobUrl);
      } catch (e) {
        setError(e.response?.data?.detail || 'Impossible de charger votre QR code.');
      }
    })();
    return () => {
      cancelled = true;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, []);

  const doDownload = async () => {
    try {
      const r = await axios.get(`${API}/api/users/me/qr-code.png`,
                                { responseType: 'blob', withCredentials: true });
      const url = URL.createObjectURL(r.data);
      const a = document.createElement('a');
      a.href = url; a.download = `japap-qr-${payload?.uid || 'me'}.png`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch { toast.error('Téléchargement impossible'); }
  };

  const doShare = async () => {
    try {
      const r = await axios.get(`${API}/api/users/me/qr-code.png`,
                                { responseType: 'blob', withCredentials: true });
      const file = new File([r.data], 'japap-qr.png', { type: 'image/png' });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({
          files: [file],
          title: 'Mon QR JAPAP Pay',
          text: `Scannez-moi sur JAPAP pour m'envoyer ${payload?.ccy || 'XAF'} — ${payload?.name || ''}`.trim(),
        });
      } else {
        doDownload();
      }
    } catch (e) {
      if (e?.name !== 'AbortError') doDownload();
    }
  };

  const doCopyLink = async () => {
    if (!payload?.uid) return;
    const link = `${window.location.origin}/pay/${payload.uid}`;
    try {
      await navigator.clipboard.writeText(link);
      toast.success('Lien de paiement copié');
    } catch { toast.error('Copie impossible'); }
  };

  return (
    <div className="jp-card-elevated p-5 mb-6 jp-animate-scaleIn" data-testid="wallet-qr-card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2" style={{ color: 'var(--jp-text)' }}>
          <QrCode size={20} weight="fill" /> Mon QR JAPAP Pay
        </h3>
        {onClose && (
          <button onClick={onClose} className="p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}>
            <X size={18} />
          </button>
        )}
      </div>
      <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
        Montrez ce code à un autre utilisateur JAPAP — il pourra vous envoyer de l'argent en 2 taps.
      </p>
      <div className="flex items-center justify-center p-4 rounded-2xl"
           style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
        {imgSrc ? (
          <img src={imgSrc} alt="Mon QR JAPAP Pay"
               data-testid="wallet-qr-image"
               className="w-[240px] h-[240px]" />
        ) : error ? (
          <div className="text-center text-xs p-6" data-testid="wallet-qr-error"
               style={{ color: '#E01C2E' }}>
            {error}
          </div>
        ) : (
          <div className="text-center text-xs p-6 opacity-60">
            Génération du QR…
          </div>
        )}
      </div>
      {payload && (
        <div className="mt-3 text-center">
          <div className="font-semibold text-sm">{payload.name}</div>
          <div className="text-[11px] font-mono" style={{ color: 'var(--jp-text-muted)' }}>
            {payload.uid} · {payload.ccy}
          </div>
        </div>
      )}
      <div className="flex gap-2 mt-4">
        <button onClick={doShare} data-testid="wallet-qr-share"
                disabled={!imgSrc}
                className="jp-btn jp-btn-primary flex-1">
          <ShareNetwork size={14} weight="bold" /> Partager
        </button>
        <button onClick={doDownload} data-testid="wallet-qr-download"
                disabled={!imgSrc}
                className="jp-btn jp-btn-ghost flex-1">
          <Download size={14} weight="bold" /> Télécharger
        </button>
      </div>
      <button onClick={doCopyLink} data-testid="wallet-qr-copy-link"
              disabled={!payload?.uid}
              className="jp-btn jp-btn-ghost w-full mt-2 text-xs">
        Copier mon lien de paiement
      </button>
    </div>
  );
}
