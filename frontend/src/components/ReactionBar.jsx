/**
 * ReactionBar — 6 quick emojis (❤️😂😮😢👏🔥) for any post.
 * Long-press on heart / click on reaction count opens the picker.
 *
 * Props:
 *   postId          : string
 *   initialCounts?  : { '❤️': n, ... }
 *   initialMy?      : '❤️' | '🔥' | ...      user's current reaction
 */
import { useState, useEffect } from 'react';
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

const EMOJIS = ['❤️', '😂', '😮', '😢', '👏', '🔥'];

export default function ReactionBar({ postId, initialCounts = {}, initialMy = null }) {
  const [counts, setCounts] = useState(initialCounts);
  const [my, setMy] = useState(initialMy);
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    // Lazy-load counts on first open OR on mount if empty
    if (loaded || Object.keys(initialCounts).length > 0) return;
    axios.get(`${API}/api/feed/posts/${postId}/reactions`, { withCredentials: true })
      .then(r => { setCounts(r.data.counts || {}); setMy(r.data.my_emoji); setLoaded(true); })
      .catch(() => {});
  }, [postId, initialCounts, loaded]);

  const total = Object.values(counts).reduce((a, b) => a + b, 0);

  const pick = async (emoji) => {
    setOpen(false);
    const prev = { counts: { ...counts }, my };
    const newCounts = { ...counts };
    if (my && my !== emoji) newCounts[my] = Math.max(0, (newCounts[my] || 0) - 1);
    if (my !== emoji) newCounts[emoji] = (newCounts[emoji] || 0) + 1;
    setMy(emoji); setCounts(newCounts);
    try {
      const { data } = await axios.post(`${API}/api/feed/posts/${postId}/react`,
        { emoji }, { withCredentials: true });
      setCounts(data.counts || {});
    } catch {
      setCounts(prev.counts); setMy(prev.my);
    }
  };

  const remove = async () => {
    const prev = { counts: { ...counts }, my };
    const newCounts = { ...counts };
    if (my) newCounts[my] = Math.max(0, (newCounts[my] || 0) - 1);
    setMy(null); setCounts(newCounts); setOpen(false);
    try {
      const { data } = await axios.delete(`${API}/api/feed/posts/${postId}/react`,
        { withCredentials: true });
      setCounts(data.counts || {});
    } catch {
      setCounts(prev.counts); setMy(prev.my);
    }
  };

  return (
    <div className="relative" data-testid={`reactions-${postId}`}>
      <button type="button"
        data-testid={`reaction-toggle-${postId}`}
        onClick={() => my ? remove() : setOpen(true)}
        className="flex items-center gap-1 px-2 py-1 rounded-full text-xs transition-transform active:scale-90"
        style={{
          background: my ? 'var(--jp-primary-subtle)' : 'transparent',
          border: my ? '1px solid var(--jp-primary)' : '1px solid transparent',
        }}>
        <span className="text-base">{my || '😊'}</span>
        {total > 0 && <span className="font-['Manrope'] font-bold" style={{ color: 'var(--jp-text-secondary)' }}>{total}</span>}
      </button>
      {open && (
        <div className="absolute bottom-9 left-0 z-20 flex gap-1 px-2 py-1.5 rounded-full shadow-xl jp-animate-scaleIn"
          style={{ background: 'white', border: '1px solid var(--jp-border)' }}
          data-testid={`reaction-picker-${postId}`}>
          {EMOJIS.map(e => (
            <button key={e} type="button" onClick={() => pick(e)}
              data-testid={`react-${postId}-${e}`}
              className="text-xl transition-transform hover:scale-125 active:scale-95"
              style={{ padding: '2px 4px' }}>
              {e}
            </button>
          ))}
          <button type="button" onClick={() => setOpen(false)}
            className="text-xs px-1 opacity-50">✕</button>
        </div>
      )}
    </div>
  );
}
