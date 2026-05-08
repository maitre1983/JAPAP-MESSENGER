import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { useCurrency, formatMoney } from '@/utils/currency';
import { useSmartUpsell } from '@/context/SmartUpsellContext';
import { useTranslation } from 'react-i18next';
import {
  WifiHigh, WifiHigh as Wifi, Plus, MapPin, Trophy, Lightning, Check, X,
  Crown, Eye, EyeSlash, Gear, Lock, Star, Sparkle, Coins, Info, ArrowRight, Globe,
  QrCode, Key, Users, Warning, Camera,
} from '@phosphor-icons/react';
import ConnectQRModal from '@/components/connect/ConnectQRModal';
import ConnectWifiCredentialsModal from '@/components/connect/ConnectWifiCredentialsModal';
import WifiQrScanModal from '@/components/connect/WifiQrScanModal';
import ShareHotspotCardModal from '@/components/connect/ShareHotspotCardModal';

const API = process.env.REACT_APP_BACKEND_URL;

const PLAN_LABEL = { 0: '—', 1: 'Starter', 2: 'Creator', 3: 'Business' };
const PLAN_FALLBACK_PRICE = { starter: 5, creator: 10, business: 30 };

export default function ConnectPage() {
  const { t } = useTranslation();
  const [view, setView] = useState('nearby'); // nearby | mine | leaderboard | earnings
  const [loc, setLoc] = useState(null);
  const [nearby, setNearby] = useState([]);
  const [mine, setMine] = useState([]);
  const [leaders, setLeaders] = useState([]);
  const [me, setMe] = useState(null);
  const [lbCountry, setLbCountry] = useState(''); // '' = global
  const [revshareHistory, setRevshareHistory] = useState({ items: [], total: 0, loaded: false });
  const [showCreate, setShowCreate] = useState(false);
  const [shareHotspotId, setShareHotspotId] = useState(null);  // iter141nineJ — post-create share modal
  const [qrHotspot, setQrHotspot] = useState(null);        // { hotspot } → ConnectQRModal
  const [wifiCredsHotspot, setWifiCredsHotspot] = useState(null);  // → ConnectWifiCredentialsModal
  const [activeConn, setActiveConn] = useState(null);
  const [proPlans, setProPlans] = useState({});   // {starter: "4.00", ...}
  const currency = useCurrency();
  const { trackProBlock } = useSmartUpsell();
  const navigate = useNavigate();

  // --- Geo + initial loads ----------------------------------------------------
  useEffect(() => {
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (p) => setLoc({ lat: p.coords.latitude, lng: p.coords.longitude }),
        () => setLoc({ lat: 4.0611, lng: 9.7876 }),
        { timeout: 5000 }
      );
    } else setLoc({ lat: 4.0611, lng: 9.7876 });
  }, []);

  const loadMe = useCallback(async () => {
    try {
      const [me, pro] = await Promise.all([
        axios.get(`${API}/api/connect/me`, { withCredentials: true }),
        axios.get(`${API}/api/pro/plans`, { withCredentials: true }).catch(() => null),
      ]);
      setMe(me.data);
      if (pro?.data?.plans) {
        const map = {};
        pro.data.plans.forEach(p => { map[p.plan_id] = p.price_usd; });
        setProPlans(map);
      }
    } catch {}
  }, []);

  const loadAll = useCallback(async () => {
    if (!loc) return;
    try {
      const [n, m] = await Promise.all([
        axios.get(`${API}/api/connect/nearby?lat=${loc.lat}&lng=${loc.lng}`, { withCredentials: true }),
        axios.get(`${API}/api/connect/my-hotspots`, { withCredentials: true }),
      ]);
      setNearby(n.data.hotspots); setMine(m.data);
    } catch {}
  }, [loc]);

  const loadLeaderboard = useCallback(async () => {
    try {
      const q = lbCountry ? `?country=${lbCountry}` : '';
      const { data } = await axios.get(`${API}/api/connect/leaderboard${q}`, { withCredentials: true });
      setLeaders(data);
    } catch {}
  }, [lbCountry]);

  useEffect(() => { loadMe(); }, [loadMe]);
  useEffect(() => { loadAll(); }, [loadAll]);
  useEffect(() => { if (view === 'leaderboard') loadLeaderboard(); }, [view, loadLeaderboard]);

  useEffect(() => {
    if (view === 'earnings' && me?.revshare?.eligible && !revshareHistory.loaded) {
      axios.get(`${API}/api/connect/revshare/history`, { withCredentials: true })
        .then(({ data }) => setRevshareHistory({ ...data, loaded: true }))
        .catch(() => setRevshareHistory((h) => ({ ...h, loaded: true })));
    }
  }, [view, me, revshareHistory.loaded]);

  // --- Actions ---------------------------------------------------------------
  const connect = async (hotspot) => {
    try {
      const { data } = await axios.post(`${API}/api/connect/start`,
        { hotspot_id: hotspot.hotspot_id, device_id: navigator.userAgent.slice(0, 100) },
        { withCredentials: true });
      if (data.blocked) toast.warning('Connexion enregistrée — plafond atteint (aucune récompense)');
      else toast.success(`Connecté à ${hotspot.alias} ✓`);
      setActiveConn({ ...data, hotspot });
    } catch (e) {
      const d = e.response?.data?.detail || '';
      if (typeof d === 'string' && d.startsWith('PRO_REQUIRED:')) {
        const [, , tier] = d.split(':');
        toast.error(`Accès réservé aux abonnés JAPAP ${tier?.toUpperCase() || 'PRO'}.`);
        trackProBlock('connect_access');
      } else toast.error(d || 'Erreur');
    }
  };

  const endSession = async () => {
    if (!activeConn) return;
    try {
      const { data } = await axios.post(`${API}/api/connect/end`,
        { connection_id: activeConn.connection_id }, { withCredentials: true });
      if (parseFloat(data.reward_usd) > 0) {
        toast.success(`Session terminée (${data.duration_seconds}s) — récompense : ${formatMoney(data.reward_usd, currency, { short: true })}`);
      } else {
        toast.info(`Session terminée (${data.duration_seconds}s)`);
      }
      setActiveConn(null); loadAll(); loadMe();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  const deleteHotspot = async (id) => {
    if (!window.confirm('Supprimer ce hotspot ?')) return;
    try {
      await axios.delete(`${API}/api/connect/hotspots/${id}`, { withCredentials: true });
      toast.success('Supprimé'); loadAll();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  const toggleActive = async (h) => {
    try {
      await axios.put(`${API}/api/connect/hotspots/${h.hotspot_id}`,
        { is_active: !h.is_active }, { withCredentials: true });
      loadAll();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  // --- Derived ---------------------------------------------------------------
  const gating = me?.gating || {};
  const canUse = gating.can_use !== false;
  const canShare = gating.can_share !== false;
  const rs = me?.revshare || {};
  const isBusiness = (me?.plan_rank || 0) >= 3;
  const level = me?.level || 1;
  const points = me?.points || 0;
  const progressPct = me?.progress_pct || 0;
  const countryList = useMemo(() => {
    // derive from `nearby` hotspots + user country hint — simple v1 list
    const cc = new Set();
    nearby.forEach(h => { if (h.country_code) cc.add(h.country_code); });
    mine.forEach(h => { if (h.country_code) cc.add(h.country_code); });
    return Array.from(cc);
  }, [nearby, mine]);

  return (
    <div className="px-4 sm:px-6 py-5 max-w-5xl mx-auto pb-28 jp-animate-fadeIn" data-testid="connect-page">

      {/* ───────── HEADER ───────── */}
      <header className="mb-5">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider mb-2"
          style={{ background: 'var(--jp-primary-subtle)', color: 'var(--jp-primary)' }}>
          <Wifi size={14} weight="duotone" /> JAPAP Connect
        </div>
        <h1 className="font-['Outfit'] text-3xl sm:text-4xl font-extrabold leading-tight"
          style={{ color: 'var(--jp-text)' }}>
          Internet partout. Récompenses à chaque connexion.
        </h1>
        <p className="text-sm mt-2 max-w-2xl" style={{ color: 'var(--jp-text-secondary)' }}>
          Connectez-vous au WiFi communautaire. Partagez le vôtre pour gagner.
          {me?.revshare?.enabled && me.revshare.pct > 0 && (
            <> Les abonnés <strong>{t('connect.business')}</strong> touchent <strong>{me.revshare.pct}%</strong> sur les abonnements Pro générés.</>
          )}
        </p>
      </header>

      {/* ───────── HERO STATS ───────── */}
      {me && (
        <section className="relative overflow-hidden rounded-3xl p-5 sm:p-6 mb-5 jp-animate-scaleIn" data-testid="connect-hero"
          style={{
            background: 'linear-gradient(135deg, #0EA5E9 0%, #6366F1 55%, #8B5CF6 100%)',
            color: 'white',
          }}>
          <div className="absolute inset-0 opacity-20" style={{
            background: 'radial-gradient(circle at 85% 20%, white 0%, transparent 45%)',
          }} />
          <div className="relative flex flex-col sm:flex-row items-start sm:items-center gap-4">
            <div className="flex items-center gap-3">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center backdrop-blur-sm"
                style={{ background: 'rgba(255,255,255,0.22)' }}>
                <Lightning size={26} weight="fill" />
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-widest opacity-85 font-bold">Mon niveau</div>
                <div className="font-['Outfit'] text-3xl font-black leading-none" data-testid="me-level">
                  Niv. {level}
                </div>
                <div className="text-xs opacity-90 mt-0.5">{points} pts · {me.points_to_next_level} pour niv. {level + 1}</div>
              </div>
            </div>

            <div className="flex-1 w-full">
              <div className="h-2.5 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.25)' }}>
                <div className="h-full rounded-full transition-all" style={{
                  width: `${progressPct}%`,
                  background: 'linear-gradient(90deg, #FDE68A, #FBBF24)',
                }} data-testid="me-progress" />
              </div>
              <div className="flex justify-between text-[11px] opacity-85 mt-1">
                <span>Niveau {level}</span>
                <span>{progressPct}%</span>
                <span>Niveau {level + 1}</span>
              </div>
            </div>
          </div>

          {/* Stats strip */}
          <div className="relative grid grid-cols-2 sm:grid-cols-4 gap-2 mt-5">
            <HeroStat label="Connexions faites" value={me.connections_made} testid="stat-connections-made" />
            <HeroStat label="Mes hotspots" value={me.hotspots_owned} testid="stat-hotspots-owned" />
            <HeroStat label="Connexions reçues" value={me.connections_received} testid="stat-connections-received" />
            <HeroStat label="Gains totaux" value={formatMoney(me.earned_usd, currency, { short: true })} testid="stat-earned" />
          </div>

          {/* Badges */}
          {me.badges?.length > 0 && (
            <div className="relative flex flex-wrap gap-2 mt-4" data-testid="me-badges">
              {me.badges.map(b => (
                <span key={b.id} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold"
                  style={{ background: 'rgba(255,255,255,0.22)', backdropFilter: 'blur(8px)' }}>
                  <span className="text-sm">{b.icon}</span> {b.label}
                </span>
              ))}
            </div>
          )}
        </section>
      )}

      {/* ───────── PRO GATING BANNERS ───────── */}
      {me && !canUse && (
        <GatingBanner
          tone="use"
          tier={gating.min_use}
          pct={rs.pct}
          price={proPlans[gating.min_use] || PLAN_FALLBACK_PRICE[gating.min_use]}
          testid="gating-use"
        />
      )}
      {me && canUse && !canShare && (
        <GatingBanner
          tone="share"
          tier={gating.min_share}
          pct={rs.pct}
          price={proPlans[gating.min_share] || PLAN_FALLBACK_PRICE[gating.min_share]}
          testid="gating-share"
        />
      )}

      {/* ───────── BUSINESS REVSHARE HIGHLIGHT ───────── */}
      {me && isBusiness && rs.enabled && (
        <BusinessRevshareCard
          rs={rs} currency={currency}
          onViewHistory={() => setView('earnings')}
          testid="revshare-card"
        />
      )}

      {/* ───────── ACTIVE SESSION ───────── */}
      {activeConn && (
        <div className="rounded-2xl p-4 mb-4 flex items-center gap-3" data-testid="active-session"
          style={{ background: 'linear-gradient(135deg, #10B981, #059669)', color: 'white' }}>
          <WifiHigh size={24} weight="fill" />
          <div className="flex-1">
            <div className="font-['Outfit'] font-bold">Session active — {activeConn.hotspot.alias}</div>
            <div className="text-xs opacity-90">Accès autorisé · terminez la session pour libérer la récompense du propriétaire.</div>
          </div>
          <button onClick={endSession} className="jp-btn jp-btn-sm"
            style={{ background: 'white', color: '#10B981' }} data-testid="end-session">
            Terminer
          </button>
        </div>
      )}

      {/* ───────── ACTION BAR ───────── */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        <div className="jp-tabs flex-1 min-w-0 overflow-x-auto">
          <TabButton active={view === 'nearby'} onClick={() => setView('nearby')} icon={<MapPin size={14} weight="bold" />} label={`À proximité (${nearby.length})`} testid="tab-nearby" />
          <TabButton active={view === 'mine'} onClick={() => setView('mine')} icon={<Gear size={14} weight="bold" />} label={`Mes hotspots (${mine.length})`} testid="tab-mine" />
          <TabButton active={view === 'leaderboard'} onClick={() => setView('leaderboard')} icon={<Trophy size={14} weight="bold" />} label="Top providers" testid="tab-leaderboard" />
          {isBusiness && rs.enabled && (
            <TabButton active={view === 'earnings'} onClick={() => setView('earnings')} icon={<Coins size={14} weight="bold" />} label="Mes revenus" testid="tab-earnings" />
          )}
        </div>
        {canShare && (
          <button onClick={() => setShowCreate(true)} className="jp-btn jp-btn-primary" data-testid="create-hotspot-btn">
            <Plus size={16} weight="bold" /> Partager mon WiFi
          </button>
        )}
      </div>

      {/* ───────── VIEWS ───────── */}
      {view === 'nearby' && (
        <div className="space-y-3" data-testid="nearby-list">
          {nearby.length === 0 && <EmptyState msg="Aucun hotspot à proximité. Soyez le premier à en partager !" />}
          {nearby.map(h => (
            <HotspotCard key={h.hotspot_id} h={h} currency={currency}
              action={
                h.wifi_configured ? (
                  <button
                    onClick={() => navigate('/connect/redeem')}
                    disabled={!canUse || (h.is_premium && (me?.plan_rank || 0) < 1)}
                    className="jp-btn jp-btn-primary jp-btn-sm"
                    data-testid={`scan-${h.hotspot_id}`}
                    title={!canUse ? 'Abonnement Pro requis' : 'Scannez le QR affiché par l\'hôte'}
                  >
                    {!canUse ? <><Lock size={14} weight="bold" /> Pro requis</> : <><QrCode size={14} weight="fill" /> Scanner</>}
                  </button>
                ) : (
                  <button
                    onClick={() => connect(h)}
                    disabled={!!activeConn || !canUse || (h.is_premium && (me?.plan_rank || 0) < 1)}
                    className="jp-btn jp-btn-ghost jp-btn-sm"
                    data-testid={`connect-${h.hotspot_id}`}
                    title={!canUse ? 'Abonnement Pro requis' : 'Check-in simple (pas de partage de mot de passe)'}
                  >
                    {!canUse ? <><Lock size={14} weight="bold" /> Pro requis</> : <><Lightning size={14} weight="fill" /> Check-in</>}
                  </button>
                )
              }
            />
          ))}
        </div>
      )}

      {view === 'mine' && (
        <div className="space-y-3" data-testid="mine-list">
          {mine.length === 0 && (
            <EmptyState msg={canShare
              ? "Vous n'avez pas encore partagé de WiFi. Commencez maintenant pour gagner des récompenses."
              : "Le partage de hotspot est réservé aux abonnés Business Pro."} />
          )}
          {mine.map(h => (
            <HotspotCard key={h.hotspot_id} h={h} currency={currency} owner
              action={
                <div className="flex items-center gap-1 flex-wrap justify-end">
                  <button onClick={() => setWifiCredsHotspot(h)}
                          className="jp-btn jp-btn-ghost jp-btn-sm"
                          data-testid={`wifi-creds-${h.hotspot_id}`}
                          title={h.wifi_configured ? 'Modifier le WiFi' : 'Configurer le WiFi'}>
                    <Key size={14} /> {h.wifi_configured ? 'WiFi ✓' : 'WiFi'}
                  </button>
                  {h.wifi_configured && (
                    <button onClick={() => setQrHotspot(h)}
                            className="jp-btn jp-btn-primary jp-btn-sm"
                            data-testid={`show-qr-${h.hotspot_id}`}>
                      <QrCode size={14} weight="fill" /> QR
                    </button>
                  )}
                  <button onClick={() => toggleActive(h)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid={`toggle-${h.hotspot_id}`}>
                    {h.is_active ? <Eye size={14} /> : <X size={14} />}
                  </button>
                  <button onClick={() => deleteHotspot(h.hotspot_id)} className="jp-btn jp-btn-ghost jp-btn-sm"
                    style={{ color: 'var(--jp-error)' }} data-testid={`delete-${h.hotspot_id}`}>
                    <X size={14} />
                  </button>
                </div>
              }
            />
          ))}
        </div>
      )}

      {view === 'leaderboard' && (
        <div data-testid="leaderboard-panel">
          {/* country filter */}
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-muted)' }}>Filtrer :</span>
            <button onClick={() => setLbCountry('')}
              className={`px-3 py-1.5 rounded-full text-xs font-bold inline-flex items-center gap-1 ${lbCountry === '' ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
              data-testid="lb-country-global">
              <Globe size={12} weight="bold" /> Monde
            </button>
            {countryList.map(cc => (
              <button key={cc} onClick={() => setLbCountry(cc)}
                className={`px-3 py-1.5 rounded-full text-xs font-bold ${lbCountry === cc ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
                data-testid={`lb-country-${cc}`}>
                {cc}
              </button>
            ))}
          </div>

          <div className="jp-card-elevated p-0 overflow-hidden" data-testid="leaderboard-list">
            <div className="grid grid-cols-[36px_1fr_auto_auto_auto] gap-2 sm:gap-3 px-4 py-2.5 text-[11px] font-bold uppercase tracking-wider"
              style={{ background: 'var(--jp-surface-2)', color: 'var(--jp-text-muted)' }}>
              <span>#</span>
              <span>{t('connect.provider')}</span>
              <span className="text-right hidden sm:block">Points</span>
              <span className="text-right">Connexions</span>
              <span className="text-right">Gains</span>
            </div>
            {leaders.length === 0 && <EmptyState msg="Classement bientôt disponible — soyez le premier à partager votre WiFi !" />}
            {leaders.map(l => (
              <div key={l.user_id}
                className="grid grid-cols-[36px_1fr_auto_auto_auto] gap-2 sm:gap-3 items-center px-4 py-3 border-b last:border-b-0"
                style={{ borderColor: 'var(--jp-border)' }}
                data-testid={`lb-row-${l.rank}`}>
                <span className="font-['Outfit'] font-extrabold text-sm"
                  style={{ color: l.rank === 1 ? '#F59E0B' : l.rank === 2 ? '#9CA3AF' : l.rank === 3 ? '#CD7F32' : 'var(--jp-text-muted)' }}>
                  {l.rank === 1 ? '🥇' : l.rank === 2 ? '🥈' : l.rank === 3 ? '🥉' : `#${l.rank}`}
                </span>
                <div className="flex items-center gap-2 min-w-0">
                  <div className="jp-avatar jp-avatar-sm jp-avatar-primary">{l.name?.[0] || '?'}</div>
                  <div className="min-w-0">
                    <div className="font-medium truncate inline-flex items-center gap-1">
                      {l.name}{l.is_pro && <Crown size={12} weight="fill" style={{ color: '#F7931A' }} />}
                    </div>
                    <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
                      {l.hotspots} hotspot(s)
                    </div>
                  </div>
                </div>
                <span className="text-right text-sm font-bold hidden sm:block" style={{ color: 'var(--jp-primary)' }}>
                  {l.points || 0}
                </span>
                <span className="text-right text-sm font-bold" style={{ color: 'var(--jp-text)' }}>
                  {l.connections}
                </span>
                <span className="text-right font-bold" style={{ color: '#10B981' }}>
                  {formatMoney(l.earned_usd, currency, { short: true })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {view === 'earnings' && isBusiness && rs.enabled && (
        <EarningsPanel rs={rs} history={revshareHistory} currency={currency} />
      )}

      {/* ───────── HOW IT WORKS ───────── */}
      <HowItWorks gating={gating} me={me} />

      {showCreate && loc && (
        <CreateHotspotModal loc={loc} onClose={() => setShowCreate(false)}
          onCreated={(newHotspotId) => {
            setShowCreate(false); loadAll(); loadMe();
            toast.success('Hotspot créé');
            if (newHotspotId) setShareHotspotId(newHotspotId);
          }} />
      )}

      {shareHotspotId && (
        <ShareHotspotCardModal
          hotspotId={shareHotspotId}
          onClose={() => setShareHotspotId(null)} />
      )}

      {qrHotspot && (
        <ConnectQRModal hotspot={qrHotspot} onClose={() => setQrHotspot(null)} />
      )}

      {wifiCredsHotspot && (
        <ConnectWifiCredentialsModal
          hotspot={wifiCredsHotspot}
          onClose={() => setWifiCredsHotspot(null)}
          onSaved={() => { loadAll(); loadMe(); }} />
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Sub-components
// ═══════════════════════════════════════════════════════════════════════════
function HeroStat({ label, value, testid }) {
  return (
    <div className="rounded-xl px-3 py-2 backdrop-blur-sm" style={{ background: 'rgba(255,255,255,0.14)' }} data-testid={testid}>
      <div className="text-[10px] uppercase tracking-widest opacity-80 font-bold">{label}</div>
      <div className="font-['Outfit'] text-lg font-black leading-tight">{value}</div>
    </div>
  );
}

function TabButton({ active, onClick, icon, label, testid }) {
  return (
    <button onClick={onClick} className={`jp-tab whitespace-nowrap ${active ? 'jp-tab-active' : ''}`} data-testid={testid}>
      <span className="inline-flex items-center gap-1">{icon} {label}</span>
    </button>
  );
}

function GatingBanner({ tone, tier, pct, price, testid }) {
  const label = PLAN_LABEL[{ starter: 1, creator: 2, business: 3 }[tier] || 0] || 'Pro';
  const displayPrice = price ?? (PLAN_FALLBACK_PRICE[tier] || 5);
  const isShare = tone === 'share';
  const pctLabel = pct && pct > 0 ? `${pct}%` : null;
  return (
    <div className="relative overflow-hidden rounded-2xl p-5 mb-4 jp-animate-scaleIn" data-testid={testid}
      style={{
        background: isShare
          ? 'linear-gradient(135deg, #F7931A 0%, #EC4899 100%)'
          : 'linear-gradient(135deg, #4338CA 0%, #EC4899 100%)',
        color: 'white',
      }}>
      <div className="absolute -right-10 -top-10 opacity-30">
        <Lock size={140} weight="duotone" />
      </div>
      <div className="relative flex flex-col sm:flex-row sm:items-center gap-4">
        <div className="flex-1">
          <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-widest"
            style={{ background: 'rgba(255,255,255,0.22)' }}>
            <Lock size={10} weight="bold" /> Accès verrouillé
          </div>
          <h3 className="font-['Outfit'] text-xl font-black mt-1.5">
            {isShare
              ? `🔒 Partage réservé aux ${label} Pro`
              : `🔒 Accès réservé aux ${label} Pro`}
          </h3>
          <p className="text-sm opacity-90 mt-1 max-w-xl">
            {isShare
              ? (pctLabel
                  ? `Devenez hôte JAPAP et touchez ${pctLabel} sur chaque abonnement Pro généré par vos utilisateurs connectés.`
                  : 'Devenez hôte JAPAP et touchez une part sur chaque abonnement Pro généré par vos utilisateurs connectés.')
              : `Débloquez le WiFi communautaire en illimité et gagnez ${label === 'Starter' ? 'des' : 'plus de'} points à chaque connexion.`}
          </p>
        </div>
        <div className="flex items-center gap-3 sm:flex-col sm:items-end">
          <div>
            <div className="text-[11px] opacity-85 font-bold uppercase tracking-wider">À partir de</div>
            <div className="font-['Outfit'] text-3xl font-black leading-none">${displayPrice}<span className="text-sm font-bold opacity-75">/mois</span></div>
          </div>
          <Link to="/pro" className="jp-btn inline-flex items-center gap-1.5" data-testid={`${testid}-cta`}
            style={{ background: 'white', color: '#4338CA' }}>
            Passer {label} <ArrowRight size={14} weight="bold" />
          </Link>
        </div>
      </div>
    </div>
  );
}

function BusinessRevshareCard({ rs, currency, onViewHistory, testid }) {
  return (
    <div className="relative overflow-hidden rounded-2xl p-5 mb-4" data-testid={testid}
      style={{
        background: 'linear-gradient(135deg, #064E3B 0%, #10B981 55%, #34D399 100%)',
        color: 'white',
      }}>
      <div className="absolute -right-6 -top-6 opacity-25">
        <Coins size={130} weight="duotone" />
      </div>
      <div className="relative flex flex-col sm:flex-row items-start sm:items-center gap-3">
        <div>
          <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-widest"
            style={{ background: 'rgba(255,255,255,0.22)' }}>
            <Star size={10} weight="fill" /> Business Pro
          </div>
          <h3 className="font-['Outfit'] text-xl font-black mt-1.5">
            💰 Vous gagnez {rs.pct}% sur les abonnements Pro générés
          </h3>
          <p className="text-sm opacity-90 mt-1">
            Quand un utilisateur connecté à votre WiFi s'abonne à JAPAP Pro, {rs.pct}% lui revient — crédité automatiquement sur votre wallet.
          </p>
        </div>
        <div className="flex-1" />
        <div className="flex items-center gap-3">
          <div className="text-right">
            <div className="text-[11px] uppercase tracking-widest opacity-85 font-bold">Gains cumulés</div>
            <div className="font-['Outfit'] text-2xl font-black">{formatMoney(rs.total_earned_usd || 0, currency, { short: true })}</div>
            <div className="text-[11px] opacity-85">30j : {formatMoney(rs.last_30d_usd || 0, currency, { short: true })} ({rs.last_30d_count || 0})</div>
          </div>
          <button onClick={onViewHistory} className="jp-btn jp-btn-sm" data-testid={`${testid}-history`}
            style={{ background: 'white', color: '#10B981' }}>
            Historique <ArrowRight size={12} weight="bold" />
          </button>
        </div>
      </div>
    </div>
  );
}

function HotspotCard({ h, owner, action, currency }) {
  const { t } = useTranslation();
  const typeLabel = { user: 'Perso', partner: 'Partenaire', public: 'Public' }[h.type] || h.type;
  const typeColor = { user: 'var(--jp-primary-subtle)', partner: '#FEF3C7', public: '#DBEAFE' }[h.type] || 'var(--jp-surface-2)';
  const typeText = { user: 'var(--jp-primary)', partner: '#B45309', public: '#1D4ED8' }[h.type] || 'var(--jp-text)';
  const [live, setLive] = useState(null);

  // Fetch live-stats once when the card mounts — debounced across re-renders.
  useEffect(() => {
    let alive = true;
    const fetchStats = async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/connect/hotspots/${h.hotspot_id}/live-stats`,
          { withCredentials: true });
        if (alive) setLive(data);
      } catch { /* silent — not a blocker */ }
    };
    fetchStats();
    const iv = setInterval(fetchStats, 30_000);   // refresh every 30s (gentle)
    return () => { alive = false; clearInterval(iv); };
  }, [h.hotspot_id]);

  return (
    <div className="jp-card p-4 flex items-start gap-3 jp-card-hover" data-testid={`hotspot-${h.hotspot_id}`}>
      <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style={{
        background: h.is_premium
          ? 'linear-gradient(135deg, #F7931A, #EC4899)'
          : h.is_sponsored ? 'linear-gradient(135deg, #F59E0B, #EC4899)' : 'var(--jp-primary-subtle)',
        color: (h.is_premium || h.is_sponsored) ? 'white' : 'var(--jp-primary)',
      }}>
        {h.is_premium ? <Crown size={20} weight="fill" /> : <WifiHigh size={20} weight="fill" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <strong className="font-['Outfit'] truncate">{h.alias}</strong>
          <span className="jp-badge" style={{ background: typeColor, color: typeText }}>{typeLabel}</span>
          {h.wifi_configured && (
            <span className="jp-badge inline-flex items-center gap-1"
                  style={{ background: 'rgba(16,185,129,0.12)', color: '#059669' }}
                  data-testid={`hotspot-${h.hotspot_id}-wifi-ready`}
                  title={t('connect.acces_protege_par_qr_japap')}>
              <Key size={10} weight="fill" /> QR WiFi
            </span>
          )}
          {/* iter141nineG — surface "WiFi non configuré" warning so owners
              don't unknowingly leave their hotspot in a half-built state.
              type='public' hotspots intentionally don't store creds. */}
          {!h.wifi_configured && h.type !== 'public' && (
            <span className="jp-badge inline-flex items-center gap-1"
                  style={{ background: 'rgba(239,68,68,0.12)', color: '#DC2626' }}
                  data-testid={`hotspot-${h.hotspot_id}-wifi-missing`}
                  title={t('connect.aucun_ssid_mot_de_passe_enregistre')}>
              <Warning size={10} weight="fill" /> WiFi à configurer
            </span>
          )}
          {h.is_premium && (
            <span className="jp-badge inline-flex items-center gap-1"
              style={{ background: 'linear-gradient(135deg, #F7931A, #EC4899)', color: 'white' }}>
              <Crown size={10} weight="fill" /> Premium
            </span>
          )}
          {h.is_sponsored && <span className="jp-badge" style={{ background: 'linear-gradient(135deg, #F59E0B, #EC4899)', color: 'white' }}>Partenaire {h.sponsor_name || ''}</span>}
          {h.zone && <span className="jp-badge jp-badge-neutral text-[10px]"><MapPin size={9} className="inline" /> {h.zone}</span>}
          {!h.is_active && <span className="jp-badge jp-badge-warning">Inactif</span>}
          {h.is_blocked && <span className="jp-badge jp-badge-error">Bloqué</span>}
          {h.distance_km !== undefined && <span className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>· {h.distance_km} km</span>}
        </div>
        {h.address && <div className="text-xs mt-0.5 truncate" style={{ color: 'var(--jp-text-muted)' }}><MapPin size={10} className="inline" /> {h.address}</div>}
        {h.description && <div className="text-xs mt-1" style={{ color: 'var(--jp-text-secondary)' }}>{h.description}</div>}

        {/* Social proof live counter — always render when live stats are loaded
            (zero-state kept intentionally to anchor the "someone will connect"
            social-proof signal on brand-new hotspots) */}
        {live && (
          <div className="text-[11px] mt-1 inline-flex items-center gap-1.5 font-['Manrope'] font-semibold"
               data-testid={`hotspot-${h.hotspot_id}-live-stats`}
               style={{ color: live.connected_now > 0 ? '#059669' : 'var(--jp-text-muted)' }}>
            {live.connected_now > 0 && (
              <span className="relative flex h-1.5 w-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75"
                      style={{ background: '#10B981' }} />
                <span className="relative inline-flex rounded-full h-1.5 w-1.5" style={{ background: '#10B981' }} />
              </span>
            )}
            <Users size={11} weight="bold" />
            {live.connected_now > 0
              ? `${live.connected_now} en ligne`
              : live.connected_today > 0
                ? `${live.connected_today} connecté${live.connected_today > 1 ? 's' : ''} aujourd'hui`
                : `Soyez le premier à scanner aujourd'hui`}
            {live.connected_now > 0 && live.connected_today > live.connected_now &&
              ` · ${live.connected_today} aujourd'hui`}
          </div>
        )}

        {owner && (
          <div className="text-[11px] mt-1 font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
            {h.total_connections} connexion(s) · {h.total_unique_users} uniques · Total gagné : <strong style={{ color: '#10B981' }}>{formatMoney(h.total_rewarded_usd, currency, { short: true })}</strong>
          </div>
        )}
        {!owner && h.owner && (
          <div className="text-[11px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
            par {h.owner.name} {h.owner.is_pro && <Crown size={10} weight="fill" className="inline" style={{ color: '#F7931A' }} />}
          </div>
        )}
      </div>
      <div className="ml-auto shrink-0">{action}</div>
    </div>
  );
}

function EmptyState({ msg }) {
  return <div className="jp-card p-8 text-center text-sm" style={{ color: 'var(--jp-text-muted)' }}>{msg}</div>;
}

function EarningsPanel({ rs, history, currency }) {
  return (
    <div className="space-y-4" data-testid="earnings-panel">
      <div className="grid grid-cols-3 gap-3">
        <MiniKPI label="Cumulés" value={formatMoney(rs.total_earned_usd || 0, currency, { short: true })} tone="emerald" testid="kpi-total" />
        <MiniKPI label="30 derniers jours" value={formatMoney(rs.last_30d_usd || 0, currency, { short: true })} tone="sky" testid="kpi-30d" />
        <MiniKPI label="Crédits (30j)" value={rs.last_30d_count || 0} tone="violet" testid="kpi-count" />
      </div>
      <div className="jp-card-elevated p-0 overflow-hidden">
        <div className="px-4 py-3 border-b flex items-center gap-2" style={{ borderColor: 'var(--jp-border)' }}>
          <Coins size={16} weight="fill" style={{ color: '#10B981' }} />
          <h3 className="font-['Outfit'] font-bold">Historique des parts reçues</h3>
        </div>
        {!history.loaded && <div className="p-6 text-center text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>}
        {history.loaded && history.items.length === 0 && (
          <EmptyState msg={rs.pct > 0
            ? `Aucun crédit reçu pour l'instant. Invitez vos utilisateurs à s'abonner — ${rs.pct}% vous reviennent automatiquement.`
            : "Aucun crédit reçu pour l'instant."} />
        )}
        {history.items.map((it, i) => (
          <div key={i} className="flex items-center gap-3 px-4 py-3 border-b last:border-b-0" style={{ borderColor: 'var(--jp-border)' }} data-testid={`earning-row-${i}`}>
            <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: '#D1FAE5', color: '#065F46' }}>
              <Coins size={14} weight="fill" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium truncate">{it.context || 'Revshare Pro'}</div>
              <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{new Date(it.created_at).toLocaleString()}</div>
            </div>
            <div className="text-right">
              <div className="font-['Outfit'] font-black text-sm" style={{ color: '#10B981' }}>
                +{formatMoney(it.amount_usd, currency, { short: true })}
              </div>
              {it.currency && it.currency !== 'USD' && (
                <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                  {it.amount_local} {it.currency}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      {rs.cap_monthly_usd > 0 && (
        <div className="text-[11px] flex items-center gap-1.5" style={{ color: 'var(--jp-text-muted)' }}>
          <Info size={12} /> Plafond mensuel : ${rs.cap_monthly_usd} par hôte.
        </div>
      )}
    </div>
  );
}

function MiniKPI({ label, value, tone, testid }) {
  const tones = {
    emerald: 'linear-gradient(135deg, #ECFDF5, #A7F3D0)',
    sky: 'linear-gradient(135deg, #EFF6FF, #BFDBFE)',
    violet: 'linear-gradient(135deg, #F5F3FF, #DDD6FE)',
  };
  const txt = { emerald: '#065F46', sky: '#1E3A8A', violet: '#4C1D95' }[tone];
  return (
    <div className="rounded-2xl p-4" style={{ background: tones[tone], color: txt }} data-testid={testid}>
      <div className="text-[11px] uppercase tracking-widest font-bold opacity-80">{label}</div>
      <div className="font-['Outfit'] text-xl font-black mt-1">{value}</div>
    </div>
  );
}

function HowItWorks({ gating, me }) {
  const { t } = useTranslation();
  const rank = me?.plan_rank || 0;
  const pct = me?.revshare?.pct || 0;
  const rsEnabled = !!me?.revshare?.enabled;
  return (
    <details className="jp-card mt-6 p-4" data-testid="how-it-works">
      <summary className="cursor-pointer flex items-center gap-2 font-['Outfit'] font-bold">
        <Sparkle size={16} weight="fill" style={{ color: 'var(--jp-primary)' }} />
        Comment ça marche ?
      </summary>
      <ol className="mt-3 space-y-2 text-sm" style={{ color: 'var(--jp-text-secondary)' }}>
        <li><strong>1. Se connecter</strong> — Ouvrez "À proximité", choisissez un hotspot, cliquez "Me connecter".{' '}
          {gating.min_use && gating.min_use !== 'none' && (
            <em>{t('connect.abonnement')}<strong>{gating.min_use}</strong> requis.</em>
          )}
        </li>
        <li><strong>2. Gagner</strong> — Chaque session valide vous rapporte des points Connect et crédite le propriétaire en wallet.</li>
        <li><strong>3. Partager</strong> — Publiez votre WiFi et recevez des crédits à chaque session
          {rsEnabled && pct > 0 && <> + <strong>{pct}%</strong> sur tous les abonnements Pro générés</>}
          .
          {gating.min_share && gating.min_share !== 'none' && (
            <em> {rank < (gating.share_required_rank || 0) ? 'Passez ' : 'Vous êtes '}<strong>{gating.min_share}</strong>{rank < (gating.share_required_rank || 0) ? ' pour débloquer.' : '.'}</em>
          )}
        </li>
      </ol>
    </details>
  );
}

function CreateHotspotModal({ loc, onClose, onCreated }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    alias: '', description: '', address: '',
    latitude: loc.lat, longitude: loc.lng,
    type: 'user', max_daily_users: 0,
    is_premium: false, country_code: '',
    // iter141nineG/H — bundle WiFi creds with creation. SSID + security are
    // AUTO-DETECTED via /api/connect/wifi-suggest (IP→ISP→SSID candidate)
    // so 90% of users only have to type the password.
    ssid: '', password: '', security_type: 'WPA2',
  });
  const [showPw, setShowPw] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showQrScan, setShowQrScan] = useState(false);
  const [scannedFromQr, setScannedFromQr] = useState(false);
  const [suggestion, setSuggestion] = useState(null);  // { ssid_suggestion, security_type, isp_name, confidence }
  const [err, setErr] = useState('');
  const { trackProBlock } = useSmartUpsell();

  // iter141nineH — auto-detect on mount. Best-effort, never blocks.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await axios.get(`${API}/api/connect/wifi-suggest`, { withCredentials: true });
        if (cancelled) return;
        setSuggestion(r.data);
        setForm(f => ({
          ...f,
          ssid: f.ssid || r.data.ssid_suggestion || '',
          security_type: r.data.security_type || f.security_type,
          country_code: f.country_code || r.data.country_code || '',
        }));
      } catch { /* ignore — user can edit manually */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const submit = async (e) => {
    e.preventDefault(); setErr('');
    // For non-public hotspots, both ssid + password are required so the
    // hotspot ships fully configured. Public hotspots are directory-only.
    if (form.type !== 'public') {
      const ssid = (form.ssid || '').trim();
      const pw = form.password || '';
      if (!ssid) { setErr("Le nom du réseau WiFi (SSID) est obligatoire."); return; }
      if (!pw)   { setErr("Le mot de passe WiFi est obligatoire."); return; }
    }
    try {
      const r = await axios.post(`${API}/api/connect/hotspots`, form, { withCredentials: true });
      onCreated(r.data?.hotspot_id);
    } catch (e) {
      // iter141nineH — handle Pydantic 422 (detail = array of {msg,loc}),
      // string detail, AND total fallback. Used to swallow real errors as
      // a generic "Erreur" toast.
      const d = e.response?.data?.detail;
      let msg;
      if (Array.isArray(d) && d.length) {
        msg = d.map(x => (x.msg || x.message || JSON.stringify(x))).join(' · ');
      } else if (typeof d === 'string') {
        msg = d;
      } else if (e.response?.data && typeof e.response.data === 'object') {
        msg = JSON.stringify(e.response.data).slice(0, 200);
      } else {
        msg = e.message || `Erreur ${e.response?.status || ''}`.trim() || 'Erreur';
      }
      if (typeof msg === 'string' && msg.startsWith('PRO_REQUIRED:')) {
        const [, , tier] = msg.split(':');
        setErr(`Abonnement ${tier?.toUpperCase() || 'PRO'} requis pour partager un hotspot.`);
        trackProBlock('connect_share');
      } else setErr(msg);
    }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }} data-testid="create-hotspot-modal">
      <div className="jp-card-elevated max-w-md w-full p-6 jp-animate-scaleIn max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-['Outfit'] text-lg font-bold">Partager mon WiFi</h3>
          <button onClick={onClose} className="p-1 rounded-lg"><X size={18} /></button>
        </div>
        <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
          <strong>🔒 Confidentialité :</strong> ton mot de passe WiFi est stocké
          chiffré (AES-256) sur nos serveurs et n'est révélé qu'aux utilisateurs
          que tu autorises via QR code dynamique (60 s).
        </p>
        <form onSubmit={submit} className="space-y-3">
          <div><label className="jp-label">Nom du POI (alias visible) *</label>
            <input className="jp-input text-sm" required value={form.alias}
              onChange={e => setForm(f => ({ ...f, alias: e.target.value }))} placeholder="Ex: Café Akwa" data-testid="hotspot-alias" /></div>

          {form.type !== 'public' && (
            <>
              <div className="rounded-xl p-3" style={{ background: 'rgba(247,147,26,0.08)', border: '1px solid rgba(247,147,26,0.25)' }}>
                <p className="text-[11px] mb-2 font-bold" style={{ color: '#F7931A' }}>
                  📶 Identifiants du réseau WiFi (chiffrés sur nos serveurs)
                </p>

                {/* iter141nineI — One-tap "Scan WiFi QR from your box" — fills
                    SSID + security + password from the standard WIFI:T:...;
                    URI scheme. 95% of consumer routers print such a QR. */}
                <button type="button"
                  onClick={() => setShowQrScan(true)}
                  data-testid="hotspot-qr-scan-button"
                  className="w-full mb-2 jp-btn jp-btn-sm flex items-center justify-center gap-1.5"
                  style={{
                    background: scannedFromQr
                      ? 'rgba(16,185,129,0.12)'
                      : 'linear-gradient(135deg, #4338ca 0%, #3730a3 100%)',
                    color: scannedFromQr ? '#059669' : '#fff',
                    border: scannedFromQr ? '1px solid rgba(16,185,129,0.30)' : 'none',
                  }}>
                  <Camera size={14} weight="fill" />
                  {scannedFromQr
                    ? 'WiFi importé depuis le QR ✓'
                    : 'Scanner le QR de ma box (auto-remplit tout)'}
                </button>

                {/* iter141nineH — Auto-detected SSID + security pill (from
                    ISP). User only types the password. The ✏️ toggle exposes
                    the inputs for the rare edge cases (custom router, etc). */}
                {!showAdvanced ? (
                  <div className="rounded-lg px-3 py-2 mb-2 flex items-center justify-between gap-2"
                       style={{ background: 'var(--jp-surface-secondary)', border: '1px dashed var(--jp-border)' }}
                       data-testid="wifi-autodetected-pill">
                    <div className="text-xs flex-1 min-w-0">
                      <div className="font-bold truncate" style={{ color: 'var(--jp-text)' }}>
                        ✨ Détecté automatiquement
                      </div>
                      <div className="truncate" style={{ color: 'var(--jp-text-muted)' }}>
                        {form.ssid || '—'} · {form.security_type || 'WPA2'}
                        {suggestion?.isp_name ? ` · ${suggestion.isp_name}` : ''}
                      </div>
                    </div>
                    <button type="button"
                      onClick={() => setShowAdvanced(true)}
                      className="text-[11px] underline whitespace-nowrap"
                      style={{ color: '#F7931A' }}
                      data-testid="wifi-edit-toggle">
                      ✏️ Modifier
                    </button>
                  </div>
                ) : (
                  <div className="space-y-2 mb-2">
                    <div>
                      <label className="jp-label">Nom du réseau (SSID) *</label>
                      <input className="jp-input text-sm" required
                        value={form.ssid}
                        onChange={e => setForm(f => ({ ...f, ssid: e.target.value }))}
                        placeholder={t('connect.ex_camtel_5g_2_4g')}
                        data-testid="hotspot-ssid"
                        maxLength={64} />
                    </div>
                    <div>
                      <label className="jp-label">Sécurité</label>
                      <select className="jp-input text-sm" value={form.security_type}
                        onChange={e => setForm(f => ({ ...f, security_type: e.target.value }))}
                        data-testid="hotspot-security">
                        <option value="WPA2">{t('connect.wpa2_le_plus_courant')}</option>
                        <option value="WPA3">WPA3</option>
                        <option value="WPA">WPA</option>
                        <option value="WEP">{t('connect.wep_deconseille')}</option>
                        <option value="OPEN">{t('connect.ouvert_sans_mot_de_passe')}</option>
                      </select>
                    </div>
                    <button type="button"
                      onClick={() => setShowAdvanced(false)}
                      className="text-[11px] underline"
                      style={{ color: 'var(--jp-text-muted)' }}>
                      ↩︎ Revenir au mode automatique
                    </button>
                  </div>
                )}

                <div>
                  <label className="jp-label">Mot de passe WiFi *</label>
                  <div className="relative">
                    <input className="jp-input text-sm pr-10"
                      type={showPw ? 'text' : 'password'}
                      required={form.type !== 'public'}
                      value={form.password}
                      onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                      placeholder={t('connect.au_moins_8_caracteres')}
                      data-testid="hotspot-password"
                      autoComplete="new-password"
                      maxLength={128} />
                    <button type="button"
                      onClick={() => setShowPw(s => !s)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded"
                      data-testid="hotspot-password-toggle"
                      aria-label={showPw ? 'Masquer le mot de passe' : 'Afficher le mot de passe'}>
                      {showPw ? <EyeSlash size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>
              </div>
            </>
          )}

          <div><label className="jp-label">Adresse (optionnel)</label>
            <input className="jp-input text-sm" value={form.address}
              onChange={e => setForm(f => ({ ...f, address: e.target.value }))} placeholder="Ex: 15 rue Foch, Douala" data-testid="hotspot-address" /></div>
          <div><label className="jp-label">Description (optionnel)</label>
            <input className="jp-input text-sm" value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))} placeholder={t('connect.wifi_fibre_rapide_acces_libre')} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="jp-label">Latitude</label>
              <input type="number" step="0.0001" className="jp-input text-sm" required value={form.latitude}
                onChange={e => setForm(f => ({ ...f, latitude: parseFloat(e.target.value) }))} /></div>
            <div><label className="jp-label">Longitude</label>
              <input type="number" step="0.0001" className="jp-input text-sm" required value={form.longitude}
                onChange={e => setForm(f => ({ ...f, longitude: parseFloat(e.target.value) }))} /></div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="jp-label">Type</label>
              <select className="jp-input text-sm" value={form.type} onChange={e => setForm(f => ({ ...f, type: e.target.value }))}>
                <option value="user">{t('connect.personnel')}</option>
                <option value="partner">{t('connect.partenaire_business')}</option>
                <option value="public">{t('connect.public')}</option>
              </select></div>
            <div><label className="jp-label">Pays (ISO 2)</label>
              <input type="text" maxLength={2} className="jp-input text-sm uppercase" value={form.country_code}
                onChange={e => setForm(f => ({ ...f, country_code: e.target.value.toUpperCase() }))} placeholder="CM" /></div>
          </div>
          <div><label className="jp-label">Limite d'utilisateurs/jour (0 = illimité)</label>
            <input type="number" min="0" className="jp-input text-sm" value={form.max_daily_users}
              onChange={e => setForm(f => ({ ...f, max_daily_users: parseInt(e.target.value) || 0 }))} /></div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={form.is_premium}
              onChange={e => setForm(f => ({ ...f, is_premium: e.target.checked }))} data-testid="hotspot-premium" />
            <Crown size={14} weight="fill" style={{ color: '#F7931A' }} />
            <span>{t('connect.hotspot')}<strong>{t('connect.premium')}</strong> (réservé aux abonnés Pro)</span>
          </label>
          {err && <div className="jp-alert jp-alert-error text-xs" data-testid="hotspot-form-error">{err}</div>}
          <button type="submit" className="jp-btn jp-btn-primary w-full" data-testid="create-hotspot-submit">
            <Check size={14} weight="bold" /> Créer le hotspot
          </button>
        </form>
      </div>

      {/* iter141nineI — WiFi QR Scan Modal mounted last so its z-index
          (110) sits above the create-hotspot modal (100). */}
      {showQrScan && (
        <WifiQrScanModal
          onClose={() => setShowQrScan(false)}
          onScanned={(parsed) => {
            setForm(f => ({
              ...f,
              ssid: parsed.ssid || f.ssid,
              password: parsed.password || f.password,
              security_type: parsed.security_type || f.security_type,
            }));
            setScannedFromQr(true);
            setShowQrScan(false);
            // Surface the detected fields so the user can confirm.
            setShowAdvanced(true);
          }}
        />
      )}
    </div>
  );
}
