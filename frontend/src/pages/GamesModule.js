import { useEffect, useState } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { CaretLeft, Sparkle, Question, HandTap, Trophy, Coin, Target, Crown, Lightning } from '@phosphor-icons/react';
import EngagementLeaderboard from '@/components/games/EngagementLeaderboard';
import DailyChallengeBanner from '@/components/games/DailyChallengeBanner';
import RecruitLeaderboardCard from '@/components/RecruitLeaderboardCard';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

export default function GamesModule({ onBack }) {
  const { t } = useTranslation();
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [view, setView] = useState('hub'); // hub | spin
  const [status, setStatus] = useState(null);
  const [toggles, setToggles] = useState({ wheel_enabled: true, quiz_enabled: true, tap_enabled: true, unavailable_message: 'Ce jeu est temporairement indisponible.' });
  const [leaderboard, setLeaderboard] = useState([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const loadStatus = async () => {
    try {
      const { data } = await axios.get(`${API}/api/games/status`, { withCredentials: true });
      setStatus(data);
    } catch (_) {}
  };
  const loadToggles = async () => {
    try {
      const { data } = await axios.get(`${API}/api/games/toggles`);
      setToggles(data);
    } catch (_) {}
  };
  const [leaderboardScope, setLeaderboardScope] = useState('country');
  const [meRanks, setMeRanks] = useState({ rank_global: null, rank_country: null });
  const loadLeaderboard = async (scope = leaderboardScope) => {
    try {
      const { data } = await axios.get(`${API}/api/games/leaderboard`, {
        params: { scope, game: 'all', period: '30d', limit: 50 },
        withCredentials: true,
      });
      setLeaderboard(data.items || []);
      setMeRanks({
        rank_global:  data.me?.rank_global,
        rank_country: data.me?.rank_country,
        country_code: data.me?.country_code || '',
        is_country_champion: Boolean(data.me?.is_country_champion),
        country_champion_badge: data.me?.country_champion_badge || '',
      });
    } catch {}
  };
  useEffect(() => { loadStatus(); loadLeaderboard(); loadToggles(); }, []);
  useEffect(() => { loadLeaderboard(leaderboardScope); /* eslint-disable-next-line */ }, [leaderboardScope]);

  if (view === 'spin') return <SpinGame onBack={() => { setView('hub'); loadStatus(); refreshUser(); }} status={status} />;

  return (
    <div className="p-6 max-w-4xl mx-auto pb-24" data-testid="games-page">
      <button onClick={onBack || (() => navigate('/services'))} className="text-xs font-['Manrope'] mb-3 flex items-center gap-1"
        style={{ color: 'var(--jp-text-muted)' }} data-testid="back-to-services-from-games">
        <CaretLeft size={14} /> Services
      </button>
      <h1 className="font-['Outfit'] text-2xl font-bold mb-1" style={{ color: 'var(--jp-text)' }}>Jeux JAPAP</h1>
      <p className="text-xs font-['Manrope'] mb-6" style={{ color: 'var(--jp-text-secondary)' }}>
        Cumulez des points sur 30 jours et débloquez votre Pack Starter Pro — plus des mini-jeux XAF quotidiens.
      </p>

      {/* iter130 — Phase 3.E: Daily Challenge banner (state-aware) */}
      <DailyChallengeBanner />

      {/* Daily status */}
      {status && (
        <div className="rounded-2xl p-5 mb-6" style={{ background: 'linear-gradient(135deg, #0F056B 0%, #E01C2E 100%)' }} data-testid="daily-status">
          <div className="flex justify-between items-center text-white">
            <div>
              <p className="text-[11px] uppercase tracking-widest font-bold opacity-70 font-['Manrope']">Gagné aujourd'hui</p>
              <p className="font-['Outfit'] text-3xl font-extrabold">{parseFloat(status.earned_today).toLocaleString('fr-FR')} XAF</p>
            </div>
            <div className="text-right">
              <p className="text-[11px] uppercase tracking-widest font-bold opacity-70 font-['Manrope']">Plafond / jour</p>
              <p className="font-['Outfit'] text-xl font-bold">{parseFloat(status.daily_cap).toLocaleString('fr-FR')} XAF</p>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        <GameCard
          icon={<Target size={32} weight="fill" />}
          label="Roue de la Fortune"
          desc="Cycle 30 jours · 10 000 pts + 25 jours de jeu → Pack Starter Pro offert"
          color="#FFD700"
          remaining={null}
          disabled={!toggles.wheel_enabled}
          disabledMessage={toggles.unavailable_message}
          onClick={() => navigate('/games/wheel')}
          testid="game-wheel-fortune"
          badge="ENGAGEMENT"
        />
        <GameCard
          icon={<Question size={32} weight="fill" />}
          label="Quiz JAPAP"
          desc="5 questions · 10 s chrono · +20 pts/bonne réponse → Starter Pro"
          color="#8B5CF6"
          remaining={null}
          disabled={!toggles.quiz_enabled}
          disabledMessage={toggles.unavailable_message}
          onClick={() => navigate('/games/quiz')}
          testid="game-quiz-japap"
          badge="ENGAGEMENT"
        />
        <GameCard
          icon={<HandTap size={32} weight="fill" />}
          label="Tap Challenge"
          desc="10 s chrono · 1 pt/tap + bonus palier → Starter Pro"
          color="#E01C2E"
          remaining={null}
          disabled={!toggles.tap_enabled}
          disabledMessage={toggles.unavailable_message}
          onClick={() => navigate('/games/tap')}
          testid="game-tap-challenge"
          badge="ENGAGEMENT"
        />
      </div>

      <div className="mb-4">
        <GameCard
          icon={<Crown size={32} weight="fill" />}
          label="Champion par Pays"
          desc="Défiez le top joueur de votre pays · Free ou Paid · 10% commission JAPAP"
          color="#FFD700"
          remaining={null}
          disabled={!toggles.quiz_enabled}
          disabledMessage={toggles.unavailable_message}
          onClick={() => navigate('/games/quiz/champion')}
          testid="game-quiz-champion"
          badge="NOUVEAU"
        />
      </div>

      <div className="mb-4">
        <GameCard
          icon={<Lightning size={32} weight="fill" />}
          label="Défi du jour"
          desc="1 fois par jour · 5 questions inédites · jusqu'à 225 pts + bonus série"
          color="#FF6B00"
          remaining={null}
          disabled={!toggles.quiz_enabled}
          disabledMessage={toggles.unavailable_message}
          onClick={() => navigate('/games/quiz/daily')}
          testid="game-quiz-daily"
          badge="QUOTIDIEN"
        />
      </div>

      <div className="mb-4">
        <GameCard
          icon={<Trophy size={32} weight="fill" />}
          label="Top joueurs"
          desc="Consultez le classement 30 jours en bas de page"
          color="#0F056B"
          remaining={null}
          onClick={() => document.querySelector('[data-testid="engagement-leaderboard"]')?.scrollIntoView({ behavior: 'smooth' })}
          testid="game-leaderboard-jump"
        />
      </div>

      <div className="text-[11px] uppercase tracking-widest font-bold mb-2 mt-6"
           style={{ color: 'var(--jp-text-muted)' }}>{t('games.mini_jeux_xaf_quotidiens')}</div>
      <div className="grid grid-cols-1 gap-4 mb-6">
        <GameCard icon={<Sparkle size={32} weight="fill" />} label="Mini-spin XAF" desc="3 spins/jour — jusqu'à 500 XAF"
          color="#F59E0B" remaining={status?.games?.spin?.remaining} onClick={() => setView('spin')} testid="game-spin" />
      </div>

      {/* Engagement leaderboard (Quiz + Tap + Wheel combined — this week) */}
      <div className="mb-6">
        <EngagementLeaderboard />
      </div>

      {/* iter141seven — Top Recruteurs JAPAP (viral leaderboard) */}
      <div className="mb-6">
        <RecruitLeaderboardCard />
      </div>

      {/* Legacy XAF games leaderboard (Mini-spin XAF winners, 30d) */}
      {leaderboard.length > 0 && (
        <div className="jp-card-elevated p-5" data-testid="games-leaderboard">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2"
                style={{ color: 'var(--jp-text)' }}>
              <Trophy size={20} style={{ color: '#F59E0B' }} weight="fill" /> Classement gagnants
            </h3>
            <div className="flex gap-1 p-0.5 rounded-lg"
                 style={{ background: 'var(--jp-surface-secondary)' }}>
              <button onClick={() => setLeaderboardScope('country')}
                      className={`px-3 py-1 rounded-md text-[10px] font-bold ${leaderboardScope === 'country' ? 'jp-btn-primary' : ''}`}
                      data-testid="leaderboard-scope-country">
                {meRanks.country_code || 'Pays'}
              </button>
              <button onClick={() => setLeaderboardScope('global')}
                      className={`px-3 py-1 rounded-md text-[10px] font-bold ${leaderboardScope === 'global' ? 'jp-btn-primary' : ''}`}
                      data-testid="leaderboard-scope-global">
                Mondial
              </button>
            </div>
          </div>
          {(meRanks.rank_country || meRanks.rank_global) && (
            <div className="flex gap-2 mb-3 flex-wrap" data-testid="leaderboard-me-ranks">
              {meRanks.is_country_champion && meRanks.country_champion_badge && (
                <span className="text-[10px] px-2 py-0.5 rounded-full font-bold inline-flex items-center gap-1"
                      style={{ background: '#F7931A20', color: '#F7931A',
                               border: '1px solid #F7931A60' }}
                      data-testid="leaderboard-me-champion-badge">
                  🏆 {meRanks.country_champion_badge}
                </span>
              )}
              {meRanks.rank_country && (
                <span className="text-[10px] px-2 py-0.5 rounded-full font-bold"
                      style={{ background: '#10b98120', color: '#10b981' }}>
                  Vous · #{meRanks.rank_country} {meRanks.country_code}
                </span>
              )}
              {meRanks.rank_global && (
                <span className="text-[10px] px-2 py-0.5 rounded-full font-bold"
                      style={{ background: '#7c3aed20', color: '#7c3aed' }}>
                  Vous · #{meRanks.rank_global} mondial
                </span>
              )}
            </div>
          )}
          <div className="space-y-2">
            {leaderboard.map(row => (
              <div key={row.user_id} className="flex items-center gap-3 p-3 rounded-xl"
                style={{ background: row.user_id === user?.user_id ? 'rgba(15,5,107,0.08)' : 'var(--jp-surface-secondary)' }}>
                <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold font-['Outfit']"
                  style={{
                    background: row.rank === 1 ? '#FFD700' : row.rank === 2 ? '#C0C0C0' : row.rank === 3 ? '#CD7F32' : 'var(--jp-surface)',
                    color: row.rank <= 3 ? '#000' : 'var(--jp-text)',
                  }}>{row.rank}</div>
                <div className="jp-avatar jp-avatar-sm" style={{ background: 'var(--jp-primary)', color: '#fff' }}>
                  {row.avatar ? <img src={row.avatar} alt="" /> : (row.name[0] || '?').toUpperCase()}
                </div>
                <p className="flex-1 text-sm font-semibold font-['Manrope']" style={{ color: 'var(--jp-text)' }}>
                  {row.name}{row.country_code && <span className="text-[10px] opacity-50 ml-1">· {row.country_code}</span>}
                </p>
                <span className="text-sm font-bold font-['Outfit']" style={{ color: 'var(--jp-secondary)' }}>
                  {parseFloat(row.total).toLocaleString('fr-FR')} XAF
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function GameCard({ icon, label, desc, color, remaining, onClick, testid, badge, disabled, disabledMessage }) {
  const quotaDone = remaining === 0;
  const isOff = !!disabled;
  const notClickable = quotaDone || isOff;
  const handle = () => {
    if (isOff) {
      import('sonner').then(({ toast }) => toast.error(disabledMessage || 'Indisponible'));
      return;
    }
    if (!notClickable) onClick?.();
  };
  return (
    <button onClick={handle} disabled={quotaDone} data-testid={testid}
      className="jp-card p-5 text-left transition-transform hover:scale-[1.02] disabled:opacity-50 disabled:cursor-not-allowed relative">
      {badge && !isOff && (
        <span className="absolute top-3 right-3 text-[9px] font-bold font-['Outfit'] px-2 py-0.5 rounded-full tracking-widest"
              style={{ background: color, color: '#111' }}>{badge}</span>
      )}
      {isOff && (
        <span className="absolute top-3 right-3 text-[9px] font-bold font-['Outfit'] px-2 py-0.5 rounded-full tracking-widest"
              style={{ background: '#E01C2E', color: '#fff' }}
              data-testid={`${testid}-off-badge`}>INDISPONIBLE</span>
      )}
      <div className={`w-14 h-14 rounded-2xl flex items-center justify-center mb-3 ${isOff ? 'grayscale opacity-50' : ''}`}
        style={{ background: color, color: '#fff' }}>{icon}</div>
      <p className="font-['Outfit'] font-bold text-sm mb-1" style={{ color: isOff ? 'var(--jp-text-muted)' : 'var(--jp-text)' }}>
        {label}
      </p>
      <p className="text-[11px] font-['Manrope'] mb-2" style={{ color: 'var(--jp-text-secondary)' }}>
        {isOff ? (disabledMessage || 'Ce jeu est temporairement indisponible.') : desc}
      </p>
      {!isOff && remaining !== null && (
        <span className="text-[10px] font-bold font-['Manrope'] px-2 py-0.5 rounded-full"
          style={{ color, background: 'rgba(255,255,255,0.1)', border: `1px solid ${color}` }}>
          {remaining !== undefined ? `${remaining} restant(s)` : '…'}
        </span>
      )}
    </button>
  );
}

function SpinGame({ onBack }) {
  const [spinning, setSpinning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const doSpin = async () => {
    setError(''); setResult(null); setSpinning(true);
    try {
      const { data } = await axios.post(`${API}/api/games/spin`, {}, { withCredentials: true });
      setTimeout(() => { setResult(data); setSpinning(false); }, 2000);
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur');
      setSpinning(false);
    }
  };

  return (
    <div className="p-6 max-w-xl mx-auto pb-24 text-center" data-testid="spin-page">
      <button onClick={onBack} className="text-xs font-['Manrope'] mb-3 flex items-center gap-1"
        style={{ color: 'var(--jp-text-muted)' }}>
        <CaretLeft size={14} /> Retour jeux
      </button>
      <h1 className="font-['Outfit'] text-2xl font-bold mb-6" style={{ color: 'var(--jp-text)' }}>Roue de la fortune</h1>

      {error && <div className="jp-alert jp-alert-error mb-4">{error}</div>}

      <div className="mx-auto w-64 h-64 rounded-full flex items-center justify-center mb-6 transition-transform"
        style={{
          background: 'conic-gradient(#E01C2E 0% 14%, #F59E0B 14% 28%, #10B981 28% 42%, #0F056B 42% 56%, #4A90E2 56% 70%, #9333EA 70% 85%, #DC2626 85% 100%)',
          transform: spinning ? 'rotate(1800deg)' : 'rotate(0deg)',
          transition: spinning ? 'transform 2s cubic-bezier(0.25,0.46,0.45,0.94)' : 'none',
        }} data-testid="spin-wheel">
        <div className="w-40 h-40 rounded-full bg-white flex items-center justify-center shadow-2xl">
          <Sparkle size={60} style={{ color: '#F59E0B' }} weight="fill" />
        </div>
      </div>

      {result && (
        <div className="jp-card-elevated p-6 mb-4 jp-animate-scaleIn" data-testid="spin-result">
          <p className="font-['Outfit'] text-3xl font-extrabold mb-2"
            style={{ color: parseFloat(result.reward) > 0 ? 'var(--jp-secondary)' : 'var(--jp-text-muted)' }}>
            {parseFloat(result.reward) > 0 ? `+${result.reward} XAF` : '0 XAF'}
          </p>
          <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>{result.message}</p>
        </div>
      )}

      <button onClick={doSpin} disabled={spinning} className="jp-btn jp-btn-primary jp-btn-lg"
        data-testid="spin-btn">
        {spinning ? 'Rotation...' : 'Tourner la roue !'}
      </button>
    </div>
  );
}
