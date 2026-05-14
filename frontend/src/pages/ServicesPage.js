import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/context/AuthContext';
import { useTranslation } from 'react-i18next';
import { useNavigate, Link } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { Storefront, Taxi, Hamburger, Briefcase, Handshake, CurrencyBtc, Megaphone, GameController, CalendarBlank, Tag, ShoppingBag, TrendUp, Lock, Plus, MagnifyingGlass, Eye, X, ShoppingCart } from '@phosphor-icons/react';
import JapapStakingApp from '@/CryptoStakingApp';
import CrowdfundingModule from '@/pages/CrowdfundingModule';
import GamesModule from '@/pages/GamesModule';
import JobsModule from '@/pages/JobsModule';
import KycVerifiedBadge from '@/components/KycVerifiedBadge';
import UserNameLink from '@/components/common/UserNameLink';
import TransportModule from '@/pages/TransportModule';
import AdsUserPage from '@/pages/AdsUserPage';
import AdSlot from '@/components/AdSlot';
import { useCurrency, formatMoney } from '@/utils/currency';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ServicesPage() {
  const { user } = useAuth();
  const { t } = useTranslation();
  // iter189-currency-fix — USD is the canonical price storage; this context
  // provides live conversion to the user's display currency for the cards.
  const currencyCtx = useCurrency();
  // "Coming soon" services — labels pulled from i18n so the card stays
  // localised as the catalogue grows.
  const SERVICE_MODULES = [
    { icon: Hamburger,     label: t('services.soon_food_title'),   desc: t('services.soon_food_desc'),   available: false },
    { icon: CalendarBlank, label: t('services.soon_events_title'), desc: t('services.soon_events_desc'), available: false },
    { icon: Storefront,    label: t('services.soon_ecom_title'),   desc: t('services.soon_ecom_desc'),   available: false },
  ];
  const [view, setView] = useState('hub');

  // iter142E: support ?view=crowdfunding for direct deep-linking from
  // Universal Links (e.g. /crowdfunding redirects here).
  useEffect(() => {
    try {
      const sp = new URLSearchParams(window.location.search);
      const v = sp.get('view');
      if (v && ['crowdfunding', 'marketplace', 'crypto', 'games', 'jobs', 'transport', 'ads'].includes(v)) {
        setView(v);
      }
    } catch {}
  }, []);
  const [flags, setFlags] = useState({
    crypto_enabled: true, ads_enabled: false, offers_enabled: false, transport_enabled: true,
    // iter237x — admin-editable badges per module (empty string = no badge).
    badges: { ads: '', offers: 'Nouveau', jobs: '', crypto: '', transport: 'Active' },
  });
  const [products, setProducts] = useState([]);
  const [sponsoredSlots, setSponsoredSlots] = useState([]);  // iter180 — Ads slots injected in feed
  const [totalProducts, setTotalProducts] = useState(0);
  const [featured, setFeatured] = useState([]);   // iter175 — Produits Vedettes
  const [categories, setCategories] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('');
  const [showCreateProduct, setShowCreateProduct] = useState(false);
  const [productForm, setProductForm] = useState({ title: '', description: '', price: '', category: 'general', condition: 'new', location: '' });
  // iter174 — Marketplace product images (1-5 max).
  const [productImages, setProductImages] = useState([]); // [{thumb, full, hd}, ...]
  const [aiModalOpen, setAiModalOpen] = useState(false);  // iter181 — AI modal
  const [uploadingImg, setUploadingImg] = useState(false);
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [orders, setOrders] = useState([]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    axios.get(`${API}/api/settings/public`, { withCredentials: true })
      .then(({ data }) => setFlags({
        crypto_enabled: (data.crypto_enabled ?? 'true') === 'true',
        ads_enabled: (data.ads_enabled ?? 'false') === 'true',
        offers_enabled: (data.offers_enabled ?? 'false') === 'true',
        // iter237w — admin can hide the whole Transport hub from end users
        // without removing any code. Defaults to true so historical
        // behaviour is preserved when the setting is missing.
        transport_enabled: (data.transport_enabled ?? 'true') === 'true',
        // iter237x — admin-editable badges per module.
        badges: {
          ads: data.module_ads_badge ?? '',
          offers: data.module_offers_badge ?? 'Nouveau',
          jobs: data.module_jobs_badge ?? '',
          crypto: data.module_crypto_badge ?? '',
          transport: data.module_transport_badge ?? 'Active',
        },
      }))
      .catch(() => {});
  }, []);

  const [sortBy, setSortBy] = useState('smart');
  const [priceRange, setPriceRange] = useState({ min: '', max: '' });

  const loadProducts = useCallback(async () => {
    try {
      const qs = new URLSearchParams({ page: 1, limit: 30, sort: sortBy });
      if (selectedCategory) qs.set('category', selectedCategory);
      if (searchQuery) qs.set('search', searchQuery);
      if (priceRange.min) qs.set('price_min', priceRange.min);
      if (priceRange.max) qs.set('price_max', priceRange.max);
      if (verifiedOnly) qs.set('verified_only', 'true');
      const { data } = await axios.get(`${API}/api/marketplace/products?${qs.toString()}`, { withCredentials: true });
      setProducts(data.products);
      setTotalProducts(data.total);
      setSponsoredSlots(data.sponsored_slots || []);
      // iter180 — Fire impression log for rendered sponsored slots
      const ids = (data.sponsored_slots || []).map(s => s.campaign_id).filter(Boolean);
      if (ids.length) {
        axios.post(`${API}/api/ads_console/impress`, { campaign_ids: ids },
          { withCredentials: true, headers: { 'X-Requested-With': 'XMLHttpRequest' } })
          .catch(() => {});
      }
    } catch {}
  }, [selectedCategory, searchQuery, sortBy, priceRange, verifiedOnly]);

  const loadCategories = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/marketplace/categories`, { withCredentials: true });
      setCategories(data);
    } catch {}
  }, []);

  // iter175 — Produits Vedettes (homepage_30d boost)
  const loadFeatured = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/marketplace/featured?limit=8`, { withCredentials: true });
      setFeatured(data.items || []);
    } catch { setFeatured([]); }
  }, []);

  const loadOrders = useCallback(async () => {
    try {
      const [bought, sold] = await Promise.all([
        axios.get(`${API}/api/marketplace/orders?role=buyer`, { withCredentials: true }),
        axios.get(`${API}/api/marketplace/orders?role=seller`, { withCredentials: true }),
      ]);
      setOrders([...bought.data.map(o => ({ ...o, role: 'buyer' })), ...sold.data.map(o => ({ ...o, role: 'seller' }))]);
    } catch {}
  }, []);

  useEffect(() => { if (view === 'marketplace') { loadProducts(); loadCategories(); loadOrders(); loadFeatured(); } }, [view, loadProducts, loadCategories, loadOrders, loadFeatured]);

  const handleCreateProduct = async (e) => {
    e.preventDefault();
    setError(''); setMessage('');
    if (productImages.length === 0) {
      setError("Au moins 1 image produit est obligatoire.");
      return;
    }
    try {
      await axios.post(`${API}/api/marketplace/products`, {
        ...productForm, price: parseFloat(productForm.price),
        images: productImages,
      }, { withCredentials: true });
      setMessage('Produit cree avec succes!');
      setShowCreateProduct(false);
      setProductForm({ title: '', description: '', price: '', category: 'general', condition: 'new', location: '' });
      setProductImages([]);
      loadProducts();
    } catch (err) { setError(err.response?.data?.detail || 'Erreur'); }
  };

  const onUploadProductImages = async (filesList) => {
    const incoming = Array.from(filesList || []);
    if (!incoming.length) return;
    const remaining = 5 - productImages.length;
    if (remaining <= 0) {
      setError('Maximum 5 images par produit.');
      return;
    }
    const fd = new FormData();
    incoming.slice(0, remaining).forEach((f) => fd.append('files', f));
    setUploadingImg(true); setError('');
    try {
      const { data } = await axios.post(
        `${API}/api/marketplace/products/upload-images`, fd,
        { withCredentials: true,
          headers: { 'Content-Type': 'multipart/form-data' } });
      setProductImages((prev) => [...prev, ...(data.images || [])]);
    } catch (err) {
      setError(err.response?.data?.detail || 'Upload échoué');
    } finally { setUploadingImg(false); }
  };

  const handleBuy = async (productId) => {
    setError(''); setMessage('');
    try {
      const { data } = await axios.post(`${API}/api/marketplace/orders`, { product_id: productId }, { withCredentials: true });
      setMessage(data.message);
      loadOrders();
    } catch (err) { setError(err.response?.data?.detail || 'Erreur'); }
  };

  // iter178 — Shadcn AlertDialog state for confirm + dispute (buyer-side)
  const [confirmOrderId, setConfirmOrderId] = useState(null);
  const [disputeOrderId, setDisputeOrderId] = useState(null);
  const [disputeReason, setDisputeReason] = useState('');

  const handleConfirmOrder = async (orderId) => {
    try {
      const { data } = await axios.put(`${API}/api/marketplace/orders/${orderId}/confirm`, {}, { withCredentials: true });
      setMessage(data.message || 'Commande confirmée');
      setConfirmOrderId(null);
      loadOrders();
    } catch (err) { setError(err.response?.data?.detail || 'Erreur'); }
  };

  // iter178 — Buyer opens a dispute via shadcn AlertDialog (textarea inside)
  const handleDisputeOrder = async (orderId) => {
    if (!disputeReason || disputeReason.trim().length < 10) {
      setError('Motif requis (min 10 caractères)');
      return;
    }
    try {
      const { data } = await axios.post(`${API}/api/marketplace/orders/${orderId}/dispute`,
        { reason: disputeReason.trim() }, { withCredentials: true });
      setMessage(data.message || '⚖️ Litige enregistré');
      setDisputeOrderId(null); setDisputeReason('');
      loadOrders();
    } catch (err) { setError(err.response?.data?.detail || 'Erreur'); }
  };

  // === HUB VIEW ===
  if (view === 'hub') {
    return (
      <div className="p-6 max-w-4xl mx-auto pb-24" data-testid="services-page">
        <div className="mb-8">
          <h1 className="font-['Outfit'] text-2xl font-bold" style={{ color: 'var(--jp-text)' }}>{t('services.hub_title')}</h1>
          <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{t('services.hub_subtitle')}</p>
        </div>

        {/* Active Services */}
        <div className="space-y-4 mb-6">
          {/* Marketplace */}
          <button onClick={() => setView('marketplace')} data-testid="service-marketplace"
            className="jp-card p-6 w-full text-left transition-all jp-card-hover"
            style={{ borderColor: 'var(--jp-primary)', borderWidth: '2px' }}>
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: 'var(--jp-primary-subtle)' }}>
                <ShoppingBag size={28} weight="duotone" style={{ color: 'var(--jp-primary)' }} />
              </div>
              <div className="flex-1">
                <p className="text-lg font-bold font-['Outfit']" style={{ color: 'var(--jp-text)' }}>{t('services.marketplace_title')}</p>
                <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{t('services.marketplace_desc')}</p>
              </div>
              <span className="jp-badge jp-badge-success">{t('services.badge_active')}</span>
            </div>
          </button>

          {/* JAPAP Staking — admin-gated (unique point d'entrée, depuis iter68) */}
          {flags.crypto_enabled && (
            <button onClick={() => setView('crypto')} data-testid="service-staking"
              className="jp-card p-6 w-full text-left transition-all jp-card-hover"
              style={{ borderColor: '#F7931A', borderWidth: '2px' }}>
              <div className="flex items-center gap-4">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: 'rgba(247,147,26,0.1)' }}>
                  <CurrencyBtc size={28} weight="duotone" style={{ color: '#F7931A' }} />
                </div>
                <div className="flex-1">
                  <p className="text-lg font-bold font-['Outfit']" style={{ color: 'var(--jp-text)' }}>{t('services.staking_title')}</p>
                  <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{t('services.staking_desc')}</p>
                </div>
                <span className="jp-badge jp-badge-success">{t('services.badge_active')}</span>
              </div>
            </button>
          )}

          {/* Crowdfunding */}
          <button onClick={() => setView('crowdfunding')} data-testid="service-crowdfunding"
            className="jp-card p-6 w-full text-left transition-all jp-card-hover"
            style={{ borderColor: '#E01C2E', borderWidth: '2px' }}>
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: 'rgba(224,28,46,0.1)' }}>
                <Handshake size={28} weight="duotone" style={{ color: '#E01C2E' }} />
              </div>
              <div className="flex-1">
                <p className="text-lg font-bold font-['Outfit']" style={{ color: 'var(--jp-text)' }}>{t('services.crowdfunding_title')}</p>
                <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{t('services.crowdfunding_desc')}</p>
              </div>
              <span className="jp-badge jp-badge-success">{t('services.badge_active')}</span>
            </div>
          </button>

          {/* Games */}
          <button onClick={() => setView('games')} data-testid="service-games"
            className="jp-card p-6 w-full text-left transition-all jp-card-hover"
            style={{ borderColor: '#10B981', borderWidth: '2px' }}>
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: 'rgba(16,185,129,0.1)' }}>
                <GameController size={28} weight="duotone" style={{ color: '#10B981' }} />
              </div>
              <div className="flex-1">
                <p className="text-lg font-bold font-['Outfit']" style={{ color: 'var(--jp-text)' }}>{t('services.games_title')}</p>
                <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{t('services.games_desc')}</p>
              </div>
              <span className="jp-badge jp-badge-success">{t('services.badge_active')}</span>
            </div>
          </button>

          {/* Jobs */}
          <button onClick={() => setView('jobs')} data-testid="service-jobs"
            className="jp-card p-6 w-full text-left transition-all jp-card-hover"
            style={{ borderColor: '#4A90E2', borderWidth: '2px' }}>
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: 'rgba(74,144,226,0.1)' }}>
                <Briefcase size={28} weight="duotone" style={{ color: '#4A90E2' }} />
              </div>
              <div className="flex-1">
                <p className="text-lg font-bold font-['Outfit']" style={{ color: 'var(--jp-text)' }}>{t('services.jobs_title')}</p>
                <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{t('services.jobs_desc')}</p>
              </div>
              <span className="jp-badge jp-badge-success">{t('services.badge_active')}</span>
            </div>
          </button>

          {/* Transport — admin-gated (iter237w) */}
          {flags.transport_enabled && (
          <button onClick={() => setView('transport')} data-testid="service-transport"
            className="jp-card p-6 w-full text-left transition-all jp-card-hover"
            style={{ borderColor: '#F59E0B', borderWidth: '2px' }}>
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: 'rgba(245,158,11,0.1)' }}>
                <Taxi size={28} weight="duotone" style={{ color: '#F59E0B' }} />
              </div>
              <div className="flex-1">
                <p className="text-lg font-bold font-['Outfit']" style={{ color: 'var(--jp-text)' }}>{t('services.transport_title')}</p>
                <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{t('services.transport_desc')}</p>
              </div>
              <span className="jp-badge jp-badge-success">{t('services.badge_active')}</span>
            </div>
          </button>
          )}

          {/* Publicité — new (admin-gated) */}
          {flags.ads_enabled && (
            <button onClick={() => setView('ads')} data-testid="service-ads"
              className="jp-card p-6 w-full text-left transition-all jp-card-hover"
              style={{ borderColor: '#EC4899', borderWidth: '2px' }}>
              <div className="flex items-center gap-4">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: 'rgba(236,72,153,0.12)' }}>
                  <Megaphone size={28} weight="duotone" style={{ color: '#EC4899' }} />
                </div>
                <div className="flex-1">
                  <p className="text-lg font-bold font-['Outfit']" style={{ color: 'var(--jp-text)' }}>{t('services.ads_title')}</p>
                  <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{t('services.ads_desc')}</p>
                </div>
                {flags.badges.ads && (
                  <span className="jp-badge" data-testid="ads-badge"
                        style={{ background: 'linear-gradient(135deg,#F59E0B,#EC4899)', color: 'white' }}>
                    {flags.badges.ads}
                  </span>
                )}
              </div>
            </button>
          )}

          {/* Offres (extension Jobs) */}
          {flags.offers_enabled && (
            <button onClick={() => setView('offers')} data-testid="service-offers"
              className="jp-card p-6 w-full text-left transition-all jp-card-hover"
              style={{ borderColor: '#8B5CF6', borderWidth: '2px' }}>
              <div className="flex items-center gap-4">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: 'rgba(139,92,246,0.12)' }}>
                  <Tag size={28} weight="duotone" style={{ color: '#8B5CF6' }} />
                </div>
                <div className="flex-1">
                  <p className="text-lg font-bold font-['Outfit']" style={{ color: 'var(--jp-text)' }}>{t('services.offers_label')}</p>
                  <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{t('services.offers_desc')}</p>
                </div>
                {flags.badges.offers && (
                  <span className="jp-badge" data-testid="offers-badge"
                        style={{ background: 'linear-gradient(135deg,#6366F1,#8B5CF6)', color: 'white' }}>
                    {flags.badges.offers}
                  </span>
                )}
              </div>
            </button>
          )}
        </div>

        {/* Other services grid */}
        <h3 className="font-['Outfit'] text-base font-bold mb-3" style={{ color: 'var(--jp-text-secondary)' }}>{t('services.coming_soon_header')}</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 jp-stagger">
          {SERVICE_MODULES.map((svc, i) => (
            <div key={i} className="jp-card p-4 text-center opacity-50" data-testid={`service-${svc.label.toLowerCase().replace(/\s/g,'-')}`}>
              <svc.icon size={24} weight="duotone" className="mx-auto mb-2" style={{ color: 'var(--jp-text-muted)' }} />
              <p className="text-xs font-semibold font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{svc.label}</p>
              <span className="text-[9px]" style={{ color: 'var(--jp-text-muted)' }}>{svc.desc}</span>
            </div>
          ))}
        </div>

        <div className="mt-8 p-6 rounded-2xl" style={{ background: 'linear-gradient(135deg, var(--jp-primary), var(--jp-primary-hover))' }}>
          <h3 className="font-['Outfit'] text-lg font-bold text-white mb-2">{t('services.new_services_arrive')}</h3>
          <p className="text-sm text-white/80 font-['Manrope']">{t('services.new_services_desc')}</p>
        </div>
      </div>
    );
  }

  // === JAPAP STAKING VIEW (legacy MIR staking module, JAPAP-wrapped, single entry point) ===
  if (view === 'crypto') {
    return (
      <div className="min-h-screen bg-[#0e0e12] text-white" data-testid="japap-staking-view">
        <div className="sticky top-0 z-20 bg-[#0e0e12]/90 backdrop-blur border-b border-white/5">
          <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-3">
            <button
              data-testid="staking-back-btn"
              onClick={() => setView('hub')}
              className="p-2 rounded-full hover:bg-white/5"
              aria-label={t('services.back')}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M19 12H5M12 19l-7-7 7-7" />
              </svg>
            </button>
            <div className="flex-1">
              <p className="text-xl font-bold font-['Outfit']">JAPAP Staking</p>
              <p className="text-xs text-white/40">Stakez vos MIR · Binance Smart Chain · Plans 3/6/12 mois</p>
            </div>
          </div>
        </div>
        <JapapStakingApp />
      </div>
    );
  }

  // === OFFRES VIEW — extension Jobs ===
  if (view === 'offers') {
    return <JobsModule onBack={() => setView('hub')} offersMode />;
  }

  // === PUBLICITÉ VIEW — création & suivi de campagnes ===
  if (view === 'ads') {
    return <AdsUserPage onBack={() => setView('hub')} />;
  }

  // === CROWDFUNDING VIEW ===
  if (view === 'crowdfunding') {
    return <CrowdfundingModule onBack={() => setView('hub')} />;
  }

  // === GAMES VIEW ===
  if (view === 'games') {
    return <GamesModule onBack={() => setView('hub')} />;
  }

  // === JOBS VIEW ===
  if (view === 'jobs') {
    return <JobsModule onBack={() => setView('hub')} />;
  }

  // === TRANSPORT VIEW ===
  // iter237w — guard against deeplink while the module is admin-disabled.
  if (view === 'transport' && flags.transport_enabled) {
    return <TransportModule onBack={() => setView('hub')} />;
  }

  // === MARKETPLACE VIEW ===
  return (
    <div className="p-6 max-w-6xl mx-auto pb-24" data-testid="marketplace-page">
      <div className="flex items-center justify-between mb-6">
        <div>
          <button onClick={() => setView('hub')} className="text-xs font-['Manrope'] mb-1 flex items-center gap-1"
            style={{ color: 'var(--jp-text-muted)' }} data-testid="back-to-services">
            &larr; Services
          </button>
          <h1 className="font-['Outfit'] text-2xl font-bold" style={{ color: 'var(--jp-text)' }}>{t('services.marketplace')}</h1>
        </div>
        <button onClick={() => setShowCreateProduct(!showCreateProduct)} data-testid="create-product-btn"
          className="jp-btn jp-btn-primary">
          <Plus size={16} /> {t('services.sell_cta')}
        </button>
      </div>

      {error && <div className="jp-alert jp-alert-error mb-4" data-testid="marketplace-error">{error}</div>}
      {message && <div className="jp-alert jp-alert-success mb-4" data-testid="marketplace-success">{message}</div>}

      {/* Create Product Form */}
      {showCreateProduct && (
        <div className="jp-card-elevated p-6 mb-6 jp-animate-scaleIn" data-testid="create-product-form">
          <div className="flex justify-between items-center mb-4">
            <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>{t('services.new_product')}</h3>
            <button onClick={() => setShowCreateProduct(false)} style={{ color: 'var(--jp-text-muted)' }}><X size={18} /></button>
          </div>
          <form onSubmit={handleCreateProduct} className="space-y-4">
            <div>
              <label className="jp-label">Titre</label>
              <input data-testid="product-title" value={productForm.title} onChange={e => setProductForm(f => ({...f, title: e.target.value}))}
                className="jp-input text-sm" placeholder="Nom du produit" required />
            </div>
            <div>
              <label className="jp-label">Description</label>
              <textarea value={productForm.description} onChange={e => setProductForm(f => ({...f, description: e.target.value}))}
                className="jp-input text-sm resize-none" style={{ height: '80px' }} placeholder="Decrivez votre produit..." />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="jp-label">Prix (USD)</label>
                <input data-testid="product-price" type="number" min="0.01" step="0.01" value={productForm.price}
                  onChange={e => setProductForm(f => ({...f, price: e.target.value}))}
                  className="jp-input text-sm" placeholder="0.00" required />
              </div>
              <div>
                <label className="jp-label">Categorie</label>
                <select value={productForm.category} onChange={e => setProductForm(f => ({...f, category: e.target.value}))}
                  className="jp-input text-sm">
                  {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="jp-label">Etat</label>
                <select value={productForm.condition} onChange={e => setProductForm(f => ({...f, condition: e.target.value}))}
                  className="jp-input text-sm">
                  <option value="new">Neuf</option>
                  <option value="like_new">{t('services.comme_neuf')}</option>
                  <option value="used">{t('services.occasion')}</option>
                </select>
              </div>
              <div>
                <label className="jp-label">Localisation</label>
                <input value={productForm.location} onChange={e => setProductForm(f => ({...f, location: e.target.value}))}
                  className="jp-input text-sm" placeholder="Ville, Pays" />
              </div>
            </div>

            {/* iter174 — Product images upload (1-5 max) */}
            <div data-testid="product-images-section">
              <label className="jp-label">{t('services.product_photos_label')}</label>
              <div className="border-2 border-dashed rounded-lg p-3"
                style={{ borderColor: 'var(--jp-border)' }}
                onDragOver={(e) => { e.preventDefault(); }}
                onDrop={(e) => {
                  e.preventDefault();
                  if (e.dataTransfer?.files) onUploadProductImages(e.dataTransfer.files);
                }}>
                <div className="flex flex-wrap gap-2 mb-2">
                  {productImages.map((img, i) => (
                    <div key={i} className="relative w-20 h-20 rounded-lg overflow-hidden"
                      data-testid={`product-image-preview-${i}`}>
                      <img src={`${API}${img.thumb}`} alt="" loading="lazy"
                        className="w-full h-full object-cover" />
                      <button type="button" onClick={() =>
                          setProductImages(prev => prev.filter((_, idx) => idx !== i))}
                        data-testid={`product-image-remove-${i}`}
                        className="absolute top-0.5 right-0.5 w-5 h-5 rounded-full text-white text-xs"
                        style={{ background: 'rgba(0,0,0,0.7)' }}>
                        ×
                      </button>
                    </div>
                  ))}
                  {productImages.length < 5 && (
                    <label className="w-20 h-20 rounded-lg border-2 border-dashed flex flex-col items-center justify-center cursor-pointer text-xs"
                      style={{ borderColor: 'var(--jp-primary)', color: 'var(--jp-primary)' }}
                      data-testid="product-image-add">
                      <Plus size={20} />
                      <span>{uploadingImg ? '...' : `+${5 - productImages.length}`}</span>
                      <input type="file" multiple accept="image/*" className="hidden"
                        onChange={(e) => onUploadProductImages(e.target.files)} />
                    </label>
                  )}
                </div>
                <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
                  {t('services.product_photos_help')}
                </p>
                {/* iter181 — AI image generation/enhancement button */}
                <button type="button" onClick={() => setAiModalOpen(true)}
                  data-testid="product-ai-image-btn"
                  className="jp-btn jp-btn-sm w-full mt-2 inline-flex items-center justify-center gap-1"
                  style={{ background: 'linear-gradient(135deg,#6366F1,#EC4899)', color: 'white' }}>
                  {t('services.ai_generate_or_enhance')}
                </button>
              </div>
            </div>

            <div className="flex gap-3">
              <button type="submit" data-testid="submit-product" className="jp-btn jp-btn-primary">{t('services.publish_product')}</button>
              <button type="button" onClick={() => setShowCreateProduct(false)} className="jp-btn jp-btn-ghost">{t('common.cancel')}</button>
            </div>
          </form>
        </div>
      )}

      {/* iter175 — Produits Vedettes (homepage boost) */}
      {featured.length > 0 && (
        <div className="mb-6" data-testid="marketplace-featured-section">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-['Outfit'] text-base font-bold flex items-center gap-2"
              style={{ color: 'var(--jp-text)' }}>
              <span style={{
                background: 'linear-gradient(135deg,#8B5CF6,#EC4899)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
              }}>🌟 Produits Vedettes</span>
              <span className="jp-badge"
                style={{ background: 'linear-gradient(135deg,#8B5CF6,#EC4899)', color: 'white' }}>
                {featured.length}
              </span>
            </h3>
            <span className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
              Boost Homepage 30j
            </span>
          </div>
          <div className="flex gap-3 overflow-x-auto pb-2 -mx-1 px-1 snap-x">
            {featured.map((prod) => {
              const imgs = Array.isArray(prod.images) ? prod.images : [];
              const first = imgs[0];
              const u = !first ? null : (typeof first === 'string' ? first : (first.thumb || first.full || first.hd));
              const url = u ? (u.startsWith('http') ? u : `${API}${u}`) : null;
              return (
                <Link key={prod.product_id}
                  to={`/marketplace/p/${prod.product_id}`}
                  className="shrink-0 w-44 jp-card overflow-hidden jp-card-hover relative no-underline snap-start"
                  style={{ color: 'inherit' }}
                  data-testid={`featured-product-${prod.product_id}`}>
                  <span className="absolute top-2 right-2 z-10 jp-badge"
                    style={{
                      background: 'linear-gradient(135deg,#8B5CF6,#EC4899)',
                      color: 'white',
                    }}>
                    🌟 Vedette
                  </span>
                  {url ? (
                    <img src={url} alt="" loading="lazy"
                      className="w-full h-32 object-cover"
                      onError={(e) => { e.currentTarget.style.display = 'none'; }} />
                  ) : (
                    <div className="h-32 flex items-center justify-center"
                      style={{ background: 'var(--jp-surface-secondary)' }}>
                      <ShoppingBag size={32} style={{ color: 'var(--jp-text-muted)', opacity: 0.3 }} />
                    </div>
                  )}
                  <div className="p-3">
                    <h4 className="font-['Outfit'] font-bold text-xs line-clamp-1"
                      style={{ color: 'var(--jp-text)' }}>{prod.title}</h4>
                    <p className="font-['Outfit'] text-base font-bold mt-1"
                      style={{ color: 'var(--jp-primary)' }}
                      data-testid="featured-product-price">
                      {formatMoney(prod.price, currencyCtx, { short: true })}
                    </p>
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      )}

      {/* Search & Filter */}
      <div className="flex gap-2 sm:gap-3 mb-4 flex-wrap" data-testid="marketplace-filters">
        <div className="relative flex-1 min-w-[180px]">
          <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2" size={16} style={{ color: 'var(--jp-text-muted)' }} />
          <input data-testid="marketplace-search" value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="jp-input text-sm" style={{ paddingLeft: '36px' }} placeholder="Rechercher un produit..." />
        </div>
        <select value={selectedCategory} onChange={e => setSelectedCategory(e.target.value)}
          className="jp-input text-sm" style={{ width: 'auto', minWidth: '140px' }}>
          <option value="">{t('services.toutes_categories')}</option>
          {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select value={sortBy} onChange={e => setSortBy(e.target.value)} data-testid="marketplace-sort"
          className="jp-input text-sm" style={{ width: 'auto', minWidth: '140px' }}>
          <option value="smart">{t('services.for_you_pro_boost')}</option>
          <option value="recent">{t('services.plus_recents')}</option>
          <option value="price_asc">{t('services.prix_croissant')}</option>
          <option value="price_desc">{t('services.prix_decroissant')}</option>
          <option value="top_rated">{t('services.mieux_notes')}</option>
        </select>
        <input type="number" min="0" placeholder={t('services.prix_min')} value={priceRange.min}
          onChange={e => setPriceRange(r => ({ ...r, min: e.target.value }))}
          className="jp-input text-sm" style={{ width: '110px' }} data-testid="marketplace-price-min" />
        <input type="number" min="0" placeholder={t('services.prix_max')} value={priceRange.max}
          onChange={e => setPriceRange(r => ({ ...r, max: e.target.value }))}
          className="jp-input text-sm" style={{ width: '110px' }} data-testid="marketplace-price-max" />
        <label className="inline-flex items-center gap-1.5 text-xs px-2 cursor-pointer select-none"
          data-testid="marketplace-verified-only-label"
          style={{ color: 'var(--jp-text)' }}>
          <input type="checkbox" checked={verifiedOnly}
            data-testid="marketplace-verified-only"
            onChange={(e) => setVerifiedOnly(e.target.checked)} />
          🛡 Vendeurs vérifiés
        </label>
      </div>

      {/* Products Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-8" data-testid="marketplace-grid">
        {products.map((prod, idx) => {
          // iter180 — Insert sponsored slot(s) whose position matches `idx+1`
          const slotsHere = sponsoredSlots.filter(s => s.position === idx + 1);
          return [
            idx === 6 ? <div key={`ad-${idx}`} className="md:col-span-2 lg:col-span-3"><AdSlot slot="marketplace" /></div> : null,
            ...slotsHere.map(slot => (
              <SponsoredProductCard key={`sp-${slot.campaign_id}`} slot={slot} />
            )),
          <Link key={prod.product_id} to={`/marketplace/p/${prod.product_id}`}
            className="jp-card overflow-hidden jp-card-hover relative block no-underline"
            style={{ color: 'inherit' }}
            data-testid={`product-${prod.product_id}`}>
            {prod.is_pro && (
              <span className="absolute top-2 left-2 z-10 jp-badge" data-testid={`product-pro-${prod.product_id}`}
                style={{ background: 'linear-gradient(135deg,#F7931A,#EC4899)', color: 'white' }}>
                ✨ Pro
              </span>
            )}
            {prod.is_boost_active && (
              <span className="absolute top-2 right-2 z-10 jp-badge"
                style={{ background: 'linear-gradient(135deg,#F59E0B,#F97316)', color: 'white' }}>
                🚀 Boost
              </span>
            )}
            {(() => {
              const imgs = Array.isArray(prod.images) ? prod.images : [];
              const first = imgs[0];
              const u = !first ? null : (typeof first === 'string' ? first : (first.thumb || first.full || first.hd));
              const url = u ? (u.startsWith('http') ? u : `${API}${u}`) : null;
              return url ? (
                <img src={url} alt="" loading="lazy"
                  className="w-full h-40 object-cover"
                  onError={(e) => { e.currentTarget.style.display='none'; }} />
              ) : (
                <div className="h-40 flex items-center justify-center" style={{ background: 'var(--jp-surface-secondary)' }}>
                  <ShoppingBag size={48} style={{ color: 'var(--jp-text-muted)', opacity: 0.3 }} />
                </div>
              );
            })()}
            <div className="p-4">
              <div className="flex justify-between items-start mb-2">
                <h4 className="font-['Outfit'] font-bold text-sm" style={{ color: 'var(--jp-text)' }}>{prod.title}</h4>
                <span className="jp-badge jp-badge-neutral text-[9px]">{prod.condition === 'new' ? 'Neuf' : prod.condition === 'like_new' ? 'Comme neuf' : 'Occasion'}</span>
              </div>
              <p className="text-xs font-['Manrope'] mb-3 line-clamp-2" style={{ color: 'var(--jp-text-secondary)' }}>{prod.description}</p>
              <div className="flex items-center justify-between">
                <p className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-primary)' }}
                  data-testid="mkt-card-price">
                  {formatMoney(prod.price, currencyCtx, { short: false })}
                </p>
                <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                  {parseInt(prod.rating_count) > 0 && (
                    <span className="inline-flex items-center gap-0.5" title={`${prod.rating_count} avis`}>
                      ⭐ {parseFloat(prod.rating_avg).toFixed(1)}
                    </span>
                  )}
                  <span className="inline-flex items-center gap-0.5"><Eye size={12} /> {prod.views_count}</span>
                </div>
              </div>
              <div className="flex items-center gap-2 mt-2 text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                <div className="jp-avatar jp-avatar-sm jp-avatar-primary" style={{ width: '20px', height: '20px', fontSize: '8px' }}>
                  {prod.first_name?.[0]}
                </div>
                <UserNameLink username={prod.username} userId={prod.seller_id}>{prod.first_name} {prod.last_name}</UserNameLink>
                <KycVerifiedBadge verified={prod.seller_kyc_verified} size="sm" iconOnly />
                {prod.location && <span> - {prod.location}</span>}
              </div>
            </div>
          </Link>,
        ];
        })}
        {products.length === 0 && (
          <div className="col-span-full jp-card p-12 text-center">
            <ShoppingBag size={40} className="mx-auto mb-3" style={{ color: 'var(--jp-text-muted)', opacity: 0.4 }} />
            <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>{t('services.no_product_yet')}</p>
          </div>
        )}
      </div>

      {/* Orders */}
      {orders.length > 0 && (
        <div className="jp-card" data-testid="orders-section">
          <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--jp-border)' }}>
            <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>{t('services.my_orders_escrow')}</h3>
            <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
              🔒 Paiement sécurisé JAPAP — fonds bloqués jusqu’à confirmation, libération automatique après le délai admin.
            </p>
          </div>
          {orders.map(o => {
            const escrow = o.escrow_status || (o.status === 'pending' ? 'held' : '');
            const STATUS = {
              held:     { label: '🔒 Fonds bloqués',      bg: '#FEF3C7', fg: '#92400E' },
              disputed: { label: '⚖️ Litige ouvert',       bg: '#FEE2E2', fg: '#991B1B' },
              released: { label: '✅ Payé au vendeur',     bg: '#D1FAE5', fg: '#065F46' },
              refunded: { label: '↩️ Remboursé',           bg: '#E0F2FE', fg: '#075985' },
              split:    { label: '⚖️ Résolu en partage',   bg: '#EDE9FE', fg: '#5B21B6' },
            };
            const meta = STATUS[escrow] || { label: o.status, bg: '#F3F4F6', fg: '#374151' };
            return (
              <div key={o.order_id}
                className="flex items-center gap-3 px-6 py-4 border-b flex-wrap"
                style={{ borderColor: 'rgba(228,228,231,0.5)' }}
                data-testid={`order-${o.order_id}`}>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium font-['Manrope'] truncate" style={{ color: 'var(--jp-text)' }}>{o.product_title}</p>
                  <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                    {o.role === 'buyer' ? 'Achat' : 'Vente'} · {new Date(o.created_at).toLocaleDateString('fr-FR')}
                    {o.auto_release_at && escrow === 'held' && (
                      <span> · Auto-release {new Date(o.auto_release_at).toLocaleDateString('fr-FR')}</span>
                    )}
                  </p>
                </div>
                <p className="font-['Outfit'] font-bold text-sm" style={{ color: 'var(--jp-primary)' }}>
                  {parseFloat(o.amount).toFixed(2)} USD
                </p>
                <span className="jp-badge"
                  style={{ background: meta.bg, color: meta.fg, fontSize: '10px' }}
                  data-testid={`order-escrow-${o.order_id}`}>
                  {meta.label}
                </span>
                {o.role === 'buyer' && escrow === 'held' && (
                  <>
                    <button onClick={() => setConfirmOrderId(o.order_id)}
                      className="jp-btn jp-btn-sm jp-btn-primary"
                      data-testid={`confirm-${o.order_id}`}>
                      ✅ Confirmer
                    </button>
                    <button onClick={() => { setDisputeOrderId(o.order_id); setDisputeReason(''); }}
                      className="jp-btn jp-btn-sm jp-btn-ghost"
                      data-testid={`dispute-${o.order_id}`}>
                      ⚖️ Litige
                    </button>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* iter178 — Buyer confirm-order AlertDialog */}
      <AlertDialog open={!!confirmOrderId} onOpenChange={(o) => !o && setConfirmOrderId(null)}>
        <AlertDialogContent data-testid="order-confirm-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>✅ Confirmer la réception</AlertDialogTitle>
            <AlertDialogDescription>
              Le paiement sera libéré au vendeur (montant net après commission JAPAP).
              Cette action est irréversible.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="order-confirm-cancel">Annuler</AlertDialogCancel>
            <AlertDialogAction
              data-testid="order-confirm-ok"
              onClick={() => confirmOrderId && handleConfirmOrder(confirmOrderId)}>
              ✅ Confirmer
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* iter178 — Buyer dispute AlertDialog (with textarea) */}
      <AlertDialog open={!!disputeOrderId} onOpenChange={(o) => { if (!o) { setDisputeOrderId(null); setDisputeReason(''); } }}>
        <AlertDialogContent data-testid="order-dispute-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>⚖️ Ouvrir un litige</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm">
                <div>
                  Décris ce qui ne va pas (min 10 caractères). Notre équipe va arbitrer
                  sous 48h ; le paiement reste bloqué pendant la résolution.
                </div>
                <textarea
                  value={disputeReason}
                  onChange={(e) => setDisputeReason(e.target.value)}
                  data-testid="order-dispute-reason"
                  rows={4}
                  placeholder={t('services.ex_le_produit_n_est_pas_conforme_a')}
                  className="w-full rounded-lg border p-2 text-sm font-['Manrope']"
                  style={{ borderColor: 'var(--jp-border)' }} />
                <div className="text-[11px]"
                  style={{ color: disputeReason.trim().length >= 10 ? '#10B981' : '#6B7280' }}>
                  {disputeReason.trim().length}/10 caractères minimum
                </div>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="order-dispute-cancel">Annuler</AlertDialogCancel>
            <AlertDialogAction
              data-testid="order-dispute-submit"
              disabled={disputeReason.trim().length < 10}
              onClick={(e) => {
                if (disputeReason.trim().length < 10) {
                  e.preventDefault();
                  return;
                }
                disputeOrderId && handleDisputeOrder(disputeOrderId);
              }}
              style={{ background: '#EF4444' }}>
              ⚖️ Ouvrir le litige
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* iter181 — AI Image Generation / Enhancement modal */}
      {aiModalOpen && (
        <AIImageModal
          onClose={() => setAiModalOpen(false)}
          onAccept={(img) => {
            setProductImages((prev) => [...prev, img].slice(0, 5));
            setAiModalOpen(false);
            toast.success("✨ Image IA ajoutée au produit");
          }}
        />
      )}
    </div>
  );
}


// iter180 — Sponsored Product Card (inline dans la grille Marketplace)
function SponsoredProductCard({ slot }) {
  const imgs = Array.isArray(slot.images) ? slot.images : [];
  const first = imgs[0];
  const u = !first ? null : (typeof first === 'string' ? first : (first.thumb || first.full || first.hd));
  const url = u ? (u.startsWith('http') ? u : `${API}${u}`) : null;
  const handleClick = () => {
    axios.post(`${API}/api/ads_console/click`, { campaign_id: slot.campaign_id },
      { withCredentials: true, headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .catch(() => {});
  };
  return (
    <Link to={`/marketplace/p/${slot.product_id}`}
      onClick={handleClick}
      data-testid={`marketplace-sponsored-${slot.campaign_id}`}
      className="jp-card overflow-hidden jp-card-hover relative block no-underline"
      style={{ color: 'inherit', border: '1px solid rgba(99,102,241,0.35)' }}>
      <span className="absolute top-2 left-2 z-10 jp-badge"
        data-testid={`marketplace-sponsored-label-${slot.campaign_id}`}
        style={{ background: 'linear-gradient(135deg,#6366F1,#EC4899)', color: 'white' }}>
        📣 {slot.ad_label || 'Sponsorisé'}
      </span>
      {url ? (
        <img src={url} alt="" loading="lazy" className="w-full h-40 object-cover"
          onError={(e) => { e.currentTarget.style.display = 'none'; }} />
      ) : (
        <div className="h-40 flex items-center justify-center" style={{ background: 'var(--jp-surface-secondary)' }}>
          <ShoppingBag size={48} style={{ color: 'var(--jp-text-muted)', opacity: 0.3 }} />
        </div>
      )}
      <div className="p-4">
        <h4 className="font-['Outfit'] font-bold text-sm mb-1" style={{ color: 'var(--jp-text)' }}>
          {slot.title || 'Produit sponsorisé'}
        </h4>
        <p className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-primary)' }}>
          {parseFloat(slot.price || 0).toLocaleString('fr-FR')}{' '}
          <span className="text-xs">{slot.currency || 'USD'}</span>
        </p>
      </div>
    </Link>
  );
}



// iter181 — AI Image Generation / Enhancement modal (Nano Banana)
function AIImageModal({ onClose, onAccept }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState('autophoto'); // autophoto | generate | enhance | bgswap
  const [quota, setQuota] = useState(null);
  const [loading, setLoading] = useState(false);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [previewPayload, setPreviewPayload] = useState(null);
  const [autoVariations, setAutoVariations] = useState(null);  // [{preset, image, ok}]
  const [autoSelected, setAutoSelected] = useState({});       // {preset: bool}
  const [prompt, setPrompt] = useState('');
  const [instruction, setInstruction] = useState('');
  const [bgPreset, setBgPreset] = useState('');
  const [customScene, setCustomScene] = useState('');
  const [file, setFile] = useState(null);

  const reload = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/marketplace/ai-image/quota`,
        { withCredentials: true });
      setQuota(data);
      if (!bgPreset && Array.isArray(data.bg_presets) && data.bg_presets[0]) {
        setBgPreset(data.bg_presets[0]);
      }
    } catch (_) {
      setQuota({ enabled: false, remaining: 0, quota: 0, used_today: 0, bg_presets: [] });
    }
  }, [bgPreset]);

  useEffect(() => { reload(); }, [reload]);

  const remaining = quota?.remaining ?? 0;
  const enabled = quota?.enabled !== false;

  const runGenerate = async () => {
    if ((prompt || '').trim().length < 5) {
      toast.error("Décris ton produit (min 5 caractères)");
      return;
    }
    setLoading(true);
    try {
      const { data } = await axios.post(
        `${API}/api/marketplace/ai-image/generate`,
        { prompt },
        { withCredentials: true,
          headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      setPreviewPayload(data.image);
      setPreviewUrl(`${API}${data.image.full}`);
      setQuota(data.quota);
      toast.success("✨ Image générée");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Échec de la génération IA");
    } finally {
      setLoading(false);
    }
  };

  const runEnhance = async () => {
    if (!file) { toast.error("Sélectionne une image"); return; }
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('instruction', instruction || '');
      const { data } = await axios.post(
        `${API}/api/marketplace/ai-image/enhance`, fd,
        { withCredentials: true,
          headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      setPreviewPayload(data.image);
      setPreviewUrl(`${API}${data.image.full}`);
      setQuota(data.quota);
      toast.success("✨ Image améliorée");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Échec de l'amélioration IA");
    } finally {
      setLoading(false);
    }
  };

  const runBgSwap = async () => {
    if (!file) { toast.error("Sélectionne une image"); return; }
    if (!bgPreset && !customScene.trim()) {
      toast.error("Choisis un preset ou décris la scène"); return;
    }
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('preset', bgPreset || '');
      fd.append('custom_scene', customScene || '');
      const { data } = await axios.post(
        `${API}/api/marketplace/ai-image/bg-swap`, fd,
        { withCredentials: true,
          headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      setPreviewPayload(data.image);
      setPreviewUrl(`${API}${data.image.full}`);
      setQuota(data.quota);
      toast.success("✨ Arrière-plan remplacé");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Échec du background swap");
    } finally {
      setLoading(false);
    }
  };

  const runAutoPhoto = async () => {
    if (!file) { toast.error("Sélectionne une image source"); return; }
    setLoading(true);
    setAutoVariations(null); setAutoSelected({});
    try {
      const fd = new FormData(); fd.append('file', file);
      const { data } = await axios.post(
        `${API}/api/marketplace/ai-image/auto-photo`, fd,
        { withCredentials: true,
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          timeout: 120000 });
      setAutoVariations(data.variations || []);
      setQuota(data.quota);
      const successes = (data.variations || []).filter(v => v.ok).length;
      if (successes > 0) {
        // Pre-select all successful variations
        const sel = {};
        (data.variations || []).forEach(v => { if (v.ok) sel[v.preset] = true; });
        setAutoSelected(sel);
        toast.success(`✨ ${successes} variations générées`);
      } else {
        toast.error("Aucune variation n'a abouti — réessaie avec une image plus nette.");
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "Échec auto-photographie");
    } finally {
      setLoading(false);
    }
  };

  const acceptAutoPhotos = () => {
    if (!autoVariations) return;
    const picked = autoVariations.filter(v => v.ok && autoSelected[v.preset]);
    if (picked.length === 0) { toast.error("Sélectionne au moins une image"); return; }
    picked.forEach(v => onAccept(v.image));
    toast.success(`✅ ${picked.length} image(s) ajoutée(s) au produit`);
  };

  const TabBtn = ({ id, label, emoji }) => (
    <button onClick={() => {
        setTab(id); setPreviewUrl(null); setPreviewPayload(null);
        setAutoVariations(null); setAutoSelected({});
      }}
      data-testid={`ai-tab-${id}`}
      className="jp-btn jp-btn-sm flex-1"
      style={{
        background: tab === id ? 'var(--jp-primary)' : 'transparent',
        color: tab === id ? 'white' : 'var(--jp-text)',
        border: '1px solid var(--jp-border)',
      }}>
      {emoji} {label}
    </button>
  );

  return (
    <div className="fixed inset-0 z-[1000] flex items-end md:items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.55)' }}
      onClick={onClose}
      data-testid="ai-image-modal">
      <div className="jp-card w-full md:max-w-lg flex flex-col"
        style={{ maxHeight: 'min(95dvh, 95vh)', borderTopLeftRadius: 18, borderTopRightRadius: 18 }}
        onClick={(e) => e.stopPropagation()}>
        <div className="p-4 border-b flex items-center justify-between flex-shrink-0">
          <h3 className="font-black text-base">✨ Image IA — Nano Banana</h3>
          <button onClick={onClose} className="opacity-60" data-testid="ai-image-close">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto min-h-0 p-4 space-y-3">
          {!enabled && (
            <div className="text-sm p-2 rounded" style={{ background: 'rgba(239,68,68,0.1)', color: '#EF4444' }}>
              La génération IA est désactivée par l'admin. Tu peux toujours uploader manuellement.
            </div>
          )}
          {enabled && quota && (
            <div className="text-xs p-2 rounded flex items-center justify-between"
              style={{ background: 'rgba(99,102,241,0.08)' }}
              data-testid="ai-quota">
              <span>{t('services.quota_du_jour')}<b>{quota.used_today}/{quota.quota}</b></span>
              <span style={{ color: remaining > 0 ? '#10B981' : '#EF4444' }}>
                {remaining > 0 ? `${remaining} restantes` : '⛔ épuisé'}
              </span>
            </div>
          )}

          <div className="flex gap-1" data-testid="ai-tabs">
            <TabBtn id="autophoto" label="Auto" emoji="🎬" />
            <TabBtn id="generate"  label="Générer" emoji="🎨" />
            <TabBtn id="enhance"   label="Améliorer" emoji="🖼️" />
            <TabBtn id="bgswap"    label="Fond" emoji="🪄" />
          </div>

          {tab === 'autophoto' && (
            <div className="space-y-2" data-testid="ai-pane-autophoto">
              <div className="text-sm p-2 rounded"
                style={{ background: 'rgba(99,102,241,0.08)' }}>
                🎬 <b>{t('services.pro_auto_photographie')}</b> — uploade 1 photo brute et JAPAP génère
                automatiquement 4 variations (studio, lifestyle, marble, luxe).
                Coût IA : 4 crédits.
              </div>
              <label className="text-sm">Photo source</label>
              <input type="file" accept="image/*" data-testid="ai-autophoto-file"
                onChange={e => { setFile(e.target.files?.[0] || null); setAutoVariations(null); }}
                className="jp-input w-full" />
              {autoVariations && (
                <div className="grid grid-cols-2 gap-2 mt-2" data-testid="ai-autophoto-results">
                  {autoVariations.map(v => (
                    <div key={v.preset}
                      data-testid={`ai-autophoto-variation-${v.preset}`}
                      onClick={() => v.ok && setAutoSelected(s => ({ ...s, [v.preset]: !s[v.preset] }))}
                      className="relative rounded-lg overflow-hidden border-2 cursor-pointer"
                      style={{
                        borderColor: autoSelected[v.preset] ? 'var(--jp-primary)' : 'rgba(0,0,0,0.1)',
                        opacity: v.ok ? 1 : 0.4,
                      }}>
                      {v.ok && v.image ? (
                        <img src={`${API}${v.image.thumb}`} alt={v.preset}
                          className="w-full h-24 object-cover" />
                      ) : (
                        <div className="w-full h-24 flex items-center justify-center text-xs p-1 text-center"
                          style={{ background: 'rgba(239,68,68,0.1)', color: '#EF4444' }}>
                          {v.error || 'échec'}
                        </div>
                      )}
                      <div className="px-1.5 py-0.5 text-[10px] text-center font-bold"
                        style={{
                          background: autoSelected[v.preset] ? 'var(--jp-primary)' : 'rgba(0,0,0,0.05)',
                          color: autoSelected[v.preset] ? 'white' : 'inherit',
                        }}>
                        {autoSelected[v.preset] ? '✓ ' : ''}{v.preset.replace('_', ' ')}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {tab === 'generate' && (
            <div className="space-y-2" data-testid="ai-pane-generate">
              <label className="text-sm">Décris le produit (FR ou EN)</label>
              <textarea value={prompt} onChange={e => setPrompt(e.target.value)}
                data-testid="ai-prompt"
                className="jp-input w-full" rows={3} maxLength={500}
                placeholder={t('services.ex_sneaker_blanc_minimaliste_avec_s')} />
              <p className="text-[11px] opacity-70">L'IA va créer une photo studio pro 45° depuis ce texte.</p>
            </div>
          )}

          {tab === 'enhance' && (
            <div className="space-y-2" data-testid="ai-pane-enhance">
              <label className="text-sm">Photo à améliorer</label>
              <input type="file" accept="image/*" data-testid="ai-enhance-file"
                onChange={e => setFile(e.target.files?.[0] || null)}
                className="jp-input w-full" />
              <label className="text-sm">Instruction (optionnel)</label>
              <input value={instruction} onChange={e => setInstruction(e.target.value)}
                data-testid="ai-enhance-instruction"
                className="jp-input w-full" maxLength={200}
                placeholder={t('services.ex_enleve_l_ombre_fond_blanc_pur')} />
            </div>
          )}

          {tab === 'bgswap' && (
            <div className="space-y-2" data-testid="ai-pane-bgswap">
              <label className="text-sm">Photo source</label>
              <input type="file" accept="image/*" data-testid="ai-bgswap-file"
                onChange={e => setFile(e.target.files?.[0] || null)}
                className="jp-input w-full" />
              <label className="text-sm">Choisis un fond</label>
              <select value={bgPreset} onChange={e => setBgPreset(e.target.value)}
                data-testid="ai-bgswap-preset"
                className="jp-input w-full">
                <option value="">— Aucun (utiliser scène libre) —</option>
                {(quota?.bg_presets || []).map(p => (
                  <option key={p} value={p}>{p.replace('_', ' ')}</option>
                ))}
              </select>
              <label className="text-sm">…ou décris la scène</label>
              <input value={customScene} onChange={e => setCustomScene(e.target.value)}
                data-testid="ai-bgswap-custom"
                className="jp-input w-full" maxLength={200}
                placeholder={t('services.ex_cafe_parisien_comptoir_bois_lumi')} />
            </div>
          )}

          {/* Preview */}
          {previewUrl && (
            <div className="mt-2" data-testid="ai-preview">
              <img src={previewUrl} alt="" className="w-full rounded-lg border"
                style={{ borderColor: 'var(--jp-border)', maxHeight: 320, objectFit: 'contain' }} />
            </div>
          )}
        </div>

        <div className="p-3 border-t flex-shrink-0 flex gap-2"
          style={{ paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom))' }}>
          {tab === 'autophoto' ? (
            !autoVariations ? (
              <button onClick={runAutoPhoto}
                disabled={loading || !enabled || remaining < 4 || !file}
                data-testid="ai-autophoto-run"
                className="jp-btn jp-btn-primary flex-1">
                {loading ? '⏳ 4 variations en cours (~60s)…'
                  : `🎬 Générer 4 variations (${remaining} crédits)`}
              </button>
            ) : (
              <>
                <button onClick={() => { setAutoVariations(null); setAutoSelected({}); }}
                  data-testid="ai-autophoto-redo"
                  className="jp-btn jp-btn-ghost">↻ Refaire</button>
                <button onClick={acceptAutoPhotos}
                  data-testid="ai-autophoto-accept"
                  className="jp-btn jp-btn-primary flex-1">
                  ✅ Ajouter au produit ({Object.values(autoSelected).filter(Boolean).length})
                </button>
              </>
            )
          ) : !previewUrl ? (
            <button
              onClick={tab === 'generate' ? runGenerate : tab === 'enhance' ? runEnhance : runBgSwap}
              disabled={loading || !enabled || remaining <= 0}
              data-testid="ai-run-btn"
              className="jp-btn jp-btn-primary flex-1">
              {loading ? '⏳ Création (10-30s)…' : `✨ Lancer (${remaining} restantes)`}
            </button>
          ) : (
            <>
              <button onClick={() => { setPreviewUrl(null); setPreviewPayload(null); }}
                data-testid="ai-redo-btn"
                className="jp-btn jp-btn-ghost">
                ↻ Refaire
              </button>
              <button onClick={() => onAccept(previewPayload)}
                data-testid="ai-accept-btn"
                className="jp-btn jp-btn-primary flex-1">
                ✅ Utiliser cette image
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
