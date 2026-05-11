/**
 * iter200 — <ZoomableImage>
 * =========================
 * Image cliquable qui zoome au double-tap (mobile) ou double-click (desktop).
 * - Toggle entre 1x et 2.5x au double-tap
 * - Pan tactile (drag) quand zoomée
 * - Pinch-zoom 2 doigts (1x → 4x)
 * - Bornes anti-débordement automatiques
 * - Aucune dépendance, ~120 LOC, fond noir intégré
 *
 * Usage :
 *   <ZoomableImage src={url} alt="" maxHeight="80vh" />
 *
 * Props :
 *   - src        : URL de l'image (obligatoire)
 *   - alt        : alt text
 *   - maxHeight  : hauteur max CSS (default: '80vh')
 *   - className  : classes additionnelles sur le wrapper
 *   - onError    : callback erreur img
 *   - background : couleur de fond (default: '#000')
 */
import { useRef, useState, useCallback } from 'react';

export default function ZoomableImage({
  src,
  // iter239e — optional responsive variants. When the upload endpoint
  // returned `{small_url, medium_url, large_url}` (3 WebP sizes), the feed
  // passes them down so the browser can pick the right one per device with
  // `<img srcset>`. Backwards-compatible — any falsy value is dropped and
  // the single `src` is used (legacy images keep working).
  smallSrc,
  mediumSrc,
  largeSrc,
  alt = '',
  maxHeight = '80vh',
  className = '',
  onError,
  background = '#000',
  testId,
}) {
  const wrapperRef = useRef(null);
  const [scale, setScale] = useState(1);
  const [tx, setTx] = useState(0);     // translateX
  const [ty, setTy] = useState(0);     // translateY
  const [animating, setAnimating] = useState(false);

  // Double-tap detection (mobile = no native ondblclick)
  const lastTapRef = useRef(0);
  // Pan state
  const panRef = useRef({ active: false, sx: 0, sy: 0, ox: 0, oy: 0 });
  // Pinch state
  const pinchRef = useRef({ active: false, startDist: 0, startScale: 1 });

  const ZOOM_LEVEL = 2.5;
  const MAX_ZOOM = 4;

  const clampPan = useCallback((x, y, s) => {
    const el = wrapperRef.current;
    if (!el) return { x, y };
    const w = el.clientWidth;
    const h = el.clientHeight;
    // Borne maximale = (s-1)/2 * size pour rester dans la zone visible
    const maxX = ((s - 1) * w) / 2;
    const maxY = ((s - 1) * h) / 2;
    return {
      x: Math.max(-maxX, Math.min(maxX, x)),
      y: Math.max(-maxY, Math.min(maxY, y)),
    };
  }, []);

  const toggleZoom = useCallback((cx, cy) => {
    if (scale > 1) {
      // Reset
      setAnimating(true);
      setScale(1);
      setTx(0);
      setTy(0);
      setTimeout(() => setAnimating(false), 250);
    } else {
      // Zoom centered on tap point
      const el = wrapperRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const px = (cx ?? rect.left + rect.width / 2) - rect.left;
      const py = (cy ?? rect.top + rect.height / 2) - rect.top;
      // Translate so tap point ends at center after zoom
      const newTx = (rect.width / 2 - px) * (ZOOM_LEVEL - 1);
      const newTy = (rect.height / 2 - py) * (ZOOM_LEVEL - 1);
      const clamped = clampPan(newTx, newTy, ZOOM_LEVEL);
      setAnimating(true);
      setScale(ZOOM_LEVEL);
      setTx(clamped.x);
      setTy(clamped.y);
      setTimeout(() => setAnimating(false), 250);
    }
  }, [scale, clampPan]);

  const handleTouchStart = (e) => {
    // Pinch
    if (e.touches.length === 2) {
      const dx = e.touches[0].clientX - e.touches[1].clientX;
      const dy = e.touches[0].clientY - e.touches[1].clientY;
      pinchRef.current = {
        active: true,
        startDist: Math.hypot(dx, dy),
        startScale: scale,
      };
      return;
    }
    // Single touch — detect double-tap
    const now = Date.now();
    const t = e.touches[0];
    if (now - lastTapRef.current < 280) {
      e.preventDefault();
      toggleZoom(t.clientX, t.clientY);
      lastTapRef.current = 0;
      return;
    }
    lastTapRef.current = now;
    // Pan only if zoomed
    if (scale > 1) {
      panRef.current = {
        active: true, sx: t.clientX, sy: t.clientY, ox: tx, oy: ty,
      };
    }
  };

  const handleTouchMove = (e) => {
    if (pinchRef.current.active && e.touches.length === 2) {
      e.preventDefault();
      const dx = e.touches[0].clientX - e.touches[1].clientX;
      const dy = e.touches[0].clientY - e.touches[1].clientY;
      const dist = Math.hypot(dx, dy);
      const next = Math.max(1, Math.min(MAX_ZOOM,
        pinchRef.current.startScale * (dist / pinchRef.current.startDist)));
      setScale(next);
      if (next === 1) { setTx(0); setTy(0); }
      return;
    }
    if (panRef.current.active && e.touches.length === 1) {
      e.preventDefault();
      const t = e.touches[0];
      const nx = panRef.current.ox + (t.clientX - panRef.current.sx);
      const ny = panRef.current.oy + (t.clientY - panRef.current.sy);
      const c = clampPan(nx, ny, scale);
      setTx(c.x); setTy(c.y);
    }
  };

  const handleTouchEnd = () => {
    panRef.current.active = false;
    pinchRef.current.active = false;
  };

  return (
    <div
      ref={wrapperRef}
      data-testid={testId || 'zoomable-image'}
      className={`relative w-full select-none overflow-hidden ${className}`}
      style={{ maxHeight, background, touchAction: scale > 1 ? 'none' : 'pan-y' }}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      onTouchCancel={handleTouchEnd}
      onDoubleClick={(e) => toggleZoom(e.clientX, e.clientY)}
    >
      <img
        src={src}
        srcSet={[
          smallSrc && `${smallSrc} 480w`,
          mediumSrc && `${mediumSrc} 1080w`,
          largeSrc && `${largeSrc} 1920w`,
        ].filter(Boolean).join(', ') || undefined}
        sizes={(smallSrc || mediumSrc || largeSrc)
          ? '(max-width: 480px) 480px, (max-width: 1080px) 1080px, 1920px'
          : undefined}
        alt={alt}
        draggable={false}
        loading="lazy"
        decoding="async"
        onError={onError}
        className="w-full h-full object-contain pointer-events-none"
        style={{
          maxHeight,
          transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
          transformOrigin: 'center center',
          transition: animating ? 'transform 220ms cubic-bezier(0.4, 0, 0.2, 1)' : 'none',
          willChange: 'transform',
        }}
      />
      {/* Hint au premier rendu si pas zoomé */}
      {scale === 1 && (
        <div
          className="absolute bottom-2 right-2 px-2 py-0.5 rounded-full text-[10px] font-medium opacity-0 pointer-events-none"
          style={{
            background: 'rgba(0,0,0,0.55)',
            color: 'white',
            animation: 'jp-zoom-hint 4s ease-in-out 0.5s 1 forwards',
          }}
        >
          Double-tap pour zoomer
        </div>
      )}
    </div>
  );
}
