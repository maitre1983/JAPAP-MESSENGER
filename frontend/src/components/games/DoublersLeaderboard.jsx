/**
 * DoublersLeaderboard — iter233, Mission 2.
 *
 * Surfaces the top players who have won ×4 pots via the "Doubler la mise"
 * mechanic. Aspirational social proof on /games/quiz home: a small badge
 * row + medal podium that pushes solvable users to enable allow_double
 * to break into the top 10.
 *
 * Hidden when the leaderboard is empty (early days post-launch).
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { Sparkle } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;
const MEDALS = ['🥇', '🥈', '🥉'];

export default function DoublersLeaderboard({ limit = 10 }) {
  const [leaders, setLeaders] = useState([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/quiz/champion/leaderboard/doublers?limit=${limit}`,
          { withCredentials: true },
        );
        if (!cancelled) setLeaders(Array.isArray(data?.leaders) ? data.leaders : []);
      } catch {
        // silent — leaderboard is optional, the page works without it
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => { cancelled = true; };
  }, [limit]);

  // Hide entirely if empty (no winners yet) — avoids visual noise pre-launch.
  if (loaded && leaders.length === 0) return null;
  if (!loaded) return null;

  return (
    <div
      data-testid="doublers-leaderboard"
      className="rounded-2xl p-4 mb-6"
      style={{
        background: 'linear-gradient(135deg, rgba(255,215,0,0.10) 0%, rgba(247,147,26,0.08) 100%)',
        border: '1px solid rgba(255,215,0,0.28)',
      }}
    >
      <div className="flex items-center gap-2 mb-3">
        <Sparkle size={18} weight="fill" style={{ color: '#FFD700' }} />
        <h3 className="font-['Outfit'] font-extrabold text-base text-white">
          Doubleurs Légendaires
        </h3>
        <span className="text-[10px] font-bold uppercase tracking-wider opacity-60 text-white">
          · 30 jours
        </span>
      </div>

      <ul className="space-y-1.5">
        {leaders.slice(0, limit).map((player, idx) => (
          <li
            key={player.user_id}
            data-testid={`doubler-row-${idx}`}
            className="flex items-center gap-3 p-2 rounded-xl"
            style={{ background: 'rgba(255,255,255,0.05)' }}
          >
            <span className="text-base w-6 text-center font-bold text-white">
              {MEDALS[idx] || `${idx + 1}`}
            </span>
            <div
              className="w-8 h-8 rounded-full overflow-hidden bg-white/10 flex items-center justify-center text-xs font-bold text-white"
              data-testid={`doubler-avatar-${idx}`}
            >
              {player.avatar_url ? (
                <img src={player.avatar_url} alt={player.name}
                     className="w-full h-full object-cover"
                     onError={(e) => { e.currentTarget.style.display = 'none'; }} />
              ) : (
                (player.name || '?').slice(0, 1).toUpperCase()
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1">
                <span className="font-bold text-sm text-white truncate"
                      data-testid={`doubler-name-${idx}`}>
                  {player.name}
                </span>
                <span className="text-xs" aria-label="diamond">💎</span>
              </div>
              <div className="text-[10px] opacity-60 text-white">
                {player.wins_doubled} victoire{player.wins_doubled > 1 ? 's' : ''} doublée{player.wins_doubled > 1 ? 's' : ''}
              </div>
            </div>
            <div className="text-right">
              <div className="font-bold text-sm" style={{ color: '#FFD700' }}
                   data-testid={`doubler-total-${idx}`}>
                +{player.total_won_usd.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} USD
              </div>
              <div className="text-[10px] opacity-60 text-white">×4</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
