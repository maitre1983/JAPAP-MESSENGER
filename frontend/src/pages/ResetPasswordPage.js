import { useState, useMemo } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import axios from 'axios';
import { LockKey, Eye, EyeSlash, CheckCircle } from '@phosphor-icons/react';
import JapapLogo from '@/components/JapapLogo';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = useMemo(() => params.get('token') || '', [params]);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!token) { setError('Invalid or missing reset token'); return; }
    if (password.length < 6) { setError('Password must be at least 6 characters'); return; }
    if (password !== confirm) { setError('Passwords do not match'); return; }
    setLoading(true);
    try {
      await axios.post(`${API}/api/auth/reset-password`, { token, new_password: password });
      setDone(true);
      setTimeout(() => navigate('/login', { replace: true }), 2500);
    } catch (err) {
      setError(err.response?.data?.detail || 'Reset failed, please request a new link.');
    } finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4"
      style={{ background: 'linear-gradient(135deg,#0F056B 0%,#1a0a8f 50%,#0B0542 100%)' }}>
      <div className="w-full max-w-md p-6 rounded-2xl shadow-2xl jp-animate-fadeIn"
        style={{ background: 'var(--jp-surface)' }} data-testid="reset-password-card">
        <div className="text-center mb-6">
          <div className="flex justify-center mb-3"><JapapLogo size="lg" /></div>
          <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
            Choose a new password
          </p>
        </div>

        {done ? (
          <div className="space-y-4 text-center" data-testid="reset-password-success">
            <CheckCircle size={56} weight="fill" style={{ color: 'var(--jp-primary)', margin: '0 auto' }} />
            <h2 className="font-['Outfit'] font-bold text-lg">Password updated</h2>
            <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>
              Redirecting you to sign in…
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="text-xs font-['Manrope'] px-3 py-2 rounded-lg"
                style={{ background: 'rgba(224,28,46,0.1)', color: '#E01C2E' }}
                data-testid="reset-password-error">{error}</div>
            )}
            <div className="relative">
              <LockKey size={18} weight="bold" className="absolute left-3 top-1/2 -translate-y-1/2"
                style={{ color: 'var(--jp-text-muted)' }} />
              <input type={showPassword ? 'text' : 'password'} required
                value={password} onChange={e => setPassword(e.target.value)}
                data-testid="reset-password-new" autoFocus placeholder="New password (min 6 chars)"
                className="jp-input w-full pl-10 pr-10" />
              <button type="button" onClick={() => setShowPassword(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--jp-text-muted)' }}>
                {showPassword ? <EyeSlash size={18} /> : <Eye size={18} />}
              </button>
            </div>
            <div className="relative">
              <LockKey size={18} weight="bold" className="absolute left-3 top-1/2 -translate-y-1/2"
                style={{ color: 'var(--jp-text-muted)' }} />
              <input type={showPassword ? 'text' : 'password'} required
                value={confirm} onChange={e => setConfirm(e.target.value)}
                data-testid="reset-password-confirm" placeholder="Confirm password"
                className="jp-input w-full pl-10" />
            </div>
            <button type="submit" disabled={loading || !token} data-testid="reset-password-submit"
              className="w-full py-3 rounded-xl font-['Outfit'] font-bold text-white transition-all disabled:opacity-50"
              style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}>
              {loading ? 'Updating…' : 'Update password'}
            </button>
            <p className="text-center text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
              <Link to="/login" className="font-semibold" style={{ color: 'var(--jp-primary)' }}>Back to sign in</Link>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
