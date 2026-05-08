import { useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { X, Download, WhatsappLogo, ShareNetwork, Copy, Sparkle, TwitterLogo, TelegramLogo } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * iter141nineJ — Post-creation "Share your hotspot" modal.
 *
 * Shown right after a hotspot is created. Displays the auto-generated
 * 1080×1920 PNG card (Stories-ready) and a row of 1-tap share actions.
 * Country-neutral: works for Vodacom Kenya, Orange France, AT&T US, etc.
 * because the underlying card auto-detects ISP from the visitor's IP.
 */
export default function ShareHotspotCardModal({ hotspotId, onClose }) {
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [imgLoaded, setImgLoaded] = useState(false);

  useEffect(() => {
    if (!hotspotId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await axios.get(`${API}/api/connect/hotspots/${hotspotId}/share`,
          { withCredentials: true });
        if (!cancelled) setMeta(r.data);
      } catch (err) {
        if (!cancelled) toast.error(err.response?.data?.detail || 'Carte indisponible.');
      } finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [hotspotId]);

  const copyLink = async () => {
    if (!meta) return;
    try {
      await navigator.clipboard.writeText(meta.share_url);
      toast.success('Lien copié.');
    } catch { toast.error('Copie impossible.'); }
  };

  const shareNative = async () => {
    if (!meta) return;
    if (!navigator.share) { copyLink(); return; }
    try {
      // Best-effort: share WITH the PNG file when supported (mobile),
      // otherwise just the share text + URL.
      let files;
      if (navigator.canShare && typeof fetch === 'function') {
        try {
          const r = await fetch(meta.png_url);
          const blob = await r.blob();
          const f = new File([blob], `japap-wifi-${hotspotId}.png`, { type: 'image/png' });
          if (navigator.canShare({ files: [f] })) files = [f];
        } catch {}
      }
      const payload = { title: meta.alias, text: meta.share_text, url: meta.share_url };
      if (files) payload.files = files;
      await navigator.share(payload);
    } catch {}
  };

  const downloadPng = () => {
    if (!meta) return;
    const a = document.createElement('a');
    a.href = meta.png_url;
    a.download = `japap-wifi-${hotspotId}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast.success('Téléchargement en cours… Glisse-la dans ta Story !');
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.65)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}
      data-testid="share-card-modal-overlay"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full sm:max-w-md jp-card-elevated p-5 sm:rounded-2xl rounded-t-2xl"
        style={{ background: 'var(--jp-surface)', maxHeight: '94vh', overflowY: 'auto' }}
        data-testid="share-card-modal"
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2"
              style={{ color: 'var(--jp-text)' }}>
            <Sparkle size={20} weight="fill" style={{ color: '#F7931A' }} />
            Hotspot créé !
          </h3>
          <button onClick={onClose} data-testid="share-card-close"
                  className="p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}>
            <X size={20} />
          </button>
        </div>

        <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
          Partage cette carte sur ton Statut WhatsApp / Story Instagram / TikTok.
          Chaque ami qui scanne te rejoint sur JAPAP — bonus filleul à la clé.
        </p>

        {/* Card preview */}
        <div className="rounded-xl overflow-hidden mb-3 mx-auto"
             style={{ background: '#000', aspectRatio: '9 / 16', maxHeight: 480 }}>
          {!loading && meta && (
            <img
              src={meta.png_url}
              alt="QR Card"
              className="w-full h-full object-contain"
              onLoad={() => setImgLoaded(true)}
              data-testid="share-card-preview"
            />
          )}
          {(loading || !imgLoaded) && (
            <div className="w-full h-full flex items-center justify-center text-white text-xs">
              Génération de la carte…
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="grid grid-cols-2 gap-2 mb-2">
          <a
            href={meta?.whatsapp_url || '#'}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => { if (!meta) e.preventDefault(); }}
            data-testid="share-card-whatsapp"
            className="jp-btn jp-btn-sm"
            style={{ background: '#25D366', color: '#fff' }}>
            <WhatsappLogo size={16} weight="fill" /> WhatsApp
          </a>
          <button
            onClick={shareNative}
            disabled={!meta}
            data-testid="share-card-share-native"
            className="jp-btn jp-btn-sm jp-btn-secondary disabled:opacity-50">
            <ShareNetwork size={16} /> Partager
          </button>
          <a
            href={meta?.telegram_url || '#'}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => { if (!meta) e.preventDefault(); }}
            data-testid="share-card-telegram"
            className="jp-btn jp-btn-sm jp-btn-ghost">
            <TelegramLogo size={16} /> Telegram
          </a>
          <a
            href={meta?.twitter_url || '#'}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => { if (!meta) e.preventDefault(); }}
            data-testid="share-card-twitter"
            className="jp-btn jp-btn-sm jp-btn-ghost">
            <TwitterLogo size={16} /> X
          </a>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={downloadPng}
            disabled={!meta}
            data-testid="share-card-download"
            className="jp-btn jp-btn-sm jp-btn-secondary disabled:opacity-50">
            <Download size={16} /> Télécharger PNG
          </button>
          <button
            onClick={copyLink}
            disabled={!meta}
            data-testid="share-card-copy"
            className="jp-btn jp-btn-sm jp-btn-ghost disabled:opacity-50">
            <Copy size={16} /> Copier le lien
          </button>
        </div>
      </div>
    </div>
  );
}
