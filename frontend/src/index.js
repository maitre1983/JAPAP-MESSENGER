import React from "react";
import ReactDOM from "react-dom/client";
import "@/i18n";
import "@/security/axiosSecurity";   // iter82 — CSRF + auto-refresh interceptor
import { installGlobalErrorReporter } from "@/lib/errorReporter";  // iter108 — AI Error Monitor
import "@/index.css";
import App from "@/App";

installGlobalErrorReporter();

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// ─── PWA service worker registration ───────────────────────────────────
// Register in all environments so Web Push works in the preview URL too.
// The SW itself is idempotent and network-first for navigations, so it
// never serves a stale dev bundle.
//
// iter146 — emit a `japap:sw-update-available` window event whenever a new
// service worker is detected and waiting. The PwaUpdateBanner subscribes to
// it to surface a "Nouvelle version disponible" prompt to the user.
function emitSwUpdate(reg) {
  try {
    window.dispatchEvent(new CustomEvent("japap:sw-update-available", {
      detail: { registration: reg },
    }));
  } catch (_) { /* ignore — old browsers without CustomEvent */ }
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then((reg) => {
        if (!reg) return;

        // Already waiting on first registration (returning visitor with a
        // new SW already cached) → notify immediately.
        if (reg.waiting && navigator.serviceWorker.controller) {
          emitSwUpdate(reg);
        }

        // New SW found mid-session → wait for it to install, then notify.
        reg.addEventListener("updatefound", () => {
          const installing = reg.installing;
          if (!installing) return;
          installing.addEventListener("statechange", () => {
            if (installing.state === "installed" && navigator.serviceWorker.controller) {
              emitSwUpdate(reg);
            }
          });
        });

        if (reg.active) {
          setInterval(() => reg.update().catch(() => {}), 60 * 1000);
        }
      })
      .catch(() => {
        // Silent: offline mode is a nice-to-have, not a crash cause.
      });

    // Reload once when the SW takes over (skipWaiting → controllerchange).
    let _hasReloaded = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (_hasReloaded) return;
      _hasReloaded = true;
      window.location.reload();
    });
  });
}
