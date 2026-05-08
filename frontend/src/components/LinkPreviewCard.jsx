/**
 * iter212 — LinkPreviewCard
 * =========================
 * LinkedIn-style rich Open Graph preview card rendered inline in the
 * feed when a post contains a generic http(s) URL (i.e. not YouTube /
 * Vimeo which have their own iframe embeds).
 *
 * Layout:
 *   ┌──────────────────────────────┐
 *   │                              │
 *   │       [16:9 cover image]     │
 *   │                              │
 *   ├──────────────────────────────┤
 *   │  DOMAIN.COM                  │  ← small uppercase, muted
 *   │  Title of the article …      │  ← bold, 2 lines max
 *   │  Short description preview … │  ← normal, 2 lines max
 *   └──────────────────────────────┘
 *
 * States:
 *   • loading    → subtle skeleton (matches final card size to avoid CLS)
 *   • has image  → full card with cover
 *   • no image   → compact card with favicon + title + description
 *   • error/null → caller falls back to a plain anchor tag
 */
import { useState } from 'react';
import useOgPreview from '@/hooks/useOgPreview';

function Skeleton({ testId }) {
  return (
    <div
      data-testid={`${testId}-skeleton`}
      className="rounded-xl overflow-hidden"
      style={{
        border: '1px solid var(--jp-border, #e4e4e7)',
        background: 'var(--jp-card, #fff)',
        marginTop: 8,
      }}
    >
      <div
        style={{
          aspectRatio: '16 / 9',
          background: 'linear-gradient(90deg, #eee 25%, #f5f5f5 50%, #eee 75%)',
          backgroundSize: '200% 100%',
          animation: 'jp-shimmer 1.4s infinite',
        }}
      />
      <div style={{ padding: '12px 14px' }}>
        <div style={{ height: 10, width: '30%', background: '#eee', borderRadius: 4, marginBottom: 8 }} />
        <div style={{ height: 14, width: '80%', background: '#eee', borderRadius: 4, marginBottom: 6 }} />
        <div style={{ height: 12, width: '60%', background: '#f0f0f0', borderRadius: 4 }} />
      </div>
      <style>{`@keyframes jp-shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}`}</style>
    </div>
  );
}

function Card({ data, href, testId }) {
  const { title, description, image, domain, favicon, site_name } = data;
  const [imgOk, setImgOk] = useState(Boolean(image));
  const [favOk, setFavOk] = useState(Boolean(favicon));

  const hasImage = imgOk && image;
  const shownDomain = (site_name || domain || '').toUpperCase();

  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      data-testid={testId}
      className="block rounded-xl overflow-hidden transition-shadow hover:shadow-md"
      style={{
        border: '1px solid var(--jp-border, #e4e4e7)',
        background: 'var(--jp-card, #fff)',
        color: 'var(--jp-text, #111)',
        textDecoration: 'none',
        marginTop: 8,
      }}
    >
      {hasImage && (
        <div
          style={{
            aspectRatio: '16 / 9',
            background: '#f4f4f5',
            overflow: 'hidden',
          }}
        >
          <img
            src={image}
            alt=""
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setImgOk(false)}
            style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
          />
        </div>
      )}
      <div style={{ padding: '12px 14px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: 0.6,
            color: 'var(--jp-muted, #71717a)',
            marginBottom: 4,
            textTransform: 'uppercase',
          }}
        >
          {favOk && favicon && (
            <img
              src={favicon}
              alt=""
              width={14}
              height={14}
              loading="lazy"
              referrerPolicy="no-referrer"
              onError={() => setFavOk(false)}
              style={{ borderRadius: 3, flexShrink: 0 }}
            />
          )}
          <span
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {shownDomain}
          </span>
        </div>
        {title && (
          <div
            style={{
              fontSize: 14,
              fontWeight: 700,
              lineHeight: 1.3,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
              marginBottom: description ? 4 : 0,
            }}
          >
            {title}
          </div>
        )}
        {description && (
          <div
            style={{
              fontSize: 12,
              lineHeight: 1.4,
              color: 'var(--jp-muted, #52525b)',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {description}
          </div>
        )}
      </div>
    </a>
  );
}

export default function LinkPreviewCard({ url, testId = 'link-preview' }) {
  const { preview, loading } = useOgPreview(url);

  if (loading) return <Skeleton testId={testId} />;

  // No data (error or empty) → fallback to a clean anchor.
  if (!preview || (!preview.title && !preview.description && !preview.image)) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer noopener"
        data-testid={`${testId}-fallback`}
        style={{
          color: 'var(--jp-primary)',
          textDecoration: 'underline',
          wordBreak: 'break-all',
          fontSize: 14,
        }}
      >
        {url}
      </a>
    );
  }

  return <Card data={preview} href={url} testId={testId} />;
}
