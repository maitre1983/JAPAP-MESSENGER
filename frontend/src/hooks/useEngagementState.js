/**
 * iter142D / Phase P3 — Engagement IA hook + auto-tracking helpers.
 *
 * useEngagementState — polls /engagement/me every 60s + manual invalidate
 *                      after vote/share. Returns state + message + ui_mode.
 *
 * trackEngagementEvent — fire-and-forget POST /events. Never blocks UX,
 *                        catches errors silently.
 *
 * postEngagementFeedback — record clicked/dismissed/shared on a specific
 *                          message_id (used by the banner).
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

export function trackEngagementEvent(eventType, opts = {}) {
  // Fire-and-forget — never throws, never awaits.
  axios
    .post(
      `${API}/api/crowdfunding/events`,
      {
        event_type: eventType,
        project_id: opts.project_id,
        cycle_id: opts.cycle_id,
        rank_before: opts.rank_before,
        rank_after: opts.rank_after,
        time_spent: opts.time_spent || 0,
        source: opts.source || _detectSource(),
        metadata: opts.metadata || {},
      },
      { withCredentials: true },
    )
    .catch(() => {});
}

export function postEngagementFeedback(messageId, action) {
  if (!messageId || !action) return;
  axios
    .post(
      `${API}/api/crowdfunding/engagement/feedback`,
      { message_id: messageId, action },
      { withCredentials: true },
    )
    .catch(() => {});
}

function _detectSource() {
  try {
    const ref = document.referrer || '';
    if (/whatsapp|wa\.me|api\.whatsapp/.test(ref)) return 'whatsapp';
    if (/t\.me|telegram/.test(ref)) return 'telegram';
    if (/twitter|x\.com/.test(ref)) return 'twitter';
    if (/instagram/.test(ref)) return 'instagram';
    if (/facebook/.test(ref)) return 'facebook';
    return ref ? 'referral' : 'direct';
  } catch {
    return 'direct';
  }
}

const _DISMISS_KEY = 'cf_engage_dismissed';

function _getDismissed() {
  try {
    return JSON.parse(localStorage.getItem(_DISMISS_KEY) || '{}');
  } catch {
    return {};
  }
}

function _setDismissed(map) {
  try {
    localStorage.setItem(_DISMISS_KEY, JSON.stringify(map));
  } catch {}
}

export function dismissMessageLocally(messageId) {
  if (!messageId) return;
  const m = _getDismissed();
  m[messageId] = Date.now();
  _setDismissed(m);
}

export function isMessageDismissed(messageId, withinHours = 168) {
  if (!messageId) return false;
  const m = _getDismissed();
  const ts = m[messageId];
  if (!ts) return false;
  return Date.now() - ts < withinHours * 3600 * 1000;
}

export function useEngagementState({ enabled = true, withLlm = true } = {}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef(null);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      const { data: payload } = await axios.get(
        `${API}/api/crowdfunding/engagement/me?with_llm=${withLlm}`,
        { withCredentials: true },
      );
      setData(payload);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, [enabled, withLlm]);

  useEffect(() => {
    if (!enabled) return undefined;
    refresh();
    timerRef.current = setInterval(refresh, 60_000);
    return () => clearInterval(timerRef.current);
  }, [enabled, refresh]);

  return { data, loading, refresh };
}
