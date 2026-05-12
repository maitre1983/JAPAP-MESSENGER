import { useEffect, useRef, useState, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { useAuth } from '@/context/AuthContext';
import { Heart, ChatCircle, ShareNetwork, HandCoins, X, Plus, CaretLeft, SpeakerHigh, SpeakerSlash, VideoCamera, PaperPlaneRight } from '@phosphor-icons/react';
import AdSlot from '@/components/AdSlot';
// iter239h — Pro VideoPlayer (autoplay on view, click-to-pause, fullscreen).
import VideoPlayer from '@/components/VideoPlayer';
import { pollVideoJobWithToast } from '@/utils/videoUploadPoll';
import uploadErrorMessage from '@/utils/uploadErrorMessage';
import UploadProgressButton from '@/components/UploadProgressButton';
import { useTranslation } from 'react-i18next';

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

/**
 * iter215 — ReelCommentsDrawer
 * Bottom-sheet drawer that opens on tap of the comment icon next to a
 * reel. Lazy-loads `/api/feed/reels/{reel_id}/comments` on first open;
 * posts new ones via POST on the same path. Optimistic count update is
 * handled by the parent (ReelsPage) so the icon counter feels instant.
 */
function ReelCommentsDrawer({ reelId, onClose, onNewCount, currentUser }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const inputRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(
        `${API}/api/feed/reels/${reelId}/comments`,
        { withCredentials: true }
      );
      setItems(Array.isArray(data) ? data : []);
    } finally { setLoading(false); }
  }, [reelId]);

  useEffect(() => { load(); }, [load]);

  const submit = async (e) => {
    e?.preventDefault?.();
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const { data } = await axios.post(
        `${API}/api/feed/reels/${reelId}/comments`,
        { text },
        { withCredentials: true }
      );
      setItems(prev => [...prev, data]);
      setDraft('');
      onNewCount?.(items.length + 1);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    } finally { setSending(false); }
  };

  return (
    <div
      className="fixed inset-0 z-[110]"
      data-testid={`reel-comments-drawer-${reelId}`}
    >
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div
        className="absolute left-0 right-0 bottom-0 bg-white rounded-t-3xl flex flex-col"
        style={{
          maxHeight: '70vh',
          minHeight: '40vh',
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
        }}
      >
        <div className="flex items-center justify-between px-4 pt-3 pb-2 border-b" style={{ borderColor: 'var(--jp-border)' }}>
          <h3 className="font-['Outfit'] font-bold text-base">
            Commentaires {items.length > 0 && <span className="text-xs font-normal text-[var(--jp-text-muted)]">· {items.length}</span>}
          </h3>
          <button onClick={onClose} className="p-1" aria-label="Fermer" data-testid="reel-comments-close">
            <X size={20} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {loading ? (
            <div className="text-center text-sm text-[var(--jp-text-muted)] py-6">Chargement…</div>
          ) : items.length === 0 ? (
            <div className="text-center text-sm text-[var(--jp-text-muted)] py-6">Sois le premier à commenter ce Reel.</div>
          ) : items.map(c => (
            <div key={c.comment_id} className="flex gap-3" data-testid={`reel-comment-${c.comment_id}`}>
              <div className="w-8 h-8 rounded-full bg-[var(--jp-surface-secondary)] flex-shrink-0 overflow-hidden flex items-center justify-center text-[10px] font-bold">
                {c.avatar
                  ? <img src={c.avatar.startsWith('http') ? c.avatar : `${API}${c.avatar}`} alt="" className="w-full h-full object-cover" onError={(e)=>{e.currentTarget.style.display='none';}} />
                  : (c.first_name?.[0] || c.username?.[0] || '?').toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-xs">
                  <span className="font-bold">@{c.username || c.first_name}</span>
                  <span className="ml-2 text-[10px] text-[var(--jp-text-muted)]">{timeAgo(c.created_at)}</span>
                </div>
                <p className="text-sm break-words mt-0.5">{c.text}</p>
              </div>
            </div>
          ))}
        </div>
        <form onSubmit={submit} className="border-t px-3 py-2 flex gap-2 items-end" style={{ borderColor: 'var(--jp-border)' }}>
          <input
            ref={inputRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            placeholder={currentUser ? 'Écris un commentaire…' : 'Connecte-toi pour commenter'}
            disabled={!currentUser || sending}
            className="jp-input text-sm flex-1"
            data-testid={`reel-comment-input-${reelId}`}
          />
          <button
            type="submit"
            disabled={!currentUser || !draft.trim() || sending}
            className="p-2 rounded-full"
            style={{ background: '#E01C2E', color: '#fff', opacity: (!currentUser || !draft.trim() || sending) ? 0.4 : 1 }}
            data-testid={`reel-comment-send-${reelId}`}
            aria-label="Envoyer"
          >
            <PaperPlaneRight size={18} weight="fill" />
          </button>
        </form>
      </div>
    </div>
  );
}

export default function ReelsPage() {
  const { t } = useTranslation();
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();
  // iter217 — deep-link: /reels/:reelId scrolls to the matching reel on
  // first load. We only honour it once so subsequent in-page swipes
  // don't trigger the snap-to-target behaviour.
  const { reelId: deepLinkReelId } = useParams();
  const deepLinkConsumed = useRef(false);
  const [reels, setReels] = useState([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [muted, setMuted] = useState(true);
  const [tipTarget, setTipTarget] = useState(null);
  const [tipAmount, setTipAmount] = useState('');
  const [tipError, setTipError] = useState('');
  const [tipSending, setTipSending] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [createForm, setCreateForm] = useState({ file: null, caption: '', music_title: '' });
  const [createError, setCreateError] = useState('');
  // iter220 — granular upload progress + phase indicator so users
  // get instant feedback on slow 3G/4G connections (data shows the
  // bounce rate plummets when a real progress bar is shown vs a
  // generic "Publication…" toast). Phases: 'upload' | 'processing' | 'publishing'.
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadPhase, setUploadPhase] = useState('idle');
  // iter215 — comments drawer
  const [commentsOpenFor, setCommentsOpenFor] = useState(null);
  const containerRef = useRef(null);
  const videoRefs = useRef({});

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/feed/reels?page=1&limit=20`, { withCredentials: true });
      let list = data;
      // iter217 — if we landed via /reels/{deepLinkReelId} and that
      // reel isn't in the first page, prepend it from a direct fetch
      // so the user instantly sees it at the top of the snap stack.
      if (deepLinkReelId && !deepLinkConsumed.current) {
        const present = list.find(r => r.reel_id === deepLinkReelId);
        if (!present) {
          try {
            const { data: one } = await axios.get(
              `${API}/api/feed/reels/${deepLinkReelId}`, { withCredentials: true });
            if (one?.reel_id) list = [one, ...list];
          } catch {}
        } else {
          // Reel already in page 1 → move to index 0 for instant deep-link.
          list = [present, ...list.filter(r => r.reel_id !== deepLinkReelId)];
        }
      }
      setReels(list);
    } catch {}
  }, [deepLinkReelId]);

  useEffect(() => { load(); }, [load]);

  // iter217 — once the list is hydrated, snap-scroll to the deep-linked
  // reel's slot (index 0 by construction above) on first paint only.
  useEffect(() => {
    if (deepLinkConsumed.current || !deepLinkReelId || reels.length === 0) return;
    const el = containerRef.current;
    if (!el) return;
    const idx = reels.findIndex(r => r.reel_id === deepLinkReelId);
    if (idx >= 0) {
      el.scrollTo({ top: idx * window.innerHeight, behavior: 'instant' });
      setActiveIdx(idx);
    }
    deepLinkConsumed.current = true;
  }, [reels, deepLinkReelId]);

  // Autoplay the visible reel, pause others, track view
  useEffect(() => {
    Object.entries(videoRefs.current).forEach(([i, v]) => {
      if (!v) return;
      if (Number(i) === activeIdx) {
        v.play().catch(() => {});
        v.muted = muted;
      } else {
        v.pause();
        v.currentTime = 0;
      }
    });
    const r = reels[activeIdx];
    if (r) {
      axios.post(`${API}/api/feed/reels/${r.reel_id}/view`, {}, { withCredentials: true }).catch(() => {});
    }
  }, [activeIdx, reels, muted]);

  // Snap scroll: detect which reel is visible
  const onScroll = () => {
    if (!containerRef.current) return;
    const h = window.innerHeight;
    const idx = Math.round(containerRef.current.scrollTop / h);
    if (idx !== activeIdx && idx >= 0 && idx < reels.length) setActiveIdx(idx);
  };

  const toggleLike = async (reel) => {
    // iter215 — optimistic UI: bump counter + heart fill instantly,
    // then reconcile with server response (or rollback on error).
    const wasLiked = !!reel.is_liked;
    setReels(rs => rs.map(r => r.reel_id === reel.reel_id
      ? { ...r, is_liked: !wasLiked, likes_count: r.likes_count + (wasLiked ? -1 : 1) }
      : r));
    try {
      const { data } = await axios.post(`${API}/api/feed/reels/${reel.reel_id}/like`, {}, { withCredentials: true });
      setReels(rs => rs.map(r => r.reel_id === reel.reel_id
        ? { ...r, is_liked: data.liked, likes_count: r.likes_count + (data.liked === wasLiked ? (data.liked ? 1 : -1) : 0) }
        : r));
    } catch {
      // rollback
      setReels(rs => rs.map(r => r.reel_id === reel.reel_id
        ? { ...r, is_liked: wasLiked, likes_count: r.likes_count + (wasLiked ? 1 : -1) }
        : r));
    }
  };

  /** iter215 — open comments bottom-sheet. Lazy-loaded inside drawer. */
  const openComments = (reel) => setCommentsOpenFor(reel.reel_id);

  /** iter215 — share via Web Share API on iOS/Android, fallback to
   *  clipboard copy + toast on desktop / unsupported browsers /
   *  Playwright-style headless contexts. The shared URL deep-links
   *  into the public reel page so the recipient can play it without
   *  an account. We also bump the local counter optimistically.
   *
   *  Robustness (iter215.1 — fixes testing agent finding):
   *  - try navigator.share FIRST; on AbortError → user cancelled, exit silently.
   *  - on ANY OTHER error → fall through to clipboard.
   *  - if clipboard fails too → last-ditch window.prompt.
   *  - fire backend POST + optimistic increment ONCE the user-visible
   *    action succeeded (any of the 3 paths above).
   */
  const shareReel = async (reel) => {
    // iter217 — share the OG-preview URL so WhatsApp / iMessage /
    // Twitter / Discord / Telegram scrapers can fetch og:title +
    // og:description + og:image (thumbnail) + og:video (player card)
    // and render an inline preview. Real users hit the meta refresh
    // and land on /reels/{reel_id} in the SPA via React Router.
    const url = `${window.location.origin}/api/og/reel/${reel.reel_id}`;
    const shareData = { title: 'JAPAP Reel', text: reel.caption ? `${reel.caption}\n\n` : '', url };

    let success = false;

    // Path 1: native Web Share (iOS, Android, modern PWAs)
    if (navigator.share && navigator.canShare?.(shareData) !== false) {
      try {
        await navigator.share(shareData);
        success = true;
      } catch (e) {
        if (e?.name === 'AbortError') return; // user cancelled
        // any other error (NotAllowedError, SecurityError, TypeError…)
        // → silently fall through to clipboard fallback below.
      }
    }

    // Path 2: clipboard
    if (!success && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(url);
        toast.success('Lien copié dans le presse-papier');
        success = true;
      } catch {
        // last-ditch fallback below
      }
    }

    // Path 3: prompt (manual copy)
    if (!success) {
      try {
        window.prompt('Copie ce lien :', url);
        success = true;
      } catch {
        toast.error('Échec du partage');
        return;
      }
    }

    // Optimistic UI bump + best-effort backend tracking.
    setReels(rs => rs.map(r => r.reel_id === reel.reel_id
      ? { ...r, shares_count: (r.shares_count || 0) + 1 }
      : r));
    axios.post(`${API}/api/feed/reels/${reel.reel_id}/share`, {}, { withCredentials: true })
      .catch(() => {});
  };

  const sendTip = async () => {
    setTipError('');
    const amt = parseFloat(tipAmount);
    if (!amt || amt < 50) { setTipError('Minimum 50 XAF'); return; }
    setTipSending(true);
    try {
      await axios.post(`${API}/api/feed/tip`, {
        target_type: 'reel', target_id: tipTarget.reel_id, amount: amt,
      }, { withCredentials: true });
      setTipTarget(null); setTipAmount('');
      refreshUser(); load();
    } catch (err) {
      setTipError(err.response?.data?.detail || 'Erreur');
    } finally {
      setTipSending(false);
    }
  };

  const handleCreateReel = async (e) => {
    e.preventDefault(); setCreateError('');
    if (!createForm.file) { setCreateError('Vidéo requise'); return; }
    setUploading(true);
    setUploadProgress(0);
    setUploadPhase('upload');
    try {
      const fd = new FormData();
      fd.append('file', createForm.file);
      const { data: u } = await axios.post(`${API}/api/upload/`, fd, {
        withCredentials: true,
        // iter220 — Real upload progress. Browser fires this every few
        // KB of body uploaded. We map it to 0–80 % of the global bar
        // and reserve the last 20 % for backend processing + publish.
        onUploadProgress: (evt) => {
          if (!evt.total) return;
          const pct = Math.min(80, Math.round((evt.loaded / evt.total) * 80));
          setUploadProgress(pct);
        },
      });
      // iter190b — Async transcode for big reels (> 50 MB).
      let resolvedUrl = u?.url;
      let serverDuration = u?.duration;
      if (u?.status === 'processing' && u.job_id) {
        setUploadPhase('processing');
        setUploadProgress(85);
        const ready = await pollVideoJobWithToast(u.job_id, 'Ton reel');
        resolvedUrl = ready?.url;
        serverDuration = ready?.duration;
      } else {
        setUploadPhase('processing');
        setUploadProgress(85);
      }
      // Prefer the duration the server probed when available — it's
      // measured on the canonical .mp4 and never lies. Fall back to the
      // browser-side measurement of the raw upload otherwise.
      let duration = serverDuration ? Math.min(60, Math.floor(serverDuration)) : 0;
      if (!duration) {
        const video = document.createElement('video');
        video.preload = 'metadata';
        video.src = URL.createObjectURL(createForm.file);
        await new Promise(r => video.onloadedmetadata = r);
        duration = Math.min(60, Math.floor(video.duration || 0));
      }
      const videoUrl = resolvedUrl?.startsWith('http') ? resolvedUrl : `${API}${resolvedUrl}`;
      setUploadPhase('publishing');
      setUploadProgress(95);
      await axios.post(`${API}/api/feed/reels`, {
        video_url: videoUrl, caption: createForm.caption, duration,
        music_title: createForm.music_title,
      }, { withCredentials: true });
      setUploadProgress(100);
      setShowCreate(false);
      setCreateForm({ file: null, caption: '', music_title: '' });
      load();
    } catch (err) {
      setCreateError(uploadErrorMessage(err));
    } finally {
      setUploading(false);
      setUploadPhase('idle');
      setUploadProgress(0);
    }
  };

  return (
    <div className="fixed inset-0 bg-black z-40" data-testid="reels-page">
      {/* iter220 — Header z-index bumped to z-[60] so the create-btn
          and back-btn sit above the global mobile sticky header
          (Layout.js, z-50). Without this the Layout's bell icon
          intercepts thumb taps on the gradient pill at 390px width. */}
      <div className="absolute top-0 left-0 right-0 z-[60] p-4 flex items-center justify-between"
        style={{ background: 'linear-gradient(180deg, rgba(0,0,0,0.5) 0%, transparent 100%)' }}>
        <button onClick={() => navigate('/feed')} className="text-white" data-testid="reels-back">
          <CaretLeft size={24} weight="bold" />
        </button>
        <span className="text-white font-['Outfit'] text-lg font-bold">{t('reels.title')}</span>
        <div className="flex gap-2">
          <button onClick={() => setMuted(m => !m)} className="text-white"
                  aria-label={t(muted ? 'reels.unmute' : 'reels.mute')}
                  data-testid="reels-mute">
            {muted ? <SpeakerSlash size={22} weight="bold" /> : <SpeakerHigh size={22} weight="bold" />}
          </button>
          <button
            onClick={() => setShowCreate(true)}
            data-testid="reels-create-btn"
            aria-label={t('reels.create_btn_aria')}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-white font-['Outfit'] font-bold text-xs shadow-lg active:scale-95 transition-transform"
            style={{
              background: 'linear-gradient(135deg, #E01C2E 0%, #F59E0B 50%, #9333EA 100%)',
            }}
          >
            <Plus size={16} weight="bold" />
            <span>{t('reels.create_btn')}</span>
          </button>
        </div>
      </div>

      {/* Vertical snap scroll */}
      <div ref={containerRef} onScroll={onScroll}
        className="h-full w-full overflow-y-scroll snap-y snap-mandatory"
        style={{ scrollSnapType: 'y mandatory' }}>
        {reels.length === 0 ? (
          <div className="h-full flex items-center justify-center text-white text-center px-8">
            <div>
              <VideoCamera size={48} className="mx-auto mb-3 opacity-60" />
              <p className="font-['Manrope']">{t('reels.empty')}</p>
              <button onClick={() => setShowCreate(true)} className="jp-btn jp-btn-primary mt-4">
                <Plus size={16} /> {t('reels.create_first')}
              </button>
            </div>
          </div>
        ) : reels.map((r, i) => [
          i > 0 && i % 5 === 0 ? (
            <div key={`ad-${i}`} className="h-screen w-full snap-start flex items-center justify-center p-6" style={{ background: 'black' }}>
              <div className="max-w-sm w-full">
                <AdSlot slot="reel" variant="reel" />
              </div>
            </div>
          ) : null,
          <div key={r.reel_id} className="h-screen w-full snap-start relative flex items-center justify-center"
            data-testid={`reel-${r.reel_id}`}>
            {/* iter239h — VideoPlayer replaces the raw <video>. The internal
                IntersectionObserver autoplays at ≥60% visible and pauses
                otherwise, which matches the TikTok-style scroll-snap UX
                more reliably than the previous manual scroll handler. */}
            <VideoPlayer
              videoUrl={r.video_url.startsWith('http') ? r.video_url : `${API}${r.video_url}`}
              thumbnailUrl={r.thumbnail_url || undefined}
              autoplay
              muted={muted}
              loop
              aspectRatio="auto"
              className="w-full h-full"
              testId={`reel-video-${r.reel_id}`}
            />
            {/* Overlay bottom-left: creator + caption (iter210 — safe-area stacked) */}
            <div
              data-testid={`reel-creator-info-${r.reel_id}`}
              className="absolute left-3 right-20 text-white z-10"
              style={{ bottom: 'calc(24px + env(safe-area-inset-bottom, 0px))' }}
            >
              <div className="flex items-center gap-2 mb-2">
                <div className="w-9 h-9 rounded-full bg-white/20 flex items-center justify-center text-xs font-bold">
                  {r.user.avatar ? <img src={r.user.avatar} alt="" className="w-full h-full rounded-full object-cover" />
                    : (r.user.name[0] || '?').toUpperCase()}
                </div>
                <span className="font-['Outfit'] font-bold text-sm">@{r.user.username || r.user.name}</span>
              </div>
              {r.caption && <p className="text-xs font-['Manrope'] leading-relaxed line-clamp-3">{r.caption}</p>}
            </div>

            {/* Right action bar (iter210 — safe-area stacked) */}
            <div
              className="absolute right-3 flex flex-col gap-5 items-center z-10"
              style={{ bottom: 'calc(96px + env(safe-area-inset-bottom, 0px))' }}
              data-testid={`reel-actions-${r.reel_id}`}
            >
              <button onClick={() => toggleLike(r)} className="flex flex-col items-center text-white" data-testid={`reel-like-${r.reel_id}`}>
                <Heart size={32} weight={r.is_liked ? 'fill' : 'regular'} style={{ color: r.is_liked ? '#E01C2E' : '#fff' }} />
                <span className="text-[10px] font-['Manrope'] font-bold mt-0.5">{r.likes_count}</span>
              </button>
              <button
                onClick={() => openComments(r)}
                className="flex flex-col items-center text-white"
                data-testid={`reel-comment-${r.reel_id}`}
                aria-label={t('reels.comment_aria')}
              >
                <ChatCircle size={32} weight="regular" />
                <span className="text-[10px] font-['Manrope'] font-bold mt-0.5">{r.comments_count}</span>
              </button>
              {r.user.user_id !== user?.user_id && (
                <button onClick={() => { setTipTarget(r); setTipAmount(''); setTipError(''); }}
                  className="flex flex-col items-center text-white" data-testid={`reel-tip-${r.reel_id}`}>
                  <HandCoins size={32} weight="fill" style={{ color: '#F59E0B' }} />
                  <span className="text-[10px] font-['Manrope'] font-bold mt-0.5">{t('reels.tip')}</span>
                </button>
              )}
              <button
                onClick={() => shareReel(r)}
                className="flex flex-col items-center text-white"
                data-testid={`reel-share-${r.reel_id}`}
                aria-label={t('reels.share_aria')}
              >
                <ShareNetwork size={32} weight="regular" />
                <span className="text-[10px] font-['Manrope'] font-bold mt-0.5">{r.shares_count || 0}</span>
              </button>
            </div>
          </div>,
        ])}
      </div>

      {/* Tip Modal */}
      {tipTarget && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/70" onClick={() => setTipTarget(null)} />
          <div className="relative bg-white rounded-2xl max-w-sm w-full p-6 shadow-2xl" data-testid="reel-tip-modal">
            <div className="flex justify-between items-center mb-3">
              <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>
                <HandCoins size={20} className="inline mr-1" weight="fill" style={{ color: '#E01C2E' }} /> Tipper @{tipTarget.user?.username}
              </h3>
              <button onClick={() => setTipTarget(null)}><X size={18} /></button>
            </div>
            {tipError && <div className="jp-alert jp-alert-error mb-3">{tipError}</div>}
            <div className="grid grid-cols-4 gap-2 mb-3">
              {[100, 500, 1000, 2500].map(v => (
                <button key={v} onClick={() => setTipAmount(String(v))}
                  data-testid={`reel-tip-quick-${v}`}
                  className="p-2 rounded-lg text-xs font-bold font-['Manrope']"
                  style={{ background: tipAmount === String(v) ? 'var(--jp-primary)' : 'var(--jp-surface-secondary)',
                           color: tipAmount === String(v) ? '#fff' : 'var(--jp-text)' }}>{v}</button>
              ))}
            </div>
            <input type="number" value={tipAmount} onChange={e => setTipAmount(e.target.value)}
              placeholder="Montant XAF" className="jp-input text-sm mb-3" data-testid="reel-tip-amount" />
            <button onClick={sendTip} disabled={tipSending || !tipAmount}
              className="jp-btn jp-btn-primary jp-btn-full jp-btn-lg" data-testid="reel-tip-send">
              {tipSending ? 'Envoi…' : `Envoyer ${tipAmount || '0'} XAF`}
            </button>
          </div>
        </div>
      )}

      {/* Create Reel Modal */}
      {showCreate && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/70" onClick={() => setShowCreate(false)} />
          <div className="relative bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl" data-testid="create-reel-modal">
            <div className="flex justify-between items-center mb-3">
              <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>Créer un Reel</h3>
              <button onClick={() => setShowCreate(false)}><X size={18} /></button>
            </div>
            {createError && <div className="jp-alert jp-alert-error mb-3">{createError}</div>}
            <form onSubmit={handleCreateReel} className="space-y-3">
              <div>
                <label className="jp-label">Vidéo (max 60s, mp4/webm)</label>
                <input type="file" accept="video/*" required
                  onChange={e => setCreateForm(f => ({ ...f, file: e.target.files[0] }))}
                  className="jp-input text-sm" data-testid="reel-file-input" />
              </div>
              <div>
                <label className="jp-label">Légende</label>
                <textarea value={createForm.caption} onChange={e => setCreateForm(f => ({ ...f, caption: e.target.value }))}
                  className="jp-input text-sm resize-none" style={{ height: '60px' }} data-testid="reel-caption"
                  placeholder={t('reels.decrivez_votre_reel')} />
              </div>
              <div>
                <label className="jp-label">Musique (optionnel)</label>
                <input value={createForm.music_title} onChange={e => setCreateForm(f => ({ ...f, music_title: e.target.value }))}
                  className="jp-input text-sm" placeholder="Artiste - Titre" data-testid="reel-music" />
              </div>
              <UploadProgressButton
                type="submit"
                disabled={uploading && !createForm.file}
                progress={uploadProgress}
                phase={uploadPhase}
                idleLabel="Publier le Reel"
                testId="reel-submit"
              />
            </form>
          </div>
        </div>
      )}

      {/* iter215 — Comments drawer */}
      {commentsOpenFor && (
        <ReelCommentsDrawer
          reelId={commentsOpenFor}
          currentUser={user}
          onClose={() => setCommentsOpenFor(null)}
          onNewCount={(n) => setReels(rs => rs.map(r =>
            r.reel_id === commentsOpenFor ? { ...r, comments_count: n } : r
          ))}
        />
      )}
    </div>
  );
}
