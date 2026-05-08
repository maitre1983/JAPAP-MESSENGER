import { useState, useEffect, useMemo, useRef } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/context/AuthContext';
import {
  Eye, EyeSlash, CheckCircle, XCircle, Envelope, CaretDown,
  MagnifyingGlass, ShieldCheck,
} from '@phosphor-icons/react';
import axios from 'axios';
import JapapLogo from '@/components/JapapLogo';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import MathCaptcha from '@/components/MathCaptcha';
// iter83 P0 fix — ship the countries list IN the bundle so the registration
// page never blocks on a network call. The API is still queried as
// progressive enhancement (to pick up future additions), but it can never
// make the selector empty anymore.
import COUNTRIES_FALLBACK from '@/data/countries.json';
import { extractErrorMessage } from '@/utils/errorMessage';

const API = process.env.REACT_APP_BACKEND_URL;

/* ── Helpers ─────────────────────────────────────────────────────────── */
function flagFor(code) {
  if (!code || code.length !== 2) return '🌐';
  return String.fromCodePoint(...[...code.toUpperCase()].map(c => 0x1F1E6 + c.charCodeAt(0) - 65));
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

/* ── Country combobox (single clean element) ─────────────────────────── */
function CountryCombobox({ countries, value, onChange, placeholder, searchLabel, testId }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef(null);

  useEffect(() => {
    const onDocClick = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  const selected = countries.find(c => c.code === value);
  const filtered = useMemo(() => {
    const q = query.toLowerCase().trim();
    if (!q) return countries;
    return countries.filter(c =>
      c.name.toLowerCase().includes(q) ||
      c.code.toLowerCase() === q ||
      (c.dial || '').includes(q)
    );
  }, [countries, query]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        data-testid={testId}
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between gap-2 px-4 py-3 rounded-xl text-left transition-all"
        style={{
          background: 'var(--jp-surface-secondary)',
          border: `1px solid ${open ? 'var(--jp-primary)' : 'rgba(15,5,107,0.12)'}`,
          color: selected ? 'var(--jp-text)' : 'var(--jp-text-muted)',
        }}
      >
        <span className="flex items-center gap-2 truncate text-sm font-['Manrope']">
          {selected ? (
            <>
              <span className="text-xl leading-none">{flagFor(selected.code)}</span>
              <span className="font-semibold truncate">{selected.name}</span>
              <span className="opacity-60">({selected.dial})</span>
            </>
          ) : (
            <span>{placeholder}</span>
          )}
        </span>
        <CaretDown size={16} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>

      {open && (
        <div
          className="absolute z-50 mt-1 w-full rounded-xl overflow-hidden shadow-2xl max-h-72 flex flex-col"
          style={{ background: 'var(--jp-surface)', border: '1px solid rgba(15,5,107,0.15)' }}
        >
          <div className="p-2 border-b" style={{ borderColor: 'rgba(15,5,107,0.08)' }}>
            <div className="relative">
              <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 opacity-50" />
              <input
                autoFocus
                placeholder={searchLabel}
                value={query}
                onChange={e => setQuery(e.target.value)}
                data-testid="register-country-search"
                className="w-full pl-9 pr-3 py-2 text-sm rounded-lg outline-none"
                style={{ background: 'var(--jp-surface-secondary)', color: 'var(--jp-text)' }}
              />
            </div>
          </div>
          <div className="flex-1 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="px-4 py-6 text-center text-sm opacity-60 font-['Manrope']">—</div>
            ) : (
              filtered.map(c => (
                <button
                  key={c.code}
                  type="button"
                  data-testid={`country-option-${c.code}`}
                  onClick={() => { onChange(c.code); setOpen(false); setQuery(''); }}
                  className="w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm font-['Manrope'] transition-all hover:bg-[var(--jp-surface-secondary)]"
                  style={{ color: 'var(--jp-text)' }}
                >
                  <span className="text-xl leading-none">{flagFor(c.code)}</span>
                  <span className="flex-1 truncate">{c.name}</span>
                  <span className="opacity-60">{c.dial}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── OTP input (6 boxes, paste + auto-advance) ──────────────────────── */
function OtpBoxes({ value, onChange, onComplete }) {
  const refs = useRef([]);

  const handleChange = (idx, raw) => {
    const digit = (raw || '').replace(/\D/g, '').slice(-1);
    const next = value.split('');
    next[idx] = digit;
    const joined = next.join('').padEnd(6, '').slice(0, 6).replace(/\s/g, '');
    onChange(joined);
    if (digit && idx < 5) refs.current[idx + 1]?.focus();
    // Auto-submit only when every one of the 6 positions is a real digit.
    // NB: "123456".includes('') is truthy in JS, so use a char-level check.
    if (joined.length === 6 && [...joined].every(c => /\d/.test(c))) onComplete(joined);
  };

  const handleKey = (idx, e) => {
    if (e.key === 'Backspace' && !value[idx] && idx > 0) refs.current[idx - 1]?.focus();
    if (e.key === 'ArrowLeft' && idx > 0) refs.current[idx - 1]?.focus();
    if (e.key === 'ArrowRight' && idx < 5) refs.current[idx + 1]?.focus();
  };

  const handlePaste = (e) => {
    const paste = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 6);
    if (!paste) return;
    e.preventDefault();
    onChange(paste.padEnd(6, ''));
    refs.current[Math.min(paste.length, 5)]?.focus();
    if (paste.length === 6) onComplete(paste);
  };

  return (
    <div className="flex gap-2 justify-center" onPaste={handlePaste}>
      {Array.from({ length: 6 }, (_, i) => (
        <input
          key={i}
          ref={el => (refs.current[i] = el)}
          value={value[i] || ''}
          onChange={e => handleChange(i, e.target.value)}
          onKeyDown={e => handleKey(i, e)}
          inputMode="numeric"
          maxLength={1}
          data-testid={`otp-box-${i}`}
          className="w-12 h-14 text-center text-2xl font-['Outfit'] font-bold rounded-xl outline-none transition-all"
          style={{
            background: 'var(--jp-surface-secondary)',
            border: `2px solid ${value[i] ? 'var(--jp-primary)' : 'rgba(15,5,107,0.15)'}`,
            color: 'var(--jp-text)',
          }}
        />
      ))}
    </div>
  );
}

/* ── Main page ───────────────────────────────────────────────────────── */
export default function RegisterPage() {
  const [step, setStep] = useState('form');
  const [form, setForm] = useState({
    first_name: '', last_name: '', email: '', password: '', confirmPassword: '',
    referral_code: '', country_code: '', phone_number: '',
  });
  const [showReferral, setShowReferral] = useState(false);
  const [otp, setOtp] = useState('');
  // iter83 — seed with the static bundle so the selector is NEVER empty.
  const [countries, setCountries] = useState(COUNTRIES_FALLBACK);
  const [showPassword, setShowPassword] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [showTerms, setShowTerms] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [loading, setLoading] = useState(false);
  const [resendIn, setResendIn] = useState(0);
  const [captcha, setCaptcha] = useState({ captcha_id: '', captcha_answer: '' });
  const captchaRef = useRef(null);

  const { register, verifyOtp, resendOtp } = useAuth();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  // iter141nine — support `?redirect=` query param for shared payment links.
  const queryRedirect = new URLSearchParams(location.search).get('redirect');
  const redirectTo = location.state?.from || queryRedirect || '/feed';

  /* Load countries + geo-detect on mount */
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [c, g] = await Promise.all([
          axios.get(`${API}/api/geo/countries`).catch(() => null),
          axios.get(`${API}/api/geo/detect`).catch(() => null),
        ]);
        if (!alive) return;
        // Only overwrite the static bundle when the API returned a NON-EMPTY
        // list. A 200 with [] or a 5xx network error must never drain the
        // selector — the user can still register with the pre-shipped list.
        const apiList = c?.data?.countries;
        if (Array.isArray(apiList) && apiList.length >= 50) {
          setCountries(apiList);
        }
        const detected = g?.data?.country_code;
        if (detected) setForm(prev => ({ ...prev, country_code: detected }));
      } catch { /* silent — fallback list already renders */ }
    })();
    const ref = new URLSearchParams(window.location.search).get('ref');
    let pending = '';
    try {
      pending = localStorage.getItem('japap_pending_ref') || '';
    } catch { /* private mode */ }
    const code = (ref || pending || '').trim().toUpperCase();
    if (code) {
      setForm(prev => ({ ...prev, referral_code: code }));
      setShowReferral(true);
    }
    return () => { alive = false; };
  }, []);

  /* Resend countdown */
  useEffect(() => {
    if (resendIn <= 0) return;
    const id = setInterval(() => setResendIn(s => (s > 0 ? s - 1 : 0)), 1000);
    return () => clearInterval(id);
  }, [resendIn]);

  const selectedCountry = countries.find(c => c.code === form.country_code);
  const dial = selectedCountry?.dial || '';

  const handleChange = (field) => (e) => setForm(prev => ({ ...prev, [field]: e.target.value }));

  /* Live validation signals */
  const emailValid = EMAIL_RE.test(form.email.trim());
  const passStrength = useMemo(() => {
    const p = form.password;
    if (!p) return 'empty';
    if (p.length < 8) return 'weak';
    const strong = /[A-Z]/.test(p) && /\d/.test(p) && /[^A-Za-z0-9]/.test(p);
    return strong ? 'strong' : 'ok';
  }, [form.password]);
  const passMatch = form.confirmPassword && form.password === form.confirmPassword;
  const passMismatch = form.confirmPassword && form.password !== form.confirmPassword;

  const localDigits = (form.phone_number || '').replace(/\D/g, '');
  const phoneValid = localDigits.length >= 7 && localDigits.length <= 10;

  const canSubmit =
    form.first_name.trim() &&
    form.last_name.trim() &&
    emailValid &&
    form.password.length >= 8 &&
    passMatch &&
    form.country_code &&
    phoneValid &&
    termsAccepted &&
    !loading;

  const submitForm = async (e) => {
    e.preventDefault();
    setError(''); setInfo('');
    if (!form.first_name.trim()) return setError(t('auth.err_first_name'));
    if (!form.last_name.trim()) return setError(t('auth.err_last_name'));
    if (!form.country_code) return setError(t('auth.err_country_required'));
    if (!phoneValid) return setError(t('auth.err_phone_required'));
    if (!emailValid) return setError(t('auth.err_email'));
    if (form.password.length < 8) return setError(t('auth.err_pass_short'));
    if (!passMatch) return setError(t('auth.err_pass_mismatch'));
    if (!termsAccepted) return setError(t('auth.err_terms_required'));
    if (!captcha.captcha_answer || !captcha.captcha_answer.trim()) {
      return setError('Réponds au calcul pour continuer.');
    }

    setLoading(true);
    try {
      const fullPhone = `${dial}${localDigits}`;
      // iter110 — capture UTM tracking from the share-link landing.
      const urlParams = new URLSearchParams(window.location.search);
      let utmSource = urlParams.get('utm_source') || '';
      let utmMedium = urlParams.get('utm_medium') || '';
      let utmCampaign = urlParams.get('utm_campaign') || '';
      try {
        if (!utmSource) utmSource = localStorage.getItem('japap_pending_utm_source') || '';
        if (!utmMedium) utmMedium = localStorage.getItem('japap_pending_utm_medium') || '';
        if (!utmCampaign) utmCampaign = localStorage.getItem('japap_pending_utm_campaign') || '';
      } catch { /* private mode */ }
      await register({
        email: form.email.toLowerCase().trim(),
        password: form.password,
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        terms_accepted: termsAccepted,
        referral_code: form.referral_code.trim(),
        country_code: form.country_code,
        phone_number: fullPhone,
        turnstile_token: undefined,
        captcha_id: captcha.captcha_id,
        captcha_answer: captcha.captcha_answer,
        utm_source: utmSource ? utmSource.slice(0, 40) : undefined,
        utm_medium: utmMedium ? utmMedium.slice(0, 40) : undefined,
        utm_campaign: utmCampaign ? utmCampaign.slice(0, 80) : undefined,
      });
      // Cleanup pending markers — registration succeeded.
      try {
        localStorage.removeItem('japap_pending_ref');
        localStorage.removeItem('japap_pending_ref_at');
        localStorage.removeItem('japap_pending_utm_source');
        localStorage.removeItem('japap_pending_utm_medium');
        localStorage.removeItem('japap_pending_utm_campaign');
      } catch { /* private mode */ }
      setInfo('');
      setStep('otp');
      setResendIn(60);
    } catch (err) {
      setError(extractErrorMessage(err));
      // Refresh captcha after any failure so user gets a fresh problem.
      captchaRef.current?.refresh();
      setCaptcha({ captcha_id: '', captcha_answer: '' });
    } finally { setLoading(false); }
  };

  const submitOtp = async (code) => {
    // iter139 — Prevent double-submit when OtpBoxes auto-completes during
    // a paste while the user also presses the submit button. The race
    // would fire two POSTs and the second would always 400 with "OTP déjà
    // utilisé" → confusing red error toast right after success.
    if (loading) return;
    setError(''); setInfo('');
    setLoading(true);
    try {
      await verifyOtp(form.email.toLowerCase().trim(), code);
      // iter237n — On registration completion, persist the legal acceptance
      // timestamps (CGU + Privacy) — the checkbox was mandatory at form step.
      // Best-effort; never block navigation if the call fails.
      try {
        await Promise.all([
          axios.post(`${API}/api/legal/accept-cgu`, {}, { withCredentials: true }),
          axios.post(`${API}/api/legal/accept-privacy`, {}, { withCredentials: true }),
        ]);
      } catch (_) { /* silent — checkbox was acknowledged client-side */ }
      navigate(redirectTo, { replace: true });
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally { setLoading(false); }
  };

  const handleResend = async () => {
    if (resendIn > 0) return;
    setError(''); setInfo('');
    try {
      await resendOtp(form.email.toLowerCase().trim());
      setInfo(t('auth.otp_resent_ok') || 'A new code has been sent.');
      setResendIn(60);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  };

  /* ── Render ─────────────────────────────────────────────────────── */
  return (
    <div
      className="min-h-screen flex items-center justify-center p-4 relative overflow-hidden"
      style={{ background: 'linear-gradient(135deg,#0F056B 0%,#1a0a8f 50%,#0B0542 100%)' }}
    >
      {/* Subtle radial glow */}
      <div className="absolute inset-0 opacity-40 pointer-events-none"
        style={{ background: 'radial-gradient(circle at 20% 20%, rgba(224,28,46,0.15), transparent 50%)' }} />

      <div
        className="w-full max-w-md p-6 sm:p-8 rounded-3xl shadow-2xl jp-animate-fadeIn relative z-10"
        style={{ background: 'var(--jp-surface)' }}
        data-testid="register-card"
      >
        {/* iter206 — language switcher accessible depuis l'inscription aussi */}
        <div className="absolute top-3 right-3 z-10">
          <LanguageSwitcher variant="dark" compact data-testid="register-lang-switcher" />
        </div>
        <div className="text-center mb-6">
          <div className="flex justify-center mb-3"><JapapLogo size="lg" /></div>
          <h1 className="text-xl font-['Outfit'] font-extrabold" style={{ color: 'var(--jp-text)' }}>
            {step === 'form' ? t('auth.register_title') : t('auth.otp_title')}
          </h1>
          <p className="text-xs font-['Manrope'] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
            {step === 'form' ? t('auth.register_subtitle') : `${t('auth.otp_subtitle')} ${form.email}`}
          </p>
        </div>

        {error && (
          <div
            className="flex items-start gap-2 text-xs font-['Manrope'] px-3 py-2 rounded-lg mb-3"
            data-testid="register-error"
            style={{ background: 'rgba(224,28,46,0.1)', color: '#E01C2E' }}
          >
            <XCircle size={14} weight="bold" className="mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}
        {info && (
          <div
            className="flex items-start gap-2 text-xs font-['Manrope'] px-3 py-2 rounded-lg mb-3"
            data-testid="register-info"
            style={{ background: 'rgba(15,5,107,0.08)', color: 'var(--jp-primary)' }}
          >
            <CheckCircle size={14} weight="bold" className="mt-0.5 flex-shrink-0" />
            <span>{info}</span>
          </div>
        )}

        {step === 'form' ? (
          <form onSubmit={submitForm} className="space-y-4" noValidate>
            {/* Names */}
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="block text-xs font-['Manrope'] font-semibold mb-1"
                  style={{ color: 'var(--jp-text-secondary)' }}>
                  {t('auth.first_name')} *
                </span>
                <input
                  data-testid="register-first-name"
                  className="jp-input w-full"
                  required
                  autoComplete="given-name"
                  value={form.first_name}
                  onChange={handleChange('first_name')}
                />
              </label>
              <label className="block">
                <span className="block text-xs font-['Manrope'] font-semibold mb-1"
                  style={{ color: 'var(--jp-text-secondary)' }}>
                  {t('auth.last_name')} *
                </span>
                <input
                  data-testid="register-last-name"
                  className="jp-input w-full"
                  required
                  autoComplete="family-name"
                  value={form.last_name}
                  onChange={handleChange('last_name')}
                />
              </label>
            </div>

            {/* Country combobox */}
            <div>
              <label className="block text-xs font-['Manrope'] font-semibold mb-1"
                style={{ color: 'var(--jp-text-secondary)' }}>
                {t('auth.country')} *
              </label>
              <CountryCombobox
                countries={countries}
                value={form.country_code}
                onChange={(code) => setForm(prev => ({ ...prev, country_code: code }))}
                placeholder={t('auth.country_placeholder')}
                searchLabel={t('auth.search_country')}
                testId="register-country-select"
              />
            </div>

            {/* Phone with integrated dial-code prefix */}
            <div>
              <label className="block text-xs font-['Manrope'] font-semibold mb-1"
                style={{ color: 'var(--jp-text-secondary)' }}>
                {t('auth.phone')} *
              </label>
              <div
                className="flex items-center gap-0 rounded-xl overflow-hidden transition-all"
                style={{
                  background: 'var(--jp-surface-secondary)',
                  border: '1px solid rgba(15,5,107,0.12)',
                }}
              >
                <div className="flex items-center gap-1 px-3 py-3 border-r"
                  style={{ borderColor: 'rgba(15,5,107,0.12)' }}>
                  <span className="text-lg leading-none">{selectedCountry ? flagFor(selectedCountry.code) : '🌐'}</span>
                  <span className="text-sm font-['Manrope'] font-bold"
                    style={{ color: 'var(--jp-text)' }}>
                    {dial || '—'}
                  </span>
                </div>
                <input
                  data-testid="register-phone"
                  required
                  inputMode="tel"
                  autoComplete="tel-national"
                  placeholder={t('auth.phone_placeholder')}
                  value={form.phone_number}
                  onChange={handleChange('phone_number')}
                  className="flex-1 px-3 py-3 bg-transparent outline-none text-sm font-['Manrope']"
                  style={{ color: 'var(--jp-text)' }}
                />
              </div>
            </div>

            {/* Email */}
            <div>
              <label className="block text-xs font-['Manrope'] font-semibold mb-1"
                style={{ color: 'var(--jp-text-secondary)' }}>
                {t('auth.email')} *
              </label>
              <input
                data-testid="register-email"
                className="jp-input w-full"
                type="email"
                required
                autoComplete="email"
                placeholder={t('auth.email_placeholder')}
                value={form.email}
                onChange={handleChange('email')}
              />
              {form.email && (
                <p className="mt-1 text-[11px] font-['Manrope'] flex items-center gap-1"
                  style={{ color: emailValid ? '#10B981' : '#E01C2E' }}>
                  {emailValid ? <CheckCircle size={12} weight="bold" /> : <XCircle size={12} weight="bold" />}
                  {emailValid ? t('auth.email') + ' ✓' : t('auth.err_email')}
                </p>
              )}
            </div>

            {/* Password + confirm with live signals */}
            <div>
              <label className="block text-xs font-['Manrope'] font-semibold mb-1"
                style={{ color: 'var(--jp-text-secondary)' }}>
                {t('auth.password_label')} *
              </label>
              <div className="relative">
                <input
                  data-testid="register-password"
                  className="jp-input w-full pr-10"
                  type={showPassword ? 'text' : 'password'}
                  required
                  autoComplete="new-password"
                  placeholder={t('auth.password_placeholder_strong')}
                  value={form.password}
                  onChange={handleChange('password')}
                />
                <button type="button" onClick={() => setShowPassword(v => !v)}
                  data-testid="toggle-password-visibility"
                  className="absolute right-3 top-1/2 -translate-y-1/2"
                  style={{ color: 'var(--jp-text-muted)' }}>
                  {showPassword ? <EyeSlash size={18} /> : <Eye size={18} />}
                </button>
              </div>
              {form.password && (
                <div className="mt-1 flex items-center gap-1 text-[11px] font-['Manrope']"
                  style={{ color: passStrength === 'weak' ? '#E01C2E'
                    : passStrength === 'strong' ? '#10B981' : '#F59E0B' }}>
                  {passStrength === 'weak' ? <XCircle size={12} weight="bold" /> : <CheckCircle size={12} weight="bold" />}
                  <span>
                    {passStrength === 'weak' ? t('auth.password_strength_weak')
                     : passStrength === 'strong' ? t('auth.password_strength_strong')
                     : t('auth.password_strength_ok')}
                  </span>
                </div>
              )}
            </div>

            <div>
              <label className="block text-xs font-['Manrope'] font-semibold mb-1"
                style={{ color: 'var(--jp-text-secondary)' }}>
                {t('auth.confirm_password')} *
              </label>
              <input
                data-testid="register-confirm-password"
                className="jp-input w-full"
                type={showPassword ? 'text' : 'password'}
                required
                autoComplete="new-password"
                value={form.confirmPassword}
                onChange={handleChange('confirmPassword')}
              />
              {form.confirmPassword && (
                <p className="mt-1 text-[11px] font-['Manrope'] flex items-center gap-1"
                  style={{ color: passMatch ? '#10B981' : '#E01C2E' }}>
                  {passMatch ? <CheckCircle size={12} weight="bold" /> : <XCircle size={12} weight="bold" />}
                  {passMatch ? t('auth.password_match_ok') : t('auth.password_match_ko')}
                </p>
              )}
            </div>

            {/* Collapsible referral */}
            {!showReferral ? (
              <button
                type="button"
                data-testid="register-referral-toggle"
                onClick={() => setShowReferral(true)}
                className="text-xs font-['Manrope'] font-semibold underline"
                style={{ color: 'var(--jp-primary)' }}
              >
                {t('auth.referral_link')}
              </button>
            ) : (
              <div>
                <label className="block text-xs font-['Manrope'] font-semibold mb-1"
                  style={{ color: 'var(--jp-text-secondary)' }}>
                  {t('auth.referral_placeholder')}
                </label>
                <input
                  data-testid="register-referral"
                  className="jp-input w-full uppercase"
                  value={form.referral_code}
                  onChange={handleChange('referral_code')}
                />
              </div>
            )}

            {/* T&C */}
            <label className="flex items-start gap-2 text-xs font-['Manrope'] cursor-pointer"
              style={{ color: 'var(--jp-text-secondary)' }}>
              <input
                type="checkbox"
                data-testid="register-terms"
                checked={termsAccepted}
                onChange={e => setTermsAccepted(e.target.checked)}
                className="mt-0.5 cursor-pointer"
              />
              <span>
                {/* iter237n — La case d'acceptation reprend la mention exacte
                    et lie aux pages légales officielles. */}
                J'ai lu et j'accepte les{' '}
                <Link to="/legal/cgu" target="_blank"
                      className="underline font-bold"
                      style={{ color: 'var(--jp-primary)' }}
                      data-testid="register-link-cgu">
                  Conditions Générales d'Utilisation
                </Link>
                {' '}et la{' '}
                <Link to="/legal/confidentialite" target="_blank"
                      className="underline font-bold"
                      style={{ color: 'var(--jp-primary)' }}
                      data-testid="register-link-privacy">
                  Politique de confidentialité
                </Link>{' '}de JAPAP TECHNOLOGIES PLC.
              </span>
            </label>

            {/* iter141ter — Math captcha (replaces Cloudflare Turnstile) */}
            <MathCaptcha
              ref={captchaRef}
              onChange={setCaptcha}
              theme="light"
              label="Vérification rapide"
              helper="Réponds au calcul pour finaliser ton inscription"
            />

            <button
              type="submit"
              disabled={!canSubmit}
              data-testid="register-submit"
              className="w-full py-3.5 rounded-xl font-['Outfit'] font-bold text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed"
              style={{
                background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)',
                boxShadow: '0 10px 30px -12px rgba(224,28,46,0.5)',
              }}
            >
              {loading ? t('auth.create_account_loading') : t('auth.create_account_cta')}
            </button>

            <p className="text-center text-xs font-['Manrope']"
              style={{ color: 'var(--jp-text-muted)' }}>
              {t('auth.have_account_prefix')}{' '}
              <Link to="/login" className="font-bold"
                style={{ color: 'var(--jp-primary)' }} data-testid="link-login">
                {t('auth.sign_in_cta')}
              </Link>
            </p>
          </form>
        ) : (
          <div className="space-y-5">
            <div className="flex items-center gap-2 p-3 rounded-xl"
              style={{ background: 'var(--jp-surface-secondary)' }}>
              <Envelope size={18} style={{ color: 'var(--jp-primary)' }} weight="bold" />
              <div className="flex-1 min-w-0">
                <p className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                  Code envoyé à
                </p>
                <p className="text-sm font-['Manrope'] font-semibold truncate"
                  style={{ color: 'var(--jp-text)' }}>
                  {form.email}
                </p>
              </div>
            </div>

            <OtpBoxes
              value={otp}
              onChange={setOtp}
              onComplete={submitOtp}
            />

            <button
              type="button"
              disabled={loading || otp.length !== 6}
              onClick={() => submitOtp(otp)}
              data-testid="register-verify-otp"
              className="w-full py-3.5 rounded-xl font-['Outfit'] font-bold text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed"
              style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}
            >
              {loading ? t('auth.otp_verify_loading') : t('auth.otp_verify_cta')}
            </button>

            <div className="flex items-center justify-between text-xs font-['Manrope']">
              <button
                type="button"
                onClick={() => { setStep('form'); setOtp(''); }}
                data-testid="register-back"
                style={{ color: 'var(--jp-text-muted)' }}
              >
                {t('auth.otp_back')}
              </button>
              <button
                type="button"
                disabled={resendIn > 0}
                onClick={handleResend}
                data-testid="register-resend-otp"
                className="font-semibold disabled:opacity-40"
                style={{ color: 'var(--jp-primary)' }}
              >
                {resendIn > 0
                  ? t('auth.otp_resend_in', { s: resendIn })
                  : t('auth.otp_resend_now')}
              </button>
            </div>
          </div>
        )}

        {/* Terms modal */}
        {showTerms && (
          <div
            className="fixed inset-0 z-[9999] flex items-center justify-center p-4"
            style={{ background: 'rgba(11,5,66,0.6)', backdropFilter: 'blur(6px)' }}
            onClick={() => setShowTerms(false)}
          >
            <div
              className="max-w-lg rounded-2xl p-6 max-h-[80vh] overflow-y-auto"
              style={{ background: 'var(--jp-surface)' }}
              onClick={e => e.stopPropagation()}
            >
              <div className="flex items-center gap-2 mb-3">
                <ShieldCheck size={22} weight="bold" style={{ color: 'var(--jp-primary)' }} />
                <h3 className="font-['Outfit'] font-bold text-lg"
                  style={{ color: 'var(--jp-text)' }}>
                  {t('auth.accept_terms_link')}
                </h3>
              </div>
              <p className="text-xs font-['Manrope'] leading-relaxed"
                style={{ color: 'var(--jp-text-secondary)' }}>
                By creating a JAPAP account, you agree to our usage policies — no spam,
                no illegal activity, wallet KYC rules for withdrawals, respect for other
                users, and data processing consistent with local regulations. Full text
                available on request.
              </p>
              <button
                onClick={() => setShowTerms(false)}
                className="mt-4 w-full py-2 rounded-lg font-semibold"
                style={{ background: 'var(--jp-primary)', color: '#fff' }}
              >
                {t('common.close')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
