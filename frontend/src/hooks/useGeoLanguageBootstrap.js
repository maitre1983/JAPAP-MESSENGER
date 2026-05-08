/**
 * iter203 — useGeoLanguageBootstrap
 * ==================================
 * Pour les visiteurs ANONYMES (avant le 1er login). Appelé une seule fois au
 * boot de l'app :
 *   1. Si `localStorage['japap_ui_lang']` existe → ne fait rien (l'utilisateur
 *      a fait un choix explicite, on respecte).
 *   2. Sinon GET /api/geo/detect → reçoit {country_code, suggested_lang, ip}.
 *   3. Si `suggested_lang` est dans la whitelist 11-langues, on l'écrit dans
 *      localStorage + on bascule i18next dessus.
 *
 * Pour les utilisateurs AUTHENTIFIÉS, c'est `AuthContext.syncUiLang(user.preferred_lang)`
 * qui prend la main (priorité plus haute que le hook). Le backend signup pose
 * déjà `preferred_lang` via `language_detector.detect_user_language(...)`.
 *
 * NB : ce hook ne doit JAMAIS écraser un choix explicite. Il s'arrête dès
 * qu'il voit `japap_ui_lang` dans localStorage.
 */
import { useEffect } from 'react';
import axios from 'axios';
import i18n from '@/i18n';
import { SUPPORTED_CODES } from '@/constants/languages';

const API = process.env.REACT_APP_BACKEND_URL;
const STORAGE_KEY = 'japap_ui_lang';
const ATTEMPTED_KEY = 'japap_geo_lang_attempted';   // empêche le ré-essai

export default function useGeoLanguageBootstrap() {
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Choix explicite déjà fait → ne rien faire
      try {
        if (localStorage.getItem(STORAGE_KEY)) return;
        // Évite de spammer /api/geo/detect en cas d'échec network
        if (localStorage.getItem(ATTEMPTED_KEY)) return;
      } catch (_) { return; }

      try {
        const { data } = await axios.get(`${API}/api/geo/detect`, {
          withCredentials: false,
          timeout: 4000,
        });
        if (cancelled) return;
        const suggested = String(data?.suggested_lang || '').toLowerCase().slice(0, 2);
        if (suggested && SUPPORTED_CODES.includes(suggested)) {
          localStorage.setItem(STORAGE_KEY, suggested);
          if (i18n.language !== suggested) i18n.changeLanguage(suggested);
        }
      } catch (_) {
        // Échec silencieux — on garde le fallback français.
      } finally {
        try { localStorage.setItem(ATTEMPTED_KEY, '1'); } catch (_) {}
      }
    })();
    return () => { cancelled = true; };
  }, []);
}
