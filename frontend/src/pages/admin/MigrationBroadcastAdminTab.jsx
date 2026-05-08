import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Megaphone, Pause, Play, Stop, Plus } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * MigrationBroadcastAdminTab — pilote la campagne d'emails de migration
 * JAPAP 1.0 → 4.0 (iter153). Permet de créer, suivre, mettre en pause,
 * reprendre, ou stopper les campagnes broadcast vers les utilisateurs
 * legacy non migrés.
 */
export default function MigrationBroadcastAdminTab() {
  const { t } = useTranslation();
  const [campaigns, setCampaigns] = useState([]);
  const [broadcastEnabled, setBroadcastEnabled] = useState(true);
  const [loading, setLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/api/admin/migration/broadcast`,
        { withCredentials: true });
      setCampaigns(r.data?.campaigns || []);
      setBroadcastEnabled(r.data?.broadcast_enabled !== false);
    } catch (e) {
      toast.error('Impossible de charger les campagnes.');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (cid) => {
    try {
      const r = await axios.get(`${API}/api/admin/migration/broadcast/${cid}`,
        { withCredentials: true });
      setDetail(r.data);
    } catch {}
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!selected) return;
    loadDetail(selected);
    const t = setInterval(() => loadDetail(selected), 15000); // refresh 15s
    return () => clearInterval(t);
  }, [selected, loadDetail]);

  const action = async (cid, verb) => {
    try {
      await axios.post(`${API}/api/admin/migration/broadcast/${cid}/${verb}`,
        {}, { withCredentials: true });
      toast.success(`Campagne ${verb === 'pause' ? 'mise en pause' :
        verb === 'resume' ? 'reprise' :
        verb === 'stop' ? 'arrêtée' : 'mise à jour'}.`);
      await load();
      if (selected === cid) loadDetail(cid);
    } catch (e) {
      toast.error(`Échec : ${e.response?.data?.detail || 'erreur'}`);
    }
  };

  const totalSent = campaigns.reduce((acc, c) => acc + (c.sent_count || 0), 0);
  const totalTargets = campaigns.reduce((acc, c) => acc + (c.total_targets || 0), 0);

  return (
    <div data-testid="admin-migration-broadcast-tab" className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold font-['Outfit'] flex items-center gap-2"
            style={{ color: 'var(--jp-text)' }}>
            <Megaphone size={22} weight="bold" />
            Migration Broadcast — JAPAP 1.0 → 4.0
          </h2>
          <p className="text-sm font-['Manrope']"
            style={{ color: 'var(--jp-text-muted)' }}>
            Pilote la campagne d'emails de réactivation des comptes legacy.
            Limite quotidienne stricte par campagne.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          data-testid="broadcast-create-btn"
          disabled={!broadcastEnabled}
          title={broadcastEnabled ? t('migration_broadcast_admin.creer_une_nouvelle_campagne') : t('migration_broadcast_admin.mode_broadcast_desactive_kill_switc')}
          className="px-4 py-2 rounded-full font-bold text-sm disabled:opacity-40 disabled:cursor-not-allowed"
          style={{ background: '#0F056B', color: '#fff' }}>
          <Plus size={14} className="inline mr-1" weight="bold" />
          Nouvelle campagne
        </button>
      </div>

      {!broadcastEnabled && (
        <div
          data-testid="broadcast-disabled-banner"
          className="rounded-2xl p-4 border font-['Manrope']"
          style={{
            background: 'rgba(239,68,68,0.08)',
            borderColor: 'rgba(239,68,68,0.4)',
            color: '#991b1b',
          }}
        >
          <div className="font-bold text-sm mb-1">⚠ Mode broadcast désactivé (kill switch iter154)</div>
          <div className="text-xs leading-relaxed">
            Suite à un signalement Resend de taux de bounce élevé, l'envoi automatique
            de masse est <strong>complètement coupé</strong>. Le worker tourne au ralenti et n'envoie
            aucun email. Le système est passé en <strong>100 % on-demand sécurisé</strong> :
            les utilisateurs legacy ne reçoivent leur lien de migration qu'après une
            tentative volontaire de connexion. Les campagnes existantes sont
            consultables mais ne peuvent plus être (re)lancées.
            <br /><br />
            Pour réactiver, définir <code>BROADCAST_ENABLED=true</code> dans
            <code> backend/.env</code> APRÈS nettoyage de la liste, puis redémarrer
            le backend.
          </div>
        </div>
      )}

      <LegacyClassificationPanel />

      {/* Aggregate stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Campagnes" value={campaigns.length} />
        <Stat label="Cibles totales" value={totalTargets.toLocaleString()} />
        <Stat label="Total envoyés" value={totalSent.toLocaleString()} />
        <Stat label="Restant" value={(totalTargets - totalSent).toLocaleString()} />
      </div>

      {/* Campaign list */}
      <div className="rounded-2xl border overflow-hidden"
        style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-card)' }}>
        <div className="px-4 py-3 border-b font-semibold text-sm font-['Outfit']"
          style={{ borderColor: 'var(--jp-border)' }}>
          Campagnes ({loading ? '…' : campaigns.length})
        </div>
        {campaigns.length === 0 && !loading && (
          <div className="px-4 py-8 text-center text-sm font-['Manrope']"
            style={{ color: 'var(--jp-text-muted)' }}>
            Aucune campagne. Cliquez "Nouvelle campagne" pour démarrer.
          </div>
        )}
        {campaigns.map((c) => {
          const progress = c.total_targets ? Math.round((c.sent_count / c.total_targets) * 100) : 0;
          return (
            <div key={c.campaign_id}
              data-testid={`broadcast-row-${c.campaign_id}`}
              className="px-4 py-3 border-b last:border-b-0 hover:bg-black/5 transition-colors"
              style={{ borderColor: 'var(--jp-border)' }}>
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-semibold text-sm truncate font-['Outfit']">
                      {c.name}
                    </span>
                    <StatusBadge status={c.status} />
                  </div>
                  <div className="text-xs font-['Manrope']"
                    style={{ color: 'var(--jp-text-muted)' }}>
                    {c.sent_count.toLocaleString()} / {c.total_targets.toLocaleString()} envoyés
                    {' · '}
                    Quota : {c.daily_limit}/jour
                    {' · '}
                    Démarrage : {new Date(c.start_at).toLocaleString('fr-FR')}
                  </div>
                  {/* Progress bar */}
                  <div className="mt-2 h-1.5 rounded-full overflow-hidden"
                    style={{ background: 'rgba(0,0,0,0.08)' }}>
                    <div className="h-full transition-all"
                      style={{
                        width: `${progress}%`,
                        background: c.status === 'running' ? '#22c55e' :
                                    c.status === 'paused' ? '#f59e0b' :
                                    c.status === 'stopped' ? '#ef4444' : '#0F056B',
                      }} />
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  {c.status === 'running' && (
                    <button onClick={() => action(c.campaign_id, 'pause')}
                      data-testid={`broadcast-${c.campaign_id}-pause`}
                      title={t('migration_broadcast_admin.pause')}
                      className="p-2 rounded-full hover:bg-black/10">
                      <Pause size={16} weight="bold" />
                    </button>
                  )}
                  {(c.status === 'paused' || c.status === 'scheduled') && (
                    <button onClick={() => action(c.campaign_id, 'resume')}
                      data-testid={`broadcast-${c.campaign_id}-resume`}
                      title={t('migration_broadcast_admin.reprendre')}
                      className="p-2 rounded-full hover:bg-black/10">
                      <Play size={16} weight="bold" />
                    </button>
                  )}
                  {c.status !== 'stopped' && c.status !== 'completed' && (
                    <button onClick={() => action(c.campaign_id, 'stop')}
                      data-testid={`broadcast-${c.campaign_id}-stop`}
                      title={t('migration_broadcast_admin.stop_definitif')}
                      className="p-2 rounded-full hover:bg-black/10"
                      style={{ color: '#ef4444' }}>
                      <Stop size={16} weight="bold" />
                    </button>
                  )}
                  <button
                    onClick={() => setSelected(selected === c.campaign_id ? null : c.campaign_id)}
                    data-testid={`broadcast-${c.campaign_id}-details`}
                    className="px-3 py-1.5 rounded-full text-xs font-semibold"
                    style={{ background: 'rgba(15,5,107,0.1)', color: '#0F056B' }}>
                    {selected === c.campaign_id ? 'Masquer' : t('migration_broadcast_admin.details')}
                  </button>
                </div>
              </div>

              {/* Detail panel */}
              {selected === c.campaign_id && detail && (
                <div className="mt-4 p-4 rounded-xl"
                  data-testid={`broadcast-${c.campaign_id}-detail-panel`}
                  style={{ background: 'rgba(15,5,107,0.04)' }}>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                    <Stat small label="Envoyés aujourd'hui" value={`${detail.sent_today || 0} / ${detail.daily_limit}`} />
                    <Stat small label="Délivrés" value={detail.delivered_count || 0} />
                    <Stat small label="Ouverts" value={detail.opened_count || 0} />
                    <Stat small label="Cliqués" value={detail.clicked_count || 0} />
                    <Stat small label="Bounced" value={detail.bounced_count || 0} negative />
                    <Stat small label="Échecs" value={detail.failed_count || 0} negative />
                    <Stat small label="Exclus" value={detail.excluded_count || 0} muted />
                    <Stat small label="En attente"
                      value={detail.status_breakdown?.pending || 0} />
                  </div>
                  <div className="text-xs font-['Manrope']"
                    style={{ color: 'var(--jp-text-muted)' }}>
                    ID : <code style={{ fontSize: 11 }}>{c.campaign_id}</code>
                    {' · '}
                    Mis à jour : {new Date(c.updated_at).toLocaleString('fr-FR')}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {showCreate && (
        <CreateCampaignModal onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); load(); }} />
      )}
    </div>
  );
}


function Stat({ label, value, small = false, negative = false, muted = false }) {
  const color = negative ? '#ef4444' : muted ? 'var(--jp-text-muted)' : 'var(--jp-text)';
  return (
    <div className={`rounded-xl border ${small ? 'p-3' : 'p-4'}`}
      style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-card)' }}>
      <div className={`font-bold font-['Outfit'] ${small ? 'text-base' : 'text-2xl'}`}
        style={{ color }}>
        {value}
      </div>
      <div className="text-xs mt-0.5 font-['Manrope']"
        style={{ color: 'var(--jp-text-muted)' }}>
        {label}
      </div>
    </div>
  );
}


function StatusBadge({ status }) {
  const { t } = useTranslation();
  const map = {
    running:   { label: 'EN COURS',     bg: '#22c55e', fg: '#fff' },
    paused:    { label: 'PAUSE',        bg: '#f59e0b', fg: '#fff' },
    stopped:   { label: t('migration_broadcast_admin.stoppee'),      bg: '#ef4444', fg: '#fff' },
    completed: { label: t('migration_broadcast_admin.terminee'),     bg: '#0F056B', fg: '#fff' },
    scheduled: { label: t('migration_broadcast_admin.programmee'),   bg: '#94a3b8', fg: '#fff' },
  };
  const s = map[status] || { label: status, bg: '#64748b', fg: '#fff' };
  return (
    <span style={{
      background: s.bg, color: s.fg, fontSize: 9, fontWeight: 800,
      padding: '2px 8px', borderRadius: 99, letterSpacing: '0.5px',
    }}>{s.label}</span>
  );
}


// ── iter155 — Legacy classification + smart cleanup widget ──────────
function LegacyClassificationPanel() {
  const { t } = useTranslation();
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [purging, setPurging] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(
        `${API}/api/admin/migration/legacy-classification`,
        { withCredentials: true });
      setReport(r.data);
    } catch {
      toast.error("Impossible de charger la classification.");
    } finally {
      setLoading(false);
    }
  };

  const purge = async (dryRun) => {
    setPurging(true);
    try {
      const r = await axios.post(
        `${API}/api/admin/migration/legacy-cleanup`,
        { dry_run: dryRun },
        { withCredentials: true });
      const d = r.data;
      if (dryRun) {
        toast.success(`Dry run : ${d.purged} comptes seraient purgés.`);
      } else {
        toast.success(`✓ ${d.purged} comptes purgés. ${d.remaining_eligible} récupérables restants.`);
        setConfirmOpen(false);
        await load();
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "Échec de la purge.");
    } finally {
      setPurging(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  if (loading && !report) {
    return (
      <div className="rounded-2xl border p-4 text-sm font-['Manrope']"
        style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-card)',
                 color: 'var(--jp-text-muted)' }}>
        Chargement de la classification email…
      </div>
    );
  }
  if (!report) return null;

  const tiers = report.tiers || {};
  const total = report.total_legacy_pending || 0;
  const pct = (n) => total ? Math.round((n / total) * 100) : 0;

  return (
    <div data-testid="legacy-classification-panel"
      className="rounded-2xl border p-4 space-y-4"
      style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-card)' }}>
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-bold text-base font-['Outfit']">
            🎯 Classification intelligente liste legacy (iter155)
          </h3>
          <p className="text-xs font-['Manrope']"
            style={{ color: 'var(--jp-text-muted)' }}>
            Tier 1 : Gmail/Outlook/Yahoo/iCloud/Proton — Tier 2 : autres domaines valides — Tier 3 : à risque (exclus)
          </p>
        </div>
        <button onClick={load}
          data-testid="legacy-classification-refresh"
          className="px-3 py-1.5 rounded-full text-xs font-bold border">
          ↻ Recharger
        </button>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <TierCard label="Tier 1 — Mainstream" count={tiers.tier1 || 0} pct={pct(tiers.tier1 || 0)}
          color="#22c55e" hint="Haute deliverability" />
        <TierCard label="Tier 2 — Autres valides" count={tiers.tier2 || 0} pct={pct(tiers.tier2 || 0)}
          color="#0F056B" hint="Deliverability normale" />
        <TierCard label="Tier 3 — À risque" count={tiers.tier3 || 0} pct={pct(tiers.tier3 || 0)}
          color="#ef4444" hint="invalid / disposable / hard-bounced" testId="tier3-card" />
      </div>

      <div className="rounded-xl p-3 text-xs font-['Manrope']"
        style={{ background: 'rgba(15,5,107,0.04)', color: 'var(--jp-text)' }}>
        <strong>Récupérables (Tier 1 + Tier 2) : {report.deliverable.toLocaleString('fr-FR')}</strong>
        {' / '}{total.toLocaleString('fr-FR')} comptes legacy en attente
        {' '}— soit <strong>{total ? Math.round((report.deliverable / total) * 100) : 0}%</strong> de la base.
        {tiers.tier3 > 0 && (
          <>
            {' '}Tier 3 détaillé :
            {' '}<code>invalid_format={tiers.tier3_invalid_format}</code>
            {' '}<code>disposable={tiers.tier3_disposable_domain}</code>
            {' '}<code>hard_bounced={tiers.tier3_hard_bounced}</code>
          </>
        )}
      </div>

      <div className="flex justify-end gap-2">
        <button
          data-testid="legacy-cleanup-dryrun"
          onClick={() => purge(true)} disabled={purging}
          className="px-4 py-2 rounded-full text-xs font-bold border disabled:opacity-50">
          {purging ? '…' : t('migration_broadcast_admin.apercu_purge_dry_run')}
        </button>
        <button
          data-testid="legacy-cleanup-apply"
          onClick={() => setConfirmOpen(true)} disabled={purging || !tiers.tier3}
          className="px-4 py-2 rounded-full text-xs font-bold text-white disabled:opacity-50"
          style={{ background: '#ef4444' }}>
          🧹 Purger les {tiers.tier3 || 0} comptes Tier 3
        </button>
      </div>

      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.55)' }}
          onClick={() => setConfirmOpen(false)}>
          <div onClick={(e) => e.stopPropagation()}
            className="bg-white rounded-2xl p-6 max-w-md space-y-3"
            data-testid="legacy-cleanup-confirm-modal">
            <h4 className="font-bold text-base font-['Outfit']">Confirmer la purge ?</h4>
            <p className="text-sm">
              {tiers.tier3} comptes Tier 3 vont avoir <code>is_legacy_account=FALSE</code>
              {' '}+ <code>email_subscribed=FALSE</code>. Ils seront définitivement
              exclus de tout futur broadcast. <strong>{t('migration_broadcast_admin.action_irreversible')}</strong>
              {' '}(les comptes ne sont pas supprimés, mais ne pourront plus recevoir
              de mail tant qu'ils n'opt-in pas).
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmOpen(false)}
                className="px-4 py-2 rounded-full text-xs font-bold border">
                Annuler
              </button>
              <button
                data-testid="legacy-cleanup-confirm-submit"
                onClick={() => purge(false)} disabled={purging}
                className="px-4 py-2 rounded-full text-xs font-bold text-white disabled:opacity-50"
                style={{ background: '#ef4444' }}>
                {purging ? 'Purge en cours…' : `Confirmer purge (${tiers.tier3})`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function TierCard({ label, count, pct, color, hint, testId }) {
  return (
    <div data-testid={testId}
      className="rounded-xl border p-3"
      style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-card)' }}>
      <div className="text-xs font-bold mb-1 font-['Manrope']" style={{ color }}>
        {label}
      </div>
      <div className="text-2xl font-bold font-['Outfit']" style={{ color: 'var(--jp-text)' }}>
        {count.toLocaleString('fr-FR')}
      </div>
      <div className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
        {pct}% — {hint}
      </div>
      <div className="mt-2 h-1.5 rounded-full overflow-hidden"
        style={{ background: 'rgba(0,0,0,0.08)' }}>
        <div className="h-full" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}


function CreateCampaignModal({ onClose, onCreated }) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [startAt, setStartAt] = useState(() => {
    const d = new Date();
    d.setUTCDate(d.getUTCDate() + 1);
    d.setUTCHours(0, 0, 0, 0);
    return d.toISOString().slice(0, 16); // YYYY-MM-DDTHH:mm
  });
  const [dailyLimit, setDailyLimit] = useState(900);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim() || !startAt) return;
    setSubmitting(true);
    try {
      await axios.post(`${API}/api/admin/migration/broadcast`, {
        name: name.trim(),
        start_at: new Date(startAt).toISOString(),
        daily_limit: Number(dailyLimit) || 900,
      }, { withCredentials: true });
      toast.success('Campagne créée et lancée.');
      onCreated();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur création campagne');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.55)' }}
      data-testid="broadcast-create-modal"
      onClick={onClose}>
      <form onSubmit={submit} onClick={(e) => e.stopPropagation()}
        className="bg-white rounded-2xl p-6 w-full max-w-md space-y-4">
        <h3 className="font-bold text-lg font-['Outfit']">Nouvelle campagne broadcast</h3>
        <div>
          <label className="text-xs font-semibold font-['Manrope']">Nom</label>
          <input type="text" required value={name} onChange={(e) => setName(e.target.value)}
            data-testid="broadcast-create-name"
            placeholder={t('migration_broadcast_admin.ex_migration_japap_1_0_4_0_vague_1')}
            className="w-full mt-1 px-3 py-2 rounded-lg border text-sm" />
        </div>
        <div>
          <label className="text-xs font-semibold font-['Manrope']">Date/heure de démarrage (UTC)</label>
          <input type="datetime-local" required value={startAt}
            onChange={(e) => setStartAt(e.target.value)}
            data-testid="broadcast-create-startat"
            className="w-full mt-1 px-3 py-2 rounded-lg border text-sm" />
        </div>
        <div>
          <label className="text-xs font-semibold font-['Manrope']">Limite quotidienne</label>
          <input type="number" min="1" max="5000" value={dailyLimit}
            onChange={(e) => setDailyLimit(e.target.value)}
            data-testid="broadcast-create-dailylimit"
            className="w-full mt-1 px-3 py-2 rounded-lg border text-sm" />
          <p className="text-xs mt-1 font-['Manrope']"
            style={{ color: 'var(--jp-text-muted)' }}>
            Recommandé : 900 emails/jour pour préserver la réputation du domaine.
          </p>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose}
            data-testid="broadcast-create-cancel"
            className="px-4 py-2 rounded-full text-sm font-semibold border">
            Annuler
          </button>
          <button type="submit" disabled={submitting}
            data-testid="broadcast-create-submit"
            className="px-4 py-2 rounded-full text-sm font-bold text-white disabled:opacity-50"
            style={{ background: '#0F056B' }}>
            {submitting ? t('migration_broadcast_admin.creation') : t('migration_broadcast_admin.creer_lancer')}
          </button>
        </div>
      </form>
    </div>
  );
}
