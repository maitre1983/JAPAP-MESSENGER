/**
 * BatchSafetyPane — iter82
 * Batch Scale & Safety controls for the Admin Messaging Center.
 *
 * Shows live queue stats (pending / locked / sent / failed), throughput,
 * and lets the admin tune:
 *   • Safe mode (real_send_enabled) — hard kill-switch
 *   • Audience cap per campaign
 *   • Worker rate (emails/min)
 *   • Batch size per poll
 *
 * One-click "Requeue failed" button re-enqueues the last 500 failed rows.
 */
import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  ShieldCheck, Warning, Gauge, UsersThree, ArrowCounterClockwise, PaperPlaneTilt,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function BatchSafetyPane() {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    max_audience_per_campaign: '',
    worker_rate_per_minute: '',
    batch_size: '',
  });

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(
        `${API}/api/admin/messaging/batch/status`, { withCredentials: true });
      setData(data);
      setForm({
        max_audience_per_campaign: String(data.settings.max_audience_per_campaign),
        worker_rate_per_minute: String(data.settings.worker_rate_per_minute),
        batch_size: String(data.settings.batch_size),
      });
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement batch status');
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  // Live refresh every 5s while pane is open
  useEffect(() => {
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const toggleSafeMode = async (nextEnabled) => {
    const confirmMsg = nextEnabled
      ? '⚠️ ATTENTION : Activer l\'envoi réel enverra des emails aux destinataires.\n\nConfirmez uniquement si les tests ont été validés.'
      : t('batch_safety_pane.basculer_en_mode_safe_aucun_email_r');
    if (!window.confirm(confirmMsg)) return;
    setSaving(true);
    try {
      await axios.put(`${API}/api/admin/messaging/batch/settings`,
        { real_send_enabled: nextEnabled }, { withCredentials: true });
      toast.success(nextEnabled ? t('batch_safety_pane.envoi_reel_active') : t('batch_safety_pane.mode_safe_active'));
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setSaving(false); }
  };

  const saveNumeric = async () => {
    const payload = {};
    const cap = parseInt(form.max_audience_per_campaign, 10);
    const rate = parseInt(form.worker_rate_per_minute, 10);
    const bsz = parseInt(form.batch_size, 10);
    if (cap >= 1) payload.max_audience_per_campaign = cap;
    if (rate >= 1) payload.worker_rate_per_minute = rate;
    if (bsz >= 1) payload.batch_size = bsz;
    if (!Object.keys(payload).length) {
      toast.error('Aucune valeur valide à enregistrer');
      return;
    }
    setSaving(true);
    try {
      await axios.put(`${API}/api/admin/messaging/batch/settings`,
        payload, { withCredentials: true });
      toast.success('Paramètres batch enregistrés');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setSaving(false); }
  };

  const requeueFailed = async () => {
    if (!window.confirm('Remettre en file tous les envois échoués (jusqu\'à 500) ?')) return;
    setSaving(true);
    try {
      const { data } = await axios.post(
        `${API}/api/admin/messaging/batch/requeue-failed`, {},
        { withCredentials: true });
      toast.success(`${data.requeued} envoi(s) remis en file`);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setSaving(false); }
  };

  if (!data) {
    return <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;
  }

  const { queue, throughput, settings } = data;
  const safeMode = !settings.real_send_enabled;
  const oldestPending = queue.oldest_pending_at
    ? new Date(queue.oldest_pending_at).toLocaleString('fr-FR')
    : null;

  return (
    <div className="space-y-5 jp-animate-fadeIn" data-testid="batch-safety-pane">
      {/* Safe Mode banner */}
      <div className={`jp-alert ${safeMode ? 'jp-alert-success' : 'jp-alert-warning'} flex items-center gap-3`}
           data-testid="safe-mode-banner">
        {safeMode ? <ShieldCheck size={22} weight="duotone" /> : <Warning size={22} weight="duotone" />}
        <div className="flex-1">
          <div className="font-bold text-sm">
            {safeMode ? t('batch_safety_pane.mode_safe_active') : '🚨 ENVOI RÉEL ACTIVÉ'}
          </div>
          <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            {safeMode
              ? t('batch_safety_pane.aucun_email_reel_n_est_envoye')
              : t('batch_safety_pane.les_campagnes_envoient_de_vrais')}
          </div>
        </div>
        <button
          onClick={() => toggleSafeMode(safeMode)}
          disabled={saving}
          className={`jp-btn jp-btn-sm ${safeMode ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
          data-testid="toggle-safe-mode">
          {safeMode ? (<><PaperPlaneTilt size={14} /> Activer envoi réel</>)
                   : (<><ShieldCheck size={14} /> Repasser en SAFE</>)}
        </button>
      </div>

      {/* Live queue stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3" data-testid="queue-stats">
        <StatCard label="En attente" value={queue.pending} accent="#F59E0B" />
        <StatCard label="Verrouillés" value={queue.locked} accent="#6366F1" />
        <StatCard label="Envoyés" value={queue.sent} accent="var(--jp-success)" />
        <StatCard label="Échoués" value={queue.failed} accent="var(--jp-error)" />
        <StatCard label="Total" value={queue.total} accent="var(--jp-primary)" />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <StatCard label="Envoyés (60 dernières min)" value={throughput.sent_last_hour} accent="#10B981" />
        <StatCard label="Campagnes en cours d'envoi" value={throughput.active_campaigns_sending} accent="#8B5CF6" />
        <div className="jp-card-elevated p-4 flex flex-col justify-center">
          <div className="text-[11px] uppercase tracking-wide" style={{ color: 'var(--jp-text-muted)' }}>
            Plus ancien en attente
          </div>
          <div className="text-sm font-semibold" style={{ color: 'var(--jp-text)' }}>
            {oldestPending || '—'}
          </div>
        </div>
      </div>

      {/* Tuning controls */}
      <div className="jp-card-elevated p-5">
        <h3 className="font-['Outfit'] text-lg font-bold mb-1 flex items-center gap-2">
          <Gauge size={18} weight="duotone" /> Paramètres de débit
        </h3>
        <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
          Ajustez finement le volume envoyé par le worker. Plus la cadence est
          élevée, plus les alertes spam peuvent se déclencher côté Resend.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div data-testid="field-max-audience">
            <label className="jp-label text-xs flex items-center gap-1">
              <UsersThree size={12} /> Audience max par campagne
            </label>
            <input type="number" min="1" max="100000" className="jp-input text-sm"
                   value={form.max_audience_per_campaign}
                   onChange={e => setForm(f => ({ ...f, max_audience_per_campaign: e.target.value }))} />
            <div className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
              Dépasser ce seuil exige `force=true` côté API.
            </div>
          </div>
          <div data-testid="field-worker-rate">
            <label className="jp-label text-xs flex items-center gap-1">
              <PaperPlaneTilt size={12} /> Cadence worker (emails/min)
            </label>
            <input type="number" min="1" max="10000" className="jp-input text-sm"
                   value={form.worker_rate_per_minute}
                   onChange={e => setForm(f => ({ ...f, worker_rate_per_minute: e.target.value }))} />
            <div className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
              Limite dure de débit sortant.
            </div>
          </div>
          <div data-testid="field-batch-size">
            <label className="jp-label text-xs">Taille de lot / poll</label>
            <input type="number" min="1" max="500" className="jp-input text-sm"
                   value={form.batch_size}
                   onChange={e => setForm(f => ({ ...f, batch_size: e.target.value }))} />
            <div className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
              Nombre max de rows traités par cycle (poll 3s).
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 mt-4">
          <button onClick={saveNumeric} disabled={saving}
                  className="jp-btn jp-btn-primary" data-testid="save-batch-settings">
            {saving ? 'Enregistrement…' : t('batch_safety_pane.enregistrer_les_parametres')}
          </button>
          <button onClick={requeueFailed} disabled={saving || queue.failed === 0}
                  className="jp-btn jp-btn-ghost" data-testid="requeue-failed-btn">
            <ArrowCounterClockwise size={14} /> Rejouer les échoués ({queue.failed})
          </button>
        </div>
      </div>

      <div className="jp-alert jp-alert-info text-xs">
        <strong>{t('batch_safety_pane.a_propos_du_plafond_d_audience')}</strong> lorsque vous envoyez une
        campagne dont l'audience dépasse le plafond, l'API renvoie une erreur
        explicite. Il faut alors renvoyer la requête avec <code>force=true</code>
        (ou augmenter le plafond ici). Le filtrage anti-bots / emails jetables /
        doublons reste actif dans tous les cas.
      </div>
    </div>
  );
}

function StatCard({ label, value, accent = 'var(--jp-primary)' }) {
  return (
    <div className="jp-card-elevated p-4" data-testid={`stat-${label.toLowerCase().replace(/\s/g, '-')}`}>
      <div className="text-[11px] uppercase tracking-wide" style={{ color: 'var(--jp-text-muted)' }}>
        {label}
      </div>
      <div className="text-2xl font-bold" style={{ color: accent }}>
        {typeof value === 'number' ? value.toLocaleString('fr-FR') : value}
      </div>
    </div>
  );
}
