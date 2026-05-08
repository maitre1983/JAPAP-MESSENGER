/**
 * FollowSuggestions — compact horizontal card list of users the current
 * viewer doesn't follow yet, ranked by mutual connections + popularity.
 *
 * Used on:
 *   - PostDetailPage (below the post)
 *   - (future) FeedPage empty state, onboarding screen, profile-bottom
 *
 * Mounts its own state (optimistic follow/unfollow + dismiss) so you can
 * drop it anywhere with a single <FollowSuggestions/>.
 */
import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { useTranslation } from 'react-i18next';
import { UserPlus, UserMinus, User as UserIcon, SealCheck, Crown } from '@phosphor-icons/react';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;

export default function FollowSuggestions({ limit = 3, compact = false }) {
  const { t } = useTranslation();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(
        `${API}/api/users/me/suggestions?limit=${limit}`,
        { withCredentials: true },
      );
      setItems((data.items || []).map((u) => ({ ...u, is_following: false })));
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => { load(); }, [load]);

  const toggleFollow = async (u) => {
    if (busyId) return;
    setBusyId(u.user_id);
    const prev = u.is_following;
    // Optimistic flip for responsiveness.
    setItems((list) => list.map((x) =>
      x.user_id === u.user_id ? { ...x, is_following: !prev } : x));
    try {
      const res = prev
        ? await axios.delete(`${API}/api/users/${u.user_id}/follow`, { withCredentials: true })
        : await axios.post(`${API}/api/users/${u.user_id}/follow`, {}, { withCredentials: true });
      const serverFollowed = !!res?.data?.followed;
      setItems((list) => list.map((x) =>
        x.user_id === u.user_id ? { ...x, is_following: serverFollowed } : x));
    } catch (e) {
      // Rollback
      setItems((list) => list.map((x) =>
        x.user_id === u.user_id ? { ...x, is_following: prev } : x));
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setBusyId(null); }
  };

  if (loading) {
    return (
      <div className="jp-card p-4 mt-4" data-testid="follow-suggestions-loading">
        <div className="h-4 w-40 rounded animate-pulse mb-3"
          style={{ background: 'var(--jp-surface-secondary)' }} />
        <div className="flex gap-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-24 flex-1 rounded-xl animate-pulse"
              style={{ background: 'var(--jp-surface-secondary)' }} />
          ))}
        </div>
      </div>
    );
  }

  if (items.length === 0) return null;

  return (
    <section className="jp-card p-4 mt-4" data-testid="follow-suggestions">
      <header className="mb-3">
        <h3 className="font-['Outfit'] text-base font-bold"
          style={{ color: 'var(--jp-text)' }}
          data-testid="follow-suggestions-title">
          {t('social.suggested_title')}
        </h3>
        {!compact && (
          <p className="text-[11px] font-['Manrope']"
            style={{ color: 'var(--jp-text-muted)' }}>
            {t('social.suggested_subtitle')}
          </p>
        )}
      </header>

      <div className="grid grid-cols-3 gap-2">
        {items.map((u) => (
          <div key={u.user_id} className="relative rounded-xl p-3 text-center overflow-hidden"
            data-testid={`suggestion-${u.user_id}`}
            style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
            <Link to={`/users/${u.user_id}`} className="block">
              <div className="relative inline-block mb-2">
                <div className="w-12 h-12 rounded-full overflow-hidden flex items-center justify-center mx-auto"
                  style={{ background: 'var(--jp-surface)' }}>
                  {u.avatar
                    ? <img src={u.avatar.startsWith('http') ? u.avatar : `${API}${u.avatar}`}
                        alt="" className="w-full h-full object-cover" />
                    : <UserIcon size={20} style={{ color: 'var(--jp-text-muted)' }} />}
                </div>
                {u.is_pro ? (
                  <Crown size={14} weight="fill"
                    className="absolute -top-1 -right-1 p-0.5 rounded-full"
                    style={{ color: '#F59E0B', background: 'var(--jp-surface)' }} />
                ) : u.is_verified ? (
                  <SealCheck size={14} weight="fill"
                    className="absolute -top-1 -right-1 p-0.5 rounded-full"
                    style={{ color: 'var(--jp-primary)', background: 'var(--jp-surface)' }} />
                ) : null}
              </div>
              <p className="text-xs font-['Outfit'] font-bold truncate"
                style={{ color: 'var(--jp-text)' }}>
                {`${u.first_name || ''} ${u.last_name || ''}`.trim() || u.username || 'Utilisateur'}
              </p>
              <p className="text-[10px] font-['Manrope'] truncate"
                style={{ color: 'var(--jp-text-muted)' }}>
                @{u.username || u.user_id.slice(-6)}
              </p>
              {u.mutual_hits > 0 ? (
                <p className="text-[10px] font-['Manrope'] mt-0.5"
                  style={{ color: 'var(--jp-primary)' }}>
                  {t(u.mutual_hits > 1 ? 'social.mutual_many' : 'social.mutual_one', { count: u.mutual_hits })}
                </p>
              ) : (
                <p className="text-[10px] font-['Manrope'] mt-0.5"
                  style={{ color: 'var(--jp-text-muted)' }}>
                  {u.followers_count || 0} followers
                </p>
              )}
            </Link>
            <button onClick={() => toggleFollow(u)} disabled={busyId === u.user_id}
              data-testid={`suggestion-follow-${u.user_id}`}
              className={`w-full mt-2 inline-flex items-center justify-center gap-1 text-[11px] font-['Manrope'] font-bold py-1.5 rounded-full transition-colors disabled:opacity-60`}
              style={u.is_following
                ? { background: 'transparent', color: 'var(--jp-text)', border: '1px solid var(--jp-border)' }
                : { background: 'var(--jp-primary)', color: '#fff' }}>
              {u.is_following
                ? <><UserMinus size={12} weight="bold" /> {t('social.following_action')}</>
                : <><UserPlus size={12} weight="bold" /> {t('social.follow_action')}</>}
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
