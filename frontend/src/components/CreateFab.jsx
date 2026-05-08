/**
 * CreateFab — Global floating "+" button (mobile-first).
 * Visible on feed/reels/profile. Opens a speed-dial with 3 quick actions:
 *   Post / Reel / Story
 *
 * Props:
 *   onCreatePost  : () => void     (opens composer or scrolls to it)
 *   onCreateReel  : () => void     (navigates to reels creation)
 *   onCreateStory : () => void     (opens story composer)
 */
import { useState, useEffect, useRef } from 'react';
import { Plus, X, FileText, VideoCamera, Clock } from '@phosphor-icons/react';

export default function CreateFab({ onCreatePost, onCreateReel, onCreateStory }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    window.addEventListener('mousedown', close);
    return () => window.removeEventListener('mousedown', close);
  }, [open]);

  const items = [
    { id: 'post',  icon: FileText,    label: 'Publication', color: '#0F056B', onClick: onCreatePost },
    { id: 'reel',  icon: VideoCamera, label: 'Reel',        color: '#E01C2E', onClick: onCreateReel },
    { id: 'story', icon: Clock,       label: 'Story',       color: '#F59E0B', onClick: onCreateStory },
  ];

  return (
    <div ref={ref}
      className="fixed z-40 flex flex-col items-end gap-2"
      style={{ bottom: 'calc(80px + env(safe-area-inset-bottom, 0px))', right: '18px' }}
      data-testid="create-fab">
      {open && items.map((it, idx) => (
        <button key={it.id}
          onClick={() => { setOpen(false); it.onClick?.(); }}
          data-testid={`fab-create-${it.id}`}
          className="flex items-center gap-2 jp-animate-scaleIn"
          style={{ animationDelay: `${idx * 40}ms` }}>
          <span className="px-3 py-1 rounded-full text-xs font-['Manrope'] font-bold shadow-lg"
            style={{ background: 'white', color: 'var(--jp-text)' }}>
            {it.label}
          </span>
          <span className="w-11 h-11 rounded-full flex items-center justify-center shadow-xl"
            style={{ background: it.color, color: 'white' }}>
            <it.icon size={18} weight="fill" />
          </span>
        </button>
      ))}
      <button onClick={() => setOpen(v => !v)}
        data-testid="fab-main-button"
        className="w-14 h-14 rounded-full flex items-center justify-center shadow-2xl transition-transform active:scale-95"
        style={{
          background: open
            ? 'linear-gradient(135deg, #E01C2E 0%, #9333EA 100%)'
            : 'linear-gradient(135deg, #0F056B 0%, #5B21B6 100%)',
          color: 'white',
          transform: open ? 'rotate(45deg)' : 'rotate(0)',
          transition: 'transform 180ms ease, background 180ms ease',
        }}>
        {open ? <X size={26} weight="bold" /> : <Plus size={26} weight="bold" />}
      </button>
    </div>
  );
}
