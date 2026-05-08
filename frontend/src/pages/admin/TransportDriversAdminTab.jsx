/**
 * TransportDriversAdminTab — iter97 Phase 1 admin driver KYC review.
 *
 * Lists drivers per KYC status (pending_review by default), shows the 3
 * documents side-by-side for review, lets the admin approve / reject / suspend
 * with a mandatory reason field. Decision history available per driver.
 */
import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Car, IdentificationBadge, ArrowsClockwise, CheckCircle, XCircle, Pause,
  Eye, ClockClockwise, X,
} from '@phosphor-icons/react';
import ZoomableImage from '@/components/media/ZoomableImage';

const API = process.env.REACT_APP_BACKEND_URL;

const STATUSES = [
  { id: 'pending_review', label: 'À examiner', color: '#9A6700', bg: '#FEF3C7' },
  { id: 'approved',       label: 'Validés',    color: '#047857', bg: '#D1FAE5' },
  { id: 'rejected',       label: 'Refusés',    color: '#b91c1c', bg: '#FEE2E2' },
  { id: 'suspended',      label: 'Suspendus',  color: '#7f1d1d', bg: '#FECACA' },
];

export default function TransportDriversAdminTab({ onAction }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState('pending_review');
  const [items, setItems] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(false);
  const [reviewing, setReviewing] = useState(null);   // full driver detail object

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(
        `${API}/api/transport/admin/drivers`,
        { params: { status, limit: 50 }, withCredentials: true },
      );
      setItems(r.data.items || []);
      setCounts(r.data.counts || {});
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur de chargement');
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-4" data-testid="transport-drivers-admin">
      {/* Counts cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {STATUSES.map((s) => (
          <button
            key={s.id}
            onClick={() => setStatus(s.id)}
            className="p-3 rounded-xl text-left transition-colors"
            style={{
              background: status === s.id ? s.bg : 'var(--jp-surface-secondary)',
              border: `1px solid ${status === s.id ? s.color : 'var(--jp-border)'}`,
            }}
            data-testid={`drivers-tab-${s.id}`}
          >
            <div className="text-[10px] uppercase font-bold opacity-70">{s.label}</div>
            <div className="text-2xl font-extrabold" style={{ color: status === s.id ? s.color : 'var(--jp-text)' }}>
              {counts[s.id] ?? 0}
            </div>
          </button>
        ))}
      </div>

      {/* Refresh */}
      <div className="flex items-center justify-between">
        <h2 className="font-['Outfit'] font-extrabold text-base flex items-center gap-2">
          <Car size={18} weight="duotone" /> Chauffeurs — {STATUSES.find((x) => x.id === status)?.label}
        </h2>
        <button onClick={load} className="jp-btn jp-btn-ghost text-xs">
          <ArrowsClockwise size={12} className={loading ? 'animate-spin' : ''} /> Actualiser
        </button>
      </div>

      {/* Table */}
      <div className="space-y-2">
        {items.map((it) => (
          <div
            key={it.driver_id}
            className="jp-card-elevated p-3 flex items-center gap-3"
            data-testid={`driver-row-${it.driver_id}`}
          >
            <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0"
                 style={{ background: 'var(--jp-surface-secondary)' }}>
              {it.user.avatar ? (
                <img src={it.user.avatar} className="w-full h-full rounded-full object-cover" alt="" />
              ) : <IdentificationBadge size={18} />}
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-bold text-sm truncate">{it.user.name || it.user.email}</div>
              <div className="text-[11px] opacity-70 truncate">
                {it.vehicle_model} · {it.vehicle_plate} · {it.vehicle_type} · {it.country_code || '—'}
              </div>
              <div className="text-[10px] opacity-50">
                Permis : {it.license_number} · soumis le {it.kyc_submitted_at ? new Date(it.kyc_submitted_at).toLocaleString('fr-FR') : '—'}
              </div>
            </div>
            <button
              onClick={() => loadDetail(it.driver_id, setReviewing)}
              className="jp-btn jp-btn-primary text-xs flex items-center gap-1"
              data-testid={`driver-review-${it.driver_id}`}
            >
              <Eye size={13} weight="bold" /> Examiner
            </button>
          </div>
        ))}
        {!loading && items.length === 0 && (
          <div className="text-center py-10 text-xs opacity-60">
            Aucun chauffeur dans cette catégorie.
          </div>
        )}
      </div>

      {reviewing && (
        <DriverReviewModal
          driver={reviewing}
          onClose={() => setReviewing(null)}
          onDecided={() => {
            setReviewing(null);
            load();
            onAction?.();
          }}
        />
      )}
    </div>
  );
}

async function loadDetail(driverId, setReviewing) {
  try {
    const r = await axios.get(
      `${API}/api/transport/admin/drivers/${driverId}`,
      { withCredentials: true },
    );
    setReviewing(r.data);
  } catch (e) {
    toast.error(e.response?.data?.detail || 'Erreur de chargement');
  }
}

/* ════════════════════════════════════════════════════════════════════════ */
function DriverReviewModal({ driver, onClose, onDecided }) {
  const { t } = useTranslation();
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const decide = async (kind) => {
    if (kind !== 'approve' && reason.trim().length < 5) {
      toast.error('Une raison (≥ 5 caractères) est obligatoire.');
      return;
    }
    if (!window.confirm(`Confirmer : ${kind === 'approve' ? 'APPROUVER' : kind === 'reject' ? 'REFUSER' : 'SUSPENDRE'} ce chauffeur ?`)) return;
    setBusy(true);
    try {
      await axios.post(
        `${API}/api/transport/admin/drivers/${driver.driver_id}/${kind}`,
        { reason: reason.trim() },
        { withCredentials: true },
      );
      toast.success('Décision enregistrée');
      onDecided?.();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-3"
      style={{ background: 'rgba(0,0,0,0.7)' }}
      onClick={onClose}
      data-testid="driver-review-modal"
    >
      <div
        className="w-full max-w-3xl max-h-[92vh] rounded-2xl overflow-hidden flex flex-col"
        style={{ background: 'var(--jp-surface)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="px-5 py-3 flex items-center justify-between"
          style={{ borderBottom: '1px solid var(--jp-border)' }}>
          <h2 className="font-['Outfit'] font-extrabold text-base">Examen du dossier chauffeur</h2>
          <button onClick={onClose} className="p-2 rounded-full hover:bg-white/10"><X size={18} /></button>
        </header>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Identité */}
          <Block title={t('transport_drivers_admin.identite')}>
            <KV k="Nom" v={driver.user.name || '—'} />
            <KV k="Email" v={driver.user.email} />
            <KV k="Pays" v={driver.country_code || '—'} />
            <KV k="Téléphone perso" v={driver.personal_phone || '—'} />
            <KV k="Contact urgence" v={`${driver.emergency_contact_name} · ${driver.emergency_contact_phone}`} />
          </Block>

          <Block title={t('transport_drivers_admin.vehicule')}>
            <KV k="Modèle" v={driver.vehicle_model} />
            <KV k="Plaque" v={driver.vehicle_plate} />
            <KV k="Catégorie" v={driver.vehicle_type} />
          </Block>

          <Block title={t('transport_drivers_admin.permis')}>
            <KV k="Numéro" v={driver.license_number} />
            <KV k="Date d'établissement" v={driver.license_issue_date || '—'} />
          </Block>

          <Block title={t('transport_drivers_admin.documents')}>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <DocCard label="📄 Permis" url={driver.license_image_url} />
              <DocCard label="🪪 Pièce d'identité" url={driver.id_card_image_url} />
              <DocCard label="🤳 Selfie + permis" url={driver.selfie_with_license_url} />
            </div>
          </Block>

          {driver.history?.length > 0 && (
            <Block title={<><ClockClockwise size={14} className="inline mr-1" /> Historique</>}>
              <ul className="space-y-1 text-[11px]">
                {driver.history.map((h, i) => (
                  <li key={i} className="flex items-center justify-between gap-2 py-1"
                      style={{ borderBottom: '1px dashed var(--jp-border)' }}>
                    <span className="font-bold uppercase">{h.decision}</span>
                    <span className="opacity-70 truncate">{h.reason || '—'}</span>
                    <span className="opacity-50 shrink-0">{new Date(h.decided_at).toLocaleString('fr-FR')}</span>
                  </li>
                ))}
              </ul>
            </Block>
          )}

          {driver.reviews && (
            <Block title={t('transport_drivers_admin.avis_notation')}>
              <div className="flex items-center gap-3 mb-2">
                <div className="text-2xl font-extrabold" style={{ color: '#F59E0B' }}>
                  {parseFloat(driver.reviews.summary.average).toFixed(2)}
                  <span className="text-xs opacity-60">/5</span>
                </div>
                <div className="text-[11px] opacity-70">
                  {driver.reviews.summary.total} avis
                </div>
                <div className="flex-1 grid grid-cols-5 gap-1 text-[10px]">
                  {[5, 4, 3, 2, 1].map((s) => {
                    const count = driver.reviews.summary.histogram[String(s)] || 0;
                    const total = driver.reviews.summary.total || 1;
                    const pct = Math.round((count / total) * 100);
                    return (
                      <div key={s} className="text-center">
                        <div className="opacity-60">{s}★</div>
                        <div className="h-1 rounded-full mt-0.5" style={{
                          background: `linear-gradient(to right, #F59E0B ${pct}%, var(--jp-border) ${pct}%)`,
                        }} />
                        <div className="opacity-50 mt-0.5">{count}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
              {driver.reviews.recent.length > 0 ? (
                <ul className="space-y-1.5 text-[11px]" data-testid="driver-reviews-list">
                  {driver.reviews.recent.map((r, i) => (
                    <li key={i} className="py-1.5 px-2 rounded-md"
                        style={{ background: 'var(--jp-surface)', border: '1px solid var(--jp-border)' }}>
                      <div className="flex items-center justify-between mb-0.5">
                        <span className="font-bold">
                          {'★'.repeat(r.rating)}<span className="opacity-30">{'★'.repeat(5 - r.rating)}</span>
                          <span className="ml-1.5 opacity-60 text-[10px]">{r.rider_first_name}</span>
                        </span>
                        <span className="opacity-50 text-[10px]">
                          {new Date(r.created_at).toLocaleDateString('fr-FR')}
                        </span>
                      </div>
                      {r.comment && <p className="opacity-80 leading-snug">{r.comment}</p>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[11px] opacity-50 italic">Aucun avis pour l'instant.</p>
              )}
            </Block>
          )}

          <div>
            <div className="text-[10px] uppercase font-bold mb-1.5 opacity-60">
              Raison (obligatoire pour Refuser / Suspendre)
            </div>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder={t('transport_drivers_admin.ex_photo_du_permis_floue_illisible')}
              className="jp-input text-sm w-full"
              maxLength={500}
              data-testid="driver-review-reason"
            />
          </div>
        </div>

        <footer className="px-5 py-3 grid grid-cols-3 gap-2"
          style={{ borderTop: '1px solid var(--jp-border)' }}>
          <button
            onClick={() => decide('approve')}
            disabled={busy}
            className="jp-btn text-xs flex items-center justify-center gap-1 font-bold"
            style={{ background: '#10b981', color: 'white' }}
            data-testid="driver-action-approve"
          >
            <CheckCircle size={14} weight="bold" /> Approuver
          </button>
          <button
            onClick={() => decide('reject')}
            disabled={busy}
            className="jp-btn text-xs flex items-center justify-center gap-1 font-bold"
            style={{ background: '#E01C2E', color: 'white' }}
            data-testid="driver-action-reject"
          >
            <XCircle size={14} weight="bold" /> Refuser
          </button>
          <button
            onClick={() => decide('suspend')}
            disabled={busy}
            className="jp-btn text-xs flex items-center justify-center gap-1 font-bold"
            style={{ background: '#7f1d1d', color: 'white' }}
            data-testid="driver-action-suspend"
          >
            <Pause size={14} weight="bold" /> Suspendre
          </button>
        </footer>
      </div>
    </div>
  );
}

function Block({ title, children }) {
  return (
    <section
      className="p-3 rounded-xl"
      style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
    >
      <div className="text-[10px] uppercase font-bold mb-2 opacity-70">{title}</div>
      <div className="space-y-1">{children}</div>
    </section>
  );
}

function KV({ k, v }) {
  return (
    <div className="flex justify-between text-xs gap-2 py-0.5">
      <span className="opacity-60">{k}</span>
      <span className="font-semibold truncate text-right max-w-[60%]">{v}</span>
    </div>
  );
}

function DocCard({ label, url }) {
  return (
    <div>
      <div className="text-[11px] font-semibold mb-1 flex items-center justify-between">
        <span>{label}</span>
        {url && (
          <a href={url} target="_blank" rel="noreferrer"
             className="text-[10px] opacity-60 hover:opacity-100 underline">
            Plein écran
          </a>
        )}
      </div>
      {url ? (
        <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--jp-border)' }}>
          <ZoomableImage src={url} alt={label} maxHeight="40vh" background="#000"
            testId={`doc-card-${label}`} />
        </div>
      ) : (
        <div className="w-full h-40 rounded-lg flex items-center justify-center text-[11px] opacity-50"
             style={{ background: 'var(--jp-surface)', border: '1px dashed var(--jp-border)' }}>
          Aucun fichier
        </div>
      )}
    </div>
  );
}
