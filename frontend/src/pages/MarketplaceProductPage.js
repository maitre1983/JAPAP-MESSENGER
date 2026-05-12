/**
 * MarketplaceProductPage — Fiche produit complète (iter174 P0 Phase A)
 * =====================================================================
 * • Galerie images (slider + swipe + zoom plein écran)
 * • Métadonnées : prix, état, localisation, vendeur, badges
 * • CTA "Contacter le vendeur" (WhatsApp / Message JAPAP / Appel)
 * • Owner actions: Modifier, Supprimer, Online/Offline toggle
 * • Admin actions: Online / Offline / Delete moderation
 * • SEO-ready (preview-friendly URL /marketplace/p/{product_id})
 */
import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import i18n from 'i18next';
import {
  ArrowLeft, Heart, MapPin, Tag, ChatCircle, WhatsappLogo,
  PhoneCall, X, CaretLeft, CaretRight, MagnifyingGlassPlus,
  PencilSimple, TrashSimple, EyeSlash, Eye, ShieldCheck, Storefront,
  Rocket, Star, Wallet,
} from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import KycVerifiedBadge from '@/components/KycVerifiedBadge';
import ZoomableImage from '@/components/media/ZoomableImage';
import SmartImage from '@/components/media/SmartImage';
import Seo from '@/components/Seo';
import { useShareViralTracking, buildShareUrl } from '@/hooks/useShareViralTracking';
import { useCurrency, formatMoney } from '@/utils/currency';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';

const API = process.env.REACT_APP_BACKEND_URL;

const CONDITIONS = (t) => ({
  new: 'Neuf', used: 'Occasion', refurbished: i18n.t('marketplace_product.reconditionne'),
});

function ImageGallery({ images }) {
  const { t } = useTranslation();
  const [idx, setIdx] = useState(0);
  const [zoomed, setZoomed] = useState(false);
  const [touchStartX, setTouchStartX] = useState(null);

  const list = Array.isArray(images) && images.length > 0 ? images : [];
  if (list.length === 0) {
    return (
      <div className="w-full aspect-square rounded-2xl flex items-center justify-center"
        style={{ background: 'var(--jp-bg-2,#f3f4f6)', color: 'var(--jp-text-muted)' }}
        data-testid="mkt-no-image">
        <Storefront size={48} weight="duotone" />
        <span className="ml-2 text-sm">Aucune image</span>
      </div>
    );
  }
  const go = (delta) => setIdx((i) => (i + delta + list.length) % list.length);
  const cur = list[idx];
  const fullUrl = (img) => {
    const u = typeof img === 'string' ? img : (img?.full || img?.hd || img?.thumb || '');
    if (!u) return '';
    return u.startsWith('http') ? u : `${API}${u}`;
  };
  const hdUrl = (img) => {
    const u = typeof img === 'string' ? img : (img?.hd || img?.full || img?.thumb || '');
    if (!u) return '';
    return u.startsWith('http') ? u : `${API}${u}`;
  };
  const thumbUrl = (img) => {
    const u = typeof img === 'string' ? img : (img?.thumb || img?.full || img?.hd || '');
    if (!u) return '';
    return u.startsWith('http') ? u : `${API}${u}`;
  };
  return (
    <div data-testid="mkt-gallery">
      <div className="relative w-full aspect-square rounded-2xl overflow-hidden bg-black"
        onTouchStart={(e) => setTouchStartX(e.touches[0].clientX)}
        onTouchEnd={(e) => {
          if (touchStartX === null) return;
          const dx = e.changedTouches[0].clientX - touchStartX;
          if (Math.abs(dx) > 50) go(dx > 0 ? -1 : 1);
          setTouchStartX(null);
        }}>
        <button type="button" onClick={() => setZoomed(true)}
          data-testid="mkt-image-zoom-btn"
          className="absolute inset-0 flex items-center justify-center cursor-zoom-in">
          {/* iter239p — orientation-aware main product image. ZoomableImage
              stays in the fullscreen overlay below for actual pinch zoom. */}
          <SmartImage src={fullUrl(cur)} alt=""
            testId="mkt-image-main"
            style={{ width: '100%', height: '100%' }}
            onError={(e) => { e.currentTarget.style.opacity = '0.2'; }} />
        </button>
        {list.length > 1 && (
          <>
            <button onClick={() => go(-1)} data-testid="mkt-image-prev"
              className="absolute left-2 top-1/2 -translate-y-1/2 p-2 rounded-full"
              style={{ background: 'rgba(0,0,0,0.5)', color: 'white' }}>
              <CaretLeft size={20} weight="bold" />
            </button>
            <button onClick={() => go(1)} data-testid="mkt-image-next"
              className="absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-full"
              style={{ background: 'rgba(0,0,0,0.5)', color: 'white' }}>
              <CaretRight size={20} weight="bold" />
            </button>
            <div className="absolute bottom-2 left-1/2 -translate-x-1/2 px-2 py-0.5 rounded-full text-[10px] font-bold"
              style={{ background: 'rgba(0,0,0,0.5)', color: 'white' }}>
              {idx + 1} / {list.length}
            </div>
          </>
        )}
        <div className="absolute top-2 left-2 px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1"
          style={{ background: 'rgba(0,0,0,0.5)', color: 'white' }}>
          <MagnifyingGlassPlus size={11} weight="bold" /> Tap pour zoomer · 2 doigts pour pincer
        </div>
      </div>

      {list.length > 1 && (
        <div className="flex gap-2 overflow-x-auto mt-2 pb-1">
          {list.map((img, i) => (
            <button key={i} onClick={() => setIdx(i)}
              data-testid={`mkt-thumb-${i}`}
              className={`shrink-0 w-16 h-16 rounded-lg overflow-hidden border-2 transition-all ${i === idx ? 'border-rose-500 ring-2 ring-rose-200' : 'border-transparent opacity-70 hover:opacity-100'}`}>
              <img src={thumbUrl(img)} alt="" loading="lazy"
                className="w-full h-full object-cover" />
            </button>
          ))}
        </div>
      )}

      {zoomed && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center cursor-zoom-out"
          style={{ background: 'rgba(0,0,0,0.95)' }}
          onClick={() => setZoomed(false)}
          data-testid="mkt-image-zoom-overlay">
          <div onClick={(e) => e.stopPropagation()} className="w-full h-full flex items-center justify-center p-4">
            <ZoomableImage src={hdUrl(cur)} alt={t('marketplace_product.zoom')}
              maxHeight="92vh" background="transparent"
              testId="mkt-image-zoom-img" />
          </div>
          <button onClick={() => setZoomed(false)}
            className="absolute top-4 right-4 p-2 rounded-full text-white z-10"
            style={{ background: 'rgba(255,255,255,0.15)' }}>
            <X size={22} />
          </button>
        </div>
      )}
    </div>
  );
}

function ContactSellerActions({ product, user }) {
  // ⚠️ React rule-of-hooks: ALL hooks must be called unconditionally,
  // BEFORE any early return. Previous code returned null before the hooks,
  // causing "Rendered fewer hooks than expected" crashes when product was
  // null then resolved, or when the owner viewed their own product.
  const navigate = useNavigate();
  const [contacting, setContacting] = useState(false);

  const hidden = !product || product.seller_id === user?.user_id;
  const phone = (product?.phone_number || '').replace(/[^\d+]/g, '');
  const waMsg = encodeURIComponent(
    `Bonjour, je suis intéressé par "${product?.title || ''}" sur JAPAP Marketplace.`);
  const waLink = phone ? `https://wa.me/${phone.replace(/^\+/, '')}?text=${waMsg}` : null;

  const handleContactJapap = async () => {
    if (!product) return;
    if (!user?.user_id) {
      // iter187 — Save intent and redirect to login (CEO spec)
      try {
        sessionStorage.setItem('pending_contact_seller', product.product_id);
      } catch (_) {}
      navigate(`/login?next=/marketplace/p/${product.product_id}`);
      return;
    }
    setContacting(true);
    try {
      const { data } = await axios.post(
        `${API}/api/marketplace/products/contact-seller`,
        { product_id: product.product_id },
        { withCredentials: true,
          headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      toast.success("💬 Message envoyé au vendeur");
      // Redirect to the conversation in Messenger (route is /chat in this codebase)
      navigate(`/chat?conv=${encodeURIComponent(data.conv_id)}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Impossible de contacter le vendeur.");
    } finally {
      setContacting(false);
    }
  };

  if (hidden) return null;

  return (
    <div className="flex flex-wrap gap-2 mt-4" data-testid="mkt-contact-actions">
      <button onClick={handleContactJapap}
        disabled={contacting}
        data-testid="mkt-contact-japap"
        className="jp-btn jp-btn-primary inline-flex items-center gap-1.5">
        <ChatCircle size={16} weight="fill" />
        {contacting ? '⏳ Ouverture…' : '💬 Message JAPAP'}
      </button>
      {waLink && (
        <a href={waLink} target="_blank" rel="noopener noreferrer"
          data-testid="mkt-contact-whatsapp"
          className="jp-btn inline-flex items-center gap-1.5"
          style={{ background: '#25D366', color: 'white' }}>
          <WhatsappLogo size={16} weight="fill" /> WhatsApp
        </a>
      )}
      {phone && (
        <a href={`tel:${phone}`} data-testid="mkt-contact-call"
          className="jp-btn jp-btn-ghost inline-flex items-center gap-1.5">
          <PhoneCall size={16} weight="fill" /> Appeler
        </a>
      )}
    </div>
  );
}

/**
 * BoostModal — iter175
 * Sponsored Product Boost paid via JAPAP Wallet (USD canonical only).
 * 3 plans: basic_24h ($1) | standard_7d ($5) | homepage_30d ($10).
 * NO external PSP. Wallet only — cohérent avec la vision JAPAP.
 */
function BoostModal({ product, onClose, onSuccess }) {
  const { t } = useTranslation();
  const [plans, setPlans] = useState([]);
  const [balance, setBalance] = useState(null);
  const [selected, setSelected] = useState('basic_24h');
  const [loading, setLoading] = useState(false);
  const [paying, setPaying] = useState(false);
  // iter179 — targeting state
  const [isGlobal, setIsGlobal] = useState(true);
  const [countries, setCountries] = useState([]);   // ISO2 uppercase
  const [countryInput, setCountryInput] = useState('');
  const [ageMin, setAgeMin] = useState('');
  const [ageMax, setAgeMax] = useState('');
  const [targetingSettings, setTargetingSettings] = useState({
    enabled: true, country: true, age: true,
  });

  useEffect(() => {
    setLoading(true);
    Promise.all([
      axios.get(`${API}/api/marketplace/boost/plans`, { withCredentials: true }),
      axios.get(`${API}/api/wallet/balance`, { withCredentials: true }),
      axios.get(`${API}/api/settings/public`, { withCredentials: true }).catch(() => ({ data: {} })),
    ]).then(([p, w, s]) => {
      setPlans(p.data.plans || []);
      setBalance(parseFloat(w.data.balance_usd ?? w.data.balance ?? 0));
      setTargetingSettings({
        enabled: (s.data?.targeting_enabled ?? 'true') === 'true',
        country: (s.data?.allow_country_filter ?? 'true') === 'true',
        age: (s.data?.allow_age_filter ?? 'true') === 'true',
      });
    }).catch(() => {
      toast.error('Impossible de charger les plans de boost');
    }).finally(() => setLoading(false));
  }, []);

  const addCountry = () => {
    const code = (countryInput || '').toUpperCase().trim().slice(0, 2);
    if (code.length === 2 && !countries.includes(code) && countries.length < 30) {
      setCountries([...countries, code]);
    }
    setCountryInput('');
  };
  const removeCountry = (c) => setCountries(countries.filter(x => x !== c));

  const cur = plans.find((p) => p.plan === selected);
  const insufficient = cur && balance !== null && balance < parseFloat(cur.price_usd);
  const canConfirm = !!cur && !paying && !loading && !insufficient;

  const onConfirm = async () => {
    if (!cur || paying) return;
    setPaying(true);
    try {
      // iter179 — include targeting (sanitized: if global=true, ignore countries)
      const amin = ageMin === '' ? null : Math.max(13, Math.min(99, parseInt(ageMin, 10) || 13));
      const amax = ageMax === '' ? null : Math.max(13, Math.min(99, parseInt(ageMax, 10) || 99));
      const payload = {
        plan: selected,
        is_global: isGlobal,
        target_countries: isGlobal ? [] : countries,
        age_min: amin, age_max: amax,
      };
      const { data } = await axios.post(
        `${API}/api/marketplace/products/${product.product_id}/boost-wallet`,
        payload, { withCredentials: true });
      // iter176 — Premium share-on-boost toast (CEO viral loop)
      // iter184 — share URL via /api/seo/product/{id} (FB/WA OG card propre)
      // iter185 — UTM = share_{userId}_product_{productId} (récompense JAPAP)
      const shareUrl = buildShareUrl({
        type: 'product', entityId: product.product_id,
        sharerId: user?.user_id || product.seller_id,
      }) || `${window.location.origin}/marketplace/p/${product.product_id}`;
      const shareText = `🚀 ${product.title} — disponible sur JAPAP Marketplace`;
      const handleCopy = async () => {
        try {
          await navigator.clipboard.writeText(shareUrl);
          toast.success('Lien copié dans le presse-papiers');
        } catch {
          toast.error('Copie indisponible');
        }
      };
      const handleNative = async () => {
        if (navigator.share) {
          try {
            await navigator.share({ title: product.title, text: shareText, url: shareUrl });
          } catch {/* user cancelled */}
        } else {
          handleCopy();
        }
      };
      const waLink = `https://wa.me/?text=${encodeURIComponent(`${shareText}\n${shareUrl}`)}`;

      toast.custom((id) => (
        <div className="rounded-xl shadow-2xl overflow-hidden"
          style={{ minWidth: '320px', maxWidth: '380px', background: 'white' }}
          data-testid="mkt-boost-share-toast">
          <div className="p-4"
            style={{ background: 'linear-gradient(135deg,#10B981,#0EA5E9)', color: 'white' }}>
            <div className="font-['Outfit'] font-bold text-base flex items-center gap-2">
              <span>🚀</span> {data.message || 'Boost activé !'}
            </div>
            <div className="text-xs opacity-90 mt-1">
              Partage ton produit boosté pour décupler la visibilité.
            </div>
          </div>
          <div className="p-3 flex gap-2">
            <button onClick={() => { handleCopy(); }}
              data-testid="mkt-boost-share-copy"
              className="flex-1 jp-btn jp-btn-ghost jp-btn-sm inline-flex items-center justify-center gap-1">
              📋 Copier
            </button>
            <a href={waLink} target="_blank" rel="noopener noreferrer"
              data-testid="mkt-boost-share-whatsapp"
              onClick={() => setTimeout(() => toast.dismiss(id), 200)}
              className="flex-1 jp-btn jp-btn-sm inline-flex items-center justify-center gap-1"
              style={{ background: '#25D366', color: 'white' }}>
              💬 WhatsApp
            </a>
            <button onClick={() => { handleNative(); }}
              data-testid="mkt-boost-share-native"
              className="flex-1 jp-btn jp-btn-primary jp-btn-sm inline-flex items-center justify-center gap-1">
              📱 Partager
            </button>
          </div>
          <button onClick={() => toast.dismiss(id)}
            className="w-full text-[11px] py-1.5 border-t"
            style={{ color: 'var(--jp-text-muted)', borderColor: 'var(--jp-border)' }}>
            Fermer
          </button>
        </div>
      ), { duration: 12000 });

      onSuccess && onSuccess(data);
      onClose();
    } catch (e) {
      const detail = e.response?.data?.detail || 'Erreur';
      if (String(detail).startsWith('INSUFFICIENT_BALANCE')) {
        toast.error('Solde insuffisant — recharge ton wallet pour booster.');
      } else {
        toast.error(detail);
      }
    } finally {
      setPaying(false);
    }
  };

  const PLAN_META = {
    basic_24h:    { icon: '⚡', tagline: t('marketplace_product.ideal_pour_tester_un_produit'),          color: '#F59E0B' },
    standard_7d:  { icon: '🚀', tagline: t('marketplace_product.le_meilleur_rapport_visibilite_prix'),   color: '#EC4899' },
    homepage_30d: { icon: '🌟', tagline: 'Section "Produits Vedettes" en haut',   color: '#8B5CF6' },
  };

  return (
    <div className="fixed inset-0 z-[90] flex items-end sm:items-center justify-center p-0 sm:p-4"
      style={{ background: 'rgba(0,0,0,0.6)' }}
      onClick={onClose}
      data-testid="mkt-boost-modal-overlay">
      <div className="w-full sm:max-w-md bg-white rounded-t-2xl sm:rounded-2xl shadow-2xl flex flex-col"
        style={{ maxHeight: 'min(95dvh, 95vh)' }}
        onClick={(e) => e.stopPropagation()}
        data-testid="mkt-boost-modal">
        <div className="p-5 border-b flex items-center justify-between shrink-0"
          style={{ background: 'linear-gradient(135deg,#F59E0B,#EC4899)', color: 'white' }}>
          <div>
            <div className="text-xs opacity-90">Marketplace JAPAP</div>
            <div className="font-['Outfit'] font-bold text-lg flex items-center gap-2">
              <Rocket size={20} weight="fill" /> Booster ce produit
            </div>
          </div>
          <button onClick={onClose}
            data-testid="mkt-boost-close"
            className="p-2 rounded-full hover:bg-white/20"><X size={20} /></button>
        </div>

        <div className="p-5 space-y-3 flex-1 overflow-y-auto min-h-0">
          {loading ? (
            <div className="text-center text-sm py-8" style={{ color: 'var(--jp-text-muted)' }}>
              Chargement…
            </div>
          ) : (
            <>
              <div className="text-xs flex items-center gap-2 mb-2"
                style={{ color: 'var(--jp-text-muted)' }}>
                <Wallet size={14} /> Solde wallet :
                <span className="font-bold" style={{ color: 'var(--jp-text)' }}
                  data-testid="mkt-boost-wallet-balance">
                  {balance !== null ? `${balance.toFixed(2)} USD` : '…'}
                </span>
              </div>

              {plans.map((p) => {
                const meta = PLAN_META[p.plan] || {};
                const active = selected === p.plan;
                return (
                  <button key={p.plan}
                    onClick={() => setSelected(p.plan)}
                    data-testid={`mkt-boost-plan-${p.plan}`}
                    className={`w-full text-left p-4 rounded-xl border-2 transition-all ${active ? 'shadow-lg' : 'opacity-80 hover:opacity-100'}`}
                    style={{
                      borderColor: active ? meta.color : 'var(--jp-border)',
                      background: active ? `${meta.color}10` : 'var(--jp-surface)',
                    }}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-lg">{meta.icon}</span>
                          <div className="font-['Outfit'] font-bold">{p.label}</div>
                        </div>
                        <div className="text-xs mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                          {meta.tagline}
                        </div>
                      </div>
                      <div className="text-right shrink-0">
                        <div className="font-['Outfit'] font-extrabold text-lg"
                          style={{ color: meta.color }}>
                          ${parseFloat(p.price_usd).toFixed(0)}
                        </div>
                        <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                          {p.days} {p.days === 1 ? 'jour' : 'jours'}
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}

              {/* iter179 — Audience targeting */}
              {targetingSettings.enabled && (
                <div className="rounded-xl border p-3 space-y-3"
                  style={{ borderColor: 'var(--jp-border)', background: '#FAFAFA' }}
                  data-testid="mkt-boost-targeting">
                  <div className="font-['Outfit'] font-bold text-sm flex items-center gap-1">
                    🎯 Audience (optionnel)
                  </div>

                  {targetingSettings.country && (
                    <div>
                      <label className="flex items-center gap-2 text-xs mb-2 cursor-pointer"
                        data-testid="mkt-boost-target-global-label">
                        <input type="checkbox" checked={isGlobal}
                          onChange={e => setIsGlobal(e.target.checked)}
                          data-testid="mkt-boost-target-global" />
                        <span>🌍 Tous les pays</span>
                      </label>
                      {!isGlobal && (
                        <div>
                          <div className="flex gap-1">
                            <input type="text" value={countryInput}
                              onChange={e => setCountryInput(e.target.value.toUpperCase().slice(0, 2))}
                              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCountry(); } }}
                              data-testid="mkt-boost-country-input"
                              placeholder={t('marketplace_product.cm_fr_gh')}
                              maxLength={2}
                              className="jp-input text-xs flex-1 uppercase"
                              style={{ textTransform: 'uppercase' }} />
                            <button onClick={addCountry}
                              data-testid="mkt-boost-country-add"
                              className="jp-btn jp-btn-ghost jp-btn-sm px-3">
                              Ajouter
                            </button>
                          </div>
                          <div className="flex flex-wrap gap-1 mt-2">
                            {countries.map(c => (
                              <span key={c}
                                data-testid={`mkt-boost-country-chip-${c}`}
                                className="jp-badge inline-flex items-center gap-1"
                                style={{ background: '#EEF2FF', color: '#3730A3' }}>
                                {c}
                                <button onClick={() => removeCountry(c)}
                                  className="hover:text-red-500 ml-0.5">×</button>
                              </span>
                            ))}
                            {countries.length === 0 && (
                              <span className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                                Aucun pays sélectionné (diffusion bloquée si non global)
                              </span>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {targetingSettings.age && (
                    <div>
                      <div className="text-xs font-bold mb-1"
                        style={{ color: 'var(--jp-text)' }}>🎂 Tranche d'âge</div>
                      <div className="flex gap-2 items-center">
                        <input type="number" min="13" max="99"
                          value={ageMin} onChange={e => setAgeMin(e.target.value)}
                          data-testid="mkt-boost-age-min"
                          placeholder={t('marketplace_product.min')}
                          className="jp-input text-xs flex-1" />
                        <span className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>à</span>
                        <input type="number" min="13" max="99"
                          value={ageMax} onChange={e => setAgeMax(e.target.value)}
                          data-testid="mkt-boost-age-max"
                          placeholder={t('marketplace_product.max')}
                          className="jp-input text-xs flex-1" />
                      </div>
                      <p className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                        Laisse vide pour diffuser à tous les âges.
                      </p>
                    </div>
                  )}
                </div>
              )}

              <div className="text-[11px] p-3 rounded-lg flex gap-2"
                style={{ background: 'var(--jp-bg-2,#f9fafb)', color: 'var(--jp-text-muted)' }}>
                <Wallet size={14} className="shrink-0 mt-0.5" />
                <div>
                  Paiement débité du <strong>{t('marketplace_product.wallet_usd_japap')}</strong> uniquement.
                  Aucun débit externe (Hubtel/Stripe/PSP). Pour recharger, va dans
                  ton Wallet → Dépôt.
                </div>
              </div>
            </>
          )}
        </div>

        {/* iter179 — Sticky footer CTA (always visible on mobile + desktop) */}
        <div className="p-4 border-t shrink-0 sticky bottom-0"
          style={{
            background: 'var(--jp-bg-2,#fafafa)',
            paddingBottom: 'max(1rem, env(safe-area-inset-bottom))',
          }}>
          {insufficient ? (
            <Link to="/wallet" onClick={onClose}
              data-testid="mkt-boost-recharge-cta"
              className="jp-btn w-full inline-flex items-center justify-center gap-2"
              style={{ background: 'var(--jp-error)', color: 'white' }}>
              Solde insuffisant — Recharger le wallet
            </Link>
          ) : (
            <button onClick={onConfirm}
              disabled={!canConfirm}
              data-testid="mkt-boost-confirm"
              className="jp-btn jp-btn-primary w-full inline-flex items-center justify-center gap-2"
              style={{
                background: canConfirm
                  ? 'linear-gradient(135deg,#F59E0B,#EC4899)'
                  : undefined,
                color: 'white', fontWeight: 'bold',
              }}>
              <Rocket size={16} weight="fill" />
              {paying
                ? 'Paiement en cours…'
                : `🚀 Confirmer le boost — $${cur ? parseFloat(cur.price_usd).toFixed(0) : '?'}`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function MarketplaceProductPage() {
  const { t } = useTranslation();
  const { product_id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  // iter189-currency-fix — USD canonical → smart local display
  const currencyCtx = useCurrency();
  // iter185 — Boucle virale UTM : ping `/api/seo/viral/track` au mount si
  // l'URL contient `?utm=share_*` → +pts JAPAP au sharer (anti-fraud côté
  // backend : daily cap, dedupe IP/24h, pas de self-reward).
  useShareViralTracking({ visitorUserId: user?.user_id });
  const [product, setProduct] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [showBoost, setShowBoost] = useState(false);
  // iter176 — Escrow public settings (zero hardcode)
  const [escrowSettings, setEscrowSettings] = useState({
    auto_release_days: 7, commission_pct: 2, dispute_enabled: true,
  });
  useEffect(() => {
    axios.get(`${API}/api/settings/public`, { withCredentials: true })
      .then(({ data }) => setEscrowSettings({
        auto_release_days: parseInt(data.mkt_escrow_auto_release_days ?? '7', 10),
        commission_pct: parseFloat(data.mkt_escrow_commission_percent ?? '2'),
        dispute_enabled: (data.mkt_escrow_dispute_enabled ?? 'true') === 'true',
      }))
      .catch(() => {});
  }, []);

  const reload = useCallback(() => {
    setLoading(true); setErr('');
    axios.get(`${API}/api/marketplace/products/${product_id}`,
      { withCredentials: true })
      .then(({ data }) => setProduct(data))
      .catch((e) => setErr(e.response?.data?.detail || 'Produit introuvable'))
      .finally(() => setLoading(false));
  }, [product_id]);
  useEffect(reload, [reload]);

  const isOwner = user && product && user.user_id === product.seller_id;
  const isAdmin = user && (user.role === 'admin' || user.role === 'superadmin');

  const toggleStatus = async () => {
    const next = product.status === 'active' ? 'offline' : 'active';
    try {
      await axios.put(
        `${API}/api/marketplace/products/${product.product_id}`,
        { status: next }, { withCredentials: true });
      toast.success(next === 'active' ? 'Produit remis en ligne' : 'Produit hors ligne');
      reload();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  const [deleteOpen, setDeleteOpen] = useState(false);
  const onDelete = async () => {
    setDeleteOpen(false);
    try {
      await axios.delete(
        `${API}/api/marketplace/products/${product.product_id}`,
        { withCredentials: true });
      toast.success('Produit supprimé');
      navigate('/services?view=marketplace');
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  // iter176/178 — Buy via Wallet USD JAPAP escrow (shadcn AlertDialog confirm)
  const [buying, setBuying] = useState(false);
  const [confirmBuyOpen, setConfirmBuyOpen] = useState(false);
  const buyEscrow = async () => {
    if (!product || buying) return;
    setBuying(true);
    setConfirmBuyOpen(false);
    try {
      const { data } = await axios.post(`${API}/api/marketplace/orders`,
        { product_id: product.product_id, notes: '' }, { withCredentials: true });
      toast.success(data.message || 'Commande créée — fonds en escrow ✅');
      navigate('/services?view=marketplace');
    } catch (e) {
      const detail = e.response?.data?.detail || 'Erreur';
      if (String(detail).startsWith('INSUFFICIENT_BALANCE')) {
        toast.error('Solde insuffisant — recharge ton wallet pour acheter.');
      } else {
        toast.error(detail);
      }
    } finally {
      setBuying(false);
    }
  };

  // iter178 — Admin moderation via shadcn dialog with reason textarea
  const [adminModOpen, setAdminModOpen] = useState(null); // 'deleted' | 'offline' | null
  const [adminModReason, setAdminModReason] = useState('');
  const adminModerate = async (status) => {
    const reason = adminModReason.trim();
    setAdminModOpen(null); setAdminModReason('');
    try {
      await axios.post(
        `${API}/api/marketplace/admin/products/${product.product_id}/moderate`,
        { status, reason }, { withCredentials: true });
      toast.success('Modération appliquée');
      if (status === 'deleted') navigate('/services?view=marketplace');
      else reload();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  if (loading) return (
    <div className="p-8 text-center text-sm" style={{ color: 'var(--jp-text-muted)' }}
      data-testid="mkt-detail-loading">Chargement…</div>
  );
  if (err || !product) return (
    <div className="p-6 max-w-xl mx-auto" data-testid="mkt-detail-error">
      <button onClick={() => navigate(-1)} className="jp-btn jp-btn-ghost jp-btn-sm mb-4">
        <ArrowLeft size={14} /> Retour
      </button>
      <div className="jp-alert jp-alert-error">{err || 'Produit introuvable'}</div>
    </div>
  );

  return (
    <div className="max-w-3xl mx-auto px-4 py-4 pb-24" data-testid="mkt-detail-page">
      {/* iter184 — Dynamic SEO meta tags (title, OG, JSON-LD Product) */}
      <Seo
        title={`${product.title} — JAPAP Marketplace`}
        description={(product.description || `Achetez ${product.title} sur JAPAP Marketplace avec paiement sécurisé.`).slice(0, 160)}
        image={(product.images || []).find(Boolean) && (
          (typeof product.images[0] === 'object'
            ? (product.images[0].hd || product.images[0].full || product.images[0].thumb)
            : product.images[0])
        )}
        type="product"
        jsonLd={{
          '@context': 'https://schema.org', '@type': 'Product',
          name: product.title,
          description: (product.description || '').slice(0, 500),
          image: typeof product.images?.[0] === 'object'
            ? (product.images[0].hd || product.images[0].full)
            : product.images?.[0],
          offers: {
            '@type': 'Offer',
            priceCurrency: product.currency || 'USD',
            price: String(product.price || '0'),
            availability: product.status === 'active'
              ? 'https://schema.org/InStock' : 'https://schema.org/OutOfStock',
          },
          brand: { '@type': 'Brand', name: 'JAPAP' },
        }}
      />
      <button onClick={() => navigate(-1)}
        data-testid="mkt-detail-back"
        className="jp-btn jp-btn-ghost jp-btn-sm mb-3 inline-flex items-center gap-1">
        <ArrowLeft size={14} /> Retour
      </button>

      <ImageGallery images={product.images} />

      <div className="mt-4 flex items-start gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <h1 className="font-['Outfit'] font-bold text-2xl">{product.title}</h1>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-2xl font-extrabold" style={{ color: 'var(--jp-primary)' }}
              data-testid="mkt-detail-price">
              {formatMoney(product.price, currencyCtx, { short: false })}
            </span>
          </div>
        </div>
        {product.status !== 'active' && (
          <span className="jp-badge"
            style={{ background: '#FEF3C7', color: '#854D0E' }}
            data-testid={`mkt-detail-status-${product.status}`}>
            {product.status === 'offline' ? 'Hors ligne' : t('marketplace_product.supprime')}
          </span>
        )}
      </div>

      <div className="flex flex-wrap gap-3 text-xs mt-3" style={{ color: 'var(--jp-text-muted)' }}>
        {product.condition && (
          <span className="inline-flex items-center gap-1">
            <Tag size={12} /> {CONDITIONS(t)[product.condition] || product.condition}
          </span>
        )}
        {product.location && (
          <span className="inline-flex items-center gap-1">
            <MapPin size={12} /> {product.location}
          </span>
        )}
        <span>👁 {product.views_count || 0} vues</span>
      </div>

      {/* iter175 — 24h freshness counter (social proof / scarcity) */}
      {(product.unique_viewers_24h || 0) > 0 && (
        <div className="mt-3 inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-semibold"
          style={{
            background: 'linear-gradient(135deg,#FEF3C7,#FED7AA)',
            color: '#92400E',
          }}
          data-testid="mkt-views-24h">
          <Eye size={13} weight="fill" />
          <span>
            {product.unique_viewers_24h === 1
              ? '1 personne a vu ce produit aujourd’hui'
              : `${product.unique_viewers_24h} personnes ont vu ce produit aujourd’hui`}
          </span>
        </div>
      )}

      {/* iter175 — Active boost badges */}
      {product.active_boost && (
        <div className="mt-3 inline-flex items-center gap-2 ml-2 px-3 py-1.5 rounded-full text-xs font-bold"
          style={{
            background: product.active_boost.is_homepage
              ? 'linear-gradient(135deg,#8B5CF6,#EC4899)'
              : 'linear-gradient(135deg,#F59E0B,#F97316)',
            color: 'white',
          }}
          data-testid={`mkt-boost-active-${product.active_boost.plan}`}>
          {product.active_boost.is_homepage
            ? <><Star size={13} weight="fill" /> Vedette</>
            : <><Rocket size={13} weight="fill" /> Boosté</>}
          <span className="opacity-90">·</span>
          <span>jusqu’au {new Date(product.active_boost.expires_at).toLocaleDateString('fr-FR')}</span>
        </div>
      )}

      {/* iter179 — Audience targeting badges */}
      {product.active_boost && (
        !product.active_boost.is_global
        || product.active_boost.age_min != null
        || product.active_boost.age_max != null
      ) && (
        <div className="mt-2 flex flex-wrap gap-1.5" data-testid="mkt-boost-audience">
          <span className="jp-badge inline-flex items-center gap-1"
            style={{ background: '#EEF2FF', color: '#3730A3', fontSize: '10px' }}>
            🎯 Audience ciblée
          </span>
          {!product.active_boost.is_global && (product.active_boost.target_countries || []).length > 0 && (
            <span className="jp-badge inline-flex items-center gap-1"
              style={{ background: '#F0FDF4', color: '#065F46', fontSize: '10px' }}
              data-testid="mkt-boost-audience-countries">
              🌍 {product.active_boost.target_countries.join(' · ')}
            </span>
          )}
          {(product.active_boost.age_min != null || product.active_boost.age_max != null) && (
            <span className="jp-badge inline-flex items-center gap-1"
              style={{ background: '#FFFBEB', color: '#92400E', fontSize: '10px' }}
              data-testid="mkt-boost-audience-age">
              🎂 {product.active_boost.age_min ?? '?'}–{product.active_boost.age_max ?? '?'} ans
            </span>
          )}
        </div>
      )}


      {/* Vendor block */}
      <Link
        to={`/profile/${product.seller_id}`}
        className="block mt-4 p-3 jp-card jp-card-hover"
        data-testid="mkt-detail-seller">
        <div className="flex items-center gap-3">
          {product.avatar ? (
            <img src={product.avatar} alt="" className="w-10 h-10 rounded-full object-cover" />
          ) : (
            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-indigo-400 to-rose-400 text-white font-bold flex items-center justify-center">
              {(product.first_name?.[0] || '?').toUpperCase()}
            </div>
          )}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1 flex-wrap">
              <span className="font-semibold">{product.first_name} {product.last_name}</span>
              <KycVerifiedBadge verified={product.seller_kyc_verified} size="sm" />
              {product.seller_verified_pro && (
                <span data-testid="mkt-vendor-verified-pro"
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold"
                  style={{ background: 'linear-gradient(135deg,#F59E0B,#EC4899)', color: 'white' }}>
                  <ShieldCheck size={11} weight="fill" /> Vendeur Pro
                </span>
              )}
            </div>
            <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
              @{product.username} · {product.seller_completed_sales || 0} ventes finalisées
            </div>
          </div>
        </div>
      </Link>

      {/* iter176 — Escrow trust banner (CEO mandatory UX) */}
      {!isOwner && product.status === 'active' && (
        <div className="mt-4 p-4 rounded-xl border-2"
          style={{
            borderColor: 'var(--jp-success,#10B981)',
            background: 'rgba(16,185,129,0.08)',
            color: 'var(--jp-text)',
          }}
          data-testid="mkt-escrow-info">
          <div className="font-bold mb-1.5 inline-flex items-center gap-1.5 text-sm">
            🔒 Paiement sécurisé JAPAP
          </div>
          <ul className="space-y-0.5 list-none text-xs mb-3"
            style={{ color: 'var(--jp-text-secondary)' }}>
            <li>💰 Tes fonds sont bloqués jusqu’à confirmation de réception.</li>
            <li>⏳ Libération automatique après {escrowSettings.auto_release_days} jours sans action.</li>
            {escrowSettings.dispute_enabled && (
              <li>⚖️ Litige possible avant la libération — équipe JAPAP arbitre.</li>
            )}
          </ul>
          <button onClick={() => setConfirmBuyOpen(true)} disabled={buying}
            data-testid="mkt-buy-escrow"
            className="jp-btn w-full inline-flex items-center justify-center gap-2 font-bold"
            style={{
              background: 'linear-gradient(135deg,#10B981,#0EA5E9)',
              color: 'white',
            }}>
            🛒 {buying
              ? 'Paiement en cours…'
              : `Acheter pour ${parseFloat(product.price).toFixed(2)} USD via Escrow`}
          </button>
        </div>
      )}

      {/* Contact actions */}
      <ContactSellerActions product={product} user={user} />

      {/* Description */}
      {product.description && (
        <div className="mt-4 p-4 jp-card whitespace-pre-line text-sm leading-relaxed"
          data-testid="mkt-detail-description">
          {product.description}
        </div>
      )}

      {/* Owner / Admin actions */}
      {(isOwner || isAdmin) && (
        <div className="mt-5 p-4 jp-card border-2"
          style={{ borderColor: 'var(--jp-primary)', borderStyle: 'dashed' }}>
          <div className="text-xs font-bold uppercase tracking-widest mb-2"
            style={{ color: 'var(--jp-primary)' }}>
            {isOwner ? 'Mes actions vendeur' : t('marketplace_product.moderation_admin')}
          </div>
          <div className="flex flex-wrap gap-2">
            {isOwner && (
              <>
                <button onClick={() => setShowBoost(true)}
                  data-testid="mkt-owner-boost"
                  className="jp-btn jp-btn-sm inline-flex items-center gap-1"
                  style={{
                    background: 'linear-gradient(135deg,#F59E0B,#EC4899)',
                    color: 'white',
                  }}>
                  <Rocket size={14} weight="fill" /> Booster ce produit
                </button>
                <Link to={`/services?view=marketplace&edit=${product.product_id}`}
                  data-testid="mkt-owner-edit"
                  className="jp-btn jp-btn-ghost jp-btn-sm inline-flex items-center gap-1">
                  <PencilSimple size={14} /> Modifier
                </Link>
                <button onClick={toggleStatus} data-testid="mkt-owner-toggle-status"
                  className="jp-btn jp-btn-ghost jp-btn-sm inline-flex items-center gap-1">
                  {product.status === 'active'
                    ? <><EyeSlash size={14} /> Mettre hors ligne</>
                    : <><Eye size={14} /> Remettre en ligne</>}
                </button>
                <button onClick={() => setDeleteOpen(true)} data-testid="mkt-owner-delete"
                  className="jp-btn jp-btn-sm inline-flex items-center gap-1"
                  style={{ background: 'var(--jp-error)', color: 'white' }}>
                  <TrashSimple size={14} /> Supprimer
                </button>
                <Link to={`/marketplace/ads?product=${product.product_id}`}
                  data-testid="mkt-owner-ads-link"
                  className="jp-btn jp-btn-ghost jp-btn-sm inline-flex items-center gap-1">
                  📊 Mes campagnes Ads
                </Link>
              </>
            )}
            {isAdmin && !isOwner && (
              <>
                <button onClick={() => adminModerate('active')}
                  data-testid="mkt-admin-online"
                  className="jp-btn jp-btn-ghost jp-btn-sm inline-flex items-center gap-1">
                  <Eye size={14} /> ONLINE
                </button>
                <button onClick={() => { setAdminModReason(''); setAdminModOpen('offline'); }}
                  data-testid="mkt-admin-offline"
                  className="jp-btn jp-btn-ghost jp-btn-sm inline-flex items-center gap-1">
                  <EyeSlash size={14} /> OFFLINE
                </button>
                <button onClick={() => { setAdminModReason(''); setAdminModOpen('deleted'); }}
                  data-testid="mkt-admin-delete"
                  className="jp-btn jp-btn-sm inline-flex items-center gap-1"
                  style={{ background: 'var(--jp-error)', color: 'white' }}>
                  <TrashSimple size={14} /> DELETED
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {/* iter175 — Boost modal */}
      {showBoost && (
        <BoostModal
          product={product}
          onClose={() => setShowBoost(false)}
          onSuccess={() => reload()} />
      )}

      {/* iter178 — Buy escrow confirm dialog (shadcn) */}
      <AlertDialog open={confirmBuyOpen} onOpenChange={setConfirmBuyOpen}>
        <AlertDialogContent data-testid="mkt-buy-escrow-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>🔒 Confirmer l'achat via Escrow</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm">
                <div>
                  Vous êtes sur le point d'acheter <strong>{product?.title}</strong> pour{' '}
                  <strong>{product ? parseFloat(product.price).toFixed(2) : '0.00'} USD</strong>.
                </div>
                <div className="p-3 rounded-lg text-xs"
                  style={{ background: '#F0FDF4', color: '#065F46' }}>
                  💰 Le montant sera <strong>débité immédiatement</strong> de votre wallet et
                  bloqué dans l'escrow JAPAP.<br />
                  ⏳ Le vendeur sera payé après votre confirmation, ou automatiquement après{' '}
                  <strong>{escrowSettings.auto_release_days} jours</strong>.<br />
                  ⚖️ Vous pourrez ouvrir un litige avant la libération si nécessaire.
                </div>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="mkt-buy-escrow-confirm-cancel">
              Annuler
            </AlertDialogCancel>
            <AlertDialogAction
              data-testid="mkt-buy-escrow-confirm-ok"
              onClick={buyEscrow}
              style={{ background: 'linear-gradient(135deg,#10B981,#0EA5E9)' }}>
              🛒 Confirmer l'achat
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* iter178 — Owner delete confirm */}
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent data-testid="mkt-owner-delete-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>🗑️ Supprimer ce produit ?</AlertDialogTitle>
            <AlertDialogDescription>
              Cette action est <strong>irréversible</strong>. Le produit ne sera plus
              accessible aux acheteurs et toutes ses statistiques seront perdues.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="mkt-owner-delete-cancel">Annuler</AlertDialogCancel>
            <AlertDialogAction
              data-testid="mkt-owner-delete-ok"
              onClick={onDelete}
              style={{ background: 'var(--jp-error)' }}>
              🗑️ Supprimer définitivement
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* iter178 — Admin moderation dialog with reason textarea */}
      <AlertDialog open={!!adminModOpen} onOpenChange={(o) => !o && setAdminModOpen(null)}>
        <AlertDialogContent data-testid="mkt-admin-moderate-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {adminModOpen === 'deleted' ? '🗑️ Suppression admin' : '⏸️ Mettre hors-ligne'}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm">
                <div>
                  Cette action sera consignée dans l'audit trail. Indique un motif clair
                  pour ce produit :
                </div>
                <textarea
                  value={adminModReason}
                  onChange={(e) => setAdminModReason(e.target.value)}
                  data-testid="mkt-admin-moderate-reason"
                  rows={3}
                  placeholder={adminModOpen === 'deleted'
                    ? 'Ex : Contenu interdit (faux produits, copyright)…'
                    : t('marketplace_product.ex_photos_non_conformes_prix_incohe')}
                  className="w-full rounded-lg border p-2 text-sm"
                  style={{ borderColor: 'var(--jp-border)' }} />
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="mkt-admin-moderate-cancel">Annuler</AlertDialogCancel>
            <AlertDialogAction
              data-testid="mkt-admin-moderate-ok"
              onClick={() => adminModerate(adminModOpen)}
              style={{ background: adminModOpen === 'deleted' ? 'var(--jp-error)' : '#F59E0B' }}>
              {adminModOpen === 'deleted' ? '🗑️ Supprimer' : '⏸️ Mettre hors-ligne'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
