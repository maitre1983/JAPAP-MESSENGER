/**
 * PostMenu — "..." action menu on every post card.
 * Owner sees: Modifier / Épingler / Supprimer / Partager
 * Non-owner sees: Partager only.
 *
 * EditPostModal — inline edit of text (media edit delegated to v2).
 *
 * SharePostModal — Phase-1 share picker: Feed / Groupe / Page / DM.
 *   Feed works end-to-end (creates a share post).
 *   Groupe/Page/DM record the share and show "Bientôt disponible" toast —
 *   wired to real surfaces in Phase 3.
 */
import { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  DotsThreeVertical, PencilSimple, Trash, PushPin, ShareNetwork, X, Check,
  UsersThree, FileText, ChatCircle, ListBullets, WhatsappLogo, FacebookLogo, LinkSimple,
} from '@phosphor-icons/react';
const API = process.env.REACT_APP_BACKEND_URL;

/** Build a stable, shareable URL for a given post — safe on SSR/missing window.
 *  We return the /api/og/... route so WhatsApp/Facebook scrapers can read the
 *  Open Graph meta tags; real users hitting the URL get a meta-refresh redirect
 *  to the SPA. See /app/backend/routes/og.py.
 */
export function buildPostShareUrl(postId) {
  const origin = (typeof window !== 'undefined' && window.location?.origin) || '';
  return `${origin}/api/og/post/${postId}`;
}

export function PostMenu({ post, currentUserId, onEdit, onDeleted, onPinned, onShare }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const isOwner = post.user_id === currentUserId;

  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    window.addEventListener('mousedown', close);
    return () => window.removeEventListener('mousedown', close);
  }, [open]);

  const pin = async () => {
    try {
      const { data } = await axios.post(`${API}/api/feed/posts/${post.post_id}/pin`, {}, { withCredentials: true });
      onPinned?.(post.post_id, data.is_pinned);
      toast.success(data.is_pinned ? t('post_menu.publication_epinglee') : t('post_menu.publication_desepinglee'));
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setOpen(false); }
  };

  const remove = async () => {
    if (!window.confirm('Supprimer cette publication ?')) return;
    try {
      await axios.delete(`${API}/api/feed/posts/${post.post_id}`, { withCredentials: true });
      onDeleted?.(post.post_id);
      toast.success('Publication supprimée');
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setOpen(false); }
  };

  return (
    <div className="relative" ref={ref}>
      <button data-testid={`post-menu-btn-${post.post_id}`}
        onClick={() => setOpen(v => !v)}
        className="p-1.5 rounded-full hover:bg-black/5 transition-colors"
        style={{ color: 'var(--jp-text-muted)' }}>
        <DotsThreeVertical size={18} />
      </button>
      {open && (
        <div className="absolute right-0 top-8 z-30 w-48 rounded-xl py-1.5 shadow-xl jp-animate-scaleIn"
          style={{ background: 'white', border: '1px solid var(--jp-border)' }}
          data-testid={`post-menu-${post.post_id}`}>
          {isOwner && (
            <>
              <MenuItem icon={PencilSimple} label="Modifier" testid={`post-edit-${post.post_id}`}
                onClick={() => { setOpen(false); onEdit?.(post); }} />
              <MenuItem icon={PushPin} label={post.is_pinned ? t('post_menu.desepingler') : t('post_menu.epingler')}
                testid={`post-pin-${post.post_id}`}
                active={!!post.is_pinned} onClick={pin} />
            </>
          )}
          <MenuItem icon={ShareNetwork} label="Partager" testid={`post-share-${post.post_id}`}
            onClick={() => { setOpen(false); onShare?.(post); }} />
          {isOwner && (
            <>
              <div className="h-px my-1" style={{ background: 'var(--jp-border)' }} />
              <MenuItem icon={Trash} label="Supprimer" testid={`post-delete-${post.post_id}`}
                danger onClick={remove} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function MenuItem({ icon: Icon, label, onClick, danger, active, testid }) {
  return (
    <button onClick={onClick} data-testid={testid}
      className="w-full flex items-center gap-2.5 px-3.5 py-2 text-sm font-['Manrope'] transition-colors hover:bg-black/5"
      style={{ color: danger ? '#E01C2E' : active ? 'var(--jp-primary)' : 'var(--jp-text)' }}>
      <Icon size={16} weight={active || danger ? 'fill' : 'regular'} />
      <span>{label}</span>
      {active && <Check size={14} weight="bold" className="ml-auto" />}
    </button>
  );
}

export function EditPostModal({ post, onClose, onUpdated }) {
  const [text, setText] = useState(post.text || '');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!text.trim()) { toast.error('Le texte ne peut pas être vide'); return; }
    setSaving(true);
    try {
      const { data } = await axios.put(`${API}/api/feed/posts/${post.post_id}`,
        { text: text.trim() }, { withCredentials: true });
      onUpdated?.(data);
      toast.success('Publication modifiée');
      onClose();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}
      onClick={onClose} data-testid="edit-post-modal">
      <div className="jp-card-elevated max-w-lg w-full p-5 jp-animate-scaleIn" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold">Modifier la publication</h3>
          <button onClick={onClose} className="p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}><X size={18} /></button>
        </div>
        <textarea data-testid="edit-post-textarea"
          value={text} onChange={e => setText(e.target.value)}
          rows={5} className="jp-input text-sm w-full" style={{ resize: 'vertical' }}
          autoFocus />
        <div className="flex gap-2 justify-end mt-3">
          <button className="jp-btn jp-btn-ghost" onClick={onClose}>Annuler</button>
          <button className="jp-btn jp-btn-primary" onClick={save} disabled={saving}
            data-testid="edit-post-save">
            {saving ? 'Enregistrement…' : 'Enregistrer'}
          </button>
        </div>
      </div>
    </div>
  );
}

export function SharePostModal({ post, onClose, onShared }) {
  const { t } = useTranslation();
  const [caption, setCaption] = useState('');
  const [target, setTarget] = useState('feed');
  const [targetId, setTargetId] = useState(null);
  const [groups, setGroups] = useState([]);
  const [pages, setPages] = useState([]);
  const [sending, setSending] = useState(false);
  const [copied, setCopied] = useState(false);

  const shareUrl = buildPostShareUrl(post.post_id);
  const shareText = (post.text || '').trim();
  // Short, WhatsApp-friendly message. Truncate long posts so the preview
  // is readable in the chat bubble and we stay under URL length limits.
  const previewBody = shareText
    ? (shareText.length > 160 ? `${shareText.slice(0, 160)}…` : shareText)
    : `Regarde cette publication sur JAPAP :`;
  const whatsappShare = `https://wa.me/?text=${encodeURIComponent(`${previewBody}\n${shareUrl}`)}`;
  const facebookShare = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(shareUrl)}`;

  const copyLink = async () => {
    const fallbackCopy = () => {
      const ta = document.createElement('textarea');
      ta.value = shareUrl;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    };

    let copied = false;
    try {
      // Clipboard API requires a secure context + user gesture.
      // In some sandboxed iframes / older Safari the call resolves
      // with a permission denial; fall back to execCommand instead
      // of surfacing an error to the user.
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(shareUrl);
        copied = true;
      }
    } catch (_) { /* fall through to execCommand */ }

    if (!copied) {
      try { copied = fallbackCopy(); } catch (_) { copied = false; }
    }

    if (!copied) {
      toast.error('Impossible de copier le lien');
      return;
    }
    setCopied(true);
    toast.success('Lien copié');
    setTimeout(() => setCopied(false), 1800);
    // Record the share server-side as 'external' so shares_count reflects it.
    axios.post(`${API}/api/feed/posts/${post.post_id}/share`,
      { target: 'external', caption: 'copy_link' }, { withCredentials: true })
      .then((r) => onShared?.(r.data))
      .catch(() => {});
  };

  const openExternal = (platform) => {
    const url = platform === 'whatsapp' ? whatsappShare : facebookShare;
    // Record the share (non-blocking) so counters/analytics stay truthful.
    axios.post(`${API}/api/feed/posts/${post.post_id}/share`,
      { target: 'external', caption: platform }, { withCredentials: true })
      .then((r) => onShared?.(r.data))
      .catch(() => {});
    // Open in a new tab/window. WhatsApp deep-links handle the mobile app switch.
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  useEffect(() => {
    if (target === 'group' && groups.length === 0) {
      axios.get(`${API}/api/groups?mine=true`, { withCredentials: true })
        .then(r => setGroups(r.data.items || []))
        .catch(() => {});
    }
    if (target === 'page' && pages.length === 0) {
      axios.get(`${API}/api/pages?mine=true`, { withCredentials: true })
        .then(r => setPages((r.data.items || []).filter(p => p.is_owner || p.owner_id)))
        .catch(() => {});
    }
  }, [target, groups.length, pages.length]);

  const submit = async () => {
    if ((target === 'group' || target === 'page') && !targetId) {
      toast.error(target === 'group' ? 'Choisissez un groupe' : 'Choisissez une page');
      return;
    }
    setSending(true);
    try {
      const payload = { target, caption };
      if (targetId) payload.target_id = targetId;
      const { data } = await axios.post(`${API}/api/feed/posts/${post.post_id}/share`,
        payload, { withCredentials: true });
      if (target === 'feed') toast.success('Publication partagée sur votre feed');
      else if (target === 'dm') toast.info(data.note || 'Partage enregistré — bientôt en DM.');
      else toast.success(target === 'group' ? t('post_menu.partage_dans_le_groupe') : t('post_menu.partage_sur_la_page'));
      onShared?.(data);
      onClose();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setSending(false); }
  };

  const TARGETS = [
    { id: 'feed',  icon: ListBullets, label: 'Mon feed', desc: t('post_menu.partager_avec_tous_mes_abonnes'), ready: true },
    { id: 'group', icon: UsersThree, label: 'Un groupe', desc: t('post_menu.choisir_un_groupe_dont_vous_etes_me'), ready: true },
    { id: 'page',  icon: FileText,   label: 'Une page', desc: t('post_menu.publier_sur_une_page_dont_vous_etes'), ready: true },
    { id: 'dm',    icon: ChatCircle, label: t('post_menu.message_prive'), desc: 'Disponible en Phase 3+', ready: false },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}
      onClick={onClose} data-testid="share-post-modal">
      <div className="jp-card-elevated max-w-lg w-full p-5 jp-animate-scaleIn" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold">Partager</h3>
          <button onClick={onClose} className="p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}><X size={18} /></button>
        </div>
        <div className="grid grid-cols-2 gap-2 mb-4">
          {TARGETS.map(t => (
            <button key={t.id}
              onClick={() => { setTarget(t.id); setTargetId(null); }}
              data-testid={`share-target-${t.id}`}
              className="text-left p-3 rounded-xl border-2 transition-all"
              style={{
                borderColor: target === t.id ? 'var(--jp-primary)' : 'var(--jp-border)',
                background: target === t.id ? 'var(--jp-primary-subtle)' : 'transparent',
                opacity: t.ready ? 1 : 0.6,
              }}>
              <div className="flex items-center gap-2 mb-1">
                <t.icon size={18} weight="duotone" style={{ color: target === t.id ? 'var(--jp-primary)' : 'var(--jp-text)' }} />
                <span className="text-sm font-['Manrope'] font-bold">{t.label}</span>
              </div>
              <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{t.desc}</p>
            </button>
          ))}
        </div>
        {target === 'group' && (
          <select value={targetId || ''} onChange={e => setTargetId(e.target.value)}
            className="jp-input text-sm mb-3" data-testid="share-group-picker">
            <option value="">— Choisir un groupe —</option>
            {groups.map(g => <option key={g.group_id} value={g.group_id}>{g.name}</option>)}
          </select>
        )}
        {target === 'page' && (
          <select value={targetId || ''} onChange={e => setTargetId(e.target.value)}
            className="jp-input text-sm mb-3" data-testid="share-page-picker">
            <option value="">— Choisir une page —</option>
            {pages.map(p => <option key={p.page_id} value={p.page_id}>{p.name}</option>)}
          </select>
        )}
        {(target === 'feed' || target === 'group' || target === 'page') && (
          <textarea data-testid="share-caption"
            value={caption} onChange={e => setCaption(e.target.value)}
            rows={2} placeholder={t('post_menu.ajoutez_un_commentaire_optionnel')}
            className="jp-input text-sm w-full" />
        )}

        {/* ══ External share — WhatsApp / Facebook / Copy link ══ */}
        <div className="mt-4 pt-3 border-t" style={{ borderColor: 'var(--jp-border)' }}
          data-testid="share-external-section">
          <p className="text-[11px] font-['Manrope'] font-bold uppercase tracking-wide mb-2"
            style={{ color: 'var(--jp-text-muted)' }}>
            Partager en externe
          </p>
          <div className="grid grid-cols-3 gap-2">
            <button type="button" onClick={() => openExternal('whatsapp')}
              data-testid="share-external-whatsapp"
              className="flex flex-col items-center justify-center gap-1 p-2.5 rounded-xl border transition-all hover:scale-[1.02] active:scale-[0.98]"
              style={{ borderColor: 'var(--jp-border)', background: 'rgba(37,211,102,0.08)' }}>
              <WhatsappLogo size={22} weight="fill" style={{ color: '#25D366' }} />
              <span className="text-[11px] font-['Manrope'] font-semibold">WhatsApp</span>
            </button>
            <button type="button" onClick={() => openExternal('facebook')}
              data-testid="share-external-facebook"
              className="flex flex-col items-center justify-center gap-1 p-2.5 rounded-xl border transition-all hover:scale-[1.02] active:scale-[0.98]"
              style={{ borderColor: 'var(--jp-border)', background: 'rgba(24,119,242,0.08)' }}>
              <FacebookLogo size={22} weight="fill" style={{ color: '#1877F2' }} />
              <span className="text-[11px] font-['Manrope'] font-semibold">Facebook</span>
            </button>
            <button type="button" onClick={copyLink}
              data-testid="share-external-copy-link"
              className="flex flex-col items-center justify-center gap-1 p-2.5 rounded-xl border transition-all hover:scale-[1.02] active:scale-[0.98]"
              style={{ borderColor: copied ? 'var(--jp-primary)' : 'var(--jp-border)',
                background: copied ? 'var(--jp-primary-subtle)' : 'transparent' }}>
              {copied ? (
                <>
                  <Check size={22} weight="bold" style={{ color: 'var(--jp-primary)' }} />
                  <span className="text-[11px] font-['Manrope'] font-semibold"
                    style={{ color: 'var(--jp-primary)' }}>{t('post_menu.copie')}</span>
                </>
              ) : (
                <>
                  <LinkSimple size={22} weight="bold" style={{ color: 'var(--jp-text)' }} />
                  <span className="text-[11px] font-['Manrope'] font-semibold">Copier le lien</span>
                </>
              )}
            </button>
          </div>
          <p className="text-[10px] font-['Manrope'] mt-2 truncate"
            style={{ color: 'var(--jp-text-muted)' }} data-testid="share-url-preview">
            {shareUrl}
          </p>
        </div>

        <div className="flex gap-2 justify-end mt-3">
          <button className="jp-btn jp-btn-ghost" onClick={onClose}>Annuler</button>
          <button className="jp-btn jp-btn-primary" onClick={submit} disabled={sending}
            data-testid="share-confirm">
            <ShareNetwork size={14} /> {sending ? 'Envoi…' : 'Partager'}
          </button>
        </div>
      </div>
    </div>
  );
}
