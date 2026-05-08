/**
 * VideoCallGrid — Sprint C scaffolding for video + group calls.
 * Renders a responsive grid of participant tiles with speaker highlight.
 * Ready to receive LiveKit Track refs once the SDK is wired in Sprint C.
 *
 * Mobile-first :
 *   - 1 column on phones (<480px)
 *   - 2 columns on small tablets (480-960px)
 *   - auto-fit on desktop
 *   - No horizontal scroll at any size
 *
 * Expected `participants` shape :
 *   [{ sid, identity, name, avatar, muted, videoOn, isSpeaking, isLocal }]
 */
import {
  Microphone, MicrophoneSlash, VideoCamera, VideoCameraSlash,
  CameraRotate, PhoneSlash, Users, Record,
} from '@phosphor-icons/react';
import { useEffect, useState } from 'react';
import './calls.css';

function fmt(sec) {
  const s = Math.max(0, Math.floor(sec));
  const mm = String(Math.floor(s / 60) % 60).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

export default function VideoCallGrid({
  session,
  participants = [],
  videoRefs = {},             // map: participant.sid → ref to <video>
  muted = false,
  videoOn = true,
  recording = false,
  canRecord = false,
  onToggleMute,
  onToggleVideo,
  onSwitchCamera,
  onToggleRecord,
  onHangup,
}) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!session?.started_at) return;
    const start = new Date(session.started_at).getTime();
    const iv = setInterval(() => setElapsed((Date.now() - start) / 1000), 1000);
    return () => clearInterval(iv);
  }, [session?.started_at]);

  if (!session) return null;

  return (
    <div className="jp-call-overlay" data-testid="video-call-grid" style={{ background: '#000' }}>
      {/* Grid of participant tiles — absolutely positioned behind chrome */}
      <div className="jp-video-grid" data-testid="video-participants-grid">
        {participants.length === 0 ? (
          <div className="jp-video-tile" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontFamily: "'Manrope', sans-serif" }}>
            En attente de participants…
          </div>
        ) : (
          participants.map((p) => {
            const initial = (p.name?.[0] || '?').toUpperCase();
            return (
              <div
                key={p.sid || p.identity}
                data-testid={`video-tile-${p.identity}`}
                className={`jp-video-tile ${p.isSpeaking ? 'jp-video-tile-active' : ''}`}>
                {p.videoOn ? (
                  <video
                    ref={(el) => { if (el && videoRefs) videoRefs[p.sid] = el; }}
                    autoPlay playsInline muted={p.isLocal}
                  />
                ) : (
                  <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <div className="jp-call-avatar" style={{ width: 80, height: 80, fontSize: 36 }}>{initial}</div>
                  </div>
                )}
                <div className="jp-video-tile-label">
                  {p.isLocal ? 'Vous' : (p.name || p.identity)}
                  {p.muted && <MicrophoneSlash size={12} style={{ marginLeft: 6, verticalAlign: 'middle' }} />}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Top chrome — floating */}
      <div className="jp-call-header" style={{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 2 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {recording && (
            <span data-testid="video-recording-indicator" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span className="jp-rec-dot" /> REC
            </span>
          )}
        </span>
        <span className="jp-call-title">
          <Users size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
          {participants.length} participant{participants.length > 1 ? 's' : ''}
        </span>
        <span className="jp-call-timer" data-testid="video-call-duration">{fmt(elapsed)}</span>
      </div>

      {/* Bottom controls — floating safe-area-aware */}
      <div className="jp-call-controls" style={{ position: 'absolute', bottom: 0, left: 0, right: 0, zIndex: 2 }}>
        <div className="jp-call-controls-row">
          <button
            type="button" onClick={onToggleMute}
            data-testid="video-mute-btn" aria-pressed={muted}
            className={`jp-call-btn ${muted ? 'jp-call-btn-active' : ''}`}>
            {muted ? <MicrophoneSlash size={22} weight="fill" /> : <Microphone size={22} weight="fill" />}
          </button>
          <button
            type="button" onClick={onToggleVideo}
            data-testid="video-cam-btn" aria-pressed={!videoOn}
            className={`jp-call-btn ${!videoOn ? 'jp-call-btn-active' : ''}`}>
            {videoOn ? <VideoCamera size={22} weight="fill" /> : <VideoCameraSlash size={22} weight="fill" />}
          </button>
          <button
            type="button" onClick={onSwitchCamera}
            data-testid="video-switch-cam-btn"
            className="jp-call-btn">
            <CameraRotate size={22} weight="fill" />
          </button>
          {canRecord && (
            <button
              type="button" onClick={onToggleRecord}
              data-testid="video-record-btn" aria-pressed={recording}
              className={`jp-call-btn ${recording ? 'jp-call-btn-active' : ''}`}>
              <Record size={22} weight="fill" color={recording ? '#E01C2E' : undefined} />
            </button>
          )}
          <button
            type="button" onClick={onHangup}
            data-testid="video-hangup-btn"
            className="jp-call-btn jp-call-btn-end">
            <PhoneSlash size={24} weight="fill" />
          </button>
        </div>
      </div>
    </div>
  );
}
