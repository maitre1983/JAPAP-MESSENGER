/**
 * JAPAP Messenger — TranslateButton
 * =================================
 * Small inline button that translates a message (text or voice transcription)
 * via Claude Sonnet 4.5 / Emergent LLM key. Cached server-side per (msg_id, lang).
 *
 * Two modes:
 *  - Manual (default): user clicks "Traduire" → fetches on demand
 *  - Auto: when `autoMode` is true, fetches silently on mount and displays the
 *    translation with a discreet "traduit automatiquement de XX" badge. Only
 *    appears when the detected source language differs from the target.
 */
import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { Translate } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
const API = process.env.REACT_APP_BACKEND_URL;

const getLang_names = (t) => ({
  fr: t('translate_button.francais'), en: 'English', pt: t('translate_button.portugues'), es: t('translate_button.espanol'),
  ar: 'العربية', sw: 'Kiswahili', ln: 'Lingala', yo: 'Yoruba',
});

/**
 * @param {string} msgId
 * @param {'light'|'dark'} tone
 * @param {string} targetLang  — ISO code (default 'fr')
 * @param {boolean} autoMode   — auto-fetch on mount, hide button, show inline translation
 */
export default function TranslateButton({ msgId, tone = 'light', targetLang = 'fr', autoMode = false }) {
  const { t } = useTranslation();
  const LANG_NAMES = getLang_names(t);
  const [state, setState] = useState('idle');
  const [translation, setTranslation] = useState(null);
  const [error, setError] = useState('');
  const [visible, setVisible] = useState(autoMode);
  const triggeredRef = useRef(false);

  const doTranslate = async () => {
    if (state === 'loading') return;
    if (translation) { setVisible(v => !v); return; }
    setState('loading'); setError('');
    try {
      const { data } = await axios.post(
        `${API}/api/messages/${msgId}/translate`,
        { target_lang: targetLang },
        { withCredentials: true }
      );
      setTranslation(data);
      setState('done');
      setVisible(true);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Traduction impossible');
      setState('error');
    }
  };

  // Auto-fetch once on mount when autoMode is enabled
  useEffect(() => {
    if (!autoMode || triggeredRef.current) return;
    triggeredRef.current = true;
    doTranslate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoMode, msgId]);

  const darkTheme = tone === 'dark';
  const chipBg = darkTheme ? 'rgba(255,255,255,0.18)' : 'rgba(15,5,107,0.08)';
  const chipColor = darkTheme ? 'rgba(255,255,255,0.95)' : 'var(--jp-primary)';
  const textColor = darkTheme ? 'rgba(255,255,255,0.95)' : 'var(--jp-text)';
  const borderColor = darkTheme ? 'rgba(255,255,255,0.2)' : 'rgba(15,5,107,0.15)';
  const mutedColor = darkTheme ? 'rgba(255,255,255,0.6)' : 'var(--jp-text-muted)';

  // Auto mode: silent, only show translation when source != target
  if (autoMode) {
    // Suppress if source language already matches target (no translation needed)
    const sameLang = translation?.detected_lang && translation.detected_lang.toLowerCase() === targetLang.toLowerCase();
    if (!translation || sameLang || !translation.translated_text) return null;
    return (
      <div className="mt-1.5 pt-1.5 border-t" data-testid={`auto-translation-${msgId}`}
        style={{ borderColor }}>
        <p className="text-[9px] font-['Manrope'] font-semibold uppercase tracking-wide mb-0.5 flex items-center gap-1"
          style={{ color: mutedColor }}>
          <Translate size={10} weight="bold" />
          Traduit automatiquement de {translation.detected_lang?.toUpperCase() || '??'}
        </p>
        <p className="text-xs font-['Manrope'] leading-relaxed break-words italic" style={{ color: textColor }}>
          "{translation.translated_text}"
        </p>
      </div>
    );
  }

  // Manual mode (classic button)
  return (
    <div className="mt-1.5" data-testid={`translate-wrap-${msgId}`}>
      <button type="button" onClick={(e) => { e.stopPropagation(); doTranslate(); }}
        disabled={state === 'loading'}
        data-testid={`translate-button-${msgId}`}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-['Manrope'] font-semibold transition-all hover:scale-105 disabled:opacity-60"
        style={{ background: chipBg, color: chipColor }}>
        <Translate size={11} weight="bold" />
        {state === 'loading' ? 'Traduction…' : translation ? (visible ? 'Masquer' : 'Afficher traduction') : 'Traduire'}
      </button>

      {error && state === 'error' && (
        <p className="text-[10px] font-['Manrope'] mt-1 italic" data-testid={`translate-error-${msgId}`}
          style={{ color: darkTheme ? 'rgba(255,200,200,0.95)' : '#E01C2E' }}>{error}</p>
      )}

      {translation && visible && !(translation.detected_lang && translation.detected_lang.toLowerCase() === targetLang.toLowerCase()) && (
        <div className="mt-1.5 pt-1.5 border-t" data-testid={`translation-${msgId}`} style={{ borderColor }}>
          <div className="flex items-center gap-1 mb-0.5">
            <span className="text-[9px] font-['Manrope'] font-semibold uppercase tracking-wide" style={{ color: mutedColor }}>
              {translation.detected_lang ? `${translation.detected_lang.toUpperCase()} → ${targetLang.toUpperCase()}` : `→ ${LANG_NAMES[targetLang] || targetLang}`}
              {translation.cached && ' · cache'}
            </span>
          </div>
          <p className="text-xs font-['Manrope'] leading-relaxed break-words italic" style={{ color: textColor }}>
            "{translation.translated_text}"
          </p>
        </div>
      )}
    </div>
  );
}
