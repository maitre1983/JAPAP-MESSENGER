/**
 * iter237q — MediaPreviewGrid
 *
 * Grille adaptative pour la prévisualisation des médias (images/vidéos)
 * avant publication. Affiche entre 1 et 4 vignettes maximum dans le
 * composer ; au-delà, le 4e tile montre un overlay "+N".
 *
 * Layout :
 *   1 média  → 1 col, ratio 16/9
 *   2 médias → 2 cols, ratios 1/1
 *   3 médias → 3 cols, ratios 1/1
 *   4+ médias → 2×2, le 4e affiche overlay "+N"
 *
 * Chaque vignette propose :
 *   - bouton « × » en haut à droite pour retirer le média
 *   - badge bas-gauche pour les vidéos
 *   - placeholder vidéo (1ère frame) côté navigateur via <video muted>
 *
 * Props :
 *   files    — File[]
 *   onRemove — (idx: number) => void
 */
import { useEffect, useMemo, useState } from 'react';
import { VideoCamera, X } from '@phosphor-icons/react';

export default function MediaPreviewGrid({ files, onRemove }) {
  // Cache per-file blob URLs; revoke on unmount to avoid memory leaks.
  const urls = useMemo(
    () => files.map(f => URL.createObjectURL(f)),
    [files]
  );
  useEffect(() => () => urls.forEach(u => URL.revokeObjectURL(u)), [urls]);

  const count = files.length;
  if (count === 0) return null;

  const visible = files.slice(0, 4);
  const overflow = Math.max(0, count - 4);

  // Choose a CSS grid based on visible count.
  const gridClass =
    visible.length === 1 ? 'grid-cols-1'
    : visible.length === 2 ? 'grid-cols-2'
    : visible.length === 3 ? 'grid-cols-3'
    : 'grid-cols-2';

  return (
    <div
      className={`grid gap-2 mt-3 ${gridClass}`}
      data-testid="media-preview-grid"
    >
      {visible.map((f, idx) => {
        const isVideo = f.type.startsWith('video/');
        const isLast = idx === 3 && overflow > 0;
        const aspect = visible.length === 1 ? '16/9' : '1/1';
        return (
          <div
            key={`${f.name}-${idx}`}
            className="relative rounded-xl overflow-hidden"
            data-testid={`media-preview-tile-${idx}`}
            style={{
              aspectRatio: aspect,
              background: 'rgba(15,5,107,0.04)',
            }}
          >
            {isVideo ? (
              <Thumbnail url={urls[idx]} isVideo />
            ) : (
              <Thumbnail url={urls[idx]} />
            )}

            {/* Overflow overlay — shown on 4th tile only. */}
            {isLast && (
              <div
                className="absolute inset-0 flex items-center justify-center"
                style={{ background: 'rgba(0,0,0,0.55)', color: 'white' }}
                data-testid="media-preview-overflow"
              >
                <span className="font-bold text-xl">+{overflow}</span>
              </div>
            )}

            {/* Remove button — 28×28 desktop, 32×32 mobile. */}
            <button
              type="button"
              aria-label="Retirer ce média"
              onPointerDown={(e) => e.preventDefault()}
              onClick={(e) => { e.stopPropagation(); onRemove?.(idx); }}
              data-testid={`media-preview-remove-${idx}`}
              className="absolute top-1.5 right-1.5 rounded-full flex items-center justify-center"
              style={{
                width: 28, height: 28,
                background: 'rgba(0,0,0,0.65)',
                color: 'white',
              }}
            >
              <X size={14} weight="bold" />
            </button>

            {/* Video badge */}
            {isVideo && (
              <div
                className="absolute bottom-1.5 left-1.5 px-2 py-0.5 rounded-full flex items-center gap-1 text-[10px]"
                style={{ background: 'rgba(0,0,0,0.65)', color: 'white' }}
              >
                <VideoCamera size={11} weight="fill" />
                Vidéo
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Thumbnail({ url, isVideo }) {
  if (isVideo) {
    // <video> with `muted preload="metadata"` shows the first frame on
    // most browsers without auto-playing — saves CPU on the feed.
    return (
      <video
        src={url}
        muted
        playsInline
        preload="metadata"
        className="w-full h-full object-cover"
      />
    );
  }
  return (
    <img
      src={url}
      alt=""
      className="w-full h-full object-cover"
      loading="lazy"
      decoding="async"
    />
  );
}
