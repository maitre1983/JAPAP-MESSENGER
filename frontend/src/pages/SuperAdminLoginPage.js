import { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import axios from 'axios';
import { ShieldCheck, XCircle, Envelope, CaretLeft } from '@phosphor-icons/react';
import JapapLogo from '@/components/JapapLogo';
import { useAuth } from '@/context/AuthContext';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

function formatApiError(detail) {
  if (!detail) return 'Une erreur est survenue.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(e => e?.msg || JSON.stringify(e)).join(' ');
  return String(detail);
}

/* 6-box OTP input — identical pattern to RegisterPage */
function OtpBoxes({ value, onChange, onComplete }) {
  const refs = useRef([]);

  const handleChange = (idx, raw) => {
    const digit = (raw || '').replace(/\D/g, '').slice(-1);
    const next = value.split('');
    next[idx] = digit;
    const joined = next.join('').padEnd(6, '').slice(0, 6).replace(/\s/g, '');
    onChange(joined);
    if (digit && idx < 5) refs.current[idx + 1]?.focus();
    if (joined.length === 6 && [...joined].every(c => /\d/.test(c))) onComplete(joined);
  };
  const handleKey = (idx, e) => {
    if (e.key === 'Backspace' && !value[idx] && idx > 0) refs.current[idx - 1]?.focus();
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
          data-testid={`sa-otp-${i}`}
          className="w-12 h-14 text-center text-2xl font-['Outfit'] font-bold rounded-xl outline-none bg-white/10 text-white"
          style={{ border: `2px solid ${value[i] ? '#E01C2E' : 'rgba(255,255,255,0.2)'}` }}
        />
      ))}
    </div>
  );
}

export default function SuperAdminLoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  // Path shape: /admin{DDMMYY} → extract the 6 digits that follow 'admin'.
  const token = (location.pathname.match(/^\/admin(\d{6})/) || [, ''])[1];
  const { login, verifySuperadmin2fa } = useAuth();

  const [tokenValid, setTokenValid] = useState(null); // null=loading, true/false
  const [step, setStep] = useState('password');        // 'password' | 'otp'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [otp, setOtp] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [resendIn, setResendIn] = useState(0);

  /* Validate the date token against the backend on mount */
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/admin/super/url-check?token=${encodeURIComponent(token || '')}`,
        );
        if (alive) setTokenValid(Boolean(data?.valid));
      } catch {
        if (alive) setTokenValid(false);
      }
    })();
    return () => { alive = false; };
  }, [token]);

  useEffect(() => {
    if (resendIn <= 0) return;
    const id = setInterval(() => setResendIn(s => Math.max(0, s - 1)), 1000);
    return () => clearInterval(id);
  }, [resendIn]);

  const submitPassword = async (e) => {
    e.preventDefault();
    setError(''); setLoading(true);
    try {
      const res = await login(email.trim().toLowerCase(), password);
      // If the backend returned the standard user payload, the user was not
      // a superadmin → block here (normal users must NOT be able to reach
      // the superadmin dashboard through this dynamic path).
      if (res?.user && res.user.role !== 'superadmin') {
        setError("Ce portail est réservé au superadmin.");
        return;
      }
      // Otherwise the backend flipped into 2FA mode.
      if (res?.status === 'otp_required') {
        setStep('otp');
        setResendIn(60);
      }
    } catch (err) {
      setError(formatApiError(err.response?.data?.detail) || err.message);
    } finally { setLoading(false); }
  };

  const submitOtp = async (code) => {
    setError(''); setLoading(true);
    try {
      const res = await verifySuperadmin2fa(email.trim().toLowerCase(), code);
      if (res?.user?.role === 'superadmin') {
        navigate(`/admin${token}/dashboard`, { replace: true });
      } else {
        setError("Compte invalide pour ce portail.");
      }
    } catch (err) {
      setError(formatApiError(err.response?.data?.detail) || err.message);
    } finally { setLoading(false); }
  };

  const resendCode = async () => {
    if (resendIn > 0) return;
    setError(''); setLoading(true);
    try {
      // Re-trigger the login flow to issue a new OTP.
      await login(email.trim().toLowerCase(), password);
      setResendIn(60);
    } catch (err) {
      // 429 throttle will come here — surface the message as-is.
      setError(formatApiError(err.response?.data?.detail) || err.message);
    } finally { setLoading(false); }
  };

  /* ── 404 for invalid token (or while loading nothing visible) ─────── */
  if (tokenValid === null) {
    return (
      <div className="min-h-screen flex items-center justify-center"
        style={{ background: '#0B0542' }}>
        <div className="w-10 h-10 border-4 border-white/20 border-t-white rounded-full animate-spin" />
      </div>
    );
  }
  if (tokenValid === false) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-4 text-center text-white"
        style={{ background: 'linear-gradient(135deg,#0B0542 0%,#0F056B 100%)' }}>
        <div className="mb-4 opacity-60"><JapapLogo size="lg" /></div>
        <h1 className="text-6xl font-['Outfit'] font-black mb-2">404</h1>
        <p className="text-sm font-['Manrope'] opacity-70 mb-6">Cette page n'existe pas.</p>
        <button
          onClick={() => navigate('/')}
          data-testid="sa-404-home"
          className="px-5 py-2.5 rounded-full text-sm font-semibold"
          style={{ background: '#fff', color: '#0F056B' }}
        >
          Retour à l'accueil
        </button>
      </div>
    );
  }

  /* ── Main render ──────────────────────────────────────────────────── */
  return (
    <div
      className="min-h-screen flex items-center justify-center p-4 text-white relative overflow-hidden"
      style={{ background: 'linear-gradient(135deg,#0B0542 0%,#0F056B 60%,#000 100%)' }}
      data-testid="sa-login-page"
    >
      <div className="absolute top-[-120px] right-[-120px] w-[320px] h-[320px] rounded-full opacity-20 blur-3xl"
        style={{ background: '#E01C2E' }} />

      <div className="w-full max-w-md relative z-10 p-7 rounded-3xl jp-animate-fadeIn"
        style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', backdropFilter: 'blur(12px)' }}>
        <div className="flex flex-col items-center mb-6">
          <div className="flex items-center gap-2 px-3 py-1 rounded-full text-[10px] font-['Manrope'] font-bold uppercase tracking-wider mb-4"
            style={{ background: 'rgba(224,28,46,0.15)', color: '#FCA5A5' }}>
            <ShieldCheck size={12} weight="bold" /> Superadmin Portal
          </div>
          <JapapLogo size="lg" inverted />
          <h1 className="mt-4 text-xl font-['Outfit'] font-extrabold">
            {step === 'password' ? 'Connexion superadmin' : t('super_admin_login.verification_2fa')}
          </h1>
          <p className="text-xs font-['Manrope'] opacity-60 mt-1 text-center">
            {step === 'password'
              ? t('super_admin_login.saisissez_vos_identifiants_un_code')
              : `Saisissez le code envoyé à ${email}.`}
          </p>
        </div>

        {error && (
          <div className="flex items-start gap-2 text-xs font-['Manrope'] px-3 py-2 rounded-lg mb-4"
            data-testid="sa-error"
            style={{ background: 'rgba(224,28,46,0.15)', color: '#FCA5A5', border: '1px solid rgba(224,28,46,0.3)' }}>
            <XCircle size={14} weight="bold" className="mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {step === 'password' ? (
          <form onSubmit={submitPassword} className="space-y-4" noValidate>
            <input
              type="email"
              required
              placeholder={t('super_admin_login.admin_japap_com')}
              value={email}
              onChange={e => setEmail(e.target.value)}
              data-testid="sa-email"
              className="w-full px-4 py-3 rounded-xl outline-none text-sm font-['Manrope'] text-white placeholder:text-white/40"
              style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.15)' }}
            />
            <input
              type="password"
              required
              placeholder={t('super_admin_login.mot_de_passe')}
              value={password}
              onChange={e => setPassword(e.target.value)}
              data-testid="sa-password"
              className="w-full px-4 py-3 rounded-xl outline-none text-sm font-['Manrope'] text-white placeholder:text-white/40"
              style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.15)' }}
            />
            <button
              type="submit"
              disabled={loading || !email || !password}
              data-testid="sa-submit-password"
              className="w-full py-3.5 rounded-xl font-['Outfit'] font-bold text-white transition-all disabled:opacity-40"
              style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}
            >
              {loading ? t('super_admin_login.verification') : 'Continuer'}
            </button>
          </form>
        ) : (
          <div className="space-y-5">
            <div className="flex items-center gap-2 p-3 rounded-xl"
              style={{ background: 'rgba(255,255,255,0.06)' }}>
              <Envelope size={18} weight="bold" style={{ color: '#FCA5A5' }} />
              <span className="text-xs font-['Manrope'] truncate">{email}</span>
            </div>

            <OtpBoxes value={otp} onChange={setOtp} onComplete={submitOtp} />

            <button
              type="button"
              disabled={loading || otp.length !== 6}
              onClick={() => submitOtp(otp)}
              data-testid="sa-submit-otp"
              className="w-full py-3.5 rounded-xl font-['Outfit'] font-bold text-white transition-all disabled:opacity-40"
              style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}
            >
              {loading ? t('super_admin_login.verification') : 'Entrer dans le portail'}
            </button>

            <div className="flex items-center justify-between text-xs font-['Manrope']">
              <button
                type="button"
                onClick={() => { setStep('password'); setOtp(''); setError(''); }}
                data-testid="sa-back-password"
                className="flex items-center gap-1 opacity-60 hover:opacity-100"
              >
                <CaretLeft size={12} weight="bold" /> Retour
              </button>
              <button
                type="button"
                disabled={resendIn > 0 || loading}
                onClick={resendCode}
                data-testid="sa-resend"
                className="font-semibold disabled:opacity-40"
                style={{ color: '#FCA5A5' }}
              >
                {resendIn > 0 ? `Renvoyer dans ${resendIn}s` : 'Renvoyer le code'}
              </button>
            </div>
          </div>
        )}

        <p className="text-center text-[10px] font-['Manrope'] opacity-40 mt-6">
          Protégé par 2FA · audit trail · rate-limit
        </p>
      </div>
    </div>
  );
}
