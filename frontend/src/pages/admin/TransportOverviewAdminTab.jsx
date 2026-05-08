/**
 * TransportOverviewAdminTab — Phase C (P1.3 Admin Transport Dashboard).
 *
 * Operational overview: drivers (KYC status + online), rides (counts +
 * status breakdown over the window), revenue (gross + JAPAP commission),
 * daily timeseries (Recharts LineChart), top 10 earners + bottom 10 rated.
 *
 * Window selector: 7 / 14 / 30 / 60 / 90 / 365 days. All numbers are
 * computed by `GET /api/transport/admin/overview?days=N`.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Users, Car, CheckCircle, ClockCounterClockwise, XCircle, Pause,
  CurrencyCircleDollar, ChartLineUp, Star, ArrowsClockwise, Crown,
  CaretDown,
} from '@phosphor-icons/react';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts';

const API = process.env.REACT_APP_BACKEND_URL;

const WINDOWS = [7, 14, 30, 60, 90, 365];

const formatNum = (n) => Number(n || 0).toLocaleString('fr-FR', { maximumFractionDigits: 0 });


export default function TransportOverviewAdminTab() {
  const [data, setData] = useState(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/api/transport/admin/overview`, {
        params: { days }, withCredentials: true,
      });
      setData(r.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Chargement échoué');
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { load(); }, [load]);

  if (!data) {
    return (
      <div className="jp-card-elevated p-12 text-center" data-testid="transport-overview-loading">
        <ArrowsClockwise size={28} className="mx-auto opacity-30 animate-spin" />
        <p className="text-xs opacity-60 mt-2">Chargement…</p>
      </div>
    );
  }

  const { drivers, rides, revenue, timeseries, top_earners, bottom_rated } = data;

  return (
    <div className="space-y-4" data-testid="transport-overview">
      {/* Header — window selector + refresh */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-['Outfit'] font-extrabold text-base">Vue d'ensemble Transport</h2>
          <p className="text-xs opacity-60">Fenêtre glissante : {days} derniers jours</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <select
              value={days}
              onChange={(e) => setDays(parseInt(e.target.value, 10))}
              className="jp-input text-xs pr-7 appearance-none"
              data-testid="overview-window-select"
            >
              {WINDOWS.map((d) => <option key={d} value={d}>{d} j</option>)}
            </select>
            <CaretDown size={10} className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none opacity-50" />
          </div>
          <button onClick={load} disabled={loading}
                  className="jp-btn jp-btn-ghost text-xs flex items-center gap-1"
                  data-testid="overview-refresh">
            <ArrowsClockwise size={12} className={loading ? 'animate-spin' : ''} />
            Actualiser
          </button>
        </div>
      </div>

      {/* 4 big KPI cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <KpiCard
          icon={Users}
          label="Chauffeurs approuvés"
          value={formatNum(drivers.by_kyc_status.approved)}
          sub={`${drivers.online} en ligne · ${drivers.total} au total`}
          color="#10b981"
          testid="kpi-drivers"
        />
        <KpiCard
          icon={Car}
          label="Courses"
          value={formatNum(rides.total)}
          sub={`${rides.by_status.completed} terminées · ${rides.by_status.cancelled} annulées`}
          color="#0F056B"
          testid="kpi-rides"
        />
        <KpiCard
          icon={CurrencyCircleDollar}
          label="Revenus bruts"
          value={`${formatNum(revenue.gross)} ${revenue.currency}`}
          sub={`Sur ${days} jours`}
          color="#F7931A"
          testid="kpi-gross"
        />
        <KpiCard
          icon={ChartLineUp}
          label="Commission JAPAP"
          value={`${formatNum(revenue.commission)} ${revenue.currency}`}
          sub="15 % des courses terminées"
          color="#7c3aed"
          testid="kpi-commission"
        />
      </div>

      {/* Driver KYC distribution chips */}
      <div className="jp-card p-4">
        <h3 className="text-xs font-bold uppercase opacity-60 mb-3">Statut des chauffeurs</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <KycChip icon={ClockCounterClockwise} color="#F59E0B" label="En attente"
                   value={drivers.by_kyc_status.pending_review} testid="chip-pending" />
          <KycChip icon={CheckCircle} color="#10b981" label="Approuvés"
                   value={drivers.by_kyc_status.approved} testid="chip-approved" />
          <KycChip icon={XCircle} color="#E01C2E" label="Rejetés"
                   value={drivers.by_kyc_status.rejected} testid="chip-rejected" />
          <KycChip icon={Pause} color="#7f1d1d" label="Suspendus"
                   value={drivers.by_kyc_status.suspended} testid="chip-suspended" />
        </div>
      </div>

      {/* Timeseries chart */}
      <div className="jp-card p-4" data-testid="overview-timeseries">
        <h3 className="text-xs font-bold uppercase opacity-60 mb-3">
          Activité quotidienne — courses & commission
        </h3>
        {timeseries.length > 0 ? (
          <div style={{ width: '100%', height: 240 }}>
            <ResponsiveContainer>
              <LineChart data={timeseries.map(t => ({
                day: t.day, rides: t.rides,
                commission: parseFloat(t.commission),
              }))}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                <XAxis dataKey="day" fontSize={10} tickFormatter={(d) => d.slice(5)} />
                <YAxis yAxisId="left" fontSize={10} />
                <YAxis yAxisId="right" orientation="right" fontSize={10} />
                <Tooltip
                  contentStyle={{ background: 'var(--jp-surface)', border: '1px solid var(--jp-border)', fontSize: 11 }}
                  formatter={(v, name) => name === 'commission' ? [`${formatNum(v)} ${revenue.currency}`, 'Commission'] : [v, name === 'rides' ? 'Courses' : name]}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line yAxisId="left" type="monotone" dataKey="rides" stroke="#0F056B" strokeWidth={2} name="Courses" dot={{ r: 2 }} />
                <Line yAxisId="right" type="monotone" dataKey="commission" stroke="#F7931A" strokeWidth={2} name="Commission" dot={{ r: 2 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="text-center text-xs opacity-50 py-8">Aucune course terminée sur la période.</p>
        )}
      </div>

      {/* Top earners + bottom rated */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="jp-card p-4" data-testid="overview-top-earners">
          <h3 className="text-xs font-bold uppercase opacity-60 mb-3 flex items-center gap-1.5">
            <Crown size={14} weight="fill" style={{ color: '#F7931A' }} />
            Top 10 chauffeurs · revenus période
          </h3>
          {top_earners.length > 0 ? (
            <ul className="space-y-1.5">
              {top_earners.map((d, i) => (
                <li key={d.driver_id} className="flex items-center gap-2 p-2 rounded-lg"
                    style={{ background: i === 0 ? '#F7931A14' : 'var(--jp-surface-secondary)' }}>
                  <span className="text-xs font-bold opacity-50 w-5">#{i + 1}</span>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-bold truncate">{d.name || 'Chauffeur'}</div>
                    <div className="text-[10px] opacity-60 flex items-center gap-2">
                      <span>{d.completed_window} courses</span>
                      <span className="flex items-center gap-0.5">
                        <Star size={9} weight="fill" style={{ color: '#F59E0B' }} />
                        {parseFloat(d.rating).toFixed(2)}
                      </span>
                    </div>
                  </div>
                  <span className="text-xs font-extrabold tabular-nums">
                    {formatNum(d.earnings)} {revenue.currency}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-center text-xs opacity-50 py-6">Aucun chauffeur actif.</p>
          )}
        </div>

        <div className="jp-card p-4" data-testid="overview-bottom-rated">
          <h3 className="text-xs font-bold uppercase opacity-60 mb-3 flex items-center gap-1.5">
            <Star size={14} weight="fill" style={{ color: '#E01C2E' }} />
            10 notes les plus basses (≥3 avis)
          </h3>
          {bottom_rated.length > 0 ? (
            <ul className="space-y-1.5">
              {bottom_rated.map((d) => (
                <li key={d.driver_id} className="flex items-center gap-2 p-2 rounded-lg"
                    style={{ background: 'var(--jp-surface-secondary)' }}>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-bold truncate">{d.name || 'Chauffeur'}</div>
                    <div className="text-[10px] opacity-60">
                      {d.total_rides} courses · {d.total_reviews} avis
                    </div>
                  </div>
                  <span className="text-xs font-extrabold flex items-center gap-0.5"
                        style={{ color: '#E01C2E' }}>
                    <Star size={11} weight="fill" />
                    {parseFloat(d.rating).toFixed(2)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-center text-xs opacity-50 py-6">
              Aucun chauffeur avec ≥3 avis encore.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}


/* ════════════════════════════════════════════════════════════════════════ */
function KpiCard({ icon: Icon, label, value, sub, color, testid }) {
  return (
    <div className="jp-card-elevated p-4" data-testid={testid}>
      <div className="flex items-center gap-2 mb-1.5">
        <div className="w-7 h-7 rounded-lg flex items-center justify-center"
             style={{ background: color + '20' }}>
          <Icon size={15} weight="fill" style={{ color }} />
        </div>
        <span className="text-[10px] uppercase font-bold opacity-60">{label}</span>
      </div>
      <div className="text-xl font-extrabold tabular-nums">{value}</div>
      <div className="text-[10px] opacity-60 mt-0.5">{sub}</div>
    </div>
  );
}

function KycChip({ icon: Icon, color, label, value, testid }) {
  return (
    <div className="p-2.5 rounded-lg flex items-center gap-2"
         style={{ background: color + '14', border: `1px solid ${color}40` }}
         data-testid={testid}>
      <Icon size={14} weight="fill" style={{ color }} />
      <div className="flex-1 min-w-0">
        <div className="text-[10px] uppercase font-bold opacity-70">{label}</div>
        <div className="text-base font-extrabold leading-none">{formatNum(value)}</div>
      </div>
    </div>
  );
}
