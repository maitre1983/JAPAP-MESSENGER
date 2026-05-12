/**
 * iter239d — VideoPlayer
 * =======================
 * Instagram/TikTok-style video player with:
 *   • IntersectionObserver autoplay (plays when ≥60% visible, pauses when not)
 *   • Click-to-play/pause central control, lazy load via `preload="metadata"`
 *   • Thumbnail poster while video loads
 *   • Buffering spinner, scrubbable progress bar with handle
 *   • Volume mute toggle, fullscreen button, time display
 *   • Controls auto-hide after 3s of inactivity
 *
 * The video element uses `playsInline` + `muted` defaults so autoplay works
 * on iOS Safari without user gesture. The component is fully additive — no
 * existing player is removed by this commit. Callers opt in by replacing
 * `<video>` with `<VideoPlayer />`.
 */
import { useState, useRef, useEffect, useCallback } from 'react';

export default function VideoPlayer({
  videoUrl,
  thumbnailUrl,
  duration: initialDuration,
  autoplay = true,
  muted: initialMuted = true,
  loop = false,
  className = '',
  aspectRatio = '16/9',
  testId = 'video-player',
}) {
  const videoRef = useRef(null);
  const containerRef = useRef(null);
  const progressRef = useRef(null);
  const controlsTimer = useRef(null);

  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(initialMuted);
  const [isLoaded, setIsLoaded] = useState(false);
  const [isBuffering, setIsBuffering] = useState(false);
  const [progress, setProgress] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [videoDuration, setVideoDuration] = useState(initialDuration || 0);
  const [showControls, setShowControls] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  // iter239n — auto-detected aspect ratio of the loaded video. When
  // caller passes aspectRatio="auto", we let the video element flag us
  // its natural width/height once metadata is parsed and apply the real
  // ratio (portrait, landscape, square) instead of forcing a layout.
  // Caller's explicit aspectRatio always wins until metadata arrives.
  const [autoRatio, setAutoRatio] = useState(null);

  const effectiveRatio = aspectRatio === 'auto'
    ? (autoRatio || '9/16')   // sensible default while metadata loads
    : aspectRatio;

  // IntersectionObserver — autoplay when scrolled into view.
  useEffect(() => {
    if (!autoplay || !containerRef.current) return undefined;
    const observer = new IntersectionObserver(
      ([entry]) => {
        const v = videoRef.current;
        if (!v) return;
        if (entry.isIntersecting && entry.intersectionRatio >= 0.6) {
          v.play().then(() => setIsPlaying(true)).catch(() => {});
        } else {
          v.pause();
          setIsPlaying(false);
        }
      },
      { threshold: [0, 0.6, 1] },
    );
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [autoplay]);

  const formatTime = (s) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  };

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      v.play().then(() => setIsPlaying(true)).catch(() => {});
    } else {
      v.pause();
      setIsPlaying(false);
    }
  }, []);

  const onTimeUpdate = () => {
    const v = videoRef.current;
    if (!v) return;
    setCurrentTime(v.currentTime);
    setProgress((v.currentTime / (v.duration || 1)) * 100);
  };

  const seekTo = (e) => {
    const v = videoRef.current;
    if (!v || !progressRef.current) return;
    const r = progressRef.current.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    v.currentTime = ratio * (v.duration || 0);
  };

  const onMouseMove = () => {
    setShowControls(true);
    clearTimeout(controlsTimer.current);
    controlsTimer.current = setTimeout(() => setShowControls(false), 3000);
  };

  const toggleFs = () => {
    const c = containerRef.current;
    if (!c) return;
    if (!document.fullscreenElement) {
      c.requestFullscreen?.().catch(() => {});
      setIsFullscreen(true);
    } else {
      document.exitFullscreen?.();
      setIsFullscreen(false);
    }
  };

  const toggleMute = (e) => {
    e?.stopPropagation();
    const next = !isMuted;
    setIsMuted(next);
    if (videoRef.current) videoRef.current.muted = next;
  };

  return (
    <div
      ref={containerRef}
      className={`jp-video-player ${className}`}
      data-testid={testId}
      data-playing={isPlaying ? 'true' : 'false'}
      style={{
        position: 'relative', borderRadius: 12, overflow: 'hidden',
        background: '#000', aspectRatio: effectiveRatio, cursor: 'pointer',
        userSelect: 'none',
      }}
      onMouseMove={onMouseMove}
      onMouseLeave={() => setShowControls(false)}
      onClick={togglePlay}
    >
      {/* Poster before video loads */}
      {!isLoaded && thumbnailUrl && (
        <img
          src={thumbnailUrl} alt=""
          loading="lazy" decoding="async"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%',
                   objectFit: 'cover', zIndex: 1 }}
        />
      )}

      {/* Centred play indicator before first play */}
      {!isPlaying && !isLoaded && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex',
                      alignItems: 'center', justifyContent: 'center', zIndex: 2 }}>
          <div style={{
            width: 60, height: 60, borderRadius: '50%',
            background: 'rgba(0,0,0,0.6)', display: 'flex',
            alignItems: 'center', justifyContent: 'center',
            backdropFilter: 'blur(4px)',
          }}>
            <span style={{ fontSize: 24, color: '#fff', marginLeft: 4 }}>▶</span>
          </div>
        </div>
      )}

      {isBuffering && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex',
                      alignItems: 'center', justifyContent: 'center', zIndex: 3 }}>
          <div style={{
            width: 40, height: 40, border: '3px solid rgba(255,255,255,0.3)',
            borderTop: '3px solid #fff', borderRadius: '50%',
            animation: 'jpVideoSpin 1s linear infinite',
          }} />
        </div>
      )}

      <video
        ref={videoRef}
        src={videoUrl}
        poster={thumbnailUrl}
        muted={isMuted}
        loop={loop}
        playsInline
        preload="metadata"
        data-testid={`${testId}-video`}
        style={{
          width: '100%', height: '100%',
          // iter239n — `contain` instead of `cover` so portrait videos
          // keep their full frame (no top/bottom crop) and landscape
          // videos sit in a letterboxed black band — the orientation
          // detection above already sized the container to the natural
          // ratio so there's typically no visible bar at all.
          objectFit: aspectRatio === 'auto' ? 'contain' : 'cover',
          display: 'block',
        }}
        onLoadedMetadata={() => {
          const v = videoRef.current;
          if (!v) return;
          if (aspectRatio === 'auto' && v.videoWidth && v.videoHeight) {
            setAutoRatio(`${v.videoWidth}/${v.videoHeight}`);
          }
        }}
        onLoadedData={() => {
          setIsLoaded(true);
          setIsBuffering(false);
          setVideoDuration(videoRef.current?.duration || 0);
        }}
        onTimeUpdate={onTimeUpdate}
        onWaiting={() => setIsBuffering(true)}
        onPlaying={() => setIsBuffering(false)}
        onEnded={() => setIsPlaying(false)}
      />

      {/* Bottom controls — visible on hover or while paused */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: 'absolute', bottom: 0, left: 0, right: 0,
          background: 'linear-gradient(transparent, rgba(0,0,0,0.8))',
          padding: '20px 12px 12px',
          opacity: showControls || !isPlaying ? 1 : 0,
          transition: 'opacity 0.3s ease', zIndex: 4,
        }}
      >
        <div
          ref={progressRef}
          onClick={seekTo}
          data-testid={`${testId}-progress`}
          style={{
            height: 4, background: 'rgba(255,255,255,0.3)', borderRadius: 2,
            marginBottom: 10, cursor: 'pointer', position: 'relative',
          }}
        >
          <div style={{ width: `${progress}%`, height: '100%', background: '#fff',
                        borderRadius: 2, transition: 'width 0.1s linear' }} />
          <div style={{
            position: 'absolute', top: '50%', left: `${progress}%`,
            transform: 'translate(-50%, -50%)', width: 12, height: 12,
            borderRadius: '50%', background: '#fff',
            boxShadow: '0 0 4px rgba(0,0,0,0.5)',
          }} />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); togglePlay(); }}
              data-testid={`${testId}-play-pause`}
              style={{ background: 'none', border: 'none', color: '#fff',
                       fontSize: 18, cursor: 'pointer', padding: 4, display: 'flex' }}
            >
              {isPlaying ? '⏸' : '▶'}
            </button>
            <button
              type="button"
              onClick={toggleMute}
              data-testid={`${testId}-mute-toggle`}
              style={{ background: 'none', border: 'none', color: '#fff',
                       fontSize: 16, cursor: 'pointer', padding: 4 }}
            >
              {isMuted ? '🔇' : '🔊'}
            </button>
            <span style={{ color: '#fff', fontSize: 12, fontFamily: 'monospace' }}>
              {formatTime(currentTime)} / {formatTime(videoDuration)}
            </span>
          </div>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); toggleFs(); }}
            data-testid={`${testId}-fullscreen`}
            style={{ background: 'none', border: 'none', color: '#fff',
                     fontSize: 16, cursor: 'pointer', padding: 4 }}
          >
            {isFullscreen ? '⊡' : '⛶'}
          </button>
        </div>
      </div>

      <style>{`
        @keyframes jpVideoSpin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
