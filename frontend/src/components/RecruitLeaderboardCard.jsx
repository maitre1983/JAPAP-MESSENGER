/**
 * RecruitLeaderboardCard — iter141seven
 *
 * Lightweight read-only widget that lists the top recruiters on a
 * rolling window (default 7 days). Public endpoint, no auth required.
 *
 * Use it on /games landing or the home feed to give a competitive
 * pulse to the viral loop : "Look how many friends Alice has invited
 * this week — try to beat her."
 *
 * Props:
 *   compact : when true, shows only the top 5 without medals.
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { Trophy, Crown, Users } from '@phosphor-icons/react';
import { Link } from 'react-router-dom';

const API = process.env.REACT_APP_BACKEND_URL;

export default function RecruitLeaderboardCard({ compact = false }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    axios.get(`${API}/api/recruit/leaderboard`)
      .then(r => setData(r.data))
      .catch(() => setData({ items: [], settings: {} }));
  }, []);

  if (!data) {
    return (
      <div className="rounded-2xl p-4 text-center text-white/60 text-xs"
           style={{ background: 'rgba(255,255,255,0.05)' }}>
        Chargement…
      </div>
    );
  }

  const items = (data.items || []).slice(0, compact ? 5 : 10);
  const settings = data.settings || {};
  const reward = settings.recruit_per_friend_points;
  const buzz = settings.recruit_buzz_threshold;
  const badge = `${settings.recruit_buzz_badge_emoji || '👑'} ${settings.recruit_buzz_badge_label || 'Roi du Buzz'}`;

  return (
    <div className="rounded-2xl p-4"
         data-testid="recruit-leaderboard-card"
         style={{ background: 'linear-gradient(135deg, #FFD70022, #F7931A11)',
                  border: '1px solid rgba(255,215,0,0.30)' }}>
      <div className="flex items-center gap-2 mb-1">
        <Crown size={20} weight="fill" color="#FFD700" />
        <h3 className="font-['Outfit'] text-base font-extrabold text-white">
          Top Recruteurs JAPAP
        </h3>
        <span className="text-[10px] text-white/50 ml-auto">
          {data.period_days || 7}j
        </span>
      </div>
      <p className="text-[11px] text-white/70 mb-3">
        Invite des amis à jouer tes défis : <span className="font-bold text-[#FFD700]">+{reward} pts</span> par ami,
        bonus <span className="font-bold text-[#FFD700]">+{settings.recruit_buzz_bonus_points} pts</span> + badge {badge} à partir de <span className="font-bold">{buzz} amis</span>.
      </p>

      {items.length === 0 ? (
        <div className="rounded-xl p-4 text-center text-xs text-white/60"
             data-testid="recruit-lb-empty"
             style={{ background: 'rgba(255,255,255,0.05)' }}>
          <Users size={24} weight="duotone" color="#FFD700" className="mx-auto mb-2 opacity-70" />
          Sois le premier — partage ton défi pour grimper au classement.
        </div>
      ) : (
        <ol className="space-y-2" data-testid="recruit-lb-list">
          {items.map((it) => (
            <li key={it.user_id}
                data-testid={`recruit-lb-row-${it.rank}`}
                className="flex items-center gap-3 p-2 rounded-xl"
                style={{
                  background: it.rank === 1
                    ? 'linear-gradient(90deg, rgba(255,215,0,0.18), rgba(247,147,26,0.06))'
                    : 'rgba(255,255,255,0.05)',
                  border: `1px solid ${it.rank === 1 ? 'rgba(255,215,0,0.45)' : 'rgba(255,255,255,0.10)'}`,
                }}>
              <div className="w-7 text-center font-['Outfit'] text-base font-extrabold"
                   style={{ color: it.rank === 1 ? '#FFD700' : (it.rank === 2 ? '#cdcfd6' : it.rank === 3 ? '#cd7f32' : '#fff') }}>
                {it.rank === 1 ? '🥇' : it.rank === 2 ? '🥈' : it.rank === 3 ? '🥉' : `#${it.rank}`}
              </div>
              <div className="w-9 h-9 rounded-full overflow-hidden flex items-center justify-center font-bold text-xs"
                   style={{ background: 'rgba(255,255,255,0.18)' }}>
                {it.avatar
                  ? <img src={it.avatar} alt="" className="w-full h-full object-cover" />
                  : (it.name?.[0] || '?')}
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-bold text-sm text-white truncate">{it.name}</div>
                <div className="text-[10px] text-white/60">
                  {it.recruits} ami{it.recruits > 1 ? 's' : ''} · {it.points} pts
                </div>
              </div>
              <Trophy size={14} weight="fill" color="#FFD700" />
            </li>
          ))}
        </ol>
      )}

      <Link to="/duel/me/sent"
            data-testid="recruit-lb-cta"
            className="mt-3 block text-center py-2 rounded-full font-bold text-xs"
            style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
        Lancer mon défi viral →
      </Link>
    </div>
  );
}
