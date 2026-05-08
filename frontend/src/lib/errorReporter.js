/**
 * errorReporter — iter108 (AI Error Monitor frontend client).
 *
 * Single-entry helper that ships frontend errors back to JAPAP via
 * POST /api/errors/report. Used by:
 *   • the global axios response interceptor (network / 5xx errors)
 *   • the React error boundary (ErrorBoundary.jsx)
 *   • window.onerror + window.onunhandledrejection (uncaught crashes)
 *
 * Throttled in-memory to avoid spamming the backend with the same error
 * dozens of times per second. Best-effort — the frontend NEVER waits for
 * the report to settle before continuing.
 */
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

// In-memory dedup: same {module, message} hash within 5s = single send.
const _seen = new Map();
const _DEDUP_MS = 5000;

function _hash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return String(h);
}

function _shouldSend(key) {
  const now = Date.now();
  const last = _seen.get(key) || 0;
  if (now - last < _DEDUP_MS) return false;
  _seen.set(key, now);
  // Garbage collect entries older than 60s
  if (_seen.size > 200) {
    for (const [k, t] of _seen) {
      if (now - t > 60000) _seen.delete(k);
    }
  }
  return true;
}

export function reportError({
  source = 'frontend',
  module = 'unknown',
  message = '',
  stack = '',
  severity = 'medium',
  url = '',
  http_status = null,
  request_id = '',
} = {}) {
  if (!message) return;
  const key = _hash(`${source}::${module}::${message}`);
  if (!_shouldSend(key)) return;
  const payload = {
    source,
    module: String(module || 'unknown').slice(0, 80),
    message: String(message).slice(0, 2000),
    stack: String(stack || '').slice(0, 8000),
    severity,
    url: url || (typeof window !== 'undefined' ? window.location.href : ''),
    user_agent: typeof navigator !== 'undefined' ? navigator.userAgent.slice(0, 255) : '',
    http_status,
    request_id,
  };
  axios.post(`${API}/api/errors/report`, payload, {
    withCredentials: true,
    timeout: 5000,
  }).catch(() => {
    // Reporter failure is silent — we never want this to surface to the user.
  });
}

/** Install the global hooks. Call once during app bootstrap. */
export function installGlobalErrorReporter() {
  if (typeof window === 'undefined') return;
  if (window.__japap_error_reporter_installed__) return;
  window.__japap_error_reporter_installed__ = true;

  window.addEventListener('error', (ev) => {
    reportError({
      module: 'window.onerror',
      message: ev?.message || 'window.onerror',
      stack: ev?.error?.stack || '',
      severity: 'high',
    });
  });

  window.addEventListener('unhandledrejection', (ev) => {
    const reason = ev?.reason;
    const message = reason?.message || String(reason || 'unhandledrejection');
    reportError({
      module: 'unhandledrejection',
      message,
      stack: reason?.stack || '',
      severity: 'high',
    });
  });

  // Axios global interceptor — capture network errors + 5xx server errors.
  // We don't capture 4xx here (those are mostly user-input issues, not bugs).
  axios.interceptors.response.use(
    (resp) => resp,
    (err) => {
      try {
        const status = err?.response?.status;
        const url = err?.config?.url || '';
        const path = url.split('?')[0].replace(API || '', '') || '/';
        const isNetwork = !err?.response;
        const isServer = status >= 500;
        if (isNetwork || isServer) {
          reportError({
            module: `axios.${err?.config?.method?.toLowerCase() || 'req'}.${path.split('/').slice(0, 4).join('.')}`,
            message: err?.response?.data?.detail || err?.message || 'axios error',
            severity: isServer ? 'high' : 'medium',
            http_status: status || null,
            url: path,
          });
        }
      } catch { /* never throw from inside interceptor */ }
      return Promise.reject(err);
    },
  );
}
