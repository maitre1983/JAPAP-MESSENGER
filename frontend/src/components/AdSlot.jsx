/**
 * AdSlot — Inline sponsored content slot (feed / reel / marketplace / banner)
 * ============================================================================
 * Lazily fetches one ad from /api/ads/serve for the given slot. Reports a
 * click-through to /api/ads/campaigns/{id}/click. Renders nothing when no ad
 * is available, so it's safe to drop anywhere.
 *
 * iter171 — Click destination resolution (P0 fix):
 *   1. target_type === 'post'    → SPA-route /post/{target_id}
 *   2. target_type === 'reel'    → SPA-route /reels?id={target_id}
 *   3. target_type === 'product' → SPA-route /services?product={target_id}
 *   4. cta_url present (HTTPS)   → window.open(_blank, noopener,noreferrer)
 *   5. None of the above         → toast.info "Contenu indisponible"
 * The whole card is clickable; the inner "Voir" button delegates to the
 * same handler via the wrapping onClick.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { Megaphone, ArrowUpRight } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

function isSafeExternalUrl(u) {
  if (!u || typeof u !== 'string') return false;
  try {
    const url = new URL(u);
    // HTTPS only — refuse http:// targets to avoid mixed-content warnings
    // on the production host and reduce phishing surface.
    return url.protocol === 'https:';
  } catch { return false; }
}

export default function AdSlot({ slot = 'feed', variant = 'card' }) {
  const [ad, setAd] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;
    axios.get(`${API}/api/ads/serve?slot=${slot}`, { withCredentials: true })
      .then(({ data }) => { if (active && data?.campaign_id) setAd(data); })
      .catch(() => { /* no ad available — silent */ });
    return () => { active = false; };
  }, [slot]);

  const handleClick = useCallback(() => {
    if (!ad) return;
    // Fire & forget click tracker — never block UX on it.
    axios.post(`${API}/api/ads/campaigns/${ad.campaign_id}/click`, {},
               { withCredentials: true })
      .catch(() => {});

    const id = (ad.target_id || '').trim();
    // 1-3. Internal SPA routes by target_type.
    if (id) {
      if (ad.target_type === 'post')    { navigate(`/post/${id}`); return; }
      if (ad.target_type === 'reel')    { navigate(`/reels?id=${encodeURIComponent(id)}`); return; }
      if (ad.target_type === 'product') { navigate(`/services?product=${encodeURIComponent(id)}`); return; }
    }
    // 4. External URL fallback — open in a new, sandboxed tab.
    if (isSafeExternalUrl(ad.cta_url)) {
      window.open(ad.cta_url, '_blank', 'noopener,noreferrer');
      return;
    }
    // 5. No actionable destination — let the user know instead of failing silently.
    toast.error('Contenu indisponible — merci, on a été notifiés.', { duration: 3500 });
  }, [ad, navigate]);

  // Keyboard accessibility — Enter/Space triggers the same handler.
  const onKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleClick();
    }
  };

  if (!ad) return null;

  if (variant === 'reel') {
    return (
      <div
        role="button" tabIndex={0}
        className="relative w-full aspect-[9/16] rounded-xl overflow-hidden cursor-pointer"
        onClick={handleClick} onKeyDown={onKeyDown}
        data-testid={`ad-slot-${slot}`}
        style={{ background: 'linear-gradient(135deg, #F59E0B 0%, #EC4899 55%, #8B5CF6 100%)' }}>
        {ad.image_url && (
          <img src={ad.image_url} alt="" className="w-full h-full object-cover" />
        )}
        <div className="absolute top-3 left-3 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-widest"
          style={{ background: 'rgba(0,0,0,0.5)', color: 'white', backdropFilter: 'blur(8px)' }}>
          <Megaphone size={10} weight="fill" /> Sponsorisé
        </div>
        <div className="absolute bottom-0 left-0 right-0 p-4 text-white"
          style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.85), transparent)' }}>
          <h3 className="font-['Outfit'] font-black text-lg">{ad.title || 'Découvrez'}</h3>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); handleClick(); }}
            data-testid={`ad-cta-${slot}`}
            className="jp-btn jp-btn-sm mt-2 inline-flex items-center gap-1"
            style={{ background: 'white', color: '#EC4899' }}>
            En savoir plus <ArrowUpRight size={12} weight="bold" />
          </button>
        </div>
      </div>
    );
  }

  // Default (feed/marketplace/banner) — horizontal card
  return (
    <div
      role="button" tabIndex={0}
      className="jp-card p-4 cursor-pointer jp-card-hover relative overflow-hidden"
      onClick={handleClick} onKeyDown={onKeyDown}
      data-testid={`ad-slot-${slot}`}
      style={{ borderColor: '#EC4899', borderWidth: '1.5px', borderStyle: 'solid' }}>
      <div className="absolute top-2 right-2 inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-widest"
        style={{ background: 'linear-gradient(135deg,#F59E0B,#EC4899)', color: 'white' }}>
        <Megaphone size={9} weight="fill" /> Sponsorisé
      </div>
      <div className="flex items-start gap-3">
        {ad.image_url ? (
          <img src={ad.image_url} alt="" className="w-16 h-16 rounded-xl object-cover shrink-0" />
        ) : (
          <div className="w-16 h-16 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'linear-gradient(135deg,#F59E0B,#EC4899)', color: 'white' }}>
            <Megaphone size={22} weight="fill" />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <h4 className="font-['Outfit'] font-bold text-sm">{ad.title || 'Contenu sponsorisé'}</h4>
          {ad.cta_url && (
            <p className="text-xs truncate mt-1" style={{ color: 'var(--jp-text-muted)' }}>
              {ad.cta_url.replace(/^https?:\/\//, '').split('/')[0]}
            </p>
          )}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); handleClick(); }}
            data-testid={`ad-cta-${slot}`}
            className="jp-btn jp-btn-primary jp-btn-sm mt-2 inline-flex items-center gap-1">
            Voir <ArrowUpRight size={12} weight="bold" />
          </button>
        </div>
      </div>
    </div>
  );
}
