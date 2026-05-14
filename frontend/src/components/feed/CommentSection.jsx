/**
 * CommentSection — inline accordion with the comment list + a composer.
 *
 * Mounted under a post card both on the feed (collapsible) and on the
 * post detail page (always open). Owns its own state so you can mount
 * several independently on the same screen.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { PaperPlaneRight, User as UserIcon } from '@phosphor-icons/react';
import { toast } from 'sonner';
import UserNameLink from '@/components/common/UserNameLink';

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

export default function CommentSection({ postId, autoFocus = false, onCountChange, currentUser }) {
  const [comments, setComments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const inputRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/feed/posts/${postId}/comments`, { withCredentials: true });
      setComments(Array.isArray(data) ? data : []);
    } catch (e) {
      // silent; keep an empty list
    } finally {
      setLoading(false);
    }
  }, [postId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (autoFocus && inputRef.current) {
      // Defer focus a tick to let the accordion expand animation settle, and
      // avoid scroll-jumping mobile keyboards when the section is not visible.
      setTimeout(() => inputRef.current?.focus(), 120);
    }
  }, [autoFocus]);

  const submit = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    // Optimistic insertion: the API returns the persisted row, but showing
    // the comment immediately feels more responsive on mobile.
    const tempId = `tmp_${Date.now()}`;
    const optimistic = {
      comment_id: tempId,
      text,
      user_id: currentUser?.user_id,
      first_name: currentUser?.first_name || '',
      last_name: currentUser?.last_name || '',
      avatar: currentUser?.avatar || '',
      created_at: new Date().toISOString(),
      _optimistic: true,
    };
    setComments(prev => [...prev, optimistic]);
    setDraft('');
    onCountChange?.(+1);
    try {
      const { data } = await axios.post(
        `${API}/api/feed/posts/${postId}/comments`,
        { text }, { withCredentials: true },
      );
      setComments(prev => prev.map(c => c.comment_id === tempId ? { ...optimistic, ...data, _optimistic: false } : c));
    } catch (e) {
      // rollback
      setComments(prev => prev.filter(c => c.comment_id !== tempId));
      onCountChange?.(-1);
      toast.error(e.response?.data?.detail || "Impossible d'envoyer le commentaire");
      setDraft(text);
    } finally {
      setSending(false);
    }
  };

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="px-3 pb-3 pt-2 border-t" style={{ borderColor: 'var(--jp-border)' }}
      data-testid={`comments-section-${postId}`}>
      {loading ? (
        <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</p>
      ) : comments.length === 0 ? (
        <p className="text-xs font-['Manrope'] py-2" style={{ color: 'var(--jp-text-muted)' }}
          data-testid={`comments-empty-${postId}`}>
          Soyez le premier à commenter.
        </p>
      ) : (
        <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
          {comments.map((c) => (
            <div key={c.comment_id} className="flex items-start gap-2" data-testid={`comment-${c.comment_id}`}>
              <div className="w-7 h-7 rounded-full overflow-hidden flex-shrink-0 flex items-center justify-center"
                style={{ background: 'var(--jp-surface-secondary)' }}>
                {c.avatar ? (
                  <img src={c.avatar} alt=""
                    loading="lazy" decoding="async"
                    className="w-full h-full object-cover" />
                ) : (
                  <UserIcon size={14} style={{ color: 'var(--jp-text-muted)' }} />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <div className="px-3 py-1.5 rounded-2xl inline-block max-w-full"
                  style={{ background: 'var(--jp-surface-secondary)' }}>
                  <p className="text-[11px] font-['Manrope'] font-semibold"
                    style={{ color: 'var(--jp-text)' }}>
                    <UserNameLink username={c.username} userId={c.user_id}>
                      {`${c.first_name || ''} ${c.last_name || ''}`.trim() || 'Utilisateur'}
                    </UserNameLink>
                  </p>
                  <p className="text-xs font-['Manrope'] whitespace-pre-wrap break-words"
                    style={{ color: 'var(--jp-text)' }}>{c.text}</p>
                </div>
                <p className="text-[10px] font-['Manrope'] mt-0.5 ml-2"
                  style={{ color: 'var(--jp-text-muted)' }}>{timeAgo(c.created_at)}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2 mt-2">
        <div className="w-7 h-7 rounded-full overflow-hidden flex-shrink-0 flex items-center justify-center"
          style={{ background: 'var(--jp-surface-secondary)' }}>
          {currentUser?.avatar ? (
            <img src={currentUser.avatar} alt="" className="w-full h-full object-cover" />
          ) : (
            <UserIcon size={14} style={{ color: 'var(--jp-text-muted)' }} />
          )}
        </div>
        <textarea
          ref={inputRef}
          rows={1}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          placeholder="Écrire un commentaire…"
          className="jp-input flex-1 text-sm py-2"
          style={{ resize: 'none', minHeight: 36 }}
          data-testid={`comment-input-${postId}`}
        />
        <button
          type="button"
          onClick={submit}
          disabled={!draft.trim() || sending}
          className="p-2 rounded-full transition-opacity disabled:opacity-40"
          style={{ background: 'var(--jp-primary)', color: '#fff' }}
          data-testid={`comment-submit-${postId}`}
          aria-label="Publier le commentaire">
          <PaperPlaneRight size={16} weight="fill" />
        </button>
      </div>
    </div>
  );
}
