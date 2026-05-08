/**
 * DuelPage — landing for a share_token.
 *
 * Flow :
 *   /duel/:token opens → GET /api/duel/:token (no auth needed for preview)
 *   • If not logged in: show "Connectez-vous pour relever le défi"
 *   • If logged in: "Relever le défi" → POST /api/duel/:token/start-{game}
 *     → opponent plays → submit → result screen (winner, tie, bonus)
 *   • If you are the challenger, show waiting / share again
 *   • If expired / completed, show the outcome
 */
import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { useAuth } from '@/context/AuthContext';
import { useTranslation } from 'react-i18next';
import {
  Target as SwordsIcon, Question, HandTap, Clock, Trophy, ArrowLeft,
} from '@phosphor-icons/react';
import ShareDuelContactSheet from '@/components/ShareDuelContactSheet';

const API = process.env.REACT_APP_BACKEND_URL;

export default function DuelPage() {
  const { t } = useTranslation();
  const { token } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [duel, setDuel] = useState(null);
  const [phase, setPhase] = useState('loading');    // loading|preview|playing|result|closed
  const [err, setErr] = useState('');
  const [starting, setStarting] = useState(false);
  const [payload, setPayload] = useState(null);     // runtime info from /start
  const [result, setResult] = useState(null);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [token]);

  const load = async () => {
    setPhase('loading');
    try {
      const { data } = await axios.get(`${API}/api/duel/${token}`);
      setDuel(data);
      // iter141 — multi-challenger flow: ALWAYS land on the leaderboard
      // view (initiator sees ranking, opponents see ranking + their CTA).
      // The "preview" / play flow is triggered from inside that view.
      if (data.duel_kind === 'multi_attempts') {
        if (data.status === 'expired') setPhase('closed');
        else setPhase('multi');
        return;
      }
      if (data.status === 'completed' || data.status === 'expired') setPhase('closed');
      else setPhase('preview');
    } catch (e) {
      setErr(e.response?.data?.detail || 'Défi introuvable.');
      setPhase('closed');
    }
  };

  const accept = async () => {
    if (!user) {
      toast.info('Connectez-vous pour relever le défi.');
      navigate(`/login?redirect=/duel/${token}`);
      return;
    }
    setStarting(true);
    try {
      const endpoint = duel.game === 'quiz'
        ? `${API}/api/duel/${token}/start-quiz`
        : `${API}/api/duel/${token}/start-tap`;
      const { data } = await axios.post(endpoint, {}, { withCredentials: true });
      setPayload(data);
      setPhase('playing');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Impossible de démarrer.');
    } finally { setStarting(false); }
  };

  const onFinish = async (res) => {
    setResult(res);
    // iter141seven — viral recruit toast (only the FIRST attempt by this
    // user against the initiator awards points — backend is idempotent).
    try {
      const credit = res?.recruit_credit;
      if (credit && credit.awarded_points > 0) {
        if (credit.buzz_unlocked) {
          toast.success(`👑 Tu as fait gagner le badge "Roi du Buzz" à ton hôte !`,
                        { description: `Il vient de débloquer le bonus viral.` });
        } else {
          toast.success(`+${credit.awarded_points} pts virals pour l'hôte 🎯`,
                        { description: `Tu es son nouveau recruteur.` });
        }
      }
    } catch (_) { /* never block UX */ }
    // iter141 — multi-attempts: refresh duel + jump to leaderboard view
    if (res?.duel_kind === 'multi_attempts' || duel?.duel_kind === 'multi_attempts') {
      try {
        const { data } = await axios.get(`${API}/api/duel/${token}`);
        setDuel(data);
      } catch { /* ignore */ }
      setPhase('multi');
      return;
    }
    // iter131 — Refresh duel so the rich CompletedDuelView renders with
    // both players' data (avatars, scores, winner) — single source of truth.
    try {
      const { data } = await axios.get(`${API}/api/duel/${token}`);
      setDuel(data);
    } catch { /* ignore — fall back to inline result */ }
    setPhase('result');
  };

  if (phase === 'loading') return <CenteredSpinner />;
  if (phase === 'closed')  return <ClosedView duel={duel} err={err} onBack={() => navigate('/games')} />;
  if (phase === 'multi')   return <MultiAttemptsView duel={duel} token={token} user={user}
                                                     starting={starting} onAccept={accept}
                                                     justSubmitted={!!result}
                                                     onBack={() => navigate('/games')} />;
  if (phase === 'preview') return <PreviewView duel={duel} user={user} starting={starting} onAccept={accept} onBack={() => navigate('/games')} />;
  if (phase === 'playing' && payload) {
    return duel.game === 'quiz'
      ? <QuizDuelBoard token={token} payload={payload} onFinish={onFinish} />
      : <TapDuelBoard  token={token} payload={payload} onFinish={onFinish} />;
  }
  if (phase === 'result' && duel?.status === 'completed') {
    return <CompletedDuelView duel={duel} onBack={() => navigate('/games')} />;
  }
  if (phase === 'result' && result && duel) {
    return <ResultView duel={duel} result={result} user={user} onBack={() => navigate('/games')} />;
  }
  return null;
}

/* ─────────────── views ─────────────── */

function CenteredSpinner() {
  return (
    <div className="min-h-screen flex items-center justify-center"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}>
      <div className="w-8 h-8 border-2 border-t-transparent rounded-full animate-spin"
           style={{ borderColor: '#FFD700', borderTopColor: 'transparent' }} />
    </div>
  );
}

function ClosedView({ duel, err, onBack }) {
  const { t } = useTranslation();
  // iter131 — When the duel is completed, show the rich result view
  // (both avatars, scores, winner badge, share + rematch CTAs). Falls
  // back to the simple "expired" message only for genuine error states.
  if (duel?.status === 'completed' && duel.opponent) {
    return <CompletedDuelView duel={duel} onBack={onBack} />;
  }
  const outcome = duel?.status === 'expired'
    ? t('duel.ce_defi_a_expire_24_h')
    : (err || 'Défi clos.');
  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6 text-white text-center"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="duel-closed">
      <Clock size={52} weight="fill" color="#F7931A" />
      <h1 className="font-['Outfit'] text-2xl font-extrabold mt-4 mb-2">Défi clôturé</h1>
      <p className="text-white/70 text-sm mb-6 max-w-xs">{outcome}</p>
      <button onClick={onBack} data-testid="duel-back-btn"
              className="px-6 py-3 rounded-full font-bold text-sm"
              style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
        Retour aux jeux
      </button>
    </div>
  );
}

/* iter131/132 — Rich completed-duel view (both players' avatars, scores, winner) */
function CompletedDuelView({ duel, onBack }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const me = user?.user_id;
  const isChallenger = me === duel.challenger.user_id;
  const isOpponent  = me === duel.opponent?.user_id;
  const tie = !duel.winner_id;
  const won = duel.winner_id === me;
  // iter132 — Percentile rank fetch for the connected user.
  const [rank, setRank] = useState(null);
  const [creatingRematch, setCreatingRematch] = useState(false);
  useEffect(() => {
    if (!me) return;
    axios.get(`${API}/api/duel/me/rank`, { withCredentials: true })
      .then(({ data }) => setRank(data))
      .catch(() => {});
  }, [me]);

  const headline = !me
    ? `${duel.winner_id === duel.challenger.user_id ? duel.challenger.name : duel.opponent?.name} l'emporte`
    : tie ? 'Match nul ⚖️'
      : won ? '🏆 Vous avez gagné'
        : '😔 Vous avez perdu';
  // iter132 — Psychological pressure message.
  const pressureMsg = !me ? null
    : tie ? '⚡ Égalité parfaite — départage au prochain duel !'
      : won ? '🏆 Tu es plus fort que ton adversaire — continue !'
        : '😬 Tu peux faire mieux — tente ta revanche !';
  const color = tie ? '#F7931A' : (me ? (won ? '#FFD700' : '#E01C2E') : '#FFD700');
  const scoreLabel = duel.game === 'quiz' ? 'bonnes réponses' : 'taps';
  const tiebreakerNote = duel.game === 'quiz'
    && duel.challenger_score === duel.opponent_score
    && duel.winner_id
    ? `Départagé au temps : ${duel.challenger_time_s?.toFixed(2)}s vs ${duel.opponent_time_s?.toFixed(2)}s`
    : null;

  // iter132 — Improved WhatsApp share text: always pushes a fresh challenge.
  const buildShareText = () => {
    const url = `${window.location.origin}/duel/${duel.share_token}`;
    const winnerScore = Math.max(duel.challenger_score, duel.opponent_score);
    const loserScore  = Math.min(duel.challenger_score, duel.opponent_score);
    if (tie) {
      return `⚖️ Match nul ${duel.challenger_score}-${duel.opponent_score} sur le Quiz JAPAP ! 💥 Sauras-tu nous départager ? 👉 ${url}`;
    }
    if (me && won) {
      return `🔥 Je viens de battre mon adversaire ${winnerScore}-${loserScore} sur JAPAP ! 💥 Sauras-tu faire mieux ? 👉 ${url}`;
    }
    if (me && !won) {
      return `😤 Je viens de perdre ${loserScore}-${winnerScore} sur le Quiz JAPAP. 💥 Tu penses pouvoir mieux ? Prouve-le 👉 ${url}`;
    }
    const winnerName = duel.winner_id === duel.challenger.user_id
      ? duel.challenger.name : duel.opponent.name;
    return `🏆 ${winnerName} domine le duel ${winnerScore}-${loserScore} sur le Quiz JAPAP ! 💥 Tu peux faire mieux ? 👉 ${url}`;
  };

  const shareResult = async () => {
    const txt = buildShareText();
    const url = `${window.location.origin}/duel/${duel.share_token}`;
    try {
      if (navigator.share) await navigator.share({ title: t('duel.resultat_duel_japap'), text: txt, url });
      else { await navigator.clipboard.writeText(txt); toast.success('Lien copié'); }
    } catch (_) { /* user cancelled */ }
  };

  const shareWhatsApp = () => {
    window.open(`https://wa.me/?text=${encodeURIComponent(buildShareText())}`,
                '_blank', 'noopener,noreferrer');
  };

  // iter132 — Rematch: requires a fresh quiz run from the caller. Two paths:
  //  1) Caller has played a quiz recently (lastRunId) — use it.
  //  2) Caller has not played → redirect to /games/quiz with hint.
  const onRematch = async () => {
    if (!me) { navigate('/login'); return; }
    setCreatingRematch(true);
    try {
      // Backend auto-picks our latest submitted run as the new challenger_score.
      const { data } = await axios.post(`${API}/api/duel/${duel.share_token}/rematch`,
        {}, { withCredentials: true });
      toast.success('Revanche envoyée !');
      navigate(`/duel/${data.share_token}`);
    } catch (e) {
      const detail = e.response?.data?.detail || 'Revanche impossible';
      if (detail.includes('Joue') || detail.includes('Terminez')) {
        toast.info(detail);
        navigate('/games/quiz');
      } else {
        toast.error(detail);
      }
    } finally { setCreatingRematch(false); }
  };

  return (
    <div className="min-h-screen flex flex-col text-white p-4"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="duel-completed">
      <div className="flex items-center mb-2">
        <button onClick={onBack} className="p-2 rounded-full"
                style={{ background: 'rgba(255,255,255,0.1)' }}>
          <ArrowLeft size={18} weight="bold" />
        </button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center text-center">
        <div className="w-20 h-20 rounded-full flex items-center justify-center mb-4"
             style={{ background: `linear-gradient(135deg,${color},${color}88)`,
                      boxShadow: `0 16px 48px ${color}66` }}>
          <Trophy size={36} weight="fill" color="#111" />
        </div>
        <h1 className="font-['Outfit'] text-3xl font-extrabold mb-2" style={{ color }}
            data-testid="duel-result-headline">
          {headline}
        </h1>

        {pressureMsg && (
          <div className="text-sm font-bold mb-4 px-4 py-2 rounded-full"
               data-testid="duel-pressure-msg"
               style={{ background: 'rgba(255,255,255,0.10)',
                        border: '1px solid rgba(255,255,255,0.20)' }}>
            {pressureMsg}
          </div>
        )}

        {/* Scoreboard — both players side by side */}
        <div className="grid grid-cols-3 items-center gap-3 w-full max-w-md mb-4 px-2">
          <PlayerCard player={duel.challenger}
                      score={duel.challenger_score}
                      time={duel.challenger_time_s}
                      isWinner={duel.winner_id === duel.challenger.user_id}
                      isMe={isChallenger}
                      label={isChallenger ? 'Vous' : 'Challenger'}
                      scoreLabel={scoreLabel}
                      testid="duel-card-challenger" />
          <div className="text-center">
            <div className="text-[10px] uppercase tracking-widest text-white/60">VS</div>
            <Trophy size={26} weight="fill" color="#FFD700" className="mx-auto" />
          </div>
          <PlayerCard player={duel.opponent}
                      score={duel.opponent_score}
                      time={duel.opponent_time_s}
                      isWinner={duel.winner_id === duel.opponent?.user_id}
                      isMe={isOpponent}
                      label={isOpponent ? 'Vous' : 'Adversaire'}
                      scoreLabel={scoreLabel}
                      testid="duel-card-opponent" />
        </div>

        {tiebreakerNote && (
          <div className="text-[11px] text-white/60 mb-3" data-testid="duel-tiebreaker-note">
            ⚡ {tiebreakerNote}
          </div>
        )}

        {/* iter132 — Approximate percentile rank ("Top X%") */}
        {rank?.min_plays && rank.percentile != null && (
          <div className="text-[11px] mb-4 px-3 py-1.5 rounded-full font-bold"
               data-testid="duel-percentile"
               style={{ background: 'linear-gradient(90deg, #FF6B00, #FFD700)',
                        color: '#111' }}>
            🔥 Tu es dans le top {Math.max(rank.percentile, 1)}% des joueurs
          </div>
        )}

        <div className="w-full max-w-sm space-y-2.5">
          <button onClick={shareWhatsApp} data-testid="duel-share-whatsapp"
                  className="w-full py-3 rounded-full font-bold text-sm flex items-center justify-center gap-2"
                  style={{ background: '#25D366', color: 'white',
                           boxShadow: '0 10px 28px rgba(37,211,102,0.45)' }}>
            📱 Partager sur WhatsApp
          </button>
          <button onClick={shareResult} data-testid="duel-share-result"
                  className="w-full py-3 rounded-full font-bold text-sm"
                  style={{ background: 'rgba(255,255,255,0.12)', color: '#fff',
                           border: '1px solid rgba(255,255,255,0.25)' }}>
            🔗 Copier / partager le résultat
          </button>
          {/* iter132 — Rematch button (visible to both participants) */}
          {(isChallenger || isOpponent) && duel.game === 'quiz' && (
            <button onClick={onRematch} disabled={creatingRematch}
                    data-testid="duel-rematch-btn"
                    className="w-full py-3 rounded-full font-bold text-sm disabled:opacity-60"
                    style={{ background: 'linear-gradient(90deg, #8B5CF6, #E01C2E)', color: '#fff',
                             boxShadow: '0 10px 28px rgba(139,92,246,0.45)' }}>
              {creatingRematch ? t('duel.creation_de_la_revanche') : '⚔ Demander la revanche'}
            </button>
          )}
          <Link to={duel.game === 'quiz' ? '/games/quiz' : '/games/tap'}
                data-testid="duel-replay-btn"
                className="block w-full py-3 rounded-full font-bold text-sm text-center"
                style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
            ↻ Rejouer (nouvelle partie)
          </Link>
          <Link to="/games" data-testid="duel-back-to-games"
                className="block text-sm text-white/70 underline text-center mt-2">
            Retour aux jeux
          </Link>
        </div>

        <div className="mt-4 text-[10px] text-white/40">
          Terminé le {duel.completed_at
            ? new Date(duel.completed_at).toLocaleString('fr-FR')
            : '—'}
        </div>
      </div>
    </div>
  );
}

/* iter141 — Multi-challenger view: shows the leaderboard of all
 * attempts vs the initiator. Used for duel_kind='multi_attempts'. */
function MultiAttemptsView({ duel, token, user, starting, onAccept, justSubmitted, onBack }) {
  const { t } = useTranslation();
  const [board, setBoard] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);

  const fetchBoard = async () => {
    try {
      const { data } = await axios.get(
        `${API}/api/duel/${token}/leaderboard`,
        user ? { withCredentials: true } : undefined,
      );
      setBoard(data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Classement indisponible');
    } finally { setLoading(false); }
  };

  useEffect(() => {
    fetchBoard();
    const t = setInterval(fetchBoard, 8000);    // light polling for live updates
    return () => clearInterval(t);
    /* eslint-disable-next-line */
  }, [token, user?.user_id]);

  if (loading || !board) return <CenteredSpinner />;

  const youAreInitiator = !!board.you_are_initiator;
  const youPlayed = !!board.your_attempt;
  const expired = duel?.status === 'expired';
  const initiatorScore = board.initiator_score ?? 0;
  const initiator = board.initiator;
  const url = `${window.location.origin}/duel/${token}`;
  const stats = board.stats || {};

  const buildShareText = () => {
    if (youAreInitiator) {
      return `🔥 Je viens de marquer ${initiatorScore}/5 au défi quotidien JAPAP. ${stats.participants || 0} ami(s) ont déjà joué — sauras-tu faire mieux ? 👉 ${url}`;
    }
    return `⚔️ Défi quotidien JAPAP — ${initiator?.name || 'un ami'} a fait ${initiatorScore}/5. Tente ta chance ! 👉 ${url}`;
  };
  const shareWA = () => setSheetOpen(true);
  const copyLink = async () => {
    try { await navigator.clipboard.writeText(buildShareText()); toast.success('Lien copié'); }
    catch { /* ignore */ }
  };

  const outcomeBadge = (oc) => {
    if (oc === 'won')  return { label: t('duel.gagne'),  color: '#22c55e', emoji: '🟢' };
    if (oc === 'lost') return { label: 'Perdu',  color: '#E01C2E', emoji: '🔴' };
    return { label: t('duel.egalite'), color: '#9ca3af', emoji: '⚪' };
  };

  // Top banner highlighting result for the connected challenger.
  let yourBadge = null;
  if (youPlayed && !youAreInitiator) {
    const oc = board.your_attempt.outcome;
    const headline = oc === 'won' ? '🏆 Tu as battu l\'initiateur !'
      : oc === 'lost' ? '😬 Tu as perdu de peu — retente ta chance'
        : '⚡ Égalité parfaite';
    const sub = `Toi : ${board.your_attempt.score}/5 · ${initiator?.name || 'Initiateur'} : ${initiatorScore}/5`;
    yourBadge = (
      <div className="rounded-2xl p-4 mb-4 text-center"
           data-testid="duel-multi-your-result"
           style={{ background: oc === 'won'
             ? 'linear-gradient(135deg, rgba(34,197,94,0.25), rgba(34,197,94,0.10))'
             : oc === 'lost'
               ? 'linear-gradient(135deg, rgba(224,28,46,0.25), rgba(224,28,46,0.10))'
               : 'linear-gradient(135deg, rgba(247,147,26,0.25), rgba(247,147,26,0.10))',
             border: '1px solid rgba(255,255,255,0.18)' }}>
        <div className="font-['Outfit'] text-lg font-extrabold">{headline}</div>
        <div className="text-xs text-white/80 mt-1">{sub}</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col text-white p-4 pb-20"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="duel-multi-view">
      <div className="flex items-center mb-3">
        <button onClick={onBack} className="p-2 rounded-full"
                style={{ background: 'rgba(255,255,255,0.1)' }}
                data-testid="duel-multi-back">
          <ArrowLeft size={18} weight="bold" />
        </button>
        <div className="ml-3 text-xs uppercase tracking-widest text-white/60">
          Défi quotidien · classement
        </div>
      </div>

      {/* Initiator card */}
      <div className="rounded-2xl p-4 mb-4 flex items-center gap-3"
           data-testid="duel-multi-initiator-card"
           style={{ background: 'linear-gradient(135deg, rgba(255,215,0,0.18), rgba(247,147,26,0.10))',
                    border: '1px solid rgba(255,215,0,0.35)' }}>
        <Avatar user={initiator} placeholder={initiator?.name?.[0] || '?'} />
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-widest text-white/60">Initiateur</div>
          <div className="font-bold truncate">{initiator?.name || 'Initiateur'}</div>
          <div className="text-xs text-white/70">
            {initiatorScore}/5{board.initiator_time_s != null ? ` · ⏱ ${board.initiator_time_s.toFixed(1)}s` : ''}
          </div>
        </div>
        <div className="text-right">
          <div className="font-['Outfit'] text-3xl font-extrabold" style={{ color: '#FFD700' }}>
            {initiatorScore}
          </div>
          <div className="text-[9px] uppercase tracking-widest text-white/50">/ 5</div>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2 mb-4" data-testid="duel-multi-stats">
        <Stat label="Participants" value={stats.participants ?? 0} />
        <Stat label="Meilleur" value={stats.best_score == null ? '—' : `${stats.best_score}/5`} />
        <Stat label={youAreInitiator ? 'Victoires' : 'Battent A'}
              value={`${stats.losses_for_initiator ?? 0}/${stats.participants ?? 0}`} />
      </div>

      {yourBadge}

      {/* Action: jouer ou rejouer */}
      {!youAreInitiator && !youPlayed && !expired && (
        <button onClick={onAccept} disabled={starting}
                data-testid="duel-multi-accept-btn"
                className="w-full py-3 rounded-full font-bold text-base mb-4"
                style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)',
                         color: '#111', boxShadow: '0 10px 36px rgba(255,215,0,0.55)' }}>
          {starting ? t('duel.preparation') : '⚔ Relever le défi'}
        </button>
      )}
      {!youAreInitiator && youPlayed && (
        <div className="text-xs text-white/60 mb-4 text-center">
          Tu as déjà joué ce défi — un seul essai par challenger.
        </div>
      )}
      {youAreInitiator && (
        <div className="text-xs text-white/60 mb-4 text-center">
          Tu es l'initiateur — partage ce lien pour défier plus d'amis.
        </div>
      )}

      {/* Leaderboard */}
      <div className="text-[10px] uppercase tracking-widest text-white/50 mb-2 px-1">
        Classement ({board.attempts.length} challenger{board.attempts.length > 1 ? 's' : ''})
      </div>
      <div className="space-y-2 mb-4" data-testid="duel-multi-leaderboard">
        {board.attempts.length === 0 && (
          <div className="rounded-2xl p-6 text-center text-white/60 text-sm"
               style={{ background: 'rgba(255,255,255,0.05)',
                        border: '1px dashed rgba(255,255,255,0.18)' }}
               data-testid="duel-multi-empty">
            Aucun challenger n'a encore joué — sois le premier !
          </div>
        )}
        {board.attempts.map((a) => {
          const b = outcomeBadge(a.outcome);
          return (
            <div key={`${a.user.user_id}-${a.rank}`}
                 className="flex items-center gap-3 p-3 rounded-xl"
                 data-testid={`duel-multi-row-${a.rank}`}
                 style={{ background: a.is_you
                   ? 'linear-gradient(90deg, rgba(255,215,0,0.18), rgba(247,147,26,0.06))'
                   : 'rgba(255,255,255,0.06)',
                   border: a.is_you ? '1px solid rgba(255,215,0,0.45)' : '1px solid rgba(255,255,255,0.08)' }}>
              <div className="w-8 text-center font-['Outfit'] text-lg font-extrabold"
                   style={{ color: a.rank === 1 ? '#FFD700' : '#fff' }}>
                #{a.rank}
              </div>
              <Avatar user={a.user} placeholder={a.user?.name?.[0] || '?'} />
              <div className="flex-1 min-w-0">
                <div className="font-bold text-sm truncate flex items-center gap-1">
                  {a.user.name}
                  {a.is_you && <span className="text-[9px] px-1.5 py-0.5 rounded-full"
                                     style={{ background: '#FFD700', color: '#111' }}>VOUS</span>}
                </div>
                <div className="text-[11px] text-white/60">
                  {a.score}/5 · ⏱ {a.time_s != null ? `${a.time_s.toFixed(1)}s` : '—'}
                </div>
              </div>
              <div className="text-[10px] font-bold px-2 py-1 rounded-full"
                   style={{ background: `${b.color}33`, color: b.color,
                            border: `1px solid ${b.color}66` }}>
                {b.emoji} {b.label}
              </div>
            </div>
          );
        })}
      </div>

      {/* Share row */}
      <div className="space-y-2 mt-2">
        <button onClick={shareWA}
                data-testid="duel-multi-share-whatsapp"
                className="w-full py-3 rounded-full font-bold text-sm"
                style={{ background: '#25D366', color: 'white',
                         boxShadow: '0 10px 28px rgba(37,211,102,0.45)' }}>
          📱 Partager sur WhatsApp
        </button>
        <button onClick={copyLink}
                data-testid="duel-multi-copy-link"
                className="w-full py-3 rounded-full font-bold text-sm"
                style={{ background: 'rgba(255,255,255,0.10)', color: '#fff',
                         border: '1px solid rgba(255,255,255,0.20)' }}>
          🔗 Copier le lien
        </button>
        {youAreInitiator && (
          <Link to="/duel/me/sent" data-testid="duel-multi-my-sent"
                className="block w-full py-3 rounded-full font-bold text-sm text-center"
                style={{ background: 'linear-gradient(90deg, #8B5CF6, #E01C2E)', color: '#fff' }}>
            📊 Mes défis envoyés
          </Link>
        )}
        <Link to="/games"
              className="block text-sm text-white/70 underline text-center mt-2">
          Retour aux jeux
        </Link>
      </div>
      <ShareDuelContactSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        shareToken={token}
        game={duel?.game || 'quiz'}
        score={`${initiatorScore}/5`}
      />
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="rounded-xl p-2 text-center"
         style={{ background: 'rgba(255,255,255,0.06)',
                  border: '1px solid rgba(255,255,255,0.10)' }}>
      <div className="text-[9px] uppercase tracking-widest text-white/50">{label}</div>
      <div className="font-['Outfit'] text-lg font-extrabold mt-0.5">{value}</div>
    </div>
  );
}

function PlayerCard({ player, score, time, isWinner, isMe, label, scoreLabel, testid }) {
  return (
    <div className="text-center" data-testid={testid}>
      <div className="relative inline-block mb-2">
        <Avatar user={player} placeholder={label?.[0] || '?'} />
        {isWinner && (
          <div className="absolute -top-2 -right-1 text-lg" title="Gagnant">🏆</div>
        )}
      </div>
      <div className="text-[11px] font-bold truncate"
           style={{ color: isMe ? '#FFD700' : '#fff', maxWidth: '100px' }}>
        {player?.name || label}
      </div>
      <div className="font-['Outfit'] text-2xl font-extrabold"
           style={{ color: isWinner ? '#FFD700' : '#fff' }}>
        {score ?? '—'}
      </div>
      <div className="text-[9px] uppercase tracking-widest text-white/50">{scoreLabel}</div>
      {time != null && (
        <div className="text-[10px] text-white/60 mt-0.5">⏱ {time.toFixed(1)}s</div>
      )}
    </div>
  );
}

function PreviewView({ duel, user, starting, onAccept, onBack }) {
  const { t } = useTranslation();
  const { challenger, challenger_score, game } = duel;
  const Icon = game === 'quiz' ? Question : HandTap;
  const scoreStr = game === 'quiz' ? `${challenger_score}/5` : `${challenger_score} taps`;
  const color = game === 'quiz' ? '#8B5CF6' : '#E01C2E';
  const isMe = user && user.user_id === challenger.user_id;
  return (
    <div className="min-h-screen flex flex-col text-white"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="duel-preview">
      <div className="flex items-center p-4">
        <button onClick={onBack} className="p-2 rounded-full"
                style={{ background: 'rgba(255,255,255,0.1)' }}>
          <ArrowLeft size={18} weight="bold" />
        </button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center px-6 text-center">
        <div className="w-24 h-24 rounded-3xl flex items-center justify-center mb-5"
             style={{ background: `linear-gradient(135deg, ${color}, ${color}88)`,
                      boxShadow: `0 20px 60px ${color}66` }}>
          <SwordsIcon size={44} weight="fill" />
        </div>
        <h1 className="font-['Outfit'] text-3xl font-extrabold mb-2">
          {challenger.name} vous défie
        </h1>
        <p className="text-white/70 text-sm mb-6 max-w-xs">
          Défi {game === 'quiz' ? 'Quiz JAPAP' : 'Tap Challenge'} ·
          son score : <span className="font-bold" style={{ color }}>{scoreStr}</span>
        </p>
        <div className="flex items-center gap-3 mb-8">
          <Avatar user={challenger} />
          <Icon size={28} weight="fill" color="#FFD700" />
          <Avatar user={user} placeholder={t('duel.vous')} />
        </div>
        {isMe ? (
          <ShareDuelBlock duel={duel} game={game} score={scoreStr} />
        ) : (
          <button onClick={onAccept} disabled={starting}
                  data-testid="duel-accept-btn"
                  className="w-full max-w-sm py-4 rounded-full font-bold text-base transition-transform active:scale-[0.97]"
                  style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)',
                           color: '#111', boxShadow: '0 10px 36px rgba(255,215,0,0.55)' }}>
            {starting ? t('duel.preparation') : '⚔ Relever le défi'}
          </button>
        )}
        <div className="mt-4 text-[11px] text-white/50">
          Expire le {new Date(duel.expires_at).toLocaleString('fr-FR')}
        </div>
      </div>
    </div>
  );
}

/* iter141fivex — Share block for the duel challenger.
 * Shown on the Preview screen when the connected user is the duel
 * creator. Provides a clear copy-able link, a primary "Partager le défi"
 * button via Web Share API, a WhatsApp pre-filled CTA and a "Copier le
 * lien" fallback. Pre-fills the message with the challenger's score so
 * recipients have context before clicking. */
function ShareDuelBlock({ duel, game, score }) {
  const { t } = useTranslation();
  const [sheetOpen, setSheetOpen] = useState(false);
  const url = `${window.location.origin}/duel/${duel.share_token}`;
  const message = `Je t'ai défié sur JAPAP, peux-tu faire mieux que moi ? Mon score : ${score} sur ${game === 'quiz' ? 'Quiz JAPAP' : 'Tap Challenge'} 💪 Joue ici : ${url}`;
  const shareWA = () => setSheetOpen(true);
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(message);
      toast.success('Lien copié — colle-le dans WhatsApp, SMS ou tes réseaux sociaux.');
    } catch {
      // Older browsers fallback — show the URL so the user can long-press.
      toast.message(url, { description: 'Copie ce lien manuellement.' });
    }
  };
  const sharePrimary = async () => {
    if (navigator.share) {
      try {
        await navigator.share({ title: t('duel.defi_japap'), text: message, url });
        return;
      } catch (_) { /* user cancelled — fall through to copy */ }
    }
    copyLink();
  };
  return (
    <div className="w-full max-w-sm space-y-3" data-testid="duel-share-block">
      <p className="text-xs text-white/70 text-center">
        Vous êtes le challenger — partagez ce lien à un ami pour qu'il relève votre défi.
      </p>
      <div className="rounded-xl px-3 py-2 text-[11px] text-white/80 break-all text-left font-mono"
           data-testid="duel-share-link"
           style={{ background: 'rgba(255,255,255,0.10)', border: '1px solid rgba(255,255,255,0.18)' }}>
        {url}
      </div>
      <button type="button" onClick={sharePrimary}
              data-testid="duel-share-primary"
              className="w-full py-3.5 rounded-full font-bold text-sm"
              style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111',
                       boxShadow: '0 10px 36px rgba(255,215,0,0.55)' }}>
        🔗 Partager le défi
      </button>
      <div className="grid grid-cols-2 gap-2">
        <button type="button" onClick={shareWA}
                data-testid="duel-share-whatsapp"
                className="py-3 rounded-full font-bold text-xs"
                style={{ background: '#25D366', color: 'white',
                         boxShadow: '0 8px 24px rgba(37,211,102,0.45)' }}>
          📱 WhatsApp
        </button>
        <button type="button" onClick={copyLink}
                data-testid="duel-share-copy"
                className="py-3 rounded-full font-bold text-xs"
                style={{ background: 'rgba(255,255,255,0.12)', color: '#fff',
                         border: '1px solid rgba(255,255,255,0.25)' }}>
          📋 Copier le lien
        </button>
      </div>
      <ShareDuelContactSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        shareToken={duel.share_token}
        game={game}
        score={score}
      />
    </div>
  );
}

function Avatar({ user, placeholder }) {
  if (!user) {
    return (
      <div className="w-14 h-14 rounded-full flex items-center justify-center font-bold"
           style={{ background: 'rgba(255,255,255,0.15)', color: '#fff' }}>
        {(placeholder || '?')[0]}
      </div>
    );
  }
  return (
    <div className="w-14 h-14 rounded-full overflow-hidden flex items-center justify-center font-bold border-2"
         style={{ background: 'rgba(255,255,255,0.15)', color: '#fff', borderColor: '#FFD700' }}>
      {user.avatar ? (
        <img src={user.avatar.startsWith('http') ? user.avatar : `${API}${user.avatar}`}
             alt="" className="w-full h-full object-cover" />
      ) : ((user.name || user.first_name || '?')[0]?.toUpperCase())}
    </div>
  );
}

/* ─────────────── quiz duel board (reuse the simple board) ─────────────── */

function QuizDuelBoard({ token, payload, onFinish }) {
  const [qi, setQi] = useState(0);
  const [answers, setAnswers] = useState(new Array(payload.questions.length).fill(-1));
  const [remaining, setRemaining] = useState(payload.time_limit_seconds);
  const [submitting, setSubmitting] = useState(false);
  const submittedRef = useRef(false);

  useEffect(() => {
    const started = Date.now();
    const t = setInterval(() => {
      const left = Math.max(0, payload.time_limit_seconds - (Date.now() - started) / 1000);
      setRemaining(left);
      if (left <= 0) { clearInterval(t); submit(); }
    }, 100);
    return () => clearInterval(t);
    /* eslint-disable-next-line */
  }, []);

  const pick = (i) => {
    const a = [...answers]; a[qi] = i; setAnswers(a);
    if (qi < payload.questions.length - 1) setTimeout(() => setQi(qi + 1), 120);
    else setTimeout(submit, 180);
  };

  const submit = async () => {
    if (submittedRef.current) return;
    submittedRef.current = true;
    setSubmitting(true);
    try {
      const { data } = await axios.post(
        `${API}/api/duel/${token}/submit-quiz`,
        { answers },
        { withCredentials: true },
      );
      onFinish(data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Soumission impossible');
    } finally { setSubmitting(false); }
  };

  const q = payload.questions[qi];
  if (!q) return <CenteredSpinner />;
  const pct = (remaining / payload.time_limit_seconds) * 100;
  const critical = remaining < 3;
  return (
    <div className="min-h-screen flex flex-col text-white"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="duel-quiz-playing">
      <div className="h-2 w-full" style={{ background: 'rgba(255,255,255,0.1)' }}>
        <div className="h-full transition-all duration-100"
             style={{ width: `${pct}%`, background: critical
                ? 'linear-gradient(90deg, #E01C2E, #F7931A)'
                : 'linear-gradient(90deg, #FFD700, #F7931A)',
             }} />
      </div>
      <div className="flex items-center justify-between px-5 py-3 text-xs">
        <span>Q{qi + 1}/{payload.questions.length}</span>
        <span className={critical ? 'animate-pulse font-bold' : 'font-bold'}
              style={{ color: critical ? '#F7931A' : '#FFD700' }}>
          {remaining.toFixed(1)}s
        </span>
      </div>
      <div className="flex-1 flex flex-col px-6 py-4">
        <h2 className="font-['Outfit'] text-xl font-bold mb-6 flex-shrink-0">
          {q.text}
        </h2>
        <div className="space-y-3">
          {q.options.map((opt, i) => (
            <button key={i} onClick={() => pick(i)}
                    disabled={submitting}
                    data-testid={`duel-quiz-option-${qi}-${i}`}
                    className="w-full p-4 rounded-2xl text-left font-semibold transition-all active:scale-[0.98]"
                    style={{
                      background: answers[qi] === i ? 'linear-gradient(135deg,#FFD700,#F7931A)'
                        : 'rgba(255,255,255,0.08)',
                      color: answers[qi] === i ? '#111' : '#fff',
                      border: '1px solid rgba(255,255,255,0.15)',
                    }}>
              {String.fromCharCode(65 + i)} · {opt}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─────────────── tap duel board ─────────────── */

function TapDuelBoard({ token, payload, onFinish }) {
  const DURATION_MS = payload.duration_seconds * 1000;
  const [taps, setTaps] = useState(0);
  const [timeLeft, setTimeLeft] = useState(DURATION_MS);
  const tapsRef = useRef(0);
  const submittedRef = useRef(false);
  const tickRef = useRef(null);

  useEffect(() => {
    const serverStart = new Date(payload.start_at).getTime();
    const localStart = Date.now();
    const reference = Math.abs(localStart - serverStart) > 3000 ? serverStart : localStart;
    tickRef.current = setInterval(() => {
      const left = Math.max(0, DURATION_MS - (Date.now() - reference));
      setTimeLeft(left);
      if (left <= 0) { clearInterval(tickRef.current); submit(); }
    }, 80);
    return () => clearInterval(tickRef.current);
    /* eslint-disable-next-line */
  }, []);

  useEffect(() => { tapsRef.current = taps; }, [taps]);

  const tap = () => {
    if (timeLeft <= 0) return;
    setTaps(t => t + 1);
    if (navigator.vibrate) navigator.vibrate(8);
  };

  const submit = async () => {
    if (submittedRef.current) return;
    submittedRef.current = true;
    try {
      const { data } = await axios.post(
        `${API}/api/duel/${token}/submit-tap`,
        { run_id: payload.run_id, taps: tapsRef.current },
        { withCredentials: true },
      );
      onFinish(data);
    } catch (e) { toast.error(e.response?.data?.detail || 'Soumission impossible'); }
  };

  const pct = (timeLeft / DURATION_MS) * 100;
  const critical = timeLeft < 3000;
  return (
    <div className="min-h-screen flex flex-col"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="duel-tap-playing">
      <div className="h-2 w-full" style={{ background: 'rgba(255,255,255,0.08)' }}>
        <div className="h-full transition-all duration-100"
             style={{ width: `${pct}%`, background: critical
                ? 'linear-gradient(90deg, #E01C2E, #F7931A)'
                : 'linear-gradient(90deg, #FFD700, #F7931A)' }} />
      </div>
      <div className="flex items-center justify-between px-5 py-3 text-white text-xs">
        <span>Adversaire : {payload.challenger_score} taps</span>
        <span className={critical ? 'animate-pulse font-bold' : 'font-bold'}
              style={{ color: critical ? '#F7931A' : '#fff' }}>
          <Clock size={14} weight="fill" className="inline mr-1" />
          {(timeLeft / 1000).toFixed(1)}s
        </span>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center px-6">
        <div className="mb-6 text-center">
          <div className="text-[10px] uppercase tracking-widest font-bold text-white/60 mb-2">
            Votre score
          </div>
          <div className="font-['Outfit'] text-7xl font-extrabold"
               style={{ color: '#FFD700', textShadow: '0 6px 40px rgba(255,215,0,0.55)' }}>
            {taps}
          </div>
        </div>
        <button onClick={tap} onTouchStart={(e) => { e.preventDefault(); tap(); }}
                data-testid="duel-tap-button"
                className="w-64 h-64 rounded-full flex items-center justify-center active:scale-[0.92] transition-transform select-none"
                style={{
                  background: 'radial-gradient(circle at 30% 30%, #FFD700 0%, #F7931A 55%, #E01C2E 100%)',
                  boxShadow: '0 30px 80px rgba(247,147,26,0.55), inset 0 -8px 0 rgba(0,0,0,0.2)',
                  touchAction: 'manipulation', WebkitUserSelect: 'none',
                }}>
          <HandTap size={96} weight="fill" color="#111" />
        </button>
      </div>
    </div>
  );
}

/* ─────────────── result view ─────────────── */

function ResultView({ duel, result, user, onBack }) {
  const { t } = useTranslation();
  const won = result.winner_id === user?.user_id;
  const tie = result.is_tie;
  const color = tie ? '#F7931A' : won ? '#FFD700' : '#E01C2E';
  const headline = tie ? 'Match nul !' : won ? 'Victoire 🏆' : t('duel.defaite');
  const scoreLabel = duel.game === 'quiz' ? 'bonnes réponses' : 'taps';
  return (
    <div className="min-h-screen flex flex-col text-white"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="duel-result">
      <div className="flex-1 flex flex-col items-center justify-center px-6 text-center">
        <div className="w-28 h-28 rounded-full flex items-center justify-center mb-5"
             style={{ background: `linear-gradient(135deg,${color},${color}88)`,
                      boxShadow: `0 20px 60px ${color}66` }}>
          <Trophy size={52} weight="fill" color="#111" />
        </div>
        <h1 className="font-['Outfit'] text-4xl font-extrabold mb-1" style={{ color }}>
          {headline}
        </h1>
        <p className="text-white/60 text-sm mb-6">
          {duel.challenger.name} · <span className="font-bold text-white">{duel.challenger_score}</span> vs
          <span className="font-bold text-white"> {result.opponent_score}</span> · {scoreLabel}
        </p>
        <div className="grid grid-cols-2 gap-3 w-full max-w-sm mb-6">
          <StatBlock label="Points base" value={`+${result.base_points_awarded}`} />
          <StatBlock label={tie ? 'Bonus nul' : won ? 'Bonus victoire' : 'Bonus consolation'}
                     value={`+${result.bonus_awarded}`} color={color} />
        </div>
        <button onClick={onBack} data-testid="duel-back-btn"
                className="w-full max-w-sm py-3 rounded-full font-bold text-base"
                style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
          Retour aux jeux
        </button>
        <Link to="/games" data-testid="duel-challenge-someone-else"
              className="mt-4 text-sm text-white/70 underline">
          Défier quelqu'un d'autre
        </Link>
      </div>
    </div>
  );
}

function StatBlock({ label, value, color }) {
  return (
    <div className="p-3 rounded-xl text-center"
         style={{ background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.15)' }}>
      <div className="text-[10px] uppercase tracking-widest text-white/60 mb-1">{label}</div>
      <div className="font-['Outfit'] text-xl font-extrabold"
           style={{ color: color || '#FFD700' }}>{value}</div>
    </div>
  );
}
