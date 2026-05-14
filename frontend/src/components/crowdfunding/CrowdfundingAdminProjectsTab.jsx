// iter240d — Onglet partagé d'administration des projets Crowdfunding.
// Affiche TOUS les projets de TOUS les statuts avec filtres en pills,
// rendu par batch, avec actions inline via <AdminProjectActions>.
// Utilisé à la fois dans CrowdfundingModule.AdminPanel ET dans AdminPage.
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import { ArrowsClockwise } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

// iter240d — Liste exhaustive des statuts admin. L'ordre conditionne celui des pills.
const STATUSES = [
  { key: 'pending_review', emoji: '⏳', i18n: 'admin_filter_pending', fallback: 'En attente' },
  { key: 'active',         emoji: '✅', i18n: 'admin_filter_active',  fallback: 'Actifs' },
  { key: 'suspended',      emoji: '⏸️', i18n: 'admin_filter_suspended', fallback: 'Suspendus' },
  { key: 'disqualified',   emoji: '🚫', i18n: 'admin_filter_disqualified', fallback: 'Disqualifiés' },
  { key: 'expired',        emoji: '💀', i18n: 'admin_filter_expired', fallback: 'Expirés' },
  { key: 'winner',         emoji: '🏆', i18n: 'admin_filter_winner', fallback: 'Gagnants' },
  { key: 'deleted',        emoji: '🗑️', i18n: 'admin_filter_deleted', fallback: 'Supprimés' },
];

const STATUS_BADGE_COLOR = {
  pending_review: 'bg-amber-100 text-amber-800',
  active: 'bg-emerald-100 text-emerald-800',
  suspended: 'bg-orange-100 text-orange-800',
  disqualified: 'bg-rose-100 text-rose-800',
  expired: 'bg-slate-200 text-slate-700',
  winner: 'bg-yellow-200 text-yellow-900',
  deleted: 'bg-slate-100 text-slate-500 line-through',
  cancelled: 'bg-slate-200 text-slate-600',
};

function fmtRel(iso, t) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    const h = Math.floor(diff / 3_600_000);
    if (h < 1) return t('crowdfunding.rel_just_now', { defaultValue: 'à l\'instant' });
    if (h < 24) return t('crowdfunding.rel_hours_ago', { defaultValue: '{{h}}h', h });
    const days = Math.floor(h / 24);
    return t('crowdfunding.rel_days_ago', { defaultValue: '{{d}}j', d: days });
  } catch { return '—'; }
}

export default function CrowdfundingAdminProjectsTab({ ActionsComponent }) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState('pending_review'); // par défaut : ouvert sur les projets à modérer
  const [items, setItems] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(false);

  const fetchList = useCallback(async (status) => {
    setLoading(true);
    try {
      const url = `${API}/api/crowdfunding/admin/projects` + (status ? `?status=${encodeURIComponent(status)}&limit=200` : `?limit=200`);
      const { data } = await axios.get(url, { withCredentials: true });
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      toast.error(t('crowdfunding.admin_projects_load_failed', { defaultValue: 'Impossible de charger les projets.' }));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [t]);

  // iter240d — Récupération en parallèle des compteurs par statut pour les pills.
  const fetchCounts = useCallback(async () => {
    try {
      const results = await Promise.all(
        STATUSES.map((s) =>
          axios
            .get(`${API}/api/crowdfunding/admin/projects?status=${s.key}&limit=500`, { withCredentials: true })
            .then((r) => [s.key, Array.isArray(r.data) ? r.data.length : 0])
            .catch(() => [s.key, 0]),
        ),
      );
      const map = {};
      for (const [k, n] of results) map[k] = n;
      setCounts(map);
    } catch { /* silencieux */ }
  }, []);

  useEffect(() => { fetchList(filter); }, [filter, fetchList]);
  useEffect(() => { fetchCounts(); }, [fetchCounts]);

  const reload = () => { fetchList(filter); fetchCounts(); };

  return (
    <section data-testid="cf-admin-projects-tab" className="space-y-4">
      {/* Pills filtres + compteur */}
      <div className="flex flex-wrap gap-2 items-center">
        <button
          type="button"
          onClick={() => setFilter('')}
          data-testid="cf-admin-projects-filter-all"
          className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition ${filter === '' ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-700 border-slate-200 hover:border-slate-400'}`}>
          {t('crowdfunding.admin_filter_all', { defaultValue: 'Tous' })}
        </button>
        {STATUSES.map((s) => {
          const n = counts[s.key] ?? 0;
          const isActive = filter === s.key;
          const urgent = s.key === 'pending_review' && n > 0;
          return (
            <button
              key={s.key}
              type="button"
              onClick={() => setFilter(s.key)}
              data-testid={`cf-admin-projects-filter-${s.key}`}
              className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition flex items-center gap-1.5 ${isActive ? 'bg-rose-600 text-white border-rose-600' : urgent ? 'bg-amber-50 text-amber-800 border-amber-300 animate-pulse' : 'bg-white text-slate-700 border-slate-200 hover:border-slate-400'}`}>
              <span aria-hidden>{s.emoji}</span>
              {t(`crowdfunding.${s.i18n}`, { defaultValue: s.fallback })}
              <span className={`ml-0.5 px-1.5 rounded-full text-[10px] font-mono ${isActive ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-600'}`}>{n}</span>
            </button>
          );
        })}
        <button
          type="button"
          onClick={reload}
          data-testid="cf-admin-projects-reload"
          className="ml-auto p-2 rounded-full hover:bg-slate-100 text-slate-600"
          aria-label={t('common.reload', { defaultValue: 'Actualiser' })}>
          <ArrowsClockwise size={16} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Liste */}
      {loading ? (
        <div className="text-center text-slate-500 text-sm py-10">{t('crowdfunding.admin_projects_loading', { defaultValue: 'Chargement…' })}</div>
      ) : items.length === 0 ? (
        <div className="text-center text-slate-500 text-sm py-10 border border-dashed border-slate-200 rounded-xl" data-testid="cf-admin-projects-empty">
          {t('crowdfunding.admin_projects_empty', { defaultValue: 'Aucun projet dans ce filtre.' })}
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((p) => (
            <article
              key={p.project_id}
              data-testid={`cf-admin-projects-item-${p.slug}`}
              className="bg-white border border-slate-200 rounded-xl p-3 sm:p-4 hover:shadow-sm transition">
              <div className="flex flex-wrap items-start justify-between gap-2 mb-2">
                <div className="min-w-0 flex-1">
                  <h4 className="font-bold text-slate-900 text-sm sm:text-base truncate flex items-center gap-1.5" title={p.title}>
                    {p.is_priority && (
                      <span
                        data-testid={`cf-admin-projects-priority-${p.slug}`}
                        title={t('crowdfunding.fast_track_active_badge', { defaultValue: 'Priority Boost actif' })}
                        className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] font-extrabold bg-gradient-to-r from-amber-400 to-rose-500 text-white flex-shrink-0">
                        ⚡ PRIORITY
                      </span>
                    )}
                    <span className="truncate">{p.title || '—'}</span>
                  </h4>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {p.owner_name || t('crowdfunding.anonymous', { defaultValue: 'Anonyme' })}
                    {' · '}{fmtRel(p.created_at, t)}
                  </p>
                </div>
                <span
                  className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide ${STATUS_BADGE_COLOR[p.status] || 'bg-slate-100 text-slate-700'}`}
                  data-testid={`cf-admin-projects-status-${p.slug}`}>
                  {p.status}
                </span>
              </div>

              <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-600 mb-2">
                <span>📂 {p.category || '—'}</span>
                <span>🌍 {p.country_code || '—'}</span>
                <span>🗳️ {p.votes_count ?? 0} {t('crowdfunding.votes', { defaultValue: 'votes' })}</span>
                {p.suspension_reason && <span className="text-orange-700">⏸️ {p.suspension_reason}</span>}
                {p.moderation_reason && <span className="text-rose-700">📝 {p.moderation_reason}</span>}
              </div>

              {ActionsComponent ? <ActionsComponent project={p} onChanged={reload} /> : null}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

// iter240d — Hook utilitaire pour le badge pulsant sur le bouton ⚙️ Admin.
// Renvoie { count, urgent } où urgent=true si au moins 1 pending_review > 24h.
export function usePendingReviewBadge(intervalMs = 60_000) {
  const [state, setState] = useState({ count: 0, urgent: false });

  useEffect(() => {
    let alive = true;
    const fetchIt = async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/crowdfunding/admin/projects?status=pending_review&limit=500`,
          { withCredentials: true },
        );
        if (!alive) return;
        const arr = Array.isArray(data) ? data : [];
        const now = Date.now();
        const urgent = arr.some((p) => {
          if (!p.created_at) return false;
          const age = now - new Date(p.created_at).getTime();
          return age > 24 * 3_600_000;
        });
        setState({ count: arr.length, urgent });
      } catch {
        if (alive) setState({ count: 0, urgent: false });
      }
    };
    fetchIt();
    const t = setInterval(fetchIt, intervalMs);
    return () => { alive = false; clearInterval(t); };
  }, [intervalMs]);

  return state;
}
