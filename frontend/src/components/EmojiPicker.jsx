/**
 * JAPAP Messenger — Modern EmojiPicker
 * =====================================
 * Lightweight curated palette (no external lib) of trendy 2026 emojis
 * grouped by category. Used in chat compose bar + quick reactions.
 */
import { useState } from 'react';

// Categories x emojis — curated selection that matches WhatsApp / Instagram / TikTok usage 2026.
const CATEGORIES = [
  {
    id: 'smileys',
    label: 'Smileys',
    icon: '😀',
    emojis: ['😀', '😂', '🤣', '😊', '😍', '🥰', '😎', '🤩', '🥳', '😋',
             '🤗', '🤔', '😏', '😌', '😴', '🤤', '😢', '😭', '😤', '😡',
             '🥺', '😳', '🤯', '😱', '🤗', '🫣', '🫢', '🫡', '🙄', '😬'],
  },
  {
    id: 'hearts',
    label: 'Coeurs',
    icon: '❤️',
    emojis: ['❤️', '🧡', '💛', '💚', '💙', '💜', '🤎', '🖤', '🤍', '💔',
             '❣️', '💕', '💞', '💓', '💗', '💖', '💘', '💝', '💟', '♥️'],
  },
  {
    id: 'gestures',
    label: 'Gestes',
    icon: '👋',
    emojis: ['👋', '🤚', '✋', '🖖', '👌', '🤌', '🤏', '✌️', '🤞', '🫰',
             '🤟', '🤘', '🤙', '👈', '👉', '👆', '🖕', '👇', '👍', '👎',
             '✊', '👊', '🤛', '🤜', '👏', '🙌', '🫶', '👐', '🤲', '🙏'],
  },
  {
    id: 'money',
    label: 'Argent',
    icon: '💰',
    emojis: ['💰', '💸', '💵', '💴', '💶', '💷', '💳', '🧾', '💎', '🪙',
             '🤑', '🏦', '🛒', '🎁', '🔥', '⭐', '✨', '🎉', '🚀', '💯'],
  },
  {
    id: 'objects',
    label: 'Objets',
    icon: '🎉',
    emojis: ['🎉', '🎊', '🎁', '🎈', '🎂', '🍰', '🍕', '🍔', '🍟', '🌮',
             '☕', '🍺', '🥂', '🍷', '🍹', '⚽', '🏀', '🎮', '🎧', '📱',
             '💻', '📸', '🎬', '🎵', '🎤', '🚗', '🏠', '🌍', '☀️', '🌙'],
  },
];

/**
 * EmojiPicker — popover-style grid.
 * @param {(emoji: string) => void} onPick
 * @param {() => void} onClose
 */
export default function EmojiPicker({ onPick, onClose }) {
  const [activeCat, setActiveCat] = useState(CATEGORIES[0].id);
  const cat = CATEGORIES.find(c => c.id === activeCat) || CATEGORIES[0];

  return (
    <div className="absolute bottom-full left-0 mb-2 z-50 rounded-2xl shadow-2xl border jp-animate-fadeIn"
      style={{ background: 'var(--jp-surface)', borderColor: 'var(--jp-border)', width: '340px', maxWidth: 'calc(100vw - 24px)' }}
      data-testid="emoji-picker" onClick={e => e.stopPropagation()}>
      {/* Header with category tabs */}
      <div className="flex items-center justify-between px-2 py-2 border-b" style={{ borderColor: 'var(--jp-border)' }}>
        <div className="flex items-center gap-1 overflow-x-auto">
          {CATEGORIES.map(c => (
            <button key={c.id} type="button" onClick={() => setActiveCat(c.id)}
              data-testid={`emoji-cat-${c.id}`}
              aria-label={c.label}
              className="w-8 h-8 rounded-lg flex items-center justify-center text-lg transition-all flex-shrink-0"
              style={{
                background: activeCat === c.id ? 'var(--jp-primary-subtle)' : 'transparent',
                transform: activeCat === c.id ? 'scale(1.1)' : 'scale(1)',
              }}>
              {c.icon}
            </button>
          ))}
        </div>
        {onClose && (
          <button type="button" onClick={onClose} data-testid="emoji-picker-close"
            className="text-xs font-['Manrope'] font-semibold px-2 py-1 rounded"
            style={{ color: 'var(--jp-text-muted)' }}>✕</button>
        )}
      </div>
      {/* Grid */}
      <div className="p-2 grid grid-cols-8 gap-1 max-h-56 overflow-y-auto jp-scrollbar">
        {cat.emojis.map((e, i) => (
          <button key={`${cat.id}-${i}-${e}`} type="button" onClick={() => onPick(e)}
            data-testid={`emoji-pick-${e}`}
            className="w-9 h-9 rounded-lg text-xl flex items-center justify-center transition-transform hover:scale-125"
            style={{ background: 'transparent' }}
            onMouseEnter={ev => ev.currentTarget.style.background = 'var(--jp-surface-secondary)'}
            onMouseLeave={ev => ev.currentTarget.style.background = 'transparent'}>
            {e}
          </button>
        ))}
      </div>
      {/* Footer label */}
      <div className="px-3 py-1.5 text-[10px] font-['Manrope'] border-t text-center"
        style={{ borderColor: 'var(--jp-border)', color: 'var(--jp-text-muted)' }}>
        {cat.label}
      </div>
    </div>
  );
}

// Quick reaction palette (shown on bubble hover/long-press)
export const QUICK_REACTIONS = ['❤️', '🔥', '💸', '😂', '👍', '😮'];
