/**
 * iter239d — StorageAdminCard
 * ============================
 * Admin UI: Cloudflare R2 media bucket stats + one-click migration from
 * the local ephemeral filesystem (`/app/backend/uploads/`) to R2.
 *
 * Mounted from PaymentsAdminTab.jsx (next to PaystackSettings & Hubtel
 * Settings) so all infra/config cards live in one tab. Purely additive.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { CloudArrowUp, ArrowsClockwise, HardDrives } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function StorageAdminCard() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [migrating, setMigrating] = useState(false);
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/admin/storage/stats`,
                                        { withCredentials: true });
      setStats(data);
      setMigrating(Boolean(data?.migration?.running));
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement stockage');
    } finally {
      setLoading(false);
    }
  }, []);

  const startMigration = async () => {
    try {
      const { data } = await axios.post(`${API}/api/admin/storage/migrate-to-r2`, {},
                                         { withCredentials: true });
      if (data.status === 'already_running') {
        toast.info('Migration déjà en cours');
      } else {
        toast.success('Migration lancée en arrière-plan');
      }
      setMigrating(true);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur démarrage migration');
    }
  };

  // Poll migration status every 5s while running.
  useEffect(() => {
    if (!migrating) {
      if (pollRef.current) clearInterval(pollRef.current);
      return undefined;
    }
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await axios.get(`${API}/api/admin/storage/migration-status`,
                                          { withCredentials: true });
        if (!data.running) {
          setMigrating(false);
          if (data.result?.migrated > 0) {
            toast.success(`Migration terminée : ${data.result.migrated} fichiers vers R2`);
          }
          load();
        }
      } catch { /* ignore */ }
    }, 5000);
    return () => clearInterval(pollRef.current);
  }, [migrating, load]);

  useEffect(() => { load(); }, [load]);

  if (loading && !stats) {
    return <div className="jp-card-elevated p-5 text-sm" style={{ color: 'var(--jp-text-muted)' }}>
      Chargement stockage…
    </div>;
  }

  const r2 = stats?.r2 || {};
  const local = stats?.local || {};
  const migration = stats?.migration || {};
  const r2ok = r2.ok !== false;

  return (
    <div className="jp-card-elevated p-5" data-testid="storage-admin-card">
      <div className="flex items-center justify-between gap-2 flex-wrap mb-1">
        <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2">
          <HardDrives size={20} weight="duotone" /> Stockage Médias — Cloudflare R2
        </h3>
        <button
          type="button"
          onClick={load}
          className="jp-btn jp-btn-ghost jp-btn-sm text-xs"
          data-testid="storage-refresh"
          title="Rafraîchir">
          <ArrowsClockwise size={12} /> Actualiser
        </button>
      </div>
      <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
        Les uploads (images, vidéos, miniatures, avatars) sont poussés vers
        R2 en priorité. Le filesystem local sert de fallback éphémère uniquement.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        <StatBlock
          title="📂 Filesystem local (éphémère)"
          color="#FEF3C7"
          textColor="#92400E"
          rows={[
            ['Fichiers', local.file_count ?? '—'],
            ['Taille', local.size_mb != null ? `${local.size_mb} MB` : '—'],
            ['Chemin', <code key="p" style={{ fontSize: 10 }}>{local.path || '/app/backend/uploads'}</code>],
          ]}
          testId="storage-local-stats"
        />
        <StatBlock
          title={r2ok ? '☁️ R2 (persistant)' : '☁️ R2 — erreur'}
          color={r2ok ? '#D1FAE5' : '#FEE2E2'}
          textColor={r2ok ? '#065F46' : '#991B1B'}
          rows={r2ok ? [
            ['Bucket', r2.bucket || '—'],
            ['Fichiers', r2.total_files ?? '—'],
            ['Taille', r2.total_size_mb != null ? `${r2.total_size_mb} MB` : '—'],
            ['CDN', <code key="u" style={{ fontSize: 10 }}>{r2.public_url || '—'}</code>],
          ] : [
            ['Erreur', r2.error || 'inconnue'],
          ]}
          testId="storage-r2-stats"
        />
      </div>

      <div className="flex flex-wrap gap-2 items-center">
        <button
          type="button"
          onClick={startMigration}
          disabled={migrating || !r2ok || (local.file_count ?? 0) === 0}
          className="jp-btn jp-btn-primary"
          data-testid="storage-migrate-btn">
          <CloudArrowUp size={14} weight="bold" />
          {migrating ? 'Migration en cours…' : `🚀 Migrer ${local.file_count || 0} fichiers locaux → R2`}
        </button>
        {migration?.started_at && (
          <span className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
            Démarré à {new Date(migration.started_at).toLocaleTimeString('fr-FR')}
            {migration.ended_at && ` • Terminé à ${new Date(migration.ended_at).toLocaleTimeString('fr-FR')}`}
          </span>
        )}
      </div>

      {migration?.result && !migrating && (
        <div className="mt-3 p-3 rounded-xl text-xs" style={{ background: 'var(--jp-surface-secondary)' }}
             data-testid="storage-migration-result">
          <strong>Résultat dernière migration :</strong>{' '}
          {migration.result.error
            ? <span style={{ color: '#991B1B' }}>{migration.result.error}</span>
            : <>
                ✅ <strong>{migration.result.migrated || 0}</strong> migrés
                {migration.result.failed > 0 && <> • ❌ <strong>{migration.result.failed}</strong> échecs</>}
                {' '}sur <strong>{migration.result.total || 0}</strong> au total
              </>}
        </div>
      )}

      {/* iter239g — Legacy posts variant regeneration */}
      <RegenerateVariantsBlock />

      {/* iter239h — Orphan media cleanup */}
      <OrphanCleanupBlock />

      <div className="mt-4 p-3 rounded-xl text-[11px]"
           style={{ background: '#EFF6FF', color: '#1E40AF' }}>
        💡 La migration est <strong>idempotente</strong> — vous pouvez la
        relancer en toute sécurité. Les fichiers déjà sur R2 ne seront pas
        re-uploadés (les chemins en DB sont également mis à jour).
      </div>
    </div>
  );
}

/** iter239g — separate block, polls its own status every 4s when running. */
function RegenerateVariantsBlock() {
  const [state, setState] = useState(null);
  const [running, setRunning] = useState(false);
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/storage/regenerate-status`,
                                        { withCredentials: true });
      setState(data);
      setRunning(Boolean(data?.running));
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!running) {
      if (pollRef.current) clearInterval(pollRef.current);
      return undefined;
    }
    pollRef.current = setInterval(load, 4000);
    return () => clearInterval(pollRef.current);
  }, [running, load]);

  const start = async () => {
    try {
      const { data } = await axios.post(`${API}/api/admin/storage/regenerate-variants`,
                                         {}, { withCredentials: true });
      if (data.status === 'already_running') {
        toast.info('Régénération déjà en cours');
      } else {
        toast.success('Régénération des variantes lancée en arrière-plan');
      }
      setRunning(true);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur démarrage régénération');
    }
  };

  const progress = state?.total_posts
    ? Math.round((state.scanned_posts / state.total_posts) * 100)
    : 0;

  return (
    <div className="mt-4 p-3 rounded-xl border" data-testid="storage-regen-block"
         style={{ borderColor: 'rgba(0,0,0,0.1)' }}>
      <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
        <h4 className="font-bold text-sm">🔄 Régénérer variants WebP/AVIF (legacy)</h4>
        <button
          type="button"
          onClick={start}
          disabled={running}
          className="jp-btn jp-btn-sm text-xs"
          style={{ background: 'var(--jp-primary)', color: 'white',
                   opacity: running ? 0.6 : 1 }}
          data-testid="storage-regen-btn">
          {running ? `${progress}% — ${state?.scanned_posts}/${state?.total_posts}` : '🚀 Lancer régénération'}
        </button>
      </div>
      <p className="text-[11px] mb-2" style={{ color: 'var(--jp-text-muted)' }}>
        Reprend tous les posts existants qui n'ont pas encore les 6 variantes
        (WebP + AVIF × 3 tailles). Idempotent — relançable à tout moment.
      </p>
      {running && (
        <div className="h-2 rounded-full overflow-hidden"
             style={{ background: 'rgba(0,0,0,0.08)' }}>
          <div className="h-full transition-all"
               style={{ width: `${progress}%`, background: 'var(--jp-primary)' }} />
        </div>
      )}
      {state && (state.scanned_posts > 0 || state.ended_at) && (
        <div className="mt-2 grid grid-cols-2 sm:grid-cols-4 gap-2 text-[10px]"
             data-testid="storage-regen-stats">
          <Stat label="Posts scannés"     value={`${state.scanned_posts} / ${state.total_posts}`} />
          <Stat label="Posts mis à jour"  value={state.updated_posts} />
          <Stat label="Variantes créées"  value={state.regenerated_entries} />
          <Stat label="Sautées (déjà OK)" value={state.skipped_entries} />
          {state.failed_entries > 0 && <Stat label="❌ Échecs" value={state.failed_entries} color="#991B1B" />}
          {state.current_post_id && running && <Stat label="En cours" value={state.current_post_id.slice(0, 16) + '…'} />}
        </div>
      )}
      {!!state?.errors?.length && (
        <details className="mt-2 text-[10px]">
          <summary style={{ cursor: 'pointer', color: '#991B1B' }}>
            {state.errors.length} erreurs (voir détails)
          </summary>
          <ul className="ml-3 mt-1" style={{ color: '#991B1B' }}>
            {state.errors.slice(0, 10).map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

/** iter239h — orphan media cleanup block (legacy posts.media entries
 * whose source file is gone from both local FS and R2). Strictly additive:
 * read-only scan first, then explicit "Nettoyer" gate with confirm, then
 * background sweep with live progress.
 */
function OrphanCleanupBlock() {
  const [scan, setScan] = useState(null);         // {total_posts, posts_with_orphans, orphan_entries, sample_post_ids}
  const [scanning, setScanning] = useState(false);
  const [state, setState] = useState(null);       // cleanup live state
  const [running, setRunning] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const pollRef = useRef(null);

  const loadStatus = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/storage/cleanup-orphans-status`,
                                        { withCredentials: true });
      setState(data);
      setRunning(Boolean(data?.running));
    } catch { /* ignore */ }
  }, []);

  const scanNow = async () => {
    setScanning(true);
    try {
      const { data } = await axios.get(`${API}/api/admin/storage/scan-orphans`,
                                        { withCredentials: true });
      setScan(data);
      toast.success(`Scan terminé — ${data.orphan_entries} entrée(s) orpheline(s) sur ${data.posts_with_orphans} post(s)`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur scan orphelins');
    } finally {
      setScanning(false);
    }
  };

  const startCleanup = async () => {
    setConfirmOpen(false);
    try {
      const { data } = await axios.post(`${API}/api/admin/storage/cleanup-orphans`,
                                         {}, { withCredentials: true });
      if (data.status === 'already_running') {
        toast.info('Nettoyage déjà en cours');
      } else {
        toast.success('Nettoyage des orphelins lancé en arrière-plan');
      }
      setRunning(true);
      loadStatus();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur démarrage nettoyage');
    }
  };

  useEffect(() => { loadStatus(); }, [loadStatus]);

  useEffect(() => {
    if (!running) {
      if (pollRef.current) clearInterval(pollRef.current);
      return undefined;
    }
    pollRef.current = setInterval(loadStatus, 3000);
    return () => clearInterval(pollRef.current);
  }, [running, loadStatus]);

  const progress = state?.total_posts
    ? Math.round((state.scanned_posts / state.total_posts) * 100)
    : 0;
  const orphanCount = scan?.orphan_entries ?? null;

  return (
    <div className="mt-4 p-3 rounded-xl border" data-testid="storage-orphan-cleanup-block"
         style={{ borderColor: 'rgba(0,0,0,0.1)' }}>
      <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
        <h4 className="font-bold text-sm">🧹 Entrées media orphelines</h4>
        <div className="flex gap-2 flex-wrap">
          <button
            type="button"
            onClick={scanNow}
            disabled={scanning || running}
            className="jp-btn jp-btn-sm text-xs"
            style={{ background: 'var(--jp-surface-secondary)', color: 'var(--jp-text)',
                     opacity: (scanning || running) ? 0.6 : 1 }}
            data-testid="storage-orphan-scan-btn">
            {scanning ? '⏳ Scan…' : '🔍 Scanner'}
          </button>
          <button
            type="button"
            onClick={() => setConfirmOpen(true)}
            disabled={running || !orphanCount}
            className="jp-btn jp-btn-sm text-xs"
            style={{ background: '#DC2626', color: 'white',
                     opacity: (running || !orphanCount) ? 0.5 : 1 }}
            data-testid="storage-orphan-cleanup-btn">
            {running ? `${progress}% — ${state?.scanned_posts}/${state?.total_posts}`
                     : `🧹 Nettoyer ${orphanCount || 0} entrée(s)`}
          </button>
        </div>
      </div>
      <p className="text-[11px] mb-2" style={{ color: 'var(--jp-text-muted)' }}>
        Supprime de la table <code>posts.media</code> les références <em>ghost</em>
        (fichiers absents à la fois du disque local et de R2). Ces entrées
        renvoient un 404 en prod aujourd'hui. Idempotent + audit-loggé.
      </p>
      {scan && (
        <div className="mb-2 grid grid-cols-2 sm:grid-cols-3 gap-2 text-[10px]"
             data-testid="storage-orphan-scan-stats">
          <Stat label="Posts scannés" value={scan.total_posts} />
          <Stat label="Posts avec orphelins" value={scan.posts_with_orphans} />
          <Stat label="Entrées orphelines" value={scan.orphan_entries}
                color={scan.orphan_entries > 0 ? '#991B1B' : '#065F46'} />
        </div>
      )}
      {running && (
        <div className="h-2 rounded-full overflow-hidden mb-2"
             style={{ background: 'rgba(0,0,0,0.08)' }}>
          <div className="h-full transition-all"
               style={{ width: `${progress}%`, background: '#DC2626' }} />
        </div>
      )}
      {state && (state.scanned_posts > 0 || state.ended_at) && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[10px]"
             data-testid="storage-orphan-cleanup-stats">
          <Stat label="Posts scannés"    value={`${state.scanned_posts} / ${state.total_posts}`} />
          <Stat label="Posts nettoyés"   value={state.posts_cleaned} />
          <Stat label="Entrées retirées" value={state.entries_removed} color="#991B1B" />
          <Stat label="Orphelins trouvés" value={state.orphan_entries_found} />
        </div>
      )}
      {!!state?.errors?.length && (
        <details className="mt-2 text-[10px]">
          <summary style={{ cursor: 'pointer', color: '#991B1B' }}>
            {state.errors.length} erreurs (voir détails)
          </summary>
          <ul className="ml-3 mt-1" style={{ color: '#991B1B' }}>
            {state.errors.slice(0, 10).map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </details>
      )}

      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
             style={{ background: 'rgba(0,0,0,0.6)' }}>
          <div className="jp-card-elevated max-w-md w-full p-5 jp-animate-scaleIn"
               data-testid="storage-orphan-confirm-modal">
            <h4 className="font-['Outfit'] text-lg font-bold mb-2">
              ⚠️ Confirmer le nettoyage
            </h4>
            <p className="text-sm mb-3">
              <strong>{orphanCount}</strong> entrée(s) ghost vont être retirées
              de <code>posts.media</code> sur <strong>{scan?.posts_with_orphans}</strong> post(s).
            </p>
            <div className="p-3 rounded-xl text-[11px] mb-3"
                 style={{ background: '#FEF3C7', color: '#92400E' }}>
              Action <strong>irréversible</strong> mais audit-loggée. Les posts
              qui n'ont que des orphelins basculeront en text-only (plus de
              balises <code>&lt;img&gt;</code> cassées).
            </div>
            <div className="flex gap-2 justify-end">
              <button className="jp-btn jp-btn-ghost"
                onClick={() => setConfirmOpen(false)}
                data-testid="storage-orphan-cancel">
                Annuler
              </button>
              <button className="jp-btn"
                style={{ background: '#DC2626', color: 'white' }}
                onClick={startCleanup}
                data-testid="storage-orphan-confirm">
                🧹 Lancer le nettoyage
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div className="p-1.5 rounded" style={{ background: 'var(--jp-surface-secondary)' }}>
      <div style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
      <div className="font-bold" style={{ color: color || 'var(--jp-text)' }}>{value}</div>
    </div>
  );
}

function StatBlock({ title, color, textColor, rows, testId }) {
  return (
    <div data-testid={testId} className="p-3 rounded-xl"
         style={{ background: color, color: textColor }}>
      <div className="text-xs font-bold mb-2">{title}</div>
      {rows.map(([k, v], i) => (
        <div key={i} className="text-xs flex justify-between gap-2 py-0.5">
          <span style={{ opacity: 0.8 }}>{k}</span>
          <span className="font-mono text-right break-all">{v}</span>
        </div>
      ))}
    </div>
  );
}
