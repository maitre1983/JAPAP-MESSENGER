/**
 * TapChallengePage — iter83 Phase 3 engagement game.
 *
 * Product rules (ALL server-enforced, frontend is purely UX) :
 *   - 10 seconds hard duration, server-clocked
 *   - Max 1 run/day per user
 *   - Points = taps_valid + milestone bonus (server-computed)
 *   - Anti-cheat : server caps at 12 taps/sec
 *   - All points feed the unified Starter Pro cycle (no XAF)
 */
import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, HandTap, Lightning, Clock, Trophy } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;
const DURATION_MS = 10_000;

export default function TapChallengePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [phase, setPhase] = useState('loading');  // loading | intro | playing | result | exhausted
  const [run, setRun] = useState(null);
  const [status, setStatus] = useState(null);
  const [taps, setTaps] = useState(0);
  const [timeLeft, setTimeLeft] = useState(DURATION_MS);
  const [result, setResult] = useState(null);
  const [starting, setStarting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const tickRef = useRef(null);
  const submittedRef = useRef(false);
  const tapsRef = useRef(0);
  // iter134 — Capture run_id in a ref so the timer's submitRun never reads
  // a stale `run` state (was causing race where submit fired before setRun
  // committed → "Soumission impossible" + 0 taps validated).
  const runIdRef = useRef(null);

  useEffect(() => { loadStatus(); }, []);
  useEffect(() => () => clearInterval(tickRef.current), []);
  useEffect(() => { tapsRef.current = taps; }, [taps]);

  const loadStatus = async () => {
    try {
      const { data } = await axios.get(`${API}/api/tap/status`, { withCredentials: true });
      setStatus(data);
      if ((data.remaining_today ?? 0) <= 0) setPhase('exhausted');
      else setPhase('intro');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Impossible de charger le statut');
      setPhase('intro');
    }
  };

  const startRun = async () => {
    setStarting(true);
    try {
      const { data } = await axios.post(`${API}/api/tap/start`, {}, { withCredentials: true });
      setRun(data);
      runIdRef.current = data.run_id;        // iter134 — race-safe handoff to timer
      setTaps(0); tapsRef.current = 0;
      setTimeLeft(DURATION_MS);
      submittedRef.current = false;
      setPhase('playing');
      const serverStart = new Date(data.start_at).getTime();
      const localStart = Date.now();
      const drift = localStart - serverStart;
      // iter134 — Always anchor the countdown to localStart. Using the server
      // clock when there's a 3s+ drift caused submitRun to fire instantly
      // (server slightly ahead → DURATION_MS already elapsed at t=0). The
      // server still validates the duration on submit so this is safe.
      const reference = localStart;
      // Log start in dev to ease debugging.
      // eslint-disable-next-line no-console
      console.debug('[tap] start', { run_id: data.run_id, drift, reference });
      tickRef.current = setInterval(() => {
        const left = Math.max(0, DURATION_MS - (Date.now() - reference));
        setTimeLeft(left);
        if (left <= 0) {
          clearInterval(tickRef.current);
          if (!submittedRef.current) submitRun();
        }
      }, 80);
    } catch (e) {
      if (e.response?.status === 429) setPhase('exhausted');
      toast.error(e.response?.data?.detail || 'Impossible de démarrer');
    } finally { setStarting(false); }
  };

  const doTap = () => {
    if (phase !== 'playing') return;
    setTaps((t) => t + 1);
    if (navigator.vibrate) navigator.vibrate(8);
  };

  const submitRun = async () => {
    if (submittedRef.current || submitting) return;
    submittedRef.current = true;
    setSubmitting(true);
    clearInterval(tickRef.current);
    // iter134 — race-safe: read run_id from ref (timer fired before setRun
    // committed in some cases). Fail loud if still null.
    const runId = runIdRef.current ?? run?.run_id;
    if (!runId) {
      // eslint-disable-next-line no-console
      console.error('[tap] submitRun aborted — no run_id', { runIdRef: runIdRef.current, run });
      toast.error('Session expirée. Veuillez relancer.');
      setPhase('intro');
      setSubmitting(false);
      return;
    }
    try {
      // eslint-disable-next-line no-console
      console.debug('[tap] submit', { runId, taps: tapsRef.current });
      const { data } = await axios.post(
        `${API}/api/tap/submit`,
        { run_id: runId, taps: tapsRef.current },
        { withCredentials: true },
      );
      setResult(data);
      setPhase('result');
      if (navigator.vibrate) navigator.vibrate([50, 40, 80]);
      if (data.points_awarded > 0) toast.success(`+${data.points_awarded} points ajoutés`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Soumission impossible');
      setPhase('intro');
    } finally { setSubmitting(false); }
  };

  /* ────────────── Views ────────────── */
  if (phase === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center"
           style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}>
        <div className="w-8 h-8 border-2 border-t-transparent rounded-full animate-spin"
             style={{ borderColor: '#FFD700', borderTopColor: 'transparent' }} />
      </div>
    );
  }

  if (phase === 'exhausted') {
    return <ExhaustedView status={status} onBack={() => navigate('/games')} />;
  }

  if (phase === 'intro') {
    return <IntroView starting={starting} status={status} onStart={startRun} onBack={() => navigate(-1)} />;
  }

  if (phase === 'playing' && run) {
    const pct = (timeLeft / DURATION_MS) * 100;
    const critical = timeLeft < 3000;
    const tps = timeLeft < DURATION_MS ? (taps / ((DURATION_MS - timeLeft) / 1000)) : 0;
    return (
      <div className="min-h-screen flex flex-col"
           style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
           data-testid="tap-playing">
        {/* Top timer bar */}
        <div className="h-2 w-full" style={{ background: 'rgba(255,255,255,0.08)' }}>
          <div className="h-full transition-all duration-100"
               style={{
                 width: `${pct}%`,
                 background: critical
                   ? 'linear-gradient(90deg, #E01C2E, #F7931A)'
                   : 'linear-gradient(90deg, #FFD700, #F7931A)',
                 boxShadow: critical ? '0 0 16px #E01C2E' : '0 0 16px #F7931A',
               }} />
        </div>

        {/* Top stats */}
        <div className="flex items-center justify-between px-5 py-3 text-white">
          <div className="flex items-center gap-1.5 text-xs">
            <Lightning size={16} weight="fill" />
            <span>{tps.toFixed(1)} tps</span>
          </div>
          <div className={`flex items-center gap-1.5 text-sm font-bold ${critical ? 'animate-pulse' : ''}`}
               data-testid="tap-timer">
            <Clock size={16} weight="fill" />
            <span style={{ color: critical ? '#F7931A' : '#fff' }}>
              {(timeLeft / 1000).toFixed(1)}s
            </span>
          </div>
        </div>

        {/* Big tap zone */}
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          <div className="mb-6 text-center">
            <div className="text-[10px] uppercase tracking-widest font-bold text-white/60 mb-2">
              Score actuel
            </div>
            <div className="font-['Outfit'] text-7xl font-extrabold"
                 style={{ color: '#FFD700', textShadow: '0 6px 40px rgba(255,215,0,0.55)' }}
                 data-testid="tap-count">
              {taps}
            </div>
          </div>
          <button
            onClick={doTap}
            onTouchStart={(e) => { e.preventDefault(); doTap(); }}
            data-testid="tap-button"
            className="w-64 h-64 rounded-full flex items-center justify-center active:scale-[0.92] transition-transform select-none"
            style={{
              background: 'radial-gradient(circle at 30% 30%, #FFD700 0%, #F7931A 55%, #E01C2E 100%)',
              boxShadow: '0 30px 80px rgba(247,147,26,0.55), inset 0 -8px 0 rgba(0,0,0,0.2), inset 0 8px 0 rgba(255,255,255,0.25)',
              touchAction: 'manipulation',
              userSelect: 'none',
              WebkitUserSelect: 'none',
            }}>
            <HandTap size={96} weight="fill" color="#111" />
          </button>
          <div className="mt-6 text-xs text-white/55">Tapotez aussi vite que possible !</div>
        </div>
      </div>
    );
  }

  if (phase === 'result' && result) {
    return <ResultView result={result} onReplay={() => loadStatus()} onBack={() => navigate('/games')} />;
  }
  // iter134 — Defensive fallback so an unexpected phase never produces a
  // white screen.
  return (
    <div className="min-h-screen flex flex-col items-center justify-center text-white p-6"
         data-testid="tap-fallback"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}>
      <div className="text-3xl mb-3">🤔</div>
      <h2 className="font-['Outfit'] text-xl font-bold mb-2">État inattendu</h2>
      <button onClick={() => loadStatus()}
              className="mt-3 px-5 py-2.5 rounded-full font-bold text-sm"
              style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
        Réessayer
      </button>
      <button onClick={() => navigate('/games')}
              className="mt-2 px-5 py-2.5 rounded-full font-bold text-sm"
              style={{ background: 'rgba(255,255,255,0.12)', color: '#fff' }}>
        Retour aux jeux
      </button>
    </div>
  );
}

/* ─────────────── sub-views ─────────────── */

function IntroView({ starting, status, onStart, onBack }) {
  const { t } = useTranslation();
  return (
    <div className="min-h-screen flex flex-col"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="tap-intro">
      <div className="flex items-center p-4">
        <button onClick={onBack} className="p-2 rounded-full text-white"
                style={{ background: 'rgba(255,255,255,0.1)' }}
                data-testid="tap-back">
          <ArrowLeft size={18} weight="bold" />
        </button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center px-6 text-center text-white">
        <div className="w-24 h-24 rounded-3xl flex items-center justify-center mb-6"
             style={{
               background: 'linear-gradient(135deg, #F7931A, #E01C2E)',
               boxShadow: '0 20px 60px rgba(247,147,26,0.5)',
             }}>
          <HandTap size={44} weight="fill" />
        </div>
        <h1 className="font-['Outfit'] text-4xl font-extrabold mb-2">Tap Challenge</h1>
        <p className="text-white/70 text-sm mb-8 max-w-xs">
          Tapotez le plus vite possible en 10 secondes · 1 point / tap + bonus palier
        </p>

        {status?.best_taps_ever > 0 && (
          <div className="mb-6 px-4 py-2 rounded-full text-xs font-semibold flex items-center gap-2"
               style={{ background: 'rgba(255,215,0,0.15)', color: '#FFD700', border: '1px solid rgba(255,215,0,0.3)' }}>
            <Trophy size={14} weight="fill" />
            Meilleur score : {status.best_taps_ever} taps
          </div>
        )}

        <div className="w-full max-w-sm space-y-2 mb-8 text-[11px]">
          <RuleLine icon="⏱️" text="Timer strict de 10 secondes" />
          <RuleLine icon="🎯" text="Paliers bonus : 30 → +10 · 50 → +25 · 80 → +50 pts" />
          <RuleLine icon="📊" text="1 session / jour · points vers Starter Pro" />
        </div>

        <button onClick={onStart} disabled={starting}
                data-testid="tap-start-btn"
                className="w-full max-w-sm py-4 rounded-full font-bold text-base transition-transform active:scale-[0.97]"
                style={{
                  background: 'linear-gradient(90deg, #FFD700, #F7931A)',
                  color: '#111',
                  boxShadow: '0 10px 36px rgba(255,215,0,0.55)',
                }}>
          {starting ? t('tap_challenge.preparation') : '▶ Lancer le défi'}
        </button>
      </div>
    </div>
  );
}

function RuleLine({ icon, text }) {
  return (
    <div className="flex items-center gap-2 p-2.5 rounded-lg text-left"
         style={{ background: 'rgba(255,255,255,0.06)' }}>
      <span className="text-base flex-shrink-0">{icon}</span>
      <span className="text-white/85">{text}</span>
    </div>
  );
}

function ExhaustedView({ status, onBack }) {
  return (
    <div className="min-h-screen flex flex-col"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="tap-exhausted">
      <div className="flex items-center p-4">
        <button onClick={onBack} className="p-2 rounded-full text-white"
                style={{ background: 'rgba(255,255,255,0.1)' }}>
          <ArrowLeft size={18} weight="bold" />
        </button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center px-6 text-center text-white">
        <div className="w-24 h-24 rounded-3xl flex items-center justify-center mb-6"
             style={{ background: 'rgba(255,255,255,0.08)' }}>
          <Clock size={44} weight="fill" color="#F7931A" />
        </div>
        <h1 className="font-['Outfit'] text-3xl font-extrabold mb-2">À demain !</h1>
        <p className="text-white/70 text-sm mb-8 max-w-xs">
          Vous avez déjà joué votre session du jour. Revenez demain pour ajouter des points à votre cycle Starter Pro.
        </p>
        {status?.last_run && (
          <div className="mb-6 px-4 py-3 rounded-xl text-left text-sm"
               style={{ background: 'rgba(255,255,255,0.08)', minWidth: 240 }}>
            <div className="text-xs text-white/60 uppercase tracking-wider mb-1">Dernière session</div>
            <div className="font-bold">{status.last_run.taps} taps · +{status.last_run.points} points</div>
          </div>
        )}
        <button onClick={onBack}
                data-testid="tap-back-to-games-btn"
                className="w-full max-w-sm py-3 rounded-full font-bold text-sm"
                style={{ background: 'rgba(255,255,255,0.12)', color: '#fff' }}>
          Retour aux jeux
        </button>
      </div>
    </div>
  );
}

function ResultView({ result, onReplay, onBack }) {
  const { t } = useTranslation();
  const color = result.taps_valid >= 80 ? '#FFD700' : result.taps_valid >= 50 ? '#10B981' : '#F7931A';
  const [duelToggles, setDuelToggles] = useState({ duel_enabled: true });
  useEffect(() => {
    axios.get(`${API}/api/tap/history`, { withCredentials: true }).catch(() => {});
    axios.get(`${API}/api/games/toggles`).then(({ data }) => setDuelToggles(data)).catch(() => {});
  }, []);
  const [creatingDuel, setCreatingDuel] = useState(false);
  const challengeFriend = async () => {
    setCreatingDuel(true);
    try {
      const { data } = await axios.post(`${API}/api/duel/create-from-tap`,
        { run_id: result.run_id }, { withCredentials: true });
      const shareUrl = `${window.location.origin}${data.share_url}`;
      const text = `💥 Je viens de faire ${result.taps_valid} taps en 10 s au Tap Challenge — ferez-vous mieux ? ${shareUrl}`;
      if (navigator.share) {
        try { await navigator.share({ title: t('tap_challenge.defi_tap_challenge'), text, url: shareUrl }); }
        catch { /* cancelled */ }
      } else {
        try { await navigator.clipboard.writeText(shareUrl); toast.success('Lien copié · partagez sur WhatsApp !'); } catch {}
        window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, '_blank');
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Impossible de créer le défi');
    } finally { setCreatingDuel(false); }
  };
  return (
    <div className="min-h-screen flex flex-col"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="tap-result">
      <div className="flex items-center p-4">
        <button onClick={onBack} className="p-2 rounded-full text-white"
                style={{ background: 'rgba(255,255,255,0.1)' }}>
          <ArrowLeft size={18} weight="bold" />
        </button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center px-6 text-center text-white">
        <div className="w-28 h-28 rounded-full flex items-center justify-center mb-5"
             style={{
               background: `linear-gradient(135deg, ${color}, ${color}88)`,
               boxShadow: `0 20px 60px ${color}66`,
             }}>
          <HandTap size={52} weight="fill" color="#111" />
        </div>
        <div className="font-['Outfit'] text-6xl font-extrabold mb-1" style={{ color }}>
          {result.taps_valid}
        </div>
        <div className="text-white/60 text-sm mb-4">taps validés</div>
        {result.timed_out && (
          <div className="text-[11px] mb-3 px-3 py-1 rounded-full"
               style={{ background: '#E01C2E33', color: '#F7931A', border: '1px solid #E01C2E66' }}>
            ⏱️ Temps écoulé — session invalidée
          </div>
        )}
        {result.cheated && (
          <div className="text-[11px] mb-3 px-3 py-1 rounded-full"
               style={{ background: '#E01C2E33', color: '#E01C2E', border: '1px solid #E01C2E66' }}
               data-testid="tap-cheated-warning">
            ⚠️ Anti-triche : score plafonné au maximum humain
          </div>
        )}
        {!result.cheated && result.suspicious && (
          <div className="text-[11px] mb-3 px-3 py-1 rounded-full"
               style={{ background: '#F7931A33', color: '#F7931A', border: '1px solid #F7931A66' }}
               data-testid="tap-suspicious-warning">
            🔍 Score élevé — signalé pour revue automatique
          </div>
        )}
        <div className="font-['Outfit'] text-3xl font-bold mb-1 text-[#FFD700]"
             data-testid="tap-points">
          +{result.points_awarded} points
        </div>
        {result.bonus_awarded > 0 && (
          <div className="mb-2 text-xs px-3 py-1 rounded-full font-bold"
               style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
            🏆 Bonus palier : +{result.bonus_awarded} pts
          </div>
        )}
        <div className="text-white/50 text-xs mb-8">Total cycle : {result.points_cycle.toLocaleString('fr-FR')} / 10 000</div>
        <div className="w-full max-w-sm space-y-3">
          {duelToggles.duel_enabled && (
            <button onClick={challengeFriend} disabled={creatingDuel}
                    data-testid="tap-challenge-friend-btn"
                    className="w-full py-3 rounded-full font-bold text-base active:scale-[0.97]"
                    style={{ background: 'linear-gradient(90deg, #8B5CF6, #E01C2E)', color: '#fff',
                             boxShadow: '0 10px 36px rgba(139,92,246,0.55)' }}>
              ⚔ {creatingDuel ? t('tap_challenge.creation_du_defi') : t('tap_challenge.defier_un_ami')}
            </button>
          )}
          <button onClick={onBack}
                  data-testid="tap-back-to-games-btn"
                  className="w-full py-3 rounded-full font-bold text-base"
                  style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111',
                           boxShadow: '0 10px 36px rgba(255,215,0,0.55)' }}>
            Retour aux jeux
          </button>
        </div>
      </div>
    </div>
  );
}
