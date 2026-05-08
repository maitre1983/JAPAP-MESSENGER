/**
 * UserFollowListPage — shared page for /users/:userId/followers and :userId/following.
 *
 * Shows a paginated list of users with a Follow/Unfollow button inline. Reads
 * the `mode` prop from the route (or infers it from useMatch). We keep this
 * generic so both routes share 100% of the code.
 *
 * iter78: adds a search input (server-side filter) and a "remove follower"
 * option on the viewer's own /followers list.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { useParams, Link, useNavigate, useLocation } from 'react-router-dom';
import axios from 'axios';
import { ArrowLeft, UserPlus, UserMinus, User as UserIcon, MagnifyingGlass, Trash } from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;

export default function UserFollowListPage() {
  const { userId } = useParams();
  const loc = useLocation();
  const navigate = useNavigate();
  const { user: me } = useAuth();
  const mode = loc.pathname.endsWith('/following') ? 'following' : 'followers';
  const isOwnPage = me?.user_id === userId;

  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [subjectName, setSubjectName] = useState('');
  const [query, setQuery] = useState('');

  // Debounced search — 300ms — so we don't hit the backend on every keystroke.
  const debouncedQ = useDebouncedValue(query, 300);

  const load = useCallback(async (q) => {
    setLoading(true);
    try {
      const [listRes, profileRes] = await Promise.all([
        axios.get(`${API}/api/users/${userId}/${mode}`,
          { withCredentials: true, params: q ? { q } : {} }),
        axios.get(`${API}/api/users/profile/${userId}`, { withCredentials: true }).catch(() => ({ data: null })),
      ]);
      setItems(listRes.data.items || []);
      setTotal(listRes.data.total || 0);
      if (profileRes.data) {
        setSubjectName(
          `${profileRes.data.first_name || ''} ${profileRes.data.last_name || ''}`.trim()
          || profileRes.data.username || '',
        );
      }
    } catch (e) {
      toast.error("Impossible de charger la liste");
    } finally {
      setLoading(false);
    }
  }, [userId, mode]);

  useEffect(() => { load(debouncedQ); }, [load, debouncedQ]);

  // iter164 — Toggle now handles 4 states: not-following / pending / following / follow-back.
  // Backend response for POST /follow returns { followed, status, followers_count }
  // where status is 'accepted' (public + follow back) or 'pending' (private profile request).
  const toggleFollow = async (u) => {
    if (busyId) return;
    setBusyId(u.user_id);
    const wasFollowing = u.is_following;
    const wasPending = u.is_pending;
    // Optimistic UI: clear pending or flip following.
    setItems((prev) => prev.map((x) => x.user_id === u.user_id
      ? { ...x, is_following: !wasFollowing && !wasPending, is_pending: false }
      : x));
    try {
      const res = (wasFollowing || wasPending)
        ? await axios.delete(`${API}/api/users/${u.user_id}/follow`, { withCredentials: true })
        : await axios.post(`${API}/api/users/${u.user_id}/follow`, {}, { withCredentials: true });
      const status = res?.data?.status || (res?.data?.followed ? 'accepted' : 'none');
      setItems((prev) => prev.map((x) => x.user_id === u.user_id ? {
        ...x,
        is_following: status === 'accepted',
        is_pending: status === 'pending',
      } : x));
      if (status === 'pending') toast.info('Demande envoyée — en attente d\'acceptation');
      else if (status === 'accepted' && !wasFollowing) toast.success('Abonnement confirmé');
    } catch (e) {
      // Roll back the optimistic update on error.
      setItems((prev) => prev.map((x) => x.user_id === u.user_id
        ? { ...x, is_following: wasFollowing, is_pending: wasPending }
        : x));
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally {
      setBusyId(null);
    }
  };

  const removeFollower = async (followerId) => {
    if (busyId) return;
    if (!window.confirm('Retirer cette personne de vos followers ?')) return;
    setBusyId(followerId);
    // Optimistic removal from the list.
    const snapshot = items;
    setItems((prev) => prev.filter((x) => x.user_id !== followerId));
    setTotal((t) => Math.max(0, t - 1));
    try {
      await axios.delete(`${API}/api/users/me/followers/${followerId}`, { withCredentials: true });
      toast.success('Retiré de vos followers');
    } catch (e) {
      setItems(snapshot);
      setTotal(snapshot.length);
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setBusyId(null); }
  };

  const title = mode === 'followers' ? 'Followers' : 'Abonnements';

  return (
    <div className="max-w-xl mx-auto px-4 py-4" data-testid="user-follow-list-page">
      <button onClick={() => navigate(-1)} data-testid="follow-list-back"
        className="inline-flex items-center gap-1.5 text-sm font-['Manrope'] font-semibold mb-3"
        style={{ color: 'var(--jp-text-secondary)' }}>
        <ArrowLeft size={16} /> Retour
      </button>

      <header className="mb-3">
        <h1 className="font-['Outfit'] text-2xl font-extrabold"
          style={{ color: 'var(--jp-text)' }} data-testid="follow-list-title">
          {title}{subjectName ? ` · ${subjectName}` : ''}
        </h1>
        <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
          {total} {mode === 'followers' ? 'personne(s) vous suivent' : 'personne(s) suivies'}
        </p>
      </header>

      {/* Search bar */}
      <div className="relative mb-4">
        <MagnifyingGlass size={16} className="absolute left-3 top-1/2 -translate-y-1/2"
          style={{ color: 'var(--jp-text-muted)' }} />
        <input type="text" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="Rechercher dans la liste…"
          className="jp-input text-sm w-full pl-9"
          data-testid="follow-list-search" />
      </div>

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-16 rounded-xl animate-pulse"
              style={{ background: 'var(--jp-surface-secondary)' }} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="jp-card p-6 text-center" data-testid="follow-list-empty">
          <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
            {query ? 'Aucun résultat.' : (mode === 'followers' ? 'Aucun follower pour l’instant.' : 'Aucun abonnement pour l’instant.')}
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {items.map((u) => (
            <li key={u.user_id} className="jp-card flex items-center gap-3 p-3"
              data-testid={`follow-list-row-${u.user_id}`}>
              <Link to={`/users/${u.user_id}`} className="w-11 h-11 rounded-full overflow-hidden flex items-center justify-center flex-shrink-0"
                style={{ background: 'var(--jp-surface-secondary)' }}>
                {u.avatar
                  ? <img src={u.avatar.startsWith('http') ? u.avatar : `${API}${u.avatar}`} alt="" className="w-full h-full object-cover" />
                  : <UserIcon size={20} style={{ color: 'var(--jp-text-muted)' }} />}
              </Link>
              <div className="flex-1 min-w-0">
                <Link to={`/users/${u.user_id}`}
                  className="font-['Outfit'] text-sm font-bold truncate block"
                  style={{ color: 'var(--jp-text)' }}>
                  {`${u.first_name || ''} ${u.last_name || ''}`.trim() || u.username || 'Utilisateur'}
                </Link>
                <p className="text-[11px] font-['Manrope'] truncate"
                  style={{ color: 'var(--jp-text-muted)' }}>
                  @{u.username || u.user_id} · {u.followers_count || 0} followers
                </p>
              </div>
              {u.user_id === me?.user_id ? (
                <span className="jp-badge jp-badge-neutral">Vous</span>
              ) : (
                <div className="flex items-center gap-1">
                  {/* iter164 — 4-state Follow button:
                      - is_following=true              → "Abonné"  (jp-btn-ghost)
                      - is_pending=true                → "Demandé"  (jp-btn-ghost, italic)
                      - follows_me + !is_following     → "Suivre en retour" (jp-btn-primary)
                      - default                        → "Suivre"            (jp-btn-primary)
                  */}
                  <button onClick={() => toggleFollow(u)} disabled={busyId === u.user_id}
                    data-testid={`follow-toggle-${u.user_id}`}
                    data-follow-state={
                      u.is_following ? 'following' :
                      u.is_pending ? 'pending' :
                      u.follows_me ? 'follow-back' : 'follow'
                    }
                    className={`jp-btn ${u.is_following || u.is_pending ? 'jp-btn-ghost' : 'jp-btn-primary'} text-xs px-3 py-1.5 disabled:opacity-60`}>
                    {u.is_following
                      ? <><UserMinus size={13} /> Abonné</>
                      : u.is_pending
                        ? <><UserPlus size={13} /> Demandé</>
                        : u.follows_me
                          ? <><UserPlus size={13} /> Suivre en retour</>
                          : <><UserPlus size={13} /> Suivre</>}
                  </button>
                  {/* Remove follower — only on own followers list */}
                  {isOwnPage && mode === 'followers' && (
                    <button onClick={() => removeFollower(u.user_id)} disabled={busyId === u.user_id}
                      data-testid={`follow-remove-${u.user_id}`}
                      aria-label="Retirer de mes followers"
                      className="p-1.5 rounded-full transition-colors hover:bg-red-50 disabled:opacity-60"
                      title="Retirer des followers">
                      <Trash size={14} style={{ color: '#E01C2E' }} />
                    </button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Small inline hook to debounce a value without pulling a dependency.
function useDebouncedValue(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}
