/**
 * iter237q — EmojiPickerPopover
 *
 * Wrapper léger autour de `emoji-picker-react` (lazy-loaded) avec deux
 * particularités importantes :
 *
 *  1. **Mobile-first positioning** — sur écran < 640px, le popover est
 *     ancré en bas de viewport et POUSSE le composer vers le haut grâce
 *     à `useKeyboardOffset` (déjà en place). Sur desktop, il flotte au-
 *     dessus du bouton emoji.
 *
 *  2. **Bundle isolation** — `emoji-picker-react` (~80KB gzip + flairup)
 *     n'est chargé que lorsqu'on l'ouvre vraiment, via `lazy()`/`Suspense`.
 *     Empêche d'alourdir le bundle initial du feed.
 *
 * Props :
 *   open      — booléen
 *   onClose   — () => void
 *   onSelect  — (emoji: string) => void   (caractère brut, ex. "😀")
 *   anchorRef — Ref vers l'élément déclencheur (pour le positionnement
 *               desktop). Optionnel.
 */
import { useEffect, lazy, Suspense } from 'react';

const EmojiPickerLazy = lazy(() => import('emoji-picker-react'));

export default function EmojiPickerPopover({ open, onClose, onSelect }) {
  // ESC closes.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      {/* Backdrop — clic dehors ferme. Couvre toute la page sur mobile pour
          neutraliser le tap-through. Transparent. */}
      <div
        className="fixed inset-0 z-[90]"
        onClick={onClose}
        data-testid="emoji-picker-backdrop"
        style={{ background: 'rgba(0,0,0,0.20)' }}
      />

      {/* Popover. Sur mobile (<640px) collé en bas (au-dessus du clavier
          virtuel iOS/Android grâce à `100dvh` + `bottom: env(...)`). Sur
          desktop, simple bloc centré. */}
      <div
        className="fixed z-[100] left-1/2 -translate-x-1/2 sm:bottom-auto sm:top-auto"
        data-testid="emoji-picker-popover"
        style={{
          // Mobile: anchored to bottom safe-area, above virtual keyboard.
          bottom: 'calc(env(safe-area-inset-bottom, 0px) + 12px)',
          maxWidth: '95vw',
        }}
      >
        <Suspense fallback={
          <div className="rounded-xl bg-white p-4 shadow-xl text-xs"
               style={{ color: '#0F056B' }}>
            Chargement des emojis…
          </div>
        }>
          <EmojiPickerLazy
            onEmojiClick={(data) => {
              onSelect?.(data.emoji);
              onClose?.();
            }}
            autoFocusSearch={false}
            lazyLoadEmojis
            searchPlaceholder="Rechercher un emoji"
            previewConfig={{ showPreview: false }}
            // Compact UI — fit in mobile sheet.
            width={Math.min(340, window.innerWidth - 24)}
            height={380}
          />
        </Suspense>
      </div>
    </>
  );
}
