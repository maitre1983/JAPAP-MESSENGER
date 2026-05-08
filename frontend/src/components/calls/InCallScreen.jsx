/**
 * InCallScreen — audio 1-1 in-call UI.
 * Sprint B scaffolding : full-screen overlay with Mute, Speaker, End,
 * Record toggle, live duration timer.
 *
 * The LiveKit track attachment will be wired in Sprint B when creds arrive.
 * For now, receives a `session` prop + callbacks; the UI chrome is ready.
 *
 * Mobile-first : safe-area insets, 56px buttons, no horizontal scroll.
 */
import { useEffect, useState } from 'react';
import { Microphone, MicrophoneSlash, SpeakerHigh, SpeakerSlash, PhoneSlash, Record } from '@phosphor-icons/react';
import './calls.css';
import { useTranslation } from 'react-i18next';

function fmt(sec) {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const mm = String(m % 60).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export default function InCallScreen({
  session,
  peerName = 'En appel',
  peerAvatar = '',
  muted = false,
  speakerOn = true,
  recording = false,
  connectionStatus = 'connected',   // connecting | connected | reconnecting | failed
  canRecord = false,                // host-only
  onToggleMute,
  onToggleSpeaker,
  onToggleRecord,
  onHangup,
}) {
  const { t } = useTranslation();
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!session?.started_at || connectionStatus !== 'connected') return;
    const start = new Date(session.started_at).getTime();
    const tick = () => setElapsed((Date.now() - start) / 1000);
    tick();
    const iv = setInterval(tick, 1000);
    return () => clearInterval(iv);
  }, [session?.started_at, connectionStatus]);

  if (!session) return null;
  const initial = (peerName?.[0] || '?').toUpperCase();
  const isConnecting = connectionStatus !== 'connected';

  return (
    <div className="jp-call-overlay" data-testid="in-call-screen" role="dialog" aria-modal="true">
      <div className="jp-call-header">
        <span>
          {recording && (
            <span data-testid="recording-indicator" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span className="jp-rec-dot" /> REC
            </span>
          )}
        </span>
        <span className="jp-call-title">Appel audio</span>
        <span>{connectionStatus === 'reconnecting' ? 'Reconnexion…' : ''}</span>
      </div>

      <div className="jp-call-body">
        <div className="jp-call-avatar">
          {peerAvatar ? (
            <img src={peerAvatar} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '50%' }} />
          ) : initial}
        </div>
        <p className="jp-call-name">{peerName}</p>
        <p className="jp-call-status">
          {isConnecting ? 'Connexion en cours…' : (
            <span className="jp-call-timer" data-testid="call-duration">{fmt(elapsed)}</span>
          )}
        </p>
      </div>

      <div className="jp-call-controls">
        <div className="jp-call-controls-row">
          <button
            type="button"
            onClick={onToggleMute}
            data-testid="call-mute-btn"
            aria-pressed={muted}
            aria-label={muted ? 'Réactiver le micro' : 'Couper le micro'}
            className={`jp-call-btn ${muted ? 'jp-call-btn-active' : ''}`}>
            {muted ? <MicrophoneSlash size={22} weight="fill" /> : <Microphone size={22} weight="fill" />}
          </button>
          <button
            type="button"
            onClick={onToggleSpeaker}
            data-testid="call-speaker-btn"
            aria-pressed={speakerOn}
            aria-label={speakerOn ? 'Couper le haut-parleur' : 'Activer le haut-parleur'}
            className={`jp-call-btn ${speakerOn ? 'jp-call-btn-active' : ''}`}>
            {speakerOn ? <SpeakerHigh size={22} weight="fill" /> : <SpeakerSlash size={22} weight="fill" />}
          </button>
          {canRecord && (
            <button
              type="button"
              onClick={onToggleRecord}
              data-testid="call-record-btn"
              aria-pressed={recording}
              aria-label={recording ? 'Arrêter l\'enregistrement' : 'Démarrer l\'enregistrement'}
              className={`jp-call-btn ${recording ? 'jp-call-btn-active' : ''}`}>
              <Record size={22} weight="fill" color={recording ? '#E01C2E' : undefined} />
            </button>
          )}
          <button
            type="button"
            onClick={onHangup}
            data-testid="call-hangup-btn"
            aria-label={t('in_call_screen.raccrocher')}
            className="jp-call-btn jp-call-btn-end">
            <PhoneSlash size={24} weight="fill" />
          </button>
        </div>
      </div>
    </div>
  );
}
