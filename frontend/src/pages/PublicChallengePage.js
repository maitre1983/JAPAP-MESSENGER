/**
 * PublicChallengePage — iter228
 *
 * Public landing page for shared challenge links (/c/:cid).
 *  • Anyone can view (no auth required) — fetches /api/quiz/champion/challenge/public/:cid
 *  • If the visitor isn't logged in → CTA to login (returnTo preserved)
 *  • If logged → "Accepter et jouer" → POST /claim → redirect to play page
 *  • Score of the challenger is HIDDEN until B finishes (anti-cheat)
 */
import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import axios from 'axios';
import { Sword, CheckCircle, Wallet, Clock, X } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { useAuth } from '@/context/AuthContext';

const API = process.env.REACT_APP_BACKEND_URL;

export default function PublicChallengePage() {
  const { cid } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [ch, setCh] = useState(null);
  const [walletInfo, setWallet] = useState(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState(null);

  useEffect(() => {
    axios.get(`${API}/api/quiz/champion/challenge/public/${cid}`)
         .then(({ data }) => setCh(data))
         .catch((e) => setError(e.response?.data?.detail || 'Défi introuvable.'));
  }, [cid]);

  useEffect(() => {
    if (!user) return;
    axios.get(`${API}/api/wallet/balance`, { withCredentials: true })
         .then(({ data }) => setWallet(data))
         .catch(() => {});
  }, [user]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center text-white p-6 text-center"
           style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}>
        <X size={36} weight="fill" className="mb-3 text-[#E01C2E]" />
        <div className="font-bold text-lg mb-2">Défi indisponible</div>
        <div className="text-white/70 text-sm max-w-xs mb-5">{error}</div>
        <Link to="/games" className="px-5 py-2.5 rounded-full text-sm font-bold"
              style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
          Aller aux jeux
        </Link>
      </div>
    );
  }
  if (!ch) {
    return (
      <div className="min-h-screen flex items-center justify-center text-white"
           style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}>
        <div className="text-sm opacity-70">Chargement du défi…</div>
      </div>
    );
  }

  const stake = Number(ch.stake_amount || 0);
  const pot = stake * 2;
  const commissionPct = Number(ch.commission_pct || 10);
  const commission = (pot * commissionPct) / 100;
  const winnings = pot - commission;
  // iter232 — Mission 2 (Doubler la mise)
  const allowDouble = !!ch.allow_double;
  const potDoubled = stake * 4;
  const commissionDoubled = (potDoubled * commissionPct) / 100;
  const winningsDoubled = potDoubled - commissionDoubled;
  const expired = ch.expires_at && new Date(ch.expires_at).getTime() < now;
  const expiresIn = ch.expires_at
    ? Math.max(0, Math.floor((new Date(ch.expires_at).getTime() - now) / 1000))
    : null;
  const balanceUsd = parseFloat(walletInfo?.balance_usd || 0);
  const insufficient = ch.mode === 'paid' && user && balanceUsd < stake;
  const insufficientForDouble = ch.mode === 'paid' && user && balanceUsd < (stake * 2);
  const isPaid = ch.mode === 'paid';
  const fmt = (n, d=2) => n.toLocaleString('fr-FR', { maximumFractionDigits: d });

  const claim = async (asDouble = false) => {
    if (!user) {
      // iter237j — Use the standard /login route + state.from so
      // ProtectedRoute / LoginPage's redirectTo logic brings the user
      // back to /c/{cid} after authentication. The previous /signin
      // path didn't exist → fell through to /feed, losing the deep link.
      navigate('/login', { state: { from: `/c/${cid}` } });
      return;
    }
    if (asDouble) {
      if (insufficientForDouble) { toast.error('Solde insuffisant pour doubler.'); return; }
    } else if (insufficient) {
      toast.error('Solde insuffisant — recharge ton wallet.'); return;
    }
    setBusy(true);
    try {
      const { data } = await axios.post(`${API}/api/quiz/champion/challenge/${cid}/claim`,
                                          { double: !!asDouble },
                                          { withCredentials: true });
      toast.success(asDouble
        ? '⚡ Défi doublé ! Tu joues pour le pot ×2.'
        : 'Défi accepté ! À toi de jouer.');
      navigate(`/games/quiz/challenges/${data.challenge_id}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Impossible d'accepter le défi.");
    } finally {
      setBusy(false);
    }
  };

  // Format the countdown HHh MMm SSs.
  const cdH = expiresIn != null ? Math.floor(expiresIn / 3600) : 0;
  const cdM = expiresIn != null ? Math.floor((expiresIn % 3600) / 60) : 0;
  const cdS = expiresIn != null ? expiresIn % 60 : 0;

  return (
    <div className="min-h-screen flex flex-col text-white"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="public-challenge-page">
      <div className="flex-1 flex flex-col items-center justify-center px-6 py-10 max-w-md w-full mx-auto">
        <div className="w-20 h-20 rounded-3xl flex items-center justify-center mb-4"
             style={{ background: 'linear-gradient(135deg, #E01C2E, #B91C1C)',
                      boxShadow: '0 20px 60px rgba(224,28,46,0.5)' }}>
          <Sword size={40} weight="fill" />
        </div>
        <div className="text-center mb-6">
          <div className="text-xs uppercase tracking-wider text-white/60 font-bold mb-1">Tu es défié·e</div>
          <h1 className="font-['Outfit'] text-3xl font-extrabold"
              data-testid="public-challenge-title">
            ⚔️ {ch.challenger_name} te défie !
          </h1>
          <div className="text-white/70 text-sm mt-1">Quiz JAPAP · 5 questions</div>
        </div>

        {isPaid && (
          <div className="w-full p-4 rounded-2xl mb-3"
               data-testid="public-challenge-stake-card"
               style={{ background: 'rgba(255,215,0,0.10)', border: '1px solid rgba(255,215,0,0.30)' }}>
            <div className="text-center mb-3">
              <div className="text-[10px] uppercase tracking-wider text-white/60 font-bold mb-1">
                💰 Mise requise
              </div>
              <div className="text-[#FFD700] font-['Outfit'] text-3xl font-extrabold"
                   data-testid="public-challenge-stake-amount">
                {fmt(stake)} {ch.stake_currency || 'USD'}
              </div>
            </div>
            <div className="space-y-1 text-xs p-2.5 rounded-lg"
                 style={{ background: 'rgba(255,255,255,0.04)' }}>
              <div className="flex justify-between text-white/80"><span>🏆 Pot total</span>
                <span className="font-bold">{fmt(pot)} {ch.stake_currency}</span></div>
              <div className="flex justify-between text-white/60"><span>📊 Commission ({commissionPct}%)</span>
                <span>−{fmt(commission)} {ch.stake_currency}</span></div>
              <div className="flex justify-between text-white pt-1 border-t border-white/10">
                <span className="font-bold">✅ Tes gains si tu gagnes</span>
                <span className="font-bold text-[#10B981]"
                      data-testid="public-challenge-winnings">+{fmt(winnings)} {ch.stake_currency}</span>
              </div>
            </div>
          </div>
        )}

        {/* Countdown + status */}
        <div className="w-full grid grid-cols-2 gap-2 mb-3">
          <div className="p-3 rounded-xl text-center"
               style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}>
            <Clock size={14} weight="fill" className="inline mr-1 text-[#FFD700]" />
            <div className="text-[10px] uppercase font-bold text-white/60">Expire dans</div>
            <div className="font-bold font-mono text-sm" data-testid="public-challenge-countdown">
              {expired ? 'Expiré' : `${cdH}h ${String(cdM).padStart(2,'0')}m ${String(cdS).padStart(2,'0')}s`}
            </div>
          </div>
          <div className="p-3 rounded-xl text-center"
               style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}>
            <CheckCircle size={14} weight="fill" className="inline mr-1 text-[#10B981]" />
            <div className="text-[10px] uppercase font-bold text-white/60">{ch.challenger_name}</div>
            <div className="font-bold text-sm" data-testid="public-challenge-status">
              {ch.challenger_played ? 'A déjà joué' : 'Pas encore joué'}
            </div>
            <div className="text-[9px] text-white/40">Score : caché</div>
          </div>
        </div>

        {/* Wallet warning */}
        {user && isPaid && walletInfo && (
          <div className="w-full p-2.5 rounded-lg text-xs mb-3"
               data-testid="public-challenge-balance-info"
               style={{ background: insufficient ? 'rgba(224,28,46,0.10)' : 'rgba(16,185,129,0.08)',
                        border: `1px solid ${insufficient ? 'rgba(224,28,46,0.40)' : 'rgba(16,185,129,0.30)'}` }}>
            <Wallet size={12} weight="fill" className="inline mr-1"
                    style={{ color: insufficient ? '#E01C2E' : '#10B981' }} />
            <span className="text-white/80">Ton solde : </span>
            <span className="font-bold" style={{ color: insufficient ? '#E01C2E' : '#10B981' }}>
              {fmt(balanceUsd)} USD
            </span>
            {insufficient && (
              <span className="ml-2 text-[#FCA5A5]">· Solde insuffisant — <Link to="/wallet" className="underline">recharge</Link></span>
            )}
          </div>
        )}

        {/* CTAs */}
        {ch.is_open && !expired && (
          <div className="w-full" data-testid="public-challenge-cta-row">
            <button onClick={() => claim(false)}
                    disabled={busy || insufficient}
                    data-testid="public-challenge-accept"
                    className="w-full py-3 rounded-full font-bold text-base disabled:opacity-50 mb-2"
                    style={{ background: insufficient
                                ? 'rgba(224,28,46,0.30)'
                                : 'linear-gradient(90deg, #10B981, #059669)',
                             color: '#fff' }}>
              {!user ? 'Se connecter pour accepter'
                : insufficient ? 'Solde insuffisant'
                : busy ? 'Acceptation…'
                : `✅ Accepter et jouer ${isPaid ? `(${fmt(stake)} ${ch.stake_currency})` : ''}`}
            </button>
            {/* iter232 — Mission 2 (Doubler la mise) */}
            {allowDouble && isPaid && user && (
              <button onClick={() => claim(true)}
                      disabled={busy || insufficientForDouble}
                      data-testid="public-challenge-accept-double"
                      className="w-full py-3 rounded-full font-bold text-base disabled:opacity-50 mb-2 transition-transform active:scale-[0.97]"
                      style={{ background: insufficientForDouble
                                  ? 'rgba(224,28,46,0.30)'
                                  : 'linear-gradient(90deg, #F7931A, #FFD700)',
                               color: '#111',
                               boxShadow: insufficientForDouble ? 'none' : '0 12px 28px rgba(255,215,0,0.35)' }}>
                {insufficientForDouble
                  ? `Doubler indisponible (manque ${fmt((stake * 2) - balanceUsd)} USD)`
                  : busy ? 'Acceptation…'
                  : `💎 Doubler ×2 (${fmt(stake * 2)} ${ch.stake_currency} · gain max +${fmt(winningsDoubled)})`}
              </button>
            )}
            {allowDouble && isPaid && (
              <div className="text-[10px] text-white/55 text-center mb-1"
                   data-testid="public-challenge-double-hint">
                💡 Le créateur a autorisé le doublement — tu peux jouer pour un pot de <span className="font-bold text-[#FFD700]">{fmt(potDoubled)} {ch.stake_currency}</span>.
              </div>
            )}
          </div>
        )}
        {!ch.is_open && (
          <div className="w-full text-center text-white/60 text-sm py-3"
               data-testid="public-challenge-closed">
            Ce défi a déjà été accepté par un autre joueur.
          </div>
        )}
        <Link to="/games" className="text-white/50 text-xs underline mt-2">Retour aux jeux</Link>
      </div>
    </div>
  );
}
