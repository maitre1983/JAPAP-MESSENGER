/**
 * GamesAdminTab — iter107 Phase 2.
 *
 * Admin can tune Quiz + Tap engines without redeploys. All changes
 * persist in admin_settings (single source of truth) and take effect
 * on the next /start call. Audit-logged via games.{quiz,tap}.config_update.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Brain, HandTap, Power, FloppyDisk, Database, ArrowsClockwise, CheckCircle, Warning, XCircle, CurrencyDollar } from '@phosphor-icons/react';
import { extractErrorMessage } from '@/utils/errorMessage';
import { useTranslation } from 'react-i18next';
// iter237k — Daily Challenge PAID admin section.
import PaidDailyChallengeAdmin from '@/components/admin/PaidDailyChallengeAdmin';

const API = process.env.REACT_APP_BACKEND_URL;

export default function GamesAdminTab() {
  const { t } = useTranslation();
  const [tab, setTab] = useState('quiz');
  return (
    <div data-testid="games-admin-root">
      <div className="flex gap-1 mb-4 p-1 rounded-xl"
           style={{ background: 'var(--jp-surface-secondary)' }}>
        <button onClick={() => setTab('quiz')}
                className={`flex-1 py-2 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 ${tab === 'quiz' ? 'jp-btn-primary' : ''}`}
                data-testid="games-tab-quiz">
          <Brain size={14} weight="fill" /> Quiz
        </button>
        <button onClick={() => setTab('tap')}
                className={`flex-1 py-2 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 ${tab === 'tap' ? 'jp-btn-primary' : ''}`}
                data-testid="games-tab-tap">
          <HandTap size={14} weight="fill" /> Tap
        </button>
        <button onClick={() => setTab('paid')}
                className={`flex-1 py-2 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 ${tab === 'paid' ? 'jp-btn-primary' : ''}`}
                data-testid="games-tab-paid">
          <CurrencyDollar size={14} weight="fill" /> Défi Payant
        </button>
      </div>
      {tab === 'quiz' && <QuizConfig />}
      {tab === 'tap' && <TapConfig />}
      {tab === 'paid' && <PaidDailyChallengeAdmin />}
    </div>
  );
}


function QuizConfig() {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/api/admin/games/quiz`, { withCredentials: true });
      setData(r.data); setDraft(r.data.config);
    } catch (e) { toast.error(extractErrorMessage(e, 'Chargement échoué')); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const r = await axios.put(`${API}/api/admin/games/quiz`, draft, { withCredentials: true });
      toast.success('Configuration Quiz enregistrée');
      // iter134 — Sync `draft` to the actual persisted values (server may
      // have clamped/normalised). Without this, the UI keeps showing the
      // typed values even after the server returned a different one.
      setData({ ...data, config: r.data.config });
      setDraft(r.data.config);
    } catch (e) { toast.error(extractErrorMessage(e, 'Sauvegarde échouée')); }
    finally { setSaving(false); }
  };

  if (!data) return <div className="p-6 text-center opacity-50 text-xs">Chargement…</div>;
  const set = (k, v) => setDraft({ ...draft, [k]: v });
  const Bound = ({ k }) => {
    const b = data.bounds[k];
    return b ? <span className="text-[9px] opacity-50 ml-1">({b[0]}–{b[1]})</span> : null;
  };

  return (
    <div className="space-y-3" data-testid="quiz-admin-form">
      {/* iter224 — AI Question Pool widget */}
      <QuizPoolStatusCard />
      <div className="jp-card p-4 flex items-center gap-3">
        <Power size={18} weight="fill"
               style={{ color: draft.quiz_enabled ? '#10b981' : '#E01C2E' }} />
        <div className="flex-1">
          <div className="text-sm font-bold">Module Quiz</div>
          <div className="text-[10px] opacity-60">
            Désactiver bloque /start côté joueur (503).
          </div>
        </div>
        <label className="cursor-pointer flex items-center gap-1.5 text-xs font-bold">
          <input type="checkbox" checked={Boolean(draft.quiz_enabled)}
                 onChange={(e) => set('quiz_enabled', e.target.checked)}
                 data-testid="quiz-enabled" />
          {draft.quiz_enabled ? t('games_admin.active') : t('games_admin.desactive')}
        </label>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <NumCard label={<>{t('games_admin.sessions_jour')}<Bound k="quiz_sessions_per_day"/></>}
                 value={draft.quiz_sessions_per_day}
                 onChange={(v) => set('quiz_sessions_per_day', v)}
                 testid="quiz-sessions-per-day" />
        <NumCard label={<>{t('games_admin.timer_s')}<Bound k="quiz_timer_seconds"/></>}
                 value={draft.quiz_timer_seconds}
                 onChange={(v) => set('quiz_timer_seconds', v)}
                 testid="quiz-timer-seconds" />
        <NumCard label={<>{t('games_admin.points_par_bonne_reponse')}<Bound k="quiz_points_per_correct"/></>}
                 value={draft.quiz_points_per_correct}
                 onChange={(v) => set('quiz_points_per_correct', v)}
                 testid="quiz-points-per-correct" />
        <NumCard label={<>{t('games_admin.bonus_perfect_score')}<Bound k="quiz_perfect_bonus"/></>}
                 value={draft.quiz_perfect_bonus}
                 onChange={(v) => set('quiz_perfect_bonus', v)}
                 testid="quiz-perfect-bonus" />
        <NumCard label={<>{t('games_admin.questions_session')}<Bound k="quiz_session_size"/></>}
                 value={draft.quiz_session_size}
                 onChange={(v) => set('quiz_session_size', v)}
                 testid="quiz-session-size" />
        <NumCard label={<>{t('games_admin.timer_question_s')}<Bound k="quiz_timer_per_question_seconds"/></>}
                 value={draft.quiz_timer_per_question_seconds}
                 onChange={(v) => set('quiz_timer_per_question_seconds', v)}
                 testid="quiz-timer-per-q" />
        <div className="jp-card p-3">
          <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">
            Mode timer
          </label>
          <select value={draft.quiz_timer_mode || 'per_question'}
                  onChange={(e) => set('quiz_timer_mode', e.target.value)}
                  className="jp-input text-sm w-full"
                  data-testid="quiz-timer-mode">
            <option value="per_question">{t('games_admin.par_question')}</option>
            <option value="global">{t('games_admin.global_session_entiere')}</option>
          </select>
        </div>
        <NumCard label={<>{t('games_admin.delai_feedback_de_base_ms')}<Bound k="quiz_auto_advance_delay_ms"/></>}
                 value={draft.quiz_auto_advance_delay_ms}
                 onChange={(v) => set('quiz_auto_advance_delay_ms', v)}
                 testid="quiz-aa-delay-ms" />
      </div>

      {/* iter120 — Per-question dynamic pacing (rythme dynamique) */}
      <div className="jp-card p-4">
        <div className="flex items-center justify-between mb-2">
          <div>
            <div className="text-sm font-bold">Rythme dynamique</div>
            <div className="text-[10px] opacity-60">
              Délai d'auto-advance par question (300–3000 ms). Plus rapide vers la fin = plus addictif.
            </div>
          </div>
          <label className="cursor-pointer flex items-center gap-1.5 text-xs font-bold">
            <input type="checkbox" checked={Boolean(draft.quiz_auto_advance_enabled)}
                   onChange={(e) => set('quiz_auto_advance_enabled', e.target.checked)}
                   data-testid="quiz-aa-enabled" />
            {draft.quiz_auto_advance_enabled ? 'Auto-advance ON' : 'OFF'}
          </label>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
          {(draft.quiz_auto_advance_delays_ms || [900, 800, 700, 550, 400]).map((d, i) => (
            <label key={i} className="text-[10px] opacity-70">
              Q{i + 1} (ms)
              <input type="number" value={d} min={300} max={3000} step={50}
                     onChange={(e) => {
                       const next = [...(draft.quiz_auto_advance_delays_ms || [])];
                       next[i] = parseInt(e.target.value, 10) || 900;
                       set('quiz_auto_advance_delays_ms', next);
                     }}
                     className="jp-input text-xs w-full mt-0.5"
                     data-testid={`quiz-aa-delay-${i}`} />
            </label>
          ))}
        </div>
        <div className="flex gap-2 mt-2">
          <button onClick={() => set('quiz_auto_advance_delays_ms',
                                      [...(draft.quiz_auto_advance_delays_ms || []), 600])}
                  disabled={(draft.quiz_auto_advance_delays_ms || []).length >= 20}
                  className="text-[10px] px-2 py-1 rounded jp-btn"
                  data-testid="quiz-aa-add">+ Ajouter Q</button>
          <button onClick={() => {
                    const arr = [...(draft.quiz_auto_advance_delays_ms || [])];
                    arr.pop();
                    set('quiz_auto_advance_delays_ms', arr);
                  }}
                  disabled={(draft.quiz_auto_advance_delays_ms || []).length <= 1}
                  className="text-[10px] px-2 py-1 rounded jp-btn"
                  data-testid="quiz-aa-remove">– Retirer Q</button>
          <button onClick={() => set('quiz_auto_advance_delays_ms', [900, 800, 700, 550, 400])}
                  className="text-[10px] px-2 py-1 rounded jp-btn"
                  data-testid="quiz-aa-reset">↺ Défaut</button>
        </div>
      </div>

      {/* iter122 — Learning mode toggle */}
      <div className="jp-card p-4 flex items-center gap-3">
        <Brain size={18} weight="fill"
               style={{ color: draft.quiz_show_correct_after_wrong ? '#10b981' : 'var(--jp-text-muted)' }} />
        <div className="flex-1">
          <div className="text-sm font-bold">Mode apprentissage</div>
          <div className="text-[10px] opacity-60">
            Si la réponse est mauvaise, afficher la bonne réponse en vert pendant le feedback.
            Idéal pour transformer chaque session en mini-cours culturel. Le délai feedback passe à 1.8s
            sur les mauvaises réponses pour laisser le temps de lire.
          </div>
        </div>
        <label className="cursor-pointer flex items-center gap-1.5 text-xs font-bold">
          <input type="checkbox" checked={Boolean(draft.quiz_show_correct_after_wrong)}
                 onChange={(e) => set('quiz_show_correct_after_wrong', e.target.checked)}
                 data-testid="quiz-show-correct-after-wrong" />
          {draft.quiz_show_correct_after_wrong ? t('games_admin.active') : t('games_admin.desactive')}
        </label>
      </div>

      {/* iter125 — Phase 3.B: paid challenge / escrow settings */}
      <div className="jp-card p-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-sm font-bold">Défi Champion · Mode payant (escrow)</div>
            <div className="text-[10px] opacity-60">
              Mises libres entre challenger et champion. JAPAP prélève une commission configurable.
              Refus / expiration → remboursement automatique + bonus engagement au challenger.
            </div>
          </div>
          <label className="cursor-pointer flex items-center gap-1.5 text-xs font-bold">
            <input type="checkbox" checked={Boolean(draft.quiz_challenge_paid_enabled)}
                   onChange={(e) => set('quiz_challenge_paid_enabled', e.target.checked)}
                   data-testid="quiz-challenge-paid-enabled" />
            {draft.quiz_challenge_paid_enabled ? 'Payant ON' : 'Payant OFF'}
          </label>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <NumCard label="Commission JAPAP (%)"
                   value={draft.quiz_challenge_commission_pct}
                   onChange={(v) => set('quiz_challenge_commission_pct', v)}
                   testid="quiz-challenge-commission-pct" />
          <NumCard label="Mise min"
                   value={draft.quiz_challenge_stake_min}
                   onChange={(v) => set('quiz_challenge_stake_min', v)}
                   testid="quiz-challenge-stake-min" />
          <NumCard label="Mise max"
                   value={draft.quiz_challenge_stake_max}
                   onChange={(v) => set('quiz_challenge_stake_max', v)}
                   testid="quiz-challenge-stake-max" />
          <NumCard label="Expiry (heures)"
                   value={draft.quiz_challenge_expiry_hours}
                   onChange={(v) => set('quiz_challenge_expiry_hours', v)}
                   testid="quiz-challenge-expiry-hours" />
          <NumCard label="Bonus challenger (pts)"
                   value={draft.quiz_challenge_challenger_bonus_points}
                   onChange={(v) => set('quiz_challenge_challenger_bonus_points', v)}
                   testid="quiz-challenge-bonus-pts" />
          <div className="jp-card p-3 col-span-2 sm:col-span-2 flex items-center gap-2">
            <input type="checkbox" checked={Boolean(draft.quiz_challenge_refund_on_expiry)}
                   onChange={(e) => set('quiz_challenge_refund_on_expiry', e.target.checked)}
                   data-testid="quiz-challenge-refund-on-expiry" />
            <span className="text-xs">Refund auto à l'expiration</span>
          </div>
        </div>
      </div>
      <button onClick={save} disabled={saving}
              className="jp-btn jp-btn-primary text-xs flex items-center gap-1.5 ml-auto font-bold"
              data-testid="quiz-save-btn">
        <FloppyDisk size={12} weight="fill" />
        {saving ? 'Sauvegarde…' : 'Enregistrer'}
      </button>

      {/* iter130 — Phase 3.E: Anti-repetition + Daily Challenge + AI Distribution */}
      <Phase3EBlock />
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
//  iter130 — Phase 3.E config block (anti-rep, daily, distribution, AI gen)
// ════════════════════════════════════════════════════════════════════════
function Phase3EBlock() {
  const { t } = useTranslation();
  const [dist, setDist] = useState(null);
  const [draft, setDraft] = useState({});
  const [savingDist, setSavingDist] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [genTotal, setGenTotal] = useState(20);
  const [lastGenResult, setLastGenResult] = useState(null);
  const [cats, setCats] = useState([]);
  const [quizCfg, setQuizCfg] = useState({});
  const [savingDaily, setSavingDaily] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, c, qc] = await Promise.all([
        axios.get(`${API}/api/quiz/admin/distribution`, { withCredentials: true }),
        axios.get(`${API}/api/quiz/admin/categories`,   { withCredentials: true }),
        axios.get(`${API}/api/admin/games/quiz`,        { withCredentials: true }),
      ]);
      setDist(d.data);
      setDraft(d.data);
      setCats(c.data?.items || []);
      setQuizCfg(qc.data?.config || {});
    } catch (e) { /* silent */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!dist) return null;

  const total = (parseInt(draft.quiz_dist_africa_pct) || 0)
    + (parseInt(draft.quiz_dist_sport_pct)  || 0)
    + (parseInt(draft.quiz_dist_econ_pct)   || 0)
    + (parseInt(draft.quiz_dist_world_pct)  || 0);

  const saveDist = async () => {
    if (total !== 100) {
      toast.error(`La distribution doit totaliser 100% (actuel: ${total}%)`);
      return;
    }
    setSavingDist(true);
    try {
      const r = await axios.put(`${API}/api/quiz/admin/distribution`, {
        quiz_dist_africa_pct: parseInt(draft.quiz_dist_africa_pct),
        quiz_dist_sport_pct:  parseInt(draft.quiz_dist_sport_pct),
        quiz_dist_econ_pct:   parseInt(draft.quiz_dist_econ_pct),
        quiz_dist_world_pct:  parseInt(draft.quiz_dist_world_pct),
      }, { withCredentials: true });
      setDist({ ...dist, ...r.data });
      toast.success('Distribution enregistrée');
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Sauvegarde échouée'));
    } finally { setSavingDist(false); }
  };

  const saveDailyAndAntiRepeat = async () => {
    setSavingDaily(true);
    try {
      const payload = {
        quiz_anti_repeat_days: parseInt(quizCfg.quiz_anti_repeat_days),
        quiz_daily_challenge_enabled: Boolean(quizCfg.quiz_daily_challenge_enabled),
        quiz_daily_challenge_points_per_correct: parseInt(quizCfg.quiz_daily_challenge_points_per_correct),
        quiz_daily_challenge_perfect_bonus: parseInt(quizCfg.quiz_daily_challenge_perfect_bonus),
        quiz_daily_challenge_streak_bonus_per_day: parseInt(quizCfg.quiz_daily_challenge_streak_bonus_per_day),
        quiz_daily_challenge_streak_bonus_cap: parseInt(quizCfg.quiz_daily_challenge_streak_bonus_cap),
      };
      const r = await axios.put(`${API}/api/admin/games/quiz`, payload, { withCredentials: true });
      setQuizCfg({ ...quizCfg, ...(r.data?.config || {}) });
      toast.success('Défi quotidien enregistré');
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Sauvegarde échouée'));
    } finally { setSavingDaily(false); }
  };

  const generate = async () => {
    if (!confirm(`Générer ${genTotal} questions IA selon la distribution actuelle ?\nCela coûte 4-7 appels Claude Sonnet 4.5.`)) return;
    setGenerating(true);
    setLastGenResult(null);
    try {
      const r = await axios.post(`${API}/api/quiz/admin/generate-ai`,
        { total: genTotal }, { withCredentials: true, timeout: 120000 });
      setLastGenResult(r.data);
      toast.success(`${r.data.inserted} questions ajoutées (${r.data.skipped} ignorées)`);
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Génération échouée'));
    } finally { setGenerating(false); }
  };

  const toggleCategory = async (cat, enabled) => {
    try {
      await axios.put(`${API}/api/quiz/admin/categories/${cat}`,
        { enabled }, { withCredentials: true });
      setCats(cats.map(c => c.category === cat ? { ...c, enabled } : c));
      toast.success(`${cat} ${enabled ? 'activée' : 'désactivée'}`);
    } catch (e) { toast.error(extractErrorMessage(e, 'Erreur')); }
  };

  return (
    <div className="space-y-3 mt-6 pt-6" style={{ borderTop: '2px solid var(--jp-border)' }}>
      <div className="text-[11px] uppercase tracking-widest font-bold opacity-60">
        Phase 3.E — Anti-répétition · Défi quotidien · Génération IA
      </div>

      {/* Distribution */}
      <div className="jp-card p-4">
        <div className="text-sm font-bold mb-1">Distribution catégorielle (génération IA)</div>
        <div className="text-[10px] opacity-60 mb-3">
          Total obligatoire : 100%. Affecte le picker dynamique ET la génération IA.
          <br/>Défaut : 50% Afrique · 20% Sport · 15% Éco/Crypto · 15% Général.
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-2">
          <NumCardE label="🌍 Afrique (%)" value={draft.quiz_dist_africa_pct}
                    onChange={(v) => setDraft({ ...draft, quiz_dist_africa_pct: v })}
                    testid="quiz-dist-africa" />
          <NumCardE label="⚽ Sport (%)" value={draft.quiz_dist_sport_pct}
                    onChange={(v) => setDraft({ ...draft, quiz_dist_sport_pct: v })}
                    testid="quiz-dist-sport" />
          <NumCardE label="💰 Éco/Crypto (%)" value={draft.quiz_dist_econ_pct}
                    onChange={(v) => setDraft({ ...draft, quiz_dist_econ_pct: v })}
                    testid="quiz-dist-econ" />
          <NumCardE label="🌐 Général (%)" value={draft.quiz_dist_world_pct}
                    onChange={(v) => setDraft({ ...draft, quiz_dist_world_pct: v })}
                    testid="quiz-dist-world" />
        </div>
        <div className={`text-xs font-bold mb-2 ${total === 100 ? 'text-emerald-500' : 'text-red-500'}`}
             data-testid="quiz-dist-total">
          Total : {total}% {total === 100 ? '✓' : '— ajustez pour atteindre 100%'}
        </div>
        <button onClick={saveDist} disabled={savingDist || total !== 100}
                className="jp-btn jp-btn-primary text-xs"
                data-testid="quiz-dist-save">
          {savingDist ? 'Sauvegarde…' : 'Enregistrer la distribution'}
        </button>
      </div>

      {/* AI generation */}
      <div className="jp-card p-4">
        <div className="text-sm font-bold mb-1">Génération IA — Claude Sonnet 4.5</div>
        <div className="text-[10px] opacity-60 mb-3">
          Génère N questions selon la distribution. Insertion directe dans la banque.
          Le picker dynamique les sert dès le prochain /start.
        </div>
        <div className="flex gap-2 items-center mb-2">
          <input type="number" value={genTotal}
                 onChange={(e) => setGenTotal(parseInt(e.target.value) || 20)}
                 min={4} max={100}
                 className="jp-input text-sm w-24"
                 data-testid="quiz-ai-total" />
          <button onClick={generate} disabled={generating}
                  className="jp-btn jp-btn-primary text-xs"
                  data-testid="quiz-ai-generate">
            {generating ? t('games_admin.generation') : `Générer ${genTotal} questions`}
          </button>
        </div>
        {lastGenResult && (
          <div className="text-[11px] opacity-80" data-testid="quiz-ai-result">
            ✓ {lastGenResult.inserted} insérées · {lastGenResult.skipped} ignorées
            <div className="text-[10px] opacity-60 mt-1">
              Par catégorie : {Object.entries(lastGenResult.by_category || {}).map(
                ([k, v]) => `${k}=${v}`
              ).join(', ')}
            </div>
          </div>
        )}
      </div>

      {/* Daily Challenge config */}
      <div className="jp-card p-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-sm font-bold">Défi quotidien</div>
            <div className="text-[10px] opacity-60">
              1 partie / utilisateur / jour. Bonus série jusqu'au plafond. Anti-répétition strict.
            </div>
          </div>
          <label className="cursor-pointer flex items-center gap-1.5 text-xs font-bold">
            <input type="checkbox" checked={Boolean(quizCfg.quiz_daily_challenge_enabled)}
                   onChange={(e) => setQuizCfg({ ...quizCfg, quiz_daily_challenge_enabled: e.target.checked })}
                   data-testid="quiz-daily-enabled" />
            {quizCfg.quiz_daily_challenge_enabled ? t('games_admin.active') : t('games_admin.desactive')}
          </label>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          <NumCardE label="Anti-répétition (jours)"
                    value={quizCfg.quiz_anti_repeat_days}
                    onChange={(v) => setQuizCfg({ ...quizCfg, quiz_anti_repeat_days: v })}
                    testid="quiz-anti-repeat-days" />
          <NumCardE label="Pts / bonne réponse"
                    value={quizCfg.quiz_daily_challenge_points_per_correct}
                    onChange={(v) => setQuizCfg({ ...quizCfg, quiz_daily_challenge_points_per_correct: v })}
                    testid="quiz-daily-pts-correct" />
          <NumCardE label="Bonus sans-faute"
                    value={quizCfg.quiz_daily_challenge_perfect_bonus}
                    onChange={(v) => setQuizCfg({ ...quizCfg, quiz_daily_challenge_perfect_bonus: v })}
                    testid="quiz-daily-perfect-bonus" />
          <NumCardE label="Bonus série / jour"
                    value={quizCfg.quiz_daily_challenge_streak_bonus_per_day}
                    onChange={(v) => setQuizCfg({ ...quizCfg, quiz_daily_challenge_streak_bonus_per_day: v })}
                    testid="quiz-daily-streak-per-day" />
          <NumCardE label="Plafond bonus série"
                    value={quizCfg.quiz_daily_challenge_streak_bonus_cap}
                    onChange={(v) => setQuizCfg({ ...quizCfg, quiz_daily_challenge_streak_bonus_cap: v })}
                    testid="quiz-daily-streak-cap" />
        </div>
        <button onClick={saveDailyAndAntiRepeat} disabled={savingDaily}
                className="jp-btn jp-btn-primary text-xs mt-3"
                data-testid="quiz-daily-save">
          {savingDaily ? 'Sauvegarde…' : t('games_admin.enregistrer_le_defi_quotidien')}
        </button>
      </div>

      {/* Categories list */}
      <div className="jp-card p-4">
        <div className="text-sm font-bold mb-2">Catégories de questions</div>
        <div className="text-[10px] opacity-60 mb-3">
          Désactiver une catégorie l'exclut du picker. {cats.length} catégories.
        </div>
        <div className="space-y-1.5">
          {cats.map(c => (
            <div key={c.category}
                 className="flex items-center justify-between text-xs py-1 px-2 rounded"
                 style={{ background: 'var(--jp-card-bg)' }}
                 data-testid={`quiz-cat-row-${c.category}`}>
              <div>
                <span className="font-bold">{c.category}</span>
                <span className="opacity-60 ml-2">
                  · {c.active_count} actives
                  {c.obsolete_count > 0 && ` · ${c.obsolete_count} obsolètes`}
                </span>
              </div>
              <label className="cursor-pointer flex items-center gap-1">
                <input type="checkbox" checked={Boolean(c.enabled)}
                       onChange={(e) => toggleCategory(c.category, e.target.checked)}
                       data-testid={`quiz-cat-toggle-${c.category}`} />
                <span className="text-[10px] font-bold">
                  {c.enabled ? 'ON' : 'OFF'}
                </span>
              </label>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function NumCardE({ label, value, onChange, testid }) {
  return (
    <div className="jp-card p-2">
      <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">{label}</label>
      <input type="number" value={value ?? ''}
             onChange={(e) => onChange(parseInt(e.target.value, 10))}
             className="jp-input text-sm w-full"
             data-testid={testid} />
    </div>
  );
}


function TapConfig() {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/api/admin/games/tap`, { withCredentials: true });
      setData(r.data); setDraft(r.data.config);
    } catch (e) { toast.error(extractErrorMessage(e, 'Chargement échoué')); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const r = await axios.put(`${API}/api/admin/games/tap`, draft, { withCredentials: true });
      toast.success('Configuration Tap enregistrée');
      // iter134 — Sync draft to the actual persisted values (server may
      // have sorted/normalised the thresholds list).
      setData({ ...data, config: r.data.config });
      setDraft(r.data.config);
    } catch (e) { toast.error(extractErrorMessage(e, 'Sauvegarde échouée')); }
    finally { setSaving(false); }
  };

  if (!data) return <div className="p-6 text-center opacity-50 text-xs">Chargement…</div>;
  const set = (k, v) => setDraft({ ...draft, [k]: v });
  const setTier = (idx, field, val) => {
    const list = [...(draft.tap_reward_thresholds || [])];
    list[idx] = { ...list[idx], [field]: parseInt(val, 10) || 0 };
    set('tap_reward_thresholds', list);
  };
  const addTier = () => set('tap_reward_thresholds',
    [...(draft.tap_reward_thresholds || []), { taps: 100, reward: 5 }]);
  const rmTier = (idx) => set('tap_reward_thresholds',
    (draft.tap_reward_thresholds || []).filter((_, i) => i !== idx));
  const Bound = ({ k }) => {
    const b = data.bounds[k];
    return b ? <span className="text-[9px] opacity-50 ml-1">({b[0]}–{b[1]})</span> : null;
  };

  return (
    <div className="space-y-3" data-testid="tap-admin-form">
      <div className="jp-card p-4 flex items-center gap-3">
        <Power size={18} weight="fill"
               style={{ color: draft.tap_enabled ? '#10b981' : '#E01C2E' }} />
        <div className="flex-1">
          <div className="text-sm font-bold">Module Tap Challenge</div>
          <div className="text-[10px] opacity-60">
            Désactiver bloque /start côté joueur (503).
          </div>
        </div>
        <label className="cursor-pointer flex items-center gap-1.5 text-xs font-bold">
          <input type="checkbox" checked={Boolean(draft.tap_enabled)}
                 onChange={(e) => set('tap_enabled', e.target.checked)}
                 data-testid="tap-enabled" />
          {draft.tap_enabled ? t('games_admin.active') : t('games_admin.desactive')}
        </label>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <NumCard label={<>{t('games_admin.sessions_jour')}<Bound k="tap_sessions_per_day"/></>}
                 value={draft.tap_sessions_per_day}
                 onChange={(v) => set('tap_sessions_per_day', v)}
                 testid="tap-sessions-per-day" />
        <NumCard label={<>{t('games_admin.duree_session_s')}<Bound k="tap_duration_seconds"/></>}
                 value={draft.tap_duration_seconds}
                 onChange={(v) => set('tap_duration_seconds', v)}
                 testid="tap-duration-seconds" />
        <NumCard label={<>{t('games_admin.anti_cheat_taps_s_max')}<Bound k="tap_max_taps_per_second"/></>}
                 value={draft.tap_max_taps_per_second}
                 onChange={(v) => set('tap_max_taps_per_second', v)}
                 testid="tap-max-tps" />
      </div>
      <div className="jp-card p-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-xs font-bold">Paliers de récompense</h4>
          <button onClick={addTier} className="jp-btn jp-btn-ghost text-[10px] font-bold"
                  data-testid="tap-add-tier">+ Ajouter</button>
        </div>
        <div className="space-y-2">
          {(draft.tap_reward_thresholds || []).map((t, idx) => (
            <div key={idx} className="grid grid-cols-[1fr_1fr_auto] gap-2 items-center">
              <label className="text-[10px] opacity-70">
                Taps requis
                <input type="number" value={t.taps}
                       onChange={(e) => setTier(idx, 'taps', e.target.value)}
                       className="jp-input text-xs w-full mt-0.5"
                       data-testid={`tier-taps-${idx}`} />
              </label>
              <label className="text-[10px] opacity-70">
                Récompense (XAF)
                <input type="number" value={t.reward}
                       onChange={(e) => setTier(idx, 'reward', e.target.value)}
                       className="jp-input text-xs w-full mt-0.5"
                       data-testid={`tier-reward-${idx}`} />
              </label>
              <button onClick={() => rmTier(idx)}
                      className="text-xs px-2 py-1 self-end mb-0.5"
                      style={{ color: '#E01C2E' }}
                      data-testid={`tier-remove-${idx}`}>×</button>
            </div>
          ))}
        </div>
      </div>
      <button onClick={save} disabled={saving}
              className="jp-btn jp-btn-primary text-xs flex items-center gap-1.5 ml-auto font-bold"
              data-testid="tap-save-btn">
        <FloppyDisk size={12} weight="fill" />
        {saving ? 'Sauvegarde…' : 'Enregistrer'}
      </button>
    </div>
  );
}


function NumCard({ label, value, onChange, testid }) {
  return (
    <div className="jp-card p-3">
      <label className="text-[10px] uppercase font-bold opacity-60 mb-1 block">{label}</label>
      <input type="number" value={value ?? ''}
             onChange={(e) => onChange(parseInt(e.target.value, 10))}
             className="jp-input text-sm w-full"
             data-testid={testid} />
    </div>
  );
}



/**
 * iter224 — Quiz AI Question Pool widget.
 *
 * Read-only health card displayed at the top of the Quiz tab.
 *  • Active question count + colour-coded health badge (OK / WARNING / CRITICAL)
 *  • Last refresh date + size of last batch
 *  • Live countdown to the next 48h cron refresh
 *  • Manual "Forcer renouvellement" button → POST /api/admin/games/quiz/pool-refresh
 *
 * Polls /pool-status every 30 s while the panel is open. Single-flight
 * guarded server-side, so a second click while a refresh is in flight
 * returns 409 (handled gracefully here as a toast).
 */
function QuizPoolStatusCard() {
  const [status, setStatus] = useState(null);
  const [validationStats, setValidationStats] = useState(null);  // iter233 — Mission 1
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(Date.now());

  const load = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/api/admin/games/quiz/pool-status`,
                                { withCredentials: true });
      setStatus(r.data);
    } catch (e) {
      // silent — widget is optional, errors surface in the network tab
    }
    // iter233 — pull AI validation acceptance/rejection stats in parallel.
    try {
      const v = await axios.get(`${API}/api/admin/games/quiz/validation-stats?days=30`,
                                { withCredentials: true });
      setValidationStats(v.data);
    } catch { /* widget is optional */ }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  // 1Hz tick to animate the countdown.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const triggerRefresh = async () => {
    setRefreshing(true);
    try {
      const r = await axios.post(`${API}/api/admin/games/quiz/pool-refresh`, {},
                                 { withCredentials: true });
      toast.success(r.data.message || 'Renouvellement lancé.');
      // Re-poll a couple of times in the background to reflect the in-flight flag.
      setTimeout(load, 1500);
      setTimeout(load, 8000);
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Renouvellement impossible'));
    } finally {
      setRefreshing(false);
    }
  };

  if (!status) {
    return (
      <div className="jp-card p-3 text-xs opacity-60" data-testid="quiz-pool-loading">
        Chargement de l'état du pool de questions…
      </div>
    );
  }

  const HEALTH = {
    ok:       { bg: 'rgba(16,185,129,0.10)', border: 'rgba(16,185,129,0.35)',
                label: 'Pool en bonne santé', color: '#10b981', Icon: CheckCircle },
    warning:  { bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.40)',
                label: 'Pool faible — surveillance', color: '#F59E0B', Icon: Warning },
    critical: { bg: 'rgba(224,28,46,0.12)',  border: 'rgba(224,28,46,0.40)',
                label: 'Pool critique — action requise', color: '#E01C2E', Icon: XCircle },
  };
  const meta = HEALTH[status.health] || HEALTH.ok;

  // Format countdown: HHh MMm SS s. Falls back to "—" if unknown.
  let countdown = '—';
  if (status.next_refresh_at) {
    const remain = Math.max(0, Math.floor((new Date(status.next_refresh_at).getTime() - now) / 1000));
    const h = Math.floor(remain / 3600);
    const m = Math.floor((remain % 3600) / 60);
    const s = remain % 60;
    countdown = remain > 0 ? `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s` : 'À renouveler';
  }

  const lastFmt = status.last_refresh_at
    ? new Date(status.last_refresh_at).toLocaleString('fr-FR',
        { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
    : '—';

  return (
    <div
      data-testid="quiz-pool-status-card"
      className="jp-card p-4"
      style={{ background: meta.bg, border: `1px solid ${meta.border}` }}
    >
      <div className="flex items-start gap-3">
        <Database size={20} weight="fill" style={{ color: meta.color, marginTop: 2 }} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm font-bold" style={{ color: meta.color }}
                 data-testid="quiz-pool-active-count">
              {status.active_count.toLocaleString('fr-FR')} questions actives
            </div>
            <span
              data-testid="quiz-pool-health-badge"
              className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full"
              style={{ background: meta.color, color: '#fff' }}
            >
              <meta.Icon size={10} weight="fill" style={{ display: 'inline', marginRight: 4 }} />
              {meta.label}
            </span>
            {status.refresh_in_flight && (
              <span
                data-testid="quiz-pool-refreshing-badge"
                className="text-[10px] font-bold px-2 py-0.5 rounded-full animate-pulse"
                style={{ background: '#0F056B', color: '#fff' }}
              >
                Génération en cours…
              </span>
            )}
          </div>

          <div className="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px]">
            <div data-testid="quiz-pool-last-refresh">
              <div className="opacity-60 uppercase tracking-wider text-[9px] font-bold">
                Dernier renouvellement
              </div>
              <div className="font-bold">
                {lastFmt}
                {status.last_batch_size > 0 && (
                  <span className="opacity-60 font-normal ml-1">
                    · +{status.last_batch_size} questions
                  </span>
                )}
              </div>
            </div>
            <div data-testid="quiz-pool-countdown">
              <div className="opacity-60 uppercase tracking-wider text-[9px] font-bold">
                Prochain renouvellement
              </div>
              <div className="font-bold font-mono">{countdown}</div>
            </div>
            <div data-testid="quiz-pool-config">
              <div className="opacity-60 uppercase tracking-wider text-[9px] font-bold">
                Configuration
              </div>
              <div className="font-bold">
                {status.refresh_interval_hours}h · batch {status.batch_size}
              </div>
            </div>
          </div>

          {/* iter233 — Mission 1: AI validation stats (Claude 2nd-pass) */}
          {validationStats && validationStats.batches > 0 && (
            <div className="mt-3 pt-3 border-t flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]"
                 style={{ borderColor: 'rgba(255,255,255,0.10)' }}
                 data-testid="quiz-validation-stats">
              <span className="opacity-60 uppercase tracking-wider text-[9px] font-bold">
                Validation IA (30j)
              </span>
              <span data-testid="quiz-validation-accepted">
                ✅ <strong>{validationStats.accepted.toLocaleString('fr-FR')}</strong> validées
              </span>
              <span data-testid="quiz-validation-rejected">
                ❌ <strong>{validationStats.rejected.toLocaleString('fr-FR')}</strong> rejetées
              </span>
              <span data-testid="quiz-validation-rate">
                Taux acceptation : <strong>{validationStats.accept_rate_pct}%</strong>
              </span>
              <span data-testid="quiz-validation-confidence" className="opacity-70">
                Confiance moy. : {validationStats.avg_confidence}%
              </span>
            </div>
          )}
        </div>

        <button
          onClick={triggerRefresh}
          disabled={refreshing || status.refresh_in_flight}
          data-testid="quiz-pool-refresh-button"
          className="jp-btn jp-btn-sm jp-btn-primary flex items-center gap-1.5 disabled:opacity-50 shrink-0"
        >
          <ArrowsClockwise size={12} weight="bold"
                            className={(refreshing || status.refresh_in_flight) ? 'animate-spin' : ''} />
          {refreshing || status.refresh_in_flight ? 'En cours…' : 'Forcer renouvellement'}
        </button>
      </div>
    </div>
  );
}
