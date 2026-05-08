/**
 * EngagementPointsCard — compact bridge between Wallet and Games.
 *
 * Shows the user's current 30-day Starter Pro cycle : points, days played,
 * quiz accuracy, progress bar. Click → /games (where the actual earning
 * happens). Lightweight, relies on /api/wheel/status.
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import { Target, Trophy, ArrowRight } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

export default function EngagementPointsCard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [data, setData] = useState(null);

  useEffect(() => {
    axios.get(`${API}/api/wheel/status`, { withCredentials: true })
      .then(({ data }) => setData(data))
      .catch(() => {});
  }, []);

  if (!data || !data.cycle) return null;
  const { cycle } = data;
  const pts = cycle.points_cycle || 0;
  const days = cycle.days_played_count || 0;
  const pctPts = Math.min(100, (pts / 10000) * 100);
  const pctDays = Math.min(100, (days / 25) * 100);
  const combined = Math.min(pctPts, pctDays);
  const accuracy = data.progress?.quiz_accuracy_current != null
    ? Math.round(data.progress.quiz_accuracy_current * 100)
    : null;
  const answersTotal = data.progress?.quiz_answers_total || 0;

  return (
    <div className="jp-card-elevated p-5 mb-6 jp-animate-fadeIn cursor-pointer transition-transform active:scale-[0.99]"
         onClick={() => navigate('/games')}
         data-testid="wallet-engagement-card"
         style={{
           background: 'linear-gradient(135deg, rgba(15,5,107,0.05) 0%, rgba(139,92,246,0.08) 50%, rgba(255,215,0,0.08) 100%)',
           border: '1px solid rgba(139,92,246,0.2)',
         }}>
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center"
               style={{ background: 'linear-gradient(135deg, #FFD700, #F7931A)' }}>
            <Trophy size={20} weight="fill" color="#111" />
          </div>
          <div>
            <div className="font-['Outfit'] font-bold text-sm" style={{ color: 'var(--jp-text)' }}>
              Mes points d'engagement
            </div>
            <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
              Cycle Starter Pro · 30 jours
            </div>
          </div>
        </div>
        <ArrowRight size={18} weight="bold" style={{ color: 'var(--jp-text-muted)' }} />
      </div>

      <div className="grid grid-cols-3 gap-3 mb-3">
        <Stat label="Points" value={pts.toLocaleString('fr-FR')} goal="/ 10 000" color="#F59E0B" />
        <Stat label="Jours joués" value={days} goal="/ 25" color="#8B5CF6" />
        <Stat label="Précision Quiz"
              value={answersTotal === 0 ? '—' : `${accuracy}%`}
              goal={answersTotal < 50 ? `${answersTotal}/50 réponses` : '≥ 75 %'}
              color={accuracy !== null && accuracy >= 75 ? '#10B981' : '#E01C2E'} />
      </div>

      {/* Progress bar (min of the two hard conditions) */}
      <div className="w-full h-2 rounded-full overflow-hidden"
           style={{ background: 'var(--jp-surface-secondary)' }}>
        <div className="h-full transition-all duration-500"
             style={{
               width: `${combined}%`,
               background: combined >= 99
                 ? 'linear-gradient(90deg, #10B981, #FFD700)'
                 : 'linear-gradient(90deg, #FFD700, #F7931A)',
             }} />
      </div>
      <div className="flex items-center justify-between mt-2 text-[11px]"
           style={{ color: 'var(--jp-text-muted)' }}>
        <span>{t('engagement_points_card.progression_vers_le_pack_starter_pr')}</span>
        <span className="font-bold flex items-center gap-1">
          <Target size={12} weight="fill" style={{ color: '#FFD700' }} />
          {Math.round(combined)} %
        </span>
      </div>
    </div>
  );
}

function Stat({ label, value, goal, color }) {
  return (
    <div>
      <div className="font-['Outfit'] text-xl font-extrabold" style={{ color }}>
        {value}
      </div>
      <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
        {label} <span className="opacity-70">{goal}</span>
      </div>
    </div>
  );
}
