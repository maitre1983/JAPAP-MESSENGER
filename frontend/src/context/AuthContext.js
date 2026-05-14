import { createContext, useContext, useState, useCallback, useEffect } from 'react';
import axios from 'axios';
import i18n from '@/i18n';
import { SUPPORTED_CODES } from '@/constants/languages';

const AuthContext = createContext(null);
const API = process.env.REACT_APP_BACKEND_URL;

/** Apply the user's preferred_lang to i18next so the whole UI switches.
 *  iter72: supports all 11 locales defined in constants/languages.js.
 *  iter234: gated on i18n.isInitialized to prevent a post-login race where
 *  React was mid-rendering with the old language while i18next was still
 *  loading the new bundle synchronously. The deferred listener still
 *  applies the change once i18next signals 'initialized'. */
function syncUiLang(preferred, fallbackLang) {
  // iter240f — Order of preference: preferred_lang (explicit user choice) →
  // language (legacy field still used by some flows). If both are empty,
  // i18next keeps its detector result (localStorage/browser/htmlTag).
  const candidate = preferred || fallbackLang || '';
  const lang = String(candidate).toLowerCase().slice(0, 2);
  if (!SUPPORTED_CODES.includes(lang)) return;
  if (i18n.language === lang) return;
  if (i18n.isInitialized) {
    i18n.changeLanguage(lang).catch((e) => {
      // eslint-disable-next-line no-console
      console.error('[auth] changeLanguage failed:', e?.message || e);
    });
    return;
  }
  // Defer until i18next finishes its synchronous-then-async init.
  const apply = () => {
    i18n.changeLanguage(lang).catch(() => { /* logged above */ });
    i18n.off('initialized', apply);
  };
  i18n.on('initialized', apply);
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const checkAuth = useCallback(async () => {
    if (window.location.hash?.includes('session_id=')) {
      setLoading(false);
      return;
    }
    // iter171 — Resilient session restore.
    // We MUST distinguish 3 outcomes:
    //   (a) 200 → user is authenticated, set state.
    //   (b) 401/403 → no session, set null (anon path).
    //   (c) 5xx / network blip → DO NOT log the user out. Retry with
    //       short backoff, then keep last known state silently. This
    //       prevents a momentary backend hiccup from kicking a logged-
    //       in user back to /login with a "Service indisponible" toast.
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const tryOnce = async () => {
      const { data } = await axios.get(`${API}/api/auth/me`, {
        withCredentials: true,
        // Soft-fail validation so 5xx doesn't throw inside React tree
        // unless we actively choose to. We still throw on 4xx so the
        // catch branch can decide based on status.
        validateStatus: (s) => s < 500,
      });
      return data;
    };
    let attempts = 0;
    while (attempts < 3) {
      try {
        const data = await tryOnce();
        if (data && data.user_id) {
          setUser(data);
          syncUiLang(data?.preferred_lang, data?.language);
          break;
        }
        // 401/403 path → not authenticated. Stop retrying.
        setUser(null);
        break;
      } catch (err) {
        const status = err?.response?.status;
        // Definite "no session" → stop.
        if (status === 401 || status === 403) {
          setUser(null);
          break;
        }
        // 5xx / network → silent retry, no toast, no logout.
        attempts += 1;
        if (attempts >= 3) {
          // Give up gracefully — keep user as null but surface NO error
          // toast. The next component fetch will retry naturally.
          break;
        }
        await sleep(400 * attempts);
      }
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // iter71: auto-tag OneSignal with our internal user_id whenever a user
  // is resolved. This is idempotent — OneSignal.login() is a no-op when
  // called with the same external id twice. Runs even if the user hasn't
  // opted into push yet, so the moment they do, targeting works.
  useEffect(() => {
    if (!user?.user_id) return;
    (async () => {
      try {
        const { identifyUser } = await import('@/services/webpush');
        await identifyUser(user.user_id);
      } catch (_) {
        // Silent: push tagging failure shouldn't break auth flow.
      }
    })();
  }, [user?.user_id]);

  const login = async (email, password, captchaOrTurnstile, rememberMe = false) => {
    // iter141ter — `captchaOrTurnstile` can be either:
    //   • { captcha_id, captcha_answer } (new math captcha)
    //   • a string (legacy Turnstile token)
    // The backend accepts both shapes (via Optional fields).
    // iter237 — `rememberMe` (default false) controls cookie persistence.
    //   false → session cookies (browser drops on close, no auto-relogin).
    //   true  → 8h access + 7-90d refresh (current behaviour).
    const body = { email, password, remember_me: !!rememberMe };
    if (captchaOrTurnstile && typeof captchaOrTurnstile === 'object') {
      body.captcha_id = captchaOrTurnstile.captcha_id;
      body.captcha_answer = captchaOrTurnstile.captcha_answer;
    } else if (typeof captchaOrTurnstile === 'string') {
      body.turnstile_token = captchaOrTurnstile;
    }
    const { data } = await axios.post(`${API}/api/auth/login`, body, { withCredentials: true });
    // Iter83 — superadmin flow returns {status:'otp_required'} instead of
    // {user,...}. Only set the user context when a real user payload is
    // present; otherwise hand the status back to the caller so the UI can
    // switch to the 2FA step.
    if (data?.user) {
      setUser(data.user);
      syncUiLang(data.user?.preferred_lang, data.user?.language);
    }
    return data;
  };

  const verifySuperadmin2fa = async (email, code) => {
    const { data } = await axios.post(
      `${API}/api/auth/verify-2fa`,
      { email, code },
      { withCredentials: true },
    );
    if (data?.user) {
      setUser(data.user);
      syncUiLang(data.user?.preferred_lang, data.user?.language);
    }
    return data;
  };

  const register = async (payload) => {
    // iter73 + iter74: auto-detect UI language + wallet currency from the
    // browser and pass them along. `navigator.language` → "fr-CA" → "fr".
    // `Intl.NumberFormat().resolvedOptions().locale` + `Intl.Locale` give
    // us a best-effort currency guess when the browser exposes it.
    // `SUPPORTED_CODES` filters out anything outside our 11-language
    // whitelist so the backend never sees garbage.
    let detected_lang, detected_currency;
    try {
      const nav = (typeof navigator !== 'undefined' && navigator.language) || '';
      const short = nav.toLowerCase().slice(0, 2);
      if (short && SUPPORTED_CODES.includes(short)) {
        detected_lang = short;
      }
    } catch (_) { /* ignore — backend will fall back */ }
    try {
      // Modern browsers expose the user's region via Intl.Locale.
      // Extract region (e.g. "fr-CM" → "CM") and let the backend map it.
      const loc = typeof Intl !== 'undefined' && Intl.DateTimeFormat
        ? Intl.DateTimeFormat().resolvedOptions()
        : null;
      const region = (loc?.locale?.split('-')?.[1] || '').toUpperCase();
      if (region && region.length === 2) {
        // We don't know the currency directly, but payload.country_code
        // will carry the hint. If the user left country blank, we send
        // the browser region as a secondary hint.
        if (!payload.country_code) {
          payload = { ...payload, country_code: region };
        }
      }
    } catch (_) { /* ignore */ }
    const { data } = await axios.post(
      `${API}/api/auth/register`,
      { ...payload, detected_lang, detected_currency },
      { withCredentials: true }
    );
    return data;
  };

  const verifyOtp = async (email, code) => {
    const { data } = await axios.post(`${API}/api/auth/verify-otp`, { email, code }, { withCredentials: true });
    setUser(data.user);
    syncUiLang(data.user?.preferred_lang, data.user?.language);
    return data;
  };

  const resendOtp = async (email) => {
    const { data } = await axios.post(`${API}/api/auth/resend-otp`, { email }, { withCredentials: true });
    return data;
  };

  const googleLogin = async (sessionId) => {
    const { data } = await axios.post(`${API}/api/auth/google/session`, { session_id: sessionId }, { withCredentials: true });
    setUser(data.user);
    syncUiLang(data.user?.preferred_lang, data.user?.language);
    return data;
  };

  const logout = async () => {
    try {
      await axios.post(`${API}/api/auth/logout`, {}, { withCredentials: true });
    } catch {}
    setUser(null);
  };

  const refreshUser = async () => {
    try {
      const { data } = await axios.get(`${API}/api/auth/me`, { withCredentials: true });
      setUser(data);
    } catch {}
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, verifySuperadmin2fa, register, verifyOtp, resendOtp, googleLogin, logout, refreshUser, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
