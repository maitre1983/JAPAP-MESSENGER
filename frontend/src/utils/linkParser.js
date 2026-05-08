/**
 * iter211 — URL auto-rendering for Feed posts.
 *
 * Detects YouTube, Vimeo and generic HTTP links inside a piece of user-
 * authored text and returns a parsed representation that the caller can
 * render as embeds / link cards.
 *
 * CEO spec (bug 1): YouTube URLs in posts must render as iframe embeds.
 */

// Matches: youtube.com/watch?v=ID, youtu.be/ID, youtube.com/shorts/ID
// (11 chars, alphanumeric + _ + -)
export const YOUTUBE_REGEX =
  /(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/)([A-Za-z0-9_-]{11})(?:[?&][^\s]*)?/i;

export const YOUTUBE_REGEX_GLOBAL =
  /(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/)([A-Za-z0-9_-]{11})(?:[?&][^\s]*)?/gi;

// Matches: vimeo.com/123456789
export const VIMEO_REGEX =
  /(?:https?:\/\/)?(?:www\.|player\.)?vimeo\.com\/(?:video\/)?(\d{6,})/i;

export const VIMEO_REGEX_GLOBAL =
  /(?:https?:\/\/)?(?:www\.|player\.)?vimeo\.com\/(?:video\/)?(\d{6,})/gi;

// Any http(s) URL — must be tested AFTER the video regexes so video URLs
// don't fall into the generic link-card branch.
export const URL_REGEX_GLOBAL = /(https?:\/\/[^\s]+)/gi;


export function extractYoutubeId(url) {
  if (!url) return null;
  const m = String(url).match(YOUTUBE_REGEX);
  return m ? m[1] : null;
}

export function extractVimeoId(url) {
  if (!url) return null;
  const m = String(url).match(VIMEO_REGEX);
  return m ? m[1] : null;
}


/**
 * Parse a post's text and return an ordered list of tokens ready for
 * React rendering.
 *
 * Each token is one of:
 *   { type: 'text',    content: string }
 *   { type: 'youtube', url: string, videoId: string }
 *   { type: 'vimeo',   url: string, videoId: string }
 *   { type: 'link',    url: string }
 *
 * Example:
 *   parsePostText('Hey https://youtu.be/dQw4w9WgXcQ check this out')
 *   → [
 *       { type: 'text', content: 'Hey ' },
 *       { type: 'youtube', url: 'https://youtu.be/dQw4w9WgXcQ', videoId: 'dQw4w9WgXcQ' },
 *       { type: 'text', content: ' check this out' },
 *     ]
 */
export function parsePostText(text) {
  if (!text || typeof text !== 'string') return [];
  const tokens = [];
  // Split on any URL — keeps the URL token via the capture group.
  const parts = text.split(URL_REGEX_GLOBAL);
  for (const part of parts) {
    if (!part) continue;
    if (!/^https?:\/\//i.test(part)) {
      tokens.push({ type: 'text', content: part });
      continue;
    }
    // It IS a URL — classify it.
    const ytId = extractYoutubeId(part);
    if (ytId) {
      tokens.push({ type: 'youtube', url: part, videoId: ytId });
      continue;
    }
    const vId = extractVimeoId(part);
    if (vId) {
      tokens.push({ type: 'vimeo', url: part, videoId: vId });
      continue;
    }
    tokens.push({ type: 'link', url: part });
  }
  return tokens;
}


export function youtubeEmbedUrl(videoId) {
  // `rel=0` restricts related videos to the same channel.
  // `modestbranding=1` removes the YouTube logo (ignored by newer player
  // but harmless). `playsinline=1` prevents iOS fullscreen hijack.
  return `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?rel=0&modestbranding=1&playsinline=1`;
}

export function vimeoEmbedUrl(videoId) {
  return `https://player.vimeo.com/video/${encodeURIComponent(videoId)}?title=0&byline=0&portrait=0`;
}

export function youtubeThumbnailUrl(videoId) {
  // hqdefault is always available; maxresdefault is best-effort and may 404.
  return `https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/hqdefault.jpg`;
}
