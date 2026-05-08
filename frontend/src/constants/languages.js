/**
 * JAPAP — Single source of truth for the supported languages.
 *
 * This list MUST match the backend set in `backend/constants.py::SUPPORTED_LANGS`.
 * Both the **Interface Language** selector (LanguageSwitcher.jsx) and the
 * **Auto-translate Messages** dropdown (ProfilePage.js) read from here so
 * the two lists can never drift.
 *
 * IMPORTANT — native labels are NEVER translated. A French speaker wants to
 * see "Français", a Portuguese speaker wants "Português", etc. These strings
 * are data, not UI copy.
 *
 * When adding a new language:
 *   1. Add its entry below (code + flag + native label).
 *   2. Add the matching set member in `backend/constants.py`.
 *   3. Drop a translation bundle at `src/locales/{code}.json`.
 *   4. Register it in `src/i18n.js`.
 */

export const SUPPORTED_LANGUAGES = [
  { code: "en", flag: "🇬🇧", label: "English",    native: "English"            },
  { code: "fr", flag: "🇫🇷", label: "Français",   native: "Français"           },
  { code: "pt", flag: "🇵🇹", label: "Português",  native: "Português"          },
  { code: "es", flag: "🇪🇸", label: "Español",    native: "Español"            },
  { code: "ar", flag: "🇸🇦", label: "العربية",    native: "العربية"            },
  { code: "sw", flag: "🇹🇿", label: "Kiswahili",  native: "Kiswahili"          },
  { code: "ln", flag: "🇨🇩", label: "Lingala",    native: "Lingála"            },
  { code: "yo", flag: "🇳🇬", label: "Yoruba",     native: "Yorùbá"             },
  { code: "hi", flag: "🇮🇳", label: "हिन्दी",      native: "हिन्दी"              },
  { code: "bn", flag: "🇧🇩", label: "বাংলা",      native: "বাংলা"              },
  { code: "ta", flag: "🇮🇳", label: "தமிழ்",      native: "தமிழ்"              },
];

/** RTL languages — used to flip layout direction when active. */
export const RTL_CODES = new Set(["ar"]);

export const SUPPORTED_CODES = SUPPORTED_LANGUAGES.map((l) => l.code);

export function findLanguage(code) {
  if (!code) return null;
  const c = String(code).toLowerCase().slice(0, 2);
  return SUPPORTED_LANGUAGES.find((l) => l.code === c) || null;
}

export function isRTL(code) {
  return RTL_CODES.has(String(code || "").toLowerCase().slice(0, 2));
}
