import { useState, useRef, useEffect } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/context/AuthContext';
import { Eye, EyeSlash, User, Lock } from '@phosphor-icons/react';
import { toast } from 'sonner';
import MathCaptcha from '@/components/MathCaptcha';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import { extractErrorMessage } from '@/utils/errorMessage';

export default function LoginPage() {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [migrationReset, setMigrationReset] = useState(false);
  const [migrationLoading, setMigrationLoading] = useState(false);
  const [migrationSent, setMigrationSent] = useState(false);
  // iter152 — track real delivery status (sent | failed | cooldown) so the
  // UI never lies to the user. `migrationCooldown` counts down to enable
  // the "Renvoyer le lien" button after the backend cooldown elapses.
  const [migrationStatus, setMigrationStatus] = useState({ ok: null, message: '', messageId: '' });
  const [migrationCooldown, setMigrationCooldown] = useState(0);
  const [loading, setLoading] = useState(false);

  // iter152 — countdown the cooldown so the "Renvoyer" button re-enables.
  useEffect(() => {
    if (migrationCooldown <= 0) return;
    const t = setTimeout(() => setMigrationCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [migrationCooldown]);
  const [captcha, setCaptcha] = useState({ captcha_id: '', captcha_answer: '' });
  // iter237 — "Se souvenir de moi" : default OFF, persisted in localStorage
  // so the user's last preference is restored on next visit. Controls the
  // remember_me flag sent to /api/auth/login (cookie persistence on the server).
  const [rememberMe, setRememberMe] = useState(() => {
    try { return localStorage.getItem('jp_remember_me') === '1'; }
    catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem('jp_remember_me', rememberMe ? '1' : '0'); }
    catch { /* ignore */ }
  }, [rememberMe]);
  const captchaRef = useRef(null);
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // If ProtectedRoute redirected us here, `location.state.from` holds the
  // original URL (e.g. /post/abc123). Honour it so external-share clicks
  // land on the intended page after auth.
  // iter141nine — also honour `?redirect=` query param so shared payment
  // request links (PayPage) survive a login round-trip.
  const queryRedirect = new URLSearchParams(location.search).get('redirect');
  const redirectTo = location.state?.from || queryRedirect || '/feed';

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setMigrationReset(false);
    setMigrationSent(false);
    // iter237b — Allow submission when captcha returns the "unreachable" or
    // "silent" sentinel (silent = server told us captcha is not required, or
    // CAPTCHA_ENABLED=false on the backend). The backend is the source of
    // truth; if it really needs a fresh answer it will reject with 400 and
    // we'll show the message AND refresh the captcha automatically.
    const ans = (captcha.captcha_answer || '').trim();
    if (!ans) {
      setError('Réponds au calcul pour continuer.');
      return;
    }
    setLoading(true);
    try {
      const data = await login(email, password, captcha, rememberMe);
      // iter146 — celebrate the trusted-device transition (≥2 successful
      // logins from the same device). Surfaces *only* on the transition
      // login so the toast doesn't spam the user every session afterwards.
      if (data?.device?.newly_trusted) {
        toast.success("Cet appareil est désormais reconnu — tu resteras connecté(e).", {
          duration: 5000,
        });
      }
      navigate(redirectTo, { replace: true });
    } catch (err) {
      const raw = extractErrorMessage(err, t('auth.generic_error') || 'Une erreur est survenue.');
      // iter146 — Detect MIGRATION_RESET_REQUIRED from the backend and switch
      // the error UI to a CTA "Définir un nouveau mot de passe" flow instead
      // of leaking the technical prefix to the user.
      if (typeof raw === 'string' && raw.startsWith('MIGRATION_RESET_REQUIRED:')) {
        const friendly = raw.slice('MIGRATION_RESET_REQUIRED:'.length).trim();
        setMigrationReset(true);
        setError(friendly || 'Votre compte a été migré vers JAPAP 4.0. Veuillez définir un nouveau mot de passe.');
      } else {
        setError(raw);
      }
      // Refresh captcha after any failure so user gets a fresh problem.
      // (also resets the silent-bypass state if cookie was rejected).
      captchaRef.current?.refresh();
      setCaptcha({ captcha_id: '', captcha_answer: '' });
    }
    finally { setLoading(false); }
  };

  const handleMigrationReset = async () => {
    if (!email || !email.trim()) {
      setError("Saisis ton email avant de continuer.");
      return;
    }
    setMigrationLoading(true);
    setMigrationStatus({ ok: null, message: '', messageId: '' });
    try {
      const API = process.env.REACT_APP_BACKEND_URL;
      // iter152 — the previous login attempt that triggered
      // MIGRATION_RESET_REQUIRED already passed the math captcha and the
      // backend issued the silent-bypass humanity cookie. We can call
      // /forgot-password with empty captcha fields and the backend will
      // accept it via `has_valid_human_cookie`.
      const r = await fetch(`${API}/api/auth/forgot-password`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          captcha_id: '',
          captcha_answer: '',
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const msg = data?.detail || "Impossible d'envoyer le lien — réessaie ou contacte le support.";
        setMigrationStatus({ ok: false, message: typeof msg === 'string' ? msg : "Une erreur est survenue.", messageId: '' });
        setMigrationSent(false);
        return;
      }
      const delivery = data?.delivery || {};
      const cooldown = Number(data?.cooldown_seconds || 30);
      setMigrationCooldown(cooldown);
      if (delivery.status === 'sent' && delivery.ok) {
        setMigrationStatus({
          ok: true,
          message: `✓ Email envoyé à ${email}. Vérifie ta boîte (et tes spams).`,
          messageId: delivery.message_id || '',
        });
        setMigrationSent(true);
      } else if (delivery.status === 'cooldown') {
        setMigrationStatus({
          ok: true,
          message: data.message || "Lien déjà envoyé récemment — vérifie tes spams.",
          messageId: '',
        });
        setMigrationSent(true);
      } else if (delivery.status === 'queued') {
        // Anti-enumeration response — we keep the same friendly copy
        // because we genuinely don't know if the address exists.
        setMigrationStatus({
          ok: true,
          message: `Si ce compte existe, un email a été envoyé à ${email}.`,
          messageId: '',
        });
        setMigrationSent(true);
      } else if (delivery.status === 'invalid' || delivery.reason === 'invalid_format') {
        // iter154 — server rejected the address as malformed.
        setMigrationStatus({
          ok: false,
          message: data.message || "Adresse email invalide. Corrige l'orthographe et réessaie.",
          messageId: '',
        });
        setMigrationSent(false);
      } else if (delivery.status === 'blocked') {
        // iter154 — disposable domain or known hard-bounce.
        setMigrationStatus({
          ok: false,
          message: data.message || "Cette adresse n'est pas acceptée — utilise une autre adresse personnelle ou contacte le support.",
          messageId: '',
        });
        setMigrationSent(false);
      } else {
        // delivery.status === 'failed' — provider rejected
        setMigrationStatus({
          ok: false,
          message: "L'envoi a échoué côté provider — vérifie l'adresse et réessaie, ou contacte le support si le problème persiste.",
          messageId: '',
        });
        setMigrationSent(false);
      }
    } catch (e) {
      setMigrationStatus({
        ok: false,
        message: "Réseau indisponible — réessaie quand tu auras une connexion.",
        messageId: '',
      });
      setMigrationSent(false);
    } finally {
      setMigrationLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col relative" data-testid="login-page">
      {/* ══ HEADER BAR — White bar with coloured JAPAP logo + language switcher ══ */}
      <div className="w-full flex items-center justify-between px-4 py-3 relative z-20"
        style={{ background: '#fff', boxShadow: '0 1px 6px rgba(15,5,107,0.1)' }}>
        {/* iter206 — Spacer keeps the logo centered while leaving room for the
            language switcher on the right. Empty <div> = same width on mobile. */}
        <div className="w-20" aria-hidden />
        <img src="/japap-logo.jpg" alt="JAPAP" className="h-10 object-contain" data-testid="login-logo" />
        <div className="w-20 flex justify-end">
          <LanguageSwitcher variant="dark" compact data-testid="login-lang-switcher" />
        </div>
      </div>

      {/* ══ BACKGROUND IMAGE — Friends at sunset ══ */}
      <div className="flex-1 relative flex flex-col">
        <div className="absolute inset-0 z-0">
          <img
            src="https://images.pexels.com/photos/28999878/pexels-photo-28999878.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"
            alt="" className="w-full h-full object-cover" />
          <div className="absolute inset-0" style={{ background: 'linear-gradient(to bottom, rgba(11,5,66,0.3) 0%, rgba(0,0,0,0.55) 50%, rgba(0,0,0,0.75) 100%)' }} />
        </div>

        {/* ══ FORM CONTENT — Over the image ══ */}
        <div className="flex-1 flex flex-col justify-center items-center px-6 relative z-10 max-w-md mx-auto w-full">
          <h1 className="font-['Outfit'] text-3xl md:text-4xl font-extrabold text-white text-center mb-2" data-testid="login-title">
            {t('auth.login_title')}
          </h1>
          <p className="font-['Manrope'] text-white/70 text-center text-sm mb-8 max-w-xs">
            {t('auth.login_subtitle')}
          </p>

          {error && !migrationReset && (
            <div className="w-full mb-4 px-4 py-3 rounded-xl text-sm font-['Manrope'] text-white"
              style={{ background: 'rgba(220,38,38,0.8)' }} data-testid="login-error">{error}</div>
          )}

          {migrationReset && (
            <div
              className="w-full mb-4 px-4 py-4 rounded-xl text-sm font-['Manrope'] text-white space-y-3"
              style={{ background: 'rgba(11,5,107,0.85)', border: '1px solid rgba(255,255,255,0.2)' }}
              data-testid="login-migration-banner"
            >
              <p className="font-semibold leading-snug" data-testid="login-migration-text">
                {error || 'Votre compte a été migré vers JAPAP 4.0. Veuillez définir un nouveau mot de passe.'}
              </p>

              {/* iter152 — real delivery feedback. We no longer claim
                  "Email envoyé" unless the provider accepted the message. */}
              {migrationStatus.ok === true && migrationSent && (
                <p className="text-xs text-white/85" data-testid="login-migration-sent">
                  {migrationStatus.message}
                </p>
              )}
              {migrationStatus.ok === false && (
                <p
                  className="text-xs px-3 py-2 rounded-lg font-medium"
                  style={{ background: 'rgba(220,38,38,0.85)', color: 'white' }}
                  data-testid="login-migration-error"
                >
                  {migrationStatus.message}
                </p>
              )}

              {/* Initial CTA + resend button after a successful or failed attempt */}
              {!migrationSent && migrationStatus.ok !== false && (
                <button
                  type="button"
                  onClick={handleMigrationReset}
                  disabled={migrationLoading}
                  className="w-full py-2.5 rounded-full font-bold text-white text-sm transition-all disabled:opacity-50"
                  style={{ background: '#22c55e' }}
                  data-testid="login-migration-cta"
                >
                  {migrationLoading
                    ? 'Envoi en cours…'
                    : 'Définir un nouveau mot de passe'}
                </button>
              )}

              {(migrationSent || migrationStatus.ok === false) && (
                <button
                  type="button"
                  onClick={handleMigrationReset}
                  disabled={migrationLoading || migrationCooldown > 0}
                  className="w-full py-2 rounded-full font-semibold text-white text-xs transition-all disabled:opacity-50"
                  style={{ background: 'rgba(255,255,255,0.2)', border: '1px solid rgba(255,255,255,0.35)' }}
                  data-testid="login-migration-resend"
                >
                  {migrationLoading
                    ? 'Envoi en cours…'
                    : migrationCooldown > 0
                      ? `Renvoyer le lien (${migrationCooldown}s)`
                      : 'Renvoyer le lien'}
                </button>
              )}
            </div>
          )}

          <form onSubmit={handleSubmit} className="w-full space-y-4">
            {/* Username/Email Input — Transparent with icon */}
            <div className="relative">
              <User className="absolute left-4 top-1/2 -translate-y-1/2 text-white/70" size={20} />
              <input data-testid="login-email-input" type="email" value={email} onChange={e => setEmail(e.target.value)}
                autoComplete="username"
                inputMode="email"
                autoCapitalize="off"
                autoCorrect="off"
                className="w-full pl-12 pr-4 py-3.5 rounded-full text-white text-sm font-['Manrope'] placeholder:text-white/50 outline-none transition-all"
                style={{ background: 'rgba(255,255,255,0.15)', backdropFilter: 'blur(8px)', border: '1px solid rgba(255,255,255,0.25)' }}
                placeholder={t('auth.username_placeholder')} required />
            </div>

            {/* Password Input — Transparent with icon + eye toggle */}
            <div className="relative">
              <Lock className="absolute left-4 top-1/2 -translate-y-1/2 text-white/70" size={20} />
              <input data-testid="login-password-input" type={showPassword ? 'text' : 'password'} value={password}
                onChange={e => setPassword(e.target.value)}
                autoComplete="current-password"
                className="w-full pl-12 pr-12 py-3.5 rounded-full text-white text-sm font-['Manrope'] placeholder:text-white/50 outline-none transition-all"
                style={{ background: 'rgba(255,255,255,0.15)', backdropFilter: 'blur(8px)', border: '1px solid rgba(255,255,255,0.25)' }}
                placeholder={t('auth.password_placeholder')} required />
              <button type="button" onClick={() => setShowPassword(!showPassword)}
                className="absolute right-4 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full flex items-center justify-center"
                style={{ background: 'rgba(255,255,255,0.2)' }}>
                {showPassword ? <EyeSlash size={14} className="text-white" /> : <Eye size={14} className="text-white" />}
              </button>
            </div>

            {/* iter141ter — Math captcha (replaces Cloudflare Turnstile) */}
            <MathCaptcha ref={captchaRef} onChange={setCaptcha} />

            {/* iter237 — Se souvenir de moi (OFF par défaut). */}
            <label className="flex items-center gap-2 text-xs font-['Manrope'] text-white/80 cursor-pointer select-none px-1"
                   data-testid="login-remember-me-row">
              <input type="checkbox"
                     data-testid="login-remember-me"
                     checked={rememberMe}
                     onChange={(e) => setRememberMe(e.target.checked)}
                     className="w-4 h-4 rounded cursor-pointer"
                     style={{ accentColor: '#FFD700' }} />
              <span>Se souvenir de moi</span>
              <span className="text-white/50 text-[10px] ml-1">— reste connecté(e) sur cet appareil</span>
            </label>

            {/* Links: Forgot password + Register */}
            <div className="flex items-center justify-between px-1">
              <Link to="/forgot-password" data-testid="forgot-password-link" className="text-xs font-['Manrope'] text-white/60 underline">
                {t('auth.forgot_password_cta')}
              </Link>
              <Link to="/register" state={{ from: redirectTo }} className="text-xs font-['Manrope'] text-white/80 underline font-semibold" data-testid="register-link">
                {t('auth.signup_cta')}
              </Link>
            </div>

            {/* Login Button — Blue */}
            <button data-testid="login-submit-button" type="submit" disabled={loading}
              className="w-full py-3.5 rounded-full font-['Manrope'] font-bold text-white text-base transition-all disabled:opacity-50"
              style={{ background: '#0F056B' }}>
              {loading ? t('auth.loading_login') : t('auth.login_cta')}
            </button>
            {/* iter237n — Mention légale obligatoire sous le bouton de connexion */}
            <p className="mt-3 text-[11px] text-center leading-relaxed"
               style={{ color: 'rgba(255,255,255,0.65)' }}
               data-testid="login-legal-blurb">
              En te connectant, tu acceptes nos{' '}
              <Link to="/legal/cgu" className="underline font-bold"
                    style={{ color: 'rgba(255,255,255,0.9)' }}
                    data-testid="login-legal-cgu">
                Conditions Générales d'Utilisation
              </Link>{' '}
              et notre{' '}
              <Link to="/legal/confidentialite" className="underline font-bold"
                    style={{ color: 'rgba(255,255,255,0.9)' }}
                    data-testid="login-legal-privacy">
                Politique de confidentialité
              </Link>.
            </p>
          </form>

          {/* QR Code section */}
          <div className="flex items-center gap-4 mt-6 px-2">
            <div className="flex-1">
              <p className="text-white/60 text-xs font-['Manrope']">{t('auth.scan_download_1')}</p>
              <p className="text-white/60 text-xs font-['Manrope']">{t('auth.scan_download_2')}</p>
            </div>
            <div className="w-16 h-16 rounded-lg flex items-center justify-center" style={{ background: 'white' }}>
              <svg viewBox="0 0 40 40" width="40" height="40">
                <rect width="40" height="40" fill="white"/>
                <rect x="4" y="4" width="12" height="12" fill="#0F056B" rx="2"/>
                <rect x="24" y="4" width="12" height="12" fill="#0F056B" rx="2"/>
                <rect x="4" y="24" width="12" height="12" fill="#0F056B" rx="2"/>
                <rect x="7" y="7" width="6" height="6" fill="white" rx="1"/>
                <rect x="27" y="7" width="6" height="6" fill="white" rx="1"/>
                <rect x="7" y="27" width="6" height="6" fill="white" rx="1"/>
                <rect x="18" y="4" width="4" height="4" fill="#0F056B"/>
                <rect x="18" y="18" width="4" height="4" fill="#0F056B"/>
                <rect x="24" y="18" width="4" height="4" fill="#0F056B"/>
                <rect x="32" y="18" width="4" height="4" fill="#0F056B"/>
                <rect x="18" y="28" width="4" height="4" fill="#0F056B"/>
                <rect x="26" y="26" width="6" height="6" fill="#0F056B" rx="1"/>
                <rect x="34" y="26" width="2" height="2" fill="#0F056B"/>
                <rect x="34" y="32" width="2" height="2" fill="#0F056B"/>
              </svg>
            </div>
          </div>
        </div>

        {/* ══ FOOTER ══ */}
        <div className="relative z-10 py-3 px-4 flex items-center justify-center" style={{ background: '#0F056B' }}>
          <p className="text-white/60 text-[10px] font-['Manrope'] text-center">
            2026 {t('auth.footer_copyright')} . <span className="text-white/80">{t('auth.footer_policy')}</span> . <span className="text-white/80">{t('auth.footer_contact')}</span> . <span className="text-white/80">{t('auth.footer_about')}</span> . <span className="text-white/80">{t('auth.footer_market')}</span>
          </p>
        </div>
      </div>
    </div>
  );
}
