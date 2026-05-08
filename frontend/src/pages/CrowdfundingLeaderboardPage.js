/**
 * CrowdfundingLeaderboardPage — Public read-only viral landing
 * (iter170, P1 acquisition)
 * =============================================================
 * Accessible without login (no ProtectedRoute wrapper).
 * Goal: maximise external sharing & SEO surface area for the
 * recruiter leaderboard. CTA pushes anonymous viewers towards
 * /register (with `?next=/crowdfunding/leaderboard` so they
 * return after auth).
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { Trophy, ArrowRight, Sparkle, ShareNetwork } from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import { RecruiterLeaderboard, RecruiterProgressCard } from '@/components/crowdfunding/RecruiterPanel';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

export default function CrowdfundingLeaderboardPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [leaderboard, setLeaderboard] = useState(null);
  const [progress, setProgress] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const reqs = [axios.get(
          `${API}/api/crowdfunding/recruiter/leaderboard?limit=20`,
          { withCredentials: true },
        )];
        if (user) {
          reqs.push(axios.get(
            `${API}/api/crowdfunding/recruiter/me`,
            { withCredentials: true },
          ));
        }
        const [lb, me] = await Promise.all(reqs);
        if (cancelled) return;
        setLeaderboard(lb.data);
        setProgress(me?.data || null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [user]);

  const onShareLeaderboard = async () => {
    const url = window.location.href;
    if (navigator.share) {
      try {
        await navigator.share({
          title: 'Top recruteurs JAPAP Crowdfunding',
          text: 'Découvre les meilleurs recruteurs du cycle en cours sur JAPAP 🏆',
          url,
        });
        return;
      } catch { /* user cancelled */ }
    }
    try {
      await navigator.clipboard.writeText(url);
    } catch { /* noop */ }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-rose-50" data-testid="cf-leaderboard-page">
      {/* Hero */}
      <header className="bg-gradient-to-br from-indigo-700 via-fuchsia-700 to-rose-600 text-white">
        <div className="max-w-2xl mx-auto px-5 pt-10 pb-8">
          <Link to="/" className="inline-flex items-center gap-1.5 text-xs uppercase tracking-widest text-white/70 hover:text-white" data-testid="cf-lb-home-link">
            <ArrowRight size={12} className="rotate-180" /> Retour à JAPAP
          </Link>
          <div className="mt-4 flex items-center gap-3">
            <div className="w-12 h-12 rounded-2xl bg-white/15 flex items-center justify-center">
              <Trophy size={28} weight="fill" className="text-amber-300" />
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-widest text-white/70">Cycle en cours</div>
              <h1 className="text-2xl sm:text-3xl font-extrabold leading-tight">
                Top recruteurs Crowdfunding
              </h1>
            </div>
          </div>
          <p className="mt-3 text-sm text-white/85 max-w-md">
            Les utilisateurs qui ramènent le plus de votants sur JAPAP gagnent
            des badges et de l'XP. <b>{t('crowdfunding_leaderboard.pas_de_cash_juste_de_la_gloire')}</b>
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              onClick={onShareLeaderboard}
              data-testid="cf-lb-share-btn"
              className="inline-flex items-center gap-1.5 bg-white text-rose-700 font-bold text-xs px-3 py-1.5 rounded-full hover:bg-rose-50">
              <ShareNetwork size={14} weight="bold" /> Partager le classement
            </button>
            {!user && (
              <Link
                to="/register?next=/crowdfunding/leaderboard"
                data-testid="cf-lb-register-cta"
                className="inline-flex items-center gap-1.5 bg-amber-300 text-amber-900 font-bold text-xs px-3 py-1.5 rounded-full hover:bg-amber-200">
                <Sparkle size={14} weight="fill" /> Crée ton compte pour participer
              </Link>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-4 py-6 space-y-4 pb-20">
        {loading ? (
          <div className="text-center py-10 text-slate-400 text-sm" data-testid="cf-lb-loading">
            Chargement du classement…
          </div>
        ) : (
          <>
            {user && progress && (
              <RecruiterProgressCard progress={progress} />
            )}
            <RecruiterLeaderboard data={leaderboard} viewerId={user?.user_id} />

            {/* Pedagogy block (anon-friendly, SEO-rich) */}
            <section className="rounded-2xl bg-white p-5 border border-slate-200" data-testid="cf-lb-rules">
              <h2 className="font-bold text-base mb-2">Comment ça marche ?</h2>
              <ul className="text-sm text-slate-600 space-y-1.5 leading-relaxed">
                <li>📤 Tu partages le lien d'un projet sur WhatsApp / Telegram / X.</li>
                <li>👥 Quelqu'un clique → vote → tu gagnes 1 recrue dans le cycle.</li>
                <li>🏆 À 3, 10, 25 et 50 recrues tu débloques Bronze, Silver, Gold puis Platine.</li>
                <li>⛔ Anti-triche : impossible de recruter pour ton propre projet, max 3 visites par IP, une recrue compte une seule fois par recruteur.</li>
                <li>🚫 Aucune récompense en cash — uniquement badges &amp; XP.</li>
              </ul>
            </section>

            {!user && (
              <section className="rounded-2xl bg-gradient-to-br from-amber-100 to-rose-100 border border-amber-200 p-5 text-center" data-testid="cf-lb-cta-block">
                <h3 className="font-extrabold text-lg text-rose-800 mb-1">
                  Tu veux apparaître dans ce top ?
                </h3>
                <p className="text-sm text-rose-900/80 mb-3">
                  Crée ton compte gratuit, partage un projet, fais voter tes
                  amis. C'est tout.
                </p>
                <Link
                  to="/register?next=/crowdfunding/leaderboard"
                  data-testid="cf-lb-cta-register-btn"
                  className="inline-flex items-center gap-1.5 bg-rose-600 text-white font-bold text-sm px-5 py-2.5 rounded-full hover:bg-rose-700">
                  Commencer maintenant <ArrowRight size={14} weight="bold" />
                </Link>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
