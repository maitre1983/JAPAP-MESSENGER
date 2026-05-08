/**
 * iter137 — Global axios interceptor: auto-report client-visible API errors
 * to the AI Error Monitor pipeline so admin sees them in <5min instead of
 * waiting for user feedback.
 *
 * Behaviour:
 *   • Reports ALL responses with HTTP status >= 500 as severity='high'
 *   • Reports 422 (Pydantic validation errors) as severity='medium' since
 *     these usually mean a frontend ⇄ backend schema drift (the bug class
 *     that crashed the Quiz admin save in iter136).
 *   • Throttles to 1 report per signature per 60s (client-side) to avoid
 *     storms when an endpoint is down.
 *   • Skips reports for the report endpoint itself (avoid recursion).
 *   • Skips noisy network-cancelled requests (axios CanceledError).
 *
 * Install once at app boot from index.js or App.js.
 */
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

const _seen = new Map();           // signature → last_sent_at_ms
const THROTTLE_MS = 60_000;

function _shouldSkip(error) {
  if (!error) return true;
  if (axios.isCancel?.(error)) return true;
  const url = error.config?.url || '';
  if (url.includes('/api/errors/report')) return true;
  // No response → genuine network error, browser will already surface it.
  if (!error.response) return true;
  const st = error.response.status;
  return st < 422 || (st !== 422 && st < 500);
}

function _signature(error) {
  const cfg = error.config || {};
  const url = (cfg.url || '').replace(/\/\d+/g, '/:id').slice(0, 120);
  const status = error.response?.status || 0;
  return `${cfg.method || 'GET'} ${url} ${status}`;
}

function _stringifyDetail(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(d => {
      if (typeof d === 'string') return d;
      const loc = Array.isArray(d?.loc) ? d.loc.filter(x => x !== 'body').join('.') : '';
      const msg = d?.msg || d?.message || 'invalid';
      return loc ? `${loc}: ${msg}` : msg;
    }).join(' · ');
  }
  if (typeof detail === 'object' && detail) return JSON.stringify(detail).slice(0, 500);
  return '';
}

export function installAxiosErrorReporter() {
  axios.interceptors.response.use(
    (response) => response,
    async (error) => {
      try {
        if (_shouldSkip(error)) throw error;
        const sig = _signature(error);
        const now = Date.now();
        const last = _seen.get(sig);
        if (last && now - last < THROTTLE_MS) throw error;
        _seen.set(sig, now);

        const status = error.response?.status || 0;
        const detail = _stringifyDetail(error.response?.data?.detail || error.response?.data);
        const url = error.config?.url || '';
        const method = (error.config?.method || 'GET').toUpperCase();
        const message = `${method} ${url} → ${status}${detail ? ' · ' + detail : ''}`.slice(0, 2000);
        const severity = status >= 500 ? 'high' : 'medium';
        const module = `api/${url.replace(/^.*\/api\//, '').split('?')[0].split('/').slice(0, 3).join('/').slice(0, 70) || 'unknown'}`;

        const payload = {
          source: 'frontend',
          module,
          message,
          stack: '',           // network errors don't have one — keep slim
          severity,
          url: window.location.href.slice(0, 500),
          user_agent: navigator.userAgent.slice(0, 255),
          http_status: status,
          request_id: error.response?.headers?.['x-request-id'] || '',
        };

        // Fire & forget, never block the original error from propagating.
        const body = JSON.stringify(payload);
        try {
          if (navigator.sendBeacon) {
            const blob = new Blob([body], { type: 'application/json' });
            navigator.sendBeacon(`${API}/api/errors/report`, blob);
          } else {
            fetch(`${API}/api/errors/report`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body,
              credentials: 'include',
              keepalive: true,
            }).catch(() => {});
          }
        } catch { /* never let reporting break the app */ }
      } catch { /* fall through to reject */ }
      return Promise.reject(error);
    },
  );
}

export default installAxiosErrorReporter;
