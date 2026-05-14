/**
 * iter239o — <SmartImage>
 * =======================
 * Image component that auto-detects the natural orientation of the loaded
 * image (portrait / landscape / square) and renders it inside a container
 * with a *categorical* aspect-ratio (3/4, 16/9, 1/1) so the feed stays
 * visually consistent. The image itself is `object-fit: cover` to fill
 * the container without empty bars.
 *
 * Strictly additive — does NOT replace ZoomableImage (which is still
 * used for fullscreen zoom modals). Use SmartImage for inline feed/grid
 * thumbnails; use ZoomableImage when the user explicitly opens a media
 * detail view.
 *
 * Bonus features (kept compatible with ZoomableImage's prop names):
 *  • Responsive `<picture>` with AVIF (preferred) + WebP fallbacks at
 *    480w / 1080w / 1920w via the same `smallSrc / mediumSrc / largeSrc`
 *    prop names — drop-in for callers that already wire variants.
 *  • Skeleton shimmer while the image is being downloaded + decoded.
 *  • i18n alt fallback via `t('image.error')` (rendered only on error).
 *
 * The component does NOT apply to:
 *  • Avatars (always 1/1, handled by other components).
 *  • System logos / icons.
 *  • Video thumbnails (handled by <VideoPlayer />).
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

const RATIO_BY_ORIENTATION = {
  portrait:  '3/4',
  landscape: '16/9',
  square:    '1/1',
};

function detectOrientation(w, h) {
  if (!w || !h) return null;
  if (w > h) return 'landscape';
  if (w < h) return 'portrait';
  return 'square';
}

export default function SmartImage({
  src,
  // Responsive variants — same names as ZoomableImage for drop-in compat.
  smallSrc,
  mediumSrc,
  largeSrc,
  smallSrcAvif,
  mediumSrcAvif,
  largeSrcAvif,
  alt = '',
  className = '',
  style = {},
  onClick,
  onError,
  testId = 'smart-image',
}) {
  const { t } = useTranslation();
  const [orientation, setOrientation] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  const ratio = orientation
    ? RATIO_BY_ORIENTATION[orientation]
    : '1/1';   // sensible neutral default while the image is loading

  const hasAvif = smallSrcAvif || mediumSrcAvif || largeSrcAvif;
  const hasWebp = smallSrc || mediumSrc || largeSrc;

  return (
    <div
      data-testid={testId}
      data-orientation={orientation || 'unknown'}
      onClick={onClick}
      className={className}
      style={{
        width: '100%',
        aspectRatio: ratio,
        overflow: 'hidden',
        borderRadius: 12,
        position: 'relative',
        background: loaded ? 'transparent' : '#f3f4f6',
        cursor: onClick ? 'pointer' : 'default',
        transition: 'aspect-ratio 0.2s ease',
        ...style,
      }}
    >
      {/* Shimmer skeleton while the image is downloading + decoding. */}
      {!loaded && !failed && (
        <div
          aria-hidden="true"
          style={{
            position: 'absolute', inset: 0,
            background: 'linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%)',
            backgroundSize: '200% 100%',
            animation: 'jpSmartImageShimmer 1.5s infinite',
          }}
        />
      )}

      {failed ? (
        <div
          data-testid={`${testId}-error`}
          role="img"
          aria-label={t('image.error', { defaultValue: 'Image non disponible' })}
          style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#9ca3af', fontSize: 12, fontFamily: 'Manrope, sans-serif',
            background: '#f3f4f6',
          }}
        >
          {t('image.error', { defaultValue: 'Image non disponible' })}
        </div>
      ) : (
        <picture>
          {hasAvif && (
            <source
              type="image/avif"
              srcSet={[
                smallSrcAvif  && `${smallSrcAvif} 480w`,
                mediumSrcAvif && `${mediumSrcAvif} 1080w`,
                largeSrcAvif  && `${largeSrcAvif} 1920w`,
              ].filter(Boolean).join(', ')}
              sizes="(max-width: 480px) 480px, (max-width: 1080px) 1080px, 1920px"
            />
          )}
          {hasWebp && (
            <source
              type="image/webp"
              srcSet={[
                smallSrc  && `${smallSrc} 480w`,
                mediumSrc && `${mediumSrc} 1080w`,
                largeSrc  && `${largeSrc} 1920w`,
              ].filter(Boolean).join(', ')}
              sizes="(max-width: 480px) 480px, (max-width: 1080px) 1080px, 1920px"
            />
          )}
          <img
            src={src}
            alt={alt}
            loading="lazy"
            decoding="async"
            draggable={false}
            onLoad={(e) => {
              const { naturalWidth, naturalHeight } = e.target;
              setOrientation(detectOrientation(naturalWidth, naturalHeight));
              setLoaded(true);
            }}
            onError={(e) => {
              setFailed(true);
              setLoaded(true);
              onError?.(e);
            }}
            style={{
              position: 'absolute', inset: 0,
              width: '100%', height: '100%',
              // iter240h — Portrait/Square sources stay un-cropped on a black
              // letterbox; landscape uses cover so it fills the 16/9 slot.
              // This kills the "tall photo rendered as a tiny landscape strip"
              // bug reported by users on iOS.
              objectFit: orientation === 'portrait' || orientation === 'square'
                ? 'contain' : 'cover',
              objectPosition: 'center',
              background: orientation === 'portrait' ? '#000' : 'transparent',
              display: 'block',
              transition: 'opacity 0.3s ease',
              opacity: loaded ? 1 : 0,
            }}
          />
        </picture>
      )}

      <style>{`
        @keyframes jpSmartImageShimmer {
          from { background-position: 200% 0; }
          to   { background-position: -200% 0; }
        }
      `}</style>
    </div>
  );
}
