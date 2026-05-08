/**
 * JAPAP — Web Push helpers (iter71, OneSignal SDK v16).
 *
 * Replaces the iter70 VAPID-native helpers. Same exported surface so the
 * `PushNotificationsToggle` component and any other caller keep working.
 *
 * Key behavioural differences vs. iter70:
 *   • The OneSignal SDK loads asynchronously via `OneSignalDeferred`. We
 *     resolve it lazily on first use instead of racing init.
 *   • `subscribePush()` triggers OneSignal's permission flow, then calls
 *     `OneSignal.login(userId)` so the backend can target by `user_id`
 *     (External ID). The server does NOT need its own subscribe endpoint.
 *   • `unsubscribePush()` calls `optOut()` — the browser keeps the push
 *     subscription, OneSignal just stops sending to it.
 */
const APP_ID = process.env.REACT_APP_ONESIGNAL_APP_ID || "";

// Don't register the native SW twice: OneSignal's SDK manages its own.
// Our custom /sw.js is still installed (by src/index.js) for offline/cache.
let sdkReady = null;

/** Wait for the OneSignal v16 page SDK to be injected & init'd. */
function waitForSdk() {
  if (sdkReady) return sdkReady;
  sdkReady = new Promise((resolve) => {
    if (!APP_ID) return resolve(null);
    window.OneSignalDeferred = window.OneSignalDeferred || [];
    window.OneSignalDeferred.push(async (OneSignal) => {
      try {
        // Init is idempotent — calling twice throws, so guard it.
        if (!window.__japapOneSignalInit) {
          window.__japapOneSignalInit = true;
          await OneSignal.init({
            appId: APP_ID,
            allowLocalhostAsSecureOrigin: true,
            // Route the OneSignal SW through a side path so it doesn't
            // collide with our app-shell `/sw.js` at the root scope.
            serviceWorkerPath: "OneSignalSDKWorker.js",
            serviceWorkerParam: { scope: "/push/onesignal/" },
            notifyButton: { enable: false }, // we ship our own UI
            autoResubscribe: true,
          });
        }
        resolve(OneSignal);
      } catch (e) {
        // Init failed — resolve null so callers fall through to "unsupported"
        resolve(null);
      }
    });
    // Hard timeout — if the CDN script never loads (offline, adblock), give up.
    setTimeout(() => resolve(null), 8000);
  });
  return sdkReady;
}

export function isPushSupported() {
  try {
    return (
      typeof window !== "undefined" &&
      "serviceWorker" in navigator &&
      "PushManager" in window &&
      "Notification" in window &&
      !!APP_ID
    );
  } catch (_) {
    return false;
  }
}

export function currentPermission() {
  if (typeof Notification === "undefined") return "unsupported";
  return Notification.permission; // "default" | "granted" | "denied"
}

/** Truthy iff the user is subscribed AND opted-in on OneSignal's side. */
export async function getSubscription() {
  if (!isPushSupported()) return null;
  const OS = await waitForSdk();
  if (!OS) return null;
  try {
    const id = OS.User?.PushSubscription?.id;
    const optedIn = OS.User?.PushSubscription?.optedIn;
    if (id && optedIn) {
      return { id, optedIn: true, provider: "onesignal" };
    }
    return null;
  } catch (_) {
    return null;
  }
}

/** Tag the current OneSignal subscription with our internal user_id so
 * the backend can target pushes by External ID. */
export async function identifyUser(userId) {
  if (!userId) return { ok: false, reason: "no_user_id" };
  const OS = await waitForSdk();
  if (!OS) return { ok: false, reason: "sdk_unavailable" };
  try {
    await OS.login(String(userId));
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: "login_failed", detail: e?.message };
  }
}

/** Request permission + opt in. Returns {ok, reason?}. */
export async function subscribePush() {
  if (!isPushSupported()) return { ok: false, reason: "unsupported" };
  const OS = await waitForSdk();
  if (!OS) return { ok: false, reason: "sdk_unavailable" };

  try {
    // OneSignal's requestPermission resolves to "granted"|"denied"|"default"
    // (boolean on some older Safari). Normalize.
    const out = await OS.Notifications.requestPermission();
    const granted = out === true || out === "granted" || Notification.permission === "granted";
    if (!granted) {
      return { ok: false, reason: Notification.permission || "denied" };
    }
    // Make sure the user is opted in (in case they previously optedOut).
    if (OS.User?.PushSubscription?.optIn) {
      await OS.User.PushSubscription.optIn();
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: "subscribe_failed", detail: e?.message };
  }
}

/** Opt out — keeps the browser subscription but stops delivery. */
export async function unsubscribePush() {
  const OS = await waitForSdk();
  if (!OS) return { ok: true, reason: "sdk_unavailable" };
  try {
    if (OS.User?.PushSubscription?.optOut) {
      await OS.User.PushSubscription.optOut();
    }
    // Also log out the external ID so the user isn't targeted by user_id
    // even if they re-opt-in later without re-identifying.
    if (OS.logout) { try { await OS.logout(); } catch (_) {} }
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: "unsubscribe_failed", detail: e?.message };
  }
}
