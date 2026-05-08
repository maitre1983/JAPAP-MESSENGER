import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { WifiHigh, ArrowRight, Warning, Crown } from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * iter141nineJ — Public landing page for a hotspot share-card scan.
 *
 * Visitor journey:
 *   1. Anonymous → CTA "Se connecter / Créer un compte" with `?redirect=`.
 *   2. Logged-in non-Pro → invite to upgrade to Starter (link to /pro).
 *   3. Logged-in Pro Starter+ → 1-tap CTA that triggers a fresh QR
 *      generation server-side and routes to the existing redeem flow.
 */
export default function ConnectHotspotLanding() {
  const { t } = useTranslation();
  const { hotspotId } = useParams();
  const navigate = useNavigate();
  const { user, loading: authLoading } = useAuth();
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await axios.get(`${API}/api/connect/hotspots/${hotspotId}/share`);
        if (!cancelled) setMeta(r.data);
      } catch (err) {
        if (!cancelled) setError(err.response?.data?.detail || 'Hotspot introuvable.');
      } finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [hotspotId]);

  if (loading || authLoading) {
    return <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--jp-bg)' }}>
      <div style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>
    </div>;
  }

  if (error || !meta) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4" style={{ background: 'var(--jp-bg)' }}>
        <div className="jp-card-elevated p-6 max-w-md w-full text-center" data-testid="connect-landing-error">
          <Warning size={40} weight="fill" style={{ color: '#ef4444' }} className="mx-auto mb-3" />
          <h1 className="font-['Outfit'] text-xl font-bold mb-2" style={{ color: 'var(--jp-text)' }}>Hotspot introuvable</h1>
          <p className="text-sm mb-4" style={{ color: 'var(--jp-text-muted)' }}>{error || 'Ce lien semble invalide.'}</p>
          <Link to="/" className="jp-btn jp-btn-primary">Retour à l'accueil</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen p-4 flex items-center justify-center"
         style={{ background: 'linear-gradient(180deg, #0F056B 0%, #E01C2E 100%)' }}>
      <div className="jp-card-elevated p-6 max-w-md w-full text-center"
           style={{ background: 'var(--jp-surface)' }}
           data-testid="connect-landing-card">
        <div className="rounded-2xl p-2 mb-4 inline-flex"
             style={{ background: 'rgba(247,147,26,0.10)' }}>
          <WifiHigh size={42} weight="fill" style={{ color: '#F7931A' }} />
        </div>
        <h1 className="font-['Outfit'] text-2xl font-bold mb-1" style={{ color: 'var(--jp-text)' }}
            data-testid="connect-landing-alias">{meta.alias}</h1>
        <p className="text-sm mb-6" style={{ color: 'var(--jp-text-muted)' }}>
          WiFi partagé via <strong>{t('connect_hotspot_landing.japap')}</strong>
        </p>

        {!user && (
          <div className="space-y-3">
            <p className="text-sm mb-2" style={{ color: 'var(--jp-text-muted)' }}>
              Connecte-toi pour te connecter au WiFi en 1 clic. Les nouveaux comptes reçoivent un bonus de bienvenue.
            </p>
            <button onClick={() => navigate(`/login?redirect=${encodeURIComponent(`/connect/h/${hotspotId}`)}`)}
                    data-testid="connect-landing-login"
                    className="jp-btn jp-btn-primary w-full">
              Se connecter <ArrowRight size={16} />
            </button>
            <button onClick={() => navigate(`/register?redirect=${encodeURIComponent(`/connect/h/${hotspotId}`)}`)}
                    data-testid="connect-landing-register"
                    className="jp-btn jp-btn-ghost w-full">
              Créer un compte
            </button>
          </div>
        )}

        {user && (
          <div className="space-y-3">
            <button onClick={() => {
                      // The /connect main page already exposes the QR scan
                      // flow + nearby list. Send them there with a hint.
                      toast.success(`Recherche du hotspot « ${meta.alias} » dans tes hotspots favoris…`);
                      navigate('/connect');
                    }}
                    data-testid="connect-landing-open"
                    className="jp-btn jp-btn-primary w-full">
              <WifiHigh size={18} weight="fill" /> Ouvrir sur JAPAP Connect
            </button>
            {!user.is_pro && (
              <button onClick={() => navigate('/pro')}
                      data-testid="connect-landing-upgrade"
                      className="jp-btn jp-btn-ghost w-full">
                <Crown size={16} /> Passer Pro pour te connecter au WiFi
              </button>
            )}
          </div>
        )}

        <div className="mt-6 pt-4 text-[11px]"
             style={{ color: 'var(--jp-text-muted)', borderTop: '1px solid var(--jp-border)' }}>
          Sécurisé par <strong>{t('connect_hotspot_landing.japap_connect')}</strong>
        </div>
      </div>
    </div>
  );
}
