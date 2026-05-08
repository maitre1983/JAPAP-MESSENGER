/**
 * iter237k — Daily Challenge PAID — modal/flow utilisateur.
 *
 * UX :
 *   1. Modal de mise (saisie USD + équivalent devise locale + tableau gains/pertes).
 *   2. Modal de jeu (5 questions, 10 sec/question, feedback immédiat avec
 *      explication IA après chaque mauvaise réponse).
 *   3. Modal de résultat (score, delta wallet, encadré encouragement, partage).
 *
 * Strictement additif. Le mode gratuit reste piloté ailleurs.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  CurrencyDollar, X, Lightning, CheckCircle, XCircle, Lightbulb, Trophy, ArrowRight,
} from '@phosphor-icons/react';
import WalletDepositCurrencySelector from '@/components/wallet/WalletDepositCurrencySelector';
// iter237n — CGJ pre-accept gate before first paid wager.
import CgjAcceptanceModal from '@/components/legal/CgjAcceptanceModal';

const API = process.env.REACT_APP_BACKEND_URL;

const BAREME_LABELS = [
  { key: '5',   label: '5/5',   icon: '🏆',  isWin: true  },
  { key: '4',   label: '4/5',   icon: '😊',  isWin: false },
  { key: '3',   label: '3/5',   icon: '😐',  isWin: false },
  { key: '2',   label: '2/5',   icon: '😟',  isWin: false },
  { key: '0_1', label: '0-1/5', icon: '💀',  isWin: false },
];

export default function PaidDailyChallengeFlow({ open, onClose, onPlayed }) {
  const [phase, setPhase] = useState('stake'); // stake | playing | result
  const [config, setConfig] = useState(null);
  const [stakeUsd, setStakeUsd] = useState('1');
  const [localPreview, setLocalPreview] = useState(null);
  const [starting, setStarting] = useState(false);
  // iter237l — anti-tilt redemption toggle
  const [useBonus, setUseBonus] = useState(false);
  // iter237n — CGJ acceptance gate (HTTP 451 from /paid/start).
  const [cgjModalOpen, setCgjModalOpen] = useState(false);

  // Game state
  const [session, setSession] = useState(null); // { session_id, questions[], time_per_question }
  const [qIdx, setQIdx] = useState(0);
  const [answers, setAnswers] = useState([]);
  const [revealed, setRevealed] = useState(null); // { is_correct, correct_idx, explanation }
  const [secondsLeft, setSecondsLeft] = useState(0);
  const timerRef = useRef(null);

  const [result, setResult] = useState(null); // { score, amount_delta_usd, results[] }
  const [submitting, setSubmitting] = useState(false);

  // ── Load config when modal opens ─────────────────────────────────────────
  useEffect(() => {
    if (!open) return;
    setPhase('stake');
    setSession(null); setQIdx(0); setAnswers([]); setResult(null); setRevealed(null);
    axios.get(`${API}/api/quiz/daily-challenge/paid/config`, { withCredentials: true })
      .then(r => setConfig(r.data))
      .catch(e => {
        toast.error(e?.response?.data?.detail || 'Mode payant indisponible.');
        onClose?.();
      });
  }, [open, onClose]);

  // ── Timer per question ───────────────────────────────────────────────────
  useEffect(() => {
    if (phase !== 'playing' || revealed) return;
    if (!session) return;
    setSecondsLeft(session.time_per_question || 10);
    timerRef.current = setInterval(() => {
      setSecondsLeft(s => {
        if (s <= 1) {
          clearInterval(timerRef.current);
          autoSkip();
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, qIdx, revealed, session]);

  const stakeNum = parseFloat(stakeUsd) || 0;
  const stakeMin = config?.stake_min || 0.1;
  const stakeMax = config?.stake_max || 1000;
  const canStart = config?.enabled && !config?.played_today && !config?.cap_reached
                && stakeNum >= stakeMin && stakeNum <= stakeMax
                && (config?.pool_size || 0) >= 5;

  const startGame = async () => {
    setStarting(true);
    try {
      const { data } = await axios.post(
        `${API}/api/quiz/daily-challenge/paid/start`,
        { stake_usd: stakeNum, use_bonus: !!useBonus },
        { withCredentials: true });
      setSession(data);
      setQIdx(0);
      setAnswers([]);
      setPhase('playing');
    } catch (e) {
      // iter237n — HTTP 451: user must accept the CGJ first. Open the
      // acceptance modal and stay on the stake phase. After acceptance,
      // the modal will call onAccepted() → we retry startGame().
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail;
      const code = (typeof detail === 'object' && detail?.code) || null;
      if (status === 451 || code === 'cgje_required') {
        setCgjModalOpen(true);
        return;
      }
      const msg = (typeof detail === 'string' ? detail : detail?.message)
                || 'Échec du démarrage.';
      toast.error(msg);
    } finally { setStarting(false); }
  };

  const answerQuestion = (idx) => {
    if (revealed) return;
    clearInterval(timerRef.current);
    const newAnswers = [...answers, idx];
    setAnswers(newAnswers);
    // We DON'T have the correct answer client-side — we show it only at the
    // end when /submit returns. To still give immediate feedback as requested
    // by user, we lock the UI and let the user click "Suivant" — the
    // explanation will be revealed in the final result screen alongside
    // the global score (server-validated). For per-question explanation
    // shown immediately, we'd need a /reveal endpoint that returns the
    // correct answer + explanation server-side. Adding it here.
    revealAnswer(session.questions[qIdx].id, idx);
  };

  const revealAnswer = async (questionId, userAnswer) => {
    try {
      const { data } = await axios.post(
        `${API}/api/quiz/daily-challenge/paid/reveal`,
        { session_id: session.session_id, question_id: questionId, user_answer: userAnswer },
        { withCredentials: true });
      setRevealed(data);
    } catch {
      // Fallback: no reveal endpoint available — explanation will only
      // show on final result screen.
      setRevealed({ is_correct: null, correct_idx: null, explanation: '' });
    }
  };

  const autoSkip = () => {
    if (revealed) return;
    const newAnswers = [...answers, -1]; // -1 means timeout / not answered
    setAnswers(newAnswers);
    revealAnswer(session.questions[qIdx].id, -1);
  };

  const nextQuestion = async () => {
    setRevealed(null);
    if (qIdx + 1 >= (session.questions?.length || 0)) {
      await submitAll();
      return;
    }
    setQIdx(qIdx + 1);
  };

  const submitAll = async () => {
    setSubmitting(true);
    try {
      const { data } = await axios.post(
        `${API}/api/quiz/daily-challenge/paid/submit`,
        { session_id: session.session_id, answers },
        { withCredentials: true });
      setResult(data);
      setPhase('result');
      onPlayed?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Échec de la soumission.');
    } finally { setSubmitting(false); }
  };

  const close = () => {
    clearInterval(timerRef.current);
    onClose?.();
  };

  if (!open) return null;

  // ── Stake phase ────────────────────────────────────────────────────────
  if (phase === 'stake' && config) {
    return (
      <Modal onClose={close} testId="paid-daily-stake-modal">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-xl font-extrabold flex items-center gap-2"
              style={{ color: '#0F056B' }}>
            <CurrencyDollar size={22} weight="duotone" />
            Choisir votre mise
          </h3>
          <button onClick={close} data-testid="paid-stake-close"
                  className="p-1 rounded-full hover:bg-gray-100">
            <X size={18} />
          </button>
        </div>

        {config.played_today && (
          <div className="p-3 rounded-xl text-sm mb-3"
               style={{ background: '#FEF3C7', color: '#92400E' }}
               data-testid="paid-already-played">
            Tu as déjà joué le défi payant aujourd'hui. Reviens demain !
          </div>
        )}

        {config.cap_reached && (
          <div className="p-3 rounded-xl text-sm mb-3"
               style={{ background: '#FEE2E2', color: '#991B1B' }}
               data-testid="paid-cap-reached">
            Tu as atteint le plafond de gain du jour
            ({Number(config.daily_gain_cap).toLocaleString('fr-FR')} USD).
            Reviens demain !
          </div>
        )}

        {(config.pool_size || 0) < 5 && !config.played_today && !config.cap_reached && (
          <div className="p-3 rounded-xl text-sm mb-3"
               style={{ background: '#E0E7FF', color: '#3730A3' }}
               data-testid="paid-pool-warming">
            Le pool de questions est en cours de renouvellement.
            Réessaie dans quelques minutes.
          </div>
        )}

        <label className="jp-label">Montant à miser (USD)</label>
        <input type="number" min={stakeMin} max={stakeMax} step="0.1"
               value={stakeUsd}
               onChange={e => setStakeUsd(e.target.value)}
               className="jp-input text-base"
               placeholder={`${stakeMin} — ${stakeMax}`}
               data-testid="paid-stake-input"
               disabled={config.played_today || config.cap_reached} />

        <div className="mt-3" data-testid="paid-stake-currency-block">
          <WalletDepositCurrencySelector
            amountUsd={stakeNum}
            onChange={(p) => setLocalPreview(p)} />
        </div>

        {/* Tableau gains/pertes — iter237m : 3 colonnes lisibles, % chargés depuis admin config en temps réel */}
        <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: 'var(--jp-border)' }}>
          <div className="px-3 py-2"
               style={{ background: 'rgba(15, 5, 107, 0.04)' }}>
            <p className="text-xs font-bold uppercase tracking-wider"
               style={{ color: 'var(--jp-text-muted)' }}>
              Si tu mises {stakeNum > 0 ? stakeNum.toFixed(2) : '?'} USD
            </p>
          </div>
          <table className="w-full text-sm" data-testid="paid-stake-bareme">
            <thead>
              <tr className="text-[10px] uppercase tracking-wider"
                  style={{ color: 'var(--jp-text-muted)',
                           borderBottom: '1px solid var(--jp-border)' }}>
                <th className="text-left px-3 py-1.5 font-bold">Score</th>
                <th className="text-center px-2 py-1.5 font-bold">% mise</th>
                <th className="text-right px-3 py-1.5 font-bold">Δ Wallet</th>
              </tr>
            </thead>
            <tbody>
              {BAREME_LABELS.map(({ key, label, icon, isWin }, i) => {
                const pct = config?.score_pct?.[key] || 0;
                const delta = (stakeNum || 0) * pct / 100;
                const positive = delta > 0;
                const isLast = i === BAREME_LABELS.length - 1;
                return (
                  <tr key={key} data-testid={`paid-bareme-row-${key}`}
                      style={{ borderBottom: isLast ? 'none' : '1px solid rgba(0,0,0,0.04)' }}>
                    <td className="px-3 py-2">
                      <span className="font-bold">{label}</span>
                      <span className="ml-1.5">{icon}</span>
                    </td>
                    <td className="px-2 py-2 text-center text-xs"
                        style={{ color: positive ? '#065F46' : '#991B1B' }}
                        data-testid={`paid-bareme-pct-${key}`}>
                      {positive ? '+' : ''}{pct}%
                    </td>
                    <td className="px-3 py-2 text-right font-bold font-['Outfit']"
                        style={{ color: positive ? '#065F46' : '#991B1B' }}
                        data-testid={`paid-bareme-delta-${key}`}>
                      {positive ? '+' : ''}{delta.toFixed(2)} USD
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <p className="text-xs opacity-60 mt-3 leading-relaxed">
          ⚠️ Questions niveau expert — solde débité immédiatement.
          1 seule participation payante par jour.
        </p>

        {/* iter237l — Bonus anti-tilt toggle */}
        {config?.redemption?.available && (
          <label className="mt-3 flex items-start gap-2 p-3 rounded-xl cursor-pointer"
                 style={{ background: 'rgba(255,215,0,0.08)',
                          border: '1px solid rgba(247,147,26,0.30)' }}
                 data-testid="paid-bonus-toggle-label">
            <input type="checkbox"
                   checked={useBonus}
                   onChange={() => setUseBonus(v => !v)}
                   data-testid="paid-bonus-toggle"
                   className="mt-0.5 accent-[#F7931A]" />
            <span className="text-xs leading-relaxed">
              <strong>🎁 Utiliser mon bonus +5% anti-tilt</strong>
              <span className="block opacity-80 mt-0.5">
                Réduit ta perte de 5 points en cas d'échec.
                Aucun effet si tu fais 5/5. <strong>Usage unique.</strong>
              </span>
            </span>
          </label>
        )}

        <button onClick={startGame}
                disabled={!canStart || starting}
                data-testid="paid-stake-launch"
                className="jp-btn jp-btn-primary jp-btn-full mt-4"
                style={{ background: canStart ? 'linear-gradient(90deg, #FFD700, #F7931A)' : '#D1D5DB',
                         color: canStart ? '#111' : '#666',
                         opacity: starting ? 0.6 : 1 }}>
          {starting ? 'Lancement…' : `💰 Lancer le défi (${stakeNum.toFixed(2)} USD)`}
        </button>

        {/* iter237n — CGJ acceptance modal (anti-paywall pre-flight). */}
        <CgjAcceptanceModal
          open={cgjModalOpen}
          onClose={() => setCgjModalOpen(false)}
          onAccepted={async () => {
            setCgjModalOpen(false);
            await startGame();
          }}
        />
      </Modal>
    );
  }

  // ── Playing phase ────────────────────────────────────────────────────────
  if (phase === 'playing' && session) {
    const q = session.questions[qIdx];
    if (!q) return null;
    return (
      <Modal onClose={null} testId="paid-daily-playing-modal">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-bold uppercase tracking-wider opacity-60">
            Question {qIdx + 1}/{session.questions.length}
          </span>
          <span className="text-sm font-mono font-bold"
                style={{ color: secondsLeft <= 3 ? '#E01C2E' : '#0F056B' }}
                data-testid="paid-timer">
            ⏱ {secondsLeft}s
          </span>
        </div>
        <div className="text-[10px] uppercase tracking-widest mb-1"
             style={{ color: 'var(--jp-text-muted)' }}>
          {q.category}
        </div>
        <h3 className="font-['Outfit'] font-bold text-base mb-3"
            data-testid={`paid-question-${qIdx}`}>
          {q.question}
        </h3>
        <div className="space-y-2" data-testid="paid-options">
          {q.options.map((opt, i) => {
            let bg = 'rgba(255,255,255,0.04)';
            let border = 'var(--jp-border)';
            let color = 'var(--jp-text)';
            if (revealed) {
              if (revealed.correct_idx === i) {
                bg = 'rgba(16,185,129,0.12)'; border = '#10B981'; color = '#065F46';
              } else if (answers[answers.length - 1] === i && !revealed.is_correct) {
                bg = 'rgba(224,28,46,0.10)'; border = '#E01C2E'; color = '#991B1B';
              }
            }
            return (
              <button key={i}
                      onClick={() => answerQuestion(i)}
                      disabled={!!revealed}
                      data-testid={`paid-option-${i}`}
                      className="w-full text-left p-3 rounded-xl transition-all disabled:cursor-not-allowed"
                      style={{ background: bg, border: `1px solid ${border}`, color }}>
                <span className="font-bold mr-2">{String.fromCharCode(65 + i)}.</span> {opt}
              </button>
            );
          })}
        </div>

        {revealed && (
          <div className="mt-3" data-testid="paid-feedback">
            {revealed.is_correct === true && (
              <div className="p-3 rounded-xl text-sm"
                   style={{ background: 'rgba(16,185,129,0.10)', color: '#065F46' }}>
                <CheckCircle size={16} weight="fill" className="inline mr-1" />
                Bonne réponse !
              </div>
            )}
            {revealed.is_correct === false && (
              <div className="p-3 rounded-xl text-sm"
                   style={{ background: 'rgba(224,28,46,0.08)', color: '#991B1B' }}>
                <XCircle size={16} weight="fill" className="inline mr-1" />
                Mauvaise réponse.
                {revealed.explanation && (
                  <div className="mt-2 text-xs leading-relaxed flex items-start gap-1.5"
                       style={{ color: '#7F1D1D' }}
                       data-testid="paid-feedback-explanation">
                    <Lightbulb size={14} weight="duotone" className="shrink-0 mt-0.5" />
                    <span><strong>Explication :</strong> {revealed.explanation}</span>
                  </div>
                )}
              </div>
            )}
            {revealed.is_correct === null && (
              <div className="p-3 rounded-xl text-sm"
                   style={{ background: '#F3F4F6', color: '#6B7280' }}>
                Vérification…
              </div>
            )}
            <button onClick={nextQuestion} disabled={submitting}
                    data-testid="paid-next-btn"
                    className="jp-btn jp-btn-primary jp-btn-full mt-3"
                    style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
              {qIdx + 1 >= session.questions.length
                ? (submitting ? 'Calcul…' : 'Voir mon résultat')
                : <>Question suivante <ArrowRight size={14} /></>}
            </button>
          </div>
        )}
      </Modal>
    );
  }

  // ── Result phase ─────────────────────────────────────────────────────────
  if (phase === 'result' && result) {
    const won = result.won;
    const delta = result.amount_delta_usd;
    const bonusConsumed = !!result.bonus_consumed;
    // iter237l — Display the redemption CTA only when player lost AND
    // bonus has never been used yet AND profile is still incomplete.
    const lost = delta < 0;
    const redemption = config?.redemption || {};
    const showRedeemCta = lost && !redemption.used_at && !redemption.profile_complete;
    return (
      <Modal onClose={close} testId="paid-daily-result-modal">
        <div className="text-center" data-testid="paid-result-card">
          <div className="text-5xl mb-2">{won ? '🏆' : delta < 0 ? '💀' : '📊'}</div>
          <h3 className="font-['Outfit'] text-2xl font-extrabold mb-1"
              style={{ color: won ? '#B45309' : '#0F056B' }}>
            {result.score}/5
          </h3>
          <div className="text-sm mb-3" style={{ color: 'var(--jp-text-muted)' }}>
            {won ? 'Sans-faute légendaire !' : 'Reviens demain pour réessayer.'}
          </div>
          <div className="p-4 rounded-2xl mb-3"
               style={{ background: delta >= 0
                   ? 'linear-gradient(135deg, rgba(255,215,0,0.20), rgba(247,147,26,0.15))'
                   : 'linear-gradient(135deg, rgba(224,28,46,0.10), rgba(153,27,27,0.10))',
                        border: '1px solid rgba(0,0,0,0.06)' }}>
            <div className="text-xs uppercase tracking-wider opacity-60 mb-1">
              Variation wallet
            </div>
            <div className="font-['Outfit'] text-3xl font-extrabold"
                 data-testid="paid-result-delta"
                 style={{ color: delta > 0 ? '#065F46' : delta < 0 ? '#991B1B' : '#0F056B' }}>
              {delta > 0 ? '+' : ''}{delta.toFixed(2)} USD
            </div>
            <div className="text-xs mt-1 opacity-70">
              ({result.result_pct >= 0 ? '+' : ''}{result.result_pct}% sur {result.stake_usd.toFixed(2)} USD misés)
            </div>
            {bonusConsumed && (
              <div className="mt-2 text-[11px] font-bold inline-block px-2 py-0.5 rounded-full"
                   style={{ background: 'rgba(247,147,26,0.18)', color: '#B45309' }}
                   data-testid="paid-bonus-applied-pill">
                🎁 Bonus profil appliqué : +{result.bonus_applied_pct}pp
              </div>
            )}
          </div>
        </div>

        {/* iter237l — Redemption CTA on losses (one-time anti-tilt offer) */}
        {showRedeemCta && (
          <div className="rounded-2xl p-4 mb-3"
               style={{ background: 'linear-gradient(135deg, #FFE066, #F7931A)',
                        color: '#1F2937' }}
               data-testid="paid-redeem-cta">
            <div className="flex items-start gap-3">
              <div className="text-2xl shrink-0">🎁</div>
              <div className="flex-1">
                <div className="font-['Outfit'] text-base font-extrabold mb-0.5">
                  Tu as perdu {Math.abs(delta).toFixed(2)} USD ?
                </div>
                <div className="text-xs leading-relaxed mb-2">
                  Complète ton profil et débloque <strong>+5% sur ta prochaine
                  partie payante</strong> (atténuation de perte, usage unique).
                </div>
                <ul className="text-[11px] opacity-90 mb-2 space-y-0.5">
                  {(redemption.missing || []).includes('avatar') && (
                    <li>• Ajouter une photo de profil</li>
                  )}
                  {(redemption.missing || []).includes('about') && (
                    <li>• Écrire une bio (≥ 30 caractères)</li>
                  )}
                  {(redemption.missing || []).includes('interests') && (
                    <li>
                      • Renseigner {redemption.interests_required - (redemption.interests_filled || 0)}
                      {' '}info(s) personnelle(s) en plus
                      <span className="opacity-70">
                        {' '}(date de naissance / genre / pays / téléphone)
                      </span>
                    </li>
                  )}
                </ul>
                <button onClick={() => { close(); window.location.assign('/profile'); }}
                        data-testid="paid-redeem-go-profile"
                        className="jp-btn jp-btn-sm font-bold"
                        style={{ background: '#1F2937', color: '#FFD700' }}>
                  Compléter mon profil →
                </button>
              </div>
            </div>
          </div>
        )}

        <div className="space-y-2" data-testid="paid-result-questions">
          {(result.results || []).map((r, i) => (
            <div key={i} className="p-2.5 rounded-xl"
                 style={{ background: r.is_correct ? 'rgba(16,185,129,0.06)' : 'rgba(224,28,46,0.05)',
                          border: `1px solid ${r.is_correct ? 'rgba(16,185,129,0.20)' : 'rgba(224,28,46,0.20)'}` }}
                 data-testid={`paid-result-q-${i}`}>
              <div className="flex items-center gap-2 text-xs font-bold">
                {r.is_correct ? <CheckCircle size={14} weight="fill" style={{ color: '#10B981' }} />
                              : <XCircle size={14} weight="fill" style={{ color: '#E01C2E' }} />}
                Question {i + 1}
              </div>
              {!r.is_correct && r.explanation && (
                <div className="mt-1 text-xs leading-relaxed opacity-80 flex items-start gap-1.5"
                     style={{ color: 'var(--jp-text)' }}>
                  <Lightbulb size={12} weight="duotone" className="shrink-0 mt-0.5" />
                  <span>{r.explanation}</span>
                </div>
              )}
            </div>
          ))}
        </div>

        <button onClick={close}
                data-testid="paid-result-close"
                className="jp-btn jp-btn-primary jp-btn-full mt-4">
          Fermer
        </button>
      </Modal>
    );
  }

  return null;
}

function Modal({ children, onClose, testId }) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.55)' }}
         onClick={onClose ? () => onClose() : undefined}>
      <div className="jp-card-elevated w-full max-w-md p-5 max-h-[90vh] overflow-y-auto"
           data-testid={testId}
           onClick={e => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}
