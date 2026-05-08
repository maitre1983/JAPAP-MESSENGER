/**
 * JAPAP — Language switcher (iter206)
 * ====================================
 * Premium dropdown powered by shadcn `<DropdownMenu>`. Shows the current
 * language's flag inline and opens a panel with all 11 supported languages,
 * each with flag + native label, with the active one visually highlighted.
 *
 * Persistence :
 *   • localStorage via i18next LanguageDetector (`japap_ui_lang`)
 *   • Backend `users.preferred_lang` via PUT /api/auth/preferences
 *     (fire-and-forget; anonymous visitors ignore the 401)
 *
 * Variants via props :
 *   - compact=true         → icône-only button (header sombre /login)
 *   - variant="light"      → texte clair sur fond sombre
 *   - variant="dark"       → texte foncé sur fond clair (default)
 *
 * Works for both logged-in (Layout sidebar) and anonymous (LoginPage header)
 * contexts.
 */
import { useTranslation } from 'react-i18next';
import axios from 'axios';
import { Translate, Check, CaretDown } from '@phosphor-icons/react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { SUPPORTED_LANGUAGES } from '@/constants/languages';

const API = process.env.REACT_APP_BACKEND_URL;

export default function LanguageSwitcher({
  compact = false,
  variant = 'dark',
  className = '',
}) {
  const { i18n, t } = useTranslation();
  const current = i18n.language?.slice(0, 2) || 'fr';
  const currentLang = SUPPORTED_LANGUAGES.find((l) => l.code === current)
                    || SUPPORTED_LANGUAGES.find((l) => l.code === 'fr');

  const handleChange = async (code) => {
    if (code === current) return;
    try { localStorage.setItem('japap_ui_lang', code); } catch (_) {}
    await i18n.changeLanguage(code);
    // iter210 — Defence-in-depth: flip <html dir> + lang here directly. The
    // central listener in /src/i18n.js does the same, but this guarantees
    // RTL applies even if the listener was lost (e.g. HMR / lazy chunks).
    try {
      const short = String(code || 'en').toLowerCase().slice(0, 2);
      document.documentElement.setAttribute('lang', short);
      document.documentElement.setAttribute('dir', short === 'ar' ? 'rtl' : 'ltr');
    } catch (_) {}
    // Persist server-side so the preference follows the user across devices.
    try {
      await axios.put(
        `${API}/api/auth/preferences`,
        { preferred_lang: code },
        { withCredentials: true, timeout: 5000 }
      );
    } catch (_) { /* silent — local change still took effect */ }
  };

  const textColor = variant === 'light'
    ? 'rgba(255,255,255,0.95)'
    : 'var(--jp-text)';
  const borderColor = variant === 'light'
    ? 'rgba(255,255,255,0.35)'
    : 'var(--jp-border)';
  const bgHover = variant === 'light'
    ? 'rgba(255,255,255,0.12)'
    : 'var(--jp-surface)';

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          data-testid="language-switcher-trigger"
          className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full
            text-xs font-['Manrope'] font-semibold transition-colors outline-none
            focus-visible:ring-2 focus-visible:ring-offset-1
            ${className}`}
          style={{
            background: 'transparent',
            border: `1px solid ${borderColor}`,
            color: textColor,
          }}
          aria-label={t('common.change_language') || 'Change language'}
        >
          <span className="text-base leading-none" aria-hidden>
            {currentLang.flag}
          </span>
          {!compact && (
            <span className="whitespace-nowrap">{currentLang.native}</span>
          )}
          <CaretDown size={10} weight="bold" />
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="end"
        className="max-h-[70vh] overflow-y-auto"
        style={{ minWidth: 200 }}
        data-testid="language-switcher-menu"
      >
        <DropdownMenuLabel className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide">
          <Translate size={12} weight="bold" />
          {t('common.language') || 'Language'}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {SUPPORTED_LANGUAGES.map((l) => {
          const isActive = l.code === current;
          return (
            <DropdownMenuItem
              key={l.code}
              onSelect={() => handleChange(l.code)}
              data-testid={`lang-option-${l.code}`}
              aria-current={isActive ? 'true' : undefined}
              className="flex items-center gap-2 text-sm cursor-pointer"
              style={{
                background: isActive ? bgHover : undefined,
                fontWeight: isActive ? 600 : 400,
              }}
            >
              <span className="text-base leading-none" aria-hidden>
                {l.flag}
              </span>
              <span className="flex-1">{l.native}</span>
              {isActive && <Check size={14} weight="bold" />}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
