/**
 * ReferralPreviewPage — Public conversion landing for /p/:code (iter110).
 *
 * Renders a minimal, single-CTA page that introduces the referrer and the
 * incentives. Designed to maximise WhatsApp/Telegram inbound conversions:
 *  - referrer's avatar + first name ("Marie t'invite sur JAPAP")
 *  - exclusive welcome bonus (referee_usd) prominently shown
 *  - boost banner if a Referral Boost Event is currently active
 *  - 3 reasons to join (cards) + 1 single CTA "Rejoindre maintenant"
 *
 * UTM forwarding: we forward any utm_* query params straight through to the
 * /register URL so the conversion is correctly attributed by the backend.
 */
import { useEffect, useState } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import axios from 'axios';
import { useTranslation } from 'react-i18next';
import {
  Lightning, Wallet as WalletIcon, GameController, Users as UsersIcon,
  ArrowRight, Crown, Sparkle, ShieldCheck,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ReferralPreviewPage() {
  const { t } = useTranslation();
  const { code } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const clean = (code || '').trim().toUpperCase().slice(0, 16);
    if (!clean) {
      setError('Code invalide');
      setLoading(false);
      return;
    }
    // Persist code + UTM in localStorage so the registration page picks them
    // up even if the user clicks the CTA after a slight detour.
    try {
      localStorage.setItem('japap_pending_ref', clean);
      localStorage.setItem('japap_pending_ref_at', String(Date.now()));
      const params = new URLSearchParams(location.search);
      const utmSource = params.get('utm_source');
      const utmMedium = params.get('utm_medium');
      const utmCampaign = params.get('utm_campaign');
      if (utmSource) localStorage.setItem('japap_pending_utm_source', utmSource.slice(0, 40));
      if (utmMedium) localStorage.setItem('japap_pending_utm_medium', utmMedium.slice(0, 40));
      if (utmCampaign) localStorage.setItem('japap_pending_utm_campaign', utmCampaign.slice(0, 80));
    } catch { /* private mode */ }

    let alive = true;
    axios.get(`${API}/api/referrals/preview/${encodeURIComponent(clean)}`)
      .then((r) => { if (alive) { setData(r.data); setLoading(false); } })
      .catch((e) => {
        if (!alive) return;
        setError(e.response?.data?.detail || 'Code de parrainage introuvable');
        setLoading(false);
      });
    return () => { alive = false; };
  }, [code, location.search]);

  const onJoin = () => {
    const params = new URLSearchParams(location.search);
    if (data?.code) params.set('ref', data.code);
    navigate(`/register?${params.toString()}`);
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center"
           style={{ background: 'var(--jp-surface)' }}
           data-testid="referral-preview-loading">
        <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-6 text-center"
           style={{ background: 'var(--jp-surface)' }}
           data-testid="referral-preview-error">
        <div className="text-2xl mb-2">😔</div>
        <h1 className="font-['Outfit'] text-xl font-bold mb-1">Lien indisponible</h1>
        <p className="text-sm mb-4" style={{ color: 'var(--jp-text-muted)' }}>
          {error || 'Ce code de parrainage est introuvable ou expiré.'}
        </p>
        <button onClick={() => navigate('/register')}
                className="jp-btn jp-btn-primary" data-testid="referral-preview-fallback-cta">
          Créer mon compte
        </button>
      </div>
    );
  }

  const r = data.referrer || {};
  const refereeUsd = parseFloat(data.bonuses?.referee_usd || 0);
  const referrerUsd = parseFloat(data.bonuses?.referrer_usd || 0);
  const boost = data.boost && data.boost.active ? data.boost : null;
  const boostMult = boost ? Number(boost.multiplier || 1) : 1;
  const effRefereeUsd = boost && boost.applies_to_referee ? refereeUsd * boostMult : refereeUsd;

  const initial = (r.first_name || r.username || 'J').slice(0, 1).toUpperCase();
  const flag = r.country_code ? r.country_code : '';

  return (
    <div className="min-h-screen jp-animate-fadeIn"
         style={{
           background: 'linear-gradient(160deg, #0F056B 0%, #1B0A8A 60%, #E01C2E 140%)',
           color: 'white',
         }}
         data-testid="referral-preview-page">
      <div className="max-w-md mx-auto px-5 py-8 pb-16">
        {/* Branding */}
        <div className="flex items-center justify-between mb-5">
          <img src="/japap-logo.jpg" alt="JAPAP" className="h-9 w-auto rounded-lg"
               onError={(e) => { e.currentTarget.style.display = 'none'; }} />
          <span className="text-[10px] uppercase tracking-wider font-bold opacity-70">
            Invitation parrainage
          </span>
        </div>

        {/* Boost banner */}
        {boost && (
          <div className="mb-4 p-3 rounded-2xl flex items-center gap-2 jp-animate-pulse"
               style={{ background: 'rgba(245,158,11,0.18)', border: '1px solid rgba(245,158,11,0.4)' }}
               data-testid="referral-preview-boost">
            <Lightning size={18} weight="fill" style={{ color: '#FBBF24' }} />
            <div className="text-xs">
              <strong>{boost.name || 'Bonus boost'} ×{boostMult}</strong>
              {boost.applies_to_referee && ' · bonus filleul boosté'}
            </div>
          </div>
        )}

        {/* Hero */}
        <div className="text-center mb-6">
          <div className="inline-flex w-20 h-20 rounded-full items-center justify-center mb-3 mx-auto overflow-hidden relative"
               style={{ background: 'rgba(255,255,255,0.18)', border: '2px solid rgba(255,255,255,0.4)' }}
               data-testid="referral-preview-avatar">
            <span className="font-['Outfit'] text-3xl font-extrabold absolute inset-0 flex items-center justify-center">
              {initial}
            </span>
            {r.avatar ? (
              <img src={r.avatar} alt={r.name} className="w-full h-full object-cover relative"
                   onError={(e) => { e.currentTarget.style.display = 'none'; }} />
            ) : null}
          </div>
          {r.is_pro && (
            <div className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold mb-2"
                 style={{ background: 'rgba(245,158,11,0.25)', color: '#FBBF24' }}>
              <Crown size={10} weight="fill" /> JAPAP PRO
            </div>
          )}
          <h1 className="font-['Outfit'] text-3xl font-extrabold leading-tight"
              data-testid="referral-preview-headline">
            <span style={{ color: '#FBBF24' }}>{r.first_name || r.name || 'Un ami'}</span>{' '}
            t'invite sur JAPAP
            {flag && <span className="ml-1 text-base opacity-70 font-normal">· {flag}</span>}
          </h1>
          <p className="text-sm opacity-85 mt-2">
            Rejoins la super-app sociale, financière et ludique d'Afrique.
          </p>
        </div>

        {/* Bonus card */}
        {effRefereeUsd > 0 && (
          <div className="mb-5 p-4 rounded-2xl text-center"
               style={{
                 background: 'rgba(255,255,255,0.95)',
                 color: '#0F056B',
                 boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
               }}
               data-testid="referral-preview-bonus">
            <div className="text-[10px] uppercase tracking-wider font-bold opacity-70">
              Ton bonus de bienvenue
            </div>
            <div className="font-['Outfit'] text-4xl font-extrabold my-1"
                 style={{ color: '#E01C2E' }}>
              ${effRefereeUsd.toFixed(2)}
            </div>
            <div className="text-xs opacity-70">
              Crédité automatiquement sur ton wallet dès l'activation.
              {boost && boost.applies_to_referee && (
                <span className="ml-1 font-bold" style={{ color: '#F59E0B' }}>
                  ×{boostMult} BOOST !
                </span>
              )}
            </div>
          </div>
        )}

        {/* Reasons */}
        <div className="grid grid-cols-1 gap-2.5 mb-6" data-testid="referral-preview-reasons">
          <Reason icon={WalletIcon} title={t('referral_preview.wallet_multi_devises')}
                  desc="Reçois, envoie, dépose et retire en USDT, XAF, XOF, USD…" />
          <Reason icon={GameController} title={t('referral_preview.jeux_recompenses_quotidiennes')}
                  desc="Quiz, Tap Challenge, Roue de la fortune. Gagne en jouant." />
          <Reason icon={UsersIcon} title={t('referral_preview.chat_feed_marketplace')}
                  desc="Tout en une seule app, mobile-first." />
          {referrerUsd > 0 && (
            <Reason icon={Sparkle} title={`${r.first_name || 'Ton parrain'} reçoit aussi un bonus`}
                    desc={`+$${referrerUsd.toFixed(2)} dès que tu seras activé.`} />
          )}
        </div>

        {/* CTA */}
        <button onClick={onJoin}
                className="w-full font-['Outfit'] font-extrabold text-lg py-4 rounded-full
                           flex items-center justify-center gap-2 transition-all"
                style={{
                  background: 'white',
                  color: '#E01C2E',
                  boxShadow: '0 16px 40px rgba(224,28,46,0.45)',
                }}
                data-testid="referral-preview-cta">
          Rejoindre maintenant <ArrowRight size={20} weight="bold" />
        </button>
        <p className="text-center text-[11px] opacity-70 mt-3 flex items-center justify-center gap-1">
          <ShieldCheck size={12} weight="fill" />
          Inscription gratuite · 30 secondes
        </p>

        {/* Already have an account */}
        <div className="text-center mt-4 text-xs opacity-80">
          Déjà inscrit ?{' '}
          <button onClick={() => navigate('/login')} className="underline font-bold"
                  data-testid="referral-preview-login-link">
            Se connecter
          </button>
        </div>
      </div>
    </div>
  );
}

function Reason({ icon: Icon, title, desc }) {
  return (
    <div className="flex items-start gap-3 p-3 rounded-xl"
         style={{ background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.12)' }}>
      <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
           style={{ background: 'rgba(255,255,255,0.15)' }}>
        <Icon size={18} weight="duotone" style={{ color: '#FBBF24' }} />
      </div>
      <div className="min-w-0">
        <div className="text-sm font-bold">{title}</div>
        <div className="text-[11px] opacity-80">{desc}</div>
      </div>
    </div>
  );
}
