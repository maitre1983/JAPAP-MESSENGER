/**
 * Global axios + fetch CSRF/CSP bootstrap (iter82 security hardening).
 *
 *  - Adds `X-Requested-With: XMLHttpRequest` to every API call so the
 *    backend's CSRF middleware accepts cookie-authenticated POST/PUT/
 *    PATCH/DELETE originating from the SPA.
 *  - Mirrors the `csrf_token` cookie into the `X-CSRF-Token` header as
 *    defence-in-depth (double-submit pattern).
 *  - Intercepts 401 responses and tries ONE automatic `/api/auth/refresh`
 *    before surfacing the error.
 *  - Iter83 — Rewrites API calls that target the build-time
 *    REACT_APP_BACKEND_URL to `window.location.origin` whenever the app
 *    is served from a different host (typical custom-domain deploy,
 *    e.g. japapmessenger.com). This keeps every call same-origin at
 *    runtime, sidesteps the Emergent ingress CORS wildcard rewrite, and
 *    avoids the `credentials: include` + `Allow-Origin: *` browser
 *    block. The build-time env var stays useful for the preview URL
 *    where the frontend domain == backend domain.
 *
 * This file has intentionally no React imports — pure side-effect module
 * imported once at app boot.
 */
import axios from 'axios';

const BUILD_API = process.env.REACT_APP_BACKEND_URL || '';

function currentOrigin() {
  if (typeof window === 'undefined' || !window.location) return '';
  return window.location.origin;
}

function shouldRewriteToSameOrigin() {
  if (!BUILD_API) return false;
  try {
    const built = new URL(BUILD_API);
    const here = new URL(currentOrigin());
    // If we're on the exact same host as the build-time backend URL,
    // same-origin rewrite would be a no-op, so skip it.
    if (built.host === here.host) return false;
    // Otherwise the user is accessing the app from a different host
    // (custom domain, marketing host, etc.) — route API calls back to
    // the current origin so they transit the shared reverse proxy.
    return true;
  } catch { return false; }
}

function resolveApiUrl(url) {
  if (!url) return url;
  if (!BUILD_API) return url;
  if (!shouldRewriteToSameOrigin()) return url;
  if (typeof url !== 'string') return url;
  if (url.startsWith(BUILD_API)) {
    return currentOrigin() + url.slice(BUILD_API.length);
  }
  return url;
}

const API = BUILD_API;

function readCookie(name) {
  if (typeof document === 'undefined') return '';
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]+)'));
  return m ? decodeURIComponent(m[1]) : '';
}

function ensureCsrfHeaders(headers = {}) {
  const h = { ...headers };
  h['X-Requested-With'] = h['X-Requested-With'] || 'XMLHttpRequest';
  const token = readCookie('csrf_token');
  if (token && !h['X-CSRF-Token']) {
    h['X-CSRF-Token'] = token;
  }
  return h;
}

// ── Axios ─────────────────────────────────────────────────────────────
axios.defaults.withCredentials = true;
axios.interceptors.request.use((config) => {
  config.headers = ensureCsrfHeaders(config.headers || {});
  // Rewrite outgoing URLs to same-origin when the runtime host differs
  // from the build-time REACT_APP_BACKEND_URL. We only touch strings that
  // actually start with the build-time base to avoid mangling 3rd-party
  // URLs (e.g. OneSignal, Cloudinary).
  if (config.url) config.url = resolveApiUrl(config.url);
  if (config.baseURL && config.baseURL === BUILD_API) {
    const rewritten = resolveApiUrl(BUILD_API);
    if (rewritten !== BUILD_API) config.baseURL = rewritten;
  }
  return config;
});

let _refreshing = null;
let _csrfRefreshing = null;
axios.interceptors.response.use(
  (r) => r,
  async (error) => {
    const cfg = error?.config || {};
    const status = error?.response?.status;
    const detail = error?.response?.data?.detail || '';
    // Only attempt silent refresh once, and not for the refresh endpoint itself.
    const skipPaths = ['/api/auth/refresh', '/api/auth/login', '/api/auth/register'];
    const isSkip = (cfg.url || '').match(new RegExp(skipPaths.join('|').replace(/\//g, '\\/')));
    if (status === 401 && !cfg._retried && !isSkip) {
      cfg._retried = true;
      if (!_refreshing) {
        _refreshing = axios.post(resolveApiUrl(`${API}/api/auth/refresh`), {}, {
          withCredentials: true,
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
        }).finally(() => { _refreshing = null; });
      }
      try {
        await _refreshing;
        return axios(cfg);
      } catch (_) {
        // Fall through — original 401 will be thrown.
      }
    }
    // iter218 — Auto-retry on 403 CSRF (one-shot per request). Triggers
    // a token refresh via GET /api/auth/me (which mints a fresh
    // csrf_token cookie) then replays the original call exactly once.
    // This handles the rare case where the cookie expired between two
    // user actions, or a tab woke up after a long sleep.
    const isCsrf =
      status === 403 &&
      typeof detail === 'string' &&
      detail.toLowerCase().includes('csrf');
    if (isCsrf && !cfg._csrfRetry) {
      cfg._csrfRetry = true;
      if (!_csrfRefreshing) {
        _csrfRefreshing = axios.get(resolveApiUrl(`${API}/api/auth/me`), {
          withCredentials: true,
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
        }).catch(() => {})  // we don't care about the result, just the cookie
          .finally(() => { _csrfRefreshing = null; });
      }
      try {
        await _csrfRefreshing;
        // Re-read fresh CSRF cookie + replay.
        cfg.headers = ensureCsrfHeaders(cfg.headers || {});
        return axios(cfg);
      } catch (_) {
        // Fall through.
      }
    }
    // iter171 — Silent ONE retry on transient 5xx (502/503/504) and on
    // pure network errors (no response). Idempotent methods only (GET/
    // HEAD) so we never accidentally double-charge a POST.
    const method = (cfg.method || 'get').toLowerCase();
    const isIdempotent = method === 'get' || method === 'head';
    const isTransient5xx = status === 502 || status === 503 || status === 504;
    const isNetworkBlip = !error.response && !axios.isCancel?.(error);
    if (isIdempotent && !cfg._retried5xx && !isSkip && (isTransient5xx || isNetworkBlip)) {
      cfg._retried5xx = true;
      await new Promise((r) => setTimeout(r, 500));
      try {
        return await axios(cfg);
      } catch (_) {
        // Fall through to original error.
      }
    }
    return Promise.reject(error);
  },
);

// ── fetch (for places where the codebase uses raw fetch) ─────────────
//
// iter218 — robust fetch patch:
//   • Catches BOTH absolute (BUILD_API/...) AND relative (/api/...) URLs.
//     The previous version only matched the build-time absolute prefix
//     and silently let raw fetch('/api/...') calls escape without CSRF
//     headers — those would fail with "CSRF protection: missing or
//     invalid token" once the cookie wasn't pre-populated.
//   • Always applies `credentials: 'include'` for any /api/ call so
//     the auth + csrf cookies travel.
//   • Honours user-provided headers (Content-Type, Authorization, etc.)
//     and only injects CSRF defaults if missing.
if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
  const _origFetch = window.fetch;
  window.fetch = function patchedFetch(input, init = {}) {
    const url = typeof input === 'string' ? input : (input?.url || '');
    const isRelativeApi = url.startsWith('/api/');
    const isAbsoluteApi = !!(API && url.startsWith(API));
    if (!isRelativeApi && !isAbsoluteApi) {
      return _origFetch(input, init);
    }
    // Rewrite absolute build-time URL to same-origin if we're on a
    // different host (custom domain deploy).
    let target = input;
    if (isAbsoluteApi) {
      const rewritten = resolveApiUrl(url);
      if (rewritten !== url) target = rewritten;
    }
    const headers = ensureCsrfHeaders(init.headers || {});
    return _origFetch(target, { credentials: 'include', ...init, headers });
  };
}

export {};
