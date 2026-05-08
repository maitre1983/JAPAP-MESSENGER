/**
 * IncomingCallOverlay — full-screen ringing UI for incoming audio/video calls.
 * Sprint B scaffolding : static layout ready to be wired to a real LiveKit
 * session + Socket.io signaling. For now, accepts `call` prop and two
 * callbacks (onAccept / onDecline) — parent controls whether to render.
 *
 * Mobile-first :
 *   - Full viewport overlay with safe-area insets (via calls.css)
 *   - Two large 56px buttons → touch-friendly
 *   - No horizontal scroll
 *   - Works on 320px and up
 */
import { useEffect } from 'react';
import { Phone, PhoneSlash, VideoCamera } from '@phosphor-icons/react';
import './calls.css';

export default function IncomingCallOverlay({ call, onAccept, onDecline }) {
  useEffect(() => {
    // Escape = decline
    const onEsc = (e) => e.key === 'Escape' && onDecline?.();
    window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [onDecline]);

  if (!call) return null;
  const {
    caller_name = 'Inconnu',
    caller_avatar = '',
    mode = 'audio',
  } = call;
  const initial = (caller_name?.[0] || '?').toUpperCase();

  return (
    <div className="jp-call-overlay" data-testid="incoming-call-overlay" role="dialog" aria-modal="true">
      <div className="jp-call-header">
        <span>{mode === 'video' ? 'Appel vidéo' : 'Appel audio'} entrant</span>
        <span />
      </div>
      <div className="jp-call-body">
        <div className="jp-call-avatar jp-ringing" aria-hidden="true">
          {caller_avatar ? (
            <img src={caller_avatar} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '50%' }} />
          ) : initial}
        </div>
        <p className="jp-call-name">{caller_name}</p>
        <p className="jp-call-status">Souhaite vous {mode === 'video' ? 'appeler en vidéo' : 'parler'}…</p>
      </div>
      <div className="jp-call-controls">
        <div className="jp-call-controls-row">
          <button
            type="button"
            onClick={onDecline}
            aria-label="Refuser"
            data-testid="incoming-call-decline"
            className="jp-call-btn jp-call-btn-end">
            <PhoneSlash size={26} weight="fill" />
          </button>
          <button
            type="button"
            onClick={onAccept}
            aria-label="Accepter"
            data-testid="incoming-call-accept"
            className="jp-call-btn jp-call-btn-accept">
            {mode === 'video' ? <VideoCamera size={26} weight="fill" /> : <Phone size={26} weight="fill" />}
          </button>
        </div>
      </div>
    </div>
  );
}
