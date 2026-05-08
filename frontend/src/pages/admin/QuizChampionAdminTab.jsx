/**
 * QuizChampionAdminTab — KPI dashboard for the Quiz Champion subsystem.
 * Shows GMV, JAPAP revenue, refusals hot-list, top countries, and exposes
 * one-click admin actions (expire-stale, manual demote/promote).
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { Coin, FlagBanner, Crown, ArrowsClockwise, Hourglass, ChartBar, UserMinus, UserPlus } from '@phosphor-icons/react';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;
const WIN_OPTIONS = [7, 30, 90];

function flag(cc) {
  if (!cc || cc.length !== 2) return '🏳️';
  return cc.toUpperCase().replace(/./g, c => String.fromCodePoint(127397 + c.charCodeAt(0)));
}

export default function QuizChampionAdminTab() {
  const [windowDays, setWindowDays] = useState(30);
  const [kpis, setKpis] = useState(null);
  const [champions, setChampions] = useState([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [k, c] = await Promise.all([
        axios.get(`${API}/api/quiz/champion/admin/kpis?window_days=${windowDays}`, { withCredentials: true }),
        axios.get(`${API}/api/quiz/champion/admin/list?include_demoted=false`, { withCredentials: true }),
      ]);
      setKpis(k.data);
      setChampions(c.data.items || []);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur de chargement');
    } finally { setBusy(false); }
  }, [windowDays]);

  useEffect(() => { load(); }, [load]);

  const promoteAll = async () => {
    setBusy(true);
    try {
      const { data } = await axios.post(`${API}/api/quiz/champion/admin/promote-all?window_days=7`,
        {}, { withCredentials: true });
      toast.success(`Promotion : ${data.promoted?.length || 0} nouveaux, ${data.unchanged?.length || 0} inchangés`);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setBusy(false); }
  };

  const expireStale = async () => {
    setBusy(true);
    try {
      const { data } = await axios.post(`${API}/api/quiz/champion/admin/expire-stale?limit=100`,
        {}, { withCredentials: true });
      toast.success(`${data.expired_count} défi(s) expiré(s)`);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setBusy(false); }
  };

  const demote = async (cc) => {
    if (!confirm(`Déclasser le champion de ${cc} ?`)) return;
    try {
      await axios.post(`${API}/api/quiz/champion/admin/${cc}/demote`,
        { reason: 'admin_demote' }, { withCredentials: true });
      toast.success('Champion déclassé');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    }
  };

  return (
    <div className="space-y-4" data-testid="quiz-champion-admin-tab">
      {/* Action bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex gap-1 bg-white/5 p-1 rounded-lg">
          {WIN_OPTIONS.map(w => (
            <button key={w} onClick={() => setWindowDays(w)}
                    data-testid={`qcadm-window-${w}`}
                    className="px-3 py-1 text-xs font-bold rounded transition-all"
                    style={{
                      background: windowDays === w ? 'linear-gradient(90deg, #FFD700, #F7931A)' : 'transparent',
                      color: windowDays === w ? '#111' : 'var(--jp-text)',
                    }}>
              {w}j
            </button>
          ))}
        </div>
        <button onClick={promoteAll} disabled={busy}
                data-testid="qcadm-promote-all"
                className="text-xs px-3 py-1.5 rounded-full font-bold flex items-center gap-1.5"
                style={{ background: 'linear-gradient(90deg, #10B981, #059669)', color: '#fff' }}>
          <ArrowsClockwise size={14} weight="bold" /> Recalculer champions (7j)
        </button>
        <button onClick={expireStale} disabled={busy}
                data-testid="qcadm-expire-stale"
                className="text-xs px-3 py-1.5 rounded-full font-bold flex items-center gap-1.5"
                style={{ background: 'rgba(247,147,26,0.18)', color: '#F7931A', border: '1px solid #F7931A' }}>
          <Hourglass size={14} weight="bold" /> Expirer défis stale
        </button>
        <button onClick={load} disabled={busy}
                data-testid="qcadm-refresh"
                className="text-xs px-3 py-1.5 rounded-full font-bold ml-auto"
                style={{ background: 'rgba(255,255,255,0.08)' }}>
          ↻ Refresh
        </button>
      </div>

      {!kpis ? (
        <div className="text-center py-12 opacity-60 text-sm">Chargement…</div>
      ) : (
        <>
          {/* KPI tiles */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <KpiTile icon={<ChartBar size={18} weight="fill" color="#A78BFA"/>}
                     label="Défis créés"
                     value={kpis.challenges_total.toLocaleString('fr-FR')}
                     testid="qcadm-kpi-challenges" />
            <KpiTile icon={<Coin size={18} weight="fill" color="#FFD700"/>}
                     label={`GMV (${windowDays}j)`}
                     value={`${kpis.gmv.toLocaleString('fr-FR')}`}
                     testid="qcadm-kpi-gmv" />
            <KpiTile icon={<Coin size={18} weight="fill" color="#10B981"/>}
                     label="Revenu JAPAP"
                     value={`${kpis.revenue_japap.toLocaleString('fr-FR')}`}
                     testid="qcadm-kpi-revenue" />
            <KpiTile icon={<Crown size={18} weight="fill" color="#FFD700"/>}
                     label="Champions actifs"
                     value={kpis.active_champions}
                     testid="qcadm-kpi-active-champions" />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <KpiTile small icon={<ArrowsClockwise size={14} color="#A78BFA"/>}
                     label="Refunds (XAF)"
                     value={kpis.refunds_total.toLocaleString('fr-FR')} />
            <KpiTile small label="Bonus engagement (pts)" value={kpis.engagement_bonus_pts.toLocaleString('fr-FR')} />
            <KpiTile small label="Free wins (count)" value={(kpis.by_mode || []).filter(r => r.mode==='free' && r.status==='completed').reduce((a,r)=>a+r.n,0)} />
            <KpiTile small label="Paid wins (count)" value={(kpis.by_mode || []).filter(r => r.mode==='paid' && r.status==='completed').reduce((a,r)=>a+r.n,0)} />
          </div>

          {/* Top countries */}
          <div className="jp-card p-4">
            <div className="text-sm font-bold mb-2 flex items-center gap-1.5">
              <FlagBanner size={16} weight="fill" color="#FFD700" /> Top pays — {windowDays}j
            </div>
            {(!kpis.top_countries || kpis.top_countries.length === 0) ? (
              <div className="opacity-60 text-xs italic py-3">Aucun défi sur la période.</div>
            ) : (
              <div className="space-y-1">
                {kpis.top_countries.map(c => (
                  <div key={c.country_code} className="flex items-center gap-3 py-1.5 px-2 rounded-lg"
                       style={{ background: 'rgba(255,255,255,0.04)' }}
                       data-testid={`qcadm-country-${c.country_code}`}>
                    <span className="text-lg">{flag(c.country_code)}</span>
                    <span className="font-bold text-sm">{c.country_code}</span>
                    <div className="flex-1 grid grid-cols-3 gap-1 text-[11px] text-right opacity-80">
                      <span>{c.challenges} défis</span>
                      <span>GMV {c.gmv.toLocaleString('fr-FR')}</span>
                      <span className="text-[#10B981] font-bold">+{c.revenue.toLocaleString('fr-FR')}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Refusal hot-list */}
          <div className="jp-card p-4">
            <div className="text-sm font-bold mb-2 flex items-center gap-1.5">
              <UserMinus size={16} weight="fill" color="#E01C2E" /> Champions à risque (refus)
            </div>
            {(!kpis.top_refusers || kpis.top_refusers.length === 0) ? (
              <div className="opacity-60 text-xs italic py-3">Aucun refus enregistré 👍</div>
            ) : (
              <div className="space-y-1">
                {kpis.top_refusers.map(r => (
                  <div key={r.user_id} className="flex items-center gap-2 py-1.5 px-2 rounded-lg"
                       style={{ background: 'rgba(224,28,46,0.08)' }}
                       data-testid={`qcadm-refuser-${r.user_id}`}>
                    <span className="text-lg">{flag(r.country_code)}</span>
                    {r.avatar ? (
                      <img src={r.avatar.startsWith('http') ? r.avatar : `${API}${r.avatar}`}
                           alt="" className="w-7 h-7 rounded-full object-cover" />
                    ) : (
                      <div className="w-7 h-7 rounded-full bg-white/10 flex items-center justify-center text-[10px] font-bold">
                        {(r.first_name || r.username || '?')[0].toUpperCase()}
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-bold truncate">{r.first_name || r.username}</div>
                      <div className="text-[10px] opacity-60">
                        {r.refusal_count_consecutive}/4 consécutifs · {r.refusals_30d}/4 sur 30j
                      </div>
                    </div>
                    <button onClick={() => demote(r.country_code)}
                            data-testid={`qcadm-demote-${r.country_code}`}
                            className="text-[10px] px-2 py-1 rounded-full font-bold"
                            style={{ background: '#E01C2E', color: '#fff' }}>
                      Déclasser
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* All active champions */}
          <div className="jp-card p-4">
            <div className="text-sm font-bold mb-2 flex items-center gap-1.5">
              <Crown size={16} weight="fill" color="#FFD700" /> Champions actifs ({champions.length})
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1">
              {champions.map(c => (
                <div key={c.country_code} className="flex items-center gap-2 py-1.5 px-2 rounded-lg"
                     style={{ background: 'rgba(255,215,0,0.06)' }}
                     data-testid={`qcadm-champ-${c.country_code}`}>
                  <span className="text-lg">{flag(c.country_code)}</span>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-bold truncate">{c.user.first_name || c.user.username}</div>
                    <div className="text-[10px] opacity-60">{c.country_code} · {c.source} · {c.refusal_count_30d}/4 refus 30j</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function KpiTile({ icon, label, value, small, testid }) {
  return (
    <div className={`jp-card ${small ? 'p-2' : 'p-3'}`} data-testid={testid}>
      <div className="flex items-center gap-1.5 opacity-60 mb-1">
        {icon}
        <div className={`${small ? 'text-[10px]' : 'text-[11px]'} uppercase font-bold`}>{label}</div>
      </div>
      <div className={`${small ? 'text-sm' : 'text-xl'} font-extrabold font-['Outfit']`}>{value}</div>
    </div>
  );
}
