import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import axios from 'axios';
import uploadErrorMessage from '@/utils/uploadErrorMessage';
import UploadProgressButton from '@/components/UploadProgressButton';
import { Heart, ChatCircle, ShareNetwork, Image, VideoCamera, Smiley, GearSix, Camera, HandCoins, X, Play, Plus, PushPin } from '@phosphor-icons/react';
import AdSlot from '@/components/AdSlot';
import { PostMenu, EditPostModal, SharePostModal } from '@/components/PostMenu';
import AIImproveButton from '@/components/AIImproveButton';
import CreateFab from '@/components/CreateFab';
import ReactionBar from '@/components/ReactionBar';
import GroupsOnboardingModal from '@/components/GroupsOnboardingModal';
import CommentSection from '@/components/feed/CommentSection';
// iter237q — Rich composer additions (additive, no replacement of existing UI).
import FormatToolbar from '@/components/feed/FormatToolbar';
import EmojiPickerPopover from '@/components/feed/EmojiPickerPopover';
import MediaPreviewGrid from '@/components/feed/MediaPreviewGrid';
import RichTextEditor from '@/components/feed/RichTextEditor';
import { compressFiles } from '@/utils/imageCompression';
import ImageCropper from '@/components/ImageCropper';
// iter239d — Pro video player (lazy autoplay, scrubbable, fullscreen).
import VideoPlayer from '@/components/VideoPlayer';
import MediaFilterEditor from '@/components/media/MediaFilterEditor';
import ZoomableImage from '@/components/media/ZoomableImage';
// iter239o — SmartImage: orientation-aware feed thumbnail (portrait→3/4,
// landscape→16/9, square→1/1). ZoomableImage stays for fullscreen zoom.
import SmartImage from '@/components/media/SmartImage';
import PostContent from '@/components/PostContent';
import useKeyboardOffset from '@/hooks/useKeyboardOffset';
import { uploadAvatar, uploadCover } from '@/utils/imageUpload';
import { pollVideoJobWithToast } from '@/utils/videoUploadPoll';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;

export default function FeedPage() {
  const { user, refreshUser } = useAuth();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const composerRef = useRef(null);
  const composerInputRef = useRef(null);
  const [composerFocused, setComposerFocused] = useState(false);
  // iter237q — Emoji picker open state for the composer.
  const [emojiPickerOpen, setEmojiPickerOpen] = useState(false);
  const kbOffset = useKeyboardOffset();
  const [posts, setPosts] = useState([]);
  const [stories, setStories] = useState([]);
  const [newPost, setNewPost] = useState('');
  const [posting, setPosting] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  // iter220 — granular progress for the publish button. We aggregate
  // across multiple files: uploadProgress is the AVERAGE of all files'
  // upload progress, capped to 80 % until the publish POST kicks in.
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadPhase, setUploadPhase] = useState('idle');
  const [compressing, setCompressing] = useState(false);
  const [editingPost, setEditingPost] = useState(null);
  const [sharingPost, setSharingPost] = useState(null);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [tipTarget, setTipTarget] = useState(null); // { type, id, recipientName, recipientId }
  const [tipAmount, setTipAmount] = useState('');
  const [tipMessage, setTipMessage] = useState('');
  const [tipSending, setTipSending] = useState(false);
  // iter147 — unified media filter editor (Phase 1 — CSS/canvas filters).
  const [filterEditor, setFilterEditor] = useState({ open: false, files: [], mode: 'upload' });
  const [tipError, setTipError] = useState('');
  // iter141nineF — author-configurable tip presets fetched on demand
  const [tipSettings, setTipSettings] = useState(null); // { enabled, presets, message, is_pro }
  const [storyViewer, setStoryViewer] = useState(null); // { group, index }
  const [showStoryCreate, setShowStoryCreate] = useState(false);
  const [storyText, setStoryText] = useState('');
  const [storyBg, setStoryBg] = useState('#0F056B');
  // iter147 — story photo support via MediaFilterEditor
  const [storyPhoto, setStoryPhoto] = useState(null); // File (filtered)
  const [storyPhotoPreview, setStoryPhotoPreview] = useState(null); // ObjectURL
  const [storyPreset, setStoryPreset] = useState(''); // iter150 — preset id chosen for the story photo
  // iter150 — preset id chosen for the current composer photo selection
  const [composerPreset, setComposerPreset] = useState('');
  const [activeTab, setActiveTab] = useState('photos');
  const [feedSort, setFeedSort] = useState('smart'); // 'smart' | 'recent'
  // Comments: map post_id → true when the inline section is expanded.
  // We only mount CommentSection for open posts so the feed stays light.
  const [openComments, setOpenComments] = useState({});
  // Iter83 P0 — single source of truth: read cover_image / avatar from the
  // /api/auth/me user object everywhere. If the backend hasn't populated
  // them yet (fresh signup before profile edit) we render the clean empty
  // state instead of a stock Unsplash image (was wrong + inconsistent).
  const [cropperFor, setCropperFor] = useState(null); // 'avatar' | 'cover' | null

  const toggleComments = useCallback((postId) => {
    setOpenComments(prev => ({ ...prev, [postId]: !prev[postId] }));
  }, []);

  const bumpCommentCount = useCallback((postId, delta) => {
    setPosts(prev => prev.map(p => p.post_id === postId
      ? { ...p, comments_count: Math.max(0, (p.comments_count || 0) + delta) }
      : p));
  }, []);

  const loadPosts = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/feed/posts?page=1&limit=30&sort=${feedSort}`, { withCredentials: true });
      setPosts(data.posts);
    } catch {}
  }, [feedSort]);

  const loadStories = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/feed/stories`, { withCredentials: true });
      setStories(data);
    } catch {}
  }, []);

  useEffect(() => { loadPosts(); loadStories(); }, [loadPosts, loadStories]);

  // Show Groups onboarding once per user. Gate on a fresh /api/auth/me check
  // to avoid flashing the modal (and firing a stray analytics event) when
  // AuthContext still holds a stale cached user with onboarding_completed=false.
  useEffect(() => {
    if (!user || user.onboarding_completed !== false) return;
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const { data } = await axios.get(`${API}/api/auth/me`, { withCredentials: true });
        if (cancelled) return;
        if (data?.onboarding_completed === false) setShowOnboarding(true);
      } catch { /* silent */ }
    }, 900);
    return () => { cancelled = true; clearTimeout(t); };
  }, [user]);

  const handlePost = async () => {
    // iter237r — `newPost` is now HTML produced by RichTextEditor. Use the
    // editor's getPlainText() to test emptiness reliably (HTML like
    // "<br><br>" trims non-empty but is visually blank).
    const plain = composerInputRef.current?.getPlainText?.() || '';
    const htmlTrim = newPost.trim();
    const isVisuallyEmpty = !plain.trim();
    if (isVisuallyEmpty && selectedFiles.length === 0) return;
    const textTrim = isVisuallyEmpty ? '' : htmlTrim;
    setPosting(true);

    // iter183 — Optimistic UI : afficher le post immédiatement (< 1s ressenti)
    // avec un placeholder local. Quand le serveur répond, on remplace par la
    // vraie row. En cas d'erreur, on retire le placeholder et on garde le
    // composer rempli pour que l'utilisateur retente.
    const tempId = `temp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const localFilesPreview = selectedFiles.map(f => ({
      url: URL.createObjectURL(f), kind: f.type.startsWith('video/') ? 'video' : 'image',
    }));
    const optimistic = {
      post_id: tempId,
      _optimistic: true,
      user_id: user?.user_id || '',
      first_name: user?.first_name || '',
      last_name: user?.last_name || '',
      username: user?.username || '',
      avatar: user?.avatar || '',
      is_verified: !!user?.is_verified,
      text: textTrim,
      media: localFilesPreview.map(p => p.url),  // local URLs for instant preview
      visibility: 'public',
      likes_count: 0, comments_count: 0, shares_count: 0,
      is_liked: false, is_pinned: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    setPosts(prev => [optimistic, ...prev]);
    // Reset composer NOW so the user feels the action is done.
    const filesToUpload = selectedFiles;
    setNewPost('');
    setSelectedFiles([]);
    setComposerPreset('');

    try {
      let mediaUrls = [];
      if (filesToUpload.length > 0) {
        setUploading(true);
        setUploadPhase('upload');
        setUploadProgress(0);
        // Track per-file ratio so the aggregate progress reflects ALL
        // files (otherwise the bar would jump back to 0 between each).
        const perFile = new Array(filesToUpload.length).fill(0);
        const recompute = () => {
          const avg = perFile.reduce((a, b) => a + b, 0) / perFile.length;
          // Reserve 80–100 % for the publish POST that follows.
          setUploadProgress(Math.min(80, Math.round(avg * 80)));
        };
        for (let i = 0; i < filesToUpload.length; i++) {
          const file = filesToUpload[i];
          const isImage = (file.type || '').startsWith('image/');
          const formData = new FormData();
          formData.append('file', file);
          const endpoint = isImage
            ? `${API}/api/upload/image?kind=post`
            : `${API}/api/upload/`;
          const { data: u } = await axios.post(endpoint, formData, {
            withCredentials: true,
            onUploadProgress: (evt) => {
              if (!evt.total) return;
              perFile[i] = evt.loaded / evt.total;
              recompute();
            },
          });
          // iter190b — Async path: backend returns status='processing' for
          // videos > 50 MB. Toast + poll until ready, then attach URL.
          let url;
          let mediaEntry; // iter239e — full object with srcset variants if available
          if (u?.status === 'processing' && u.job_id) {
            setUploadPhase('processing');
            const ready = await pollVideoJobWithToast(u.job_id, file.name || 'Ta vidéo');
            url = ready?.url;
            mediaEntry = ready;
          } else if (isImage) {
            url = u?.main?.url;
            mediaEntry = u?.main || u;
          } else {
            url = u?.url;
            mediaEntry = u;
          }
          if (url) {
            // iter239e — preserve responsive variants (small_url/medium_url/
            // large_url) + thumbnail in the media entry so the feed renderer
            // can build `<img srcset>`. Legacy single-string entries still
            // work — the backend accepts both shapes.
            const entry = {
              url,
              type: isImage ? 'image' : (mediaEntry?.type || undefined),
              small_url:  mediaEntry?.small_url,
              medium_url: mediaEntry?.medium_url,
              large_url:  mediaEntry?.large_url,
              // iter239f — AVIF variants (additive — null/undef in legacy posts)
              small_url_avif:  mediaEntry?.small_url_avif,
              medium_url_avif: mediaEntry?.medium_url_avif,
              large_url_avif:  mediaEntry?.large_url_avif,
              thumbnail_url: mediaEntry?.thumbnail_url,
            };
            // Strip undefined to keep the JSON tidy.
            Object.keys(entry).forEach(k => entry[k] === undefined && delete entry[k]);
            mediaUrls.push(Object.keys(entry).length > 1 ? entry : url);
          }
          perFile[i] = 1; // mark this file done
          recompute();
        }
        setUploading(false);
      }
      setUploadPhase('publishing');
      setUploadProgress(95);
      const { data } = await axios.post(`${API}/api/feed/posts`,
        { text: textTrim, media: mediaUrls, filter_preset: composerPreset || '' },
        { withCredentials: true });
      setUploadProgress(100);
      // Replace optimistic placeholder with the real server post
      setPosts(prev => prev.map(p => p.post_id === tempId ? data : p));
      // Free the local object URLs we created for instant preview
      localFilesPreview.forEach(p => { try { URL.revokeObjectURL(p.url); } catch (_) {} });
    } catch (e) {
      // Roll back the optimistic placeholder and restore the composer
      setPosts(prev => prev.filter(p => p.post_id !== tempId));
      setNewPost(textTrim);
      setSelectedFiles(filesToUpload);
      try {
        const { toast: _t } = await import('sonner');
        _t.error(uploadErrorMessage(e, "Échec de la publication. Réessaie."));
      } catch (_) {}
    } finally {
      setPosting(false);
      setUploading(false);
      setUploadPhase('idle');
      setUploadProgress(0);
    }
  };

  const toggleLike = async (postId) => {
    try {
      const { data } = await axios.post(`${API}/api/feed/posts/${postId}/like`, {}, { withCredentials: true });
      setPosts(prev => prev.map(p => p.post_id === postId ? { ...p, is_liked: data.liked, likes_count: data.liked ? p.likes_count + 1 : p.likes_count - 1 } : p));
    } catch {}
  };

  const handleFileSelect = async (e) => {
    const raw = Array.from(e.target.files).slice(0, 5);
    if (!raw.length) return;
    const images = raw.filter(f => !f.type.startsWith('video/'));
    const videos = raw.filter(f => f.type.startsWith('video/'));
    // Vidéos → pas de filtre canvas (crash mobile bas de gamme) → direct
    if (videos.length > 0 && images.length === 0) {
      // Vidéo seule : compression légère nom + vérification taille
      const MAX_VIDEO_MB = 50;
      const oversized = videos.filter(v => v.size > MAX_VIDEO_MB * 1024 * 1024);
      if (oversized.length > 0) {
        toast.error(`Vidéo trop lourde (max ${MAX_VIDEO_MB}MB). Essaie de raccourcir la vidéo.`);
        if (e.target) e.target.value = '';
        return;
      }
      setSelectedFiles(videos);
      setTimeout(() => {
        const btn = document.querySelector('[data-testid="publish-button"]');
        if (btn) btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 200);
    } else {
      // Images seules ou mix → ouvrir filtre uniquement pour les images
      setFilterEditor({ open: true, files: images.length > 0 ? images : raw, mode: 'upload' });
    }
    if (e.target) e.target.value = '';
  };

  // Called after the user applies a filter (or chooses "Original").
  const handleFilterApply = async (filteredFiles, presetId) => {
    setComposerPreset(presetId || '');
    setCompressing(true);
    try {
      const optimized = await compressFiles(filteredFiles);
      setSelectedFiles(optimized);
    } catch {
      setSelectedFiles(filteredFiles);
    } finally { setCompressing(false); }
    // iter165 — make the Publier button reachable: the editor is full-screen
    // so when it closes the page may be scrolled anywhere. We deliberately
    // scroll the publish button (not the composer top) into view AND we do
    // NOT focus the textarea — that would pop the on-screen keyboard which
    // hides the bottom half of the viewport on small phones.
    setTimeout(() => {
      const btn = document.querySelector('[data-testid="publish-button"]');
      if (btn) {
        btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
      } else {
        composerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
      }
    }, 200);
  };

  const focusComposer = () => {
    composerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setTimeout(() => composerInputRef.current?.focus(), 300);
  };

  const handlePostDeleted = (postId) => {
    setPosts(prev => prev.filter(p => p.post_id !== postId));
  };
  const handlePostUpdated = (updated) => {
    setPosts(prev => prev.map(p => p.post_id === updated.post_id ? { ...p, ...updated } : p));
  };
  const handlePostPinned = (postId, isPinned) => {
    setPosts(prev => prev.map(p => p.post_id === postId ? { ...p, is_pinned: isPinned } : p));
  };

  const timeSince = (dateStr) => {
    const s = Math.floor((new Date() - new Date(dateStr)) / 1000);
    if (s < 60) return 'A l\'instant';
    if (s < 3600) return `${Math.floor(s/60)}min`;
    if (s < 86400) return `${Math.floor(s/3600)}h`;
    return `${Math.floor(s/86400)}j`;
  };

  const tabs = [
    { id: 'photos', label: 'Photos', icon: Camera },
    { id: 'videos', label: 'Videos', icon: VideoCamera },
    { id: 'groupes', label: 'Groupes', icon: ({ size, ...p }) => <svg width={size} height={size} viewBox="0 0 20 20" fill="currentColor" {...p}><path d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z"/></svg> },
    { id: 'suivies', label: 'Suivies', icon: Heart },
  ];

  return (
    <div className="pb-24" data-testid="feed-page" style={{ background: 'var(--jp-bg)' }}>
      {/* ══ PROFILE HEADER — Cover + Avatar (single source of truth: user object) ══ */}
      <div className="relative">
        {/* Cover Photo — read from user.cover_image, identical to ProfilePage */}
        <div className="h-36 w-full relative overflow-hidden group" data-testid="feed-cover">
          {user?.cover_image ? (
            <img
              src={user.cover_image.startsWith('http') ? user.cover_image : `${API}${user.cover_image}`}
              alt=""
              className="w-full h-full object-cover"
              style={{ objectPosition: `center ${user.cover_position_y ?? 50}%` }}
              data-testid="feed-cover-image"
            />
          ) : (
            <div className="absolute inset-0"
              style={{ background: 'linear-gradient(135deg, #0B0542 0%, #0F056B 60%, #E01C2E 100%)' }} />
          )}
          {/* Soft overlay for CTA affordance */}
          <div className="absolute inset-0 bg-gradient-to-t from-black/30 via-transparent to-transparent pointer-events-none" />
          {/* Edit cover CTA — mirrors ProfilePage for consistency */}
          <button onClick={() => setCropperFor('cover')} data-testid="feed-edit-cover-button"
            aria-label={user?.cover_image ? 'Modifier la photo de couverture' : 'Ajouter une photo de couverture'}
            className="absolute bottom-2 right-2 flex items-center gap-1.5 px-3 py-1.5 rounded-full backdrop-blur-md transition-all hover:scale-105 active:scale-95 shadow-lg font-['Manrope'] font-semibold text-[11px]"
            style={{ background: 'rgba(0,0,0,0.65)', color: '#fff', border: '1px solid rgba(255,255,255,0.2)' }}>
            <Camera size={12} weight="fill" />
            {user?.cover_image ? 'Modifier' : 'Ajouter'}
          </button>
        </div>

        {/* Avatar — overlapping cover, click to edit */}
        <div className="absolute left-1/2 -translate-x-1/2 -bottom-12 z-10">
          <button
            type="button"
            onClick={() => setCropperFor('avatar')}
            data-testid="feed-edit-avatar-button"
            aria-label={user?.avatar ? 'Modifier la photo de profil' : 'Ajouter une photo de profil'}
            className="relative w-24 h-24 rounded-full border-4 flex items-center justify-center overflow-hidden transition-transform hover:scale-[1.03] active:scale-95 cursor-pointer"
            style={{ borderColor: 'white', background: '#E4E4E7' }}
          >
            {user?.avatar ? (
              <img
                src={user.avatar.startsWith('http') ? user.avatar : `${API}${user.avatar}`}
                alt="" className="w-full h-full object-cover"
                data-testid="feed-avatar-image"
              />
            ) : (
              <div className="flex flex-col items-center gap-0.5">
                <Camera size={20} weight="duotone" style={{ color: 'var(--jp-primary)' }} className="opacity-60" />
                <span className="text-[9px] font-['Manrope'] font-semibold" style={{ color: 'var(--jp-primary)' }}>
                  Ajouter
                </span>
              </div>
            )}
          </button>
          {/* Fixed camera badge, gradient */}
          <button
            type="button"
            onClick={() => setCropperFor('avatar')}
            aria-label={t('feed.modifier_la_photo_de_profil')}
            data-testid="feed-avatar-camera-badge"
            className="absolute bottom-0 right-0 w-8 h-8 rounded-full flex items-center justify-center shadow-lg transition-all hover:scale-110 active:scale-95"
            style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)', color: 'white', border: '3px solid white' }}
          >
            <Camera size={14} weight="fill" />
          </button>
        </div>
      </div>

      {/* User Name + Settings */}
      <div className="pt-14 pb-3 px-4 text-center">
        <h2 className="font-['Outfit'] text-xl font-bold" style={{ color: 'var(--jp-text)' }} data-testid="user-display-name">
          {user?.first_name} {user?.last_name}
        </h2>
        <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>@{user?.username}</p>
      </div>

      {/* ══ TABS — Pill-shaped like in screenshots ══ */}
      <div className="flex justify-center gap-2 px-4 mb-4" data-testid="profile-tabs">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setActiveTab(t.id)}
            data-testid={`tab-${t.id}`}
            className="flex items-center gap-1.5 px-4 py-2 rounded-full text-xs font-['Manrope'] font-semibold transition-all"
            style={{
              background: activeTab === t.id ? '#0F056B' : '#F4F4F5',
              color: activeTab === t.id ? 'white' : 'var(--jp-text-secondary)',
            }}>
            <t.icon size={14} />
            {t.label}
          </button>
        ))}
      </div>

      {/* ══ FEED SORT TOGGLE (AI re-ranking) ══ */}
      <div className="flex justify-center gap-1 px-4 mb-4 max-w-2xl mx-auto" data-testid="feed-sort-toggle">
        <button onClick={() => setFeedSort('smart')} data-testid="feed-sort-smart"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] font-['Manrope'] font-semibold transition-all"
          style={{
            background: feedSort === 'smart' ? '#0F056B' : 'transparent',
            color: feedSort === 'smart' ? 'white' : 'var(--jp-text-secondary)',
            border: feedSort === 'smart' ? 'none' : '1px solid var(--jp-border)',
          }}>
          ✨ Pour toi
        </button>
        <button onClick={() => setFeedSort('recent')} data-testid="feed-sort-recent"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] font-['Manrope'] font-semibold transition-all"
          style={{
            background: feedSort === 'recent' ? '#0F056B' : 'transparent',
            color: feedSort === 'recent' ? 'white' : 'var(--jp-text-secondary)',
            border: feedSort === 'recent' ? 'none' : '1px solid var(--jp-border)',
          }}>
          🕐 Récents
        </button>
      </div>

      {/* ══ CONTENT AREA ══ */}
      <div className="px-4 max-w-2xl mx-auto">
        {/* ── Create Post composer (iter211 — CEO UX spec: no border,
              expand on focus, iOS keyboard-safe, borderless input) */}
        <div
          ref={composerRef}
          data-testid="create-post"
          className="jp-composer-card"
          style={{
            // iOS keyboard fix (Bug 2): extra bottom padding when the
            // visualViewport shrinks so the input + publish button stay
            // above the keyboard. On Android and desktop kbOffset==0.
            paddingBottom: kbOffset ? `${kbOffset + 12}px` : undefined,
          }}
        >
          <div className="flex items-start gap-3">
            <div className="jp-avatar jp-avatar-md jp-avatar-primary flex-shrink-0">
              {(user?.first_name?.[0] || '?').toUpperCase()}
            </div>
            <div className="flex-1 min-w-0">
              {/* iter237r — WYSIWYG editor: contentEditable + execCommand
                   for live B/I/U formatting (replaces the previous textarea).
                   `composerInputRef` exposes { focus, getPlainText, getHtml,
                   clear, applyFormat, insertText } — see RichTextEditor.jsx. */}
              <RichTextEditor
                ref={composerInputRef}
                testId="post-input"
                value={newPost}
                onChange={(html) => setNewPost(html)}
                onFocus={() => {
                  setComposerFocused(true);
                  setTimeout(() => {
                    composerInputRef.current?.focus();
                  }, 350);
                }}
                onBlur={() => {
                  // Collapse only if there's no draft (use plain text).
                  const plain = composerInputRef.current?.getPlainText?.() || '';
                  if (!plain.trim() && selectedFiles.length === 0) {
                    setComposerFocused(false);
                  }
                }}
                onSubmitShortcut={() => {
                  const plain = composerInputRef.current?.getPlainText?.() || '';
                  if ((plain.trim() || selectedFiles.length > 0) && !posting && !compressing) {
                    handlePost();
                  }
                }}
                placeholder={t('feed.quoi_de_neuf')}
                minHeight={composerFocused || newPost || selectedFiles.length > 0 ? 120 : 44}
                maxLength={500}
                className="jp-composer-input"
              />
              {/* iter237q — Format toolbar (WYSIWYG B/I/U + hashtag/mention/link/emoji). */}
              {(composerFocused || newPost || selectedFiles.length > 0) && (
                <FormatToolbar
                  editorApi={composerInputRef.current}
                  onEmojiClick={() => setEmojiPickerOpen(true)}
                />
              )}
              {selectedFiles.length > 0 && (
                <MediaPreviewGrid
                  files={selectedFiles}
                  onRemove={(idx) => setSelectedFiles(prev => prev.filter((_, i) => i !== idx))}
                />
              )}
              {/* Actions row — shown when the composer is expanded
                   (focused, has text, or media attached). Keeps the
                   collapsed state minimal & clean.

                   iter215 — `onPointerDown e.preventDefault()` on every
                   action button below: prevents the textarea from
                   losing focus on tap, which would unmount this row
                   mid-tap (because of the onBlur collapse logic) and
                   make the click miss its target on iOS Safari. The
                   buttons stay `onClick`-driven so keyboard users are
                   not affected. */}
              {(composerFocused || newPost || selectedFiles.length > 0) && (
                <div className="jp-composer-actions flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2">
                  <div className="flex items-center gap-1 flex-wrap">
                    <label
                      className="p-1.5 rounded-lg cursor-pointer"
                      style={{ color: '#0F056B' }}
                      onPointerDown={(e) => e.preventDefault()}
                      data-testid="image-upload-label"
                    >
                      <Image size={18} />
                      <input type="file" accept="image/*,video/*,.heic,.heif,.avif" multiple className="hidden" onChange={handleFileSelect} data-testid="file-upload-input" />
                    </label>
                    <label
                      className="p-1.5 rounded-lg cursor-pointer"
                      style={{ color: '#E01C2E' }}
                      onPointerDown={(e) => e.preventDefault()}
                      data-testid="video-upload-label"
                    >
                      <VideoCamera size={18} />
                      <input type="file" accept="video/*,.mp4,.mov,.avi,.mkv,.webm,.3gp" multiple className="hidden" onChange={handleFileSelect} data-testid="video-upload-input" />
                    </label>
                    {/* iter147 — Live camera capture with realtime filters */}
                    <button
                      type="button"
                      onPointerDown={(e) => e.preventDefault()}
                      onClick={() => setFilterEditor({ open: true, files: [], mode: 'capture' })}
                      data-testid="camera-capture-btn"
                      aria-label={t('feed.camera')}
                      className="p-1.5 rounded-lg"
                      style={{ color: '#0F056B' }}
                    >
                      <Camera size={18} />
                    </button>
                    <button
                      type="button"
                      className="p-1.5 rounded-lg"
                      style={{ color: '#D97706' }}
                      onPointerDown={(e) => e.preventDefault()}
                      onClick={() => setEmojiPickerOpen(true)}
                      data-testid="emoji-btn"
                      aria-label="Insérer un emoji"
                    >
                      <Smiley size={18} />
                    </button>
                    {/* AIImproveButton: wrap with a span that swallows
                        pointerdown so the inner button can fire its
                        own click without losing textarea focus. */}
                    <span onPointerDown={(e) => e.preventDefault()}>
                      <AIImproveButton
                        text={newPost}
                        disabled={posting || uploading}
                        onApply={(improved) => setNewPost(improved)}
                      />
                    </span>
                    {compressing && (
                      <span className="text-[10px] px-2 py-1 rounded-full ml-1"
                        style={{ background: 'var(--jp-primary-subtle)', color: 'var(--jp-primary)' }}
                        data-testid="compressing-indicator">
                        Optimisation…
                      </span>
                    )}
                    {/* iter237q — Character counter (visible only when typing).
                        iter237r — counts visible characters (HTML stripped). */}
                    {(() => {
                      const plain = composerInputRef.current?.getPlainText?.() || '';
                      const len = plain.length;
                      if (len === 0) return null;
                      return (
                        <span
                          className="text-[11px] font-['Manrope'] font-medium ml-1"
                          data-testid="composer-char-counter"
                          style={{
                            color:
                              len > 480 ? '#E01C2E' :
                              len > 400 ? '#D97706' :
                              'var(--jp-text-muted)',
                          }}
                        >
                          {500 - len}
                        </span>
                      );
                    })()}
                  </div>
                  <UploadProgressButton
                    type="button"
                    onPointerDown={(e) => e.preventDefault()}
                    onClick={handlePost}
                    progress={uploadProgress}
                    phase={uploadPhase}
                    disabled={(() => {
                      const plain = composerInputRef.current?.getPlainText?.() || '';
                      return (!plain.trim() && selectedFiles.length === 0) || posting || compressing;
                    })()}
                    idleLabel="Publier"
                    testId="publish-button"
                    className="w-full sm:w-auto px-5 py-2.5 rounded-full text-white text-sm font-['Manrope'] font-semibold transition-all"
                    style={{
                      background: '#0F056B',
                      cursor: ((!(composerInputRef.current?.getPlainText?.() || '').trim() && selectedFiles.length === 0) || posting || compressing) ? 'not-allowed' : 'pointer',
                    }}
                    title={(!(composerInputRef.current?.getPlainText?.() || '').trim() && selectedFiles.length === 0)
                      ? "Ajoute du texte, une photo ou une vidéo" : "Publier"}
                  />
                </div>
              )}
            </div>
          </div>
        </div>

        {/* iter237q — Emoji picker popover (lazy-loaded, mobile-first positioned). */}
        <EmojiPickerPopover
          open={emojiPickerOpen}
          onClose={() => setEmojiPickerOpen(false)}
          onSelect={(emoji) => {
            // iter237r — RichTextEditor exposes insertText(text) which
            // performs cursor-aware insertion + flush onChange.
            composerInputRef.current?.insertText?.(emoji);
          }}
        />

        {/* Stories bar + Reels CTA */}
        <div className="mb-4 rounded-2xl bg-white p-3" style={{ border: '1px solid var(--jp-border)' }} data-testid="stories-bar">
          <div className="flex gap-3 overflow-x-auto jp-scrollbar">
            {/* Create Story */}
            <button onClick={() => setShowStoryCreate(true)} data-testid="create-story-btn"
              className="flex flex-col items-center gap-1 flex-shrink-0">
              <div className="w-16 h-16 rounded-full flex items-center justify-center text-white relative"
                style={{ background: 'var(--jp-surface-secondary)', border: '2px dashed var(--jp-primary)' }}>
                <Plus size={24} style={{ color: 'var(--jp-primary)' }} weight="bold" />
              </div>
              <span className="text-[10px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>{t('feed.story_add')}</span>
            </button>

            {/* Reels quick link */}
            <Link to="/reels" data-testid="reels-cta" className="flex flex-col items-center gap-1 flex-shrink-0">
              <div className="w-16 h-16 rounded-full flex items-center justify-center relative"
                style={{ background: 'linear-gradient(135deg, #E01C2E 0%, #F59E0B 50%, #9333EA 100%)' }}>
                <Play size={24} color="#fff" weight="fill" />
              </div>
              <span className="text-[10px] font-['Manrope'] font-bold" style={{ color: 'var(--jp-primary)' }}>{t('feed.reels')}</span>
            </Link>

            {/* Active stories by user */}
            {stories.map(g => (
              <button key={g.user_id} onClick={() => setStoryViewer({ group: g, index: 0 })}
                data-testid={`story-${g.user_id}`}
                className="flex flex-col items-center gap-1 flex-shrink-0">
                <div className="w-16 h-16 rounded-full p-0.5"
                  style={{ background: g.all_viewed ? 'var(--jp-border)' : 'linear-gradient(135deg, #E01C2E 0%, #F59E0B 100%)' }}>
                  <div className="w-full h-full rounded-full overflow-hidden bg-white p-0.5">
                    <div className="w-full h-full rounded-full flex items-center justify-center text-white font-bold font-['Outfit']"
                      style={{ background: 'var(--jp-primary)' }}>
                      {g.avatar ? <img src={g.avatar} alt="" className="w-full h-full rounded-full object-cover" /> : (g.name[0] || '?').toUpperCase()}
                    </div>
                  </div>
                </div>
                <span className="text-[10px] font-['Manrope'] truncate max-w-[70px]" style={{ color: 'var(--jp-text)' }}>
                  {g.user_id === user?.user_id
                    ? t('stories.your_story', { defaultValue: 'Vous' })
                    : g.name.split(' ')[0]}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Posts */}
        <div className="space-y-4">
          {posts.length === 0 && (
            <div className="rounded-2xl p-10 text-center" style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
              <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>{t('feed.empty_title')}</p>
            </div>
          )}
          {posts.map((post, idx) => [
            idx > 0 && idx % 4 === 0 ? <div key={`ad-${idx}`} className="mb-4"><AdSlot slot="feed" /></div> : null,
            <div key={post.post_id} className="rounded-2xl overflow-hidden relative" style={{ background: 'white', border: '1px solid var(--jp-border)' }}
              data-testid={`post-${post.post_id}`}>
              {post._optimistic && (
                <div className="absolute top-2 right-2 z-10 text-[10px] px-2 py-1 rounded-full font-semibold flex items-center gap-1.5"
                  data-testid={`post-${post.post_id}-uploading`}
                  style={{ background: 'rgba(15,5,107,0.92)', color: 'white' }}>
                  {/* iter240h — Live pulse dot + i18n label. */}
                  <span style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: '#4ade80',
                    animation: 'jpFeedPulse 1s infinite',
                  }} />
                  {t('feed.uploading', { defaultValue: 'Publication en cours…' })}
                </div>
              )}
              <div className="flex items-center gap-3 p-4 pb-2">
                <div className="jp-avatar jp-avatar-md jp-avatar-primary">
                  {post.avatar ? <img src={post.avatar} alt="" /> : (post.first_name?.[0] || '?').toUpperCase()}
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-1.5">
                    <p className="text-sm font-semibold font-['Manrope']" style={{ color: 'var(--jp-text)' }}>
                      {post.first_name} {post.last_name}
                    </p>
                    {post.is_pinned && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded-full flex items-center gap-0.5 font-bold"
                        style={{ background: '#FEF3C7', color: '#B45309' }}
                        data-testid={`pinned-badge-${post.post_id}`}>
                        <PushPin size={9} weight="fill" /> Épinglé
                      </span>
                    )}
                  </div>
                  <p className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{timeSince(post.created_at)}</p>
                </div>
                <PostMenu
                  post={post}
                  currentUserId={user?.user_id}
                  onEdit={(p) => !post._optimistic && setEditingPost(p)}
                  onShare={(p) => !post._optimistic && setSharingPost(p)}
                  onPinned={handlePostPinned}
                  onDeleted={handlePostDeleted}
                />
              </div>

              {post.text && (
                <PostContent text={post.text} testIdPrefix={`post-${post.post_id}`} />
              )}

              {post.media && Array.isArray(post.media) && post.media.length > 0 && (
                <div className={`${post.media.length === 1 ? '' : 'grid grid-cols-2 gap-1'}`}>
                  {post.media.map((m, i) => {
                    // Support both string format and {type, url} object format
                    const url = typeof m === 'string' ? m : (m?.url || '');
                    if (!url) return null;
                    const rawType = typeof m === 'string' ? '' : (m?.type || '');
                    // Infer real type from URL extension when type is generic 'file'
                    const isVideo = /\.(mp4|webm|mov|avi)(\?|$)/i.test(url);
                    const isYoutube = rawType === 'youtube' || /youtube\.com|youtu\.be/.test(url);
                    const isImage = /\.(jpg|jpeg|png|gif|webp|svg)(\?|$)/i.test(url);
                    const effectiveType = isYoutube ? 'youtube' : isVideo ? 'video' : (isImage || rawType === 'image') ? 'image' : 'file';
                    const isRelative = !url.startsWith('http');
                    const src = isRelative ? `${API}${url.startsWith('/') ? '' : '/'}${url}` : url;
                    return (
                      <div key={i}>
                        {effectiveType === 'video' ? (
                          <VideoPlayer
                            videoUrl={src}
                            thumbnailUrl={m?.thumbnail_url || m?.thumbnailUrl || post.thumbnail_url}
                            autoplay
                            muted
                            loop={false}
                            aspectRatio="16/9"
                            className="w-full"
                            testId={`feed-video-${post.post_id}-${i}`}
                          />
                        ) : effectiveType === 'youtube' ? (
                          <div className="relative w-full aspect-video">
                            <iframe src={`https://www.youtube.com/embed/${url.replace(/.*(?:v=|youtu\.be\/)([^&]+).*/, '$1')}`}
                              className="w-full h-full" allowFullScreen title="YouTube" />
                          </div>
                        ) : effectiveType === 'image' ? (
                          <SmartImage src={src} alt=""
                            smallSrc={typeof m === 'object' ? m?.small_url : undefined}
                            mediumSrc={typeof m === 'object' ? m?.medium_url : undefined}
                            largeSrc={typeof m === 'object' ? m?.large_url : undefined}
                            smallSrcAvif={typeof m === 'object' ? m?.small_url_avif : undefined}
                            mediumSrcAvif={typeof m === 'object' ? m?.medium_url_avif : undefined}
                            largeSrcAvif={typeof m === 'object' ? m?.large_url_avif : undefined}
                            testId={`feed-image-${post.post_id}-${i}`}
                            onError={(e) => { e.currentTarget.style.display = 'none'; }} />
                        ) : (
                          <a href={src} target="_blank" rel="noreferrer"
                            className="block p-3 text-xs underline truncate"
                            style={{ color: 'var(--jp-primary)' }}>{m?.name || url}</a>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              <div className="flex items-center px-2 py-2">
                <button onClick={() => toggleLike(post.post_id)} data-testid={`like-${post.post_id}`}
                  className="flex items-center gap-1.5 px-2 py-2 rounded-lg text-xs font-['Manrope'] font-medium transition-colors"
                  style={{ color: post.is_liked ? '#E01C2E' : 'var(--jp-text-secondary)' }}>
                  <Heart size={16} weight={post.is_liked ? 'fill' : 'regular'} /> {post.likes_count || 0}
                </button>
                <ReactionBar postId={post.post_id} />
                <button onClick={() => toggleComments(post.post_id)}
                  data-testid={`comment-btn-${post.post_id}`}
                  aria-expanded={!!openComments[post.post_id]}
                  aria-controls={`comments-section-${post.post_id}`}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-['Manrope'] transition-colors hover:bg-black/5"
                  style={{ color: openComments[post.post_id] ? 'var(--jp-primary)' : 'var(--jp-text-secondary)' }}>
                  <ChatCircle size={16} weight={openComments[post.post_id] ? 'fill' : 'regular'} /> {post.comments_count || 0}
                </button>
                {post.user_id !== user?.user_id && (
                  <button onClick={async () => {
                    setTipTarget({ type: 'post', id: post.post_id, recipientName: `${post.first_name} ${post.last_name}`, recipientId: post.user_id });
                    setTipAmount(''); setTipMessage(''); setTipError(''); setTipSettings(null);
                    // iter141nineF — fetch author tip presets/thank-you message
                    try {
                      const r = await axios.get(`${API}/api/users/${post.user_id}/tip-settings`);
                      setTipSettings(r.data);
                      // Auto-pick the middle preset for fastest "1 tap to send"
                      if (r.data?.enabled && Array.isArray(r.data.presets) && r.data.presets.length > 0) {
                        const mid = r.data.presets[Math.floor(r.data.presets.length / 2)];
                        setTipAmount(String(mid));
                      }
                    } catch { /* fallback to default presets */ }
                  }}
                    data-testid={`tip-${post.post_id}`}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-['Manrope'] font-bold transition-all"
                    style={{ color: '#E01C2E' }}>
                    <HandCoins size={16} weight="fill" /> Tip{post.tips_count > 0 ? ` ${post.tips_count}` : ''}
                  </button>
                )}
                <button onClick={() => setSharingPost(post)}
                  data-testid={`share-${post.post_id}`}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-['Manrope'] transition-colors hover:bg-black/5"
                  style={{ color: 'var(--jp-text-secondary)' }}>
                  <ShareNetwork size={16} /> {post.shares_count || 0}
                </button>
              </div>

              {openComments[post.post_id] && (
                <CommentSection
                  postId={post.post_id}
                  autoFocus
                  currentUser={user}
                  onCountChange={(delta) => bumpCommentCount(post.post_id, delta)}
                />
              )}
            </div>,
          ])}
        </div>
      </div>

      {/* Tip Modal */}
      {tipTarget && (
        <TipModal
          target={tipTarget}
          settings={tipSettings}
          amount={tipAmount}
          setAmount={setTipAmount}
          message={tipMessage}
          setMessage={setTipMessage}
          sending={tipSending}
          error={tipError}
          onClose={() => setTipTarget(null)}
          onSend={async () => {
            setTipError('');
            const amt = parseFloat(tipAmount);
            if (!amt || amt < 50) { setTipError('Minimum 50 XAF'); return; }
            setTipSending(true);
            try {
              await axios.post(`${API}/api/feed/tip`, {
                target_type: tipTarget.type,
                target_id: tipTarget.id,
                amount: amt,
                message: tipMessage,
              }, { withCredentials: true });
              setTipTarget(null);
              refreshUser();
              loadPosts();
            } catch (err) {
              setTipError(err.response?.data?.detail || 'Erreur');
            } finally {
              setTipSending(false);
            }
          }}
        />
      )}

      {/* Story Creator */}
      {showStoryCreate && (
        <StoryCreator
          text={storyText}
          setText={setStoryText}
          bg={storyBg}
          setBg={setStoryBg}
          photoPreview={storyPhotoPreview}
          onClose={() => {
            setShowStoryCreate(false);
            setStoryText('');
            if (storyPhotoPreview) { URL.revokeObjectURL(storyPhotoPreview); }
            setStoryPhoto(null);
            setStoryPhotoPreview(null);
          }}
          onAddPhoto={() => {
            // Open MediaFilterEditor in capture mode (camera-first for stories).
            setFilterEditor({ open: true, files: [], mode: 'capture', target: 'story' });
          }}
          onClearPhoto={() => {
            if (storyPhotoPreview) URL.revokeObjectURL(storyPhotoPreview);
            setStoryPhoto(null);
            setStoryPhotoPreview(null);
          }}
          onPublish={async () => {
            if (!storyText.trim() && !storyPhoto) return;
            try {
              if (storyPhoto) {
                // Upload the filtered photo first, then create the story
                // referencing the resulting URL.
                const fd = new FormData();
                fd.append('file', storyPhoto);
                const { data: up } = await axios.post(`${API}/api/upload/`, fd, { withCredentials: true });
                await axios.post(`${API}/api/feed/stories`, {
                  text: storyText, background_color: storyBg, image_url: up.url,
                  filter_preset: storyPreset || '',
                }, { withCredentials: true });
              } else {
                await axios.post(`${API}/api/feed/stories`, {
                  text: storyText, background_color: storyBg,
                }, { withCredentials: true });
              }
              setShowStoryCreate(false);
              setStoryText('');
              if (storyPhotoPreview) URL.revokeObjectURL(storyPhotoPreview);
              setStoryPhoto(null);
              setStoryPhotoPreview(null);
              setStoryPreset('');
              loadStories();
            } catch {}
          }}
        />
      )}

      {/* Story Viewer */}
      {storyViewer && (
        <StoryViewer
          group={storyViewer.group}
          index={storyViewer.index}
          onChangeIndex={(i) => setStoryViewer(s => ({ ...s, index: i }))}
          onClose={() => setStoryViewer(null)}
        />
      )}

      {/* Edit post */}
      {editingPost && (
        <EditPostModal
          post={editingPost}
          onClose={() => setEditingPost(null)}
          onUpdated={(updated) => { handlePostUpdated(updated); }}
        />
      )}

      {/* Share post */}
      {sharingPost && (
        <SharePostModal
          post={sharingPost}
          onClose={() => setSharingPost(null)}
          onShared={() => { loadPosts(); }}
        />
      )}

      {/* Global FAB "+" */}
      <CreateFab
        onCreatePost={focusComposer}
        onCreateReel={() => navigate('/reels?create=1')}
        onCreateStory={() => setShowStoryCreate(true)}
      />

      {/* First-login Groups onboarding */}
      <GroupsOnboardingModal
        open={showOnboarding}
        onClose={() => { setShowOnboarding(false); refreshUser?.(); }}
      />

      {/* Iter83 P0 — Avatar / Cover cropper, shared between Feed and Profile
          via the common user record so edits are instantly visible everywhere. */}
      <ImageCropper
        open={cropperFor === 'avatar'}
        shape="round"
        title={t('feed.photo_de_profil')}
        onCancel={() => setCropperFor(null)}
        onCrop={async (blob) => {
          try {
            await uploadAvatar(blob);
            await refreshUser?.();
            setCropperFor(null);
            toast.success('Photo de profil mise à jour');
          } catch (e) {
            toast.error(uploadErrorMessage(e, 'Échec du téléversement'));
          }
        }}
      />
      <ImageCropper
        open={cropperFor === 'cover'}
        shape="rect3x1"
        title={t('feed.photo_de_couverture')}
        initialPositionY={user?.cover_position_y ?? 50}
        onCancel={() => setCropperFor(null)}
        onCrop={async (blob, { positionY }) => {
          try {
            await uploadCover(blob, positionY);
            await refreshUser?.();
            setCropperFor(null);
            toast.success('Photo de couverture mise à jour');
          } catch (e) {
            toast.error(uploadErrorMessage(e, 'Échec du téléversement'));
          }
        }}
      />
      {/* iter147 — Unified media filter editor (CSS/canvas Phase 1).
           Shared with chat / stories / profile in subsequent integrations. */}
      <MediaFilterEditor
        open={filterEditor.open}
        files={filterEditor.files}
        mode={filterEditor.mode}
        onClose={() => setFilterEditor({ open: false, files: [], mode: 'upload' })}
        onApply={async (filteredFiles, presetId) => {
          // iter165 — bug fix: presetId was previously undefined here because
          // the callback wasn't destructuring the 2nd arg. The chosen filter
          // preset was lost on submit. Also, we explicitly close the editor
          // here so the user lands BACK on the composer with the Publier
          // button right under their thumb.
          if (filterEditor.target === 'story') {
            const f = filteredFiles[0];
            if (!f) return;
            if (storyPhotoPreview) URL.revokeObjectURL(storyPhotoPreview);
            setStoryPhoto(f);
            setStoryPhotoPreview(URL.createObjectURL(f));
            setStoryPreset(presetId || '');
            setFilterEditor({ open: false, files: [], mode: 'upload' });
            return;
          }
          await handleFilterApply(filteredFiles, presetId);
          setFilterEditor({ open: false, files: [], mode: 'upload' });
        }}
      />
    </div>
  );
}

function TipModal({ target, settings, amount, setAmount, message, setMessage, sending, error, onClose, onSend }) {
  const { t } = useTranslation();
  // iter141nineF — Use author-configured presets when available, fall back
  // to the global default. If the author has explicitly disabled tips, the
  // chips area shows nothing and only the custom amount input is used.
  const presets = (settings?.enabled && Array.isArray(settings?.presets) && settings.presets.length)
    ? settings.presets
    : [100, 500, 1000, 2500];
  const cols = Math.min(presets.length, 4);
  const gridColsCls = { 1: 'grid-cols-1', 2: 'grid-cols-2', 3: 'grid-cols-3', 4: 'grid-cols-4' }[cols] || 'grid-cols-4';
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative bg-white rounded-2xl max-w-sm w-full p-6 shadow-2xl jp-animate-scaleIn" data-testid="tip-modal">
        <div className="flex justify-between items-center mb-2">
          <div>
            <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>
              <HandCoins size={20} className="inline mr-1" weight="fill" style={{ color: '#E01C2E' }} /> Soutenir l'auteur
            </h3>
            <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
              à {target.recipientName}{settings?.is_pro ? ' · ✨ Pro' : ''}
            </p>
          </div>
          <button onClick={onClose} data-testid="tip-close"><X size={18} /></button>
        </div>
        {settings?.message && (
          <div
            data-testid="tip-author-message"
            className="rounded-lg p-3 mb-3 text-xs italic"
            style={{ background: 'rgba(224,28,46,0.06)', color: 'var(--jp-text)' }}
          >
            « {settings.message} »
          </div>
        )}
        {error && <div className="jp-alert jp-alert-error mb-3" data-testid="tip-error">{error}</div>}
        <div className={`grid ${gridColsCls} gap-2 my-3`}>
          {presets.map(v => (
            <button key={v} onClick={() => setAmount(String(v))} data-testid={`tip-quick-${v}`}
              className="p-2 rounded-lg text-xs font-bold font-['Manrope']"
              style={{ background: amount === String(v) ? 'var(--jp-primary)' : 'var(--jp-surface-secondary)', color: amount === String(v) ? '#fff' : 'var(--jp-text)' }}>
              {v}
            </button>
          ))}
        </div>
        <input type="number" min={50} step={50} value={amount} onChange={e => setAmount(e.target.value)}
          placeholder="Montant XAF (min 50)" className="jp-input text-sm mb-3" data-testid="tip-amount" autoFocus />
        <input value={message} onChange={e => setMessage(e.target.value)} placeholder={t('feed.message_optionnel')}
          className="jp-input text-sm mb-3" data-testid="tip-message" />
        <button onClick={onSend} disabled={sending || !amount}
          className="jp-btn jp-btn-primary jp-btn-full jp-btn-lg" data-testid="tip-send">
          {sending ? 'Envoi…' : `Envoyer ${amount || '0'} XAF`}
        </button>
      </div>
    </div>
  );
}

function StoryCreator({ text, setText, bg, setBg, onClose, onPublish, onAddPhoto, photoPreview, onClearPhoto }) {
  const { t } = useTranslation();
  const colors = ['#0F056B', '#E01C2E', '#10B981', '#F59E0B', '#9333EA', '#000000'];
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/80" onClick={onClose} />
      {/* iter165 — Make the modal fully responsive: max-h capped to viewport,
          internal scroll, and the Publier CTA pinned to the bottom via a
          flex column. Previous version used min-height:480px + absolute
          bottom button which got cut off on small Androids (<560px height)
          and iOS devices once the on-screen keyboard appeared. */}
      <div
        className="relative rounded-2xl max-w-sm w-full text-center overflow-hidden flex flex-col"
        style={{
          background: photoPreview ? `url(${photoPreview}) center/cover` : bg,
          minHeight: 'min(480px, calc(100vh - 32px))',
          maxHeight: 'calc(100vh - 32px)',
        }}
        data-testid="story-creator">
        {photoPreview && <div className="absolute inset-0" style={{ background: 'rgba(0,0,0,0.35)' }} />}

        {/* Scrollable content */}
        <div className="relative flex-1 overflow-y-auto p-6 pb-2">
          <button onClick={onClose} className="absolute top-2 right-2 text-white p-1" data-testid="story-creator-close" aria-label="Fermer"><X size={22} /></button>
          {photoPreview && (
            <button onClick={onClearPhoto}
              data-testid="story-clear-photo"
              className="absolute top-2 left-2 text-white/85 text-[11px] font-['Manrope'] underline">
              Retirer photo
            </button>
          )}
          <textarea value={text} onChange={e => setText(e.target.value)} placeholder={t('feed.votre_story')}
            className="w-full bg-transparent text-white text-center font-['Outfit'] text-2xl font-bold resize-none outline-none mt-12"
            style={{ minHeight: '120px', textShadow: photoPreview ? '0 1px 4px rgba(0,0,0,0.6)' : 'none' }}
            data-testid="story-text-input" />
          <div className="flex justify-center gap-2 mt-4 flex-wrap">
            {colors.map((c, i) => (
              <button key={c} onClick={() => setBg(c)} data-testid={`story-bg-${i}`}
                disabled={!!photoPreview}
                className={`w-8 h-8 rounded-full transition-transform ${bg === c ? 'scale-125 ring-2 ring-white' : ''} ${photoPreview ? 'opacity-30' : ''}`}
                style={{ background: c }} />
            ))}
          </div>
          {!photoPreview && (
            <button
              type="button"
              onClick={onAddPhoto}
              data-testid="story-add-photo"
              className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-['Manrope'] font-semibold text-white"
              style={{ background: 'rgba(255,255,255,0.18)', border: '1px solid rgba(255,255,255,0.35)' }}
            >
              📸 Ajouter une photo
            </button>
          )}
        </div>

        {/* Sticky bottom Publier CTA — always visible regardless of content */}
        <div
          className="relative px-5 pt-2"
          style={{ paddingBottom: 'calc(1rem + env(safe-area-inset-bottom, 0px))' }}
        >
          <button onClick={onPublish} disabled={!text.trim() && !photoPreview}
            className="w-full py-3 rounded-full bg-white text-black font-['Manrope'] font-bold disabled:opacity-40"
            data-testid="story-publish">
            Publier
          </button>
        </div>
      </div>
    </div>
  );
}

function StoryViewer({ group, index, onChangeIndex, onClose }) {
  const story = group.stories[index];
  useEffect(() => {
    if (!story) return;
    axios.post(`${API}/api/feed/stories/${story.story_id}/view`, {}, { withCredentials: true }).catch(() => {});
    const t = setTimeout(() => {
      if (index < group.stories.length - 1) onChangeIndex(index + 1);
      else onClose();
    }, 5000);
    return () => clearTimeout(t);
    // eslint-disable-next-line
  }, [index]);
  if (!story) return null;
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black" data-testid="story-viewer">
      <button onClick={onClose} className="absolute top-4 right-4 text-white z-10" data-testid="story-viewer-close"><X size={24} /></button>
      <div className="absolute top-2 left-2 right-2 flex gap-1 z-10">
        {group.stories.map((_, i) => (
          <div key={i} className="flex-1 h-1 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.3)' }}>
            <div className="h-full bg-white transition-all" style={{ width: i < index ? '100%' : i === index ? '100%' : '0%', transitionDuration: i === index ? '5s' : '0s' }} />
          </div>
        ))}
      </div>
      <div className="absolute top-6 left-4 right-12 flex items-center gap-2 z-10 pt-2">
        <div className="w-8 h-8 rounded-full bg-white/20 flex items-center justify-center text-white text-xs font-bold">
          {group.avatar ? <img src={group.avatar} alt="" className="w-full h-full rounded-full object-cover" /> : (group.name[0] || '?').toUpperCase()}
        </div>
        <span className="text-white text-sm font-['Manrope'] font-bold">{group.name}</span>
      </div>

      <div className="w-full h-full flex items-center justify-center p-8" style={{ background: story.background_color }}>
        {story.image_url ? (
          // iter239p — story image is orientation-aware. Portrait/landscape
          // get categorical aspect ratios; container caps to viewport via the
          // outer flex layout's centering.
          <SmartImage src={story.image_url} alt=""
            testId={`story-image-${story.story_id || 'cur'}`}
            style={{ maxWidth: '100%', maxHeight: '100%' }} />
        ) : (
          <p className="text-white text-center font-['Outfit'] text-3xl font-bold">{story.text}</p>
        )}
      </div>

      <button onClick={() => index > 0 ? onChangeIndex(index - 1) : onClose()}
        className="absolute left-0 top-0 bottom-0 w-1/3" data-testid="story-prev" />
      <button onClick={() => index < group.stories.length - 1 ? onChangeIndex(index + 1) : onClose()}
        className="absolute right-0 top-0 bottom-0 w-1/3" data-testid="story-next" />
    </div>
  );
}

function timeSince(iso) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}j`;
}
