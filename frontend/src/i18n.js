/**
 * JAPAP — i18n configuration (iter72, 11-language coverage)
 * ==========================================================
 * All 11 supported languages ship a full UI bundle. The list is the
 * single source of truth exported from `constants/languages.js` and
 * mirrored on the backend in `constants.py::SUPPORTED_LANGS`.
 *
 * Priority order for picking the active language:
 *   1. `preferred_lang` on the authenticated user (synced in AuthContext)
 *   2. `localStorage['japap_ui_lang']`
 *   3. browser `navigator.language`
 *   4. fallback: English
 *
 * RTL languages (Arabic) automatically flip the `dir` attribute on
 * `<html>` via the `languageChanged` listener below.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import en from './locales/en.json';
import fr from './locales/fr.json';
import pt from './locales/pt.json';
import es from './locales/es.json';
import ar from './locales/ar.json';
import sw from './locales/sw.json';
import ln from './locales/ln.json';
import yo from './locales/yo.json';
import hi from './locales/hi.json';
import bn from './locales/bn.json';
import ta from './locales/ta.json';

import { SUPPORTED_CODES, isRTL } from './constants/languages';

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      fr: { translation: fr },
      pt: { translation: pt },
      es: { translation: es },
      ar: { translation: ar },
      sw: { translation: sw },
      ln: { translation: ln },
      yo: { translation: yo },
      hi: { translation: hi },
      bn: { translation: bn },
      ta: { translation: ta },
    },
    fallbackLng: 'fr',
    supportedLngs: SUPPORTED_CODES,
    // i18next is picky about regional codes (pt-PT, fr-CA…). Strip them.
    load: 'languageOnly',
    nonExplicitSupportedLngs: true,
    interpolation: { escapeValue: false },
    detection: {
      // iter203 — Detection priority for ANONYMOUS users (logged-in users
      // are still overridden by `preferred_lang` from AuthContext) :
      //   1. localStorage (explicit user choice — sticky across sessions)
      //   2. navigator (browser/system language → covers most native users)
      //   3. htmlTag (server-rendered <html lang> — useful for SEO)
      //   4. fallbackLng: 'fr' (historical default)
      // Geo-IP detection happens via /api/geo/detect on first load (see
      // useGeoLanguageBootstrap hook) and writes into localStorage, so it
      // wins over navigator on the second page.
      order: ['localStorage', 'navigator', 'htmlTag'],
      caches: ['localStorage'],
      lookupLocalStorage: 'japap_ui_lang',
    },
  });

// Flip <html lang/dir> on every language change. Done here so the effect
// is shared by every surface (app, marketing page, auth page) without
// each component re-implementing it.
function applyHtmlLang(lng) {
  try {
    const short = String(lng || 'en').toLowerCase().slice(0, 2);
    document.documentElement.setAttribute('lang', short);
    document.documentElement.setAttribute('dir', isRTL(short) ? 'rtl' : 'ltr');
  } catch (_) {}
}
applyHtmlLang(i18n.language);
i18n.on('languageChanged', applyHtmlLang);

export default i18n;
