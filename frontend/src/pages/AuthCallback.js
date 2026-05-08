import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';

export default function AuthCallback() {
  const hasProcessed = useRef(false);
  const navigate = useNavigate();
  const { googleLogin } = useAuth();

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const hash = window.location.hash;
    const params = new URLSearchParams(hash.replace('#', ''));
    const sessionId = params.get('session_id');

    if (sessionId) {
      googleLogin(sessionId)
        .then(() => {
          window.location.hash = '';
          navigate('/feed', { replace: true });
        })
        .catch(() => {
          navigate('/login', { replace: true });
        });
    } else {
      navigate('/login', { replace: true });
    }
  }, [googleLogin, navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#FCFBF8]" data-testid="auth-callback">
      <div className="text-center">
        <div className="w-8 h-8 border-2 border-[#1B4332] border-t-transparent rounded-full animate-spin mx-auto mb-4" />
        <p className="text-[#4A4A4A] font-['Manrope']">Connexion en cours...</p>
      </div>
    </div>
  );
}
