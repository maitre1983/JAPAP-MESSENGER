/**
 * MarketplaceAdsPage (iter180) — Régie publicitaire interne JAPAP Marketplace
 * ============================================================================
 * Dashboard des campagnes Ads : création, liste, analytics live (CTR, spent).
 * Paiement 100% via Wallet USD JAPAP. Zéro hardcode (budgets, CPM, CPC, durée
 * pilotés par admin settings `/api/settings/public`).
 *
 * Endpoints consommés :
 *   POST   /api/ads_console/campaigns
 *   GET    /api/ads_console/campaigns/mine
 *   PATCH  /api/ads_console/campaigns/{id}   (status: paused|active|cancelled)
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import i18n from 'i18next';
import {
  Rocket, TrendUp, Eye, CursorClick, Coins, Pause, Play, XCircle,
  Plus, ArrowLeft, CurrencyDollar, Target, CalendarBlank,
} from '@phosphor-icons/react';
import Layout from '@/components/layout/Layout';

const API = process.env.REACT_APP_BACKEND_URL;
axios.defaults.withCredentials = true;

function fmtUSD(v) {
  const n = parseFloat(v || 0);
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtDate(s) {
  try {
    return new Date(s).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch { return s; }
}

const STATUS_CHIPS = (t) => ({
  active:    { label: 'Active',    color: '#10B981', bg: 'rgba(16,185,129,0.15)' },
  paused:    { label: 'En pause',  color: '#F59E0B', bg: 'rgba(245,158,11,0.15)' },
  completed: { label: i18n.t('marketplace_ads.terminee'),  color: '#6B7280', bg: 'rgba(107,114,128,0.15)' },
  cancelled: { label: i18n.t('marketplace_ads.annulee'),   color: '#EF4444', bg: 'rgba(239,68,68,0.15)' },
});

export default function MarketplaceAdsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const preselectProduct = params.get('product') || '';

  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(Boolean(preselectProduct));
  const [settings, setSettings] = useState({
    default_cpm_rate: 2.0, default_cpc_rate: 0.10,
    min_campaign_budget: 5.0, max_campaign_duration_days: 30,
    ads_enabled: true,
  });
  const [balance, setBalance] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [mine, s, w] = await Promise.all([
        axios.get(`${API}/api/ads_console/campaigns/mine`),
        axios.get(`${API}/api/settings/public`),
        axios.get(`${API}/api/wallet/balance`).catch(() => ({ data: null })),
      ]);
      setCampaigns(mine.data.items || []);
      const st = s.data?.settings || s.data || {};
      setSettings({
        default_cpm_rate: parseFloat(st.default_cpm_rate || 2.0),
        default_cpc_rate: parseFloat(st.default_cpc_rate || 0.10),
        min_campaign_budget: parseFloat(st.min_campaign_budget || 5.0),
        max_campaign_duration_days: parseInt(st.max_campaign_duration_days || 30, 10),
        ads_enabled: String(st.ads_enabled ?? 'true') === 'true',
      });
      if (w?.data) setBalance(w.data);
    } catch (e) {
      toast.error("Impossible de charger les campagnes.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const aggregates = useMemo(() => {
    const a = { total: 0, active: 0, impressions: 0, clicks: 0, spent: 0, budget: 0 };
    campaigns.forEach(c => {
      a.total += 1;
      if (c.status === 'active') a.active += 1;
      a.impressions += Number(c.impressions || 0);
      a.clicks += Number(c.clicks || 0);
      a.spent += parseFloat(c.spent_usd || 0);
      a.budget += parseFloat(c.budget_usd || 0);
    });
    a.ctr = a.impressions > 0 ? (a.clicks / a.impressions * 100).toFixed(2) : '0.00';
    return a;
  }, [campaigns]);

  const updateStatus = async (campaignId, newStatus) => {
    try {
      await axios.patch(`${API}/api/ads_console/campaigns/${campaignId}`,
        { status: newStatus },
        { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      toast.success(`Campagne ${newStatus === 'paused' ? 'mise en pause' : newStatus === 'active' ? 'reprise' : 'annulée'}`);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Erreur de mise à jour");
    }
  };

  return (
    <Layout>
      <div className="max-w-5xl mx-auto px-4 py-5" data-testid="marketplace-ads-page">
        {/* Header */}
        <div className="flex items-center gap-3 mb-5">
          <button onClick={() => navigate(-1)}
            data-testid="ads-back"
            className="jp-btn jp-btn-ghost jp-btn-sm inline-flex items-center gap-1">
            <ArrowLeft size={16} /> Retour
          </button>
          <div>
            <h1 className="text-2xl font-black flex items-center gap-2"
              style={{ color: 'var(--jp-primary)' }}>
              <Rocket size={24} weight="fill" /> Mes campagnes Ads
            </h1>
            <p className="text-xs opacity-70">Régie JAPAP — Wallet USD uniquement, audience ciblée.</p>
          </div>
        </div>

        {/* KPIs */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-5" data-testid="ads-kpis">
          <KPI label="Campagnes" value={aggregates.total} sub={`${aggregates.active} actives`} icon={<TrendUp size={18} />} />
          <KPI label="Impressions" value={aggregates.impressions.toLocaleString('fr-FR')} sub={`CTR ${aggregates.ctr}%`} icon={<Eye size={18} />} />
          <KPI label="Clics" value={aggregates.clicks.toLocaleString('fr-FR')} sub="total" icon={<CursorClick size={18} />} />
          <KPI label="Dépensé" value={`$${fmtUSD(aggregates.spent)}`} sub={`/ $${fmtUSD(aggregates.budget)}`} icon={<Coins size={18} />} />
        </div>

        {/* Create CTA */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-bold">Mes campagnes</h2>
          <button onClick={() => setShowCreate(true)}
            data-testid="ads-create-btn"
            className="jp-btn jp-btn-primary jp-btn-sm inline-flex items-center gap-1">
            <Plus size={16} weight="bold" /> Nouvelle campagne
          </button>
        </div>

        {/* List */}
        {loading ? (
          <div className="text-center py-8 opacity-60" data-testid="ads-loading">Chargement…</div>
        ) : campaigns.length === 0 ? (
          <EmptyState onCreate={() => setShowCreate(true)} />
        ) : (
          <div className="space-y-3" data-testid="ads-campaigns-list">
            {campaigns.map(c => (
              <CampaignCard key={c.campaign_id} c={c} onUpdate={updateStatus} />
            ))}
          </div>
        )}

        {/* Create modal */}
        {showCreate && (
          <CreateCampaignModal
            onClose={() => setShowCreate(false)}
            onSuccess={() => { setShowCreate(false); load(); }}
            settings={settings}
            balance={balance}
            preselectProduct={preselectProduct}
          />
        )}
      </div>
    </Layout>
  );
}

function KPI({ label, value, sub, icon }) {
  return (
    <div className="jp-card p-3">
      <div className="flex items-center justify-between opacity-70 text-xs mb-1">
        <span>{label}</span>{icon}
      </div>
      <div className="text-lg font-black leading-tight">{value}</div>
      <div className="text-xs opacity-60">{sub}</div>
    </div>
  );
}

function EmptyState({ onCreate }) {
  return (
    <div className="jp-card p-6 text-center" data-testid="ads-empty-state">
      <Rocket size={40} weight="duotone" style={{ color: 'var(--jp-primary)' }} className="mx-auto mb-2" />
      <div className="font-bold mb-1">Aucune campagne pour l'instant</div>
      <p className="text-sm opacity-70 mb-4">Lance ta première campagne pour booster la visibilité d'un produit dans le feed Marketplace.</p>
      <button onClick={onCreate}
        data-testid="ads-empty-create"
        className="jp-btn jp-btn-primary inline-flex items-center gap-1">
        <Plus size={16} weight="bold" /> Créer ma première campagne
      </button>
    </div>
  );
}

function CampaignCard({ c, onUpdate }) {
  const { t } = useTranslation();
  const chip = STATUS_CHIPS(t)[c.status] || STATUS_CHIPS(t).completed;
  const pct = parseFloat(c.budget_usd) > 0
    ? Math.min(100, (parseFloat(c.spent_usd) / parseFloat(c.budget_usd)) * 100) : 0;
  const ctr = c.ctr ?? 0;
  const img = Array.isArray(c.product_images) && c.product_images[0]
    ? (c.product_images[0].thumb || c.product_images[0].full || c.product_images[0])
    : (typeof c.product_images === 'string' ? null : null);

  return (
    <div className="jp-card p-3" data-testid={`ads-campaign-${c.campaign_id}`}>
      <div className="flex items-start gap-3">
        {img ? (
          <img src={img} alt="" className="w-14 h-14 rounded object-cover flex-shrink-0"
            onError={(e) => { e.currentTarget.style.display = 'none'; }} />
        ) : (
          <div className="w-14 h-14 rounded flex items-center justify-center flex-shrink-0"
            style={{ background: 'rgba(99,102,241,0.1)' }}>
            <Rocket size={22} style={{ color: 'var(--jp-primary)' }} />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <Link to={`/marketplace/p/${c.product_id}`}
                className="font-bold truncate hover:underline block">
                {c.product_title || c.name}
              </Link>
              <div className="text-xs opacity-60 truncate">#{c.campaign_id}</div>
            </div>
            <span className="text-xs font-bold px-2 py-0.5 rounded-full"
              style={{ color: chip.color, background: chip.bg }}
              data-testid={`ads-status-${c.campaign_id}`}>
              {chip.label}
            </span>
          </div>

          <div className="grid grid-cols-4 gap-2 mt-2 text-xs">
            <Stat label="Budget" value={`$${fmtUSD(c.budget_usd)}`} />
            <Stat label="Dépensé" value={`$${fmtUSD(c.spent_usd)}`} />
            <Stat label="Impr." value={c.impressions || 0} />
            <Stat label="Clics" value={`${c.clicks || 0} · ${ctr}%`} />
          </div>

          {/* Progress */}
          <div className="mt-2 h-1 rounded-full overflow-hidden" style={{ background: 'rgba(0,0,0,0.08)' }}>
            <div style={{ width: `${pct}%`, background: 'linear-gradient(90deg,#6366F1,#EC4899)', height: '100%' }} />
          </div>

          {/* Meta */}
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs opacity-70">
            <span><CalendarBlank size={12} className="inline mr-1" />Jusqu'au {fmtDate(c.end_date)}</span>
            <span><CurrencyDollar size={12} className="inline mr-1" />CPM ${fmtUSD(c.cpm_rate)} · CPC ${fmtUSD(c.cpc_rate)}</span>
            {c.is_global
              ? <span><Target size={12} className="inline mr-1" />🌍 Monde</span>
              : <span><Target size={12} className="inline mr-1" />{(c.target_countries || []).join(', ') || '—'}</span>}
            {(c.age_min || c.age_max) && (
              <span>Âge {c.age_min || 13}–{c.age_max || 99}</span>
            )}
          </div>

          {/* Actions */}
          {(c.status === 'active' || c.status === 'paused') && (
            <div className="mt-2 flex gap-2">
              {c.status === 'active' ? (
                <button onClick={() => onUpdate(c.campaign_id, 'paused')}
                  data-testid={`ads-pause-${c.campaign_id}`}
                  className="jp-btn jp-btn-ghost jp-btn-sm inline-flex items-center gap-1">
                  <Pause size={14} /> Mettre en pause
                </button>
              ) : (
                <button onClick={() => onUpdate(c.campaign_id, 'active')}
                  data-testid={`ads-resume-${c.campaign_id}`}
                  className="jp-btn jp-btn-primary jp-btn-sm inline-flex items-center gap-1">
                  <Play size={14} weight="fill" /> Reprendre
                </button>
              )}
              <button onClick={() => {
                onUpdate(c.campaign_id, 'cancelled');
              }}
                data-testid={`ads-cancel-${c.campaign_id}`}
                className="jp-btn jp-btn-ghost jp-btn-sm inline-flex items-center gap-1"
                style={{ color: 'var(--jp-error)' }}
                title={t('marketplace_ads.annule_definitivement_budget_restan')}>
                <XCircle size={14} /> Annuler
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div className="opacity-60">{label}</div>
      <div className="font-bold">{value}</div>
    </div>
  );
}

function CreateCampaignModal({ onClose, onSuccess, settings, balance, preselectProduct }) {
  const { t } = useTranslation();
  const [products, setProducts] = useState([]);
  const [productId, setProductId] = useState(preselectProduct || '');
  const [budget, setBudget] = useState(String(settings.min_campaign_budget));
  const [days, setDays] = useState('7');
  const [name, setName] = useState('');
  const [isGlobal, setIsGlobal] = useState(true);
  const [countries, setCountries] = useState([]);
  const [countryInput, setCountryInput] = useState('');
  const [ageMin, setAgeMin] = useState('');
  const [ageMax, setAgeMax] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    // Load user's own products by fetching /me + filtering marketplace
    (async () => {
      try {
        const me = await axios.get(`${API}/api/auth/me`);
        const uid = me.data?.user?.user_id || me.data?.user_id;
        const r = await axios.get(`${API}/api/marketplace/products?limit=50`);
        const mine = (r.data?.products || []).filter(p => p.seller_id === uid);
        setProducts(mine);
        if (!preselectProduct && mine[0]) setProductId(mine[0].product_id);
      } catch (_) {}
    })();
  }, [preselectProduct]);

  const addCountry = () => {
    const c = countryInput.trim().toUpperCase().slice(0, 2);
    if (c.length !== 2 || countries.includes(c) || countries.length >= 50) return;
    setCountries([...countries, c]);
    setCountryInput('');
  };

  const submit = async () => {
    const bNum = parseFloat(budget);
    if (!productId) return toast.error("Sélectionne un produit.");
    if (!(bNum >= parseFloat(settings.min_campaign_budget))) {
      return toast.error(`Budget minimum : $${settings.min_campaign_budget}`);
    }
    const balanceUsd = parseFloat(balance?.balance_usd || 0);
    if (balanceUsd < bNum) {
      return toast.error("Solde Wallet insuffisant. Recharge ton wallet avant de lancer la campagne.");
    }
    setSubmitting(true);
    try {
      const payload = {
        product_id: productId,
        name: name?.trim() || null,
        budget_usd: bNum,
        duration_days: Math.min(Math.max(parseInt(days || '7', 10) || 7, 1), settings.max_campaign_duration_days),
        is_global: isGlobal,
        target_countries: isGlobal ? [] : countries,
        age_min: ageMin ? parseInt(ageMin, 10) : null,
        age_max: ageMax ? parseInt(ageMax, 10) : null,
      };
      await axios.post(`${API}/api/ads_console/campaigns`, payload,
        { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      toast.success(`Campagne activée — $${bNum.toFixed(2)} débité du wallet`);
      onSuccess();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Erreur de création");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[1000] flex items-end md:items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.5)' }}
      onClick={onClose}
      data-testid="ads-create-modal">
      <div className="jp-card w-full md:max-w-md flex flex-col"
        style={{ maxHeight: 'min(95dvh, 95vh)', borderTopLeftRadius: 18, borderTopRightRadius: 18 }}
        onClick={(e) => e.stopPropagation()}>
        <div className="p-4 border-b flex items-center justify-between flex-shrink-0">
          <h3 className="font-black text-base flex items-center gap-2">
            <Rocket size={20} weight="fill" style={{ color: 'var(--jp-primary)' }} /> Nouvelle campagne Ads
          </h3>
          <button onClick={onClose} className="opacity-60">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto min-h-0 p-4 space-y-3">
          <Field label="Produit à promouvoir" required>
            <select value={productId} onChange={e => setProductId(e.target.value)}
              data-testid="ads-field-product"
              className="jp-input w-full">
              <option value="">— Sélectionner —</option>
              {products.map(p => (
                <option key={p.product_id} value={p.product_id}>{p.title}</option>
              ))}
            </select>
            {products.length === 0 && (
              <p className="text-xs opacity-60 mt-1">
                Aucun produit. <Link to="/services?view=marketplace" className="underline">Publier un produit d'abord</Link>.
              </p>
            )}
          </Field>

          <Field label="Nom (optionnel)">
            <input value={name} onChange={e => setName(e.target.value)}
              data-testid="ads-field-name"
              className="jp-input w-full"
              placeholder={t('marketplace_ads.ex_promo_week_end')} maxLength={120} />
          </Field>

          <div className="grid grid-cols-2 gap-2">
            <Field label={`Budget USD (min $${settings.min_campaign_budget})`} required>
              <input type="number" min={settings.min_campaign_budget} step="0.5"
                value={budget} onChange={e => setBudget(e.target.value)}
                data-testid="ads-field-budget"
                className="jp-input w-full" />
            </Field>
            <Field label={`Durée (j, max ${settings.max_campaign_duration_days})`} required>
              <input type="number" min="1" max={settings.max_campaign_duration_days}
                value={days} onChange={e => setDays(e.target.value)}
                data-testid="ads-field-days"
                className="jp-input w-full" />
            </Field>
          </div>

          <div className="p-2 rounded text-xs" style={{ background: 'rgba(99,102,241,0.08)' }}>
            <div className="font-bold mb-1">💡 Tarifs</div>
            <div>{t('marketplace_ads.cpm')}<b>${fmtUSD(settings.default_cpm_rate)}</b> / 1 000 impressions</div>
            <div>{t('marketplace_ads.cpc')}<b>${fmtUSD(settings.default_cpc_rate)}</b> / clic</div>
          </div>

          {/* Audience targeting */}
          <div className="p-2 rounded border" style={{ borderColor: 'rgba(0,0,0,0.1)' }}>
            <div className="text-sm font-bold mb-2 flex items-center gap-1">
              <Target size={14} /> Audience
            </div>
            <label className="flex items-center gap-2 text-sm mb-2">
              <input type="checkbox" checked={isGlobal}
                data-testid="ads-field-global"
                onChange={e => setIsGlobal(e.target.checked)} />
              🌍 Tous les pays
            </label>
            {!isGlobal && (
              <div>
                <div className="flex gap-1 mb-1">
                  <input value={countryInput}
                    data-testid="ads-field-country-input"
                    onChange={e => setCountryInput(e.target.value.toUpperCase().slice(0, 2))}
                    className="jp-input flex-1" placeholder="Code ISO2 (FR, CM…)" maxLength={2} />
                  <button onClick={addCountry}
                    data-testid="ads-field-country-add"
                    className="jp-btn jp-btn-sm">+</button>
                </div>
                <div className="flex flex-wrap gap-1">
                  {countries.map(c => (
                    <span key={c}
                      data-testid={`ads-field-country-chip-${c}`}
                      onClick={() => setCountries(countries.filter(x => x !== c))}
                      className="text-xs px-2 py-0.5 rounded-full cursor-pointer"
                      style={{ background: 'rgba(99,102,241,0.15)', color: 'var(--jp-primary)' }}>
                      {c} ✕
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-2 mt-2">
              <div>
                <label className="text-xs opacity-70">Âge min</label>
                <input type="number" min="13" max="99" value={ageMin}
                  data-testid="ads-field-age-min"
                  onChange={e => setAgeMin(e.target.value)}
                  className="jp-input w-full" placeholder="13" />
              </div>
              <div>
                <label className="text-xs opacity-70">Âge max</label>
                <input type="number" min="13" max="99" value={ageMax}
                  data-testid="ads-field-age-max"
                  onChange={e => setAgeMax(e.target.value)}
                  className="jp-input w-full" placeholder="99" />
              </div>
            </div>
          </div>

          {balance && (
            <div className="text-xs opacity-70" data-testid="ads-wallet-balance">
              Solde wallet : <b>${fmtUSD(balance.balance_usd)}</b>
            </div>
          )}
        </div>

        <div className="p-3 border-t flex-shrink-0" style={{ paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom))' }}>
          <button onClick={submit} disabled={submitting || !productId}
            data-testid="ads-create-submit"
            className="jp-btn jp-btn-primary w-full">
            {submitting ? t('marketplace_ads.creation') : `🚀 Lancer — $${fmtUSD(budget)}`}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, required, children }) {
  return (
    <label className="block text-sm">
      <span className="block mb-1 font-medium">{label}{required && <span style={{ color: 'var(--jp-error)' }}> *</span>}</span>
      {children}
    </label>
  );
}
