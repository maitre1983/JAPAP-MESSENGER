/**
 * WheelFortunePage — iter83 engagement engine
 *
 * Full flow:
 *   GET  /api/wheel/status        → cycle state, rules, slots
 *   POST /api/wheel/spin          → backend-controlled prize_slot
 *   POST /api/wheel/claim-reward  → unlock Starter Pro
 *   GET  /api/wheel/history       → last 20 spins
 *
 * Integrates Cloudflare Turnstile when wheel_turnstile_enabled=true.
 * All "randomness" is fake on the client — the wheel always lands on the
 * slot the backend decides.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  ArrowLeft, Trophy, Flame, CalendarBlank, Sparkle, ShieldCheck, Lightning,
  Share, X,
} from '@phosphor-icons/react';
import WheelFortuneCasino from '@/components/games/WheelFortuneCasino';
import { useAuth } from '@/context/AuthContext';

const API = process.env.REACT_APP_BACKEND_URL;

// tiny stable fingerprint (non-PII) to send to the backend — same as the
// backend will compute server-side, this is just a hint so the anti-bot
// logic can cross-check.
function localDeviceHash() {
  try {
    const raw = [
      navigator.userAgent, navigator.language, navigator.platform,
      screen.width + 'x' + screen.height,
      new Date().getTimezoneOffset(),
    ].join('|');
    let h = 0;
    for (let i = 0; i < raw.length; i++) {
      h = (h << 5) - h + raw.charCodeAt(i);
      h |= 0;
    }
    return 'fp_' + Math.abs(h).toString(16);
  } catch { return 'fp_unknown'; }
}

export default function WheelFortunePage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [status, setStatus] = useState(null);
  const [isSpinning, setIsSpinning] = useState(false);
  const [pendingResult, setPendingResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [turnstileToken, setTurnstileToken] = useState('');
  const [celebrateMilestone, setCelebrateMilestone] = useState(null);
  const [jackpotWin, setJackpotWin] = useState(null);       // iter83 viral modal
  const turnstileRef = useRef(null);
  const navigate = useNavigate();

  // ── Haptic + micro-audio helpers ─────────────────────────────────────
  const vibrate = (pattern) => {
    try { if (navigator.vibrate) navigator.vibrate(pattern); } catch (_) { /* noop */ }
  };
  const playTone = (freqs = [440], duration = 0.12) => {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const now = ctx.currentTime;
      freqs.forEach((f, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = f;
        gain.gain.setValueAtTime(0.18, now + i * duration);
        gain.gain.exponentialRampToValueAtTime(0.001, now + (i + 1) * duration);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now + i * duration);
        osc.stop(now + (i + 1) * duration);
      });
    } catch (_) { /* noop */ }
  };

  const load = useCallback(async () => {
    try {
      const [s, h] = await Promise.all([
        axios.get(`${API}/api/wheel/status`, { withCredentials: true }),
        axios.get(`${API}/api/wheel/history?limit=20`, { withCredentials: true }),
      ]);
      setStatus(s.data);
      setHistory(h.data.history || []);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Load Turnstile script when needed
  useEffect(() => {
    if (!status?.rules?.turnstile_enabled || !status?.rules?.turnstile_site_key) return;
    if (window.turnstile) return;
    const s = document.createElement('script');
    s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
    s.async = true; s.defer = true;
    document.head.appendChild(s);
  }, [status?.rules?.turnstile_enabled, status?.rules?.turnstile_site_key]);

  useEffect(() => {
    if (!status?.rules?.turnstile_enabled) return;
    const key = status.rules.turnstile_site_key;
    if (!key) return;
    const tick = setInterval(() => {
      if (window.turnstile && turnstileRef.current && !turnstileRef.current.dataset.rendered) {
        window.turnstile.render(turnstileRef.current, {
          sitekey: key,
          callback: (t) => setTurnstileToken(t),
          'expired-callback': () => setTurnstileToken(''),
        });
        turnstileRef.current.dataset.rendered = '1';
        clearInterval(tick);
      }
    }, 200);
    return () => clearInterval(tick);
  }, [status?.rules?.turnstile_enabled, status?.rules?.turnstile_site_key]);

  const spin = async () => {
    if (isSpinning) return;
    setPendingResult(null);
    setIsSpinning(true);
    try {
      const { data } = await axios.post(`${API}/api/wheel/spin`, {
        turnstile_token: turnstileToken || null,
        device_fingerprint: localDeviceHash(),
      }, { withCredentials: true });
      setPendingResult(data);
    } catch (e) {
      setIsSpinning(false);
      toast.error(e.response?.data?.detail || 'Erreur');
      load();
    }
  };

  const onSpinEnd = async () => {
    if (pendingResult) {
      // iter113 — Visual/logic coherence: backend now guarantees that when
      // points_awarded === 0, the wheel landed on the "Perdu" slot. So the
      // toast and the wheel always agree.
      if (pendingResult.jackpot) {
        toast.success(`🎰 JACKPOT ! +${pendingResult.points_awarded} points`);
        vibrate([100, 50, 100, 50, 300]);
        playTone([523, 659, 784, 1046], 0.15);   // C-E-G-C arpeggio
        setJackpotWin({
          points: pendingResult.points_awarded,
          newTotal: pendingResult.new_total_points,
          daysPlayed: c.days_played_count,
          daysGoal: p.days_goal,
          daysToGo: Math.max(0, p.days_goal - c.days_played_count),
        });
      } else if (pendingResult.points_awarded > 0) {
        toast.success(`+${pendingResult.points_awarded} points`);
        vibrate(40);
        playTone([659], 0.1);
      } else if (pendingResult.near_miss) {
        toast.info("So close! La prochaine est la bonne !");
        vibrate([200, 80, 80]);
        playTone([440, 330], 0.18);
      } else {
        toast.info("Pas cette fois, retentez demain !");
      }
      // Milestone crossing (5k / 8k / 10k)
      const crossed = pendingResult.crossed_milestones || [];
      if (crossed.length > 0) {
        const top = Math.max(...crossed);
        setCelebrateMilestone(top);
        vibrate([80, 40, 80, 40, 200]);
        playTone([523, 659, 784], 0.18);
        setTimeout(() => setCelebrateMilestone(null), 3200);
      }
    }
    setIsSpinning(false);
    await load();
  };

  const claim = async () => {
    try {
      const { data } = await axios.post(
        `${API}/api/wheel/claim-reward`, {},
        { withCredentials: true },
      );
      toast.success(data.message || "Récompense attribuée !");
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Erreur");
    }
  };

  if (!status) {
    return (
      <div className="min-h-screen flex items-center justify-center"
           style={{ background: 'var(--jp-bg)' }}>
        <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>
      </div>
    );
  }

  const c = status.cycle;
  const p = status.progress;
  const r = status.rules;
  const cooldownActive = r.cooldown_remaining > 0;
  const dailyCapped = r.plays_today >= r.max_spins_per_day;
  const canSpin = !isSpinning && !cooldownActive && !dailyCapped &&
                  (!r.turnstile_enabled || turnstileToken);

  return (
    <div className="min-h-screen pb-16"
         style={{
           background:
             'radial-gradient(circle at 50% 20%, #1a1540 0%, #0a0718 60%, #000 100%)',
           color: '#fff',
         }}
         data-testid="wheel-fortune-page">
      <header className="px-4 py-4 flex items-center gap-3">
        <button onClick={() => navigate(-1)} className="jp-btn jp-btn-ghost"
                data-testid="wheel-back"
                style={{ color: '#fff' }}>
          <ArrowLeft size={18} />
        </button>
        <div className="flex-1">
          <h1 className="font-['Outfit'] text-xl font-bold flex items-center gap-2">
            <Trophy size={20} weight="duotone" color="#FFD700" />
            Roue de la Fortune
          </h1>
          <p className="text-[11px] opacity-70">
            10 000 pts + 25 jours → Pack Starter Pro 30 jours
          </p>
        </div>
      </header>

      {/* Cycle progress — dynamic motivational block (iter83 P2) */}
      <ProgressBlock cycle={c} progress={p} />

      {/* iter114 — Wheel Boost Event banner (admin-piloted retention spike) */}
      {status.boost?.active && <BoostBanner boost={status.boost} />}

      {/* P1 — pending_claim banner (reward won on a past cycle, still in the grace window) */}
      {status.pending_claim && (
        <PendingClaimBanner pending={status.pending_claim} onClaim={claim} />
      )}

      {p.can_claim && (
        <div className="mx-4 -mt-2 mb-4">
          <button onClick={claim} data-testid="wheel-claim-btn"
                  className="w-full py-3 rounded-xl font-bold text-base jp-animate-fadeIn"
                  style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111',
                           boxShadow: '0 6px 24px rgba(255,215,0,0.5)' }}>
            🏆 Réclamer mon Pack Starter Pro
          </button>
        </div>
      )}

      {/* The wheel */}
      <div className="flex flex-col items-center gap-4 px-4">
        <WheelFortuneCasino
          slots={status.wheel_slots}
          isSpinning={isSpinning}
          landingSlot={pendingResult?.prize_slot ?? null}
          nearMiss={Boolean(pendingResult?.near_miss)}
          boostActive={Boolean(status.boost?.active)}
          onSpinEnd={onSpinEnd}
        />

        {r.turnstile_enabled && (
          <div ref={turnstileRef} className="min-h-[65px]" data-testid="turnstile-widget" />
        )}

        <button onClick={spin} disabled={!canSpin}
                data-testid="wheel-spin-btn"
                className="px-8 py-3 rounded-full font-bold text-lg tracking-wide transition-all"
                style={{
                  background: canSpin
                    ? 'linear-gradient(90deg, #E01C2E, #FFD700)'
                    : 'rgba(255,255,255,0.1)',
                  color: canSpin ? '#111' : '#999',
                  boxShadow: canSpin ? '0 6px 20px rgba(224,28,46,0.5)' : 'none',
                  cursor: canSpin ? 'pointer' : 'not-allowed',
                }}>
          <Lightning size={18} weight="fill" className="inline mr-1" />
          {isSpinning ? t('wheel_fortune.ca_tourne') :
           cooldownActive ? `Patientez ${r.cooldown_remaining}s` :
           dailyCapped ? `Revenez demain (${r.plays_today}/${r.max_spins_per_day})` :
           'Faire tourner'}
        </button>

        <div className="text-[11px] opacity-60 text-center">
          Tours aujourd'hui : {r.plays_today}/{r.max_spins_per_day} · Cooldown {r.cooldown_seconds}s
        </div>
      </div>

      {/* Streak bonus guide */}
      <section className="mx-4 mt-6 p-4 rounded-xl text-xs"
               style={{ background: 'rgba(255,255,255,0.04)' }}>
        <div className="font-bold mb-2 flex items-center gap-2">
          <Flame size={14} color="#FF6B35" /> Bonus fidélité
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className={`p-2 rounded-lg ${c.streak_days >= 3 ? 'opacity-100' : 'opacity-50'}`}
               style={{ background: c.streak_days >= 3 ? 'rgba(255,215,0,0.15)' : 'rgba(255,255,255,0.05)' }}>
            <div className="font-bold">3 jours</div>
            <div className="opacity-80">+50 pts</div>
          </div>
          <div className={`p-2 rounded-lg ${c.streak_days >= 7 ? 'opacity-100' : 'opacity-50'}`}
               style={{ background: c.streak_days >= 7 ? 'rgba(255,215,0,0.15)' : 'rgba(255,255,255,0.05)' }}>
            <div className="font-bold">7 jours</div>
            <div className="opacity-80">+150 pts</div>
          </div>
          <div className={`p-2 rounded-lg ${c.streak_days >= 15 ? 'opacity-100' : 'opacity-50'}`}
               style={{ background: c.streak_days >= 15 ? 'rgba(255,215,0,0.15)' : 'rgba(255,255,255,0.05)' }}>
            <div className="font-bold">15 jours</div>
            <div className="opacity-80">+400 pts</div>
          </div>
        </div>
      </section>

      {/* History */}
      {history.length > 0 && (
        <section className="mx-4 mt-6 p-4 rounded-xl"
                 style={{ background: 'rgba(255,255,255,0.04)' }}
                 data-testid="wheel-history">
          <div className="font-bold mb-2 text-xs opacity-80">Historique récent</div>
          <ul className="space-y-1 text-xs">
            {history.slice(0, 8).map((h) => (
              <li key={h.id} className="flex items-center justify-between">
                <span className="opacity-70">
                  {new Date(h.spin_at).toLocaleDateString('fr-FR')} · Phase {h.phase}
                  {h.jackpot && ' 🎰'}
                  {h.near_miss && ' 😬'}
                  {h.streak_bonus > 0 && ` (streak +${h.streak_bonus})`}
                </span>
                <span className={h.points_awarded > 0 ? 'text-amber-300 font-bold' : 'opacity-60'}>
                  {h.points_awarded > 0 ? `+${h.points_awarded}` : '—'}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Milestone celebration overlay */}
      {celebrateMilestone && <MilestoneOverlay threshold={celebrateMilestone} />}
      {jackpotWin && <JackpotCelebrationModal win={jackpotWin} onClose={() => setJackpotWin(null)} user={user} />}
    </div>
  );
}


/* ────────────────────────── Progress block ─────────────────────────── */

function ProgressBlock({ cycle, progress }) {
  const { t } = useTranslation();
  const c = cycle; const p = progress;
  const urgencyColor = {
    critical: '#E01C2E',
    high: '#F7931A',
    medium: '#FFD700',
    low: '#10B981',
  }[p.urgency_level] || '#FFD700';

  const daysLeft = c.remaining_days_in_cycle;
  return (
    <section className="mx-4 p-4 rounded-2xl mb-4"
             style={{ background: 'rgba(255,255,255,0.05)',
                      border: `1px solid ${urgencyColor}55` }}
             data-testid="wheel-progress">

      {/* Dynamic motivational sentence */}
      <div className="text-sm font-bold mb-3 leading-snug"
           style={{ color: p.can_claim ? '#FFD700' : '#fff' }}
           data-testid="wheel-progression-msg">
        {p.progression_message}
      </div>

      {/* Countdown */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full"
             style={{ background: `${urgencyColor}22`, border: `1px solid ${urgencyColor}66` }}
             data-testid="wheel-countdown">
          <CalendarBlank size={14} color={urgencyColor} weight="duotone" />
          <span className="text-xs font-semibold" style={{ color: urgencyColor }}>
            {daysLeft > 0
              ? `Il vous reste ${daysLeft} jour${daysLeft > 1 ? 's' : ''}`
              : t('wheel_fortune.cycle_termine')}
          </span>
        </div>
        <span className="flex items-center gap-1 text-[11px] opacity-80">
          <Sparkle size={12} /> Phase {p.phase}/3
        </span>
      </div>

      {/* Points counter */}
      <div className="mb-3" data-testid="wheel-points-counter">
        <div className="flex items-center justify-between text-xs mb-1">
          <span className="font-semibold">🎯 Points</span>
          <span><strong>{c.points_cycle.toLocaleString('fr-FR')}</strong> / {p.points_goal.toLocaleString('fr-FR')}</span>
        </div>
        <div className="h-3 rounded-full overflow-hidden relative"
             style={{ background: 'rgba(255,255,255,0.08)' }}>
          {/* Milestone markers at 5k / 8k */}
          <div className="absolute top-0 bottom-0 w-[1.5px] z-10"
               style={{ left: '50%', background: 'rgba(255,255,255,0.5)' }} />
          <div className="absolute top-0 bottom-0 w-[1.5px] z-10"
               style={{ left: '80%', background: 'rgba(255,215,0,0.7)' }} />
          <div className="h-full transition-all duration-700 relative"
               style={{ width: `${p.points_percent}%`,
                        background: 'linear-gradient(90deg, #FFD700, #E01C2E)',
                        boxShadow: '0 0 12px rgba(255,215,0,0.6)' }} />
        </div>
        <div className="flex justify-between text-[9px] opacity-50 mt-0.5 px-0.5">
          <span>0</span><span>5k</span><span>8k</span><span>10k</span>
        </div>
      </div>

      {/* Days counter */}
      <div className="mb-3" data-testid="wheel-days-counter">
        <div className="flex items-center justify-between text-xs mb-1">
          <span className="font-semibold">📅 Jours joués</span>
          <span><strong>{c.days_played_count}</strong> / {p.days_goal}</span>
        </div>
        <div className="h-3 rounded-full overflow-hidden"
             style={{ background: 'rgba(255,255,255,0.08)' }}>
          <div className="h-full transition-all duration-700"
               style={{ width: `${p.days_percent}%`,
                        background: 'linear-gradient(90deg, #10B981, #06B6D4)',
                        boxShadow: '0 0 12px rgba(16,185,129,0.5)' }} />
        </div>
      </div>

      {/* iter83 — Quiz performance (3rd condition) */}
      <div className="mb-3" data-testid="wheel-quiz-performance">
        <div className="flex items-center justify-between text-xs mb-1">
          <span className="font-semibold">🧠 Performance Quiz</span>
          <span>
            <strong style={{ color: p.quiz_performance_met ? '#A78BFA' : undefined }}>
              {p.quiz_accuracy_percent || 0}%
            </strong> / objectif {Math.round((p.quiz_accuracy_goal || 0.75) * 100)}%
          </span>
        </div>
        <div className="h-3 rounded-full overflow-hidden"
             style={{ background: 'rgba(255,255,255,0.08)' }}>
          <div className="h-full transition-all duration-700"
               style={{
                 width: `${p.quiz_accuracy_percent || 0}%`,
                 background: 'linear-gradient(90deg, #8B5CF6, #A78BFA)',
                 boxShadow: '0 0 12px rgba(139,92,246,0.5)',
               }} />
        </div>
        <div className="flex justify-between text-[10px] opacity-75 mt-1">
          <span>
            Réponses validées :
            <strong> {p.quiz_answers_total || 0}</strong> / {p.quiz_answers_needed || 50}
          </span>
          {(p.quiz_answers_remaining || 0) > 0 && (
            <span style={{ color: '#A78BFA' }}>
              Encore {p.quiz_answers_remaining} réponse{p.quiz_answers_remaining > 1 ? 's' : ''}
            </span>
          )}
          {(p.quiz_answers_remaining || 0) === 0 && !p.quiz_performance_met && (
            <span style={{ color: '#F7931A' }}>{t('wheel_fortune.montez_a_75')}</span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3 text-[11px] opacity-80 flex-wrap">
        <span className="flex items-center gap-1">
          <Flame size={12} color="#FF6B35" weight="fill" /> Streak {c.streak_days}j
        </span>
        {c.suspicious_flag && (
          <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-amber-300"
                style={{ background: 'rgba(245,158,11,0.15)' }}>
            <ShieldCheck size={12} /> Compte flaggé
          </span>
        )}
      </div>
    </section>
  );
}


/* ─────────────────────── Milestone celebration overlay ────────────────── */

function BoostBanner({ boost }) {
  // iter114 — Live countdown to boost end + pulsating gold accent.
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const endsAt = boost.ends_at ? new Date(boost.ends_at).getTime() : null;
  const remainMs = endsAt ? Math.max(0, endsAt - now) : null;
  const fmt = (ms) => {
    if (ms === null) return null;
    const s = Math.floor(ms / 1000);
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d > 0) return `${d}j ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m ${s % 60}s`;
  };
  const mult = Number(boost.gain_multiplier || 1);
  const perduRed = Number(boost.perdu_reduction_percent || 0);
  const jackpotOpen = Boolean(boost.unlock_jackpot_all_phases);

  const tags = [];
  if (mult > 1) tags.push(`×${mult.toFixed(2).replace(/\.00$/, '')} gains`);
  if (perduRed > 0) tags.push(`-${perduRed}% perdus`);
  if (jackpotOpen) tags.push('Jackpot ouvert');

  return (
    <section className="mx-4 mb-4 rounded-2xl overflow-hidden jp-animate-fadeIn"
             data-testid="wheel-boost-banner"
             style={{
               background: 'linear-gradient(110deg, #FFD700 0%, #F7931A 45%, #E01C2E 100%)',
               boxShadow: '0 14px 40px rgba(255,215,0,0.55)',
               animation: 'wheelBoostPulse 2.4s ease-in-out infinite',
             }}>
      <div className="px-4 py-3 text-[#111]">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 w-11 h-11 rounded-full flex items-center justify-center text-xl"
               style={{ background: 'rgba(0,0,0,0.18)' }}>
            🎉
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[10px] uppercase tracking-widest font-bold opacity-80">
              Événement spécial en cours
            </div>
            <div className="font-['Outfit'] text-base font-extrabold leading-tight"
                 data-testid="wheel-boost-name">
              {boost.name || 'Wheel Boost Event'}
            </div>
            <div className="flex flex-wrap gap-1.5 mt-1.5">
              {tags.map((t) => (
                <span key={t}
                      className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                      style={{ background: 'rgba(0,0,0,0.18)', color: '#FFD700' }}>
                  {t}
                </span>
              ))}
            </div>
            {remainMs !== null && (
              <div className="text-[11px] opacity-85 mt-1.5 font-semibold"
                   data-testid="wheel-boost-countdown">
                Se termine dans <strong>{fmt(remainMs)}</strong>
              </div>
            )}
          </div>
        </div>
      </div>
      <style>{`
        @keyframes wheelBoostPulse {
          0%, 100% { box-shadow: 0 14px 40px rgba(255,215,0,0.55); }
          50%      { box-shadow: 0 14px 60px rgba(255,215,0,0.85); }
        }
      `}</style>
    </section>
  );
}


function PendingClaimBanner({ pending, onClaim }) {
  const dl = pending.claim_days_remaining;
  const critical = dl <= 2;
  return (
    <section
      className="mx-4 -mt-2 mb-4 rounded-2xl overflow-hidden jp-animate-fadeIn"
      style={{
        background: critical
          ? 'linear-gradient(135deg, #E01C2E, #F7931A)'
          : 'linear-gradient(135deg, #FFD700, #F7931A)',
        boxShadow: '0 10px 36px rgba(255,215,0,0.35)',
      }}
      data-testid="wheel-pending-claim-banner"
    >
      <div className="p-4 text-[#111]">
        <div className="flex items-start gap-3">
          <div
            className="flex-shrink-0 w-12 h-12 rounded-full flex items-center justify-center text-2xl"
            style={{ background: 'rgba(255,255,255,0.25)' }}
          >
            🏆
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-['Outfit'] text-base font-bold leading-tight">
              Vous avez gagné votre Starter Pro !
            </div>
            <div className="text-xs mt-1 opacity-90 leading-snug">
              Cycle terminé avec {pending.points_cycle.toLocaleString('fr-FR')} pts et {pending.days_played_count} jours joués.
              {critical
                ? ` Il ne vous reste que ${dl} jour${dl > 1 ? 's' : ''} pour la réclamer.`
                : ` Réclamez-la avant d'en perdre le bénéfice (${dl} jours restants).`}
            </div>
          </div>
        </div>
        <button
          onClick={onClaim}
          data-testid="wheel-pending-claim-btn"
          className="w-full mt-3 py-2.5 rounded-xl font-bold text-sm transition-all active:scale-[0.98]"
          style={{
            background: '#111',
            color: '#FFD700',
            boxShadow: '0 6px 20px rgba(0,0,0,0.25)',
          }}
        >
          Réclamer maintenant →
        </button>
      </div>
    </section>
  );
}

function MilestoneOverlay({ threshold }) {
  const { t } = useTranslation();
  const msg = {
    5000: { title: "🔥 5 000 points atteints !",
            sub: t('wheel_fortune.vous_etes_a_mi_chemin_continuez_la') },
    8000: { title: "💎 8 000 points — zone JACKPOT !",
            sub: t('wheel_fortune.le_jackpot_peut_desormais_tomber_a') },
    10000: { title: "🏆 10 000 POINTS !",
             sub: t('wheel_fortune.vous_avez_atteint_l_objectif_points') },
  }[threshold] || { title: `${threshold} points !`, sub: "" };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center pointer-events-none jp-animate-fadeIn"
         style={{ background: 'radial-gradient(circle at center, rgba(0,0,0,0.7) 0%, rgba(0,0,0,0.9) 100%)' }}
         data-testid="milestone-overlay">
      <div className="mx-6 text-center"
           style={{
             animation: 'milestone-pop 0.6s cubic-bezier(0.34, 1.56, 0.64, 1)',
           }}>
        <div className="text-4xl font-bold mb-2"
             style={{ background: 'linear-gradient(90deg, #FFD700, #E01C2E, #FFD700)',
                      backgroundSize: '200% auto',
                      WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
                      animation: 'milestone-shine 2s linear infinite',
                      fontFamily: 'Outfit, system-ui, sans-serif' }}>
          {msg.title}
        </div>
        <div className="text-sm opacity-90 max-w-xs mx-auto">{msg.sub}</div>
      </div>
      <style>{`
        @keyframes milestone-pop {
          0% { transform: scale(0.5); opacity: 0; }
          60% { transform: scale(1.1); opacity: 1; }
          100% { transform: scale(1); opacity: 1; }
        }
        @keyframes milestone-shine {
          0% { background-position: 0% center; }
          100% { background-position: 200% center; }
        }
      `}</style>
    </div>
  );
}


/* ────────── Jackpot celebration modal (iter83 viral) ──────────
 * Full-screen premium celebration when the wheel lands on Jackpot.
 *   - Pulsating gold halo
 *   - "Vous êtes à X jours de votre Starter Pro" message
 *   - "Partager ma victoire" button that generates a social share image
 *     via Canvas (1080x1080 Instagram-ready) and uses Web Share API or
 *     falls back to download + WhatsApp URL intent.
 */
function JackpotCelebrationModal({ win, onClose, user }) {
  const { t } = useTranslation();
  const [sharing, setSharing] = useState(false);
  const pointsToGo = Math.max(0, 10000 - (win.newTotal || 0));

  // Auto-close after 8s if the user does nothing, so the UX doesn't block.
  useEffect(() => {
    const t = setTimeout(onClose, 8000);
    return () => clearTimeout(t);
  }, [onClose]);

  const buildShareImage = async () => {
    const SZ = 1080;
    const c = document.createElement('canvas');
    c.width = SZ; c.height = SZ;
    const ctx = c.getContext('2d');
    // Background — deep blue gradient
    const g = ctx.createRadialGradient(SZ/2, SZ*0.35, 60, SZ/2, SZ*0.6, SZ*0.85);
    g.addColorStop(0, '#1c0b8a'); g.addColorStop(1, '#05021f');
    ctx.fillStyle = g; ctx.fillRect(0, 0, SZ, SZ);
    // Gold radial glow
    const glow = ctx.createRadialGradient(SZ/2, SZ*0.45, 40, SZ/2, SZ*0.45, SZ*0.55);
    glow.addColorStop(0, 'rgba(255,215,0,0.55)'); glow.addColorStop(1, 'rgba(255,215,0,0)');
    ctx.fillStyle = glow; ctx.fillRect(0, 0, SZ, SZ);
    // JAPAP badge (top)
    ctx.fillStyle = '#FFD700';
    ctx.font = 'bold 44px Outfit, Arial';
    ctx.textAlign = 'center';
    ctx.fillText('JAPAP', SZ/2, 130);
    ctx.fillStyle = 'rgba(255,255,255,0.65)';
    ctx.font = '22px Manrope, Arial';
    ctx.fillText('Roue de la Fortune', SZ/2, 165);
    // JACKPOT big
    ctx.font = 'bold 140px Outfit, Arial';
    ctx.fillStyle = '#FFD700';
    ctx.shadowColor = 'rgba(255,215,0,0.7)'; ctx.shadowBlur = 40;
    ctx.fillText('🎰 JACKPOT', SZ/2, 380);
    ctx.shadowBlur = 0;
    // Points won
    ctx.font = 'bold 200px Outfit, Arial';
    ctx.fillStyle = '#FFFFFF';
    ctx.fillText(`+${win.points}`, SZ/2, 600);
    ctx.font = '40px Manrope, Arial';
    ctx.fillStyle = 'rgba(255,255,255,0.75)';
    ctx.fillText('points remportés', SZ/2, 650);
    // User identity
    const name = (user?.first_name || user?.username || 'Un joueur JAPAP').slice(0, 28);
    ctx.font = 'bold 52px Outfit, Arial';
    ctx.fillStyle = '#F7931A';
    ctx.fillText(name, SZ/2, 780);
    // Progress line
    ctx.font = '36px Manrope, Arial';
    ctx.fillStyle = 'rgba(255,255,255,0.85)';
    const msg = pointsToGo > 0
      ? `Encore ${pointsToGo.toLocaleString('fr-FR')} pts pour le Starter Pro`
      : `Objectif atteint — Starter Pro débloqué !`;
    ctx.fillText(msg, SZ/2, 860);
    // CTA
    ctx.font = 'bold 32px Outfit, Arial';
    ctx.fillStyle = '#FFD700';
    ctx.fillText('→ japap.com/games/wheel', SZ/2, 980);
    return new Promise((resolve) => c.toBlob((b) => resolve(b), 'image/png', 0.92));
  };

  const share = async () => {
    setSharing(true);
    try {
      const blob = await buildShareImage();
      const file = new File([blob], 'japap-jackpot.png', { type: 'image/png' });
      const shareData = {
        title: 'JAPAP Jackpot',
        text: `🎰 JACKPOT ! +${win.points} points sur JAPAP Roue de la Fortune !`,
      };
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ ...shareData, files: [file] });
      } else if (navigator.share) {
        await navigator.share({ ...shareData, url: window.location.origin + '/games/wheel' });
      } else {
        // Fallback : download + open WhatsApp intent in a new tab
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = 'japap-jackpot.png';
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
        const txt = encodeURIComponent(
          `🎰 JACKPOT ! +${win.points} points sur JAPAP Roue de la Fortune !\n` +
          `${window.location.origin}/games/wheel`,
        );
        window.open(`https://wa.me/?text=${txt}`, '_blank', 'noopener,noreferrer');
        toast.success('Image téléchargée — joignez-la à votre message WhatsApp.');
      }
    } catch (e) {
      if (e?.name !== 'AbortError') toast.error('Partage impossible — image téléchargée.');
    } finally { setSharing(false); }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center jp-animate-fadeIn"
         style={{ background: 'radial-gradient(circle at center, rgba(0,0,0,0.88) 0%, rgba(0,0,0,0.96) 100%)' }}
         data-testid="jackpot-celebration-modal">
      {/* Pulsating gold halo */}
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
        <div className="rounded-full"
             style={{
               width: 520, height: 520,
               background: 'radial-gradient(circle, rgba(255,215,0,0.55) 0%, rgba(255,147,26,0.25) 50%, transparent 70%)',
               animation: 'jackpotHalo 1.8s ease-in-out infinite',
             }} />
      </div>
      {/* Close button */}
      <button onClick={onClose} data-testid="jackpot-modal-close"
              aria-label={t('wheel_fortune.fermer')}
              className="absolute top-4 right-4 w-10 h-10 rounded-full flex items-center justify-center z-[65]"
              style={{ background: 'rgba(255,255,255,0.12)', color: '#fff' }}>
        <X size={18} weight="bold" />
      </button>
      {/* Content card */}
      <div className="relative z-[62] mx-6 max-w-sm w-full text-center"
           style={{ animation: 'jackpotPop 0.7s cubic-bezier(0.34, 1.56, 0.64, 1)' }}>
        <div className="text-6xl mb-2" style={{ filter: 'drop-shadow(0 4px 20px rgba(255,215,0,0.8))' }}>🎰</div>
        <div className="font-['Outfit'] text-5xl font-extrabold mb-1"
             style={{
               background: 'linear-gradient(90deg, #FFD700, #F7931A, #FFD700)',
               backgroundSize: '200% auto',
               WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
               animation: 'jackpotShine 2s linear infinite',
             }}>{t('wheel_fortune.jackpot')}</div>
        <div className="text-white font-['Outfit'] font-extrabold text-7xl mt-2"
             style={{ textShadow: '0 4px 24px rgba(255,215,0,0.55)' }}>
          +{win.points}
        </div>
        <div className="text-white/80 text-sm mb-4 font-['Manrope']">points remportés</div>

        {pointsToGo > 0 ? (
          <div className="text-white/90 text-base font-['Manrope'] mb-5 leading-snug">
            Vous êtes à <strong className="text-[#FFD700]">{pointsToGo.toLocaleString('fr-FR')} points</strong>
            {' '}et <strong className="text-[#FFD700]">{win.daysToGo} jour{win.daysToGo > 1 ? 's' : ''}</strong>
            {' '}de votre Pack Starter Pro 🎁
          </div>
        ) : (
          <div className="text-white/90 text-base font-['Manrope'] mb-5">
            Objectif atteint — votre <strong className="text-[#FFD700]">Starter Pro</strong> est prêt !
          </div>
        )}

        <button onClick={share} disabled={sharing}
                data-testid="jackpot-share-btn"
                className="w-full py-3 rounded-full font-bold text-base flex items-center justify-center gap-2 transition-transform active:scale-[0.97]"
                style={{
                  background: 'linear-gradient(90deg, #FFD700, #F7931A)',
                  color: '#111',
                  boxShadow: '0 10px 36px rgba(255,215,0,0.55)',
                }}>
          <Share size={18} weight="fill" />
          {sharing ? t('wheel_fortune.preparation') : 'Partager ma victoire'}
        </button>
        <button onClick={onClose} data-testid="jackpot-modal-continue"
                className="mt-2 text-xs text-white/60 underline font-['Manrope']">
          Continuer
        </button>
      </div>
      <style>{`
        @keyframes jackpotPop {
          0%  { transform: scale(0.4); opacity: 0; }
          60% { transform: scale(1.08); opacity: 1; }
          100% { transform: scale(1); opacity: 1; }
        }
        @keyframes jackpotShine {
          0%   { background-position: 0% center; }
          100% { background-position: 200% center; }
        }
        @keyframes jackpotHalo {
          0%,100% { transform: scale(0.95); opacity: 0.65; }
          50%     { transform: scale(1.05); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
