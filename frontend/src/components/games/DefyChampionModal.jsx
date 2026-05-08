/**
 * DefyChampionModal — choose mode (free/paid) and stake amount before
 * creating a Quiz Champion challenge. Mobile-first sheet.
 *
 * iter225 — REWRITE: stakes are in USD (wallet canonical currency since
 * iter158/178). The user types USD, we display the equivalent in his
 * preferred display currency, and the backend records stake_currency='USD'.
 * Fixes the iter223 "1000 XAF on screen, 1000 USD debited" deception.
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { X, Sword, Coin, Info, Wallet } from '@phosphor-icons/react';
import { toast } from 'sonner';
import * as WS from '../../utils/walletSecurity';

const API = process.env.REACT_APP_BACKEND_URL;

export default function DefyChampionModal({ open, onClose, champion, onCreated }) {
  const [cfg, setCfg] = useState({
    paid_enabled: false,
    commission_pct: 10,
    stake_min: 1,
    stake_max: 200,
    expiry_hours: 24,
  });
  const [mode, setMode]   = useState('free');
  const [stake, setStake] = useState(1);    // USD canonical (always)
  const [busy, setBusy]   = useState(false);
  // iter225 — Real wallet snapshot (USD canonical) + display currency from user pref.
  const [wallet, setWallet] = useState({ balance_usd: 0, display_amount: 0,
                                          display_currency: 'USD', fx_rate: 1 });
  // iter226 — Toggle d'affichage USD ↔ devise locale. La saisie reste convertie
  // en USD canonical (stocké dans `stake`) avant toute validation/POST.
  const [inputCcy, setInputCcy] = useState('USD');

  useEffect(() => {
    if (!open) return;
    (async () => {
      try {
        const { data } = await axios.get(`${API}/api/games/toggles`);
        setCfg(prev => ({
          ...prev,
          paid_enabled:    Boolean(data.quiz_challenge_paid_enabled ?? prev.paid_enabled),
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
          balance_usd:     parseFloat(w.balance_usd || 0),
          display_amount:  parseFloat(w.display_amount || 0),
          display_currency: w.display_currency || 'USD',
          fx_rate:         parseFloat(w.fx_rate || 1),
        });
        // iter226 — Default the input toggle to the user's preferred display
        // currency so francophone players see "1 200 XAF" by default and can
        // switch to USD with one tap.
        if (w.display_currency && w.display_currency !== 'USD') {
          setInputCcy(w.display_currency);
        }
      } catch {}
    })();
  }, [open]);

  // iter226 — local preview is now derived inline in the JSX from inputCcy/fx_rate.

  if (!open) return null;

  // iter223 — Borner la commission affichée entre 0 et 50 (jamais faire confiance aux config en mémoire).
  const safeCommissionPct = Math.max(0, Math.min(50, Number(cfg.commission_pct) || 0));
  const pot = stake * 2;
  const commission = (pot * safeCommissionPct) / 100;
  const winnings = pot - commission;

  // iter225 — Insufficient balance gating (USD canonical).
  const insufficient = mode === 'paid' && stake > wallet.balance_usd;

  const submit = async () => {
    // iter225 — Guard sécurité avant submit + balance check.
    if (mode === 'paid') {
      const check = WS.validateAmount(stake, cfg.stake_min, cfg.stake_max);
      if (!check.valid) {
        toast.error(check.reason);
        return;
      }
      if (insufficient) {
        toast.error('Solde insuffisant — recharge ton wallet.');
        return;
      }
    }
    setBusy(true);
    try {
      const body = { country_code: champion.country_code, mode };
      if (mode === 'paid') body.stake_amount = Number(stake);
      const { data } = await axios.post(`${API}/api/quiz/champion/challenge`,
        body, { withCredentials: true });
      onCreated && onCreated(data.challenge_id);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Impossible de créer le défi');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center"
         style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(8px)' }}
         onClick={onClose}
         data-testid="defy-modal">
      <div className="w-full sm:max-w-md rounded-t-3xl sm:rounded-2xl p-5"
           style={{ background: 'linear-gradient(160deg, #1c0b8a, #0F056B)',
                    border: '1px solid rgba(255,255,255,0.15)' }}
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Sword size={20} weight="fill" color="#E01C2E" />
            <div className="text-white font-['Outfit'] font-bold text-lg">Défier le champion</div>
          </div>
          <button onClick={onClose} className="p-1 rounded-full text-white/70"
                  data-testid="defy-modal-close">
            <X size={18} />
          </button>
        </div>

        <div className="text-white/70 text-xs mb-4">
          Vous défiez <span className="text-[#FFD700] font-bold">{champion.user.first_name || champion.user.username}</span> · 5 questions identiques · le meilleur score gagne. Expire dans {cfg.expiry_hours}h.
        </div>

        {/* Mode toggle */}
        <div className="grid grid-cols-2 gap-2 mb-4">
          <button onClick={() => setMode('free')}
                  data-testid="defy-mode-free"
                  className="py-3 rounded-xl font-bold text-sm transition-all active:scale-[0.98]"
                  style={{
                    background: mode === 'free' ? 'linear-gradient(90deg, #10B981, #059669)' : 'rgba(255,255,255,0.06)',
                    color: '#fff',
                    border: `1px solid ${mode === 'free' ? '#10B981' : 'rgba(255,255,255,0.12)'}`,
                  }}>
            🎯 Gratuit
            <div className="text-[10px] opacity-70 font-normal mt-0.5">Pour la gloire</div>
          </button>
          <button onClick={() => cfg.paid_enabled ? setMode('paid') : toast.error('Mode payant désactivé')}
                  data-testid="defy-mode-paid"
                  disabled={!cfg.paid_enabled}
                  className="py-3 rounded-xl font-bold text-sm transition-all active:scale-[0.98] disabled:opacity-40"
                  style={{
                    background: mode === 'paid' ? 'linear-gradient(90deg, #FFD700, #F7931A)' : 'rgba(255,255,255,0.06)',
                    color: mode === 'paid' ? '#111' : '#fff',
                    border: `1px solid ${mode === 'paid' ? '#FFD700' : 'rgba(255,255,255,0.12)'}`,
                  }}>
            💰 Payant
            <div className="text-[10px] opacity-70 font-normal mt-0.5">{cfg.paid_enabled ? 'Gagnez le pot' : 'Désactivé'}</div>
          </button>
        </div>

        {/* Stake slider + free input */}
        {mode === 'paid' && (
          <div className="mb-5">
            <div className="flex items-center justify-between mb-2">
              <div className="text-white text-xs uppercase font-bold tracking-wider">
                Mise ({inputCcy})
              </div>
              <div className="flex items-center gap-2">
                {/* iter226 — Toggle USD ↔ display currency (visible si wallet has FX). */}
                {wallet.display_currency !== 'USD' && WS.isValidRate(wallet.fx_rate) && (
                  <div className="flex rounded-full overflow-hidden text-[10px] font-bold"
                       data-testid="defy-ccy-toggle"
                       style={{ border: '1px solid rgba(255,215,0,0.35)' }}>
                    {['USD', wallet.display_currency].map(c => (
                      <button key={c}
                              onClick={() => setInputCcy(c)}
                              data-testid={`defy-ccy-${c}`}
                              className="px-2.5 py-1 transition-colors"
                              style={{
                                background: inputCcy === c ? '#FFD700' : 'transparent',
                                color:      inputCcy === c ? '#111'    : '#FFD700',
                              }}>
                        {c}
                      </button>
                    ))}
                  </div>
                )}
                <div className="text-[#FFD700] font-['Outfit'] text-2xl font-bold"
                     data-testid="defy-stake-display">
                  {(inputCcy === 'USD'
                      ? stake
                      : stake * wallet.fx_rate
                   ).toLocaleString('fr-FR', { maximumFractionDigits: inputCcy === 'USD' ? 2 : 0 })} {inputCcy}
                </div>
              </div>
            </div>
            {/* Equivalent line — always shows the *other* currency when FX is available. */}
            {wallet.display_currency !== 'USD' && WS.isValidRate(wallet.fx_rate) && (
              <div className="text-white/50 text-[11px] -mt-1 mb-2 text-right" data-testid="defy-stake-local">
                {inputCcy === 'USD'
                  ? `≈ ${(stake * wallet.fx_rate).toLocaleString('fr-FR', { maximumFractionDigits: 0 })} ${wallet.display_currency}`
                  : `≈ ${stake.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD`}
              </div>
            )}
            {/* iter225 — Free numeric input. iter226: input value reflects the chosen currency. */}
            <input type="number"
                   min={inputCcy === 'USD' ? cfg.stake_min : Math.round(cfg.stake_min * wallet.fx_rate)}
                   max={inputCcy === 'USD' ? cfg.stake_max : Math.round(cfg.stake_max * wallet.fx_rate)}
                   step={inputCcy === 'USD' ? 1 : 100}
                   value={inputCcy === 'USD'
                            ? Math.round((stake + Number.EPSILON) * 100) / 100
                            : Math.round(stake * wallet.fx_rate)}
                   onChange={(e) => {
                     const v = Number(e.target.value || 0);
                     setStake(inputCcy === 'USD' ? v : v / (wallet.fx_rate || 1));
                   }}
                   placeholder={`Min ${cfg.stake_min} · Max ${cfg.stake_max.toLocaleString('fr-FR')} USD`}
                   className="w-full mb-3 px-3 py-2 rounded-lg text-base font-bold text-center"
                   data-testid="defy-stake-input"
                   style={{ background: 'rgba(255,255,255,0.06)',
                            border: '1px solid rgba(255,215,0,0.35)',
                            color: '#FFD700',
                            outline: 'none' }} />
            <input type="range" min={cfg.stake_min} max={cfg.stake_max}
                   value={Math.min(Math.max(stake, cfg.stake_min), cfg.stake_max)}
                   step={Math.max(1, Math.round((cfg.stake_max - cfg.stake_min) / 100))}
                   onChange={(e) => setStake(Number(e.target.value))}
                   className="w-full"
                   data-testid="defy-stake-slider"
                   style={{ accentColor: '#FFD700' }} />
            <div className="flex justify-between text-white/50 text-[10px] mt-1">
              <span>Min : {cfg.stake_min} USD</span>
              <span>Max : {cfg.stake_max.toLocaleString('fr-FR')} USD</span>
            </div>
            <div className="grid grid-cols-3 gap-1.5 mt-3">
              {[1, 5, 20].filter(v => v >= cfg.stake_min && v <= cfg.stake_max).map(v => (
                <button key={v} onClick={() => setStake(v)}
                        data-testid={`defy-stake-preset-${v}`}
                        className="py-1.5 rounded-lg text-xs font-bold transition-all active:scale-95"
                        style={{
                          background: stake === v ? 'rgba(255,215,0,0.18)' : 'rgba(255,255,255,0.06)',
                          color: '#fff',
                          border: `1px solid ${stake === v ? '#FFD700' : 'rgba(255,255,255,0.12)'}`,
                        }}>
                  {v} USD
                </button>
              ))}
            </div>
            {/* iter225 — Wallet balance + insufficient warning */}
            <div className="mt-3 p-2.5 rounded-lg text-xs flex items-center gap-2"
                 data-testid="defy-wallet-info"
                 style={{ background: insufficient ? 'rgba(224,28,46,0.10)' : 'rgba(255,255,255,0.04)',
                          border: `1px solid ${insufficient ? 'rgba(224,28,46,0.40)' : 'rgba(255,255,255,0.10)'}` }}>
              <Wallet size={14} weight="fill"
                      style={{ color: insufficient ? '#E01C2E' : '#10B981' }} />
              <div className="flex-1">
                <span className="text-white/70">Solde : </span>
                <span className="font-bold" style={{ color: insufficient ? '#E01C2E' : '#fff' }}>
                  {wallet.balance_usd.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD
                </span>
                {insufficient && (
                  <span className="text-[#E01C2E] ml-2 font-bold" data-testid="defy-insufficient-warning">
                    · Solde insuffisant
                  </span>
                )}
              </div>
            </div>
            {/* Pot + commission breakdown */}
            <div className="mt-4 p-3 rounded-xl text-xs"
                 style={{ background: 'rgba(255,215,0,0.08)', border: '1px solid rgba(255,215,0,0.18)' }}>
              <div className="flex items-center gap-1 text-[#FFD700] font-bold mb-1.5">
                <Coin size={14} weight="fill" /> Si vous gagnez :
              </div>
              <div className="flex justify-between text-white/80"><span>Pot total</span><span className="font-bold">{pot.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD</span></div>
              <div className="flex justify-between text-white/60"><span>Commission JAPAP ({safeCommissionPct}%)</span><span>−{commission.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD</span></div>
              <div className="border-t border-white/10 mt-1.5 pt-1.5 flex justify-between text-white"><span className="font-bold">Vos gains nets</span><span className="font-bold text-[#10B981]">+{winnings.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD</span></div>
            </div>
            <div className="text-white/50 text-[10px] mt-2 flex items-start gap-1">
              <Info size={10} className="mt-0.5 shrink-0" />
              <span>Si le champion refuse ou n'accepte pas dans {cfg.expiry_hours}h, votre mise est intégralement remboursée et vous recevez un bonus engagement.</span>
            </div>
          </div>
        )}

        <button onClick={submit} disabled={busy || (mode === 'paid' && insufficient)}
                data-testid="defy-submit"
                className="w-full py-3 rounded-full font-bold text-base active:scale-[0.97] disabled:opacity-50"
                style={{ background: 'linear-gradient(90deg, #E01C2E, #B91C1C)', color: '#fff',
                         boxShadow: '0 12px 36px rgba(224,28,46,0.55)' }}>
          {busy ? 'Envoi…' : `Lancer le défi ${mode === 'paid' ? `(${stake.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD)` : '(gratuit)'}`}
        </button>
      </div>
    </div>
  );
}
