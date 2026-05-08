/**
 * iter163 — Silent PWA update flow.
 *
 * Previous versions (iter146) rendered a full-width top banner with
 * "Mise à jour en cours…" which looked like the app was frozen on
 * slow networks. CEO UX note (28/04/2026): the update must NEVER block
 * the user.
 *
 * New flow:
 *   1. Service worker announces a waiting version via the
 *      `japap:sw-update-available` window event (fired by `src/index.js`).
 *   2. We immediately `postMessage({type:'SKIP_WAITING'})` so the new SW
 *      activates in the background. The `controllerchange` listener in
 *      `src/index.js` then triggers a single reload at the next natural
 *      navigation (or after a short delay).
 *   3. A DISCRETE `sonner` toast informs the user — at most once every
 *      24 h thanks to a localStorage timestamp. Message:
 *      "Une nouvelle version de JAPAP est disponible. Elle sera appliquée
 *       automatiquement."
 *   4. Nothing else is rendered. No blocking bar. No spinner. No scroll
 *      offset. The user keeps browsing normally.
 */
import { useEffect, useRef } from "react";
import { toast } from "sonner";

const SNOOZE_KEY = "japap_pwa_update_toasted_at";
const SNOOZE_MS = 24 * 60 * 60 * 1000;

function canShowToast() {
  try {
    const last = Number(localStorage.getItem(SNOOZE_KEY) || 0);
    return Date.now() - last > SNOOZE_MS;
  } catch (_) {
    return true;
  }
}

function markToastShown() {
  try {
    localStorage.setItem(SNOOZE_KEY, String(Date.now()));
  } catch (_) {
    /* localStorage unavailable — no-op */
  }
}

export default function PwaUpdateBanner() {
  const appliedRef = useRef(false);

  useEffect(() => {
    function onUpdate(e) {
      const reg = e?.detail?.registration;
      if (!reg || appliedRef.current) return;
      appliedRef.current = true;

      // Silent auto-apply: kick the waiting worker immediately so the
      // new SW activates in the background. The reload is handled by
      // the `controllerchange` listener in src/index.js.
      try {
        const target = reg.waiting || reg.installing;
        if (target) target.postMessage({ type: "SKIP_WAITING" });
      } catch (_) {
        /* ignored — reload will still occur when a new SW takes over */
      }

      // Discrete toast, max once every 24h. We intentionally don't add
      // any CTA: the user doesn't need to do anything, and offering
      // "Apply now" would invite them to think something is blocking.
      if (canShowToast()) {
        toast.info(
          "Une nouvelle version de JAPAP est disponible. Elle sera appliquée automatiquement.",
          {
            id: "pwa-update-notice",
            duration: 5000,
          },
        );
        markToastShown();
      }
    }

    window.addEventListener("japap:sw-update-available", onUpdate);
    return () =>
      window.removeEventListener("japap:sw-update-available", onUpdate);
  }, []);

  // Render nothing — all feedback flows through the sonner toaster.
  return null;
}
