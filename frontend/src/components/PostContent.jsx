/**
 * iter211 — PostContent: renders a post's body with inline URL parsing.
 *
 * Fixes:
 *   • Bug 1: YouTube / Vimeo URLs auto-expand to iframe embeds inside the
 *     feed, no page navigation required.
 *   • iter212: Generic http(s) URLs render a rich Open Graph link card
 *     (LinkedIn-style) below the post text — fetched from `/api/og`.
 *
 * Usage:
 *   <PostContent text={post.text} testIdPrefix={`post-${post.post_id}`} />
 */
import { useState } from 'react';
import { parsePostText, youtubeEmbedUrl, youtubeThumbnailUrl, vimeoEmbedUrl } from '@/utils/linkParser';
import LinkPreviewCard from '@/components/LinkPreviewCard';
import renderRichText from '@/utils/renderRichText';
import renderRichHtml, { isRichHtml } from '@/utils/renderRichHtml';
import { Play } from '@phosphor-icons/react';

function YouTubeEmbed({ videoId, url, testId }) {
  const [activated, setActivated] = useState(false);

  // Lazy-activation: show a thumbnail + play button first. Only swap to
  // the iframe when the user clicks — saves ~300KB of YT player JS per
  // post and respects data-saver users.
  if (!activated) {
    return (
      <button
        type="button"
        onClick={() => setActivated(true)}
        data-testid={testId}
        aria-label="Lire la vidéo YouTube"
        className="relative block w-full overflow-hidden rounded-xl group"
        style={{
          aspectRatio: '16 / 9',
          background: '#000',
          marginTop: 8,
        }}
      >
        <img
          src={youtubeThumbnailUrl(videoId)}
          alt=""
          loading="lazy"
          className="w-full h-full object-cover opacity-90 group-hover:opacity-100 transition-opacity"
        />
        <div
          className="absolute inset-0 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.2)' }}
        >
          <div
            className="flex items-center justify-center shadow-xl"
            style={{
              width: 62,
              height: 62,
              borderRadius: '50%',
              background: '#E01C2E',
              color: '#fff',
            }}
          >
            <Play size={28} weight="fill" />
          </div>
        </div>
        <div
          className="absolute top-2 left-2 text-[10px] font-bold px-2 py-1 rounded-full"
          style={{ background: 'rgba(0,0,0,0.7)', color: '#fff', letterSpacing: 0.5 }}
        >
          YOUTUBE
        </div>
      </button>
    );
  }
  return (
    <div
      className="relative overflow-hidden rounded-xl"
      style={{ aspectRatio: '16 / 9', background: '#000', marginTop: 8 }}
      data-testid={`${testId}-iframe`}
    >
      <iframe
        src={youtubeEmbedUrl(videoId) + '&autoplay=1'}
        title={`YouTube video ${videoId}`}
        className="absolute inset-0 w-full h-full"
        loading="lazy"
        frameBorder="0"
        allowFullScreen
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
      />
    </div>
  );
}

function VimeoEmbed({ videoId, testId }) {
  return (
    <div
      className="relative overflow-hidden rounded-xl"
      style={{ aspectRatio: '16 / 9', background: '#000', marginTop: 8 }}
      data-testid={testId}
    >
      <iframe
        src={vimeoEmbedUrl(videoId)}
        title={`Vimeo video ${videoId}`}
        className="absolute inset-0 w-full h-full"
        loading="lazy"
        frameBorder="0"
        allowFullScreen
        allow="autoplay; fullscreen; picture-in-picture"
      />
    </div>
  );
}


export default function PostContent({ text, testIdPrefix = 'post-content' }) {
  if (!text) return null;

  // iter237r — New posts are HTML produced by the WYSIWYG composer
  // (RichTextEditor). Detect that shape and render it via the
  // sanitized HTML pipeline (DOMPurify + clickable hashtags/mentions).
  // Old posts (Markdown ** _ <u>) keep working through the legacy
  // renderRichText pipeline below — backward-compat preserved.
  if (isRichHtml(text)) {
    return (
      <div
        className="text-sm font-['Manrope'] px-4 mb-2 break-words jp-rte-rendered"
        style={{ color: 'var(--jp-text)' }}
        data-testid={`${testIdPrefix}-text`}
        data-rendered="rich-html"
      >
        {renderRichHtml(text, { keyPrefix: testIdPrefix })}
      </div>
    );
  }

  const tokens = parsePostText(text);

  // Detect the first generic link → rendered as rich OG card below.
  // (Max 1 link card per post to keep the feed light and avoid noise.)
  const firstLinkIdx = tokens.findIndex(t => t.type === 'link');
  const firstLinkUrl = firstLinkIdx >= 0 ? tokens[firstLinkIdx].url : null;

  // First pass: render the text with inline links preserved.
  const inline = tokens.map((tok, i) => {
    if (tok.type === 'text') {
      // iter237q — Pipe plain text through the rich-text renderer to
      // upgrade Markdown light (**bold**, _italic_, <u>underline</u>)
      // and turn #hashtags / @mentions into router links.
      return (
        <span key={`t${i}`}>
          {renderRichText(tok.content, { keyPrefix: `${testIdPrefix}-rt-${i}` })}
        </span>
      );
    }
    if (tok.type === 'link') {
      // The FIRST generic link is promoted to a rich card; hide it inline
      // to avoid the ugly "raw URL + card" duplication seen on lesser
      // apps. Any subsequent link remains inline-clickable.
      if (i === firstLinkIdx) return null;
      return (
        <a
          key={`l${i}`}
          href={tok.url}
          target="_blank"
          rel="noreferrer noopener"
          style={{ color: 'var(--jp-primary)', textDecoration: 'underline', wordBreak: 'break-all' }}
          data-testid={`${testIdPrefix}-link-${i}`}
        >
          {tok.url}
        </a>
      );
    }
    // YouTube / Vimeo URLs are NOT shown as text inline — they become
    // embeds below. Return null to skip.
    return null;
  });

  // Second pass: collect all embeds (max 3 per post to keep feed light).
  const embeds = tokens
    .filter(t => t.type === 'youtube' || t.type === 'vimeo')
    .slice(0, 3);

  return (
    <>
      <p
        className="text-sm font-['Manrope'] px-4 mb-2 break-words"
        style={{ color: 'var(--jp-text)' }}
        data-testid={`${testIdPrefix}-text`}
      >
        {inline}
      </p>
      {embeds.length > 0 && (
        <div className="px-4 space-y-2" data-testid={`${testIdPrefix}-embeds`}>
          {embeds.map((tok, i) => {
            if (tok.type === 'youtube') {
              return (
                <YouTubeEmbed
                  key={`yt${i}`}
                  videoId={tok.videoId}
                  url={tok.url}
                  testId={`${testIdPrefix}-youtube-${i}`}
                />
              );
            }
            return (
              <VimeoEmbed
                key={`vm${i}`}
                videoId={tok.videoId}
                testId={`${testIdPrefix}-vimeo-${i}`}
              />
            );
          })}
        </div>
      )}
      {/* iter212 — OG link preview (one max, skipped if a video embed already renders). */}
      {firstLinkUrl && embeds.length === 0 && (
        <div className="px-4" data-testid={`${testIdPrefix}-linkpreview`}>
          <LinkPreviewCard url={firstLinkUrl} testId={`${testIdPrefix}-linkpreview-card`} />
        </div>
      )}
    </>
  );
}
