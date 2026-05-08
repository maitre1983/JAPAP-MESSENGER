/**
 * iter237q — renderRichText: light Markdown + hashtag + mention rendering.
 *
 * Designed to plug INSIDE the existing token-based parser of PostContent
 * (linkParser.js): each `text` token is fed to this util, which returns
 * an array of React nodes preserving:
 *
 *   • **bold**       → <strong>
 *   • _italic_       → <em>
 *   • <u>underline</u> → <u>
 *   • #hashtag       → <Link to="/explore?tag=hashtag">
 *   • @username      → <Link to="/profile/username">
 *
 * Notes :
 *   • Pas de `dangerouslySetInnerHTML` — tout est composé en pur React,
 *     donc immune aux injections XSS. Les `<u>` sont parsés manuellement
 *     (regex narrow), pas exécutés.
 *   • Un seul passage linéaire — pas de coût quadratique.
 *   • Les wrappers Markdown peuvent être imbriqués (ex. `**_gras+italique_**`).
 *   • Stable : retourne toujours un tableau React-keyable.
 *
 * Usage :
 *   {renderRichText(text, { keyPrefix: 'p-123-t0' })}
 */
import { Link } from 'react-router-dom';

// Regex globale qui capture, dans l'ordre :
//   1. Markdown bold        : **xxx**
//   2. Markdown italic      : _xxx_
//   3. HTML underline       : <u>xxx</u>
//   4. Hashtag              : #word
//   5. Mention              : @word
const TOKEN_RX = /(\*\*([^*\n]+?)\*\*|_([^_\n]+?)_|<u>([\s\S]+?)<\/u>|(?:^|\s)(#[\p{L}0-9_]{1,40})|(?:^|\s)(@[\p{L}0-9_.]{1,40}))/gu;

export default function renderRichText(text, { keyPrefix = 'rt' } = {}) {
  if (!text) return [];

  const nodes = [];
  let lastIndex = 0;
  let i = 0;
  let m;

  while ((m = TOKEN_RX.exec(text)) !== null) {
    // Push the plain text before the match (preserving leading whitespace
    // for hashtag/mention which need a separator).
    if (m.index > lastIndex) {
      nodes.push(<span key={`${keyPrefix}-t-${i++}`}>{text.slice(lastIndex, m.index)}</span>);
    }

    const [whole, , bold, italic, underline, hashtag, mention] = m;

    if (bold !== undefined) {
      nodes.push(
        <strong key={`${keyPrefix}-b-${i++}`} style={{ fontWeight: 700 }}>
          {bold}
        </strong>
      );
    } else if (italic !== undefined) {
      nodes.push(
        <em key={`${keyPrefix}-i-${i++}`}>{italic}</em>
      );
    } else if (underline !== undefined) {
      nodes.push(
        <u key={`${keyPrefix}-u-${i++}`}>{underline}</u>
      );
    } else if (hashtag !== undefined) {
      // The regex captured optional leading whitespace inside the match;
      // preserve it before the link so spacing remains natural.
      const lead = whole.slice(0, whole.indexOf('#'));
      const tag = hashtag.slice(1);
      nodes.push(
        <span key={`${keyPrefix}-hpre-${i}`}>{lead}</span>
      );
      nodes.push(
        <Link
          key={`${keyPrefix}-h-${i++}`}
          to={`/explore?tag=${encodeURIComponent(tag)}`}
          style={{ color: 'var(--jp-primary)', fontWeight: 600 }}
          data-testid={`post-hashtag-${tag}`}
        >
          #{tag}
        </Link>
      );
    } else if (mention !== undefined) {
      const lead = whole.slice(0, whole.indexOf('@'));
      const user = mention.slice(1);
      nodes.push(<span key={`${keyPrefix}-mpre-${i}`}>{lead}</span>);
      nodes.push(
        <Link
          key={`${keyPrefix}-m-${i++}`}
          to={`/profile/${encodeURIComponent(user)}`}
          style={{ color: 'var(--jp-primary)', fontWeight: 600 }}
          data-testid={`post-mention-${user}`}
        >
          @{user}
        </Link>
      );
    }

    lastIndex = m.index + whole.length;
  }

  // Trailing plain text.
  if (lastIndex < text.length) {
    nodes.push(<span key={`${keyPrefix}-t-${i++}`}>{text.slice(lastIndex)}</span>);
  }

  return nodes;
}
