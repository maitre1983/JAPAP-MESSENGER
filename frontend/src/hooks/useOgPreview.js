/**
 * iter212 — useOgPreview
 * ======================
 * React hook that fetches Open Graph / Twitter / meta preview data for a
 * given http(s) URL from our backend (`GET /api/og?url=...`).
 *
 * Features:
 *   • In-memory cache (module-scoped Map) — same URL is fetched at most
 *     once per session, no duplicate network calls when several posts
 *     link the same article.
 *   • In-flight dedup — if two components mount simultaneously with the
 *     same URL, only ONE request is made, all hooks resolve from it.
 *   • AbortController — cancels the request if the component unmounts.
 *   • Gracefully returns { preview: null, error } when the URL cannot be
 *     fetched so callers can fall back to a plain anchor tag.
 */
import { useEffect, useRef, useState } from 'react';

const API = process.env.REACT_APP_BACKEND_URL;

// Module-level cache: url → preview data (or null on error)
const cache = new Map();
// Module-level in-flight promise map: url → Promise<preview>
const inflight = new Map();

async function fetchPreview(url, signal) {
  if (cache.has(url)) return cache.get(url);
  if (inflight.has(url)) return inflight.get(url);

  const p = (async () => {
    try {
      const res = await fetch(
        `${API}/api/og?url=${encodeURIComponent(url)}`,
        { signal, credentials: 'omit' },
      );
      if (!res.ok) {
        cache.set(url, null);
        return null;
      }
      const data = await res.json();
      cache.set(url, data);
      return data;
    } catch (e) {
      if (e.name !== 'AbortError') cache.set(url, null);
      return null;
    } finally {
      inflight.delete(url);
    }
  })();

  inflight.set(url, p);
  return p;
}

export default function useOgPreview(url) {
  const [state, setState] = useState(() => {
    if (!url) return { preview: null, loading: false };
    if (cache.has(url)) return { preview: cache.get(url), loading: false };
    return { preview: null, loading: true };
  });

  const urlRef = useRef(url);
  urlRef.current = url;

  useEffect(() => {
    if (!url) {
      setState({ preview: null, loading: false });
      return undefined;
    }
    if (cache.has(url)) {
      setState({ preview: cache.get(url), loading: false });
      return undefined;
    }
    const ctrl = new AbortController();
    setState({ preview: null, loading: true });
    fetchPreview(url, ctrl.signal).then((preview) => {
      // Drop stale response if the URL changed.
      if (urlRef.current !== url) return;
      setState({ preview, loading: false });
    });
    return () => ctrl.abort();
  }, [url]);

  return state; // { preview, loading }
}
