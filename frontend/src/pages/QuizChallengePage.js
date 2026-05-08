/**
 * QuizChallengePage — single challenge view: detail / play / result.
 * Re-uses the core Quiz UX (timer, ✅/❌, sounds, confetti) for the play step.
 */
import React, { useEffect, useState, useCallback, useRef } from 'react';
import axios from 'axios';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Crown, Sword, CheckCircle, XCircle, Trophy, Coin, ClockCountdown } from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import { toast } from 'sonner';
import ChallengeShare from '@/components/games/ChallengeShare';

const API = process.env.REACT_APP_BACKEND_URL;

function flag(cc) {
  if (!cc || cc.length !== 2) return '🏳️';
  return cc.toUpperCase().replace(/./g, c => String.fromCodePoint(127397 + c.charCodeAt(0)));
}

// Simple Web Audio API tone (mirrors QuizJAPAPPage helpers but standalone)
let _audioCtx = null;
function _ac() {
  if (typeof window === 'undefined') return null;
  if (!_audioCtx) {
    try { _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch { return null; }
  }
  return _audioCtx;
}
function playTone(freq, dur = 0.18, type = 'sine', vol = 0.07) {
  if (localStorage.getItem('quiz_muted') === '1') return;
  const ac = _ac(); if (!ac) return;
  const o = ac.createOscillator(); const g = ac.createGain();
  o.type = type; o.frequency.value = freq;
  g.gain.value = vol; o.connect(g); g.connect(ac.destination);
  o.start(); o.stop(ac.currentTime + dur);
}

export default function QuizChallengePage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { id: cid } = useParams();
  const [ch, setCh] = useState(null);
  const [view, setView] = useState('detail'); // detail | play | result
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/quiz/champion/challenges/${cid}`,
        { withCredentials: true });
      setCh(data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Défi introuvable');
      navigate('/games/quiz/challenges');
    }
  }, [cid, navigate]);

  useEffect(() => { load(); }, [load]);

  const accept = async () => {
    setBusy(true);
    try {
      await axios.post(`${API}/api/quiz/champion/challenge/${cid}/accept`,
        {}, { withCredentials: true });
      toast.success('Défi accepté !');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Impossible d\'accepter');
    } finally { setBusy(false); }
  };

  const refuse = async () => {
    if (!confirm('Êtes-vous sûr de refuser ce défi ?')) return;
    setBusy(true);
    try {
      await axios.post(`${API}/api/quiz/champion/challenge/${cid}/refuse`,
        {}, { withCredentials: true });
      toast.success('Défi refusé.');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Impossible de refuser');
    } finally { setBusy(false); }
  };

  const startPlay = async () => {
    setBusy(true);
    try {
      const { data } = await axios.post(`${API}/api/quiz/champion/challenge/${cid}/play`,
        {}, { withCredentials: true });
      setRun(data);
      setView('play');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Impossible de jouer');
    } finally { setBusy(false); }
  };

  if (!ch) return <div className="min-h-screen flex items-center justify-center text-white/60">Chargement…</div>;

  if (view === 'play' && run) {
    return <ChallengePlay run={run} cid={cid}
                          onDone={(result) => {
                            setView('result');
                            load();
                          }} />;
  }

  return <ChallengeDetail ch={ch} userId={user?.user_id}
                          onBack={() => navigate('/games/quiz/challenges')}
                          onAccept={accept}
                          onRefuse={refuse}
                          onPlay={startPlay}
                          busy={busy} />;
}

function ChallengeDetail({ ch, userId, onBack, onAccept, onRefuse, onPlay, busy }) {
  const isChallenger = ch.role === 'challenger';
  const isChampion = ch.role === 'champion';
  const isPaid = ch.mode === 'paid';
  const myScore = isChallenger ? ch.challenger_score : ch.champion_score;
  const oppScore = isChallenger ? ch.champion_score : ch.challenger_score;
  const won = ch.status === 'completed' && ch.winner_user_id === userId;
  const tied = ch.status === 'completed' && ch.winner_user_id === null;
  const lost = ch.status === 'completed' && ch.winner_user_id && ch.winner_user_id !== userId;
  const stake = Number(ch.stake_amount || 0);
  // iter233 — Mission 2: a doubled challenge has a per-side lock of 2× stake,
  // so the actual pot is 4× stake (vs 2× for a regular paid challenge).
  const isDoubled = !!ch.doubled;
  const pot = isDoubled ? stake * 4 : stake * 2;
  const commissionPct = Number(ch.commission_pct || 10);
  const commission = (pot * commissionPct) / 100;
  const winnings   = pot - commission;
  const balance    = Number(ch.viewer_balance || 0);
  const insufficient = isPaid && isChampion && ch.status === 'pending' && balance < stake;
  const opponent = isChallenger ? ch.champion : ch.challenger;

  // iter233 — Mission 2: viral toast on a ×4 victory.
  // Fires exactly once per resolved challenge (challenge_id keyed).
  React.useEffect(() => {
    if (!won || !isDoubled || !isPaid) return;
    const key = `jp_doubler_toast_${ch.challenge_id}`;
    if (typeof window === 'undefined') return;
    if (window.localStorage?.getItem(key)) return;
    try { window.localStorage?.setItem(key, '1'); } catch { /* ignore quota */ }
    const amountWon = Math.max(0, winnings).toLocaleString('fr-FR', { maximumFractionDigits: 2 });
    const shareUrl = `${window.location.origin}/c/${ch.challenge_id}`;
    const waText = encodeURIComponent(
      `💎 Je viens de gagner ${amountWon} ${ch.stake_currency || 'USD'} en doublant ma mise sur JAPAP ! Défie-moi : ${shareUrl}`,
    );
    const waUrl = `https://wa.me/?text=${waText}`;
    toast.custom((id) => (
      <div className="p-4 rounded-2xl text-center shadow-2xl"
           style={{ background: 'linear-gradient(135deg, #FFD700, #F7931A)', color: '#111', minWidth: 280 }}
           data-testid="doubler-victory-toast">
        <div className="text-2xl mb-1">🏆</div>
        <div className="font-extrabold text-base mb-1">+{amountWon} {ch.stake_currency || 'USD'} encaissés !</div>
        <div className="text-xs mb-3 opacity-80">Victoire en doublant la mise 💎</div>
        <a href={waUrl} target="_blank" rel="noopener noreferrer"
           data-testid="doubler-victory-share-wa"
           onClick={() => toast.dismiss(id)}
           className="inline-block px-4 py-2 rounded-xl font-bold text-sm"
           style={{ background: '#25D366', color: 'white' }}>
          📲 Partager ma victoire
        </a>
      </div>
    ), { duration: 12000 });
  }, [won, isDoubled, isPaid, ch.challenge_id, ch.stake_currency, winnings]);

  return (
    <div className="min-h-screen pb-24 relative" style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="quiz-challenge-detail">
      {won && <ConfettiBurst />}
      <div className="flex items-center p-4 gap-2">
        <button onClick={onBack} className="p-2 rounded-full text-white"
                style={{ background: 'rgba(255,255,255,0.1)' }}
                data-testid="quiz-challenge-back">
          <ArrowLeft size={18} weight="bold" />
        </button>
        <h1 className="text-white font-['Outfit'] text-lg font-bold flex-1 truncate">
          {flag(ch.country_code)} Défi {ch.country_code}
        </h1>
        <div className="text-[10px] uppercase font-bold px-2 py-1 rounded-full"
             style={{ background: isPaid ? '#FFD700' : '#10B981', color: '#111' }}>
          {isPaid ? `${stake} ${ch.stake_currency}` : 'Gratuit'}
        </div>
      </div>

      {/* Status banner */}
      {ch.status === 'pending' && (
        <BannerBig icon={<ClockCountdown size={28} weight="fill" color="#F7931A" />}
                   title={isChallenger ? 'En attente du champion' : 'Un challenger vous défie'}
                   subtitle={isChallenger ? 'Le champion a 24h pour accepter ou refuser.' : 'Acceptez ou refusez ci-dessous.'} />
      )}
      {/* iter231 — awaiting_acceptor banner (was missing → blank screen for A
          right after creating an open challenge via /games/quiz/challenge/new) */}
      {ch.status === 'awaiting_acceptor' && (
        <BannerBig icon={<Sword size={28} weight="fill" color="#FFD700" />}
                   title={isChallenger
                            ? (ch.challenger_run_id
                                 ? 'Défi prêt à partager'
                                 : '🎯 À toi de jouer en premier')
                            : 'Défi ouvert'}
                   subtitle={isChallenger
                                ? (ch.challenger_run_id
                                     ? 'Partage le lien — n\'importe quel joueur peut accepter et tenter de battre ton score.'
                                     : 'Joue tes 5 questions, puis tu pourras partager le lien pour qu\'un ami relève ton défi.')
                                : 'Ce défi attend qu\'un joueur l\'accepte.'} />
      )}
      {ch.status === 'accepted' && (
        <BannerBig icon={<Sword size={28} weight="fill" color="#A78BFA" />}
                   title="Défi en cours"
                   subtitle="5 questions identiques pour les deux joueurs. Le meilleur score gagne." />
      )}
      {(ch.status === 'challenger_played' || ch.status === 'champion_played') && (
        <BannerBig icon={<ClockCountdown size={28} weight="fill" color="#A78BFA" />}
                   title="En attente du second joueur"
                   subtitle="Dès que les deux ont joué, le résultat sera révélé." />
      )}
      {/* iter228 — Share invite when A has played and the challenge is open. */}
      {isChallenger && ch.status === 'challenger_played' && !ch.champion_user_id && (
        <ShareInviteCard cid={ch.challenge_id}
                          stake={Number(ch.stake_amount || 0)}
                          stakeCurrency={ch.stake_currency} mode={ch.mode} />
      )}
      {won && <BannerBig icon={<Trophy size={28} weight="fill" color="#FFD700" />} title="🏆 Vous avez gagné !" subtitle={isPaid ? `Pot ${pot} ${ch.stake_currency} crédité (commission JAPAP déduite).` : 'Bonus +30 pts engagement crédité.'} />}
      {tied && <BannerBig icon={<Sword size={28} weight="fill" color="#A78BFA" />} title="Égalité — bien joué" subtitle={isPaid ? 'Mises remboursées de part et d\'autre.' : 'Match nul. Aucun gagnant.'} />}
      {lost && <BannerBig icon={<XCircle size={28} weight="fill" color="#E01C2E" />} title="Défaite" subtitle="L'adversaire a fait mieux. Réessayez !" />}
      {ch.status === 'refused' && <BannerBig icon={<XCircle size={28} weight="fill" color="#E01C2E" />} title="Défi refusé" subtitle={isChallenger ? 'Mise remboursée + bonus engagement crédité.' : 'Vous avez refusé ce défi.'} />}
      {ch.status === 'expired' && <BannerBig icon={<ClockCountdown size={28} weight="fill" color="#666" />} title="Défi expiré" subtitle="Le délai de 24h est dépassé. Mises remboursées si applicable." />}

      {/* iter225 — Stake breakdown for the Champion before accepting. */}
      {isPaid && isChampion && ch.status === 'pending' && (
        <div className="mx-4 mt-3 p-4 rounded-2xl"
             data-testid="challenge-stake-breakdown"
             style={{ background: 'rgba(255,215,0,0.08)',
                      border: '1px solid rgba(255,215,0,0.30)' }}>
          {/* Challenger identity */}
          <div className="flex items-center gap-3 pb-3 mb-3"
               style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            {opponent?.avatar_url ? (
              <img src={opponent.avatar_url} alt={opponent.name}
                   className="w-10 h-10 rounded-full object-cover"
                   style={{ border: '2px solid #FFD700' }} />
            ) : (
              <div className="w-10 h-10 rounded-full flex items-center justify-center font-bold text-white"
                   style={{ background: 'linear-gradient(135deg,#FFD700,#F7931A)' }}>
                {(opponent?.name || '?').charAt(0).toUpperCase()}
              </div>
            )}
            <div className="flex-1 min-w-0">
              <div className="text-[10px] uppercase tracking-wider text-white/50 font-bold">Défi de</div>
              <div className="text-white font-bold truncate" data-testid="challenge-opponent-name">
                {opponent?.name || 'Joueur'}
              </div>
            </div>
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-wider text-white/50 font-bold">5 questions</div>
              <div className="text-white/80 text-xs">expire dans 24h</div>
            </div>
          </div>
          {/* Stake required */}
          <div className="text-center mb-3">
            <div className="text-[10px] uppercase tracking-wider text-white/60 font-bold mb-1">
              💰 Mise requise
            </div>
            <div className="text-[#FFD700] font-['Outfit'] text-3xl font-extrabold"
                 data-testid="challenge-stake-amount">
              {stake.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} {ch.stake_currency || 'USD'}
            </div>
          </div>
          {/* Breakdown */}
          <div className="space-y-1 text-xs mb-3 p-2.5 rounded-lg"
               style={{ background: 'rgba(255,255,255,0.04)' }}>
            <div className="flex justify-between text-white/80">
              <span>🏆 Pot total</span>
              <span className="font-bold">{pot.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} {ch.stake_currency}</span>
            </div>
            <div className="flex justify-between text-white/60">
              <span>📊 Commission JAPAP ({commissionPct}%)</span>
              <span>−{commission.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} {ch.stake_currency}</span>
            </div>
            <div className="flex justify-between text-white pt-1 border-t border-white/10">
              <span className="font-bold">✅ Tes gains si tu gagnes</span>
              <span className="font-bold text-[#10B981]" data-testid="challenge-net-winnings">
                +{winnings.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} {ch.stake_currency}
              </span>
            </div>
          </div>
          {/* Wallet warning */}
          <div className="p-2.5 rounded-lg text-xs"
               data-testid="challenge-balance-info"
               style={{ background: insufficient ? 'rgba(224,28,46,0.10)' : 'rgba(16,185,129,0.08)',
                        border: `1px solid ${insufficient ? 'rgba(224,28,46,0.40)' : 'rgba(16,185,129,0.30)'}` }}>
            {insufficient ? (
              <div className="text-[#FCA5A5]">
                ⚠️ <strong>{stake} {ch.stake_currency}</strong> seraient débités de ton wallet dès l'acceptation.
                <div className="mt-1 text-[#FCA5A5]/80">
                  Ton solde : {balance.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} {ch.stake_currency} —
                  <strong> Solde insuffisant — recharge ton wallet.</strong>
                </div>
              </div>
            ) : (
              <div className="text-white/80">
                ⚠️ <strong>{stake} {ch.stake_currency}</strong> seront débités dès acceptation ·
                Solde : <strong className="text-[#10B981]">{balance.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} {ch.stake_currency}</strong>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Score */}
      {ch.status === 'completed' && (
        <div className="px-4 mt-3">
          <div className="rounded-2xl p-5 grid grid-cols-3 items-center text-center"
               style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.12)' }}>
            <div>
              <div className="text-white/50 text-[10px] uppercase font-bold">Vous</div>
              <div className="text-white font-['Outfit'] text-4xl font-extrabold mt-1"
                   style={{ color: won ? '#FFD700' : tied ? '#A78BFA' : '#fff' }}>
                {myScore ?? '−'}
              </div>
            </div>
            <div className="text-white/40 text-2xl">vs</div>
            <div>
              <div className="text-white/50 text-[10px] uppercase font-bold">Adversaire</div>
              <div className="text-white font-['Outfit'] text-4xl font-extrabold mt-1"
                   style={{ color: lost ? '#FFD700' : '#fff' }}>
                {oppScore ?? '−'}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Action buttons */}
      <div className="px-4 mt-5 space-y-2">
        {isChampion && ch.status === 'pending' && (
          <>
            <button onClick={onAccept} disabled={busy || insufficient}
                    data-testid="quiz-challenge-accept"
                    className="w-full py-3 rounded-full font-bold text-base disabled:opacity-50"
                    style={{ background: insufficient
                              ? 'rgba(224,28,46,0.30)'
                              : 'linear-gradient(90deg, #10B981, #059669)',
                             color: '#fff' }}>
              {insufficient
                ? 'Solde insuffisant'
                : `✓ Accepter et miser ${isPaid ? `(${stake} ${ch.stake_currency})` : ''}`}
            </button>
            <button onClick={onRefuse} disabled={busy}
                    data-testid="quiz-challenge-refuse"
                    className="w-full py-3 rounded-full font-bold text-base"
                    style={{ background: 'rgba(224,28,46,0.18)', color: '#E01C2E', border: '1px solid #E01C2E' }}>
              ✗ Refuser
            </button>
            <div className="text-white/40 text-[10px] text-center mt-2">
              Refuser augmente votre compteur de refus. 5 refus = démotion automatique.
            </div>
          </>
        )}
        {ch.can_play && (
          <button onClick={onPlay} disabled={busy}
                  data-testid="quiz-challenge-play"
                  className="w-full py-3 rounded-full font-bold text-base"
                  style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111',
                           boxShadow: '0 12px 36px rgba(255,215,0,0.55)' }}>
            ▶ Jouer mes 5 questions
          </button>
        )}
      </div>

      {/* iter128 — Viral share on completed/refused/expired (any user-visible result) */}
      {(ch.status === 'completed' || (ch.status === 'refused' && isChallenger)
                                   || (ch.status === 'expired'  && isChallenger)) && (
        <ChallengeShare challenge={ch} userId={userId} />
      )}
    </div>
  );
}

function BannerBig({ icon, title, subtitle }) {
  return (
    <div className="mx-4 mt-2 mb-3 p-4 rounded-2xl flex items-center gap-3"
         style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.12)' }}>
      {icon}
      <div className="flex-1 min-w-0">
        <div className="text-white font-bold text-sm">{title}</div>
        <div className="text-white/60 text-[11px] mt-0.5">{subtitle}</div>
      </div>
    </div>
  );
}

// Pure-CSS confetti reused from Phase 2 (shorter — local helper).
function ConfettiBurst() {
  const COLORS = ['#FFD700', '#F7931A', '#10B981', '#A78BFA', '#E01C2E', '#fff'];
  const pieces = Array.from({ length: 30 }, (_, i) => {
    const left = Math.random() * 100;
    const delay = Math.random() * 0.6;
    const duration = 2 + Math.random() * 2;
    const color = COLORS[i % COLORS.length];
    const size = 6 + Math.round(Math.random() * 8);
    const rotate = Math.random() * 360;
    return (
      <span key={i}
            style={{
              position: 'absolute', top: -20, left: `${left}%`,
              width: size, height: size * 1.6, background: color,
              transform: `rotate(${rotate}deg)`, borderRadius: 2,
              animation: `qcConfettiFall ${duration}s ${delay}s linear forwards`,
              boxShadow: `0 0 6px ${color}88`, opacity: 0.95,
            }} />
    );
  });
  return (
    <div className="absolute inset-0 pointer-events-none z-20">
      {pieces}
      <style>{`
        @keyframes qcConfettiFall {
          0%   { transform: translateY(-20px) rotate(0deg);   opacity: 1; }
          80%  { opacity: 1; }
          100% { transform: translateY(110vh) rotate(720deg); opacity: 0; }
        }
      `}</style>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Play flow
// ─────────────────────────────────────────────────────────────────────

function ChallengePlay({ run, cid, onDone }) {
  const [idx, setIdx] = useState(0);
  const [answers, setAnswers] = useState([]);
  const [feedback, setFeedback] = useState(null); // {choice, correct, correctOption}
  const [secondsLeft, setSecondsLeft] = useState(run.timer_per_question_seconds || 15);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const tickRef = useRef(null);

  const total = run.questions.length;
  const q = run.questions[idx];

  // Timer per question
  useEffect(() => {
    setSecondsLeft(run.timer_per_question_seconds || 15);
    if (tickRef.current) clearInterval(tickRef.current);
    tickRef.current = setInterval(() => setSecondsLeft(s => Math.max(0, s - 1)), 1000);
    return () => clearInterval(tickRef.current);
  }, [idx, run.timer_per_question_seconds]);

  useEffect(() => {
    if (secondsLeft === 0 && feedback === null && !submitting && !done) {
      // Auto pick a "no answer" → use index 0 placeholder; backend will mark wrong
      pick(0, true);
    }
  }, [secondsLeft]);

  const pick = async (optionIdx, timedOut = false) => {
    if (feedback || submitting || done) return;
    setFeedback({ choice: optionIdx, correct: null, correctOption: null });
    let learningOpt = null;
    let isCorrect = null;
    try {
      // We could call /api/quiz/answer but the champion endpoint doesn't have
      // it. Skip live feedback here — score reveal at end. Simpler & safe.
      isCorrect = null;
    } catch {}
    setAnswers(arr => { const next = [...arr]; next[idx] = optionIdx; return next; });
    // Advance after a short delay
    const delay = (run.auto_advance_delays_ms && run.auto_advance_delays_ms[idx]) || 700;
    setTimeout(async () => {
      setFeedback(null);
      if (idx + 1 < total) {
        setIdx(idx + 1);
      } else {
        // Submit
        setSubmitting(true);
        try {
          const finalAnswers = [...answers]; finalAnswers[idx] = optionIdx;
          while (finalAnswers.length < total) finalAnswers.push(0);
          const { data } = await axios.post(`${API}/api/quiz/champion/challenge/${cid}/submit`,
            { answers: finalAnswers.slice(0, total) }, { withCredentials: true });
          setDone(true);
          // Quick celebration tone
          playTone(800, 0.18, 'sine', 0.06);
          setTimeout(() => onDone(data), 800);
        } catch (e) {
          toast.error(e.response?.data?.detail || 'Erreur de soumission');
          onDone(null);
        } finally { setSubmitting(false); }
      }
    }, delay);
  };

  const pct = ((idx + 1) / total) * 100;

  return (
    <div className="min-h-screen flex flex-col"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="quiz-challenge-play">
      <div className="p-4 flex items-center gap-3">
        <div className="text-white text-sm font-bold">Q{idx + 1}/{total}</div>
        <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.1)' }}>
          <div className="h-full transition-all"
               style={{ width: `${pct}%`, background: 'linear-gradient(90deg, #FFD700, #F7931A)' }} />
        </div>
        <div className={`text-sm font-bold ${secondsLeft <= 3 ? 'text-[#E01C2E]' : 'text-white'}`}
             data-testid="quiz-challenge-timer">
          {secondsLeft}s
        </div>
      </div>

      <div className="flex-1 px-5">
        <div className="text-white text-xs uppercase tracking-wider opacity-60 mb-1">{q.category}</div>
        <div className="text-white font-['Outfit'] text-xl font-bold mb-5 leading-tight">
          {q.text}
        </div>
        <div className="grid grid-cols-1 gap-3">
          {q.options.map((opt, i) => {
            const selected = feedback?.choice === i;
            return (
              <button key={i}
                      onClick={() => pick(i)}
                      disabled={!!feedback}
                      data-testid={`quiz-challenge-option-${idx}-${i}`}
                      className="p-4 rounded-xl text-left font-semibold transition-all active:scale-[0.98] disabled:opacity-90"
                      style={{
                        background: selected ? 'linear-gradient(90deg, #A78BFA, #8B5CF6)' : 'rgba(255,255,255,0.08)',
                        color: '#fff',
                        border: `2px solid ${selected ? '#A78BFA' : 'rgba(255,255,255,0.15)'}`,
                      }}>
                <span className="inline-block mr-3 w-7 h-7 rounded-full text-xs leading-[28px] text-center font-bold"
                      style={{ background: '#fff', color: '#8B5CF6' }}>
                  {String.fromCharCode(65 + i)}
                </span>
                {opt}
              </button>
            );
          })}
        </div>
      </div>

      {(submitting || done) && (
        <div className="text-center py-5 text-white/70 text-sm">
          {submitting ? 'Validation des réponses…' : 'Score enregistré ✓'}
        </div>
      )}
    </div>
  );
}



/**
 * ShareInviteCard — iter228
 *
 * Displayed when A just finished playing an OPEN challenge (champion not
 * yet claimed). Shows the public link + WhatsApp share + Copy buttons so A
 * can recruit B from his contacts.
 */
function ShareInviteCard({ cid, stake, stakeCurrency, mode }) {
  const origin = (typeof window !== 'undefined') ? window.location.origin : '';
  // iter229 — Share the OG URL (not /c/{cid}). Crawlers fetch the meta
  // tags directly; real users get meta-refreshed to /c/{cid} in ~50ms.
  const link = `${origin}/api/og/challenge/${cid}`;
  const isPaid = mode === 'paid';
  const text = encodeURIComponent(
    isPaid
      ? `⚔️ Je viens de jouer un défi Quiz JAPAP avec ${stake} ${stakeCurrency || 'USD'} en jeu. Bats mon score et empoche le pot ! ${link}`
      : `⚔️ Je viens de jouer un défi Quiz JAPAP — bats mon score si tu peux ! ${link}`
  );
  const wa = `https://wa.me/?text=${text}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      toast.success('Lien copié — colle-le où tu veux !');
    } catch {
      toast.error('Impossible de copier le lien.');
    }
  };
  return (
    <div className="mx-4 mt-3 p-4 rounded-2xl"
         data-testid="share-invite-card"
         style={{ background: 'linear-gradient(135deg, rgba(255,215,0,0.10), rgba(247,147,26,0.06))',
                  border: '1px solid rgba(255,215,0,0.30)' }}>
      <div className="flex items-center gap-2 mb-2">
        <span style={{ fontSize: 20 }}>📲</span>
        <div className="text-white font-bold text-sm">
          Maintenant, invite un ami à relever ton défi !
        </div>
      </div>
      <div className="text-white/70 text-xs mb-3">
        {isPaid
          ? `Mise : ${stake} ${stakeCurrency || 'USD'} · Lien valable 24h`
          : `Mode gratuit · Lien valable 24h`}
      </div>
      <div className="rounded-lg px-3 py-2 mb-3 flex items-center gap-2 break-all"
           data-testid="share-invite-link"
           style={{ background: 'rgba(0,0,0,0.20)', border: '1px solid rgba(255,255,255,0.10)' }}>
        <span className="text-[#FFD700] text-xs font-mono flex-1 truncate">{link}</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <a href={wa} target="_blank" rel="noopener noreferrer"
           data-testid="share-invite-whatsapp"
           className="py-2.5 rounded-full text-sm font-bold text-center flex items-center justify-center gap-1.5"
           style={{ background: '#25D366', color: '#fff' }}>
          📲 WhatsApp
        </a>
        <button onClick={copy}
                data-testid="share-invite-copy"
                className="py-2.5 rounded-full text-sm font-bold flex items-center justify-center gap-1.5"
                style={{ background: 'rgba(255,255,255,0.10)', color: '#fff',
                         border: '1px solid rgba(255,255,255,0.18)' }}>
          🔗 Copier
        </button>
      </div>
    </div>
  );
}
