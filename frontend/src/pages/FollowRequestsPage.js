/**
 * FollowRequestsPage — inbox of pending follow requests. Accept or decline
 * each row inline. The count shown on SettingsPage is auto-updated via
 * effect on axios success — we simply remove the row locally which is
 * enough since the total is re-queried each mount.
 */
import { useState, useEffect, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { ArrowLeft, Check, X as XIcon, User as UserIcon } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function FollowRequestsPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/users/me/follow-requests`, { withCredentials: true });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (e) {
      toast.error("Impossible de charger les demandes");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const resolveRequest = async (requestId, action) => {
    if (busyId) return;
    setBusyId(requestId);
    // Optimistic removal
    setItems((prev) => prev.filter((r) => r.request_id !== requestId));
    setTotal((t) => Math.max(0, t - 1));
    try {
      await axios.post(`${API}/api/users/me/follow-requests/${requestId}/${action}`,
        {}, { withCredentials: true });
      toast.success(action === 'accept' ? 'Demande acceptée' : 'Demande refusée');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
      // Refetch to restore the truth
      load();
    } finally { setBusyId(null); }
  };

  return (
    <div className="max-w-xl mx-auto px-4 py-4" data-testid="follow-requests-page">
      <button onClick={() => navigate(-1)} data-testid="requests-back"
        className="inline-flex items-center gap-1.5 text-sm font-['Manrope'] font-semibold mb-3"
        style={{ color: 'var(--jp-text-secondary)' }}>
        <ArrowLeft size={16} /> Retour
      </button>

      <h1 className="font-['Outfit'] text-2xl font-extrabold mb-1"
        style={{ color: 'var(--jp-text)' }} data-testid="requests-title">
        Demandes d'abonnement
      </h1>
      <p className="text-xs font-['Manrope'] mb-4" style={{ color: 'var(--jp-text-muted)' }}>
        {total} en attente
      </p>

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-16 rounded-xl animate-pulse"
              style={{ background: 'var(--jp-surface-secondary)' }} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="jp-card p-6 text-center" data-testid="requests-empty">
          <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
            Aucune demande en attente.
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {items.map((r) => (
            <li key={r.request_id} className="jp-card flex items-center gap-3 p-3"
              data-testid={`request-row-${r.request_id}`}>
              <Link to={`/users/${r.follower_id}`} className="w-11 h-11 rounded-full overflow-hidden flex-shrink-0 flex items-center justify-center"
                style={{ background: 'var(--jp-surface-secondary)' }}>
                {r.avatar ? <img src={r.avatar.startsWith('http') ? r.avatar : `${API}${r.avatar}`}
                  alt="" className="w-full h-full object-cover" /> : <UserIcon size={20} />}
              </Link>
              <div className="flex-1 min-w-0">
                <p className="font-['Outfit'] text-sm font-bold truncate"
                  style={{ color: 'var(--jp-text)' }}>
                  {`${r.first_name || ''} ${r.last_name || ''}`.trim() || r.username || 'Utilisateur'}
                </p>
                <p className="text-[11px] font-['Manrope'] truncate"
                  style={{ color: 'var(--jp-text-muted)' }}>
                  @{r.username} · {r.followers_count || 0} followers
                </p>
              </div>
              <div className="flex gap-1">
                <button onClick={() => resolveRequest(r.request_id, 'accept')}
                  disabled={busyId === r.request_id}
                  className="jp-btn jp-btn-primary text-xs px-3 py-1.5"
                  data-testid={`request-accept-${r.request_id}`}>
                  <Check size={13} weight="bold" /> Accepter
                </button>
                <button onClick={() => resolveRequest(r.request_id, 'decline')}
                  disabled={busyId === r.request_id}
                  className="jp-btn jp-btn-ghost text-xs px-3 py-1.5"
                  data-testid={`request-decline-${r.request_id}`}>
                  <XIcon size={13} weight="bold" />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
