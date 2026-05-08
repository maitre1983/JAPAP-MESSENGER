/**
 * GamesEngagementAdminTab — Quiz JAPAP + Tap Challenge observability.
 *
 * Shows :
 *   - Quiz : runs, players, points distributed, avg accuracy, top 10, daily
 *   - Tap  : runs, players, points distributed, avg/max taps, cheat attempts, top 10
 *   - Reset user controls (wipes history, audited)
 *   - Regenerate AI questions bank button
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import {
  Question, HandTap, Users, Target, Warning, Crown, ArrowCounterClockwise,
  Sparkle, Trophy, Gear,
} from '@phosphor-icons/react';
import GamesAdminTab from '@/pages/admin/GamesAdminTab';

const API = process.env.REACT_APP_BACKEND_URL;

export default function GamesEngagementAdminTab() {
  const { t } = useTranslation();
  const [view, setView] = useState('stats'); // stats | config
  return (
    <div>
      <div className="flex gap-1 mb-4 p-1 rounded-xl"
           style={{ background: 'var(--jp-surface-secondary)' }}>
        <button onClick={() => setView('stats')}
                className={`flex-1 py-2 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 ${view === 'stats' ? 'jp-btn-primary' : ''}`}
                data-testid="games-view-stats">
          <Trophy size={14} weight="fill" /> Statistiques
        </button>
        <button onClick={() => setView('config')}
                className={`flex-1 py-2 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 ${view === 'config' ? 'jp-btn-primary' : ''}`}
                data-testid="games-view-config">
          <Gear size={14} weight="fill" /> Configuration
        </button>
      </div>
      {view === 'stats' ? <GamesEngagementStats /> : <GamesAdminTab />}
    </div>
  );
}


function GamesEngagementStats() {
  const { t } = useTranslation();
  const [days, setDays] = useState(30);
  const [quiz, setQuiz] = useState(null);
  const [tap, setTap] = useState(null);
  const [loading, setLoading] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [q, t] = await Promise.all([
        axios.get(`${API}/api/quiz/admin/overview?days=${days}`, { withCredentials: true }),
        axios.get(`${API}/api/tap/admin/overview?days=${days}`, { withCredentials: true }),
      ]);
      setQuiz(q.data); setTap(t.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Chargement impossible');
    } finally { setLoading(false); }
  }, [days]);

  useEffect(() => { load(); }, [load]);

  const resetUser = async (game, userId, userName) => {
    const label = game === 'quiz' ? 'Quiz' : 'Tap';
    if (!window.confirm(`⚠ Effacer tout l'historique ${label} de ${userName} ? Cette action est tracée et irréversible.`)) return;
    setResetting(true);
    try {
      await axios.post(`${API}/api/${game}/admin/reset-user/${userId}`, {}, { withCredentials: true });
      toast.success(`${label} — historique de ${userName} réinitialisé`);
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setResetting(false); }
  };

  const regenerateAi = async () => {
    const raw = window.prompt('Combien de sessions de 5 questions générer ? (10–500)', '100');
    const n = parseInt(raw || '0');
    if (!n || n < 10 || n > 500) return;
    if (!window.confirm(`Régénérer ${n} sessions quiz via l'IA Claude 4.5 ? Cela peut prendre 1–2 minutes.`)) return;
    setRegenerating(true);
    try {
      const { data } = await axios.post(`${API}/api/quiz/admin/regenerate-ai?target_sessions=${n}`,
        {}, { withCredentials: true });
      toast.success(`Banque IA régénérée : ${data.inserted_questions || 0} questions · ${data.inserted_sessions || 0} sessions`);
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Échec de la régénération');
    } finally { setRegenerating(false); }
  };

  if (loading && !quiz && !tap) {
    return <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;
  }

  return (
    <div className="space-y-6 jp-animate-fadeIn" data-testid="games-engagement-admin-tab">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 className="font-['Outfit'] text-xl font-bold" style={{ color: 'var(--jp-text)' }}>
            Engagement Quiz &amp; Tap
          </h2>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            Moteur unifié de points · cycle 30 jours · 10 000 pts + 25 jours + 75 % quiz → Starter Pro
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select className="jp-input text-xs" value={days} onChange={(e) => setDays(parseInt(e.target.value))}
                  data-testid="games-admin-window">
            <option value={7}>7 jours</option>
            <option value={14}>14 jours</option>
            <option value={30}>30 jours</option>
            <option value={60}>60 jours</option>
            <option value={90}>90 jours</option>
          </select>
          <button onClick={load} disabled={loading}
                  className="jp-btn jp-btn-ghost jp-btn-sm"
                  data-testid="games-admin-refresh">
            <ArrowCounterClockwise size={14} /> Rafraîchir
          </button>
        </div>
      </div>

      {/* ═════════════ QUIZ SECTION ═════════════ */}
      {quiz && (
        <section className="space-y-4" data-testid="quiz-admin-section">
          <div className="flex items-center gap-2">
            <Question size={22} weight="fill" style={{ color: '#8B5CF6' }} />
            <h3 className="font-['Outfit'] text-lg font-bold">Quiz JAPAP</h3>
            <button onClick={regenerateAi} disabled={regenerating}
                    data-testid="quiz-regenerate-ai-btn"
                    className="ml-auto jp-btn jp-btn-ghost jp-btn-sm"
                    title={t('games_engagement_admin.regenerer_via_ia')}>
              <Sparkle size={14} /> {regenerating ? t('games_engagement_admin.generation') : t('games_engagement_admin.regenerer_ia')}
            </button>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MiniStat icon={Target} label="Sessions jouées" value={quiz.runs} color="#8B5CF6" />
            <MiniStat icon={Users} label="Joueurs" value={quiz.players} color="var(--jp-primary)" />
            <MiniStat icon={Sparkle} label="Points distribués" value={quiz.points_distributed.toLocaleString('fr-FR')} color="#F59E0B" />
            <MiniStat icon={Trophy} label="Précision moyenne" value={`${Math.round(quiz.avg_accuracy * 100)}%`}
                      color={quiz.avg_accuracy >= 0.75 ? '#10B981' : '#E01C2E'} />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <InfoLine label="Points moyens / session" value={`+${quiz.avg_points_per_run}`} />
            <InfoLine label="Durée moyenne session" value={`${Number(quiz.avg_duration_seconds || 0).toFixed(1)} s`} />
            <InfoLine label="Sessions timed-out"
                      value={quiz.timed_out_runs}
                      color={quiz.timed_out_runs > 0 ? '#F59E0B' : 'var(--jp-text-muted)'} />
            <InfoLine label="Banque active"
                      value={`${quiz.bank.active} Q · ${quiz.bank.sessions} sessions`} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Score bucket distribution */}
            <div className="jp-card-elevated p-4" data-testid="quiz-buckets-card">
              <h4 className="font-['Outfit'] text-sm font-bold mb-3">Distribution des scores (0–5 bonnes réponses)</h4>
              {quiz.buckets.length === 0 ? (
                <EmptyBlock label="Aucune session dans la fenêtre" />
              ) : (
                <ResponsiveContainer width="100%" height={180}>
                  <BarChart data={normaliseBuckets(quiz.buckets)}>
                    <XAxis dataKey="score" fontSize={11} />
                    <YAxis fontSize={11} />
                    <Tooltip />
                    <Bar dataKey="n" name="Sessions" fill="#8B5CF6" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
            {/* Daily timeseries */}
            <div className="jp-card-elevated p-4">
              <h4 className="font-['Outfit'] text-sm font-bold mb-3">Sessions par jour</h4>
              {quiz.timeseries.length === 0 ? (
                <EmptyBlock label="Aucune donnée" />
              ) : (
                <ResponsiveContainer width="100%" height={180}>
                  <BarChart data={quiz.timeseries}>
                    <XAxis dataKey="day" fontSize={10} />
                    <YAxis fontSize={11} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Bar dataKey="runs" name="Sessions" fill="#8B5CF6" radius={[6, 6, 0, 0]} />
                    <Bar dataKey="points" name="Points" fill="#F59E0B" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <TopPlayersTable
            data-testid="quiz-top-table"
            players={quiz.top_players}
            onReset={(p) => resetUser('quiz', p.user_id, p.name)}
            resetting={resetting}
            extraCols={[
              { key: 'runs', label: 'Sessions' },
              { key: 'accuracy', label: t('games_engagement_admin.precision'), format: (v) => `${Math.round((v ?? 0) * 100)}%` },
            ]}
          />

          <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
            Banque de questions : <strong>{quiz.bank.active}</strong> actives · <strong>{quiz.bank.inactive}</strong> inactives ·{' '}
            <strong>{quiz.bank.sessions}</strong> sessions de 5Q · total brut <strong>{quiz.bank.total}</strong>
          </div>
        </section>
      )}

      {/* ═════════════ TAP SECTION ═════════════ */}
      {tap && (
        <section className="space-y-4" data-testid="tap-admin-section">
          <div className="flex items-center gap-2">
            <HandTap size={22} weight="fill" style={{ color: '#E01C2E' }} />
            <h3 className="font-['Outfit'] text-lg font-bold">Tap Challenge</h3>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MiniStat icon={Target} label="Sessions jouées" value={tap.runs} color="#E01C2E" />
            <MiniStat icon={Users} label="Joueurs" value={tap.players} color="var(--jp-primary)" />
            <MiniStat icon={Sparkle} label="Points distribués" value={tap.points_distributed.toLocaleString('fr-FR')} color="#F59E0B" />
            <MiniStat icon={Trophy} label="Record taps" value={tap.max_taps_ever} color="#FFD700" />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-sm">
            <InfoLine label="Taps totaux" value={tap.taps_total.toLocaleString('fr-FR')} />
            <InfoLine label="Taps moyen / session" value={tap.avg_taps_per_run} />
            <InfoLine label="P95 taps / session"
                      value={tap.p95_taps}
                      color={tap.p95_taps >= 100 ? '#F59E0B' : 'var(--jp-text-muted)'} />
            <InfoLine
              label="Runs suspects / triche"
              value={`${tap.suspicious_runs} susp. · ${tap.cheat_attempts} cap`}
              color={(tap.suspicious_runs + tap.cheat_attempts) > 0 ? '#E01C2E' : 'var(--jp-text-muted)'}
              icon={(tap.suspicious_runs + tap.cheat_attempts) > 0 ? Warning : null}
            />
          </div>

          {tap.timeseries.length > 0 && (
            <div className="jp-card-elevated p-4">
              <h4 className="font-['Outfit'] text-sm font-bold mb-3">Activité Tap par jour</h4>
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={tap.timeseries}>
                  <XAxis dataKey="day" fontSize={10} />
                  <YAxis fontSize={11} />
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="runs" name="Sessions" fill="#E01C2E" radius={[6, 6, 0, 0]} />
                  <Bar dataKey="avg_taps" name="Taps moy." fill="#F59E0B" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          <TopPlayersTable
            data-testid="tap-top-table"
            players={tap.top_players}
            onReset={(p) => resetUser('tap', p.user_id, p.name)}
            resetting={resetting}
            extraCols={[
              { key: 'runs', label: 'Sessions' },
              { key: 'best_taps', label: 'Record taps' },
            ]}
          />
        </section>
      )}
    </div>
  );
}

/* ─────────────── sub-components ─────────────── */

function MiniStat({ icon: Icon, label, value, color }) {
  return (
    <div className="jp-stat-card" data-testid={`games-admin-stat-${slug(label)}`}>
      <div className="jp-stat-icon" style={{ background: `${color}1a` }}>
        <Icon size={16} weight="duotone" style={{ color }} />
      </div>
      <div className="jp-stat-label">{label}</div>
      <div className="jp-stat-value" style={{ color }}>{value}</div>
    </div>
  );
}

function InfoLine({ label, value, color, icon: Icon }) {
  return (
    <div className="jp-card p-3 flex items-center justify-between">
      <span className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>{label}</span>
      <span className="font-['Outfit'] font-bold flex items-center gap-1"
            style={{ color: color || 'var(--jp-text)' }}>
        {Icon && <Icon size={14} weight="fill" />}
        {value}
      </span>
    </div>
  );
}

function EmptyBlock({ label }) {
  return (
    <div className="py-8 text-center text-xs" style={{ color: 'var(--jp-text-muted)' }}>
      {label}
    </div>
  );
}

function TopPlayersTable({ players, onReset, resetting, extraCols = [] }) {
  const { t } = useTranslation();
  if (!players.length) {
    return (
      <div className="jp-card-elevated p-4">
        <EmptyBlock label="Aucun joueur dans la fenêtre." />
      </div>
    );
  }
  return (
    <div className="jp-card-elevated p-4 overflow-x-auto">
      <h4 className="font-['Outfit'] text-sm font-bold mb-3 flex items-center gap-2">
        <Crown size={16} weight="fill" style={{ color: '#F59E0B' }} /> Top 10 joueurs
      </h4>
      <table className="jp-table">
        <thead>
          <tr>
            <th>#</th>
            <th>{t('games_engagement_admin.joueur')}</th>
            <th>{t('games_engagement_admin.points')}</th>
            {extraCols.map((c) => <th key={c.key}>{c.label}</th>)}
            <th className="text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {players.map((p, i) => (
            <tr key={p.user_id}>
              <td><strong>{i + 1}</strong></td>
              <td>
                <div className="flex items-center gap-2">
                  <div className="jp-avatar jp-avatar-sm jp-avatar-primary">
                    {(p.name?.[0] || '?').toUpperCase()}
                  </div>
                  <div>
                    <div className="font-medium">{p.name}</div>
                    <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{p.email}</div>
                  </div>
                </div>
              </td>
              <td><span className="font-['Outfit'] font-bold" style={{ color: '#F59E0B' }}>
                +{p.points?.toLocaleString('fr-FR')}
              </span></td>
              {extraCols.map((c) => (
                <td key={c.key}>{c.format ? c.format(p[c.key]) : p[c.key]}</td>
              ))}
              <td className="text-right">
                <button onClick={() => onReset(p)} disabled={resetting}
                        className="jp-btn jp-btn-ghost jp-btn-sm text-xs"
                        data-testid={`reset-player-${p.user_id}`}>
                  <ArrowCounterClockwise size={12} /> Reset
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ─────────────── helpers ─────────────── */

function normaliseBuckets(buckets) {
  // Ensure 0..5 are all present for a stable x-axis.
  const map = new Map(buckets.map((b) => [b.score, b.n]));
  return [0, 1, 2, 3, 4, 5].map((s) => ({ score: s, n: map.get(s) || 0 }));
}

function slug(label) {
  return String(label).toLowerCase().replace(/[^a-z0-9]+/g, '-');
}
