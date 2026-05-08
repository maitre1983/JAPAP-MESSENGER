/**
 * RecruiterPanel — Crowdfunding viral loop (P1, iter169)
 * ======================================================
 * Two stacked sections:
 *   1. "Ma Progression" — personal recruit count, current tier, next-tier
 *      progress bar, badges earned in the active cycle.
 *   2. "Top Recruteurs" — leaderboard (top-10 by recruits) with the
 *      viewer's own rank pinned at the bottom if outside top-10.
 *
 * CEO rules applied here (display-only, server enforces):
 *   • XP/badges only — NO cash mention.
 *   • Self-project recruit blocked → not shown.
 *
 * Drops in below MyDashboard on `/crowdfunding`.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { Trophy, Crown, Medal, Sparkle, Confetti, ShareNetwork } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

const TIER_STYLE = {
  bronze:   { label: 'Bronze',   bg: 'from-amber-700 to-amber-500',   ring: 'ring-amber-300',   icon: Medal },
  silver:   { label: 'Silver',   bg: 'from-slate-500 to-slate-300',   ring: 'ring-slate-200',   icon: Medal },
  gold:     { label: 'Gold',     bg: 'from-yellow-500 to-amber-300',  ring: 'ring-yellow-200',  icon: Trophy },
  platinum: { label: 'Platine',  bg: 'from-cyan-400 to-fuchsia-500',  ring: 'ring-cyan-200',    icon: Crown },
};

const fmt = (n) => new Intl.NumberFormat('fr-FR').format(Number(n || 0));

function TierChip({ tier, size = 'md' }) {
  if (!tier) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200" data-testid="cf-rec-tier-none">
        <Sparkle size={10} /> Aucun palier
      </span>
    );
  }
  const s = TIER_STYLE[tier.key] || TIER_STYLE.bronze;
  const Icon = s.icon;
  const px = size === 'sm' ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-xs';
  return (
    <span
      className={`inline-flex items-center gap-1 ${px} rounded-full font-semibold text-white bg-gradient-to-r ${s.bg} shadow ring-1 ${s.ring}`}
      data-testid={`cf-rec-tier-${tier.key}`}>
      <Icon size={size === 'sm' ? 10 : 14} weight="fill" /> {s.label}
    </span>
  );
}

export function RecruiterProgressCard({ progress, onShare }) {
  const { t } = useTranslation();
  if (!progress) return null;
  const next = progress.next_tier;
  const cur = progress.tier;
  const recruitsToCur = cur ? cur.threshold : 0;
  const span = next ? next.threshold - recruitsToCur : Math.max(1, progress.recruits_count);
  const within = Math.max(0, progress.recruits_count - recruitsToCur);
  const pct = next
    ? Math.min(100, Math.round((within / span) * 100))
    : 100;

  return (
    <div
      className="rounded-2xl bg-gradient-to-br from-indigo-700 via-fuchsia-700 to-rose-600 text-white p-5 shadow-xl"
      data-testid="cf-rec-progress-card">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className="w-9 h-9 rounded-full bg-white/15 flex items-center justify-center">
            <Confetti size={20} weight="fill" className="text-amber-300" />
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-widest text-white/70">Ma progression</div>
            <div className="font-bold text-base">Programme Recruteur</div>
          </div>
        </div>
        <TierChip tier={cur} />
      </div>

      <div className="flex items-baseline gap-2 mb-1 mt-2">
        <span className="text-3xl font-extrabold text-amber-300" data-testid="cf-rec-progress-count">
          {fmt(progress.recruits_count)}
        </span>
        <span className="text-xs text-white/80">recrue{progress.recruits_count > 1 ? 's' : ''} ce cycle</span>
      </div>

      {next ? (
        <>
          <div className="flex items-center justify-between text-[11px] text-white/70 mb-1">
            <span>{t('recruiter_panel.prochain_palier')}</span>
            <span className="font-semibold text-white/90">
              <TierChip tier={next} size="sm" /> · plus que {fmt(next.missing)}
            </span>
          </div>
          <div className="bg-white/10 rounded-full h-2 overflow-hidden">
            <div
              className="bg-gradient-to-r from-amber-300 to-rose-300 h-full transition-all duration-700"
              style={{ width: `${pct}%` }}
              data-testid="cf-rec-progress-bar" />
          </div>
        </>
      ) : (
        <div className="text-xs text-amber-200 font-semibold flex items-center gap-1 mt-1">
          <Crown size={14} weight="fill" /> Palier maximum atteint — bravo, légende !
        </div>
      )}

      {progress.badges?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5" data-testid="cf-rec-badges-list">
          {progress.badges.map((b) => (
            <TierChip key={b.tier} tier={{ key: b.tier }} size="sm" />
          ))}
        </div>
      )}

      <div className="mt-3 text-[11px] text-white/70 leading-relaxed">
        +XP & badges uniquement — pas de cash. Invite des amis qui votent pour
        un projet et grimpe le classement.
      </div>

      {onShare && (
        <button
          onClick={onShare}
          data-testid="cf-rec-share-btn"
          className="mt-3 inline-flex items-center gap-1 bg-white text-rose-700 font-bold text-xs px-3 py-1.5 rounded-full hover:bg-rose-50">
          <ShareNetwork size={14} weight="bold" /> Partager un projet
        </button>
      )}
    </div>
  );
}

export function RecruiterLeaderboard({ data, viewerId }) {
  if (!data || (data.items?.length || 0) === 0 && !data.me?.recruits_count) {
    return (
      <div className="rounded-2xl bg-white p-5 border border-slate-200" data-testid="cf-rec-leaderboard-empty">
        <div className="flex items-center gap-2 mb-1 text-slate-700">
          <Trophy size={18} weight="fill" className="text-amber-500" />
          <h3 className="font-bold text-base">Top recruteurs</h3>
        </div>
        <p className="text-xs text-slate-500">
          Personne n'a encore parrainé de votant. Sois le premier — partage un
          projet et fais voter tes amis !
        </p>
      </div>
    );
  }

  const top = data.items || [];
  const meInTop = top.some((r) => r.inviter_id === viewerId);

  return (
    <div className="rounded-2xl bg-white p-4 border border-slate-200 shadow-sm" data-testid="cf-rec-leaderboard">
      <div className="flex items-center gap-2 mb-3 px-1">
        <Trophy size={18} weight="fill" className="text-amber-500" />
        <h3 className="font-bold text-base">Top recruteurs</h3>
        <span className="ml-auto text-[10px] uppercase tracking-widest text-slate-400">
          Cycle en cours
        </span>
      </div>
      <ol className="space-y-1.5">
        {top.map((r) => (
          <li
            key={r.inviter_id}
            className={`flex items-center gap-2 rounded-xl px-2 py-1.5 ${r.inviter_id === viewerId ? 'bg-rose-50 ring-1 ring-rose-200' : 'hover:bg-slate-50'}`}
            data-testid={`cf-rec-row-${r.rank}`}>
            <span className={`w-6 h-6 rounded-full text-[11px] font-bold flex items-center justify-center ${r.rank === 1 ? 'bg-amber-400 text-amber-900' : r.rank === 2 ? 'bg-slate-300 text-slate-800' : r.rank === 3 ? 'bg-amber-700 text-white' : 'bg-slate-100 text-slate-600'}`}>
              {r.rank}
            </span>
            {r.avatar ? (
              <img src={r.avatar} alt="" loading="lazy" className="w-7 h-7 rounded-full object-cover bg-slate-200" />
            ) : (
              <div className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-400 to-rose-400 text-white text-[11px] font-bold flex items-center justify-center">
                {(r.first_name?.[0] || r.username?.[0] || '?').toUpperCase()}
              </div>
            )}
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-slate-800 truncate">
                {r.first_name || r.username || 'Inconnu'} {r.last_name || ''}
              </div>
              <div className="text-[10px] text-slate-500">@{r.username || r.inviter_id?.slice(0, 8)}</div>
            </div>
            <TierChip tier={r.tier} size="sm" />
            <span className="text-sm font-bold text-rose-700 tabular-nums w-8 text-right">
              {fmt(r.recruits_count)}
            </span>
          </li>
        ))}
      </ol>

      {!meInTop && data.me?.rank && (
        <div className="mt-3 pt-3 border-t border-slate-100">
          <div className="flex items-center gap-2 rounded-xl px-2 py-1.5 bg-rose-50 ring-1 ring-rose-200" data-testid="cf-rec-me-row">
            <span className="w-6 h-6 rounded-full text-[11px] font-bold flex items-center justify-center bg-rose-200 text-rose-800">
              {data.me.rank}
            </span>
            <div className="flex-1 text-sm font-semibold text-slate-800">Toi</div>
            <TierChip tier={data.me.tier} size="sm" />
            <span className="text-sm font-bold text-rose-700 tabular-nums w-8 text-right">
              {fmt(data.me.recruits_count)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

/** Wrapper that fetches both endpoints — drop into pages. */
export default function RecruiterPanel({ user, onShare }) {
  const { t } = useTranslation();
  const [progress, setProgress] = useState(null);
  const [leaderboard, setLeaderboard] = useState(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const reqs = [
        axios.get(`${API}/api/crowdfunding/recruiter/leaderboard?limit=10`,
                  { withCredentials: true }),
      ];
      if (user) {
        reqs.push(axios.get(`${API}/api/crowdfunding/recruiter/me`,
                            { withCredentials: true }));
      }
      const [lb, me] = await Promise.all(reqs);
      setLeaderboard(lb.data);
      setProgress(me?.data || null);
    } catch (e) {
      // soft-fail — feature is optional UX
      // eslint-disable-next-line no-console
      console.warn('[recruiter-panel] load failed', e?.response?.status);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => { reload(); }, [reload]);

  if (loading && !leaderboard && !progress) {
    return <div className="text-center py-4 text-slate-400 text-sm" data-testid="cf-rec-loading">Chargement du classement…</div>;
  }

  return (
    <div className="space-y-3" data-testid="cf-rec-panel">
      {user && progress && (
        <RecruiterProgressCard progress={progress} onShare={onShare} />
      )}
      <RecruiterLeaderboard data={leaderboard} viewerId={user?.user_id} />
    </div>
  );
}
