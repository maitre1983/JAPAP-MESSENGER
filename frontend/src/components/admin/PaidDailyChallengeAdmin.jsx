/**
 * iter237k — Admin : Défi du jour PAYANT.
 *
 * 1. Configuration : toggle activation, mises min/max, plafond gain/jour,
 *    barème gains/pertes par score (5 lignes éditables).
 * 2. Dashboard revenus : sessions, win rate, mises totales, score moyen,
 *    revenus nets Japap (= -SUM(amount_won_usd) — pertes joueurs - gains versés).
 * 3. Bouton "Régénérer pool maintenant" (forçage cron Claude).
 *
 * Strictement additif. Pas de modification du panneau Quiz/Tap existant.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  CurrencyDollar, ChartBar, ArrowsClockwise, FloppyDisk, Trophy, Database,
} from '@phosphor-icons/react';
import { extractErrorMessage } from '@/utils/errorMessage';

const API = process.env.REACT_APP_BACKEND_URL;

export default function PaidDailyChallengeAdmin() {
  const [cfg, setCfg] = useState(null);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [stats, setStats] = useState(null);
  const [days, setDays] = useState(30);
  const [refreshing, setRefreshing] = useState(false);

  const loadCfg = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/daily-challenge/paid/config`,
                                        { withCredentials: true });
      setCfg(data);
      // Initialize draft once with config values
      setDraft({
        DCQ_PAID_ENABLED:        !!data.enabled,
        DCQ_STAKE_MIN_USD:       Number(data.dcq_stake_min_usd),
        DCQ_STAKE_MAX_USD:       Number(data.dcq_stake_max_usd),
        DCQ_DAILY_GAIN_CAP_USD:  Number(data.dcq_daily_gain_cap_usd),
        DCQ_SCORE_5_PCT:         Number(data.dcq_score_5_pct),
        DCQ_SCORE_4_PCT:         Number(data.dcq_score_4_pct),
        DCQ_SCORE_3_PCT:         Number(data.dcq_score_3_pct),
        DCQ_SCORE_2_PCT:         Number(data.dcq_score_2_pct),
        DCQ_SCORE_01_PCT:        Number(data.dcq_score_01_pct),
        DCQ_TIME_PER_QUESTION:   Number(data.dcq_time_per_question),
      });
    } catch (e) {
      // iter237k — never freeze the UI on a transient load error.
      // Persist a "load_failed" sentinel so the render path leaves the
      // "Chargement..." state and shows a retry hint.
      console.warn('[PaidDailyChallengeAdmin] config load failed', e);
      setCfg({ load_error: extractErrorMessage(e, 'Chargement config échoué') });
      toast.error(extractErrorMessage(e, 'Chargement config échoué'));
    }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      const { data } = await axios.get(
        `${API}/api/admin/daily-challenge/paid/stats?days=${days}`,
        { withCredentials: true });
      setStats(data);
    } catch (e) { toast.error(extractErrorMessage(e, 'Chargement stats échoué')); }
  }, [days]);

  useEffect(() => { loadCfg(); }, [loadCfg]);
  useEffect(() => { loadStats(); }, [loadStats]);

  const save = async () => {
    setSaving(true);
    try {
      await axios.put(`${API}/api/admin/daily-challenge/paid/config`, draft,
                       { withCredentials: true });
      toast.success('Configuration enregistrée');
      loadCfg();
    } catch (e) { toast.error(extractErrorMessage(e, 'Sauvegarde échouée')); }
    finally { setSaving(false); }
  };

  const refreshPool = async () => {
    setRefreshing(true);
    try {
      await axios.post(`${API}/api/admin/daily-challenge/paid/refresh-pool`, {},
                        { withCredentials: true });
      toast.success('Régénération dispatchée — vérifie le pool dans 1-2 min.');
      setTimeout(() => loadStats(), 60_000);
    } catch (e) { toast.error(extractErrorMessage(e, 'Échec dispatch')); }
    finally { setRefreshing(false); }
  };

  const setNum = (k) => (e) => setDraft(prev => ({ ...prev, [k]: parseFloat(e.target.value) }));
  const setBool = (k) => () => setDraft(prev => ({ ...prev, [k]: !prev[k] }));

  if (!cfg) {
    return <div className="text-xs opacity-60 p-4" data-testid="paid-daily-challenge-admin">Chargement…</div>;
  }
  if (cfg.load_error) {
    return (
      <div className="jp-card-elevated p-5 text-sm" data-testid="paid-daily-challenge-admin">
        <div className="text-red-600 font-bold mb-2">⚠️ Échec de chargement</div>
        <div className="text-xs opacity-80 mb-3">{cfg.load_error}</div>
        <button onClick={() => { setCfg(null); loadCfg(); }}
                className="jp-btn jp-btn-sm jp-btn-primary">
          Réessayer
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="paid-daily-challenge-admin">
      {/* Activation */}
      <div className="jp-card-elevated p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2">
            <CurrencyDollar size={20} weight="duotone" style={{ color: '#F7931A' }} />
            Défi du jour PAYANT
          </h3>
          <button onClick={setBool('DCQ_PAID_ENABLED')}
                  data-testid="dcq-paid-toggle"
                  className="relative w-12 h-6 rounded-full transition-colors shrink-0"
                  style={{ background: draft.DCQ_PAID_ENABLED ? '#10B981' : '#D1D5DB' }}>
            <div className="absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform"
                 style={{ transform: draft.DCQ_PAID_ENABLED ? 'translateX(26px)' : 'translateX(2px)' }} />
          </button>
        </div>
        <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
          Mode payant indépendant du défi gratuit. Solde débité immédiatement,
          gains/pertes selon score. 1 session par utilisateur par jour.
        </p>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <Field label="Mise min (USD)"   v={draft.DCQ_STAKE_MIN_USD}      on={setNum('DCQ_STAKE_MIN_USD')}      step="0.1" tid="dcq-stake-min" />
          <Field label="Mise max (USD)"   v={draft.DCQ_STAKE_MAX_USD}      on={setNum('DCQ_STAKE_MAX_USD')}      step="1"   tid="dcq-stake-max" />
          <Field label="Plafond gain/jour (USD)" v={draft.DCQ_DAILY_GAIN_CAP_USD} on={setNum('DCQ_DAILY_GAIN_CAP_USD')} step="1" tid="dcq-daily-cap" />
          <Field label="Temps / question (sec)"  v={draft.DCQ_TIME_PER_QUESTION}  on={setNum('DCQ_TIME_PER_QUESTION')}  step="1" tid="dcq-time" />
        </div>

        <div className="mt-4">
          <p className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color: 'var(--jp-text-muted)' }}>
            Barème gains/pertes (%)
          </p>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Field label="5/5 🏆" v={draft.DCQ_SCORE_5_PCT}  on={setNum('DCQ_SCORE_5_PCT')}  step="1" tid="dcq-score-5" />
            <Field label="4/5"    v={draft.DCQ_SCORE_4_PCT}  on={setNum('DCQ_SCORE_4_PCT')}  step="1" tid="dcq-score-4" />
            <Field label="3/5"    v={draft.DCQ_SCORE_3_PCT}  on={setNum('DCQ_SCORE_3_PCT')}  step="1" tid="dcq-score-3" />
            <Field label="2/5"    v={draft.DCQ_SCORE_2_PCT}  on={setNum('DCQ_SCORE_2_PCT')}  step="1" tid="dcq-score-2" />
            <Field label="0-1/5"  v={draft.DCQ_SCORE_01_PCT} on={setNum('DCQ_SCORE_01_PCT')} step="1" tid="dcq-score-01" />
          </div>
        </div>

        <button onClick={save} disabled={saving}
                data-testid="dcq-paid-save"
                className="jp-btn jp-btn-primary mt-4 inline-flex items-center gap-1.5">
          <FloppyDisk size={14} /> {saving ? 'Enregistrement…' : 'Enregistrer'}
        </button>
      </div>

      {/* Stats / Revenus */}
      <div className="jp-card-elevated p-5" data-testid="paid-stats">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2">
            <ChartBar size={18} weight="duotone" style={{ color: '#0F056B' }} />
            Revenus Défi Payant — {days} derniers jours
          </h3>
          <select className="jp-input text-xs" style={{ minWidth: '110px' }}
                  value={days} onChange={e => setDays(parseInt(e.target.value, 10))}
                  data-testid="dcq-stats-days">
            <option value={7}>7 jours</option>
            <option value={30}>30 jours</option>
            <option value={90}>90 jours</option>
            <option value={365}>1 an</option>
          </select>
        </div>

        {stats ? (
          <>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
              <KPI label="Sessions aujourd'hui" value={stats.today_sessions} testId="dcq-kpi-today-sessions" />
              <KPI label={`Sessions (${stats.days_window}j)`} value={stats.period_sessions} testId="dcq-kpi-period-sessions" />
              <KPI label="Sessions totales" value={stats.total_sessions} testId="dcq-kpi-total-sessions" />
              <KPI label="Taux de victoire" value={`${stats.win_rate_pct}%`} icon={<Trophy size={14} weight="fill" style={{ color: '#FFD700' }} />} testId="dcq-kpi-win-rate" />
              <KPI label="Score moyen / 5" value={stats.avg_score} testId="dcq-kpi-avg-score" />
              <KPI label="Mise moyenne (USD)" value={`$${stats.avg_stake_usd}`} testId="dcq-kpi-avg-stake" />
            </div>
            <div className="rounded-xl p-4 mb-4"
                 style={{ background: 'linear-gradient(135deg, rgba(255,215,0,0.08), rgba(247,147,26,0.06))',
                          border: '1px solid rgba(247,147,26,0.25)' }}
                 data-testid="dcq-revenue-block">
              <div className="text-xs uppercase tracking-wider opacity-70 font-bold mb-1">
                💰 Revenus nets Japap (pertes joueurs − gains versés)
              </div>
              <div className="grid grid-cols-3 gap-3 mt-2">
                <div>
                  <div className="text-[11px] opacity-70">Aujourd'hui</div>
                  <div className="font-['Outfit'] text-xl font-extrabold"
                       data-testid="dcq-rev-today"
                       style={{ color: stats.today_revenue_usd >= 0 ? '#065F46' : '#991B1B' }}>
                    {stats.today_revenue_usd >= 0 ? '+' : ''}${stats.today_revenue_usd}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] opacity-70">{stats.days_window}j</div>
                  <div className="font-['Outfit'] text-xl font-extrabold"
                       data-testid="dcq-rev-period"
                       style={{ color: stats.period_revenue_usd >= 0 ? '#065F46' : '#991B1B' }}>
                    {stats.period_revenue_usd >= 0 ? '+' : ''}${stats.period_revenue_usd}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] opacity-70">Total</div>
                  <div className="font-['Outfit'] text-xl font-extrabold"
                       data-testid="dcq-rev-total"
                       style={{ color: stats.total_revenue_usd >= 0 ? '#065F46' : '#991B1B' }}>
                    {stats.total_revenue_usd >= 0 ? '+' : ''}${stats.total_revenue_usd}
                  </div>
                </div>
              </div>
              <div className="text-[10px] opacity-60 mt-2">
                Mises totales encaissées : ${stats.total_stakes_usd.toLocaleString('fr-FR')}
              </div>
            </div>
          </>
        ) : (
          <div className="text-xs opacity-60">Chargement stats…</div>
        )}

        <div className="rounded-xl p-3 flex items-center justify-between gap-3"
             style={{ background: 'var(--jp-surface-secondary)' }}>
          <div>
            <div className="text-xs font-bold flex items-center gap-1.5">
              <Database size={14} /> Pool de questions
            </div>
            <div className="text-[11px] opacity-70 mt-0.5">
              Actives : <strong data-testid="dcq-pool-active">{stats?.pool_active || 0}</strong>
              {stats?.pool_last_generation && (
                <> · Dernière régen : {new Date(stats.pool_last_generation).toLocaleString('fr-FR')}</>
              )}
            </div>
          </div>
          <button onClick={refreshPool} disabled={refreshing}
                  data-testid="dcq-pool-refresh"
                  className="jp-btn jp-btn-sm inline-flex items-center gap-1.5"
                  style={{ background: 'var(--jp-primary)', color: 'white' }}>
            <ArrowsClockwise size={12} /> {refreshing ? 'Dispatch…' : 'Régénérer'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, v, on, step, tid }) {
  return (
    <div>
      <label className="jp-label text-xs">{label}</label>
      <input type="number" step={step} value={v ?? ''} onChange={on}
             data-testid={tid}
             className="jp-input text-sm" />
    </div>
  );
}

function KPI({ label, value, icon, testId }) {
  return (
    <div className="rounded-xl p-3" style={{ background: 'var(--jp-surface-secondary)' }}
         data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wider opacity-60 font-bold flex items-center gap-1">
        {icon} {label}
      </div>
      <div className="font-['Outfit'] text-xl font-extrabold mt-1">{value}</div>
    </div>
  );
}
