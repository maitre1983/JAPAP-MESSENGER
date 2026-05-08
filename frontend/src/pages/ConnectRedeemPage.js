/**
 * ConnectRedeemPage — User-side: scan a QR OR type the short code → exchange
 * it for a decrypted WiFi password (behind Pro gating + anti-fraud).
 *
 * URL: /connect/redeem?nonce=xxx
 *
 * State machine:
 *   enter-code  → User pastes/scans the nonce (prefilled from URL param)
 *   redeeming   → POST /api/connect/access/redeem (spinner)
 *   pw-revealed → Shows SSID + masked password + 90s auto-hide countdown
 *   upgrade     → 403 PRO_REQUIRED → upsell screen
 *   error       → any other blocking error
 *
 * Mobile-first, extremely minimal UI, zero distractions.
 */
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  WifiHigh, Eye, EyeSlash, Copy, CheckCircle, Crown, X, ArrowLeft,
  Lightning, Timer, QrCode, Shield,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ConnectRedeemPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const initialNonce = params.get('nonce') || '';

  const [nonce, setNonce] = useState(initialNonce);
  const [phase, setPhase] = useState(initialNonce ? 'redeeming' : 'enter-code');
  const [payload, setPayload] = useState(null);  // { ssid, password, security_type, connection_id, hide_after_seconds, ... }
  const [showPw, setShowPw] = useState(false);
  const [hideIn, setHideIn] = useState(0);
  const [upgradeInfo, setUpgradeInfo] = useState(null);   // { tier, min_plan }
  const [errorMsg, setErrorMsg] = useState('');
  const hasRedeemedRef = useRef(false);

  // Auto-redeem on first mount if the URL carried a nonce
  useEffect(() => {
    if (initialNonce && !hasRedeemedRef.current) {
      hasRedeemedRef.current = true;
      doRedeem(initialNonce);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialNonce]);

  // Auto-hide password countdown
  useEffect(() => {
    if (phase !== 'pw-revealed' || !payload?.hide_after_seconds) return;
    setHideIn(payload.hide_after_seconds);
    const iv = setInterval(() => {
      setHideIn((prev) => {
        if (prev <= 1) {
          setShowPw(false);
          clearInterval(iv);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(iv);
  }, [phase, payload]);

  const doRedeem = async (nonceValue) => {
    setPhase('redeeming');
    setErrorMsg('');
    setUpgradeInfo(null);
    try {
      const { data } = await axios.post(`${API}/api/connect/access/redeem`,
        { nonce: nonceValue.trim(), device_id: getDeviceId() },
        { withCredentials: true });
      setPayload(data);
      setPhase('pw-revealed');
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || '';
      if (status === 403 && detail.startsWith('PRO_REQUIRED:')) {
        const [, , tier] = detail.split(':');
        setUpgradeInfo({ tier: tier || 'starter' });
        setPhase('upgrade');
      } else if (status === 410) {
        setErrorMsg('Ce QR code a expiré ou a déjà été utilisé. Demandez un nouveau QR à l\'hôte.');
        setPhase('error');
      } else if (status === 429) {
        setErrorMsg(detail || 'Limite journalière atteinte. Réessayez demain.');
        setPhase('error');
      } else if (status === 404) {
        setErrorMsg('QR code inconnu. Vérifiez le code saisi ou scannez à nouveau.');
        setPhase('error');
      } else {
        setErrorMsg(detail || 'Erreur lors de la validation.');
        setPhase('error');
      }
    }
  };

  const revealAgain = async () => {
    if (!payload?.connection_id) return;
    try {
      const { data } = await axios.get(
        `${API}/api/connect/access/${payload.connection_id}/password`,
        { withCredentials: true });
      setPayload((prev) => ({ ...prev, ...data }));
      setShowPw(true);
      setHideIn(data.hide_after_seconds || 90);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Impossible de ré-afficher.');
    }
  };

  const copyPassword = () => {
    if (!payload?.password) return;
    navigator.clipboard.writeText(payload.password).then(
      () => toast.success('Mot de passe copié — collez-le dans la fenêtre WiFi.'),
      () => toast.error('Copie impossible sur ce navigateur.'),
    );
  };

  const copySSID = () => {
    if (!payload?.ssid) return;
    navigator.clipboard.writeText(payload.ssid).then(
      () => toast.success('Nom du réseau copié.'),
      () => {},
    );
  };

  return (
    <div className="min-h-screen w-full flex flex-col"
         style={{ background: 'linear-gradient(180deg, #0A0A1F 0%, #18181B 100%)', color: 'white' }}
         data-testid="connect-redeem-page">
      {/* Header */}
      <header className="px-4 pt-safe pt-4 pb-2 flex items-center justify-between">
        <Link to="/connect" className="p-2 -ml-2 rounded-full hover:bg-white/10" data-testid="connect-redeem-back">
          <ArrowLeft size={22} />
        </Link>
        <h1 className="font-['Outfit'] font-extrabold text-sm uppercase tracking-[0.2em]">JAPAP Connect</h1>
        <span className="w-10" />
      </header>

      <main className="flex-1 w-full max-w-md mx-auto px-5 py-6 flex flex-col items-center justify-center gap-6">
        {phase === 'enter-code' && (
          <CodeEntry nonce={nonce} setNonce={setNonce} onSubmit={() => nonce && doRedeem(nonce)} />
        )}

        {phase === 'redeeming' && <RedeemingSpinner />}

        {phase === 'pw-revealed' && payload && (
          <RevealedPassword
            payload={payload}
            showPw={showPw}
            hideIn={hideIn}
            onToggleShow={() => {
              if (!showPw && hideIn === 0) return revealAgain();
              setShowPw(!showPw);
            }}
            onCopyPw={copyPassword}
            onCopySsid={copySSID}
            onDone={() => navigate('/connect')}
          />
        )}

        {phase === 'upgrade' && <UpgradeCard tier={upgradeInfo?.tier || 'starter'} />}

        {phase === 'error' && (
          <ErrorCard message={errorMsg}
                     onRetry={() => { setPhase('enter-code'); setErrorMsg(''); }} />
        )}
      </main>
    </div>
  );
}

/* ───────────────── Phase components ───────────────── */

function CodeEntry({ nonce, setNonce, onSubmit }) {
  const { t } = useTranslation();
  return (
    <div className="w-full flex flex-col items-center gap-5">
      <div className="w-20 h-20 rounded-full flex items-center justify-center"
           style={{ background: 'rgba(247,147,26,0.15)' }}>
        <QrCode size={38} weight="bold" style={{ color: '#F7931A' }} />
      </div>
      <h2 className="text-xl font-['Outfit'] font-bold text-center">Scannez ou tapez le code</h2>
      <p className="text-sm text-center opacity-70 max-w-xs">
        Demandez à l'hôte du WiFi d'afficher son QR JAPAP, ou saisissez les 6 premiers caractères manuellement.
      </p>
      <form onSubmit={(e) => { e.preventDefault(); onSubmit(); }} className="w-full">
        <input
          autoFocus value={nonce} onChange={(e) => setNonce(e.target.value)}
          placeholder={t('connect_redeem.code_ou_nonce')}
          className="w-full px-4 py-3 rounded-2xl text-center font-mono text-lg tracking-widest outline-none"
          style={{ background: 'rgba(255,255,255,0.08)', color: 'white', border: '1px solid rgba(255,255,255,0.15)' }}
          data-testid="connect-redeem-nonce-input"
        />
        <button type="submit" disabled={!nonce}
                className="mt-4 w-full py-3 rounded-2xl font-bold transition-transform active:scale-95"
                style={{ background: nonce
                  ? 'linear-gradient(135deg, #F7931A 0%, #EC4899 100%)' : 'rgba(255,255,255,0.15)', color: 'white' }}
                data-testid="connect-redeem-submit">
          Débloquer l'accès WiFi
        </button>
      </form>
    </div>
  );
}

function RedeemingSpinner() {
  return (
    <div className="w-full flex flex-col items-center gap-5" data-testid="connect-redeem-loading">
      <div className="jp-spinner-lg" />
      <p className="text-sm opacity-70">Vérification de votre accès…</p>
    </div>
  );
}

function RevealedPassword({ payload, showPw, hideIn, onToggleShow, onCopyPw, onCopySsid, onDone }) {
  const { t } = useTranslation();
  return (
    <div className="w-full flex flex-col gap-5" data-testid="connect-redeem-success">
      <div className="flex flex-col items-center gap-3">
        <div className="w-16 h-16 rounded-full flex items-center justify-center"
             style={{ background: 'rgba(16,185,129,0.15)' }}>
          <CheckCircle size={36} weight="fill" style={{ color: '#10B981' }} />
        </div>
        <h2 className="text-xl font-['Outfit'] font-bold">Accès autorisé</h2>
        <p className="text-xs opacity-60 text-center">{payload.hotspot_alias}</p>
      </div>

      <div className="rounded-2xl p-5 flex flex-col gap-4"
           style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)' }}>
        <div className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-[0.2em] opacity-60 font-bold">Réseau</span>
          <div className="flex items-center gap-2">
            <WifiHigh size={18} style={{ color: '#F7931A' }} />
            <span className="font-bold truncate flex-1" data-testid="connect-redeem-ssid">{payload.ssid || '—'}</span>
            <button onClick={onCopySsid} className="p-2 rounded-full hover:bg-white/10"
                    data-testid="connect-redeem-copy-ssid" aria-label="Copier SSID">
              <Copy size={16} />
            </button>
          </div>
          <span className="text-[11px] opacity-50">Sécurité : {payload.security_type || 'WPA2'}</span>
        </div>

        <div className="h-px" style={{ background: 'rgba(255,255,255,0.1)' }} />

        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between">
            <span className="text-[11px] uppercase tracking-[0.2em] opacity-60 font-bold">Mot de passe</span>
            {hideIn > 0 && showPw && (
              <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2 py-0.5 rounded-full"
                    style={{ background: 'rgba(239,68,68,0.15)', color: '#FCA5A5' }}
                    data-testid="connect-redeem-hide-countdown">
                <Timer size={11} /> Masquage dans {hideIn}s
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <code className="font-mono text-lg flex-1 truncate"
                  data-testid="connect-redeem-password"
                  style={{ letterSpacing: showPw ? 'normal' : '0.2em' }}>
              {showPw ? payload.password : '•'.repeat(Math.min(16, (payload.password || '').length || 8))}
            </code>
            <button onClick={onToggleShow} className="p-2 rounded-full hover:bg-white/10"
                    data-testid="connect-redeem-toggle-pw"
                    aria-label={showPw ? 'Masquer' : 'Afficher'}>
              {showPw ? <EyeSlash size={18} /> : <Eye size={18} />}
            </button>
            <button onClick={onCopyPw} className="p-2 rounded-full hover:bg-white/10"
                    data-testid="connect-redeem-copy-pw" aria-label="Copier le mot de passe">
              <Copy size={18} />
            </button>
          </div>
          {payload.reveals_remaining !== undefined && (
            <span className="text-[11px] opacity-50 mt-1">
              Ré-affichages restants : {payload.reveals_remaining}
            </span>
          )}
        </div>
      </div>

      <div className="rounded-xl p-3 text-[11px] flex items-start gap-2"
           style={{ background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.15)', color: '#93C5FD' }}>
        <Shield size={14} className="shrink-0 mt-0.5" />
        <span>{t('connect_redeem.ne_partagez_jamais_ce_mot_de_passe')}</span>
      </div>

      <button onClick={onDone}
              className="w-full py-3 rounded-2xl font-bold transition-transform active:scale-95"
              style={{ background: 'linear-gradient(135deg, #10B981 0%, #059669 100%)', color: 'white' }}
              data-testid="connect-redeem-done">
        Terminé
      </button>
    </div>
  );
}

function UpgradeCard({ tier }) {
  const tierLabels = { starter: 'Starter (5 $/mois)', creator: 'Creator (15 $/mois)', business: 'Business (49 $/mois)' };
  const navigate = useNavigate();
  return (
    <div className="w-full flex flex-col items-center gap-5 text-center" data-testid="connect-redeem-upgrade">
      <div className="w-20 h-20 rounded-full flex items-center justify-center"
           style={{ background: 'rgba(247,147,26,0.2)' }}>
        <Crown size={40} weight="fill" style={{ color: '#F7931A' }} />
      </div>
      <h2 className="text-2xl font-['Outfit'] font-extrabold">JAPAP Pro requis</h2>
      <p className="text-sm opacity-70 max-w-xs">
        Ce hotspot réserve l'accès aux abonnés Pro. Débloquez-le + toutes les fonctionnalités premium (chat IA, appels illimités, wallet multi-devise).
      </p>
      <div className="w-full rounded-2xl p-4"
           style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(247,147,26,0.3)' }}>
        <span className="text-[11px] uppercase tracking-[0.2em] opacity-60 font-bold">Plan recommandé</span>
        <p className="font-['Outfit'] font-extrabold text-lg mt-1">{tierLabels[tier] || tier}</p>
      </div>
      <button onClick={() => navigate('/pro')}
              className="w-full py-3 rounded-2xl font-bold transition-transform active:scale-95"
              style={{ background: 'linear-gradient(135deg, #F7931A 0%, #EC4899 100%)', color: 'white' }}
              data-testid="connect-redeem-upgrade-cta">
        <Lightning size={16} weight="fill" className="inline mr-1" />
        Passer à {tier || 'Pro'}
      </button>
      <Link to="/connect" className="text-xs opacity-60 hover:opacity-100 underline">
        Retour
      </Link>
    </div>
  );
}

function ErrorCard({ message, onRetry }) {
  return (
    <div className="w-full flex flex-col items-center gap-5 text-center" data-testid="connect-redeem-error">
      <div className="w-16 h-16 rounded-full flex items-center justify-center"
           style={{ background: 'rgba(239,68,68,0.15)' }}>
        <X size={36} weight="bold" style={{ color: '#EF4444' }} />
      </div>
      <p className="text-sm opacity-80 max-w-xs">{message}</p>
      <button onClick={onRetry}
              className="px-6 py-2 rounded-full text-sm font-bold"
              style={{ background: 'rgba(255,255,255,0.1)', color: 'white' }}
              data-testid="connect-redeem-retry">
        Réessayer
      </button>
    </div>
  );
}

/* ───────────────── Helper: persistent device id ───────────────── */

function getDeviceId() {
  try {
    let id = localStorage.getItem('japap_device_id');
    if (!id) {
      id = `dv_${(crypto.randomUUID?.() || Math.random().toString(36).slice(2) + Date.now()).replace(/-/g, '').slice(0, 32)}`;
      localStorage.setItem('japap_device_id', id);
    }
    return id;
  } catch {
    return null;
  }
}
