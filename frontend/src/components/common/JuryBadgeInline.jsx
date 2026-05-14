// iter240l — Hook + Badge JuryBadgeInline (⚖️ pill doré)
// Fetch unique de la liste des user_ids jurés actifs avec cache 5 minutes,
// partagé via un singleton module-level. Aucun nouvel endpoint requis :
// utilise GET /api/crowdfunding/jury/members existant (public).
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 min

let _cache = { set: null, ts: 0, inflight: null };

async function fetchJuryIds() {
  const now = Date.now();
  if (_cache.set && (now - _cache.ts) < CACHE_TTL_MS) return _cache.set;
  if (_cache.inflight) return _cache.inflight;
  _cache.inflight = (async () => {
    try {
      const { data } = await axios.get(`${API}/api/crowdfunding/jury/members`,
        { params: { limit: 500 } });
      const list = Array.isArray(data) ? data : (data?.members || data?.jurors || []);
      const s = new Set(list.map(m => m.user_id).filter(Boolean));
      _cache = { set: s, ts: Date.now(), inflight: null };
      return s;
    } catch {
      _cache = { set: new Set(), ts: Date.now(), inflight: null };
      return _cache.set;
    }
  })();
  return _cache.inflight;
}

export function invalidateJuryCache() { _cache = { set: null, ts: 0, inflight: null }; }

export function useJuryIds() {
  const [ids, setIds] = useState(_cache.set || new Set());
  useEffect(() => {
    let mounted = true;
    fetchJuryIds().then(s => { if (mounted) setIds(s); });
    const onRefresh = () => { invalidateJuryCache(); fetchJuryIds().then(s => { if (mounted) setIds(s); }); };
    window.addEventListener('japap:refresh', onRefresh);
    return () => { mounted = false; window.removeEventListener('japap:refresh', onRefresh); };
  }, []);
  const isJury = useCallback((uid) => uid && ids && ids.has(uid), [ids]);
  return { ids, isJury };
}

/** Badge ⚖️ pill doré à afficher inline à côté d'un nom. Rend null si pas juré. */
export default function JuryBadgeInline({ userId, size = 'sm', testId, className = '' }) {
  const { t } = useTranslation();
  const { isJury } = useJuryIds();
  if (!isJury(userId)) return null;
  const px = size === 'xs' ? 9 : size === 'sm' ? 10 : 11;
  return (
    <span
      data-testid={testId || `jury-badge-${userId}`}
      title={t('crowdfunding.jury_member', { defaultValue: 'Membre du Jury' })}
      className={className}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 3,
        padding: '1px 6px', borderRadius: 999,
        background: 'linear-gradient(135deg,#FFD700,#FFA500)',
        color: '#5b2b00', fontWeight: 700, fontSize: px,
        lineHeight: 1.2, verticalAlign: 'middle',
        boxShadow: '0 1px 2px rgba(0,0,0,0.12)',
      }}>
      ⚖️ {t('crowdfunding.jury_short', { defaultValue: 'Jury' })}
    </span>
  );
}
