/**
 * iter184 — Composant SEO universel (react-helmet-async)
 * =======================================================
 * Met à jour dynamiquement le <head> côté client : title, meta description,
 * Open Graph (FB/WA), Twitter Card, canonical URL et JSON-LD.
 *
 * Pour les robots qui n'exécutent pas le JS (FB/WhatsApp/Twitter),
 * le backend `CrawlerSEOMiddleware` détecte le User-Agent et retourne
 * du HTML pré-rendu. Ce composant gère le SEO côté Google/Bing (qui
 * exécutent le JS depuis 2019+).
 *
 * Usage :
 *   <Seo title="iPhone 13" description="..." image="/foo.jpg" type="product" />
 */
import React from 'react';
import { Helmet } from 'react-helmet-async';

const PUBLIC_URL = process.env.REACT_APP_PUBLIC_URL || process.env.REACT_APP_BACKEND_URL || '';

function abs(url) {
  if (!url) return null;
  if (typeof url !== 'string') return null;
  if (url.startsWith('http://') || url.startsWith('https://')) return url;
  if (url.startsWith('/')) return `${PUBLIC_URL}${url}`;
  return url;
}

export default function Seo({
  title, description, image, url, type = 'website',
  jsonLd, noindex = false,
}) {
  const fullTitle = title
    ? (title.toLowerCase().includes('japap') ? title : `${title} — JAPAP`)
    : 'JAPAP — Réseau social, Marketplace et Wallet sans frontières';
  const desc = description ||
    "Rejoins JAPAP : feed social, marketplace sécurisé, wallet USD, crowdfunding viral et IA intégrée.";
  const img = abs(image) || `${PUBLIC_URL}/japap-logo-512.png`;
  const canonical = url ? abs(url) : (typeof window !== 'undefined' ? window.location.href : PUBLIC_URL);

  return (
    <Helmet prioritizeSeoTags>
      <title>{fullTitle}</title>
      <meta name="description" content={desc} />
      <link rel="canonical" href={canonical} />
      {noindex ? (
        <meta name="robots" content="noindex,nofollow" />
      ) : (
        <meta name="robots" content="index,follow,max-image-preview:large" />
      )}
      <link rel="alternate" hrefLang="fr" href={canonical} />
      <link rel="alternate" hrefLang="x-default" href={canonical} />

      {/* Open Graph */}
      <meta property="og:type" content={type} />
      <meta property="og:title" content={fullTitle} />
      <meta property="og:description" content={desc} />
      <meta property="og:url" content={canonical} />
      <meta property="og:image" content={img} />
      <meta property="og:image:width" content="1200" />
      <meta property="og:image:height" content="630" />
      <meta property="og:site_name" content="JAPAP" />
      <meta property="og:locale" content="fr_FR" />

      {/* Twitter */}
      <meta name="twitter:card" content="summary_large_image" />
      <meta name="twitter:title" content={fullTitle} />
      <meta name="twitter:description" content={desc} />
      <meta name="twitter:image" content={img} />
      <meta name="twitter:site" content="@japap" />

      {/* JSON-LD schema.org */}
      {jsonLd && (
        <script type="application/ld+json">
          {JSON.stringify(jsonLd)}
        </script>
      )}
    </Helmet>
  );
}
