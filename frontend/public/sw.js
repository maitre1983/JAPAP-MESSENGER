/**
 * JAPAP Service Worker — minimalist "app-shell" caching strategy.
 *
 * Goals (iter69 PWA + iter83 static-API cache):
 *   • Satisfy Chrome/Edge/Android installability criteria (needs a fetch handler)
 *   • Give the user an instant "it opens" even on flaky mobile networks
 *   • NEVER cache the LIVE API (chat, wallet, balances, admin) — staleness
 *     would break JAPAP badly.
 *   • DO cache a tight whitelist of STATIC, low-churn API endpoints
 *     (countries, currency rates/detect/symbols) aggressively for 24h so
 *     users on 2G/3G in Africa get instant loads on repeat visits.
 *
 * Strategy:
 *   • Precache a tiny "app shell" at install (logo, manifest, offline fallback).
 *   • Runtime: network-first for HTML navigations (so every visit gets the
 *     freshest bundle), falling back to cache only when offline.
 *   • Runtime: cache-first for same-origin static assets (JS/CSS/images/fonts)
 *     — these are cache-busted by the build hash, so safe to cache aggressively.
 *   • Runtime: stale-while-revalidate for STATIC_API_WHITELIST (24h TTL).
 *   • Bypass entirely for every other /api/* and socket.io (live data).
 */
const SW_VERSION = "v25-iter241a-share";
const APP_SHELL_CACHE = `japap-shell-${SW_VERSION}`;
const RUNTIME_CACHE   = `japap-runtime-${SW_VERSION}`;
const STATIC_API_CACHE = `japap-static-api-${SW_VERSION}`;

// Endpoints whose data is CANONICAL and changes on human timescales (days).
// Any mismatch between cached and live versions is absorbed by the
// frontend fallback bundle (src/data/countries.json + currency_rates.json),
// so even a 24h-stale response can't break the UX.
const STATIC_API_WHITELIST = [
  "/api/geo/countries",
  "/api/geo/detect",
  "/api/currency/rates",
  "/api/currency/detect",
  "/api/feed/posts",      // iter198 — Cache feed 5min pour mode offline
];

const STATIC_API_TTL_MS = 24 * 60 * 60 * 1000; // 24h
const FEED_CACHE_TTL_MS = 5 * 60 * 1000;       // iter198 — 5 minutes pour le feed

const APP_SHELL = [
  "/",
  "/manifest.json",
  "/japap-logo.jpg",
  "/pwa-icon-192.png",
  "/pwa-icon-512.png",
  "/apple-touch-icon.png",
  "/offline.html",
];

// ── install: prime the shell cache ───────────────────────────────────────
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(APP_SHELL_CACHE)
      .then((c) => c.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
      .catch(() => {}) // don't block install if one asset is missing
  );
});

// ── activate: evict older versioned caches ───────────────────────────────
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) => k.startsWith("japap-") && !k.endsWith(SW_VERSION))
          .map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

// ── helpers ──────────────────────────────────────────────────────────────
function isStaticApi(url) {
  if (url.hostname !== self.location.hostname) return false;
  // Exact match on pathname — we want the whitelist to stay explicit.
  return STATIC_API_WHITELIST.includes(url.pathname);
}

function isLiveApi(url) {
  return (
    url.hostname === self.location.hostname &&
    (url.pathname.startsWith("/api/") || url.pathname.includes("/socket.io"))
  );
}

function responseAgeMs(response) {
  const header = response?.headers?.get("x-japap-sw-cached-at");
  if (!header) return Infinity;
  const t = parseInt(header, 10);
  if (!t) return Infinity;
  return Date.now() - t;
}

async function cachePut(cacheName, request, response) {
  // Clone and tag with a timestamp header so we can TTL-check it later.
  const clone = response.clone();
  const body = await clone.blob();
  const headers = new Headers(clone.headers);
  headers.set("x-japap-sw-cached-at", String(Date.now()));
  const stamped = new Response(body, {
    status: clone.status,
    statusText: clone.statusText,
    headers,
  });
  const cache = await caches.open(cacheName);
  return cache.put(request, stamped);
}

// Stale-While-Revalidate for static API:
//   - Return cache hit immediately if it's younger than TTL.
//   - Fire a network refresh in the background and update the cache on success.
//   - If cache is missing or stale, fall back to network synchronously.
async function handleStaticApi(event, request) {
  const cache = await caches.open(STATIC_API_CACHE);
  const cached = await cache.match(request);
  const fresh = cached && responseAgeMs(cached) < STATIC_API_TTL_MS;

  const networkRefresh = fetch(request)
    .then(async (res) => {
      if (res && res.ok && res.type === "basic") {
        await cachePut(STATIC_API_CACHE, request, res);
      }
      return res;
    })
    .catch(() => null);

  if (fresh) {
    // Keep the refresh alive past event.respondWith — it may silently
    // update the cache for the next visit.
    try { event.waitUntil(networkRefresh); } catch (_) {}
    return cached;
  }

  // No fresh cache → try network, fall back to stale cache if available.
  const net = await networkRefresh;
  if (net && net.ok) return net;
  if (cached) return cached;
  // Last resort: empty 504 that the fallback bundle will cover.
  return new Response(JSON.stringify({ error: "offline" }), {
    status: 504,
    headers: { "content-type": "application/json" },
  });
}

// ── fetch: route-based strategy ──────────────────────────────────────────
self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // 0) STATIC API whitelist — stale-while-revalidate (24h)
  if (isStaticApi(url)) {
    event.respondWith(handleStaticApi(event, request));
    return;
  }

  // 1) LIVE API & socket.io → never intercept
  if (isLiveApi(url)) return;

  // 2) Cross-origin → never intercept (analytics, OneSignal, CDNs).
  if (url.hostname !== self.location.hostname) return;

  // 3) Navigations → network-first with offline fallback
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then((c) => c.put(request, copy)).catch(() => {});
          return res;
        })
        .catch(async () => {
          const cached = await caches.match(request);
          if (cached) return cached;
          return caches.match("/offline.html");
        })
    );
    return;
  }

  // 4) Static assets (same-origin) → cache-first
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request)
        .then((res) => {
          if (!res || res.status !== 200 || res.type !== "basic") return res;
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then((c) => c.put(request, copy)).catch(() => {});
          return res;
        })
        .catch(() => cached);
    })
  );
});

// ─── Web Push handlers ────────────────────────────────────────────────
// Iter71: Web Push is now handled entirely by OneSignal's own Service
// Worker at `/push/onesignal/OneSignalSDKWorker.js`. Our app-shell SW
// no longer owns any push / notificationclick / pushsubscriptionchange
// listeners — doing so would hijack OneSignal's events and break push
// delivery on its scope. See `public/OneSignalSDKWorker.js` for the
// OneSignal side, and `src/services/webpush.js` for the page-level
// subscribe/unsubscribe flow.

// ── message channel: let the app trigger skipWaiting on a new SW version ─
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});
