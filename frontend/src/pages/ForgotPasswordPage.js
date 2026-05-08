import { useState, useRef } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { Envelope, CheckCircle } from '@phosphor-icons/react';
import JapapLogo from '@/components/JapapLogo';
import MathCaptcha from '@/components/MathCaptcha';
import { extractErrorMessage } from '@/utils/errorMessage';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [captcha, setCaptcha] = useState({ captcha_id: '', captcha_answer: '' });
  const captchaRef = useRef(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!email.includes('@')) { setError('Adresse e-mail invalide.'); return; }
    if (!captcha.captcha_answer || !captcha.captcha_answer.trim()) {
      setError('Réponds au calcul pour continuer.');
      return;
    }
    setLoading(true);
    try {
      await axios.post(`${API}/api/auth/forgot-password`, {
        email: email.toLowerCase().trim(),
        captcha_id: captcha.captcha_id,
        captcha_answer: captcha.captcha_answer,
      });
      setSent(true);
    } catch (err) {
      setError(extractErrorMessage(err, 'Une erreur est survenue, réessaie.'));
      captchaRef.current?.refresh();
      setCaptcha({ captcha_id: '', captcha_answer: '' });
    } finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4"
      style={{ background: 'linear-gradient(135deg,#0F056B 0%,#1a0a8f 50%,#0B0542 100%)' }}>
      <div className="w-full max-w-md p-6 rounded-2xl shadow-2xl jp-animate-fadeIn"
        style={{ background: 'var(--jp-surface)' }} data-testid="forgot-password-card">
        <div className="text-center mb-6">
          <div className="flex justify-center mb-3"><JapapLogo size="lg" /></div>
          <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
            Mot de passe oublié ?
          </p>
        </div>

        {sent ? (
          <div className="space-y-4 text-center" data-testid="forgot-password-success">
            <CheckCircle size={56} weight="fill" style={{ color: 'var(--jp-primary)', margin: '0 auto' }} />
            <h2 className="font-['Outfit'] font-bold text-lg">Vérifie tes e-mails</h2>
            <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>
              Si un compte existe pour <strong>{email}</strong>, un lien de réinitialisation
              a été envoyé. Il expirera dans 1 heure.
            </p>
            <Link to="/login"
              className="block py-3 rounded-xl font-['Outfit'] font-bold text-white"
              style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}>
              Retour à la connexion
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="text-xs font-['Manrope'] px-3 py-2 rounded-lg"
                style={{ background: 'rgba(224,28,46,0.1)', color: '#E01C2E' }}
                data-testid="forgot-password-error">{error}</div>
            )}
            <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>
              Saisis l'adresse e-mail utilisée à l'inscription. Nous t'enverrons un lien pour choisir un nouveau mot de passe.
            </p>
            <div className="relative">
              <Envelope size={18} weight="bold" className="absolute left-3 top-1/2 -translate-y-1/2"
                style={{ color: 'var(--jp-text-muted)' }} />
              <input type="email" required value={email} onChange={e => setEmail(e.target.value)}
                data-testid="forgot-password-email" autoFocus placeholder="vous@exemple.com"
                className="jp-input w-full pl-10" />
            </div>

            {/* iter141ter — Math captcha (replaces Cloudflare Turnstile) */}
            <MathCaptcha
              ref={captchaRef}
              onChange={setCaptcha}
              theme="light"
              label="Vérification rapide"
            />

            <button type="submit" disabled={loading} data-testid="forgot-password-submit"
              className="w-full py-3 rounded-xl font-['Outfit'] font-bold text-white transition-all disabled:opacity-50"
              style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}>
              {loading ? 'Envoi…' : 'Envoyer le lien de réinitialisation'}
            </button>
            <p className="text-center text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
              Tu te souviens de ton mot de passe ?{' '}
              <Link to="/login" className="font-semibold" style={{ color: 'var(--jp-primary)' }}>Se connecter</Link>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
