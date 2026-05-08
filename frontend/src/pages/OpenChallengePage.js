/**
 * OpenChallengePage — iter228
 *
 * UX: A picks free vs paid + amount → POST /api/quiz/challenge/open →
 * redirects A to /games/quiz/challenges/<cid>/play so the existing
 * QuizChallengePage handles the actual 5-question run. Once A finishes,
 * he is redirected to /games/quiz/challenges/<cid>/share to invite B.
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { ArrowLeft, Sword, Coin, Wallet } from '@phosphor-icons/react';
import { toast } from 'sonner';
import * as WS from '@/utils/walletSecurity';

const API = process.env.REACT_APP_BACKEND_URL;

export default function OpenChallengePage() {
  const navigate = useNavigate();
  const [cfg, setCfg] = useState({
    paid_enabled: false, commission_pct: 10,
    stake_min: 1, stake_max: 200, expiry_hours: 24,
  });
  const [mode, setMode] = useState('free');
  const [stake, setStake] = useState(1);
  const [allowDouble, setAllowDouble] = useState(false);  // iter232 — Mission 2
  const [busy, setBusy] = useState(false);
  const [walletInfo, setWallet] = useState({ balance_usd: 0, display_currency: 'USD', fx_rate: 1 });
  const [inputCcy, setInputCcy] = useState('USD');

  useEffect(() => {
    (async () => {
      try {
        const { data } = await axios.get(`${API}/api/games/toggles`);
        setCfg(prev => ({
          ...prev,
          paid_enabled:    Boolean(data.quiz_challenge_paid_enabled),
          commission_pct:  Number(data.quiz_challenge_commission_pct ?? prev.commission_pct),
          stake_min:       Number(data.quiz_challenge_stake_min     ?? prev.stake_min),
          stake_max:       Number(data.quiz_challenge_stake_max     ?? prev.stake_max),
          expiry_hours:    Number(data.quiz_challenge_expiry_hours  ?? prev.expiry_hours),
        }));
        setStake(Number(data.quiz_challenge_stake_min ?? 1));
      } catch {}
      try {
        const { data: w } = await axios.get(`${API}/api/wallet/balance`,
                                             { withCredentials: true });
        setWallet({
          balance_usd:      parseFloat(w.balance_usd || 0),
          display_currency: w.display_currency || 'USD',
          fx_rate:          parseFloat(w.fx_rate || 1),
        });
        if (w.display_currency && w.display_currency !== 'USD') setInputCcy(w.display_currency);
      } catch {}
    })();
  }, []);

  const safeCommission = Math.max(0, Math.min(50, Number(cfg.commission_pct) || 0));
  const pot = stake * 2;
  const commission = (pot * safeCommission) / 100;
  const winnings = pot - commission;
  const insufficient = mode === 'paid' && stake > walletInfo.balance_usd;
  // iter232 — Mission 2 (Doubler la mise): toggle is greyed if A's wallet
  // can't pre-lock 2× stake. Backend will also enforce this on /open.
  const cantAffordDouble = mode === 'paid' && (stake * 2) > walletInfo.balance_usd;
  const effectiveAllowDouble = allowDouble && !cantAffordDouble;
  // Pot estimates with potential double — shown as a hint to A.
  const potIfDoubled = stake * 4;
  const winningsIfDoubled = potIfDoubled - (potIfDoubled * safeCommission) / 100;

  const submit = async () => {
    if (busy) return; // double-tap guard
    if (mode === 'paid') {
      const check = WS.validateAmount(stake, cfg.stake_min, cfg.stake_max);
      if (!check.valid) { toast.error(check.reason); return; }
      if (insufficient) { toast.error('Solde insuffisant — recharge ton wallet.'); return; }
    }
    setBusy(true);
    try {
      // iter231 — Revalidate admin toggles at submit so a recently disabled
      // paid-mode (or new stake bounds) isn't bypassed via cached `cfg`.
      try {
        const { data: fresh } = await axios.get(`${API}/api/games/toggles`);
        if (mode === 'paid') {
          if (!fresh.quiz_challenge_paid_enabled) {
            toast.error('Le mode payant a été désactivé. Choisis le mode gratuit.');
            setMode('free');
            setBusy(false);
            return;
          }
          const minN = Number(fresh.quiz_challenge_stake_min ?? cfg.stake_min);
          const maxN = Number(fresh.quiz_challenge_stake_max ?? cfg.stake_max);
          if (stake < minN || stake > maxN) {
            toast.error(`Mise hors bornes (${minN}–${maxN} USD). Ajuste et réessaie.`);
            setBusy(false);
            return;
          }
        }
      } catch { /* if toggles refresh fails, fall through to backend validation */ }

      const body = { mode, country_code: 'CM' };
      if (mode === 'paid') {
        body.stake_amount = Number(stake);
        body.allow_double = !!effectiveAllowDouble;  // iter232 — Mission 2
      }

      const { data } = await axios.post(`${API}/api/quiz/champion/challenge/open`, body,
                                          { withCredentials: true });
      console.log('[open-challenge] API response:', data);

      // iter231 — Defensive ID extraction (handles `challenge_id`, `id`, or `cid`
      // shapes — backend currently returns `challenge_id` but never silently
      // navigate to undefined which would land on /challenges/undefined and
      // render a blank page on iOS Safari).
      const cid = data?.challenge_id || data?.id || data?.cid;
      if (!cid) {
        console.error('[open-challenge] missing challenge_id in response:', data);
        toast.error('Erreur : identifiant de défi manquant. Réessaie.');
        setBusy(false);
        return;
      }
      toast.success('Défi créé — joue maintenant !');
      // iter231 — Use replace+navigate, plus a hard-fallback if React Router
      // is somehow in a stale state (PWA on iOS Safari has shown rare cases
      // where `navigate()` is queued but never applied because the SW
      // intercepts and serves a cached HTML for the new path).
      try {
        navigate(`/games/quiz/challenges/${cid}`);
        // Belt-and-braces: if after 1.2s we're still on /challenge/new, force
        // a full location change so the user is never stuck on the blue page.
        setTimeout(() => {
          if (window.location.pathname.includes('/challenge/new')) {
            console.warn('[open-challenge] navigate() did not advance — falling back to location.assign');
            window.location.assign(`/games/quiz/challenges/${cid}`);
          }
        }, 1200);
      } catch (navErr) {
        console.error('[open-challenge] navigate threw:', navErr);
        window.location.assign(`/games/quiz/challenges/${cid}`);
      }
    } catch (e) {
      console.error('[open-challenge] submit failed:', e?.response?.status, e?.response?.data);
      toast.error(e.response?.data?.detail || 'Impossible de créer le défi.');
      setBusy(false);
      return;
    }
    // intentionally DO NOT setBusy(false) on success — we're navigating away,
    // re-enabling the button would just allow a double-create on slow devices.
  };

  return (
    <div className="min-h-screen flex flex-col"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="open-challenge-page">
      <div className="flex items-center p-4">
        <button onClick={() => navigate('/games/quiz')} data-testid="open-challenge-back"
                className="p-2 rounded-full text-white"
                style={{ background: 'rgba(255,255,255,0.1)' }}>
          <ArrowLeft size={18} weight="bold" />
        </button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-start px-6 text-center text-white max-w-md w-full mx-auto">
        <div className="w-20 h-20 rounded-3xl flex items-center justify-center mb-4"
             style={{ background: 'linear-gradient(135deg, #E01C2E, #B91C1C)',
                      boxShadow: '0 20px 60px rgba(224,28,46,0.5)' }}>
          <Sword size={40} weight="fill" />
        </div>
        <h1 className="font-['Outfit'] text-3xl font-extrabold mb-1">Créer un défi</h1>
        <p className="text-white/70 text-sm mb-6">Joue en premier puis invite un ami à relever ton score.</p>

        {/* Mode */}
        <div className="grid grid-cols-2 gap-2 w-full mb-5">
          <button onClick={() => setMode('free')} data-testid="open-mode-free"
                  className="py-3 rounded-xl font-bold text-sm transition-all active:scale-95"
                  style={{
                    background: mode === 'free' ? 'rgba(16,185,129,0.20)' : 'rgba(255,255,255,0.06)',
                    color: '#fff',
                    border: `1px solid ${mode === 'free' ? '#10B981' : 'rgba(255,255,255,0.12)'}`,
                  }}>
            🎮 Gratuit
          </button>
          <button onClick={() => cfg.paid_enabled && setMode('paid')}
                  disabled={!cfg.paid_enabled}
                  data-testid="open-mode-paid"
                  className="py-3 rounded-xl font-bold text-sm transition-all active:scale-95 disabled:opacity-40"
                  style={{
                    background: mode === 'paid' ? 'rgba(255,215,0,0.20)' : 'rgba(255,255,255,0.06)',
                    color: '#fff',
                    border: `1px solid ${mode === 'paid' ? '#FFD700' : 'rgba(255,255,255,0.12)'}`,
                  }}>
            💰 Avec mise
          </button>
        </div>

        {/* Stake */}
        {mode === 'paid' && (
          <div className="w-full mb-4">
            <div className="flex items-center justify-between mb-2">
              <div className="text-white text-xs uppercase font-bold tracking-wider">
                Mise ({inputCcy})
              </div>
              <div className="flex items-center gap-2">
                {walletInfo.display_currency !== 'USD' && WS.isValidRate(walletInfo.fx_rate) && (
                  <div className="flex rounded-full overflow-hidden text-[10px] font-bold"
                       data-testid="open-ccy-toggle"
                       style={{ border: '1px solid rgba(255,215,0,0.35)' }}>
                    {['USD', walletInfo.display_currency].map(c => (
                      <button key={c} onClick={() => setInputCcy(c)}
                              data-testid={`open-ccy-${c}`}
                              className="px-2.5 py-1 transition-colors"
                              style={{ background: inputCcy === c ? '#FFD700' : 'transparent',
                                       color: inputCcy === c ? '#111' : '#FFD700' }}>
                        {c}
                      </button>
                    ))}
                  </div>
                )}
                <div className="text-[#FFD700] font-['Outfit'] text-2xl font-bold"
                     data-testid="open-stake-display">
                  {(inputCcy === 'USD' ? stake : stake * walletInfo.fx_rate)
                     .toLocaleString('fr-FR', { maximumFractionDigits: inputCcy === 'USD' ? 2 : 0 })} {inputCcy}
                </div>
              </div>
            </div>
            <input type="number"
                   min={inputCcy === 'USD' ? cfg.stake_min : Math.round(cfg.stake_min * walletInfo.fx_rate)}
                   max={inputCcy === 'USD' ? cfg.stake_max : Math.round(cfg.stake_max * walletInfo.fx_rate)}
                   step={inputCcy === 'USD' ? 1 : 100}
                   value={inputCcy === 'USD'
                            ? Math.round((stake + Number.EPSILON) * 100) / 100
                            : Math.round(stake * walletInfo.fx_rate)}
                   onChange={(e) => {
                     const v = Number(e.target.value || 0);
                     setStake(inputCcy === 'USD' ? v : v / (walletInfo.fx_rate || 1));
                   }}
                   className="w-full mb-3 px-3 py-2 rounded-lg text-base font-bold text-center"
                   data-testid="open-stake-input"
                   style={{ background: 'rgba(255,255,255,0.06)',
                            border: '1px solid rgba(255,215,0,0.35)',
                            color: '#FFD700', outline: 'none' }} />
            <div className="grid grid-cols-3 gap-1.5">
              {[1, 5, 20].filter(v => v >= cfg.stake_min && v <= cfg.stake_max).map(v => (
                <button key={v} onClick={() => setStake(v)}
                        data-testid={`open-stake-preset-${v}`}
                        className="py-1.5 rounded-lg text-xs font-bold"
                        style={{
                          background: stake === v ? 'rgba(255,215,0,0.18)' : 'rgba(255,255,255,0.06)',
                          color: '#fff',
                          border: `1px solid ${stake === v ? '#FFD700' : 'rgba(255,255,255,0.12)'}`,
                        }}>
                  {v} USD
                </button>
              ))}
            </div>
            <div className="mt-3 p-2.5 rounded-lg text-xs flex items-center gap-2"
                 data-testid="open-wallet-info"
                 style={{ background: insufficient ? 'rgba(224,28,46,0.10)' : 'rgba(255,255,255,0.04)',
                          border: `1px solid ${insufficient ? 'rgba(224,28,46,0.40)' : 'rgba(255,255,255,0.10)'}` }}>
              <Wallet size={14} weight="fill" style={{ color: insufficient ? '#E01C2E' : '#10B981' }} />
              <div className="flex-1 text-left">
                <span className="text-white/70">Solde : </span>
                <span className="font-bold" style={{ color: insufficient ? '#E01C2E' : '#fff' }}>
                  {walletInfo.balance_usd.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD
                </span>
                {insufficient && <span className="text-[#E01C2E] ml-2 font-bold">· insuffisant</span>}
              </div>
            </div>
            <div className="mt-3 p-3 rounded-xl text-xs"
                 style={{ background: 'rgba(255,215,0,0.08)', border: '1px solid rgba(255,215,0,0.18)' }}>
              <div className="flex items-center gap-1 text-[#FFD700] font-bold mb-1.5">
                <Coin size={14} weight="fill" /> Si tu gagnes :
              </div>
              <div className="flex justify-between text-white/80"><span>Pot total</span><span className="font-bold">{pot.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD</span></div>
              <div className="flex justify-between text-white/60"><span>Commission JAPAP ({safeCommission}%)</span><span>−{commission.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD</span></div>
              <div className="border-t border-white/10 mt-1.5 pt-1.5 flex justify-between text-white"><span className="font-bold">Tes gains nets</span><span className="font-bold text-[#10B981]">+{winnings.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD</span></div>
            </div>

            {/* iter232 — Mission 2 (Doubler la mise) */}
            <button type="button"
                    onClick={() => !cantAffordDouble && setAllowDouble(v => !v)}
                    disabled={cantAffordDouble}
                    data-testid="open-allow-double-toggle"
                    aria-pressed={effectiveAllowDouble}
                    className="mt-3 w-full p-3 rounded-xl text-left transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    style={{
                      background: effectiveAllowDouble ? 'rgba(247,147,26,0.18)' : 'rgba(255,255,255,0.05)',
                      border: `1px solid ${effectiveAllowDouble ? '#F7931A' : 'rgba(255,255,255,0.12)'}`,
                    }}>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-base">💎</span>
                  <span className="font-bold text-white text-sm">Autoriser le doublement</span>
                </div>
                <div className="w-10 h-6 rounded-full relative transition-colors"
                     style={{ background: effectiveAllowDouble ? '#F7931A' : 'rgba(255,255,255,0.15)' }}>
                  <div className="absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform"
                       style={{ transform: effectiveAllowDouble ? 'translateX(18px)' : 'translateX(2px)' }} />
                </div>
              </div>
              {cantAffordDouble ? (
                <div className="text-[10px] text-[#E01C2E] mt-1.5">
                  Solde insuffisant pour autoriser le doublement (il faut {(stake * 2).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD).
                </div>
              ) : effectiveAllowDouble ? (
                <div className="text-[10px] text-white/70 mt-1.5">
                  💰 Pot maximum : <span className="font-bold text-[#FFD700]">{potIfDoubled.toLocaleString('fr-FR')} USD</span>
                  · Gains nets si tu gagnes : <span className="font-bold text-[#10B981]">+{winningsIfDoubled.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD</span>.
                  Tes <span className="font-bold">{(stake * 2).toLocaleString('fr-FR')} USD</span> sont déjà bloqués.
                </div>
              ) : (
                <div className="text-[10px] text-white/55 mt-1.5">
                  Si activé, l'adversaire peut accepter en doublant la mise (pot total : 4× la mise initiale). Tes 2× la mise sont bloqués dès maintenant.
                </div>
              )}
            </button>
          </div>
        )}

        <button onClick={submit} disabled={busy || (mode === 'paid' && insufficient)}
                data-testid="open-submit"
                className="w-full py-4 rounded-full font-bold text-base mt-2 transition-transform active:scale-[0.97] disabled:opacity-50"
                style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)',
                         color: '#111',
                         boxShadow: '0 12px 36px rgba(255,215,0,0.55)' }}>
          {busy ? 'Création…' : '▶️ Jouer maintenant'}
        </button>

        <div className="text-white/40 text-[10px] mt-4 max-w-xs">
          Tu joueras en premier les 5 questions, puis tu pourras inviter qui tu veux à relever ton score via un lien partageable.
        </div>
      </div>
    </div>
  );
}
