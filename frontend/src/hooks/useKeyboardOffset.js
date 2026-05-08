/**
 * iter211 — useKeyboardOffset
 * ===========================
 * Tracks how many pixels the on-screen keyboard (or any OS overlay)
 * currently hides from the bottom of the visual viewport, and returns
 * a numeric offset that the caller can plug into `paddingBottom` or
 * `bottom` to keep a sticky composer/input above the keyboard.
 *
 * iOS Safari is the main target — it does NOT shrink `window.innerHeight`
 * when the keyboard appears, it just shifts the viewport up. Android
 * Chrome already resizes the layout viewport so this hook returns 0 and
 * becomes a no-op there.
 *
 * Usage:
 *   const offset = useKeyboardOffset();
 *   <div style={{ paddingBottom: offset }}>...</div>
 *
 * Falls back to 0 on browsers without `visualViewport` API.
 */
import { useEffect, useState } from 'react';

export default function useKeyboardOffset() {
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const vv = window.visualViewport;
    if (!vv) return;
    let rafId = null;
    const update = () => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        const diff = window.innerHeight - vv.height - vv.offsetTop;
        // Clamp negative values (some browsers briefly overshoot on transitions)
        setOffset(Math.max(0, Math.round(diff)));
      });
    };
    vv.addEventListener('resize', update);
    vv.addEventListener('scroll', update);
    update();
    return () => {
      vv.removeEventListener('resize', update);
      vv.removeEventListener('scroll', update);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, []);

  return offset;
}
