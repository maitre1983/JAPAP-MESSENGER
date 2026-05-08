/**
 * AdminErrorMonitorTab — iter108 Phase 3 (AI Error Monitor).
 *
 * Centralised dashboard listing every error group (FE + BE) collected by the
 * `error_monitor` pipeline. Admin can:
 *   • filter by status / severity / source / module
 *   • drill-down into a group → see last 20 events
 *   • change workflow state: investigate / fix / ignore / reopen
 *   • request a Claude Sonnet 4.5 RCA + fix-hint summary
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Bug, ArrowsClockwise, MagnifyingGlass, CheckCircle, XCircle,
  ArrowCounterClockwise, Sparkle, Warning, Users, Broom,
} from '@phosphor-icons/react';
import { extractErrorMessage } from '@/utils/errorMessage';

const API = process.env.REACT_APP_BACKEND_URL;

const getSeverity_meta = (t) => ({
  critical: { color: '#7f1d1d', label: 'Critique' },
  high:     { color: '#E01C2E', label: t('admin_error_monitor.elevee') },
  medium:   { color: '#F59E0B', label: 'Moyenne' },
  low:      { color: '#10b981', label: 'Faible' },
});

const getStatus_meta = (t) => ({
  open:          { color: '#E01C2E', label: 'Ouvert' },
  investigating: { color: '#F7931A', label: 'Investigation' },
  fixed:         { color: '#10b981', label: t('admin_error_monitor.corrige') },
  ignored:       { color: '#6B7280', label: t('admin_error_monitor.ignore') },
});


export default function AdminErrorMonitorTab() {
  // (testing agent iter193) declare SEVERITY_META here — it was referenced at lines 143/201 but declared only in ErrorDetailModal scope.
  const { t } = useTranslation();
  const STATUS_META = getStatus_meta(t);
  const [data, setData] = useState({ items: [], summary: {} });
  const SEVERITY_META = getSeverity_meta(t);
  const [loading, setLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState('open');
  const [filterSeverity, setFilterSeverity] = useState('');
  const [filterSource, setFilterSource] = useState('');
  const [filterModule, setFilterModule] = useState('');
  const [selected, setSelected] = useState(null);
  const [days, setDays] = useState(30);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/api/admin/errors`, {
        params: {
          status: filterStatus, severity: filterSeverity,
          source: filterSource, module: filterModule,
          since_days: days, limit: 200,
        },
        withCredentials: true,
      });
      setData(r.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Chargement échoué');
    } finally { setLoading(false); }
  }, [filterStatus, filterSeverity, filterSource, filterModule, days]);

  useEffect(() => { load(); }, [load]);

  const action = async (signature, name) => {
    try {
      await axios.post(`${API}/api/admin/errors/${signature}/${name}`, {}, { withCredentials: true });
      toast.success(`Statut mis à jour : ${name}`);
      setSelected(null);
      load();
    } catch (e) { toast.error(extractErrorMessage(e, 'Action échouée')); }
  };

  // iter138 — Bulk action: mark every currently-visible (filtered) group as
  // 'fixed' / 'ignored' in one click. Useful after a fix-campaign has been
  // shipped so the dashboard reflects reality. The cutoff is "now - 30s" so
  // a freshly-arrived legit recurring error never gets accidentally closed.
  const [bulkBusy, setBulkBusy] = useState(false);
  const bulkAction = async (act, label) => {
    if (!confirm(
      `${label} ${data.summary?.[filterStatus + '_count'] ?? '?'} groupe(s) `
      + `(filtre actuel : ${filterStatus || 'tous'})\n\nAttention : irréversible sans intervention manuelle.`,
    )) return;
    setBulkBusy(true);
    try {
      const cutoff = new Date(Date.now() - 30_000).toISOString();
      const r = await axios.post(`${API}/api/admin/errors/bulk-action`,
        {
          action: act,
          statuses: filterStatus ? [filterStatus] : ['open', 'investigating'],
          severities: filterSeverity ? [filterSeverity] : [],
          sources: filterSource ? [filterSource] : [],
          modules: filterModule ? [filterModule] : [],
          before_iso: cutoff,
        },
        { withCredentials: true },
      );
      toast.success(`${r.data.updated} groupe(s) → ${r.data.new_status}`);
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Action en masse échouée'));
    } finally { setBulkBusy(false); }
  };

  return (
    <div className="space-y-4" data-testid="admin-error-monitor">
      {/* KPI summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {['open', 'investigating', 'fixed', 'ignored'].map((s) => {
          const meta = STATUS_META[s];
          const count = data.summary?.[`${s}_count`] || 0;
          return (
            <button key={s}
                    onClick={() => setFilterStatus(filterStatus === s ? '' : s)}
                    className="jp-card-elevated p-3 text-left transition"
                    style={{
                      borderLeft: `3px solid ${meta.color}`,
                      background: filterStatus === s ? meta.color + '14' : '',
                    }}
                    data-testid={`error-kpi-${s}`}>
              <div className="text-[10px] uppercase font-bold opacity-60">{meta.label}</div>
              <div className="text-2xl font-extrabold" style={{ color: meta.color }}>{count}</div>
              {s === 'open' && data.summary?.open_affected > 0 && (
                <div className="text-[10px] mt-1 flex items-center gap-1 opacity-60">
                  <Users size={10} /> {data.summary.open_affected} utilisateur(s)
                </div>
              )}
            </button>
          );
        })}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <select value={filterSeverity} onChange={(e) => setFilterSeverity(e.target.value)}
                className="jp-input text-xs" style={{ maxWidth: 140 }}
                data-testid="error-filter-severity">
          <option value="">{t('admin_error_monitor.toutes_gravites')}</option>
          {Object.entries(SEVERITY_META).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>
        <select value={filterSource} onChange={(e) => setFilterSource(e.target.value)}
                className="jp-input text-xs" style={{ maxWidth: 130 }}
                data-testid="error-filter-source">
          <option value="">Tous</option>
          <option value="frontend">{t('admin_error_monitor.frontend')}</option>
          <option value="backend">{t('admin_error_monitor.backend')}</option>
        </select>
        <input value={filterModule} onChange={(e) => setFilterModule(e.target.value)}
               className="jp-input text-xs" style={{ maxWidth: 160 }}
               placeholder={t('admin_error_monitor.filtrer_module')}
               data-testid="error-filter-module" />
        <select value={days} onChange={(e) => setDays(parseInt(e.target.value, 10))}
                className="jp-input text-xs" style={{ maxWidth: 100 }}>
          {[1, 7, 30, 90].map((d) => <option key={d} value={d}>{d} j</option>)}
        </select>
        <button onClick={load} disabled={loading}
                className="jp-btn jp-btn-ghost text-xs flex items-center gap-1"
                data-testid="error-refresh">
          <ArrowsClockwise size={12} className={loading ? 'animate-spin' : ''} /> Actualiser
        </button>
        {/* iter138 — Bulk-fix and bulk-ignore for post-deployment cleanup */}
        <div className="ml-auto flex gap-1.5">
          <button onClick={() => bulkAction('fix', 'Marquer comme corrigé')} disabled={bulkBusy}
                  className="jp-btn text-xs flex items-center gap-1 font-bold"
                  style={{ background: '#10b981', color: '#fff' }}
                  data-testid="error-bulk-fix">
            <Broom size={12} weight="fill" /> Tout marquer corrigé
          </button>
          <button onClick={() => bulkAction('ignore', 'Ignorer')} disabled={bulkBusy}
                  className="jp-btn jp-btn-ghost text-xs flex items-center gap-1"
                  data-testid="error-bulk-ignore">
            <XCircle size={12} /> Tout ignorer
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="jp-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead style={{ background: 'var(--jp-surface-secondary)' }}>
              <tr>
                <th className="text-left p-2.5 opacity-60">Module</th>
                <th className="text-left p-2.5 opacity-60">Message</th>
                <th className="text-center p-2.5 opacity-60">Source</th>
                <th className="text-center p-2.5 opacity-60">Gravité</th>
                <th className="text-center p-2.5 opacity-60">Statut</th>
                <th className="text-right p-2.5 opacity-60">Occ.</th>
                <th className="text-right p-2.5 opacity-60">Users</th>
                <th className="text-right p-2.5 opacity-60">Vue</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((g) => {
                const sev = SEVERITY_META[g.severity];
                const st = STATUS_META[g.status];
                return (
                  <tr key={g.signature}
                      className="hover:bg-white/5 cursor-pointer"
                      onClick={() => setSelected(g)}
                      data-testid={`error-row-${g.signature}`}
                      style={{ borderTop: '1px solid var(--jp-border)' }}>
                    <td className="p-2.5 font-mono text-[10px]">{g.module}</td>
                    <td className="p-2.5 max-w-[400px] truncate">{g.message_sample}</td>
                    <td className="p-2.5 text-center">
                      <span className="text-[10px] uppercase opacity-70">{g.source}</span>
                    </td>
                    <td className="p-2.5 text-center">
                      <span className="text-[10px] font-bold uppercase" style={{ color: sev?.color }}>
                        {sev?.label || g.severity}
                      </span>
                    </td>
                    <td className="p-2.5 text-center">
                      <span className="text-[10px] px-1.5 py-0.5 rounded font-bold uppercase"
                            style={{ background: st?.color + '20', color: st?.color }}>
                        {st?.label || g.status}
                      </span>
                    </td>
                    <td className="p-2.5 text-right tabular-nums">{g.occurrences}</td>
                    <td className="p-2.5 text-right tabular-nums opacity-70">{g.affected_users}</td>
                    <td className="p-2.5 text-right">
                      <MagnifyingGlass size={12} className="inline opacity-50" />
                    </td>
                  </tr>
                );
              })}
              {data.items.length === 0 && (
                <tr><td colSpan={8} className="p-8 text-center opacity-50">
                  Aucune erreur dans cette fenêtre. Tout va bien !
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {selected && (
        <ErrorDetailModal
          signature={selected.signature}
          onClose={() => setSelected(null)}
          onAction={action} />
      )}
    </div>
  );
}


function ErrorDetailModal({ signature, onClose, onAction }) {
  const { t } = useTranslation();
  const SEVERITY_META = getSeverity_meta(t);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/api/admin/errors/${signature}`, { withCredentials: true });
      setData(r.data);
    } catch (e) { toast.error(e.response?.data?.detail || 'Chargement échoué'); }
  }, [signature]);
  useEffect(() => { load(); }, [load]);

  const askAI = async () => {
    setBusy(true);
    try {
      const r = await axios.post(`${API}/api/admin/errors/${signature}/ai-suggest`, {}, { withCredentials: true });
      setData((d) => ({ ...d, group: { ...d.group, ai_suggestion: r.data.ai_suggestion } }));
      toast.success('Analyse IA générée');
    } catch (e) { toast.error(e.response?.data?.detail || 'IA indisponible'); }
    finally { setBusy(false); }
  };

  if (!data) return null;
  const g = data.group;
  const sev = SEVERITY_META[g.severity];
  const sug = g.ai_suggestion;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
         onClick={onClose}
         data-testid="error-detail-modal">
      <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-2xl p-5 space-y-3"
           style={{ background: 'var(--jp-surface)' }}
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 mb-0.5">
              <Bug size={14} weight="fill" style={{ color: sev?.color }} />
              <code className="text-[10px] opacity-50">{g.signature}</code>
            </div>
            <h3 className="font-bold text-sm font-mono">{g.module}</h3>
            <p className="text-xs opacity-70 mt-1 break-words">{g.message_sample}</p>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <div className="p-2 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
            <div className="text-[9px] opacity-60 uppercase">Occurrences</div>
            <div className="text-base font-extrabold tabular-nums">{g.occurrences}</div>
          </div>
          <div className="p-2 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
            <div className="text-[9px] opacity-60 uppercase">Utilisateurs</div>
            <div className="text-base font-extrabold tabular-nums">{g.affected_users}</div>
          </div>
          <div className="p-2 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
            <div className="text-[9px] opacity-60 uppercase">Vu</div>
            <div className="text-[10px] opacity-70">
              {new Date(g.first_seen).toLocaleDateString('fr-FR')} →
              {' '}{new Date(g.last_seen).toLocaleDateString('fr-FR')}
            </div>
          </div>
        </div>

        {/* AI suggestion */}
        {sug ? (
          <div className="jp-card p-3" data-testid="error-ai-suggestion">
            <div className="flex items-center gap-1.5 mb-2">
              <Sparkle size={12} weight="fill" style={{ color: '#7c3aed' }} />
              <span className="text-xs font-bold">Analyse IA Claude Sonnet 4.5</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded font-bold uppercase ml-auto"
                    style={{ background: SEVERITY_META[sug.urgency || 'medium']?.color + '20',
                             color: SEVERITY_META[sug.urgency || 'medium']?.color }}>
                {SEVERITY_META[sug.urgency || 'medium']?.label || 'Moyenne'}
              </span>
            </div>
            {sug.summary && <p className="text-xs leading-relaxed mb-2">{sug.summary}</p>}
            {sug.root_cause && (
              <>
                <div className="text-[10px] uppercase opacity-60 font-bold mt-2">Cause racine</div>
                <p className="text-xs opacity-80 leading-relaxed">{sug.root_cause}</p>
              </>
            )}
            {Array.isArray(sug.fix_hint) && sug.fix_hint.length > 0 && (
              <>
                <div className="text-[10px] uppercase opacity-60 font-bold mt-2">Pistes de correction</div>
                <ul className="text-xs opacity-80 list-disc pl-4 space-y-0.5">
                  {sug.fix_hint.map((h, i) => <li key={i}>{h}</li>)}
                </ul>
              </>
            )}
          </div>
        ) : (
          <button onClick={askAI} disabled={busy}
                  className="jp-btn jp-btn-ghost text-xs flex items-center gap-1.5"
                  data-testid="error-ai-suggest-btn">
            <Sparkle size={12} weight="fill" />
            {busy ? t('admin_error_monitor.generation') : "Demander une analyse IA"}
          </button>
        )}

        {/* Recent events */}
        <div className="jp-card p-3">
          <div className="text-[10px] uppercase opacity-60 font-bold mb-2">
            Derniers événements ({data.events.length})
          </div>
          <ul className="space-y-1.5 text-[11px] max-h-[200px] overflow-y-auto">
            {data.events.map((e) => (
              <li key={e.id}
                  className="p-2 rounded-md"
                  style={{ background: 'var(--jp-surface-secondary)' }}>
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span className="opacity-60">{new Date(e.occurred_at).toLocaleString('fr-FR')}</span>
                  {e.http_status && <span className="font-mono opacity-70">HTTP {e.http_status}</span>}
                  {e.url && <span className="opacity-50 truncate flex-1">{e.url}</span>}
                </div>
                <p className="opacity-80 break-words">{e.message}</p>
              </li>
            ))}
          </ul>
        </div>

        {/* Actions */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2">
          <button onClick={() => onAction(signature, 'investigate')}
                  disabled={g.status === 'investigating'}
                  className="jp-btn text-xs font-bold flex items-center justify-center gap-1.5"
                  style={{ background: '#F7931A', color: 'white' }}
                  data-testid="error-action-investigate">
            <Warning size={11} weight="bold" /> Investiguer
          </button>
          <button onClick={() => onAction(signature, 'fix')}
                  disabled={g.status === 'fixed'}
                  className="jp-btn text-xs font-bold flex items-center justify-center gap-1.5"
                  style={{ background: '#10b981', color: 'white' }}
                  data-testid="error-action-fix">
            <CheckCircle size={11} weight="bold" /> Corrigé
          </button>
          <button onClick={() => onAction(signature, 'ignore')}
                  disabled={g.status === 'ignored'}
                  className="jp-btn text-xs font-bold flex items-center justify-center gap-1.5"
                  style={{ background: '#6B7280', color: 'white' }}
                  data-testid="error-action-ignore">
            <XCircle size={11} weight="bold" /> Ignorer
          </button>
          <button onClick={() => onAction(signature, 'reopen')}
                  disabled={g.status === 'open'}
                  className="jp-btn jp-btn-ghost text-xs font-bold flex items-center justify-center gap-1.5"
                  data-testid="error-action-reopen">
            <ArrowCounterClockwise size={11} weight="bold" /> Réouvrir
          </button>
        </div>
      </div>
    </div>
  );
}
