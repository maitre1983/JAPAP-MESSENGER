/**
 * QuizChallengesPage — "Mes Défis" hub.
 * 3 tabs: pending / active / done. Mobile-first.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Sword, Crown, Coin, Trophy } from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import { useTranslation } from 'react-i18next';
const API = process.env.REACT_APP_BACKEND_URL;

const getTabs = (t) => ([
  { id: 'pending',  label: 'En attente',  statuses: ['pending'] },
  { id: 'active',   label: 'En cours',    statuses: ['accepted', 'challenger_played', 'champion_played'] },
  { id: 'done',     label: t('quiz_challenges.termines'),    statuses: ['completed', 'refused', 'expired'] },
]);

function flag(cc) {
  if (!cc || cc.length !== 2) return '🏳️';
  return cc.toUpperCase().replace(/./g, c => String.fromCodePoint(127397 + c.charCodeAt(0)));
}

export default function QuizChallengesPage() {
  const { t } = useTranslation();
  const TABS = getTabs(t);
  const navigate = useNavigate();
  const { user } = useAuth();
  const [tab, setTab] = useState('pending');
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/quiz/champion/challenges/me`,
        { params: { limit: 100 }, withCredentials: true });
      setItems(data.items || []);
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const tabDef = TABS.find(t => t.id === tab) || TABS[0];
  const visible = items.filter(it => tabDef.statuses.includes(it.status));

  return (
    <div className="min-h-screen pb-24" style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }}
         data-testid="quiz-challenges-page">
      <div className="flex items-center p-4 gap-2">
        <button onClick={() => navigate(-1)} className="p-2 rounded-full text-white"
                style={{ background: 'rgba(255,255,255,0.1)' }}
                data-testid="quiz-challenges-back">
          <ArrowLeft size={18} weight="bold" />
        </button>
        <h1 className="text-white font-['Outfit'] text-lg font-bold flex-1">Mes Défis</h1>
        <button onClick={() => navigate('/games/quiz/champion')}
                className="text-xs px-3 py-1.5 rounded-full font-bold"
                style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}
                data-testid="quiz-challenges-go-champions">
          Défier
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 px-4 mb-4">
        {TABS.map(t => {
          const count = items.filter(it => t.statuses.includes(it.status)).length;
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
                    data-testid={`quiz-challenges-tab-${t.id}`}
                    className="flex-1 py-2 rounded-xl text-sm font-bold transition-all"
                    style={{
                      background: tab === t.id ? 'linear-gradient(90deg, #A78BFA, #8B5CF6)' : 'rgba(255,255,255,0.06)',
                      color: '#fff',
                      border: `1px solid ${tab === t.id ? '#A78BFA' : 'rgba(255,255,255,0.1)'}`,
                    }}>
              {t.label}{count > 0 && <span className="ml-1.5 text-[10px] opacity-80">({count})</span>}
            </button>
          );
        })}
      </div>

      {/* List */}
      <div className="px-4 space-y-2">
        {loading ? (
          <div className="text-white/60 text-sm text-center py-12">Chargement…</div>
        ) : visible.length === 0 ? (
          <div className="text-white/40 text-sm text-center py-12 italic">
            {tab === 'pending' ? t('quiz_challenges.aucun_defi_en_attente') :
             tab === 'active'  ? t('quiz_challenges.aucun_defi_en_cours') :
                                  t('quiz_challenges.aucun_defi_termine')}
          </div>
        ) : visible.map(it => (
          <ChallengeRow key={it.challenge_id} item={it} userId={user?.user_id}
                        onClick={() => navigate(`/games/quiz/challenges/${it.challenge_id}`)} />
        ))}
      </div>
    </div>
  );
}

function ChallengeRow({ item, userId, onClick }) {
  const { t } = useTranslation();
  const isChallenger = item.role === 'challenger';
  const opponentLabel = isChallenger
    ? `Champion ${flag(item.country_code)} ${item.country_code}`
    : `Challenger contre ${flag(item.country_code)} ${item.country_code}`;
  const opponent = isChallenger ? item.champion_user_id : item.challenger_user_id;
  const isPaid = item.mode === 'paid';
  const myScore = isChallenger ? item.challenger_score : item.champion_score;
  const oppScore = isChallenger ? item.champion_score : item.challenger_score;
  const won = item.status === 'completed' && item.winner_user_id === userId;
  const tied = item.status === 'completed' && item.winner_user_id === null;
  const lost = item.status === 'completed' && item.winner_user_id && item.winner_user_id !== userId;

  let status_color = 'rgba(255,255,255,0.12)';
  let status_label = item.status;
  if (item.status === 'pending') { status_color = '#F7931A'; status_label = 'En attente'; }
  else if (item.status === 'accepted') { status_color = '#A78BFA'; status_label = 'À jouer'; }
  else if (item.status === 'challenger_played' || item.status === 'champion_played') {
    status_color = '#A78BFA';
    status_label = (item.status === 'challenger_played' ? (isChallenger ? 'Attente adversaire' : t('quiz_challenges.a_vous_de_jouer'))
                                                       : (isChallenger ? t('quiz_challenges.a_vous_de_jouer') : 'Attente adversaire'));
  }
  else if (won)  { status_color = '#FFD700'; status_label = 'Gagné 🏆'; }
  else if (tied) { status_color = '#A78BFA'; status_label = 'Égalité'; }
  else if (lost) { status_color = '#E01C2E'; status_label = 'Perdu'; }
  else if (item.status === 'refused') { status_color = '#E01C2E'; status_label = 'Refusé'; }
  else if (item.status === 'expired') { status_color = '#666';     status_label = 'Expiré'; }

  return (
    <button onClick={onClick}
            data-testid={`quiz-challenge-row-${item.challenge_id}`}
            className="w-full text-left p-3 rounded-xl flex items-center gap-3 active:scale-[0.98] transition-all"
            style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)' }}>
      <div className="w-10 h-10 rounded-full flex items-center justify-center"
           style={{ background: isChallenger ? 'rgba(224,28,46,0.18)' : 'rgba(255,215,0,0.18)' }}>
        {isChallenger ? <Sword size={18} weight="fill" color="#E01C2E" /> : <Crown size={18} weight="fill" color="#FFD700" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-white font-bold text-sm truncate">{opponentLabel}</div>
        <div className="text-white/50 text-[11px] truncate">
          {isPaid && <Coin size={10} weight="fill" color="#FFD700" className="inline mr-1" />}
          {isPaid ? `${item.stake_amount} ${item.stake_currency}` : 'Mode gratuit'}
          {(myScore != null || oppScore != null) && ` · ${myScore ?? '−'} vs ${oppScore ?? '−'}`}
        </div>
      </div>
      <div className="px-2 py-1 rounded-full text-[10px] font-bold shrink-0"
           style={{ background: status_color, color: '#111' }}>
        {status_label}
      </div>
    </button>
  );
}
