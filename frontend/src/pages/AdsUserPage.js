/**
 * AdsUserPage — Création & suivi de campagnes publicitaires (advertiser side)
 * ===========================================================================
 * L'utilisateur choisit une cible (post/reel/produit), fixe budget USD et
 * CPM, puis lance. Le wallet est débité (escrow). Chaque campagne est
 * modérée par l'admin. Stats impressions/clics/dépensé live.
 */
import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import i18n from 'i18next';
import {
  Megaphone, Plus, X, Check, Eye, TrendUp, Coins, Image as ImageIcon,
  Rocket, ChartLineUp, Info, Link as LinkIcon,
} from '@phosphor-icons/react';
import { useCurrency, formatMoney } from '@/utils/currency';

const API = process.env.REACT_APP_BACKEND_URL;

const STATUS_STYLE = (t) => ({
  pending:  { bg: '#FEF3C7', fg: '#B45309', label: 'En attente' },
  approved: { bg: '#D1FAE5', fg: '#065F46', label: i18n.t('ads_user.approuve') },
  running:  { bg: '#DBEAFE', fg: '#1E40AF', label: 'Actif' },
  ended:    { bg: '#E5E7EB', fg: '#374151', label: i18n.t('ads_user.termine') },
  rejected: { bg: '#FEE2E2', fg: '#991B1B', label: i18n.t('ads_user.refuse') },
});

const TARGET_LABEL = (t) => ({ post: 'Post feed', reel: 'Reel', product: 'Produit', banner: i18n.t('ads_user.banniere') });
const PLAN_PRICES = {
  post: { cpm: 1, recommended_budget: 5 },
  reel: { cpm: 3, recommended_budget: 15 },
  product: { cpm: 2, recommended_budget: 10 },
  banner: { cpm: 1.5, recommended_budget: 20 },
};

export default function AdsUserPage({ onBack }) {
  const { t } = useTranslation();
  const [view, setView] = useState('list'); // list | create
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(false);
  const currency = useCurrency();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/ads/campaigns/mine`, { withCredentials: true });
      setCampaigns(data || []);
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur chargement'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (view === 'create') {
    return <CreateCampaignView onBack={() => setView('list')} onCreated={() => { setView('list'); load(); }} />;
  }

  // Aggregate stats
  const totals = campaigns.reduce((a, c) => ({
    spent: a.spent + parseFloat(c.spent_usd || 0),
    impressions: a.impressions + (c.impressions || 0),
    clicks: a.clicks + (c.clicks || 0),
  }), { spent: 0, impressions: 0, clicks: 0 });

  return (
    <div className="p-5 max-w-6xl mx-auto pb-24" data-testid="ads-user-page">
      <button onClick={onBack} className="jp-btn jp-btn-ghost jp-btn-sm mb-4" data-testid="ads-user-back">← Retour</button>

      {/* Hero */}
      <div className="relative overflow-hidden rounded-3xl p-6 mb-6"
        style={{ background: 'linear-gradient(135deg, #F59E0B 0%, #EC4899 55%, #8B5CF6 100%)', color: 'white' }}>
        <Megaphone size={140} weight="duotone" className="absolute -right-6 -top-6 opacity-25" />
        <div className="relative">
          <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-widest mb-2"
            style={{ background: 'rgba(255,255,255,0.22)' }}>
            JAPAP Publicité
          </div>
          <h1 className="font-['Outfit'] text-3xl sm:text-4xl font-black leading-tight">
            Atteignez 1000× plus d'utilisateurs
          </h1>
          <p className="text-sm opacity-90 mt-2 max-w-xl">
            Boostez vos posts, reels et produits. Paiement via wallet, modération sous 24h, stats en temps réel.
          </p>
          <button onClick={() => setView('create')} className="jp-btn mt-4 inline-flex items-center gap-1.5"
            style={{ background: 'white', color: '#EC4899' }} data-testid="new-campaign-btn">
            <Plus size={14} weight="bold" /> Nouvelle campagne
          </button>
        </div>
      </div>

      {/* Aggregate stats */}
      {campaigns.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6" data-testid="ads-aggregate">
          <Kpi label="Campagnes" value={campaigns.length} icon={<Rocket size={16} />} tone="violet" />
          <Kpi label="Dépensé" value={formatMoney(totals.spent, currency, { short: true })} icon={<Coins size={16} />} tone="emerald" />
          <Kpi label="Impressions" value={totals.impressions.toLocaleString('fr-FR')} icon={<Eye size={16} />} tone="sky" />
          <Kpi label="Clics" value={totals.clicks.toLocaleString('fr-FR')} icon={<TrendUp size={16} />} tone="pink" />
        </div>
      )}

      {/* Campaigns list */}
      <div className="space-y-3" data-testid="campaigns-list">
        {loading && <div className="jp-card p-6 text-center text-sm">Chargement…</div>}
        {!loading && campaigns.length === 0 && (
          <div className="jp-card p-10 text-center">
            <Megaphone size={40} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm mb-3" style={{ color: 'var(--jp-text-muted)' }}>
              Vous n'avez pas encore de campagne publicitaire.
            </p>
            <button onClick={() => setView('create')} className="jp-btn jp-btn-primary" data-testid="create-first-campaign">
              <Plus size={14} weight="bold" /> Lancer votre 1ère campagne
            </button>
          </div>
        )}
        {campaigns.map(c => (
          <div key={c.campaign_id} className="jp-card-elevated p-4" data-testid={`campaign-${c.campaign_id}`}>
            <div className="flex flex-col sm:flex-row sm:items-start gap-3">
              {c.image_url ? (
                <img src={c.image_url} alt={c.title || ''} className="w-20 h-20 rounded-xl object-cover" />
              ) : (
                <div className="w-20 h-20 rounded-xl flex items-center justify-center" style={{ background: 'var(--jp-surface-2)' }}>
                  <ImageIcon size={24} style={{ color: 'var(--jp-text-muted)' }} />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <strong className="font-['Outfit'] truncate">{c.title || '(sans titre)'}</strong>
                  <span className="jp-badge" style={{ background: STATUS_STYLE(t)[c.status]?.bg, color: STATUS_STYLE(t)[c.status]?.fg }}>
                    {STATUS_STYLE(t)[c.status]?.label || c.status}
                  </span>
                  <span className="jp-badge jp-badge-neutral text-[10px]">{TARGET_LABEL(t)[c.target_type] || c.target_type}</span>
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-xs font-['Manrope']">
                  <span><strong>{t('ads_user.budget')}</strong> ${c.budget_usd}</span>
                  <span style={{ color: '#EC4899' }}><strong>{t('ads_user.depense')}</strong> ${c.spent_usd}</span>
                  <span><strong>{t('ads_user.cpm')}</strong> ${c.cpm_usd}</span>
                </div>
                <div className="flex gap-3 mt-2 text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                  <span className="inline-flex items-center gap-1"><Eye size={10} /> {c.impressions} impressions</span>
                  <span className="inline-flex items-center gap-1"><TrendUp size={10} /> {c.clicks} clics</span>
                  {c.impressions > 0 && (
                    <span className="inline-flex items-center gap-1">
                      <ChartLineUp size={10} /> CTR {((c.clicks / c.impressions) * 100).toFixed(2)}%
                    </span>
                  )}
                </div>
                {/* Progress bar spent/budget */}
                <div className="mt-2 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--jp-surface-2)' }}>
                  <div className="h-full rounded-full transition-all" style={{
                    width: `${Math.min(100, (parseFloat(c.spent_usd) / parseFloat(c.budget_usd || 1)) * 100)}%`,
                    background: 'linear-gradient(90deg, #F59E0B, #EC4899)',
                  }} />
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Kpi({ label, value, icon, tone, testid }) {
  const tones = {
    violet:  { bg: 'linear-gradient(135deg, #F5F3FF, #DDD6FE)', fg: '#4C1D95' },
    emerald: { bg: 'linear-gradient(135deg, #ECFDF5, #A7F3D0)', fg: '#065F46' },
    sky:     { bg: 'linear-gradient(135deg, #EFF6FF, #BFDBFE)', fg: '#1E3A8A' },
    pink:    { bg: 'linear-gradient(135deg, #FDF2F8, #FBCFE8)', fg: '#831843' },
  };
  return (
    <div className="rounded-2xl p-3" style={{ background: tones[tone].bg, color: tones[tone].fg }} data-testid={testid}>
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-widest font-bold opacity-80">
        {icon} {label}
      </div>
      <div className="font-['Outfit'] text-xl font-black mt-1">{value}</div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// CREATE CAMPAIGN
// ═══════════════════════════════════════════════════════════════════════════
function CreateCampaignView({ onBack, onCreated }) {
  const [form, setForm] = useState({
    target_type: 'post',
    target_id: '',
    title: '',
    image_url: '',
    cta_url: '',
    budget_usd: '5',
    cpm_usd: '1',
    country_code: '',
    days: '7',
  });
  const [myPosts, setMyPosts] = useState([]);
  const [myReels, setMyReels] = useState([]);
  const [myProducts, setMyProducts] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState('');
  const currency = useCurrency();

  // load user's targetable content
  // iter165 — fixed URLs and response shape:
  //   - /api/feed/my-posts and /api/feed/reels/my both return {items, total}
  //   - reels endpoint was previously hitting /api/reels/my (404)
  useEffect(() => {
    if (form.target_type === 'post') {
      axios.get(`${API}/api/feed/my-posts?limit=30`, { withCredentials: true })
        .then(({ data }) => setMyPosts(data.items || data.posts || data || []))
        .catch(() => setMyPosts([]));
    } else if (form.target_type === 'reel') {
      axios.get(`${API}/api/feed/reels/my?limit=30`, { withCredentials: true })
        .then(({ data }) => setMyReels(data.items || data.reels || data || []))
        .catch(() => setMyReels([]));
    } else if (form.target_type === 'product') {
      axios.get(`${API}/api/marketplace/products?limit=30&sort=recent`, { withCredentials: true })
        .then(({ data }) => setMyProducts((data.products || []).filter(p => p.seller_id))).catch(() => {});
    }
  }, [form.target_type]);

  // Apply recommended defaults
  const applyPreset = (tt) => {
    const preset = PLAN_PRICES[tt];
    setForm(f => ({ ...f, target_type: tt, target_id: '',
      cpm_usd: String(preset.cpm), budget_usd: String(preset.recommended_budget) }));
  };

  const budget = parseFloat(form.budget_usd) || 0;
  const cpm = parseFloat(form.cpm_usd) || 0;
  const estImpressions = cpm > 0 ? Math.floor((budget / cpm) * 1000) : 0;

  const submit = async (e) => {
    e.preventDefault(); setErr(''); setSubmitting(true);
    try {
      if (form.target_type !== 'banner' && !form.target_id) {
        throw new Error('Sélectionnez un contenu à promouvoir.');
      }
      if (budget <= 0) throw new Error('Budget > 0 requis');
      const { data } = await axios.post(`${API}/api/ads/campaigns`, {
        target_type: form.target_type,
        target_id: form.target_id || null,
        title: form.title,
        image_url: form.image_url,
        cta_url: form.cta_url,
        budget_usd: budget,
        cpm_usd: cpm,
        country_code: form.country_code,
        days: parseInt(form.days) || 7,
      }, { withCredentials: true });
      toast.success(`Campagne créée — débit ${data.local_cost} ${data.currency}. En attente d'approbation.`);
      onCreated();
    } catch (e2) {
      const msg = e2.response?.data?.detail || e2.message || 'Erreur';
      setErr(msg); toast.error(msg);
    } finally { setSubmitting(false); }
  };

  return (
    <div className="p-5 max-w-3xl mx-auto pb-24" data-testid="create-campaign-view">
      <button onClick={onBack} className="jp-btn jp-btn-ghost jp-btn-sm mb-4" data-testid="create-back">← Retour</button>
      <h1 className="font-['Outfit'] text-2xl font-black mb-1" style={{ color: 'var(--jp-text)' }}>Nouvelle campagne</h1>
      <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
        Choisissez une cible, fixez un budget et envoyez pour approbation.
      </p>

      <form onSubmit={submit} className="space-y-5">
        {/* 1. Target type */}
        <div className="jp-card p-4">
          <label className="jp-label">1. Type de publicité</label>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-2">
            {['post', 'reel', 'product', 'banner'].map(tt => (
              <button key={tt} type="button" onClick={() => applyPreset(tt)}
                className={`rounded-xl p-3 text-sm font-bold border-2 transition-all ${form.target_type === tt ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
                data-testid={`target-type-${tt}`}
                style={{ borderColor: form.target_type === tt ? 'var(--jp-primary)' : 'var(--jp-border)' }}>
                {TARGET_LABEL(t)[tt]}
                <div className="text-[10px] font-normal opacity-80 mt-0.5">CPM ${PLAN_PRICES[tt].cpm}</div>
              </button>
            ))}
          </div>
        </div>

        {/* 2. Target picker (hidden for banner) */}
        {form.target_type !== 'banner' && (
          <div className="jp-card p-4">
            <label className="jp-label">2. Contenu à promouvoir</label>
            {/* iter165 — Rich grid picker with thumbnails + empty-state.
                Replaces the plain <select> which silently showed nothing
                when /my-posts and /reels/my returned 404 (endpoints were
                missing) — the user reported "Choisir un post n'affiche
                rien". Now we display previews and a clear empty hint. */}
            {form.target_type === 'post' && (
              <PostPickerGrid
                items={myPosts}
                selected={form.target_id}
                onSelect={(id) => setForm(f => ({ ...f, target_id: id }))}
                kind="post"
              />
            )}
            {form.target_type === 'reel' && (
              <PostPickerGrid
                items={myReels}
                selected={form.target_id}
                onSelect={(id) => setForm(f => ({ ...f, target_id: id }))}
                kind="reel"
              />
            )}
            {form.target_type === 'product' && (
              <select value={form.target_id} onChange={e => setForm(f => ({ ...f, target_id: e.target.value }))}
                className="jp-input text-sm" required data-testid="target-product-select">
                <option value="">— Choisir un de vos produits —</option>
                {myProducts.map(p => (
                  <option key={p.product_id} value={p.product_id}>{p.title} (${p.price})</option>
                ))}
              </select>
            )}
            <p className="text-[10px] mt-1.5" style={{ color: 'var(--jp-text-muted)' }}>
              <Info size={10} className="inline" /> Seuls vos contenus récents apparaissent.
            </p>
          </div>
        )}

        {/* 3. Creative (banner fields) */}
        {form.target_type === 'banner' && (
          <div className="jp-card p-4 space-y-3">
            <label className="jp-label">2. Créa bannière</label>
            <input value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              placeholder="Titre accrocheur (max 200)" className="jp-input text-sm" maxLength={200}
              data-testid="banner-title" />
            <input value={form.image_url} onChange={e => setForm(f => ({ ...f, image_url: e.target.value }))}
              placeholder="URL de l'image" className="jp-input text-sm" data-testid="banner-image" />
            <input value={form.cta_url} onChange={e => setForm(f => ({ ...f, cta_url: e.target.value }))}
              placeholder="URL de redirection (CTA)" className="jp-input text-sm" data-testid="banner-cta" />
          </div>
        )}

        {/* 4. Budget + CPM + Country + Days */}
        <div className="jp-card p-4 space-y-4">
          <label className="jp-label">3. Budget et ciblage</label>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-muted)' }}>Budget (USD)</label>
              <input type="number" min="1" step="0.5" value={form.budget_usd}
                onChange={e => setForm(f => ({ ...f, budget_usd: e.target.value }))}
                className="jp-input text-sm" data-testid="budget-usd" required />
            </div>
            <div>
              <label className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-muted)' }}>CPM (coût / 1000 vues)</label>
              <input type="number" min="0.5" step="0.5" value={form.cpm_usd}
                onChange={e => setForm(f => ({ ...f, cpm_usd: e.target.value }))}
                className="jp-input text-sm" data-testid="cpm-usd" required />
            </div>
            <div>
              <label className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-muted)' }}>Pays cible (ISO2)</label>
              <input type="text" maxLength={2} value={form.country_code}
                onChange={e => setForm(f => ({ ...f, country_code: e.target.value.toUpperCase() }))}
                placeholder="Toutes" className="jp-input text-sm uppercase" data-testid="country-code" />
            </div>
            <div>
              <label className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-muted)' }}>Durée (jours)</label>
              <input type="number" min="1" max="60" value={form.days}
                onChange={e => setForm(f => ({ ...f, days: e.target.value }))}
                className="jp-input text-sm" data-testid="days" required />
            </div>
          </div>
        </div>

        {/* 5. Preview */}
        <div className="rounded-2xl p-4"
          style={{ background: 'linear-gradient(135deg, #ECFDF5, #A7F3D0)', color: '#065F46' }}
          data-testid="campaign-preview">
          <div className="text-[10px] font-bold uppercase tracking-widest mb-1">Estimation</div>
          <div className="font-['Outfit'] text-2xl font-black">
            ~{estImpressions.toLocaleString('fr-FR')} impressions
          </div>
          <p className="text-xs mt-1 opacity-80">
            Coût local estimé : <strong>{formatMoney(budget, currency, { short: true })}</strong> ·
            Durée : <strong>{form.days} jours</strong> ·
            Débit immédiat, non remboursable si refusé (à recréditer manuellement par l'admin).
          </p>
        </div>

        {err && <div className="jp-alert jp-alert-error text-sm" data-testid="create-error">{err}</div>}

        {/* iter165 — sticky bottom CTA on mobile so the user always knows
            where to click after editing the form. Falls back to inline
            placement on ≥sm screens (640+px). Includes safe-area-inset
            padding to clear the iOS home indicator and Android nav bar. */}
        <div
          className="sm:static sm:bg-transparent sm:p-0 sm:shadow-none sticky bottom-0 left-0 right-0 -mx-5 px-5 py-3 sm:mx-0"
          style={{
            background: 'var(--jp-bg, white)',
            borderTop: '1px solid var(--jp-border)',
            paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom, 0px))',
            zIndex: 5,
          }}
        >
          <button type="submit" disabled={submitting}
            className="jp-btn jp-btn-primary jp-btn-lg jp-btn-full" data-testid="submit-campaign">
            {submitting ? 'Envoi…' : (<><Rocket size={16} weight="fill" /> Lancer la campagne</>)}
          </button>
        </div>
      </form>
    </div>
  );
}



/**
 * iter165 — Visual grid picker for posts/reels in the Ads sponsor flow.
 * Each tile shows a thumbnail (when media exists), the post text snippet,
 * and the relative date. Click → selects (single selection, radio-style).
 * Empty-state explains clearly that the user hasn't published anything yet
 * and offers a deep link to the feed.
 */
function PostPickerGrid({ items, selected, onSelect, kind }) {
  const { t } = useTranslation();
  if (!items || items.length === 0) {
    return (
      <div
        className="rounded-xl border-2 border-dashed p-6 text-center"
        style={{ borderColor: 'var(--jp-border)' }}
        data-testid={`target-${kind}-empty`}
      >
        <p className="text-sm font-['Manrope'] font-semibold mb-1" style={{ color: 'var(--jp-text)' }}>
          {kind === 'reel' ? t('ads_user.aucun_reel_a_promouvoir') : t('ads_user.aucun_post_a_promouvoir')}
        </p>
        <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
          Publie d'abord du contenu sur ton fil avant de lancer une campagne.
        </p>
        <a
          href={kind === 'reel' ? '/reels' : '/feed'}
          className="inline-block mt-3 text-xs underline"
          style={{ color: 'var(--jp-primary)' }}
        >
          Aller au {kind === 'reel' ? 'feed Reels' : 'fil'} →
        </a>
      </div>
    );
  }

  const fmtDate = (iso) => {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const days = Math.round((Date.now() - d.getTime()) / 86400000);
      if (days < 1) return "aujourd'hui";
      if (days === 1) return 'hier';
      if (days < 7) return `il y a ${days} j`;
      return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' });
    } catch { return ''; }
  };

  return (
    <div
      className="grid grid-cols-2 sm:grid-cols-3 gap-2 mt-2 max-h-[480px] overflow-y-auto pr-1"
      data-testid={`target-${kind}-grid`}
    >
      {items.map((it) => {
        const id = kind === 'reel' ? it.reel_id : it.post_id;
        const text = kind === 'reel' ? it.caption : (it.text || '');
        const thumb = kind === 'reel'
          ? (it.thumbnail_url || it.video_url)
          : (Array.isArray(it.media) && it.media[0]) || null;
        const isSelected = selected === id;
        return (
          <button
            key={id}
            type="button"
            onClick={() => onSelect(id)}
            data-testid={`target-${kind}-tile-${id}`}
            data-selected={isSelected}
            className="text-left rounded-xl overflow-hidden transition-all"
            style={{
              border: isSelected ? '2px solid var(--jp-primary)' : '1px solid var(--jp-border)',
              boxShadow: isSelected ? '0 2px 12px rgba(15, 5, 107, 0.18)' : 'none',
              background: 'white',
            }}
          >
            <div
              className="relative w-full aspect-square bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center"
            >
              {thumb ? (
                kind === 'reel' && !it.thumbnail_url ? (
                  <video src={thumb} className="w-full h-full object-cover" muted />
                ) : (
                  <img src={thumb} alt="" className="w-full h-full object-cover" />
                )
              ) : (
                <span className="text-2xl">📝</span>
              )}
              {isSelected && (
                <span
                  className="absolute top-1.5 right-1.5 w-5 h-5 rounded-full text-white text-[10px] font-bold flex items-center justify-center"
                  style={{ background: 'var(--jp-primary)' }}
                >
                  ✓
                </span>
              )}
            </div>
            <div className="p-2">
              <div className="flex items-center gap-1 mb-0.5">
                {/* iter166 — performance badges. Helps users pick their
                    best-performing content, which converts ~3x better
                    after sponsorisation according to ad-platform data. */}
                {kind === 'post' && (it.likes_count || 0) >= 10 && (
                  <span title="Post engageant" data-testid={`perf-badge-fire-${id}`}>🔥</span>
                )}
                {kind === 'reel' && (it.views_count || 0) >= 100 && (
                  <span title="Reel viral" data-testid={`perf-badge-star-${id}`}>🌟</span>
                )}
              </div>
              <p
                className="text-[11px] font-['Manrope'] font-medium leading-tight line-clamp-2"
                style={{ color: 'var(--jp-text)' }}
              >
                {text ? text.slice(0, 80) : <span className="italic" style={{ color: 'var(--jp-text-muted)' }}>(sans texte)</span>}
              </p>
              <p
                className="text-[9px] font-['Manrope'] mt-1"
                style={{ color: 'var(--jp-text-muted)' }}
              >
                {fmtDate(it.created_at)}
                {kind === 'reel' && it.views_count > 0 && ` · ${it.views_count} vues`}
                {kind === 'post' && it.likes_count > 0 && ` · ${it.likes_count} ❤`}
              </p>
            </div>
          </button>
        );
      })}
    </div>
  );
}
