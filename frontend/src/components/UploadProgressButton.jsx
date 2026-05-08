/**
 * iter220 — UploadProgressButton
 *
 * Drop-in replacement for the "Publier" submit button shown during a
 * media upload. While idle it looks like a normal jp-btn-primary; once
 * `progress > 0` it morphs into a compact circular progress indicator
 * (SVG) overlaid with a phase label so users know exactly what's
 * happening on slow 3G/4G connections.
 *
 * Phases (mapped from the parent's state machine):
 *   • upload      → 0-80 %  : real network bytes uploaded
 *   • processing  → 80-95 % : server-side transcode / thumbnail
 *   • publishing  → 95-100 %: final POST that creates the post/reel
 *
 * Why a single button instead of a separate progress bar?
 *   • Mobile portrait: vertical real estate is precious — keeping the
 *     CTA itself active as the progress carrier preserves layout.
 *   • Tap-target stays the same so users who panic-tap a second time
 *     don't end up triggering an unrelated control.
 */
import { CheckCircle, Spinner } from '@phosphor-icons/react';

const PHASE_LABELS = {
  upload:     'Téléversement…',
  processing: 'Préparation…',
  publishing: 'Publication…',
  idle:       '',
};

export default function UploadProgressButton({
  progress = 0,           // 0..100
  phase = 'idle',         // 'idle' | 'upload' | 'processing' | 'publishing'
  disabled = false,
  idleLabel = 'Publier',
  testId = 'upload-progress-btn',
  className = 'jp-btn jp-btn-primary jp-btn-full jp-btn-lg',
  type = 'submit',
  onClick,
  onPointerDown,
  style,
  title,
}) {
  const active = phase !== 'idle' || progress > 0;
  const done = progress >= 100;
  const safe = Math.max(0, Math.min(100, progress));
  const radius = 14;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (safe / 100) * circumference;

  return (
    <button
      type={type}
      onClick={onClick}
      onPointerDown={onPointerDown}
      title={title}
      disabled={disabled || (active && !done)}
      data-testid={testId}
      data-progress={safe}
      data-phase={phase}
      className={className}
      style={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 10,
        opacity: disabled ? 0.55 : 1,
        ...style,
      }}
    >
      {!active && <span data-testid={`${testId}-idle-label`}>{idleLabel}</span>}

      {active && (
        <>
          <span
            aria-hidden="true"
            style={{
              position: 'relative',
              width: 32,
              height: 32,
              flexShrink: 0,
            }}
          >
            <svg width="32" height="32" viewBox="0 0 32 32" style={{ transform: 'rotate(-90deg)' }}>
              {/* Track */}
              <circle
                cx="16" cy="16" r={radius}
                fill="none"
                stroke="rgba(255,255,255,0.25)"
                strokeWidth="3"
              />
              {/* Progress */}
              <circle
                cx="16" cy="16" r={radius}
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={offset}
                style={{
                  transition: 'stroke-dashoffset 200ms linear',
                  color: '#fff',
                }}
              />
            </svg>
            <span
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 9,
                fontWeight: 800,
                color: '#fff',
                lineHeight: 1,
              }}
            >
              {done ? <CheckCircle size={16} weight="fill" /> : `${safe}%`}
            </span>
            {!done && phase === 'processing' && (
              <span
                aria-hidden="true"
                style={{
                  position: 'absolute',
                  top: -2, right: -2,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 12, height: 12,
                  borderRadius: '50%',
                  background: '#fff',
                  color: '#E01C2E',
                }}
              >
                <Spinner size={9} weight="bold" className="animate-spin" />
              </span>
            )}
          </span>
          <span
            data-testid={`${testId}-phase-label`}
            style={{
              fontWeight: 700,
              fontSize: 14,
              letterSpacing: 0.2,
              whiteSpace: 'nowrap',
            }}
          >
            {done ? 'Terminé !' : (PHASE_LABELS[phase] || PHASE_LABELS.upload)}
          </span>
        </>
      )}
    </button>
  );
}
