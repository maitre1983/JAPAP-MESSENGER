/**
 * GroupsOnboardingModal — shown once per user right after first login
 * when `onboarding_completed` is false in /api/auth/me.
 *
 * Flow:
 *  - Loads up to 5 group suggestions (country/lang-ranked) via /api/groups/suggestions
 *  - User can join individually, "Join All", or Skip
 *  - Fires analytics events: groups_onboarding_viewed, group_joined_from_onboarding,
 *    join_all_clicked, groups_onboarding_skipped
 *  - Marks onboarding as completed on close so it never re-appears
 *
 * Kept intentionally lightweight: no spinner while loading (optimistic render),
 * instant interactions, mobile-first, <300ms perceived load.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { X, UsersThree, Check, Lightning, Sparkle } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const track = (name, props = {}) => {
  // Fire-and-forget — never block the UI on analytics.
  axios.post(`${API}/api/analytics/event`, { name, props },
    { withCredentials: true, timeout: 2000 }).catch(() => {});
};

export default function GroupsOnboardingModal({ open, onClose }) {
  const [items, setItems] = useState([]);
  const [joined, setJoined] = useState(new Set());
  const [joining, setJoining] = useState(null);
  const [bulkJoining, setBulkJoining] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const loadSuggestions = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/groups/suggestions?limit=5`,
        { withCredentials: true, timeout: 3000 });
      setItems(data.items || []);
    } catch { setItems([]); }
    finally { setLoaded(true); }
  }, []);

  useEffect(() => {
    if (!open) return;
    track('groups_onboarding_viewed');
    loadSuggestions();
  }, [open, loadSuggestions]);

  const markComplete = useCallback(() => {
    axios.post(`${API}/api/auth/onboarding/complete`, {}, { withCredentials: true }).catch(() => {});
  }, []);

  const joinOne = async (gid) => {
    setJoining(gid);
    try {
      await axios.post(`${API}/api/groups/${gid}/join`, {}, { withCredentials: true });
      setJoined(prev => new Set(prev).add(gid));
      track('group_joined_from_onboarding', { group_id: gid });
    } catch { toast.error('Erreur'); }
    finally { setJoining(null); }
  };

  const joinAll = async () => {
    setBulkJoining(true);
    track('join_all_clicked');
    try {
      const { data } = await axios.post(`${API}/api/groups/join-all`, {}, { withCredentials: true });
      setJoined(new Set(data.group_ids || items.map(g => g.group_id)));
      toast.success(`Vous avez rejoint ${data.joined_count} groupes`);
      // Small delay so user sees the confirmation before the modal disappears.
      setTimeout(close, 900);
    } catch { toast.error('Erreur'); }
    finally { setBulkJoining(false); }
  };

  const skip = () => {
    track('groups_onboarding_skipped');
    close();
  };

  const close = () => {
    markComplete();
    onClose?.();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80] flex items-end sm:items-center justify-center p-0 sm:p-4"
      style={{ background: 'rgba(0,0,0,0.7)' }}
      data-testid="groups-onboarding-modal">
      <div className="jp-card-elevated w-full sm:max-w-md p-5 jp-animate-slideUp"
        style={{ borderRadius: '24px 24px 0 0', maxHeight: '92vh', overflow: 'auto' }}>
        <div className="flex items-start justify-between mb-1">
          <div className="flex items-center gap-2">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #0F056B, #5B21B6)', color: 'white' }}>
              <UsersThree size={18} weight="duotone" />
            </div>
            <div>
              <h3 className="font-['Outfit'] text-lg font-extrabold">Rejoignez vos communautés</h3>
              <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>Sélectionnées pour vous</p>
            </div>
          </div>
          <button onClick={skip} data-testid="onboarding-skip-x"
            className="p-1.5 rounded-full hover:bg-black/5" style={{ color: 'var(--jp-text-muted)' }}>
            <X size={18} />
          </button>
        </div>

        <p className="text-sm mt-2 mb-3" style={{ color: 'var(--jp-text-secondary)' }}>
          Ces groupes correspondent à vos centres d'intérêt. Rejoignez-en quelques-uns pour ne rien manquer.
        </p>

        {!loaded && (
          <div className="space-y-2 py-2">
            {[0, 1, 2].map(i => (
              <div key={i} className="h-14 rounded-xl animate-pulse" style={{ background: 'var(--jp-surface-secondary)' }} />
            ))}
          </div>
        )}

        {loaded && items.length === 0 && (
          <div className="text-center py-6 text-sm" style={{ color: 'var(--jp-text-muted)' }}
            data-testid="onboarding-empty">
            Aucune suggestion disponible pour le moment.
          </div>
        )}

        {loaded && items.length > 0 && (
          <>
            <div className="space-y-2" data-testid="onboarding-groups-list">
              {items.map(g => {
                const isJoined = joined.has(g.group_id);
                return (
                  <div key={g.group_id} className="flex items-center gap-3 p-3 rounded-xl transition-colors"
                    style={{ background: isJoined ? 'var(--jp-primary-subtle)' : 'var(--jp-surface-secondary)' }}
                    data-testid={`onboarding-group-${g.group_id}`}>
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
                      style={{ background: 'linear-gradient(135deg, #0F056B, #5B21B6)', color: 'white' }}>
                      <UsersThree size={18} weight="duotone" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="font-['Manrope'] font-bold text-sm truncate">{g.name}</div>
                      <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
                        {g.members_count} membre{g.members_count > 1 ? 's' : ''}
                      </div>
                    </div>
                    {isJoined ? (
                      <span className="text-xs px-2.5 py-1 rounded-full flex items-center gap-1 font-bold"
                        style={{ background: '#D1FAE5', color: '#065F46' }}>
                        <Check size={11} weight="bold" /> Rejoint
                      </span>
                    ) : (
                      <button onClick={() => joinOne(g.group_id)} disabled={joining === g.group_id}
                        data-testid={`onboarding-join-${g.group_id}`}
                        className="jp-btn jp-btn-sm text-xs jp-btn-primary">
                        {joining === g.group_id ? '...' : 'Rejoindre'}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="flex gap-2 mt-4">
              <button onClick={skip} data-testid="onboarding-skip"
                className="jp-btn jp-btn-ghost flex-1 text-sm">
                Ignorer
              </button>
              <button onClick={joinAll} disabled={bulkJoining || joined.size === items.length}
                data-testid="onboarding-join-all"
                className="jp-btn flex-1 text-sm"
                style={{
                  background: 'linear-gradient(135deg, #0F056B 0%, #5B21B6 100%)',
                  color: 'white',
                  opacity: (bulkJoining || joined.size === items.length) ? 0.5 : 1,
                }}>
                <Lightning size={14} weight="fill" />
                {bulkJoining ? 'Traitement…' : joined.size === items.length ? 'Tout rejoint' : 'Tout rejoindre'}
              </button>
            </div>

            <div className="flex items-center gap-1 justify-center mt-3 text-[10px]"
              style={{ color: 'var(--jp-text-muted)' }}>
              <Sparkle size={10} weight="fill" />
              Astuce : les utilisateurs qui rejoignent ≥1 groupe ont 3× plus de rétention
            </div>
          </>
        )}
      </div>
    </div>
  );
}
