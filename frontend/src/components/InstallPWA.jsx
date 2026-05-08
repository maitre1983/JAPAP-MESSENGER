/**
 * JAPAP — Install PWA prompt (iter160 refresh)
 *
 * Strategy: **always nudge** until the app is installed.
 *
 *  • Android / Chromium:   Capture the native `beforeinstallprompt` event and
 *                          surface a branded CTA. One-tap install.
 *  • iOS Safari:           iOS doesn't fire `beforeinstallprompt`. Detect we're
 *                          on iOS + standalone-mode is off, and render a bottom
 *                          sheet with step-by-step "Partager → Sur l'écran
 *                          d'accueil" instructions + visual icons.
 *  • Desktop (Chrome/Edge): Same deferred-prompt path as Android.
 *  • In-app browsers:      Hidden (no install in Facebook/Instagram webview).
 *
 *  Dismissal re-opens in 24 h (not 7 days — we want to remind regularly).
 *  Once the app IS installed, the component vanishes forever.
 *  A persistent floating pill (bottom-right) remains visible if the user
 *  dismissed the big banner — a one-tap re-open of the install flow.
 *  Incentive copy: "Installe pour recevoir les notifications en temps réel"
 *  so the user has a concrete reason, not just a cosmetic nudge.
 */
import { useEffect, useState, useCallback } from "react";
import { X, DownloadSimple, ShareNetwork, Plus, Bell } from "@phosphor-icons/react";
import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router-dom';

const SNOOZE_KEY = "japap_pwa_install_snoozed_until";
// iter160 — shortened from 7 days to 1 day: CEO wants steady nudging.
const SNOOZE_HOURS = 24;

const isStandalone = () => {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    // iOS legacy flag
    window.navigator.standalone === true
  );
};

const isIOS = () => {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent || "";
  const iPadOS = navigator.platform === "MacIntel" && (navigator.maxTouchPoints || 0) > 1;
  return /iPad|iPhone|iPod/.test(ua) || iPadOS;
};

const isSafari = () => {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent || "";
  return /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
};

// iter160 — detect in-app webviews (FB/Instagram/TikTok/LinkedIn) so we don't
// show a broken install flow. We suggest "ouvrir dans Safari/Chrome" elsewhere.
const isInAppWebview = () => {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent || "";
  return /FBAN|FBAV|Instagram|Line|LinkedInApp|TwitterAndroid|TikTok/.test(ua);
};

const isSnoozed = () => {
  try {
    const until = Number(localStorage.getItem(SNOOZE_KEY) || 0);
    return until > Date.now();
  } catch (_) { return false; }
};

const snooze = () => {
  try {
    const until = Date.now() + SNOOZE_HOURS * 60 * 60 * 1000;
    localStorage.setItem(SNOOZE_KEY, String(until));
  } catch (_) {}
};

const clearSnooze = () => {
  try { localStorage.removeItem(SNOOZE_KEY); } catch (_) {}
};


export default function InstallPWA() {
  const { t } = useTranslation();
  // iter237s — Hide on full-screen chat / messaging routes where the
  // floating banner overlaps the message composer (reported by user).
  const { pathname } = useLocation();
  const HIDDEN_ROUTES = ['/chat', '/messages', '/messenger', '/call'];
  const isHiddenRoute = HIDDEN_ROUTES.some((r) => pathname.startsWith(r));
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [supportsNativePrompt, setSupportsNativePrompt] = useState(false);
  const [showBigBanner, setShowBigBanner] = useState(false);
  const [showIOSSheet, setShowIOSSheet] = useState(false);
  // iter160 — persistent "Installer" pill stays visible even after dismiss.
  const [installed, setInstalled] = useState(false);

  const resetAll = useCallback(() => {
    setShowBigBanner(false);
    setShowIOSSheet(false);
    setDeferredPrompt(null);
  }, []);

  useEffect(() => {
    if (isStandalone()) {
      setInstalled(true);
      clearSnooze();
      return;
    }
    if (isInAppWebview()) return;

    // Android / Chrome / Edge / Samsung — native deferred prompt
    const onBeforeInstall = (e) => {
      e.preventDefault();
      setDeferredPrompt(e);
      setSupportsNativePrompt(true);
      // Only auto-open the big banner if not snoozed. Even when snoozed,
      // the pill remains visible and lets the user trigger it manually.
      if (!isSnoozed()) setShowBigBanner(true);
    };
    window.addEventListener("beforeinstallprompt", onBeforeInstall);

    const onInstalled = () => {
      setInstalled(true);
      clearSnooze();
      resetAll();
    };
    window.addEventListener("appinstalled", onInstalled);

    // iOS: never fires beforeinstallprompt. Show sheet after grace period.
    if (isIOS() && isSafari() && !isSnoozed()) {
      const t = setTimeout(() => setShowIOSSheet(true), 4500);
      return () => {
        clearTimeout(t);
        window.removeEventListener("beforeinstallprompt", onBeforeInstall);
        window.removeEventListener("appinstalled", onInstalled);
      };
    }
    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstall);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, [resetAll]);

  const handleInstallNative = async () => {
    if (!deferredPrompt) return;
    try {
      deferredPrompt.prompt();
      const { outcome } = await deferredPrompt.userChoice;
      if (outcome === "accepted") {
        setShowBigBanner(false);
      } else {
        snooze();
        setShowBigBanner(false);
      }
    } catch (_) {
      setShowBigBanner(false);
    } finally {
      setDeferredPrompt(null);
    }
  };

  const dismiss = () => { snooze(); resetAll(); };

  // iter160 — pill reopens the appropriate flow (native prompt on Android,
  // sheet on iOS, fallback hint on desktop browsers without deferred prompt).
  const reopen = () => {
    clearSnooze();
    if (supportsNativePrompt && deferredPrompt) {
      handleInstallNative();
    } else if (isIOS()) {
      setShowIOSSheet(true);
    } else {
      // Last-resort: show the big banner in "instructions only" mode.
      setShowBigBanner(true);
    }
  };

  if (installed) return null;
  if (isInAppWebview()) return null;
  // iter237s — Hide on chat / call routes (composer overlap fix).
  if (isHiddenRoute) return null;

  const nothingOpen = !showBigBanner && !showIOSSheet;

  return (
    <>
      {/* ─────────── iter160 persistent floating pill ─────────── */}
      {nothingOpen && (
        <button
          data-testid="pwa-install-pill"
          onClick={reopen}
          aria-label={t('install_p_w_a.installer_japap_sur_mon_appareil')}
          className="fixed z-[55] flex items-center gap-2 pl-3 pr-4 py-2 rounded-full shadow-lg active:scale-95 transition-transform"
          style={{
            // sit above the bottom nav, right-aligned
            right: "12px",
            bottom: "calc(env(safe-area-inset-bottom, 0px) + 80px)",
            background: "linear-gradient(135deg,#E01C2E,#0F056B)",
            color: "white",
            fontSize: 12,
            fontWeight: 700,
            fontFamily: 'Manrope, sans-serif',
          }}
        >
          <DownloadSimple size={14} weight="bold" />
          Installer l'app
        </button>
      )}

      {/* ─────────── Big CTA banner (Android / Desktop / iOS fallback) ─────────── */}
      {showBigBanner && (
        <div
          data-testid="pwa-install-banner-android"
          className="fixed z-[60] left-3 right-3 rounded-2xl shadow-2xl"
          style={{
            bottom: "calc(env(safe-area-inset-bottom, 0px) + 72px)",
            background: "linear-gradient(135deg,#E01C2E,#0F056B)",
            color: "white",
            padding: "14px 16px",
          }}
        >
          <div className="flex items-center gap-3">
            <div className="flex-shrink-0 rounded-xl bg-white p-1.5" style={{ width: 44, height: 44 }}>
              <img src="/japap-logo.jpg" alt="JAPAP" className="w-full h-full object-contain rounded-lg" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold leading-tight">Installer JAPAP sur votre appareil</p>
              <p className="text-xs opacity-85 leading-tight mt-0.5 flex items-center gap-1">
                <Bell size={11} weight="fill" /> Notifications push · Accès hors-ligne · Icône app
              </p>
            </div>
            {supportsNativePrompt ? (
              <button
                data-testid="pwa-install-android-cta"
                onClick={handleInstallNative}
                className="flex items-center gap-1 px-3 py-2 rounded-full bg-white font-semibold text-xs"
                style={{ color: "#E01C2E" }}
              >
                <DownloadSimple size={14} weight="bold" />
                Installer
              </button>
            ) : (
              <button
                data-testid="pwa-install-android-cta"
                onClick={() => {
                  if (isIOS()) { setShowBigBanner(false); setShowIOSSheet(true); }
                  else { dismiss(); }
                }}
                className="flex items-center gap-1 px-3 py-2 rounded-full bg-white font-semibold text-xs"
                style={{ color: "#E01C2E" }}
              >
                <DownloadSimple size={14} weight="bold" />
                Comment ?
              </button>
            )}
            <button
              data-testid="pwa-install-dismiss"
              onClick={dismiss}
              aria-label={t('install_p_w_a.plus_tard')}
              className="p-1.5 rounded-full hover:bg-white/10"
            >
              <X size={16} weight="bold" className="text-white/80" />
            </button>
          </div>
        </div>
      )}

      {/* ─────────── iOS instructional sheet ─────────── */}
      {showIOSSheet && (
        <div
          data-testid="pwa-install-sheet-ios"
          className="fixed z-[60] left-0 right-0 px-3"
          style={{ bottom: "calc(env(safe-area-inset-bottom, 0px) + 12px)" }}
        >
          <div className="rounded-3xl shadow-2xl overflow-hidden" style={{ background: "#0F056B", color: "white" }}>
            <div className="flex items-center justify-between px-4 pt-4 pb-1" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
              <div className="flex items-center gap-2">
                <div className="rounded-lg bg-white p-1" style={{ width: 32, height: 32 }}>
                  <img src="/japap-logo.jpg" alt="JAPAP" className="w-full h-full object-contain rounded" />
                </div>
                <p className="text-sm font-semibold">Installer JAPAP sur iPhone</p>
              </div>
              <button data-testid="pwa-install-dismiss" onClick={dismiss} aria-label="Fermer" className="p-1 rounded-full hover:bg-white/10">
                <X size={18} weight="bold" className="text-white/70" />
              </button>
            </div>

            <div className="px-4 py-3 space-y-3">
              <p className="text-xs text-white/75 leading-relaxed">
                Installe JAPAP pour recevoir les <strong>notifications push</strong>, ouvrir l'app depuis ton écran d'accueil en un clic et continuer à l'utiliser <strong>hors-ligne</strong>.
              </p>

              <InstallStep num={1} icon={<ShareNetwork size={16} weight="bold" className="inline" />}
                title={t('install_p_w_a.touchez_partager')}
                subtitle="Dans la barre Safari en bas de l'écran." />
              <InstallStep num={2} icon={<Plus size={16} weight="bold" className="inline" />}
                title={t('install_p_w_a.selectionnez_sur_l_ecran_d_accueil')}
                subtitle="Puis « Ajouter » en haut à droite." />
              <InstallStep num={3}
                title={t('install_p_w_a.japap_est_pret_sur_votre_ecran_d_ac')}
                subtitle="Lancez-le comme n'importe quelle app native." />
            </div>

            <button
              data-testid="pwa-install-ios-dismiss"
              onClick={dismiss}
              className="w-full py-3 text-sm font-semibold"
              style={{ background: "rgba(255,255,255,0.06)", borderTop: "1px solid rgba(255,255,255,0.08)" }}
            >
              J'ai compris
            </button>
          </div>
        </div>
      )}
    </>
  );
}


function InstallStep({ num, icon, title, subtitle }) {
  return (
    <div className="flex items-start gap-3">
      <div
        className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center"
        style={{ background: "rgba(255,255,255,0.12)" }}>
        <span className="text-sm font-bold">{num}</span>
      </div>
      <div className="flex-1">
        <p className="text-sm font-medium flex items-center gap-1">
          {icon ? <>{title.split("« ")[0]}{icon}</> : title}
          {icon && title.includes("« ") && " « " + title.split("« ")[1]}
        </p>
        <p className="text-xs text-white/65">{subtitle}</p>
      </div>
    </div>
  );
}
