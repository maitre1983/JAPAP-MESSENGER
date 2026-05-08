/**
 * iter192 — Smart Product Card for the chat.
 * ==========================================
 * Detects JAPAP Marketplace product URLs in a message body and renders a
 * premium visual card instead of the raw "Message JAPAP" + long link.
 *
 * Design (mix Instagram / Marketplace / WhatsApp Business):
 *   ┌─────────────────────────────┐
 *   │    [product image 16:9]     │   ← dominant visual
 *   │        (with fade)          │
 *   ├─────────────────────────────┤
 *   │  Product Title              │   ← 2-line clamp
 *   │  $12.99                     │   ← bold primary color
 *   │  [ Voir ]   [ Acheter ]     │   ← 2 CTAs, equal weight
 *   └─────────────────────────────┘
 *
 * Behavior:
 *   - Extracts /marketplace/p/{id} from the message text (relative or absolute)
 *   - Fetches product via /api/marketplace/products/{id}
 *   - Caches fetched products in a module-level Map (one fetch per session)
 *   - "Voir" opens the product detail page
 *   - "Acheter" opens the product detail page with a buy intent hash
 */
import { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { useCurrency, formatMoney } from '@/utils/currency';

const API = process.env.REACT_APP_BACKEND_URL;
const PRODUCT_URL_RE = /(?:https?:\/\/[^\s/]+)?\/marketplace\/p\/(prod_[a-f0-9]{6,32})/i;
const _cache = new Map();  // product_id -> product data

/**
 * Returns `{ productId, before, after }` if the message text contains a
 * JAPAP marketplace link, otherwise `null`.
 */
export function extractProductLink(text) {
  if (!text || typeof text !== 'string') return null;
  const m = text.match(PRODUCT_URL_RE);
  if (!m) return null;
  const link = m[0];
  const idx = text.indexOf(link);
  const before = text.slice(0, idx).trim();
  const after = text.slice(idx + link.length).trim();
  return { productId: m[1], before, after, link };
}

export default function SmartProductCard({
  productId, fallbackText = '', isMine = false,
}) {
  const navigate = useNavigate();
  const currencyCtx = useCurrency();
  const [product, setProduct] = useState(() => _cache.get(productId) || null);
  const [loading, setLoading] = useState(!_cache.has(productId));
  const [error, setError] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    if (_cache.has(productId)) return;
    let aborted = false;
    (async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/marketplace/products/${productId}`,
          { withCredentials: true, timeout: 10000 });
        if (aborted || !mounted.current) return;
        _cache.set(productId, data);
        setProduct(data);
      } catch (e) {
        if (!aborted && mounted.current) setError(true);
      } finally {
        if (!aborted && mounted.current) setLoading(false);
      }
    })();
    return () => {
      aborted = true;
      mounted.current = false;
    };
  }, [productId]);

  const openProduct = () => {
    navigate(`/marketplace/p/${productId}`);
  };
  const buyProduct = () => {
    // Take user to product page with a buy-intent hash — product page
    // auto-scrolls to the CTA and triggers contact-seller if logged in.
    navigate(`/marketplace/p/${productId}#buy`);
  };

  // Loading skeleton — matches the final card dimensions to avoid jank.
  if (loading) {
    return (
      <div
        data-testid={`smart-product-skeleton-${productId}`}
        className="rounded-2xl overflow-hidden"
        style={{
          width: 'min(320px, 82vw)',
          background: isMine ? 'rgba(255,255,255,0.12)' : '#F3F4F6',
        }}>
        <div className="aspect-[16/10] animate-pulse"
          style={{ background: isMine ? 'rgba(255,255,255,0.08)' : '#E5E7EB' }} />
        <div className="p-3 space-y-2">
          <div className="h-4 rounded animate-pulse"
            style={{ background: isMine ? 'rgba(255,255,255,0.18)' : '#E5E7EB', width: '80%' }} />
          <div className="h-5 rounded animate-pulse w-1/2"
            style={{ background: isMine ? 'rgba(255,255,255,0.18)' : '#E5E7EB' }} />
        </div>
      </div>
    );
  }

  // Fallback — if the product is unreachable (deleted / network), show a
  // minimal card with just the plain text so the message is never lost.
  if (error || !product) {
    return (
      <div className="rounded-2xl px-3 py-2.5 text-sm leading-relaxed"
        data-testid={`smart-product-unavailable-${productId}`}
        style={{
          background: isMine ? 'rgba(255,255,255,0.12)' : '#F3F4F6',
          color: isMine ? 'rgba(255,255,255,0.95)' : '#374151',
        }}>
        {fallbackText || '🛍️ Produit non disponible'}
        <button
          onClick={openProduct}
          data-testid={`smart-product-retry-${productId}`}
          className="block mt-1 text-xs underline underline-offset-2 font-semibold"
          style={{ color: isMine ? '#FFF' : '#0F056B' }}>
          Voir sur Marketplace
        </button>
      </div>
    );
  }

  // Primary image — images[] items are {hd, full, thumb} objects.
  // Pick `full` (or `hd` or `thumb`) and resolve relative → absolute URL.
  const firstImage = (() => {
    const imgs = Array.isArray(product.images) ? product.images
               : Array.isArray(product.media) ? product.media
               : [];
    if (!imgs.length) return '';
    const first = imgs[0];
    // Object shape {hd, full, thumb} OR plain string
    const url = (first && typeof first === 'object')
      ? (first.full || first.hd || first.thumb || '')
      : String(first || '');
    if (!url) return '';
    return url.startsWith('http') ? url : `${API}${url}`;
  })();

  const priceDisplay = formatMoney(product.price, currencyCtx, { short: false });
  const sellerName = product.seller_name
    || [product.first_name, product.last_name].filter(Boolean).join(' ').trim()
    || product.seller_username
    || product.username
    || '';

  // iter192b — Dynamic urgency signals (real data only, no fake urgency).
  // Rules:
  //   🔥 "X vues aujourd'hui" — only if views_24h > 3
  //   ⚡ "Dernier exemplaire"  — if stock === 1
  //   ⚡ "Plus que X en stock" — if stock >= 2 && stock <= 5
  // Everything else: no badge (keeps the card clean). Style follows
  // Amazon / Booking conventions — discreet, left-aligned, not alarming.
  const views24h = Number(product.views_24h || 0);
  const stock = Number(product.stock || 0);
  const signals = [];
  if (views24h > 3) {
    signals.push({
      key: 'views',
      icon: '🔥',
      text: `${views24h} vue${views24h > 1 ? 's' : ''} aujourd'hui`,
      tone: 'hot',
    });
  }
  if (stock === 1) {
    signals.push({ key: 'stock1', icon: '⚡', text: 'Dernier exemplaire', tone: 'urgent' });
  } else if (stock >= 2 && stock <= 5) {
    signals.push({ key: 'stock', icon: '⚡', text: `Plus que ${stock} en stock`, tone: 'urgent' });
  }

  return (
    <div
      data-testid={`smart-product-card-${productId}`}
      className="rounded-2xl overflow-hidden shadow-sm"
      style={{
        width: 'min(320px, 82vw)',
        background: '#FFFFFF',
        border: '1px solid rgba(0,0,0,0.06)',
      }}>
      {/* Visual — dominant 16:10 product image with subtle gradient bottom
          to bleed into the title area (Instagram feel). */}
      <button
        type="button"
        onClick={openProduct}
        className="block relative w-full aspect-[16/10] overflow-hidden"
        style={{ background: '#111827' }}
        aria-label={`Voir ${product.title}`}>
        {firstImage ? (
          <img src={firstImage} alt={product.title}
            loading="lazy" decoding="async"
            className="w-full h-full object-cover transition-transform duration-500 hover:scale-[1.03]" />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-white/40 text-4xl">🛍️</div>
        )}
        {/* Price badge — Facebook Marketplace feel, bottom-right corner. */}
        <span
          className="absolute bottom-2 right-2 px-2.5 py-1 text-xs font-['Outfit'] font-extrabold rounded-full"
          style={{
            background: 'rgba(255,255,255,0.96)',
            color: '#0F056B',
            backdropFilter: 'blur(4px)',
            boxShadow: '0 2px 6px rgba(0,0,0,0.15)',
          }}
          data-testid={`smart-product-price-badge-${productId}`}>
          {priceDisplay}
        </span>
        {/* JAPAP chip — top-left, reinforces brand without shouting. */}
        <span
          className="absolute top-2 left-2 px-2 py-0.5 text-[10px] font-['Manrope'] font-bold rounded-md uppercase tracking-wide"
          style={{
            background: 'rgba(15,5,107,0.92)',
            color: '#FFF',
            letterSpacing: '0.08em',
          }}>
          JAPAP
        </span>
      </button>

      {/* Meta + CTAs */}
      <div className="px-3 pt-2.5 pb-2.5 space-y-2">
        <h4
          className="text-sm font-['Manrope'] font-semibold leading-snug line-clamp-2"
          style={{ color: '#1F2937' }}
          data-testid={`smart-product-title-${productId}`}>
          {product.title || 'Produit'}
        </h4>
        {sellerName && (
          <p className="text-[11px] font-['Manrope'] text-gray-500 -mt-1 truncate">
            par {sellerName}
          </p>
        )}
        {/* iter192b — Urgency signals. Rendered only when thresholds met,
            never otherwise. Two-row layout on very narrow screens. */}
        {signals.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-0.5"
            data-testid={`smart-product-signals-${productId}`}>
            {signals.map((s) => (
              <span
                key={s.key}
                data-testid={`smart-product-signal-${s.key}-${productId}`}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-['Manrope'] font-semibold leading-none"
                style={{
                  background: s.tone === 'urgent' ? '#FEF3C7' : '#FEE2E2',
                  color:      s.tone === 'urgent' ? '#92400E' : '#991B1B',
                }}>
                <span aria-hidden="true">{s.icon}</span>
                <span>{s.text}</span>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2 pt-0.5">
          <button
            type="button"
            onClick={openProduct}
            data-testid={`smart-product-view-${productId}`}
            className="flex-1 py-2 rounded-xl text-xs font-['Manrope'] font-semibold transition-colors"
            style={{
              background: '#F3F4F6',
              color: '#0F056B',
              border: '1px solid rgba(15,5,107,0.12)',
            }}>
            Voir
          </button>
          <button
            type="button"
            onClick={buyProduct}
            data-testid={`smart-product-buy-${productId}`}
            className="flex-1 py-2 rounded-xl text-xs font-['Outfit'] font-extrabold text-white transition-transform active:scale-95"
            style={{
              background: 'linear-gradient(135deg, #0F056B 0%, #E01C2E 120%)',
              boxShadow: '0 4px 10px rgba(15,5,107,0.25)',
            }}>
            Acheter
          </button>
        </div>
      </div>
    </div>
  );
}
