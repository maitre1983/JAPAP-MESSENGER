/**
 * iter141 — Initiator dashboard "Mes défis envoyés".
 *
 * Lists every duel/challenge the connected user has launched, with quick
 * aggregates: nb participants, best challenger score, status. Tapping a
 * row opens the public leaderboard at /duel/:token.
 */
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import axios from 'axios';
import { ArrowLeft, Trophy, Users, Clock } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

export default function MyDuelsSentPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [items, setItems] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    axios.get(`${API}/api/duel/me/sent`, { withCredentials: true })
      .then(({ data }) => setItems(data.items || []))
      .catch((e) => setErr(e.response?.data?.detail || 'Chargement impossible'));
  }, []);

  const statusBadge = (s) => {
    if (s === 'expired')   return { label: t('my_duels_sent.expire'),   color: '#9ca3af' };
    if (s === 'completed') return { label: t('my_duels_sent.termine'),  color: '#22c55e' };
    return { label: 'Actif', color: '#FFD700' };
  };

  const copyLink = async (token) => {
    const url = `${window.location.origin}/duel/${token}`;
    try { await navigator.clipboard.writeText(url); toast.success('Lien copié'); }
    catch { /* ignore */ }
  };
  const shareWA = (token, score) => {
    const url = `${window.location.origin}/duel/${token}`;
    const txt = `🔥 Mon défi quotidien JAPAP : ${score}/5. Sauras-tu faire mieux ? 👉 ${url}`;
    window.open(`https://wa.me/?text=${encodeURIComponent(txt)}`, '_blank', 'noopener,noreferrer');
  };

  return (
    <div className="min-h-screen text-white p-4 pb-24"
         style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="my-duels-sent-page">
      <div className="flex items-center mb-4">
        <button onClick={() => navigate(-1)} className="p-2 rounded-full"
                style={{ background: 'rgba(255,255,255,0.1)' }}
                data-testid="my-duels-back">
          <ArrowLeft size={18} weight="bold" />
        </button>
        <div className="ml-3">
          <div className="text-[10px] uppercase tracking-widest text-white/60">JAPAP</div>
          <h1 className="font-['Outfit'] text-xl font-extrabold">Mes défis envoyés</h1>
        </div>
      </div>

      {!items && !err && (
        <div className="text-center py-10 text-white/60 text-sm" data-testid="my-duels-loading">
          Chargement…
        </div>
      )}
      {err && (
        <div className="text-center py-10 text-red-300 text-sm" data-testid="my-duels-err">
          {err}
        </div>
      )}
      {items && items.length === 0 && (
        <div className="rounded-2xl p-8 text-center text-white/60 text-sm"
             data-testid="my-duels-empty"
             style={{ background: 'rgba(255,255,255,0.05)',
                      border: '1px dashed rgba(255,255,255,0.18)' }}>
          <Trophy size={36} weight="fill" color="#FFD700" className="mx-auto mb-2 opacity-70" />
          Aucun défi envoyé pour le moment.<br />
          Joue le défi quotidien et partage-le pour défier tes amis !
          <Link to="/games/quiz" className="block mt-4 text-[#FFD700] underline">
            Jouer maintenant
          </Link>
        </div>
      )}

      <div className="space-y-3">
        {(items || []).map((d) => {
          const s = statusBadge(d.status);
          const totalChallengers = d.participants;
          const bestStr = d.best_challenger_score == null
            ? '—'
            : `${d.best_challenger_score}/5`;
          const isMulti = d.duel_kind === 'multi_attempts';
          return (
            <div key={d.id} className="rounded-2xl p-4"
                 data-testid={`my-duels-row-${d.id}`}
                 style={{ background: 'rgba(255,255,255,0.06)',
                          border: '1px solid rgba(255,255,255,0.10)' }}>
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs uppercase tracking-widest text-white/60">
                  {isMulti ? t('my_duels_sent.defi_quotidien') : 'Duel 1v1'} · {d.game}
                </div>
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                      style={{ background: `${s.color}25`, color: s.color,
                               border: `1px solid ${s.color}55` }}>
                  {s.label}
                </span>
              </div>
              <div className="flex items-center gap-4 mb-3">
                <div className="text-center">
                  <div className="text-[9px] uppercase tracking-widest text-white/50">Mon score</div>
                  <div className="font-['Outfit'] text-2xl font-extrabold text-[#FFD700]">
                    {d.initiator_score}<span className="text-sm text-white/40">/5</span>
                  </div>
                </div>
                <div className="flex-1 grid grid-cols-3 gap-2">
                  <div className="rounded-lg p-2 text-center"
                       style={{ background: 'rgba(255,255,255,0.05)' }}>
                    <Users size={14} weight="fill" className="mx-auto mb-0.5 text-white/70" />
                    <div className="text-[9px] uppercase tracking-widest text-white/50">Joueurs</div>
                    <div className="font-bold text-sm">{totalChallengers}</div>
                  </div>
                  <div className="rounded-lg p-2 text-center"
                       style={{ background: 'rgba(255,255,255,0.05)' }}>
                    <Trophy size={14} weight="fill" className="mx-auto mb-0.5 text-white/70" />
                    <div className="text-[9px] uppercase tracking-widest text-white/50">Meilleur</div>
                    <div className="font-bold text-sm">{bestStr}</div>
                  </div>
                  <div className="rounded-lg p-2 text-center"
                       style={{ background: 'rgba(255,255,255,0.05)' }}>
                    <Clock size={14} weight="fill" className="mx-auto mb-0.5 text-white/70" />
                    <div className="text-[9px] uppercase tracking-widest text-white/50">Bilan</div>
                    <div className="font-bold text-sm" data-testid={`my-duels-record-${d.id}`}>
                      {d.wins_for_initiator}V · {d.losses_for_initiator}D · {d.ties}E
                    </div>
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Link to={`/duel/${d.share_token}`}
                      data-testid={`my-duels-open-${d.id}`}
                      className="flex-1 py-2 rounded-full font-bold text-xs text-center"
                      style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
                  Voir le classement
                </Link>
                <button onClick={() => shareWA(d.share_token, d.initiator_score)}
                        data-testid={`my-duels-share-${d.id}`}
                        className="px-3 py-2 rounded-full font-bold text-xs"
                        style={{ background: '#25D366', color: 'white' }}>
                  📱
                </button>
                <button onClick={() => copyLink(d.share_token)}
                        data-testid={`my-duels-copy-${d.id}`}
                        className="px-3 py-2 rounded-full font-bold text-xs"
                        style={{ background: 'rgba(255,255,255,0.10)', color: '#fff',
                                 border: '1px solid rgba(255,255,255,0.20)' }}>
                  🔗
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
