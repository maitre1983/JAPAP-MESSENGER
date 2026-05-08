/**
 * PublicRideTrackPage — Phase 3 read-only tracking via signed share link.
 *
 * Reached via /track/:token. No authentication required. Polls the public
 * endpoint every 8 seconds while the ride is active and renders a sanitised
 * view (rider first name only, driver + vehicle, status timeline,
 * pickup/dropoff addresses, driver position freshness).
 */
import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';
import { useTranslation } from 'react-i18next';
import i18n from 'i18next';
import {
  Car, MapPin, Hourglass, Star, ShieldCheck, WarningCircle, CheckCircle,
} from '@phosphor-icons/react';
import JapapLogo from '@/components/JapapLogo';

const API = process.env.REACT_APP_BACKEND_URL;

const STATUS_LABELS = (t) => ({
  pending:   { txt: 'Recherche d\'un chauffeur',  color: '#9A6700' },
  accepted:  { txt: 'Chauffeur en chemin',          color: '#0369a1' },
  en_route:  { txt: i18n.t('public_ride_track.chauffeur_arrive_bientot'),     color: '#7c3aed' },
  started:   { txt: 'Course en cours',              color: '#047857' },
  completed: { txt: i18n.t('public_ride_track.course_terminee'),           color: '#1f2937' },
  cancelled: { txt: i18n.t('public_ride_track.course_annulee'),               color: '#b91c1c' },
});


export default function PublicRideTrackPage() {
  const { t } = useTranslation();
  const { token } = useParams();
  const [ride, setRide] = useState(null);
  const [error, setError] = useState(null);
  const [tick, setTick] = useState(0);

  const fetchRide = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/api/transport/share/${token}`);
      setRide(r.data);
      setError(null);
    } catch (e) {
      setError({
        status: e.response?.status,
        message: e.response?.data?.detail || 'Lien invalide',
      });
    }
  }, [token]);

  useEffect(() => { fetchRide(); }, [fetchRide]);

  // Live polling — every 8s while the ride is active.
  useEffect(() => {
    if (!ride || ['completed', 'cancelled'].includes(ride.status)) return;
    const id = setInterval(() => setTick((n) => n + 1), 8000);
    return () => clearInterval(id);
  }, [ride]);

  useEffect(() => { if (tick > 0) fetchRide(); }, [tick, fetchRide]);

  if (error) {
    return (
      <ErrorScreen status={error.status} message={error.message} />
    );
  }
  if (!ride) {
    return (
      <FullCenter>
        <Hourglass size={32} className="animate-spin" style={{ color: 'var(--jp-primary)' }} />
        <div className="mt-3 text-sm opacity-70">Chargement de la course…</div>
      </FullCenter>
    );
  }

  const status = STATUS_LABELS(t)[ride.status] || STATUS_LABELS(t).pending;
  const isFinal = ['completed', 'cancelled'].includes(ride.status);

  return (
    <div className="min-h-screen flex flex-col" style={{ background: 'var(--jp-bg)' }}
         data-testid="public-ride-track-page">
      {/* Header */}
      <header className="px-4 py-3 flex items-center gap-2"
              style={{ background: 'var(--jp-surface)', borderBottom: '1px solid var(--jp-border)' }}>
        <JapapLogo width={28} />
        <div>
          <div className="font-['Outfit'] font-extrabold text-sm">JAPAP Taxi — Suivi en direct</div>
          <div className="text-[10px] opacity-60 flex items-center gap-1">
            <ShieldCheck size={10} /> Lien sécurisé · expire après 12h
          </div>
        </div>
      </header>

      <main className="flex-1 p-4 max-w-md mx-auto w-full space-y-4">
        {/* Hero status */}
        <div className="p-5 rounded-2xl text-center"
             style={{
               background: `linear-gradient(135deg, ${status.color}14, ${status.color}30)`,
               border: `2px solid ${status.color}`,
             }}>
          <div className="text-[10px] uppercase font-bold opacity-70 mb-1">Statut</div>
          <div className="text-xl font-extrabold" style={{ color: status.color }}>
            {status.txt}
          </div>
          <div className="mt-2 text-xs opacity-80">
            <strong>{ride.rider_first_name}</strong> est en route.
          </div>
        </div>

        {/* Trip timeline */}
        <div className="jp-card-elevated p-4 space-y-3" data-testid="public-trip-summary">
          <Row icon={MapPin} iconColor="#10b981"
               label="Départ" value={ride.pickup_address} />
          <Row icon={MapPin} iconColor="#E01C2E"
               label="Destination" value={ride.dropoff_address} />
          {ride.distance_km && (
            <div className="text-[11px] opacity-70 pl-8">
              ~{parseFloat(ride.distance_km).toFixed(1)} km
            </div>
          )}
        </div>

        {/* Driver block */}
        {ride.driver && (
          <div className="jp-card-elevated p-4 flex items-center gap-3" data-testid="public-driver-card">
            <div className="w-12 h-12 rounded-full flex items-center justify-center shrink-0"
                 style={{ background: 'var(--jp-surface-secondary)' }}>
              <Car size={22} weight="duotone" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-bold text-sm">
                Chauffeur : {ride.driver.first_name || 'JAPAP'}
              </div>
              <div className="text-xs opacity-70">
                {ride.driver.vehicle_model} · {ride.driver.vehicle_plate}
              </div>
            </div>
            <div className="text-xs flex items-center gap-1 shrink-0">
              <Star size={11} weight="fill" style={{ color: '#F7931A' }} />
              {parseFloat(ride.driver.rating || '5').toFixed(1)}
            </div>
          </div>
        )}

        {/* Driver position freshness */}
        {ride.driver_position && !isFinal && (
          <div className="text-center text-[11px] opacity-70" data-testid="public-driver-position">
            📍 Dernière position du chauffeur : il y a {timeAgo(ride.driver_position.at)}
          </div>
        )}

        {/* Lifecycle stamps */}
        <div className="jp-card-elevated p-3 space-y-1.5">
          <Stamp label="Demande créée"     iso={ride.created_at} done />
          <Stamp label="Acceptée"           iso={ride.accepted_at} done={!!ride.accepted_at} />
          <Stamp label="Chauffeur en route" iso={ride.en_route_at} done={!!ride.en_route_at} />
          <Stamp label="Course démarrée"    iso={ride.started_at} done={!!ride.started_at} />
          {ride.status === 'cancelled' ? (
            <Stamp label="Annulée" iso={ride.cancelled_at} done error />
          ) : (
            <Stamp label="Terminée" iso={ride.completed_at} done={!!ride.completed_at} />
          )}
        </div>

        <div className="text-center text-[10px] opacity-50 pt-2">
          Ce lien public ne contient ni les coordonnées exactes du passager, ni les
          détails de paiement. Il expire automatiquement.
        </div>
      </main>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════════ */
function Row({ icon: Icon, iconColor, label, value }) {
  return (
    <div className="flex items-start gap-2">
      <Icon size={16} weight="fill" style={{ color: iconColor }} className="mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="opacity-60 text-[10px] uppercase font-bold">{label}</div>
        <div className="text-sm">{value}</div>
      </div>
    </div>
  );
}

function Stamp({ label, iso, done, error }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <div className="flex items-center gap-2">
        {error ? (
          <WarningCircle size={14} weight="fill" style={{ color: '#b91c1c' }} />
        ) : done ? (
          <CheckCircle size={14} weight="fill" style={{ color: '#10b981' }} />
        ) : (
          <div className="w-3.5 h-3.5 rounded-full" style={{ border: '1.5px dashed var(--jp-border)' }} />
        )}
        <span className={done ? 'font-semibold' : 'opacity-50'}>{label}</span>
      </div>
      {iso && <span className="text-[10px] opacity-60">{new Date(iso).toLocaleTimeString('fr-FR')}</span>}
    </div>
  );
}

function FullCenter({ children }) {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6" style={{ background: 'var(--jp-bg)' }}>
      {children}
    </div>
  );
}

function ErrorScreen({ status, message }) {
  const { t } = useTranslation();
  const expired = status === 410;
  return (
    <FullCenter>
      <WarningCircle size={48} weight="fill" style={{ color: '#b91c1c' }} />
      <div className="mt-3 font-['Outfit'] font-bold text-lg">
        {expired ? t('public_ride_track.lien_expire') : 'Lien invalide'}
      </div>
      <div className="text-xs opacity-70 mt-1 text-center max-w-xs">{message}</div>
      <a href="/" className="jp-btn jp-btn-primary mt-4 text-xs">Retour à JAPAP</a>
    </FullCenter>
  );
}

function timeAgo(iso) {
  if (!iso) return '—';
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  return `${Math.floor(s / 3600)} h`;
}
