/**
 * PostDetailPage — shareable single-post view reachable via /post/:postId.
 *
 * Keeps the rendering surface tiny and delegates comments to the same
 * <CommentSection/> the feed uses. The page is ProtectedRoute-wrapped, so
 * anonymous clicks on external-share links hit /login first and land on
 * /feed after auth — acceptable for v1; a deep-link-preserving auth flow
 * can be layered on later without changing this surface.
 */
import { useState, useEffect } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import axios from 'axios';
import { ArrowLeft, Heart, ChatCircle, ShareNetwork, User as UserIcon } from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import CommentSection from '@/components/feed/CommentSection';
// iter239d — Pro video player (replaces native <video controls />).
import VideoPlayer from '@/components/VideoPlayer';
import FollowSuggestions from '@/components/social/FollowSuggestions';
import { SharePostModal } from '@/components/PostMenu';

const API = process.env.REACT_APP_BACKEND_URL;

function timeAgo(iso) {
  try {
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return "À l'instant";
    if (diff < 3600) return `${Math.floor(diff / 60)} min`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} h`;
    return `${Math.floor(diff / 86400)} j`;
  } catch { return ''; }
}

export default function PostDetailPage() {
  const { postId } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [post, setPost] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [sharing, setSharing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setNotFound(false);
      try {
        const { data } = await axios.get(`${API}/api/feed/posts/${postId}`, { withCredentials: true });
        if (!cancelled) setPost(data);
      } catch (e) {
        if (!cancelled) setNotFound(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [postId]);

  const toggleLike = async () => {
    if (!post) return;
    const prevLiked = post.is_liked;
    const prevCount = post.likes_count || 0;
    // Optimistic flip
    setPost({ ...post, is_liked: !prevLiked, likes_count: prevLiked ? Math.max(0, prevCount - 1) : prevCount + 1 });
    try {
      await axios.post(`${API}/api/feed/posts/${post.post_id}/like`, {}, { withCredentials: true });
    } catch {
      setPost({ ...post, is_liked: prevLiked, likes_count: prevCount });
    }
  };

  const bumpCommentCount = (delta) =>
    setPost(p => p ? { ...p, comments_count: Math.max(0, (p.comments_count || 0) + delta) } : p);

  if (loading) {
    return (
      <div className="max-w-xl mx-auto px-4 py-8" data-testid="post-detail-loading">
        <div className="h-48 rounded-2xl animate-pulse" style={{ background: 'var(--jp-surface-secondary)' }} />
      </div>
    );
  }

  if (notFound || !post) {
    return (
      <div className="max-w-xl mx-auto px-4 py-8 text-center" data-testid="post-detail-not-found">
        <p className="font-['Manrope'] text-sm" style={{ color: 'var(--jp-text-muted)' }}>
          Cette publication est introuvable ou a été supprimée.
        </p>
        <Link to="/feed" className="jp-btn jp-btn-primary inline-flex mt-4" data-testid="post-detail-back-to-feed">
          <ArrowLeft size={14} /> Retour au feed
        </Link>
      </div>
    );
  }

  const media = Array.isArray(post.media) ? post.media : [];
  const authorName = `${post.first_name || ''} ${post.last_name || ''}`.trim() || post.username || 'Utilisateur';

  return (
    <div className="max-w-xl mx-auto px-4 py-4" data-testid="post-detail-page">
      <button onClick={() => navigate(-1)} data-testid="post-detail-back"
        className="inline-flex items-center gap-1.5 text-sm font-['Manrope'] font-semibold mb-3"
        style={{ color: 'var(--jp-text-secondary)' }}>
        <ArrowLeft size={16} /> Retour
      </button>

      <article className="jp-card overflow-hidden">
        {/* Header */}
        <header className="flex items-center gap-2.5 px-3 py-2.5">
          <div className="w-10 h-10 rounded-full overflow-hidden flex items-center justify-center"
            style={{ background: 'var(--jp-surface-secondary)' }}>
            {post.avatar ? (
              <img src={post.avatar} alt="" className="w-full h-full object-cover" />
            ) : (
              <UserIcon size={18} style={{ color: 'var(--jp-text-muted)' }} />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-['Outfit'] font-bold truncate">{authorName}</p>
            <p className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
              {timeAgo(post.created_at)}
            </p>
          </div>
        </header>

        {post.text && (
          <p className="px-3 pb-2 text-sm font-['Manrope'] whitespace-pre-wrap break-words"
            style={{ color: 'var(--jp-text)' }}>{post.text}</p>
        )}

        {media.length > 0 && (
          <div className="grid grid-cols-1 gap-1">
            {media.map((m, i) => {
              const url = typeof m === 'string' ? m : m?.url;
              if (!url) return null;
              const type = typeof m === 'object' ? m?.type : null;
              const isVideo = type === 'video' || /\.(mp4|mov|webm)$/i.test(url);
              const src = url.startsWith('http') ? url : `${API}${url.startsWith('/') ? '' : '/'}${url}`;
              return isVideo
                ? <VideoPlayer key={i} videoUrl={src}
                    thumbnailUrl={typeof m === 'object' ? (m?.thumbnail_url || m?.thumbnailUrl) : undefined}
                    autoplay={false} muted loop={false}
                    aspectRatio="16/9" testId={`post-video-${i}`} />
                : <img key={i} src={src} alt=""
                    loading="lazy" decoding="async"
                    className="w-full max-h-96 object-cover"
                    onError={(e) => { e.currentTarget.style.display = 'none'; }} />;
            })}
          </div>
        )}

        <div className="flex items-center px-2 py-2 border-t" style={{ borderColor: 'var(--jp-border)' }}>
          <button onClick={toggleLike} data-testid={`detail-like-${post.post_id}`}
            className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-['Manrope'] font-medium"
            style={{ color: post.is_liked ? '#E01C2E' : 'var(--jp-text-secondary)' }}>
            <Heart size={16} weight={post.is_liked ? 'fill' : 'regular'} /> {post.likes_count || 0}
          </button>
          <button
            className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-['Manrope']"
            style={{ color: 'var(--jp-primary)' }}>
            <ChatCircle size={16} weight="fill" /> {post.comments_count || 0}
          </button>
          <button onClick={() => setSharing(true)} data-testid={`detail-share-${post.post_id}`}
            className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-['Manrope']"
            style={{ color: 'var(--jp-text-secondary)' }}>
            <ShareNetwork size={16} /> {post.shares_count || 0}
          </button>
        </div>

        <CommentSection
          postId={post.post_id}
          currentUser={user}
          onCountChange={bumpCommentCount}
        />
      </article>

      {/* ══ Follow Suggestions — viral engagement loop for deep-link traffic ══ */}
      <FollowSuggestions limit={3} />

      {sharing && (
        <SharePostModal
          post={post}
          onClose={() => setSharing(false)}
          onShared={() => setPost(p => p ? { ...p, shares_count: (p.shares_count || 0) + 1 } : p)}
        />
      )}
    </div>
  );
}
