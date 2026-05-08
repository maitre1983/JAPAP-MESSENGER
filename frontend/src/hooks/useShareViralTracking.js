/**
 * iter185 — Boucle virale UTM (acquisition gamifiée)
 * ====================================================
 * `useShareViralTracking` détecte un `?utm=share_xxx` dans l'URL au chargement
 * et ping `/api/seo/viral/track` pour récompenser le sharer (+50 pts JAPAP par
 * visiteur unique référé). L'anti-fraud côté backend gère la déduplication.
 *
 * `buildShareUrl({ type, entityId, sharerId })` → URL canonique avec UTM.
 *
 * Usage côté page produit/profil/post :
 *   import { useShareViralTracking } from '@/hooks/useShareViralTracking';
 *   useShareViralTracking();   // ping at mount
 *
 * Côté bouton Partager :
 *   const url = buildShareUrl({ type: 'product', entityId: id, sharerId: me.user_id });
 *   navigator.clipboard.writeText(url);
 */
import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

export function buildShareUrl({ type, entityId, sharerId, base }) {
  if (!type || !entityId || !sharerId) return null;
  const origin = base || (typeof window !== 'undefined' ? window.location.origin : '');
  const utm = `share_${sharerId}_${type}_${entityId}`;
  return `${origin}/api/seo/${type}/${entityId}?utm=${encodeURIComponent(utm)}`;
}

/**
 * Hook : ping `/api/seo/viral/track` au premier mount d'une page si l'URL
 * contient `?utm=share_*`. Idempotent (par session) : on ne ping qu'une seule
 * fois par utm/cookie pour éviter le double-comptage SPA.
 */
export function useShareViralTracking({ visitorUserId } = {}) {
  const { search } = useLocation();
  const firedRef = useRef(false);

  useEffect(() => {
    if (firedRef.current) return;
    const params = new URLSearchParams(search || '');
    const utm = params.get('utm');
    if (!utm || !utm.startsWith('share_')) return;

    // Session-level dedupe (avoid double-fire on quick remounts)
    try {
      const sk = `viral_share_fired:${utm}`;
      if (window.sessionStorage.getItem(sk)) return;
      window.sessionStorage.setItem(sk, '1');
    } catch (_) {}

    firedRef.current = true;
    axios.post(`${API}/api/seo/viral/track`,
      { utm, visitor_user_id: visitorUserId || null },
      { withCredentials: true,
        headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(({ data }) => {
        // Light log, no toast (UX should stay invisible)
        if (data?.rewarded) {
          // eslint-disable-next-line no-console
          console.info('[viral] +' + data.points_awarded + ' pts → ' + data.sharer_id);
        }
      })
      .catch(() => {});
  }, [search, visitorUserId]);
}
