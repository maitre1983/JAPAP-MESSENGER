/**
 * QuizJAPAP — iter118 (refondu) — engagement quiz fluide.
 *
 * Product rules (server-authoritative) :
 *   - SESSION_SIZE configurable (default 5) — backend renvoie `questions`
 *   - Timer mode: "per_question" (15s par défaut) OU "global" (60s pour les 5)
 *   - Auto-advance configurable (délai 300-3000ms après réponse)
 *   - Max sessions/jour configurable (1-50)
 *   - Points awarded server-side : +N par bonne, +bonus si parfait
 *   - Tous les paramètres pilotables admin via /api/games-settings/quiz
 *
 * Bug fixes par rapport à iter83 :
 *   - Le timer côté frontend lit `time_limit_seconds` du backend (avant: hardcodé 10s)
 *   - Mode `per_question` : chaque question a son propre budget temps
 *   - Stale-state de `setIdx(idx + 1)` corrigé via updater fonctionnel
 *   - Feedback ✅/❌ visible avant l'auto-advance (rassure l'utilisateur)
 *   - Si pas d'auto-advance, bouton "Question suivante" dispo
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useNavigate, useLocation } from 'react-router-dom';
import { ArrowLeft, Question, CheckCircle, XCircle, Clock, Trophy, Lightning, Flame } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import DoublersLeaderboard from '@/components/games/DoublersLeaderboard';

const API = process.env.REACT_APP_BACKEND_URL;

export default function QuizJAPAPPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  // iter130 — Detect daily-challenge mode from URL.
  const isDaily = location.pathname.includes('/games/quiz/daily');
  const startEndpoint = isDaily ? '/api/quiz/daily-challenge/start' : '/api/quiz/start';
  const submitEndpoint = isDaily ? '/api/quiz/daily-challenge/submit' : '/api/quiz/submit';
  const [phase, setPhase] = useState('intro');   // intro | playing | result
  const [run, setRun] = useState(null);          // includes timer config
  const [answers, setAnswers] = useState([]);    // length = run.questions.length
  const [idx, setIdx] = useState(0);
  const [timeLeftMs, setTimeLeftMs] = useState(0);
  const [feedback, setFeedback] = useState(null); // {questionIdx, choice} — visual only
  const [result, setResult] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [starting, setStarting] = useState(false);

  const tickRef = useRef(null);
  const phaseStartRef = useRef(0);    // ms timestamp when current phase (question or session) started
  const phaseDurationRef = useRef(0); // ms total budget for current phase
  const autoSubmittedRef = useRef(false);
  const answersRef = useRef([]);      // synchronously kept in sync with answers (ref for async submit)
  const idxRef = useRef(0);
  const advanceTimerRef = useRef(null);
  // iter120 — Live correctness counter (revealed by /quiz/answer endpoint).
  const [liveScore, setLiveScore] = useState({ correct: 0, wrong: 0 });
  const liveScoreRef = useRef({ correct: 0, wrong: 0 });
  const [muted, setMuted] = useState(
    typeof window !== 'undefined' && localStorage.getItem('quiz_muted') === '1'
  );
  const audioCtxRef = useRef(null);

  // iter120 — Web Audio API tone helper (no asset deps).
  const playTone = useCallback((freqs, dur = 0.12, type = 'sine') => {
    if (muted) return;
    try {
      if (!audioCtxRef.current) {
        const Ctor = window.AudioContext || window.webkitAudioContext;
        if (!Ctor) return;
        audioCtxRef.current = new Ctor();
      }
      const ctx = audioCtxRef.current;
      const list = Array.isArray(freqs) ? freqs : [freqs];
      list.forEach((f, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = type;
        osc.frequency.value = f;
        const start = ctx.currentTime + i * dur * 0.85;
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(0.18, start + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + dur);
        osc.connect(gain).connect(ctx.destination);
        osc.start(start);
        osc.stop(start + dur + 0.05);
      });
    } catch (_) { /* silent */ }
  }, [muted]);

  const toggleMute = useCallback(() => {
    setMuted((m) => {
      const next = !m;
      localStorage.setItem('quiz_muted', next ? '1' : '0');
      return next;
    });
  }, []);

  useEffect(() => () => {
    clearInterval(tickRef.current);
    clearTimeout(advanceTimerRef.current);
  }, []);

  /** Compute the current phase budget given the timer mode. */
  const currentBudgetMs = useCallback((r) => {
    if (!r) return 0;
    if (r.timer_mode === 'per_question') {
      return Math.max(3, r.timer_per_question_seconds || 15) * 1000;
    }
    return Math.max(10, r.time_limit_seconds || 60) * 1000;
  }, []);

  const startPhaseClock = useCallback((r) => {
    clearInterval(tickRef.current);
    const budget = currentBudgetMs(r);
    phaseStartRef.current = Date.now();
    phaseDurationRef.current = budget;
    setTimeLeftMs(budget);
    tickRef.current = setInterval(() => {
      const left = Math.max(0, phaseDurationRef.current - (Date.now() - phaseStartRef.current));
      setTimeLeftMs(left);
      if (left <= 0) {
        clearInterval(tickRef.current);
        handleTimeUp();
      }
    }, 100);
  }, [currentBudgetMs]);

  const start = async () => {
    setStarting(true);
    try {
      // iter234 — Pass current UI language so the backend picks
      // localised questions (with FR fallback if the EN bank is short).
      const language = (i18n.language || 'fr').slice(0, 2).toLowerCase();
      const { data } = await axios.post(
        `${API}${startEndpoint}`,
        { language },
        { withCredentials: true },
      );
      setRun(data);
      const blank = new Array(data.questions.length).fill(-1);
      setAnswers(blank);
      answersRef.current = blank;
      setIdx(0);
      idxRef.current = 0;
      autoSubmittedRef.current = false;
      setFeedback(null);
      // iter120 — Reset live counters at session start.
      liveScoreRef.current = { correct: 0, wrong: 0 };
      setLiveScore({ correct: 0, wrong: 0 });
      setPhase('playing');
      startPhaseClock(data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Impossible de démarrer');
    } finally { setStarting(false); }
  };

  const handleTimeUp = () => {
    if (!run) return;
    if (run.timer_mode === 'per_question') {
      // Move on to next question (or submit if it was the last). We DO NOT
      // overwrite an existing user choice — only auto-advance the index.
      const cur = idxRef.current;
      if (cur < run.questions.length - 1) {
        const next = cur + 1;
        idxRef.current = next;
        setIdx(next);
        startPhaseClock(run);
      } else if (!autoSubmittedRef.current) {
        autoSubmittedRef.current = true;
        submitAll();
      }
    } else {
      // Global timer expired → submit whatever the user has so far.
      if (!autoSubmittedRef.current) {
        autoSubmittedRef.current = true;
        submitAll();
      }
    }
  };

  const advanceTo = (next) => {
    if (next < (run?.questions?.length || 0)) {
      idxRef.current = next;
      setIdx(next);
      setFeedback(null);
      if (run.timer_mode === 'per_question') {
        startPhaseClock(run);
      }
    } else if (!autoSubmittedRef.current) {
      autoSubmittedRef.current = true;
      submitAll();
    }
  };

  const chooseAnswer = async (optionIdx) => {
    if (phase !== 'playing' || !run) return;
    if (feedback) return;            // pendant le feedback (300-3000ms), pas de re-clic
    const cur = idxRef.current;
    // Persist the choice synchronously (both state + ref so submitAll never
    // sees a stale array even if the user finishes Q5 in the same tick).
    setAnswers((prev) => {
      const next = [...prev];
      next[cur] = optionIdx;
      answersRef.current = next;
      return next;
    });

    // iter120 — Reveal correctness via backend (frontend = display only).
    // We optimistically render a "selected" state immediately to avoid lag,
    // then update once the server replies with the truth.
    setFeedback({ questionIdx: cur, choice: optionIdx, correct: null, correctOption: null });
    let isCorrect = null;
    let learningOption = null;
    try {
      const { data } = await axios.post(`${API}/api/quiz/answer`, {
        run_id: run.run_id,
        question_idx: cur,
        selected_option: optionIdx,
      }, { withCredentials: true });
      isCorrect = !!data.correct;
      // iter122 — Learning mode: when the answer is wrong AND admin enabled
      // `quiz_show_correct_after_wrong`, the server returns `correct_option`
      // (displayed index). We highlight it green so the player learns the
      // right answer without ever seeing it before lock-in.
      learningOption = (typeof data.correct_option === 'number') ? data.correct_option : null;
      setFeedback({ questionIdx: cur, choice: optionIdx, correct: isCorrect, correctOption: learningOption });
      // Update live score (refs are the source of truth for closures).
      const next = {
        correct: liveScoreRef.current.correct + (isCorrect ? 1 : 0),
        wrong: liveScoreRef.current.wrong + (isCorrect ? 0 : 1),
      };
      liveScoreRef.current = next;
      setLiveScore(next);
      // Sound feedback
      if (isCorrect) {
        playTone([880, 1175], 0.12, 'sine');     // tick positif (A5+D6)
        if (navigator.vibrate) navigator.vibrate(35);
      } else {
        playTone([220], 0.18, 'sawtooth');         // son discret négatif
        if (navigator.vibrate) navigator.vibrate([60, 40]);
      }
    } catch (e) {
      // If the reveal endpoint fails, keep going gracefully — submit final
      // is still authoritative. We just won't show ✅/❌ for this question.
      setFeedback({ questionIdx: cur, choice: optionIdx, correct: null, correctOption: null });
    }

    // iter120 — Dynamic pacing: prefer admin-configured per-question delays
    // (run.auto_advance_delays_ms array). Fallback to a sensible scaled
    // schedule when not provided.
    const total = run.questions.length;
    const baseDelay = Math.max(300, Math.min(3000, run.auto_advance_delay_ms || 900));
    const delaysArr = Array.isArray(run.auto_advance_delays_ms) ? run.auto_advance_delays_ms : [];
    let delay;
    if (delaysArr.length > 0 && cur < delaysArr.length) {
      delay = Math.max(300, Math.min(3000, parseInt(delaysArr[cur], 10) || baseDelay));
    } else if (total >= 5) {
      // Fallback dynamic pacing (Q4 -25%, Q5 -45%)
      if (cur === total - 2) delay = Math.round(baseDelay * 0.75);
      else if (cur === total - 1) delay = Math.round(baseDelay * 0.55);
      else delay = baseDelay;
    } else {
      delay = baseDelay;
    }
    delay = Math.max(300, delay);
    // iter122 — Learning mode: when server returned a `correct_option` (wrong
    // answer + admin enabled the feature), extend the feedback delay to at
    // least 1800ms so the player has time to see and absorb the right answer.
    if (learningOption !== null) {
      delay = Math.max(delay, 1800);
    }

    if (run.auto_advance_enabled !== false) {
      clearTimeout(advanceTimerRef.current);
      advanceTimerRef.current = setTimeout(() => advanceTo(cur + 1), delay);
    }
  };

  const submitAll = async () => {
    if (submitting) return;
    setSubmitting(true);
    clearInterval(tickRef.current);
    clearTimeout(advanceTimerRef.current);
    try {
      // Pad/truncate the answers list to exactly SESSION_SIZE so the
      // backend Pydantic validator (min_length=5) never fails the run.
      const expected = run?.questions?.length || 5;
      const padded = [...(answersRef.current || [])];
      while (padded.length < expected) padded.push(-1);
      const payload = { run_id: run.run_id, answers: padded.slice(0, expected) };
      const { data } = await axios.post(`${API}${submitEndpoint}`, payload, { withCredentials: true });
      setResult(data);
      setPhase('result');
      if (navigator.vibrate) navigator.vibrate(data.perfect ? [60, 50, 120] : [40, 50, 40]);
      // iter120 — End-of-quiz audio cue (server-decided perfect/good/normal).
      if (data.perfect) {
        playTone([523, 659, 784, 1046], 0.18, 'sine');     // C-E-G-C arpeggio (perfect)
      } else if (data.correct_count >= 3) {
        playTone([659, 880], 0.16, 'sine');                  // E5-A5 (good)
      } else {
        playTone([440, 330], 0.20, 'sine');                  // A4-E4 (encouraging)
      }
      if (data.perfect) toast.success(`🏆 Sans faute ! +${data.points_awarded} points`);
      else if (data.correct_count >= 3) toast.success(`Bien joué ! ${data.correct_count}/${data.total} — +${data.points_awarded} points`);
      else toast(`${data.correct_count}/${data.total} — +${data.points_awarded} points`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Soumission impossible');
      setPhase('intro');
    } finally { setSubmitting(false); }
  };

  /* ────────────── Views ────────────── */
  if (phase === 'intro') {
    return (
      <IntroView starting={starting} onStart={start} onBack={() => navigate(-1)} />
    );
  }

  if (phase === 'playing' && run) {
    const total = run.questions.length;
    const q = run.questions[idx];
    const budget = currentBudgetMs(run) || 1;
    const pct = Math.max(0, Math.min(100, (timeLeftMs / budget) * 100));
    const critical = timeLeftMs < 3000;
    const showFeedback = feedback && feedback.questionIdx === idx;
    return (
      <div className="min-h-screen flex flex-col"
           style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
           data-testid="quiz-playing">
        {/* Timer bar (top) */}
        <div className="h-2 w-full" style={{ background: 'rgba(255,255,255,0.08)' }}>
          <div className="h-full transition-all duration-100"
               style={{
                 width: `${pct}%`,
                 background: critical
                   ? 'linear-gradient(90deg, #E01C2E, #F7931A)'
                   : 'linear-gradient(90deg, #A78BFA, #F7931A)',
                 boxShadow: critical ? '0 0 16px #E01C2E' : '0 0 16px #A78BFA',
               }} />
        </div>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 text-white">
          <div className="flex items-center gap-2 text-xs">
            <Question size={16} weight="fill" />
            <span data-testid="quiz-progress">{idx + 1} / {total}</span>
          </div>
          {/* iter120 — Live score indicator (✅/❌) + mute toggle */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 text-xs font-bold"
                 data-testid="quiz-live-score">
              <span className="flex items-center gap-1" style={{ color: '#10B981' }}>
                <CheckCircle size={13} weight="fill" />
                <span data-testid="quiz-live-correct">{liveScore.correct}</span>
              </span>
              <span className="flex items-center gap-1" style={{ color: '#F87171' }}>
                <XCircle size={13} weight="fill" />
                <span data-testid="quiz-live-wrong">{liveScore.wrong}</span>
              </span>
            </div>
            <button onClick={toggleMute}
                    className="text-xs opacity-70 hover:opacity-100"
                    aria-label={muted ? "Activer son" : "Couper son"}
                    data-testid="quiz-mute-toggle">
              {muted ? '🔇' : '🔊'}
            </button>
            <div className={`flex items-center gap-1.5 text-sm font-bold ${critical ? 'animate-pulse' : ''}`}
                 data-testid="quiz-timer">
              <Clock size={16} weight="fill" />
              <span style={{ color: critical ? '#F7931A' : '#fff' }}>
                {(timeLeftMs / 1000).toFixed(1)}s
              </span>
            </div>
          </div>
        </div>

        {/* Question */}
        <div className="flex-1 flex flex-col px-5 py-4 max-w-lg mx-auto w-full">
          <div className="rounded-2xl p-5 mb-5 jp-animate-fadeIn"
               style={{ background: 'rgba(255,255,255,0.08)',
                        backdropFilter: 'blur(12px)' }}
               data-testid={`quiz-question-${idx}`}>
            <div className="text-[10px] uppercase tracking-widest font-bold mb-2"
                 style={{ color: '#A78BFA' }}>
              {q.category.replace('_', ' ')}
            </div>
            <div className="font-['Outfit'] text-lg font-bold leading-snug text-white">
              {q.text}
            </div>
          </div>

          {/* Options */}
          <div className="grid grid-cols-1 gap-3">
            {q.options.map((opt, i) => {
              const selected = answers[idx] === i;
              const isMyChoice = showFeedback && feedback.choice === i;
              // iter122 — Learning mode highlight: the correct option (sent by
              // the server only when the user picked wrong AND admin enabled
              // `quiz_show_correct_after_wrong`).
              const isLearning = showFeedback
                && typeof feedback.correctOption === 'number'
                && feedback.correctOption === i
                && feedback.choice !== i;
              return (
                <button key={i}
                        onClick={() => chooseAnswer(i)}
                        disabled={!!feedback}
                        data-testid={`quiz-option-${idx}-${i}`}
                        className="p-4 rounded-xl text-left font-semibold transition-all active:scale-[0.98] disabled:opacity-90"
                        style={{
                          background: isMyChoice
                            ? (feedback?.correct === false
                                ? 'linear-gradient(90deg, #E01C2E, #B91C1C)'
                                : 'linear-gradient(90deg, #10B981, #059669)')
                            : isLearning
                              ? 'linear-gradient(90deg, #10B981, #059669)'
                              : selected
                                ? 'linear-gradient(90deg, #A78BFA, #8B5CF6)'
                                : 'rgba(255,255,255,0.08)',
                          color: '#fff',
                          border: `2px solid ${
                            isMyChoice
                              ? (feedback?.correct === false ? '#E01C2E' : '#10B981')
                              : isLearning
                                ? '#10B981'
                                : selected ? '#A78BFA' : 'rgba(255,255,255,0.15)'}`,
                          boxShadow: (isMyChoice || isLearning || selected)
                            ? (isLearning ? '0 8px 28px rgba(16,185,129,0.55)' : '0 8px 28px rgba(167,139,250,0.4)')
                            : 'none',
                          animation: isLearning ? 'quizCorrectPulse 1.2s ease-in-out infinite' : 'none',
                        }}>
                  <span className="inline-block mr-3 w-7 h-7 rounded-full text-xs leading-[28px] text-center font-bold"
                        style={{ background: (isMyChoice || isLearning || selected) ? '#fff' : 'rgba(255,255,255,0.12)',
                                 color: isMyChoice
                                   ? (feedback?.correct === false ? '#E01C2E' : '#10B981')
                                   : isLearning ? '#10B981' : selected ? '#8B5CF6' : '#fff' }}>
                    {String.fromCharCode(65 + i)}
                  </span>
                  {opt}
                  {isLearning && (
                    <span className="ml-2 text-xs font-bold opacity-90">✓ bonne réponse</span>
                  )}
                </button>
              );
            })}
          </div>
          <style>{`
            @keyframes quizCorrectPulse {
              0%, 100% { box-shadow: 0 8px 28px rgba(16,185,129,0.55); }
              50%      { box-shadow: 0 8px 36px rgba(16,185,129,0.85); }
            }
          `}</style>

          {/* iter118 — Feedback row + Next button (when auto-advance is off) */}
          {showFeedback && (
            <div className="flex items-center justify-between mt-5 jp-animate-fadeIn"
                 data-testid="quiz-feedback">
              <div className="flex items-center gap-2 text-white text-sm font-semibold">
                {feedback.correct === true ? (
                  <>
                    <CheckCircle size={18} weight="fill" color="#10B981" />
                    <span>{t('quiz_j_a_p_a_p.bonne_reponse')}</span>
                  </>
                ) : feedback.correct === false ? (
                  <>
                    <XCircle size={18} weight="fill" color="#E01C2E" />
                    <span>{typeof feedback.correctOption === 'number' ? t('quiz_j_a_p_a_p.la_bonne_reponse_est_en_vert') : t('quiz_j_a_p_a_p.mauvaise_reponse')}</span>
                  </>
                ) : (
                  <>
                    <CheckCircle size={18} weight="fill" color="#10B981" />
                    <span>{t('quiz_j_a_p_a_p.reponse_enregistree')}</span>
                  </>
                )}
              </div>
              {run.auto_advance_enabled === false && (
                <button onClick={() => advanceTo(idx + 1)}
                        data-testid="quiz-next-btn"
                        className="px-4 py-2 rounded-xl font-bold text-sm"
                        style={{ background: '#fff', color: '#0F056B' }}>
                  {idx < total - 1 ? 'Question suivante →' : t('quiz_j_a_p_a_p.voir_le_resultat')}
                </button>
              )}
            </div>
          )}

          {/* Progress dots */}
          <div className="flex justify-center gap-2 mt-6">
            {Array.from({ length: total }, (_, i) => (
              <span key={i}
                    className="h-1.5 rounded-full transition-all"
                    style={{
                      width: i === idx ? 24 : 8,
                      background: i < idx ? '#10B981' : i === idx ? '#A78BFA' : 'rgba(255,255,255,0.2)',
                    }} />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (phase === 'result' && result) {
    return (
      <ResultView result={result} run={run} isDaily={isDaily} onReplay={() => setPhase('intro')} onBack={() => navigate('/games')} />
    );
  }

  // iter134 — Defensive fallback (was returning null = white screen if ever
  // phase ended up in an unhandled combination, e.g. submit error path).
  return (
    <div className="min-h-screen flex flex-col items-center justify-center text-white p-6"
         data-testid="quiz-fallback"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}>
      <div className="text-3xl mb-3">🤔</div>
      <h2 className="font-['Outfit'] text-xl font-bold mb-2">État inconnu</h2>
      <p className="text-white/70 text-sm text-center mb-6 max-w-xs">
        Quelque chose s'est mal passé. Aucune progression n'est perdue.
      </p>
      <div className="flex gap-2">
        <button onClick={() => setPhase('intro')}
                className="px-5 py-2.5 rounded-full font-bold text-sm"
                style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
          Réessayer
        </button>
        <button onClick={() => navigate('/games')}
                className="px-5 py-2.5 rounded-full font-bold text-sm"
                style={{ background: 'rgba(255,255,255,0.12)', color: '#fff' }}>
          Retour aux jeux
        </button>
      </div>
    </div>
  );
}

/* ─────────────── sub-views ─────────────── */

function IntroView({ starting, onStart, onBack }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // iter227 — Surface the paid-challenge entry point on the Quiz solo intro
  // when the admin has enabled it. Fixes the disconnect users saw between
  // /admin (paid mode ON) and /games/quiz (no defy CTA).
  const [toggles, setToggles] = useState(null);
  useEffect(() => {
    axios.get(`${API}/api/games/toggles`)
         .then(({ data }) => setToggles(data))
         .catch(() => {});
  }, []);
  return (
    <div className="min-h-screen flex flex-col"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="quiz-intro">
      <div className="flex items-center p-4">
        <button onClick={onBack} className="p-2 rounded-full text-white"
                style={{ background: 'rgba(255,255,255,0.1)' }}
                data-testid="quiz-back">
          <ArrowLeft size={18} weight="bold" />
        </button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center px-6 text-center text-white">
        <div className="w-24 h-24 rounded-3xl flex items-center justify-center mb-6"
             style={{
               background: 'linear-gradient(135deg, #8B5CF6, #A78BFA)',
               boxShadow: '0 20px 60px rgba(139,92,246,0.5)',
             }}>
          <Question size={44} weight="fill" />
        </div>
        <h1 className="font-['Outfit'] text-4xl font-extrabold mb-2">Quiz JAPAP</h1>
        <p className="text-white/70 text-sm mb-8 max-w-xs">
          5 questions · 10 secondes chrono · +20 pts par bonne réponse · +30 bonus si sans faute
        </p>
        <div className="w-full max-w-sm space-y-2 mb-8 text-[11px]">
          <RuleLine icon="⏱️" text="Timer strict de 10 secondes" />
          <RuleLine icon="🧠" text="Performance ≥ 75% sur ≥50 réponses → débloque le Starter Pro" />
          <RuleLine icon="📊" text="Max 3 sessions / jour" />
        </div>
        <button onClick={onStart} disabled={starting}
                data-testid="quiz-start-btn"
                className="w-full max-w-sm py-4 rounded-full font-bold text-base transition-transform active:scale-[0.97]"
                style={{
                  background: 'linear-gradient(90deg, #FFD700, #F7931A)',
                  color: '#111',
                  boxShadow: '0 10px 36px rgba(255,215,0,0.55)',
                }}>
          {starting ? t('quiz_j_a_p_a_p.preparation') : '▶ Démarrer la session'}
        </button>

        {/* iter227 — Paid challenge CTA, gated by admin toggle. */}
        {toggles?.quiz_challenge_paid_enabled && (
          <button onClick={() => navigate('/games/quiz/challenge/new')}
                  data-testid="quiz-defy-cta"
                  className="w-full max-w-sm mt-3 py-4 rounded-full font-bold text-base transition-transform active:scale-[0.97] flex items-center justify-center gap-2"
                  style={{
                    background: 'linear-gradient(90deg, #E01C2E, #B91C1C)',
                    color: '#fff',
                    boxShadow: '0 10px 36px rgba(224,28,46,0.45)',
                    border: '1px solid rgba(255,255,255,0.10)',
                  }}>
            <span style={{ fontSize: 18 }}>⚔️</span>
            <span>Créer un défi</span>
            <span className="text-[10px] font-bold opacity-80 ml-1 px-2 py-0.5 rounded-full"
                  style={{ background: 'rgba(255,255,255,0.18)' }}>
              Gratuit · {toggles.quiz_challenge_stake_min}–{toggles.quiz_challenge_stake_max} USD
            </span>
          </button>
        )}

        {/* iter233 — Mission 2: Doubleurs Légendaires leaderboard.
            Hidden when no winners yet to avoid pre-launch noise. */}
        <div className="w-full max-w-sm mt-6">
          <DoublersLeaderboard limit={10} />
        </div>
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

function ResultView({ result, run, isDaily = false, onReplay, onBack }) {
  const { t } = useTranslation();
  const pct = Math.round(result.accuracy * 100);
  const color = result.perfect ? '#FFD700' : result.correct_count >= 3 ? '#10B981' : '#E01C2E';
  const [duelToggles, setDuelToggles] = useState({ duel_enabled: true });
  useEffect(() => {
    axios.get(`${API}/api/games/toggles`).then(({ data }) => setDuelToggles(data)).catch(() => {});
  }, []);
  const [creatingDuel, setCreatingDuel] = useState(false);

  // iter120 — Motivating message based on score (FR).
  const motivation = result.perfect
    ? t('quiz_j_a_p_a_p.sans_faute_legendaire_vous_etes_au')
    : result.correct_count === 4
      ? "Excellent ! Si proche du sans-faute — la prochaine sera la bonne 🔥"
      : result.correct_count === 3
        ? t('quiz_j_a_p_a_p.bien_joue_continuez_sur_cette_lance')
        : result.correct_count === 2
          ? "Pas mal ! Une session de plus et vous progresserez vite 🚀"
          : result.correct_count === 1
            ? t('quiz_j_a_p_a_p.chaque_expert_a_commence_ici_reessa')
            : t('quiz_j_a_p_a_p.le_quiz_est_exigeant_l_entrainement');

  const challengeFriend = async () => {
    setCreatingDuel(true);
    try {
      const { data } = await axios.post(`${API}/api/duel/create-from-quiz`,
        { run_id: result.run_id }, { withCredentials: true });
      const shareUrl = `${window.location.origin}${data.share_url}`;
      const text = `🎯 Je viens de faire ${result.correct_count}/5 au Quiz JAPAP — saurez-vous faire mieux ? ${shareUrl}`;
      if (navigator.share) {
        try { await navigator.share({ title: t('quiz_j_a_p_a_p.defi_quiz_japap'), text, url: shareUrl }); }
        catch { /* user cancelled */ }
      } else {
        try { await navigator.clipboard.writeText(shareUrl); toast.success('Lien copié · partagez sur WhatsApp !'); } catch {}
        window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, '_blank');
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Impossible de créer le défi');
    } finally { setCreatingDuel(false); }
  };

  // iter120 — "Partager mon score" — simple share without creating a duel.
  const shareScore = async () => {
    const url = `${window.location.origin}/games/quiz`;
    const txt = result.perfect
      ? `🏆 SANS FAUTE au Quiz JAPAP ! 5/5 et +${result.points_awarded} points engagement. Tentez votre chance : ${url}`
      : `🎯 J'ai marqué ${result.correct_count}/5 au Quiz JAPAP (+${result.points_awarded} pts). Pouvez-vous faire mieux ? ${url}`;
    try {
      if (navigator.share) {
        await navigator.share({ title: 'Mon score Quiz JAPAP', text: txt, url });
      } else {
        await navigator.clipboard.writeText(txt);
        toast.success('Texte copié · collez-le où vous voulez !');
      }
    } catch { /* user cancelled */ }
  };

  return (
    <div className="min-h-screen flex flex-col relative overflow-hidden"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="quiz-result">
      {/* iter120 — Confetti only when perfect */}
      {result.perfect && <Confetti />}
      <div className="flex items-center p-4 relative z-10">
        <button onClick={onBack} className="p-2 rounded-full text-white"
                style={{ background: 'rgba(255,255,255,0.1)' }}>
          <ArrowLeft size={18} weight="bold" />
        </button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center px-6 text-center text-white relative z-10">
        <div className="w-28 h-28 rounded-full flex items-center justify-center mb-5"
             style={{
               background: `linear-gradient(135deg, ${color}, ${color}88)`,
               boxShadow: `0 20px 60px ${color}66`,
               animation: result.perfect ? 'quizPerfectPulse 1.6s ease-in-out infinite' : 'none',
             }}>
          {result.perfect
            ? <Trophy size={52} weight="fill" color="#111" />
            : result.correct_count >= 3
            ? <CheckCircle size={52} weight="fill" color="#fff" />
            : <XCircle size={52} weight="fill" color="#fff" />}
        </div>
        <div className="font-['Outfit'] text-6xl font-extrabold mb-1" style={{ color }}>
          {result.correct_count}/5
        </div>
        <div className="text-white/60 text-sm mb-2">{pct}% de bonnes réponses</div>
        {/* iter120 — Motivating message (FR) */}
        <div className="text-white text-sm font-semibold mb-4 max-w-xs"
             data-testid="quiz-motivation"
             style={{ lineHeight: 1.4 }}>
          {motivation}
        </div>
        {result.timed_out && (
          <div className="text-[11px] mb-3 px-3 py-1 rounded-full"
               style={{ background: '#E01C2E33', color: '#F7931A', border: '1px solid #E01C2E66' }}>
            ⏱️ Temps écoulé — certaines réponses n'ont pas été prises en compte
          </div>
        )}
        {/* iter130 — Daily mode: streak banner */}
        {isDaily && result.streak && (
          <div className="mb-3 px-4 py-2 rounded-full text-xs font-bold flex items-center gap-2"
               data-testid="quiz-daily-streak-banner"
               style={{ background: 'linear-gradient(90deg, #FF6B00, #E01C2E)', color: '#fff' }}>
            <Flame size={14} weight="fill" style={{ color: '#FFE066' }} />
            Série : {result.streak.current_streak} jour{result.streak.current_streak > 1 ? 's' : ''}
            {result.streak.longest_streak > result.streak.current_streak &&
              ` · Record : ${result.streak.longest_streak}`}
          </div>
        )}
        <div className="font-['Outfit'] text-3xl font-bold mb-1 text-[#FFD700]"
             data-testid="quiz-points">
          +{result.points_awarded} points
        </div>
        <div className="text-white/50 text-xs mb-8">Total cycle : {result.points_cycle.toLocaleString('fr-FR')} / 10 000</div>
        {/* iter130 — Daily mode: points breakdown */}
        {isDaily && result.points_breakdown && (
          <div className="mb-3 text-xs text-white/70 font-['Manrope']">
            Base {result.points_breakdown.base}
            {result.points_breakdown.perfect_bonus > 0 && ` + Sans-faute ${result.points_breakdown.perfect_bonus}`}
            {result.points_breakdown.streak_bonus > 0 && ` + Série ${result.points_breakdown.streak_bonus}`}
          </div>
        )}
        {/* iter130 — Daily mode: countdown to next */}
        {isDaily && result.next_eligible_at && (
          <div className="mb-5 text-xs text-white/80 font-['Manrope'] flex items-center gap-1.5"
               data-testid="quiz-daily-next-eligible">
            <Clock size={12} />
            Prochain défi : {new Date(result.next_eligible_at).toLocaleString('fr-FR', {
              dateStyle: 'short', timeStyle: 'short'
            })}
          </div>
        )}
        {result.perfect && (
          <div className="mb-5 text-xs px-3 py-1 rounded-full font-bold"
               style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
            🏆 Bonus sans faute : +30 pts
          </div>
        )}
        <div className="w-full max-w-sm space-y-3">
          {/* iter120 — "Partager mon score" — works for any score, distinct from "Défier un ami" */}
          <button onClick={shareScore}
                  data-testid="quiz-share-score-btn"
                  className="w-full py-3 rounded-full font-bold text-base active:scale-[0.97]"
                  style={{ background: 'linear-gradient(90deg, #10B981, #059669)', color: '#fff',
                           boxShadow: '0 10px 36px rgba(16,185,129,0.55)' }}>
            📣 Partager mon score
          </button>
          {duelToggles.duel_enabled && !isDaily && (
            <button onClick={challengeFriend} disabled={creatingDuel}
                    data-testid="quiz-challenge-friend-btn"
                    className="w-full py-3 rounded-full font-bold text-base active:scale-[0.97]"
                    style={{ background: 'linear-gradient(90deg, #8B5CF6, #E01C2E)', color: '#fff',
                             boxShadow: '0 10px 36px rgba(139,92,246,0.55)' }}>
              ⚔ {creatingDuel ? t('quiz_j_a_p_a_p.creation_du_defi') : t('quiz_j_a_p_a_p.defier_un_ami')}
            </button>
          )}
          {!isDaily && (
            <button onClick={onReplay}
                    data-testid="quiz-replay-btn"
                    className="w-full py-3 rounded-full font-bold text-base active:scale-[0.97]"
                    style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111',
                             boxShadow: '0 10px 36px rgba(255,215,0,0.55)' }}>
              ▶ Nouvelle session
            </button>
          )}
          <button onClick={onBack}
                  data-testid="quiz-back-to-games-btn"
                  className="w-full py-3 rounded-full font-bold text-sm"
                  style={{ background: 'rgba(255,255,255,0.12)', color: '#fff' }}>
            Retour aux jeux
          </button>
        </div>
      </div>
    </div>
  );
}

// iter120 — Pure-CSS confetti for the perfect-score celebration.
function Confetti() {
  const COLORS = ['#FFD700', '#F7931A', '#10B981', '#A78BFA', '#E01C2E', '#fff'];
  const pieces = Array.from({ length: 36 }, (_, i) => {
    const left = Math.random() * 100;
    const delay = Math.random() * 0.8;
    const duration = 2 + Math.random() * 2;
    const color = COLORS[i % COLORS.length];
    const size = 6 + Math.round(Math.random() * 8);
    const rotate = Math.random() * 360;
    return (
      <span key={i}
            style={{
              position: 'absolute',
              top: -20,
              left: `${left}%`,
              width: size,
              height: size * 1.6,
              background: color,
              transform: `rotate(${rotate}deg)`,
              borderRadius: 2,
              animation: `quizConfettiFall ${duration}s ${delay}s linear forwards`,
              boxShadow: `0 0 6px ${color}88`,
              opacity: 0.95,
            }} />
    );
  });
  return (
    <div className="absolute inset-0 pointer-events-none z-20" data-testid="quiz-confetti">
      {pieces}
      <style>{`
        @keyframes quizConfettiFall {
          0%   { transform: translateY(-20px) rotate(0deg);   opacity: 1; }
          80%  { opacity: 1; }
          100% { transform: translateY(110vh) rotate(720deg); opacity: 0; }
        }
        @keyframes quizPerfectPulse {
          0%, 100% { transform: scale(1);    box-shadow: 0 20px 60px rgba(255,215,0,0.55); }
          50%      { transform: scale(1.06); box-shadow: 0 28px 80px rgba(255,215,0,0.85); }
        }
      `}</style>
    </div>
  );
}
