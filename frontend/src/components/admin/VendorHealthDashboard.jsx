/**
 * iter239h — VendorHealthDashboard
 * =================================
 * Real-time admin view of every critical 3rd-party vendor we depend on:
 * Hubtel MoMo, Paystack, Tronscan, BSC RPC, FX API, Fixie outbound proxy.
 *
 * Backend cron pings each vendor every 5 minutes; this UI polls
 * `/api/admin/vendor-health/status` every 30s and renders a status board
 * with traffic-light verdicts (OK / SLOW / DOWN) + latency + last check.
 *
 * The "Actualiser maintenant" button triggers `/refresh` on the backend,
 * which forces a synchronous re-ping of every vendor and returns the
 * fresh snapshot.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Heartbeat, ArrowsClockwise, CheckCircle, Warning, XCircle } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;
const POLL_MS = 30000;

const STATUS_META = {
  ok:   { color: '#10B981', bg: '#D1FAE5', label: '✓ OK',   icon: CheckCircle },
  slow: { color: '#D97706', bg: '#FEF3C7', label: '⏱ SLOW', icon: Warning },
  down: { color: '#DC2626', bg: '#FEE2E2', label: '✗ DOWN', icon: XCircle },
  unknown: { color: '#6B7280', bg: '#F3F4F6', label: '? ATTENTE', icon: Warning },
};

export default function VendorHealthDashboard() {
  const [vendors, setVendors] = useState({});
  const [interval_, setInterval_] = useState(300);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/vendor-health/status`,
                                        { withCredentials: true });
      setVendors(data.vendors || {});
      setInterval_(data.interval_seconds || 300);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement vendor health');
    } finally {
      setLoading(false);
    }
  }, []);

  const forceRefresh = async () => {
    setRefreshing(true);
    try {
      const { data } = await axios.post(`${API}/api/admin/vendor-health/refresh`, {},
                                         { withCredentials: true, timeout: 30000 });
      setVendors(data.vendors || {});
      toast.success('Vendors re-pingés');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur refresh');
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    pollRef.current = setInterval(load, POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [load]);

  const entries = Object.entries(vendors);
  const okCount = entries.filter(([_n, v]) => v.status === 'ok').length;

  return (
    <div className="jp-card-elevated p-5" data-testid="vendor-health-dashboard">
      <div className="flex items-center justify-between gap-2 flex-wrap mb-1">
        <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2">
          <Heartbeat size={20} weight="duotone" /> Santé des Vendors
          <span className="text-[10px] font-normal opacity-60">
            (auto-refresh {interval_ / 60} min • poll UI 30 s)
          </span>
        </h3>
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            {okCount}/{entries.length} OK
          </span>
          <button
            type="button"
            onClick={forceRefresh}
            disabled={refreshing}
            className="jp-btn jp-btn-sm text-xs"
            style={{ background: 'var(--jp-primary)', color: 'white',
                     opacity: refreshing ? 0.6 : 1 }}
            data-testid="vendor-health-refresh">
            <ArrowsClockwise size={12} weight="bold"
              className={refreshing ? 'animate-spin' : ''} />
            {refreshing ? 'Refresh…' : 'Actualiser maintenant'}
          </button>
        </div>
      </div>
      <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
        Ping périodique de chaque dépendance externe. Vert = OK • Orange = lent (≥1.5s) • Rouge = down.
      </p>

      {loading && entries.length === 0 && (
        <div className="text-sm text-center py-6"
             style={{ color: 'var(--jp-text-muted)' }}>
          Chargement initial — premier ping en cours…
        </div>
      )}

      <div className="space-y-2">
        {entries.map(([name, v]) => <VendorRow key={name} name={name} v={v} />)}
      </div>

      {!loading && entries.length === 0 && (
        <div className="p-3 rounded-lg text-xs" style={{ background: '#FEF3C7', color: '#92400E' }}>
          ⚠️ Aucun vendor n'a encore été pingé. La boucle se lance au prochain
          tick — utilisez le bouton "Actualiser maintenant" pour forcer.
        </div>
      )}
    </div>
  );
}

function VendorRow({ name, v }) {
  const meta = STATUS_META[v.status] || STATUS_META.unknown;
  const Icon = meta.icon;
  const lastCheck = v.last_check
    ? new Date(v.last_check).toLocaleTimeString('fr-FR', {
        hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '—';
  const age = v.last_check
    ? Math.round((Date.now() - new Date(v.last_check).getTime()) / 1000)
    : null;
  return (
    <div className="flex items-center gap-3 p-3 rounded-xl"
         style={{ background: meta.bg, color: meta.color }}
         data-testid={`vendor-row-${name.toLowerCase().replace(/[^a-z]/g, '-')}`}>
      <Icon size={20} weight="duotone" className="shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <strong>{name}</strong>
          <span className="text-[10px] px-2 py-0.5 rounded-full font-bold"
                style={{ background: meta.color, color: 'white' }}>
            {meta.label}
          </span>
          {v.latency_ms != null && (
            <span className="text-xs font-mono">
              {v.latency_ms}ms
            </span>
          )}
          {v.http_code && (
            <span className="text-[10px] opacity-70">HTTP {v.http_code}</span>
          )}
        </div>
        {v.error && (
          <div className="text-xs opacity-90 mt-0.5"
               data-testid={`vendor-error-${name.toLowerCase().replace(/[^a-z]/g, '-')}`}>
            {v.error}
          </div>
        )}
        {v.extra && (
          <details className="mt-1 text-[10px] opacity-70">
            <summary style={{ cursor: 'pointer' }}>détails techniques</summary>
            <pre className="font-mono text-[9px] mt-1 overflow-x-auto">
              {JSON.stringify(v.extra, null, 2)}
            </pre>
          </details>
        )}
      </div>
      <div className="text-[10px] text-right opacity-70 shrink-0">
        <div>{lastCheck}</div>
        {age != null && <div>il y a {age}s</div>}
      </div>
    </div>
  );
}
