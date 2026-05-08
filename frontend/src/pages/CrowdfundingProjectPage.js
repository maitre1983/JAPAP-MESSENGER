import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link, useSearchParams } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Heart, ArrowRight, ShareNetwork, WhatsappLogo, Trophy, Lightning,
  Users, Crown, Lock,
} from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import { extractErrorMessage } from '@/utils/errorMessage';
import VoteCelebration from '@/components/crowdfunding/VoteCelebration';
import EngagementBanner from '@/components/crowdfunding/EngagementBanner';
import {
  useEngagementState, trackEngagementEvent,
} from '@/hooks/useEngagementState';

const API = process.env.REACT_APP_BACKEND_URL;
const fmt = (n) => new Intl.NumberFormat('fr-FR').format(Number(n || 0));

/**
 * iter142B — Public landing page for a Crowdfunding project share.
 *
 * Visitor journey:
 *   1. Anonymous → preview + CTA "Se connecter pour voter / Créer un compte"
 *      (with `?redirect=` so login redirects back here).
 *   2. Authenticated owner → "Tu ne peux pas voter pour ton propre projet."
 *   3. Authenticated viewer → 1-tap "Voter ❤️" with confetti animation +
 *      post-vote sheet "Tu viens de soutenir — invite tes amis".
 *   4. Cycle locked (votes_open=false) → friendly counter "Encore N projets
 *      pour déclencher les votes".
 */
export default function CrowdfundingProjectPage() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const refInviter = (searchParams.get('ref') || '').slice(0, 64);
  const utmSrc = (searchParams.get('src') || searchParams.get('utm_source') || '').slice(0, 40);
  const { user, loading: authLoading } = useAuth();
  const [project, setProject] = useState(null);
  const [state, setState] = useState(null);
  const [share, setShare] = useState(null);
  const [loading, setLoading] = useState(true);
  const [voting, setVoting] = useState(false);
  const [voted, setVoted] = useState(false);
  const [showCelebration, setShowCelebration] = useState(false);

  const { data: engagement, refresh: refreshEngagement } = useEngagementState({
    enabled: !!user,
  });

  // Track view event once at mount + when project loads.
  useEffect(() => {
    if (project?.project_id && user) {
      trackEngagementEvent('view', { project_id: project.project_id });
    }
  }, [project?.project_id, user]);

  // iter169 — Crowdfunding viral loop (P1)
  // When the URL carries `?ref={inviter_id}`, ping the visit-tracking
  // endpoint so attribution is recorded even when the visitor lands on
  // the SPA route directly (without going through the 303 server redirect
  // — useful for native share intents that resolve URLs in-app).
  // Fire once per (slug, ref). Stored in sessionStorage to avoid double-
  // hits on React StrictMode / hot reload.
  useEffect(() => {
    if (!slug || !refInviter) return;
    if (refInviter === user?.user_id) return; // self-referral — silent skip
    const key = `cf:visit:${slug}:${refInviter}`;
    if (sessionStorage.getItem(key)) return;
    sessionStorage.setItem(key, '1');
    fetch(
      `${API}/api/crowdfunding/projects/${encodeURIComponent(slug)}/visit?ref=${encodeURIComponent(refInviter)}${utmSrc ? `&src=${encodeURIComponent(utmSrc)}` : ''}`,
      { method: 'GET', credentials: 'include', redirect: 'manual' },
    ).catch(() => { /* network blip — silent */ });
  }, [slug, refInviter, utmSrc, user?.user_id]);

  // iter142D — On the project owner's behalf, track external visits so
  // his momentum_score reflects how many people clicked through his shares.
  useEffect(() => {
    if (!project?.project_id) return;
    const ref = document.referrer || '';
    const fromExternal = /whatsapp|t\.me|telegram|twitter|facebook|instagram/i.test(ref)
      || (!!ref && !ref.includes(window.location.host));
    if (fromExternal) {
      // Only the project owner gets the visit_generated counter, but the
      // event is recorded under the visitor's user_id (or anon) so we can
      // also build inviter→invitee graphs later.
      if (user) {
        trackEngagementEvent('visit_generated', {
          project_id: project.project_id,
          metadata: { ref_host: (ref || '').split('/')[2] || 'unknown' },
        });
      }
    }
  }, [project?.project_id, user]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [p, s, sh] = await Promise.all([
          axios.get(`${API}/api/crowdfunding/projects/${slug}`, { withCredentials: true }),
          axios.get(`${API}/api/crowdfunding/state`, { withCredentials: true }),
          axios.get(`${API}/api/crowdfunding/projects/${slug}/share`, { withCredentials: true }),
        ]);
        if (cancelled) return;
        setProject(p.data);
        setState(s.data);
        setShare(sh.data);
        setVoted(!!p.data.voted_by_me);
      } catch (e) {
        toast.error(extractErrorMessage(e, 'Projet introuvable.'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [slug]);

  const isOwner = user && project && user.user_id === project.user_id;
  const isWinner = project?.status === 'winner';
  const votesOpen = state?.cycle?.votes_open;
  const votesToWin = state?.cycle?.votes_to_win || 100;

  const onVote = async () => {
    if (voting || voted) return;
    setVoting(true);
    try {
      const { data } = await axios.post(
        `${API}/api/crowdfunding/projects/${slug}/vote`,
        {}, { withCredentials: true },
      );
      trackEngagementEvent('vote', { project_id: project?.project_id });
      setProject((p) => ({ ...p, votes_count: data.votes_count, voted_by_me: true,
                          status: data.won ? 'winner' : p.status }));
      setVoted(true);
      setShowCelebration(true);
      refreshEngagement();
      if (data.won) {
        toast.success('🏆 Projet gagnant ! Récompense créditée à l\'auteur.', { duration: 6000 });
      }
    } catch (e) {
      const detail = e?.response?.data?.detail;
      if (detail?.code === 'VOTES_NOT_OPEN') toast.error(detail.message);
      else toast.error(extractErrorMessage(e, 'Vote impossible.'));
    } finally {
      setVoting(false);
    }
  };

  const onShare = async () => {
    if (!share) return;
    trackEngagementEvent('share', { project_id: project?.project_id });
    if (navigator.share) {
      try {
        await navigator.share({
          title: project?.title || 'Projet JAPAP',
          text: share.share_text,
          url: share.share_url,
        });
        refreshEngagement();
        return;
      } catch {}
    }
    window.open(share.whatsapp_url, '_blank');
    refreshEngagement();
  };

  const onCopyLink = async () => {
    if (!share?.share_url) return;
    try {
      await navigator.clipboard.writeText(share.share_url);
      toast.success('Lien copié — partage-le sur WhatsApp.');
    } catch {
      toast.error('Copie impossible.');
    }
  };

  if (loading || authLoading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-indigo-900 via-fuchsia-900 to-rose-700 flex items-center justify-center text-white" data-testid="cf-pp-loading">
        <div className="animate-pulse">Chargement…</div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-indigo-900 to-rose-800 flex items-center justify-center p-6 text-center text-white" data-testid="cf-pp-notfound">
        <div>
          <h1 className="text-2xl font-bold mb-2">Projet introuvable</h1>
          <Link to="/" className="text-amber-300 underline">Retour à l'accueil</Link>
        </div>
      </div>
    );
  }

  const pct = Math.min(100, Math.round((project.votes_count / Math.max(1, votesToWin)) * 100));
  const voteUrl = encodeURIComponent(`/crowdfunding/p/${slug}`);

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-900 via-fuchsia-900 to-rose-700 text-white pb-20" data-testid="cf-pp-root">
      {/* Hero header */}
      <div className="relative">
        {project.image_url ? (
          <img src={project.image_url} alt=""
               loading="eager" decoding="async" fetchPriority="high"
               className="w-full h-72 object-cover opacity-70"
               onError={(e) => { e.target.style.display = 'none'; }} />
        ) : (
          <div className="w-full h-40 bg-gradient-to-br from-indigo-800 to-rose-800" />
        )}
        <div className="absolute inset-0 bg-gradient-to-b from-black/30 to-transparent" />
        <div className="absolute top-4 left-4 right-4 flex items-center justify-between">
          <Link to="/" className="bg-white/10 backdrop-blur rounded-full px-3 py-1 text-xs font-bold flex items-center gap-1" data-testid="cf-pp-home">
            <Trophy size={14} weight="fill" /> JAPAP Crowdfunding
          </Link>
          {state?.cycle?.cycle_number && (
            <div className="bg-white/10 backdrop-blur rounded-full px-3 py-1 text-xs font-semibold">
              Cycle #{state.cycle.cycle_number}
            </div>
          )}
        </div>
      </div>

      <main className="max-w-lg mx-auto px-5 -mt-8 relative">
        <div className="bg-white/10 backdrop-blur-md rounded-2xl p-5 shadow-xl border border-white/20" data-testid="cf-pp-card">
          {isWinner && (
            <div className="bg-amber-400 text-amber-900 px-3 py-1 rounded-full text-xs font-bold inline-flex items-center gap-1 mb-3">
              <Crown weight="fill" /> Gagnant — récompense créditée
            </div>
          )}

          <h1 className="text-2xl font-extrabold leading-tight mb-1" data-testid="cf-pp-title">{project.title}</h1>
          <div className="text-sm text-white/80 mb-4 flex items-center gap-2">
            <span>par <b>{project.owner_name}</b></span>
            {project.country_code && <span>· {project.country_code}</span>}
            <span>· {project.category}</span>
          </div>

          <p className="text-sm text-white/90 mb-5 leading-relaxed" data-testid="cf-pp-desc">
            {project.description}
          </p>

          {/* Progress */}
          <div className="mb-5">
            <div className="flex items-baseline justify-between mb-1">
              <span className="text-3xl font-extrabold text-amber-300" data-testid="cf-pp-votes">
                {fmt(project.votes_count)}
              </span>
              <span className="text-xs text-white/70">
                / {fmt(votesToWin)} pour gagner ({pct}%)
              </span>
            </div>
            <div className="bg-white/15 rounded-full h-3 overflow-hidden">
              <div
                className="bg-gradient-to-r from-amber-400 to-rose-400 h-full transition-all duration-700"
                style={{ width: `${pct}%` }} />
            </div>
          </div>

          {/* CTA */}
          {isWinner ? (
            <div className="bg-amber-400/20 border border-amber-300 rounded-xl p-3 text-sm text-amber-100" data-testid="cf-pp-cta-winner">
              Ce projet a remporté son cycle. Reste à l'affût du prochain cycle !
            </div>
          ) : !votesOpen ? (
            <div className="bg-white/10 border border-white/20 rounded-xl p-3 text-sm" data-testid="cf-pp-cta-locked">
              <Lock size={16} weight="fill" className="inline -mt-1 mr-1 text-amber-300" />
              Les votes ouvriront à <b>{state?.cycle?.threshold_projects}</b> projets.
              {state?.projects_remaining_to_open > 0 && (
                <span> Encore {state.projects_remaining_to_open} pour déclencher.</span>
              )}
            </div>
          ) : !user ? (
            <div className="space-y-2" data-testid="cf-pp-cta-anon">
              <p className="text-xs text-white/80 mb-2">
                Connecte-toi en 1 sec pour voter ❤️
              </p>
              <Link
                to={`/login?redirect=${voteUrl}`}
                className="block w-full bg-amber-400 text-indigo-900 text-center font-bold py-3 rounded-full hover:bg-amber-300 transition active:scale-95"
                data-testid="cf-pp-login-btn">
                Se connecter pour voter
              </Link>
              <Link
                to={`/register?redirect=${voteUrl}`}
                className="block w-full bg-white/10 border border-white/30 text-white text-center font-bold py-3 rounded-full hover:bg-white/20 transition"
                data-testid="cf-pp-register-btn">
                Créer un compte gratuit
              </Link>
            </div>
          ) : isOwner ? (
            <div className="bg-white/10 border border-white/20 rounded-xl p-3 text-sm text-center" data-testid="cf-pp-cta-owner">
              C'est ton projet ! Partage-le pour récolter des votes.
            </div>
          ) : voted ? (
            <div className="bg-emerald-400/20 border border-emerald-300 rounded-xl p-4 text-center" data-testid="cf-pp-cta-voted">
              <Heart weight="fill" className="text-rose-300 inline mb-1" size={28} />
              <div className="font-bold text-emerald-100">Tu viens de soutenir ce projet ❤️</div>
              <div className="text-xs text-white/80 mt-1">Invite tes amis à faire pareil.</div>
            </div>
          ) : (
            <button
              onClick={onVote}
              disabled={voting}
              data-testid="cf-pp-vote-btn"
              className="w-full bg-amber-400 text-indigo-900 font-extrabold py-4 rounded-full hover:bg-amber-300 transition active:scale-95 disabled:opacity-60 disabled:cursor-wait flex items-center justify-center gap-2 shadow-lg">
              {voting ? (
                <>
                  <span className="inline-block w-4 h-4 border-2 border-indigo-900 border-t-transparent rounded-full animate-spin" />
                  Vote en cours…
                </>
              ) : (
                <>
                  <Heart weight="fill" size={22} className="text-rose-600" />
                  Voter pour ce projet
                </>
              )}
            </button>
          )}

          {/* iter142D — Adaptive engagement banner for the project owner */}
          {user && engagement?.message && isOwner && (
            <div className="mt-3" data-testid="cf-pp-engage-wrapper">
              <EngagementBanner
                payload={engagement}
                onPrimaryAction={(action) => {
                  if (action === 'share' || action === 'invite') onShare();
                }}
                onDismiss={refreshEngagement}
              />
            </div>
          )}

          {/* Share row — iter142E v2: horizontal labels for narrow screens */}
          <div className="mt-4 flex flex-col gap-2" data-testid="cf-pp-share-row">
            <a
              href={share?.whatsapp_url}
              target="_blank" rel="noreferrer"
              data-testid="cf-pp-share-whatsapp"
              className="bg-emerald-500 hover:bg-emerald-600 rounded-lg py-2.5 px-3 text-sm font-bold flex items-center justify-center gap-2 active:scale-95 transition">
              <WhatsappLogo size={18} weight="fill" />
              Partager sur WhatsApp
            </a>
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={onShare}
                data-testid="cf-pp-share-native"
                className="bg-white/15 border border-white/25 rounded-lg py-2 text-xs font-bold flex items-center justify-center gap-2 hover:bg-white/25">
                <ShareNetwork size={16} />
                Plus d'options
              </button>
              <button
                onClick={onCopyLink}
                data-testid="cf-pp-share-copy"
                className="bg-white/15 border border-white/25 rounded-lg py-2 text-xs font-bold flex items-center justify-center gap-2 hover:bg-white/25">
                <Lightning size={16} weight="fill" />
                Copier le lien
              </button>
            </div>
          </div>
        </div>

        {/* Auxiliary: see other projects */}
        <div className="text-center mt-6">
          <Link to="/services" className="text-xs text-white/70 underline" data-testid="cf-pp-all-projects">
            Voir tous les projets en lice <ArrowRight size={12} className="inline" />
          </Link>
        </div>
      </main>

      {showCelebration && (
        <VoteCelebration
          ownerName={project.owner_name}
          onClose={() => setShowCelebration(false)}
          onShareWhatsApp={() => {
            window.open(share?.whatsapp_url, '_blank');
            setShowCelebration(false);
          }}
          onShareNative={async () => {
            await onShare();
            setShowCelebration(false);
          }}
        />
      )}
    </div>
  );
}
