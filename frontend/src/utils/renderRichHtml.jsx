/**
 * iter237r — renderRichHtml: render sanitized post HTML produced by the
 * WYSIWYG composer (RichTextEditor.jsx).
 *
 * Pipeline :
 *   1. Sanitize (DOMPurify) — whitelist of inline tags only (b, strong,
 *      i, em, u, br). NEVER rendered as raw HTML before this step.
 *   2. Walk the resulting DOM tree.
 *   3. For each text node, run a regex pass that turns #hashtag and
 *      @mention into <Link to="/explore?tag=…"> / <Link to="/profile/…">.
 *   4. Return a React tree (composed elements only — no
 *      `dangerouslySetInnerHTML` past sanitization).
 *
 * Sécurité :
 *   - Whitelist DOMPurify ultra-stricte. Pas d'attribut HTML accepté.
 *   - Le post-process des hashtags/mentions est un text-node walker —
 *     impossible d'injecter du HTML par ce biais.
 *
 * Heuristique `isRichHtml(text)` : True si la chaîne contient une des
 * balises whitelistées en ouverture/fermeture. Sinon → on suppose du
 * texte brut / Markdown léger (rétrocompatibilité posts antérieurs).
 */
import DOMPurify from 'dompurify';
import { Fragment } from 'react';
import { Link } from 'react-router-dom';

const ALLOWED_TAGS = ['b', 'strong', 'i', 'em', 'u', 'br'];

const RICH_HTML_TEST = /<(b|strong|i|em|u|br)\s*\/?>/i;

export function isRichHtml(text) {
  return typeof text === 'string' && RICH_HTML_TEST.test(text);
}

function sanitize(html) {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: [],
    KEEP_CONTENT: true,
  });
}

// Hashtag / mention text-node decorator. Pure-React composition; never
// returns raw HTML strings.
const HM_RX = /(?:^|(?<=\s))(#[\p{L}0-9_]{1,40})|(?:^|(?<=\s))(@[\p{L}0-9_.]{1,40})/gu;

function decorateText(text, keyPrefix) {
  const out = [];
  let lastIndex = 0;
  let i = 0;
  let m;
  HM_RX.lastIndex = 0;
  while ((m = HM_RX.exec(text)) !== null) {
    if (m.index > lastIndex) {
      out.push(<Fragment key={`${keyPrefix}-tx-${i++}`}>{text.slice(lastIndex, m.index)}</Fragment>);
    }
    if (m[1]) {
      const tag = m[1].slice(1);
      out.push(
        <Link
          key={`${keyPrefix}-h-${i++}`}
          to={`/explore?tag=${encodeURIComponent(tag)}`}
          style={{ color: 'var(--jp-primary)', fontWeight: 600 }}
          data-testid={`post-hashtag-${tag}`}
        >
          #{tag}
        </Link>
      );
    } else if (m[2]) {
      const u = m[2].slice(1);
      out.push(
        <Link
          key={`${keyPrefix}-m-${i++}`}
          to={`/profile/${encodeURIComponent(u)}`}
          style={{ color: 'var(--jp-primary)', fontWeight: 600 }}
          data-testid={`post-mention-${u}`}
        >
          @{u}
        </Link>
      );
    }
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < text.length) {
    out.push(<Fragment key={`${keyPrefix}-tx-${i++}`}>{text.slice(lastIndex)}</Fragment>);
  }
  return out.length ? out : text;
}

// Recursively render a DOM node into React elements. Only whitelisted
// tags are honored; the rest are dropped (their text content kept).
function renderNode(node, keyPrefix, idx) {
  if (node.nodeType === Node.TEXT_NODE) {
    return (
      <Fragment key={`${keyPrefix}-t-${idx}`}>
        {decorateText(node.nodeValue || '', `${keyPrefix}-${idx}`)}
      </Fragment>
    );
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return null;
  const tag = node.tagName.toLowerCase();
  if (!ALLOWED_TAGS.includes(tag)) return null;
  if (tag === 'br') return <br key={`${keyPrefix}-br-${idx}`} />;
  const children = Array.from(node.childNodes).map((c, j) =>
    renderNode(c, `${keyPrefix}-${idx}`, j)
  );
  // Map old presentational tags onto semantic equivalents.
  const Tag = tag === 'b' ? 'strong' : tag === 'i' ? 'em' : tag;
  return <Tag key={`${keyPrefix}-${tag}-${idx}`}>{children}</Tag>;
}

export default function renderRichHtml(html, { keyPrefix = 'rh' } = {}) {
  const safe = sanitize(html || '');
  // Use the browser's parser (server-side rendering would need a polyfill,
  // but Japap is fully client-rendered).
  const doc = new DOMParser().parseFromString(`<div>${safe}</div>`, 'text/html');
  const root = doc.body.firstChild;
  if (!root) return null;
  return Array.from(root.childNodes).map((n, i) => renderNode(n, keyPrefix, i));
}
