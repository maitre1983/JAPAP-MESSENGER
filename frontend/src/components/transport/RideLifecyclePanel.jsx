/**
 * RideLifecyclePanel — iter99 Phase 2.5.
 *
 * Mobile-first panel embedded in the Driver tab AND the Rider tab of
 * TransportModule. It adapts its UI to:
 *   • role        : 'driver' or 'rider'
 *   • ride.status : pending → accepted → en_route → started → completed
 *
 * Driver gets the 3 lifecycle buttons (En route / Course démarrée / Terminer).
 * Rider gets a status timeline + driver position polling every 5s + a
 * "Partager via WhatsApp" button (Phase 3 share link).
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Car, MapPin, CheckCircle, X, ArrowRight, Hourglass, WhatsappLogo, Share,
  PhoneCall, Star,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

// Friendly French label per status, plus the next driver-facing action.
const getStatus_labels = (t) => ({
  pending:               { txt: 'Recherche d\'un chauffeur…', color: '#9A6700', bg: '#FEF3C7' },
  accepted:              { txt: t('ride_lifecycle_panel.chauffeur_trouve_en_chemin'),color: '#0369a1', bg: '#E0F2FE' },
  en_route:              { txt: 'Chauffeur en route',           color: '#7c3aed', bg: '#EDE9FE' },
  arriving:              { txt: t('ride_lifecycle_panel.chauffeur_arrive_preparez_vous'), color: '#c2410c', bg: '#FFEDD5' },
  started:               { txt: 'Course en cours',              color: '#047857', bg: '#D1FAE5' },
  completed:             { txt: t('ride_lifecycle_panel.course_terminee'),              color: '#1f2937', bg: '#E5E7EB' },
  cancelled:             { txt: t('ride_lifecycle_panel.course_annulee'),               color: '#b91c1c', bg: '#FEE2E2' },
  cancelled_by_driver:   { txt: t('ride_lifecycle_panel.annulee_par_le_chauffeur'),     color: '#b91c1c', bg: '#FEE2E2' },
});


export default function RideLifecyclePanel({ ride, role, onChange }) {
  const { t } = useTranslation();
  const STATUS_LABELS = getStatus_labels(t);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);

  // Live polling (rider only) — refresh every 5s while ride is active.
  // Stops automatically when the ride finishes/cancels (LABELS final states).
  useEffect(() => {
    if (role !== 'rider') return;
    if (['completed', 'cancelled', 'cancelled_by_driver'].includes(ride.status)) return;
    const id = setInterval(() => setTick((n) => n + 1), 5000);
    return () => clearInterval(id);
  }, [role, ride.status]);

  // Re-fetch the ride detail on each tick.
  useEffect(() => {
    if (tick === 0) return;
    (async () => {
      try {
        const r = await axios.get(`${API}/api/transport/${ride.ride_id}`, { withCredentials: true });
        onChange?.(r.data);
      } catch { /* swallow polling errors */ }
    })();
  }, [tick, ride.ride_id, onChange]);

  const transition = useCallback(async (path) => {
    setBusy(true);
    let body = {};
    if (path === 'en-route' || path === 'start') {
      // Push driver position alongside transition if geolocation available.
      try {
        const pos = await new Promise((res, rej) =>
          navigator.geolocation.getCurrentPosition(res, rej, { timeout: 4000 }),
        );
        body = { driver_lat: pos.coords.latitude, driver_lng: pos.coords.longitude };
      } catch { /* GPS denied — ok, send without */ }
    }
    try {
      const r = await axios.post(
        `${API}/api/transport/${ride.ride_id}/${path}`,
        body, { withCredentials: true },
      );
      toast.success(r.data?.message || 'Statut mis à jour');
      // Refresh ride
      const fresh = await axios.get(`${API}/api/transport/${ride.ride_id}`, { withCredentials: true });
      onChange?.(fresh.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur transition');
    } finally {
      setBusy(false);
    }
  }, [ride.ride_id, onChange]);

  const status = STATUS_LABELS[ride.status] || STATUS_LABELS.pending;

  return (
    <div className="space-y-3" data-testid="ride-lifecycle-panel">
      {/* Status badge */}
      <div className="p-3 rounded-xl flex items-center gap-2"
           style={{ background: status.bg, border: `1px solid ${status.color}` }}>
        <Hourglass size={16} weight="fill" style={{ color: status.color }} />
        <span className="font-bold text-sm" style={{ color: status.color }}>
          {status.txt}
        </span>
      </div>

      {/* Trip summary */}
      <div className="space-y-1.5 text-xs">
        <div className="flex items-start gap-2">
          <MapPin size={14} weight="fill" style={{ color: '#10b981' }} className="mt-0.5 shrink-0" />
          <div>
            <div className="opacity-60 text-[10px] uppercase font-bold">Départ</div>
            <div>{ride.pickup_address || '—'}</div>
          </div>
        </div>
        <div className="flex items-start gap-2">
          <MapPin size={14} weight="fill" style={{ color: '#E01C2E' }} className="mt-0.5 shrink-0" />
          <div>
            <div className="opacity-60 text-[10px] uppercase font-bold">Destination</div>
            <div>{ride.dropoff_address || '—'}</div>
          </div>
        </div>
        {ride.distance_km && (
          <div className="text-[11px] opacity-70 pl-5">
            {parseFloat(ride.distance_km).toFixed(1)} km · {ride.fare_estimated} XAF estimé
          </div>
        )}
      </div>

      {ride.driver && role === 'rider' && (
        <DriverContactCard driver={ride.driver} />
      )}

      {/* iter133 — Driver MUST see rider name + phone after accept so they
           can call/WhatsApp before pickup (per Transport spec). Hidden in
           pending (no driver assigned yet) and after completion/cancel. */}
      {role === 'driver' && ride.rider
          && !['pending', 'completed', 'cancelled', 'cancelled_by_driver'].includes(ride.status) && (
        <ClientContactCard rider={ride.rider} />
      )}

      {/* Live driver position freshness — rider only */}
      {role === 'rider' && ride.driver_position && (
        <div className="text-[11px] opacity-70 text-center"
             data-testid="rider-driver-position-info">
          📍 Dernière position du chauffeur : il y a {timeAgo(ride.driver_position.at)}
        </div>
      )}

      {/* Driver action buttons */}
      {role === 'driver' && (
        <DriverActions ride={ride} busy={busy} transition={transition} />
      )}

      {/* Rider actions: WhatsApp share + cancel (early states) */}
      {role === 'rider' && !['completed', 'cancelled', 'cancelled_by_driver'].includes(ride.status) && (
        <RiderActions ride={ride} onCancel={async () => {
          if (!window.confirm('Annuler la course ?')) return;
          try {
            await axios.post(`${API}/api/transport/${ride.ride_id}/cancel`, {}, { withCredentials: true });
            toast.success('Course annulée');
            const fresh = await axios.get(`${API}/api/transport/${ride.ride_id}`, { withCredentials: true });
            onChange?.(fresh.data);
          } catch (e) {
            toast.error(e.response?.data?.detail || 'Erreur');
          }
        }} />
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════════ */
function DriverActions({ ride, busy, transition }) {
  if (ride.status === 'accepted') {
    return (
      <div className="grid grid-cols-1 gap-2">
        <button onClick={() => transition('en-route')} disabled={busy}
          className="jp-btn jp-btn-primary w-full font-bold flex items-center justify-center gap-2"
          data-testid="ride-action-en-route">
          <ArrowRight size={16} weight="bold" /> En route vers le passager
        </button>
        <button onClick={() => transition('cancel')} disabled={busy}
          className="jp-btn jp-btn-ghost w-full text-xs"
          style={{ color: '#b91c1c' }}
          data-testid="ride-action-cancel">
          Annuler
        </button>
      </div>
    );
  }
  if (ride.status === 'en_route') {
    return (
      <button onClick={() => transition('start')} disabled={busy}
        className="jp-btn w-full font-bold flex items-center justify-center gap-2"
        style={{ background: '#10b981', color: 'white' }}
        data-testid="ride-action-start">
        <Car size={16} weight="bold" /> Course démarrée — passager à bord
      </button>
    );
  }
  if (ride.status === 'started') {
    return (
      <button onClick={() => transition('complete')} disabled={busy}
        className="jp-btn w-full font-bold flex items-center justify-center gap-2"
        style={{ background: '#0F056B', color: 'white' }}
        data-testid="ride-action-complete">
        <CheckCircle size={16} weight="bold" /> Terminer la course
      </button>
    );
  }
  return null;
}

/* ════════════════════════════════════════════════════════════════════════ */
function RiderActions({ ride, onCancel }) {
  const [creatingShare, setCreatingShare] = useState(false);

  const shareWhatsapp = async () => {
    setCreatingShare(true);
    try {
      const r = await axios.post(
        `${API}/api/transport/${ride.ride_id}/share`,
        {}, { withCredentials: true },
      );
      const url = r.data.url;
      const driverInfo = ride.driver
        ? `${ride.driver.name} (${ride.driver.vehicle_model} ${ride.driver.vehicle_plate})`
        : 'Chauffeur en attente';
      const msg = encodeURIComponent(
        `🚕 Je prends un taxi JAPAP.\n\n` +
        `Départ : ${ride.pickup_address}\n` +
        `Destination : ${ride.dropoff_address}\n` +
        `Chauffeur : ${driverInfo}\n\n` +
        `Suivez ma course en direct :\n${url}\n\n` +
        `(Lien valide 12h)`,
      );
      window.open(`https://wa.me/?text=${msg}`, '_blank', 'noopener,noreferrer');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur de partage');
    } finally {
      setCreatingShare(false);
    }
  };

  return (
    <div className="grid grid-cols-2 gap-2">
      <button onClick={shareWhatsapp} disabled={creatingShare}
        className="jp-btn font-bold flex items-center justify-center gap-1.5 text-xs"
        style={{ background: '#25D366', color: 'white' }}
        data-testid="ride-share-whatsapp">
        <WhatsappLogo size={15} weight="fill" /> Partager sur WhatsApp
      </button>
      <button onClick={onCancel}
        className="jp-btn jp-btn-ghost text-xs"
        style={{ color: '#b91c1c', border: '1px solid #b91c1c' }}
        data-testid="ride-cancel">
        Annuler
      </button>
    </div>
  );
}

function timeAgo(iso) {
  if (!iso) return '—';
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}min`;
  return `${Math.floor(s / 3600)}h`;
}

/* iter133 — Client contact card shown to the DRIVER after acceptance.
   Surfaces rider name + Call + WhatsApp buttons so the driver can reach
   the rider during pickup. */
function ClientContactCard({ rider }) {
  const phone = (rider?.phone || '').replace(/[^\d+]/g, '');
  const waPhone = phone.startsWith('+') ? phone.slice(1) : phone;
  return (
    <div className="p-3 rounded-xl"
         style={{ background: 'linear-gradient(135deg, #FFF6E0, #FFEDD5)',
                  border: '1px solid #FED7AA' }}
         data-testid="ride-client-contact-card">
      <div className="flex items-center gap-3 mb-2">
        <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0 overflow-hidden"
             style={{ background: '#FFD700', color: '#111' }}>
          {rider.avatar
            ? <img src={rider.avatar} alt={rider.name} className="w-full h-full object-cover" />
            : <span className="font-bold text-base">{(rider.name || '?')[0]}</span>}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-widest font-bold opacity-60">
            Votre passager
          </div>
          <div className="font-bold text-sm truncate"
               data-testid="ride-client-name">
            {rider.name || 'Passager'}
          </div>
          {phone && (
            <div className="text-[11px] opacity-70 truncate font-mono"
                 data-testid="ride-client-phone">
              {rider.phone}
            </div>
          )}
        </div>
      </div>
      {phone ? (
        <div className="grid grid-cols-2 gap-2">
          <a href={`tel:${phone}`}
             className="jp-btn font-bold flex items-center justify-center gap-1.5 text-xs"
             style={{ background: '#0F056B', color: 'white' }}
             data-testid="ride-client-call">
            <PhoneCall size={14} weight="fill" /> Appeler
          </a>
          <a href={`https://wa.me/${waPhone}?text=${encodeURIComponent(
                'Bonjour, je suis votre chauffeur JAPAP. Je suis en route.'
              )}`}
             target="_blank" rel="noopener noreferrer"
             className="jp-btn font-bold flex items-center justify-center gap-1.5 text-xs"
             style={{ background: '#25D366', color: 'white' }}
             data-testid="ride-client-whatsapp">
            <WhatsappLogo size={14} weight="fill" /> WhatsApp
          </a>
        </div>
      ) : (
        <div className="text-[11px] opacity-60 text-center">
          Numéro non renseigné — utilisez le chat in-app.
        </div>
      )}
    </div>
  );
}

/* iter133 — Driver contact card shown to the RIDER once a driver accepts.
   Replaces the older inline driver bubble with a richer card that exposes
   the driver's phone (call + WhatsApp). */
function DriverContactCard({ driver }) {
  const phone = (driver?.phone || '').replace(/[^\d+]/g, '');
  const waPhone = phone.startsWith('+') ? phone.slice(1) : phone;
  return (
    <div className="p-3 rounded-xl"
         style={{ background: 'var(--jp-surface-secondary)',
                  border: '1px solid var(--jp-border)' }}
         data-testid="ride-driver-card">
      <div className="flex items-center gap-3 mb-2">
        <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0 overflow-hidden"
             style={{ background: 'var(--jp-surface)' }}>
          {driver.avatar
            ? <img src={driver.avatar} alt={driver.name} className="w-full h-full object-cover" />
            : <Car size={18} weight="duotone" />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-bold text-sm truncate">{driver.name}</div>
          <div className="text-[11px] opacity-70 truncate">
            {driver.vehicle_model} · {driver.vehicle_plate}
          </div>
        </div>
        <div className="text-xs flex items-center gap-1 shrink-0">
          <Star size={11} weight="fill" style={{ color: '#F7931A' }} />
          {parseFloat(driver.rating || '5').toFixed(1)}
        </div>
      </div>
      {phone && (
        <div className="grid grid-cols-2 gap-2">
          <a href={`tel:${phone}`}
             className="jp-btn font-bold flex items-center justify-center gap-1.5 text-xs"
             style={{ background: '#0F056B', color: 'white' }}
             data-testid="ride-driver-call">
            <PhoneCall size={14} weight="fill" /> Appeler
          </a>
          <a href={`https://wa.me/${waPhone}`}
             target="_blank" rel="noopener noreferrer"
             className="jp-btn font-bold flex items-center justify-center gap-1.5 text-xs"
             style={{ background: '#25D366', color: 'white' }}
             data-testid="ride-driver-whatsapp">
            <WhatsappLogo size={14} weight="fill" /> WhatsApp
          </a>
        </div>
      )}
    </div>
  );
}
