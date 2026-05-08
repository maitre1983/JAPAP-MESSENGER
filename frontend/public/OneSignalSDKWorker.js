// JAPAP — OneSignal Service Worker (iter71)
// ────────────────────────────────────────────────────────────────────────
// This SW is registered by the OneSignal v16 page SDK at the scope
// `/push/onesignal/` — deliberately OUTSIDE the root scope `/` that our
// app-shell SW (`/sw.js`) already owns. This is the simplest way to let
// both workers live without fighting over the same scope.
//
// Handling order for a Web Push on the browser:
//   1. Push Service Worker at the subscription's scope is woken up.
//   2. Since our OneSignal subscription is registered at `/push/onesignal/`,
//      THIS file receives the push event, not `/sw.js`.
//   3. The OneSignal SDK inside renders the notification and clicks.
//
// We don't add any extra handlers here — OneSignal's bundled SW already
// does everything (push display, click-through, analytics pings). Keep
// this file minimal so future SDK upgrades stay drop-in.
importScripts("https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.sw.js");
