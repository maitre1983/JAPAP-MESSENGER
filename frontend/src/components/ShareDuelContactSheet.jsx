/**
 * ShareDuelContactSheet — iter141sixx
 *
 * Bottom-sheet selector that lists the caller's most actionable JAPAP
 * contacts (`/api/social/recent-friends`) and lets them pick up to 5
 * friends to challenge in one tap. On confirm we open one
 * `wa.me/{phone}?text=...` tab per selected contact (each chat is a
 * separate WhatsApp thread, the recipient gets a dedicated invite),
 * plus a "📣 Mon Statut WhatsApp" shortcut that opens the standard
 * un-targeted share for broader reach.
 *
 * Props:
 *   open       — boolean, controls visibility
 *   onClose()  — close handler
 *   shareToken — duel share token, used to build the duel URL
 *   game       — 'quiz' | 'tap'
 *   score      — formatted score string ("3/5", "42 taps")
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { X, Check, Users, Phone, ShareNetwork } from '@phosphor-icons/react';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;
const MAX_PICK = 5;

export default function ShareDuelContactSheet({ open, onClose, shareToken, game, score }) {
  const [friends, setFriends] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(new Set());
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setSelected(new Set());
    axios.get(`${API}/api/social/recent-friends?limit=20&only_with_phone=true`,
              { withCredentials: true })
      .then(({ data }) => setFriends(data.items || []))
      .catch(() => setFriends([]))
      .finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  const url = `${window.location.origin}/duel/${shareToken}`;
  const buildMessage = (name) => {
    const greeting = name ? `Salut ${name} 👋, ` : '';
    const gameLabel = game === 'quiz' ? 'Quiz JAPAP' : 'Tap Challenge';
    return `${greeting}je t'ai défié sur JAPAP, peux-tu faire mieux que moi ? Mon score : ${score} sur ${gameLabel} 💪 Joue ici : ${url}`;
  };

  const toggle = (uid) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(uid)) next.delete(uid);
      else if (next.size < MAX_PICK) next.add(uid);
      else toast.info(`Maximum ${MAX_PICK} amis par envoi`);
      return next;
    });
  };

  const sendToSelection = async () => {
    if (selected.size === 0) {
      toast.info('Sélectionne au moins un ami à défier');
      return;
    }
    setSending(true);
    try {
      const picks = friends.filter(f => selected.has(f.user_id));
      // Open one WhatsApp tab per selected contact. Browsers will require
      // a user-gesture chain, so we kick the first synchronously and
      // queue the rest with tiny delays — works on iOS Safari + Chrome.
      picks.forEach((f, i) => {
        const phoneDigits = (f.phone_number || '').replace(/[^\d]/g, '');
        const msg = buildMessage(f.name?.split(' ')[0]);
        const wa = phoneDigits
          ? `https://wa.me/${phoneDigits}?text=${encodeURIComponent(msg)}`
          : `https://wa.me/?text=${encodeURIComponent(msg)}`;
        if (i === 0) window.open(wa, '_blank', 'noopener,noreferrer');
        else setTimeout(() => window.open(wa, '_blank', 'noopener,noreferrer'), 250 + i * 250);
      });
      toast.success(`Défi envoyé à ${picks.length} ami${picks.length > 1 ? 's' : ''} 🚀`);
      setTimeout(onClose, 600);
    } finally { setSending(false); }
  };

  const sendStatus = () => {
    // Generic share — perfect for posting to WhatsApp Status (no recipient).
    const wa = `https://wa.me/?text=${encodeURIComponent(buildMessage())}`;
    window.open(wa, '_blank', 'noopener,noreferrer');
  };

  return (
    <div className="fixed inset-0 z-[1000] flex items-end justify-center"
         data-testid="duel-contact-sheet"
         style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)' }}
         onClick={onClose}>
      <div className="w-full max-w-md rounded-t-3xl p-4 pb-6 max-h-[85vh] overflow-y-auto"
           onClick={e => e.stopPropagation()}
           style={{ background: 'linear-gradient(180deg, #1c0b8a 0%, #0F056B 100%)',
                    color: '#fff' }}>
        {/* Drag handle + header */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Users size={20} weight="duotone" color="#FFD700" />
            <h2 className="font-['Outfit'] text-lg font-extrabold">Défier mes amis</h2>
          </div>
          <button onClick={onClose} className="p-2 rounded-full"
                  data-testid="duel-contact-sheet-close"
                  style={{ background: 'rgba(255,255,255,0.10)' }}>
            <X size={16} weight="bold" />
          </button>
        </div>
        <p className="text-xs text-white/70 mb-4">
          Sélectionne jusqu'à <span className="font-bold text-[#FFD700]">{MAX_PICK} amis</span>.
          Un message WhatsApp pré-rempli s'ouvrira pour chacun.
        </p>

        {/* Friends list */}
        {loading && (
          <div className="text-center py-8 text-white/60 text-sm" data-testid="duel-contact-loading">
            Chargement de tes amis…
          </div>
        )}
        {!loading && friends.length === 0 && (
          <div className="rounded-2xl p-6 text-center text-white/70 text-sm mb-4"
               data-testid="duel-contact-empty"
               style={{ background: 'rgba(255,255,255,0.06)',
                        border: '1px dashed rgba(255,255,255,0.20)' }}>
            <Phone size={28} weight="duotone" color="#FFD700" className="mx-auto mb-2 opacity-70" />
            Tu n'as pas encore d'amis avec un numéro renseigné.<br />
            Partage le lien sur ton Statut WhatsApp ci-dessous 👇
          </div>
        )}

        {!loading && friends.length > 0 && (
          <div className="space-y-2 mb-4" data-testid="duel-contact-list">
            {friends.map(f => {
              const picked = selected.has(f.user_id);
              return (
                <button key={f.user_id}
                        type="button"
                        onClick={() => toggle(f.user_id)}
                        data-testid={`duel-contact-row-${f.user_id}`}
                        className="w-full flex items-center gap-3 p-3 rounded-xl text-left transition-all"
                        style={{
                          background: picked
                            ? 'linear-gradient(90deg, rgba(255,215,0,0.20), rgba(247,147,26,0.10))'
                            : 'rgba(255,255,255,0.06)',
                          border: picked
                            ? '1px solid rgba(255,215,0,0.55)'
                            : '1px solid rgba(255,255,255,0.10)',
                        }}>
                  <div className="w-10 h-10 rounded-full overflow-hidden flex items-center justify-center font-bold"
                       style={{ background: 'rgba(255,255,255,0.18)' }}>
                    {f.avatar
                      ? <img src={f.avatar} alt="" className="w-full h-full object-cover" />
                      : (f.name?.[0] || '?')}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm truncate">{f.name}</div>
                    <div className="text-[11px] text-white/60 truncate">
                      {f.phone_number}
                    </div>
                  </div>
                  <div className="w-6 h-6 rounded-full flex items-center justify-center"
                       style={{
                         background: picked ? '#FFD700' : 'rgba(255,255,255,0.10)',
                         border: picked ? 'none' : '1.5px solid rgba(255,255,255,0.30)',
                       }}>
                    {picked && <Check size={14} weight="bold" color="#0F056B" />}
                  </div>
                </button>
              );
            })}
          </div>
        )}

        {/* Footer actions (iter210 — safe-area stacked) */}
        <div
          className="space-y-2 sticky"
          style={{
            bottom: 0,
            paddingBottom: 'env(safe-area-inset-bottom, 0px)',
          }}
        >
          <button onClick={sendToSelection}
                  disabled={sending || selected.size === 0}
                  data-testid="duel-contact-send-btn"
                  className="w-full py-3.5 rounded-full font-bold text-sm disabled:opacity-50"
                  style={{ background: '#25D366', color: '#fff',
                           boxShadow: '0 10px 28px rgba(37,211,102,0.45)' }}>
            📱 Envoyer à {selected.size > 0 ? `${selected.size} ami${selected.size > 1 ? 's' : ''}` : 'mes amis'}
          </button>
          <button onClick={sendStatus}
                  data-testid="duel-contact-status-btn"
                  className="w-full py-3 rounded-full font-bold text-xs flex items-center justify-center gap-2"
                  style={{ background: 'rgba(255,255,255,0.10)',
                           border: '1px solid rgba(255,255,255,0.22)',
                           color: '#fff' }}>
            <ShareNetwork size={14} weight="bold" />
            Partager sur mon Statut WhatsApp
          </button>
        </div>
      </div>
    </div>
  );
}
