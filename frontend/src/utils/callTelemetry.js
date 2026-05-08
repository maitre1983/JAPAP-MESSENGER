/**
 * iter193b — Call telemetry black box.
 *
 * Fire-and-forget POST to /api/calls/logs/client. Never throws, never
 * awaits on the hot path — if the backend is down we drop the log.
 *
 * Usage:
 *   import { callLog } from '@/utils/callTelemetry';
 *   callLog('call_button_clicked', { call_id, meta: { type: 'audio' } });
 *   callLog('permission_denied', { call_id, error });
 *
 * CEO rules enforced here:
 *   ✗ never log audio/video data
 *   ✗ never log message bodies, tokens, or passwords
 *   ✓ log user action + timestamp + device (UA) + call_id
 */
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;
const ENDPOINT = `${API}/api/calls/logs/client`;

// Rate limiter — at most 60 events / min / session to avoid flooding.
let _sent = [];
function _allowed() {
  const now = Date.now();
  _sent = _sent.filter(t => now - t < 60000);
  if (_sent.length >= 60) return false;
  _sent.push(now);
  return true;
}

export function callLog(action, opts = {}) {
  if (!action || !_allowed()) return;
  const body = {
    action,
    call_id: opts.call_id || opts.callId || null,
    room_id: opts.room_id || opts.roomId || null,
    error_name: opts.error?.name || opts.error_name || null,
    // Trim error messages defensively — backend caps at 500 chars too.
    error_message: (opts.error?.message || opts.error_message || '').slice(0, 500) || null,
    meta: sanitiseMeta(opts.meta || {}),
  };
  // Fire-and-forget. Short timeout so a stuck request doesn't pile up.
  try {
    axios.post(ENDPOINT, body, {
      withCredentials: true,
      timeout: 5000,
    }).catch(() => { /* swallow */ });
  } catch (_) { /* swallow */ }
}

/**
 * Strip anything that even looks like PII / a secret. Keeps only
 * short strings, numbers, booleans and nested objects up to depth 2.
 */
function sanitiseMeta(meta, depth = 0) {
  if (depth > 2 || meta == null) return {};
  if (typeof meta !== 'object' || Array.isArray(meta)) return {};
  const BAD = /token|secret|password|cookie|authorization|mic|camera|blob:/i;
  const out = {};
  for (const [k, v] of Object.entries(meta)) {
    if (BAD.test(k)) continue;
    if (v == null) continue;
    if (typeof v === 'string') {
      out[k] = v.length > 200 ? v.slice(0, 200) : v;
    } else if (typeof v === 'number' || typeof v === 'boolean') {
      out[k] = v;
    } else if (typeof v === 'object') {
      out[k] = sanitiseMeta(v, depth + 1);
    }
  }
  return out;
}

/**
 * Convenience: log every LiveKit room event we can subscribe to.
 * Returns an unsubscribe function to call in cleanup.
 */
export function attachRoomTelemetry(room, { call_id, room_id } = {}) {
  if (!room) return () => {};
  const fns = [];
  const on = (event, handler) => {
    try {
      room.on(event, handler);
      fns.push(() => { try { room.off(event, handler); } catch (_) {} });
    } catch (_) {}
  };
  on('connected', () => callLog('livekit_connected', {
    call_id, room_id, meta: { state: room.state } }));
  on('disconnected', (reason) => callLog('livekit_failed', {
    call_id, room_id, error_name: 'disconnected', error_message: String(reason || '') }));
  on('reconnecting', () => callLog('livekit_connecting', {
    call_id, room_id, meta: { phase: 'reconnecting' } }));
  on('reconnected', () => callLog('livekit_connected', {
    call_id, room_id, meta: { phase: 'reconnected' } }));
  on('trackSubscribed', (_track, pub, participant) => callLog(
    'remote_track_subscribed', {
      call_id, room_id,
      meta: { kind: pub?.kind, sid: participant?.sid?.slice(0, 8) },
    },
  ));
  return () => fns.forEach(f => f());
}
