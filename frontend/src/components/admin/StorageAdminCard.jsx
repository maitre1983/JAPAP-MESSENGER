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

      <div className="mt-4 p-3 rounded-xl text-[11px]"
           style={{ background: '#EFF6FF', color: '#1E40AF' }}>
        💡 La migration est <strong>idempotente</strong> — vous pouvez la
        relancer en toute sécurité. Les fichiers déjà sur R2 ne seront pas
        re-uploadés (les chemins en DB sont également mis à jour).
      </div>
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
