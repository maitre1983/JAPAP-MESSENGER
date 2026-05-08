import { useEffect, useState } from 'react';
import axios from 'axios';
import { useAuth } from '@/context/AuthContext';
import { CaretLeft, Taxi, Car, Star } from '@phosphor-icons/react';
import DriverKycForm from '@/components/transport/DriverKycForm';
import RideLifecyclePanel from '@/components/transport/RideLifecyclePanel';
import RideReviewModal from '@/components/transport/RideReviewModal';

const API = process.env.REACT_APP_BACKEND_URL;

// All non-final ride states. Anything in this list means we should display
// the full lifecycle panel (with polling, share button, action buttons)
// instead of either the request form (rider) or the empty driver dashboard.
const ACTIVE_STATUSES = ['pending', 'accepted', 'en_route', 'started'];

// Default coordinates: Yaoundé city centre
const DEFAULT_CENTER = { lat: 3.8480, lng: 11.5021 };

export default function TransportModule({ onBack }) {
  const { user } = useAuth();
  const [mode, setMode] = useState('rider'); // rider | driver
  const [form, setForm] = useState({
    pickup_address: '', dropoff_address: '',
    pickup_lat: DEFAULT_CENTER.lat, pickup_lng: DEFAULT_CENTER.lng,
    dropoff_lat: DEFAULT_CENTER.lat + 0.02, dropoff_lng: DEFAULT_CENTER.lng + 0.015,
    vehicle_type: 'standard', notes: '',
  });
  const [estimate, setEstimate] = useState(null);
  const [activeRide, setActiveRide] = useState(null);             // rider's active ride (full payload)
  const [driverActiveRide, setDriverActiveRide] = useState(null); // driver's active ride (full payload)
  const [pendingReviewRide, setPendingReviewRide] = useState(null); // completed-not-rated ride awaiting modal
  const [history, setHistory] = useState([]);
  const [driverProfile, setDriverProfile] = useState(null);
  const [available, setAvailable] = useState([]);
  const [kycStatus, setKycStatus] = useState(null); // {status: 'none'|'pending'|'approved'|'rejected'}
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const loadAll = async () => {
    try {
      const [h, d, k] = await Promise.all([
        axios.get(`${API}/api/transport/my-rides`, { withCredentials: true }),
        axios.get(`${API}/api/transport/driver/me`, { withCredentials: true }),
        axios.get(`${API}/api/kyc/status`, { withCredentials: true }).catch(() => null),
      ]);
      setHistory(h.data);
      setDriverProfile(d.data);
      if (k?.data) setKycStatus(k.data);
      // Detect both an active rider ride and an active driver ride. They are
      // mutually independent: a user can simultaneously be waiting on a taxi
      // (rider role) AND driving someone else (driver role).
      const riderActive = h.data.find(r => r.role === 'rider' && ACTIVE_STATUSES.includes(r.status));
      const driverActive = h.data.find(r => r.role === 'driver' && ACTIVE_STATUSES.includes(r.status));
      // Enrich each with full ride detail (driver position, vehicle info,
      // accepted_at/en_route_at timestamps) — my-rides only returns flat row.
      const enrich = async (ride) => {
        if (!ride) return null;
        try {
          const r = await axios.get(`${API}/api/transport/${ride.ride_id}`, { withCredentials: true });
          return r.data;
        } catch { return ride; }
      };
      const [riderFull, driverFull] = await Promise.all([enrich(riderActive), enrich(driverActive)]);
      setActiveRide(riderFull);
      setDriverActiveRide(driverFull);

      // Detect the most recent completed rider ride that has NOT been rated
      // yet AND has not been "Plus tard"-skipped on this device. Auto-pop the
      // review modal once on mount (and after every loadAll while still
      // pending). Only inspects the latest rider-completed ride to avoid
      // pestering on multi-day-old rides.
      if (!riderFull) {
        const lastCompletedRider = h.data.find(
          (r) => r.role === 'rider' && r.status === 'completed' && r.driver_id,
        );
        if (lastCompletedRider) {
          let skipped = null;
          try { skipped = localStorage.getItem(`ride_review_skipped:${lastCompletedRider.ride_id}`); } catch {}
          if (!skipped) {
            try {
              const rv = await axios.get(
                `${API}/api/transport/${lastCompletedRider.ride_id}/review`,
                { withCredentials: true },
              );
              if (rv.data?.can_submit) {
                // Enrich with driver info so the modal can show the driver name.
                const full = await enrich(lastCompletedRider);
                setPendingReviewRide(full);
              }
            } catch {}
          }
        }
      }
    } catch {}
  };
  const loadAvailable = async () => {
    try {
      const { data } = await axios.get(`${API}/api/transport/available`, { withCredentials: true });
      setAvailable(data);
    } catch {}
  };

  useEffect(() => { loadAll(); }, []);

  const getEstimate = async () => {
    setError('');
    try {
      const { data } = await axios.get(`${API}/api/transport/estimate`, {
        params: {
          pickup_lat: form.pickup_lat, pickup_lng: form.pickup_lng,
          dropoff_lat: form.dropoff_lat, dropoff_lng: form.dropoff_lng,
          vehicle_type: form.vehicle_type,
        },
        withCredentials: true,
      });
      setEstimate(data);
    } catch (err) { setError(err.response?.data?.detail || 'Erreur'); }
  };

  useEffect(() => {
    if (form.pickup_lat && form.pickup_lng && form.dropoff_lat && form.dropoff_lng) getEstimate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.pickup_lat, form.pickup_lng, form.dropoff_lat, form.dropoff_lng, form.vehicle_type]);

  const requestRide = async (e) => {
    e.preventDefault(); setError(''); setMessage('');
    if (!form.pickup_address || !form.dropoff_address) {
      setError('Adresses requises'); return;
    }
    try {
      const { data } = await axios.post(`${API}/api/transport/request`, form, { withCredentials: true });
      setMessage(`Demande envoyée ! En attente d'un chauffeur.`);
      loadAll();
    } catch (err) { setError(err.response?.data?.detail || 'Erreur'); }
  };

  const acceptRide = async (rideId) => {
    try {
      await axios.post(`${API}/api/transport/${rideId}/accept`, {}, { withCredentials: true });
      setMessage('Course acceptée');
      loadAvailable(); loadAll();
    } catch (err) { setError(err.response?.data?.detail || 'Erreur'); }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto pb-24" data-testid="transport-page">
      <button onClick={onBack} className="text-xs font-['Manrope'] mb-3 flex items-center gap-1"
        style={{ color: 'var(--jp-text-muted)' }} data-testid="back-to-services-from-transport">
        <CaretLeft size={14} /> Services
      </button>
      <h1 className="font-['Outfit'] text-2xl font-bold mb-1" style={{ color: 'var(--jp-text)' }}>Transport JAPAP</h1>
      <p className="text-xs mb-6" style={{ color: 'var(--jp-text-secondary)' }}>
        Taxi on-demand. Paiement direct via wallet JAPAP.
      </p>

      {/* Mode toggle */}
      <div className="jp-tabs mb-6">
        <button onClick={() => setMode('rider')} className={`jp-tab ${mode === 'rider' ? 'jp-tab-active' : ''}`} data-testid="mode-rider">
          <Taxi size={14} className="inline mr-1.5" /> Passager
        </button>
        <button onClick={() => { setMode('driver'); loadAvailable(); }} className={`jp-tab ${mode === 'driver' ? 'jp-tab-active' : ''}`} data-testid="mode-driver">
          <Car size={14} className="inline mr-1.5" /> Chauffeur
        </button>
      </div>

      {message && <div className="jp-alert jp-alert-success mb-4">{message}</div>}
      {error && <div className="jp-alert jp-alert-error mb-4">{error}</div>}

      {/* RIDER MODE */}
      {mode === 'rider' && (
        <>
          {activeRide ? (
            <div className="jp-card-elevated p-5 mb-6" data-testid="active-ride">
              <RideLifecyclePanel
                ride={activeRide}
                role="rider"
                onChange={(r) => {
                  if (r && r.status === 'completed') {
                    // Capture the just-completed ride so the review modal
                    // can pop with full driver context.
                    setPendingReviewRide(r);
                    setActiveRide(null);
                    loadAll();
                  } else if (r && r.status === 'cancelled') {
                    setActiveRide(null);
                    loadAll();
                  } else if (r) {
                    setActiveRide(r);
                  }
                }}
              />
            </div>
          ) : (
            <form onSubmit={requestRide} className="jp-card-elevated p-5 mb-6 space-y-4" data-testid="rider-form">
              <div>
                <label className="jp-label">Point de départ</label>
                <input required value={form.pickup_address} onChange={e => setForm(f => ({ ...f, pickup_address: e.target.value }))}
                  className="jp-input text-sm" placeholder="Adresse de départ" data-testid="pickup-address" />
              </div>
              <div>
                <label className="jp-label">Destination</label>
                <input required value={form.dropoff_address} onChange={e => setForm(f => ({ ...f, dropoff_address: e.target.value }))}
                  className="jp-input text-sm" placeholder="Adresse d'arrivée" data-testid="dropoff-address" />
              </div>
              <div>
                <label className="jp-label">Type de véhicule</label>
                <div className="grid grid-cols-2 gap-2">
                  <button type="button" onClick={() => setForm(f => ({ ...f, vehicle_type: 'standard' }))}
                    className={`p-3 rounded-xl text-left ${form.vehicle_type === 'standard' ? 'ring-2' : ''}`}
                    style={{ background: 'var(--jp-surface-secondary)', '--tw-ring-color': 'var(--jp-primary)' }}
                    data-testid="vehicle-standard">
                    <p className="text-xs font-bold font-['Manrope']" style={{ color: 'var(--jp-text)' }}>Standard</p>
                    <p className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>500 base + 200/km</p>
                  </button>
                  <button type="button" onClick={() => setForm(f => ({ ...f, vehicle_type: 'premium' }))}
                    className={`p-3 rounded-xl text-left ${form.vehicle_type === 'premium' ? 'ring-2' : ''}`}
                    style={{ background: 'var(--jp-surface-secondary)', '--tw-ring-color': 'var(--jp-primary)' }}
                    data-testid="vehicle-premium">
                    <p className="text-xs font-bold font-['Manrope']" style={{ color: 'var(--jp-text)' }}>Premium</p>
                    <p className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>500 base + 350/km</p>
                  </button>
                </div>
              </div>

              {estimate && (
                <div className="p-3 rounded-xl" style={{ background: 'var(--jp-primary-subtle)' }} data-testid="fare-estimate">
                  <div className="flex justify-between items-center">
                    <div>
                      <p className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--jp-primary)' }}>Estimation</p>
                      {estimate.fare_low && estimate.fare_high && estimate.fare_low !== estimate.fare_high ? (
                        <p className="font-['Outfit'] text-xl font-extrabold" style={{ color: 'var(--jp-primary)' }}>
                          {parseFloat(estimate.fare_low).toLocaleString('fr-FR')}
                          <span className="text-base opacity-60"> – {parseFloat(estimate.fare_high).toLocaleString('fr-FR')}</span>
                          <span className="text-xs opacity-60"> {estimate.currency || 'XAF'}</span>
                        </p>
                      ) : (
                        <p className="font-['Outfit'] text-xl font-extrabold" style={{ color: 'var(--jp-primary)' }}>
                          {parseFloat(estimate.fare_estimated).toLocaleString('fr-FR')} {estimate.currency || 'XAF'}
                        </p>
                      )}
                      {estimate.surge_label && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase px-1.5 py-0.5 rounded mt-1"
                              style={{ background: '#E01C2E20', color: '#E01C2E' }}
                              data-testid="fare-surge-label">
                          ⚡ {estimate.surge_label}
                        </span>
                      )}
                    </div>
                    <div className="text-right text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                      <p>{estimate.distance_km} km</p>
                      <p className="capitalize">{estimate.vehicle_type}</p>
                    </div>
                  </div>
                </div>
              )}

              <button type="submit" className="jp-btn jp-btn-primary jp-btn-lg jp-btn-full" data-testid="request-ride-btn">
                Demander une course
              </button>
            </form>
          )}

          <div className="jp-card p-4">
            <h3 className="font-['Outfit'] text-sm font-bold mb-3" style={{ color: 'var(--jp-text)' }}>Historique</h3>
            <div className="space-y-2">
              {history.slice(0, 10).map(r => (
                <div key={r.ride_id} className="p-3 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }} data-testid={`history-ride-${r.ride_id}`}>
                  <div className="flex justify-between items-start mb-1">
                    <span className="text-[10px] uppercase font-bold" style={{ color: 'var(--jp-text-muted)' }}>{r.role}</span>
                    <span className={`jp-badge text-[10px] ${r.status === 'completed' ? 'jp-badge-success' : r.status === 'cancelled' ? 'jp-badge-neutral' : 'jp-badge-warning'}`}>
                      {r.status}
                    </span>
                  </div>
                  <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{r.pickup_address} → {r.dropoff_address}</p>
                  <p className="text-[10px] font-bold" style={{ color: 'var(--jp-primary)' }}>
                    {parseFloat(r.fare_final || r.fare_estimated).toLocaleString('fr-FR')} XAF
                  </p>
                </div>
              ))}
              {history.length === 0 && <p className="text-xs text-center py-4" style={{ color: 'var(--jp-text-muted)' }}>Aucune course.</p>}
            </div>
          </div>
        </>
      )}

      {/* DRIVER MODE */}
      {mode === 'driver' && (
        <>
          {/* iter97 — Strict KYC. The form drives both first-time registration
              AND post-rejection resubmission. The 'is_driver' flag becomes true
              ONLY after the admin approves; until then the form stays visible
              with a status banner. */}
          {!driverProfile?.is_driver || driverProfile?.kyc_status !== 'approved' ? (
            <DriverKycForm
              profile={driverProfile?.is_driver ? driverProfile : null}
              onSubmitted={() => loadAll()}
            />
          ) : (
            <>
              <div className="jp-card-elevated p-5 mb-4" data-testid="driver-profile">
                <div className="flex justify-between items-center mb-3">
                  <div>
                    <p className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>Mon profil chauffeur</p>
                    <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>
                      {driverProfile.vehicle_model} - {driverProfile.vehicle_plate}
                    </p>
                  </div>
                  <div className="text-right">
                    <div className="flex items-center gap-1 text-xs" style={{ color: '#F59E0B' }}>
                      <Star size={14} weight="fill" /> {parseFloat(driverProfile.rating).toFixed(1)}
                    </div>
                    <p className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{driverProfile.total_rides} courses</p>
                  </div>
                </div>
              </div>

              <h3 className="font-['Outfit'] text-base font-bold mb-3" style={{ color: 'var(--jp-text)' }}>Courses disponibles ({available.length})</h3>
              <div className="space-y-3">
                {available.map(r => (
                  <div key={r.ride_id} className="jp-card p-4" data-testid={`available-${r.ride_id}`}>
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{r.rider.name}</p>
                        <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>{r.pickup_address} → {r.dropoff_address}</p>
                      </div>
                      <span className="font-['Outfit'] font-bold text-sm" style={{ color: 'var(--jp-primary)' }}>
                        {parseFloat(r.fare_estimated).toLocaleString('fr-FR')} XAF
                      </span>
                    </div>
                    <p className="text-[10px] mb-2" style={{ color: 'var(--jp-text-muted)' }}>{r.distance_km} km • {r.vehicle_type}</p>
                    <button onClick={() => acceptRide(r.ride_id)} className="jp-btn jp-btn-primary jp-btn-sm jp-btn-full" data-testid={`accept-${r.ride_id}`}>
                      Accepter
                    </button>
                  </div>
                ))}
                {available.length === 0 && <p className="text-xs text-center py-6" style={{ color: 'var(--jp-text-muted)' }}>Aucune course en attente.</p>}
              </div>

              {/* Active driver ride — lifecycle panel with En route / Démarrer / Terminer buttons. */}
              {driverActiveRide && (
                <div className="jp-card-elevated p-4 mt-4" data-testid={`my-active-ride-${driverActiveRide.ride_id}`}>
                  <RideLifecyclePanel
                    ride={driverActiveRide}
                    role="driver"
                    onChange={(r) => {
                      if (r && ['completed', 'cancelled'].includes(r.status)) {
                        setDriverActiveRide(null);
                        loadAll();
                      } else if (r) {
                        setDriverActiveRide(r);
                      }
                    }}
                  />
                </div>
              )}
            </>
          )}
        </>
      )}

      {/* Auto-popped post-completion rider rating modal. */}
      <RideReviewModal
        ride={pendingReviewRide}
        onSubmitted={() => { setPendingReviewRide(null); loadAll(); }}
        onSkip={() => setPendingReviewRide(null)}
      />
    </div>
  );
}
