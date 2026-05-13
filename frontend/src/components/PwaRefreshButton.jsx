/**
 * iter239x — PwaRefreshButton
 * ----------------------------------------------------------------
 * Bouton de synchronisation manuelle PWA, visible en permanence dans
 * le header. Idéal pour les utilisateurs PWA installés qui veulent
 * forcer une actualisation sans recharger toute la page.
 *
 * Fonctions :
 *   1. Détecte une nouvelle version SW (event 'updatefound')
 *   2. Au clic : update SW + invalide caches API + émet
 *      window event 'japap:refresh' que toutes les pages écoutent
 *      pour re-fetch leurs données sans recharger.
 *   3. Si nouvelle version dispo après update → reload pour activer.
 *   4. Badge rouge "Mise à jour disponible" si SW update en attente.
 *
 * 5 langues. data-testid stable. Animation @keyframes spin (index.css).
 */
import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';

export default function PwaRefreshButton({ className, compact = false }) {
  const { t } = useTranslation();
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [newVersionAvailable, setNewVersionAvailable] = useState(false);

  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    let interval;
    navigator.serviceWorker.ready
      .then((registration) => {
        registration.addEventListener('updatefound', () => {
          setNewVersionAvailable(true);
        });
        // Auto-check every 5 minutes
        interval = setInterval(() => {
          registration.update().catch(() => {});
        }, 5 * 60 * 1000);
      })
      .catch(() => {});
    return () => { if (interval) clearInterval(interval); };
  }, []);

  const formatTimeAgo = useCallback((date) => {
    if (!date) return '';
    const sec = Math.floor((Date.now() - date.getTime()) / 1000);
    if (sec < 60) return t('pwa.last_refresh', { time: `${sec}s` });
    if (sec < 3600) return t('pwa.last_refresh', { time: `${Math.floor(sec / 60)}m` });
    return t('pwa.last_refresh', { time: `${Math.floor(sec / 3600)}h` });
  }, [t]);

  const handleRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      // 1. Check SW for update
      if ('serviceWorker' in navigator) {
        try {
          const registration = await navigator.serviceWorker.ready;
          await registration.update();
        } catch { /* silent */ }
      }
      // 2. Invalidate API caches (keep app shell)
      if ('caches' in window) {
        try {
          const cacheNames = await caches.keys();
          await Promise.all(
            cacheNames
              .filter((name) => name.includes('japap-api') || name.includes('api-cache'))
              .map((name) => caches.delete(name)),
          );
        } catch { /* silent */ }
      }
      // 3. If new SW version is waiting → reload to activate
      if (newVersionAvailable) {
        // skipWaiting + reload
        if ('serviceWorker' in navigator) {
          try {
            const reg = await navigator.serviceWorker.getRegistration();
            if (reg?.waiting) reg.waiting.postMessage({ type: 'SKIP_WAITING' });
          } catch { /* silent */ }
        }
        setTimeout(() => window.location.reload(), 200);
        return;
      }
      // 4. Otherwise → trigger soft refresh on all pages
      window.dispatchEvent(new CustomEvent('japap:refresh', { detail: { ts: Date.now() } }));
      setLastRefresh(new Date());
    } finally {
      setRefreshing(false);
    }
  }, [refreshing, newVersionAvailable]);

  const baseStyle = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: compact ? '6px 10px' : '8px 14px',
    borderRadius: 20,
    border: newVersionAvailable ? '2px solid #e91e63' : '1px solid #e5e7eb',
    background: newVersionAvailable ? '#fce7f3' : '#fff',
    color: newVersionAvailable ? '#e91e63' : '#475569',
    fontSize: compact ? 12 : 13,
    fontWeight: newVersionAvailable ? 700 : 500,
    cursor: refreshing ? 'wait' : 'pointer',
    transition: 'all 0.2s ease',
    whiteSpace: 'nowrap',
    lineHeight: 1.2,
  };

  return (
    <button
      type="button"
      onClick={handleRefresh}
      disabled={refreshing}
      title={t('pwa.refresh_tooltip', { defaultValue: 'Synchroniser avec le serveur' })}
      data-testid="pwa-refresh-btn"
      data-update-available={newVersionAvailable ? 'yes' : 'no'}
      className={className}
      style={baseStyle}>
      <span
        aria-hidden
        style={{
          display: 'inline-block',
          animation: refreshing ? 'japap-spin 1s linear infinite' : 'none',
        }}>
        🔄
      </span>
      {!compact && (
        <span>
          {newVersionAvailable
            ? t('pwa.update_available_short', { defaultValue: 'Mise à jour !' })
            : refreshing
              ? t('pwa.refreshing', { defaultValue: 'Actualisation…' })
              : t('pwa.refresh_btn', { defaultValue: 'Actualiser' })}
        </span>
      )}
      {lastRefresh && !refreshing && !newVersionAvailable && !compact && (
        <span style={{ fontSize: 10, color: '#9ca3af', marginLeft: 2 }}
              data-testid="pwa-refresh-lastago">
          {formatTimeAgo(lastRefresh)}
        </span>
      )}
    </button>
  );
}
