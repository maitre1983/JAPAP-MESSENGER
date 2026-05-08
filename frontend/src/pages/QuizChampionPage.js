/**
 * Quiz Champion par Pays — page (iter126, Phase 3.C).
 *
 * Public-feeling page that shows the active champion of a country with
 * a "Défier" CTA, plus the "Challengers de la semaine" leaderboard
 * (free + paid splits). Mobile-first.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Crown, Trophy, FlagBanner, Coin, Sword } from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import DefyChampionModal from '@/components/games/DefyChampionModal';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;

const DEFAULT_COUNTRIES = [
  { code: 'CM', name: 'Cameroun' },
  { code: 'SN', name: 'Sénégal' },
  { code: 'CI', name: 'Côte d\'Ivoire' },
  { code: 'BJ', name: 'Bénin' },
  { code: 'TG', name: 'Togo' },
  { code: 'BF', name: 'Burkina Faso' },
  { code: 'ML', name: 'Mali' },
  { code: 'NE', name: 'Niger' },
  { code: 'GA', name: 'Gabon' },
  { code: 'GH', name: 'Ghana' },
  { code: 'NG', name: 'Nigeria' },
  { code: 'KE', name: 'Kenya' },
];

function flag(cc) {
  if (!cc || cc.length !== 2) return '🏳️';
  return cc.toUpperCase().replace(/./g, c => String.fromCodePoint(127397 + c.charCodeAt(0)));
}

export default function QuizChampionPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { country: countryParam } = useParams();
  const initial = (countryParam || user?.country_code || 'CM').toUpperCase();
  const [country, setCountry] = useState(initial);
  const [champ, setChamp] = useState(null);
  const [loading, setLoading] = useState(true);
  const [board, setBoard] = useState({ free: [], paid: [] });
  const [defyOpen, setDefyOpen] = useState(false);
  const [config, setConfig] = useState({});

  const loadChampion = useCallback(async (cc) => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/quiz/champion/${cc}`);
      setChamp(data);
    } catch (e) {
      setChamp(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadBoard = useCallback(async (cc) => {
    try {
      const { data } = await axios.get(`${API}/api/quiz/champion/leaderboard/challengers`,
        { params: { country_code: cc, limit: 3 } });
      setBoard(data);
    } catch {}
  }, []);

  const loadConfig = useCallback(async () => {
    try {
      // Public read of admin config (uses /api/admin/games/quiz only for admins;
      // we use the public toggles + start endpoint for runtime values). For the
      // modal we read a small public shape.
      const { data } = await axios.get(`${API}/api/games/toggles`);
      setConfig(data || {});
    } catch {}
  }, []);

  useEffect(() => {
    loadChampion(country);
    loadBoard(country);
    loadConfig();
  }, [country, loadChampion, loadBoard, loadConfig]);

  const isMe = champ && user && champ.user_id === user.user_id;

  return (
    <div className="min-h-screen pb-24" style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)' }} data-testid="quiz-champion-page">
      <div className="flex items-center p-4 gap-2">
        <button onClick={() => navigate(-1)} className="p-2 rounded-full text-white"
                style={{ background: 'rgba(255,255,255,0.1)' }}
                data-testid="quiz-champion-back">
          <ArrowLeft size={18} weight="bold" />
        </button>
        <h1 className="text-white font-['Outfit'] text-lg font-bold flex-1">Champion par Pays</h1>
        <button onClick={() => navigate('/games/quiz/challenges')}
                data-testid="quiz-champion-go-challenges"
                className="text-xs px-3 py-1.5 rounded-full font-bold"
                style={{ background: 'rgba(255,255,255,0.12)', color: '#fff' }}>
          Mes défis
        </button>
      </div>

      {/* Country selector */}
      <div className="px-4 mb-4">
        <div className="flex items-center gap-2 overflow-x-auto pb-2"
             style={{ scrollbarWidth: 'none' }} data-testid="quiz-champion-country-selector">
          {DEFAULT_COUNTRIES.map(c => (
            <button key={c.code} onClick={() => setCountry(c.code)}
                    data-testid={`quiz-champion-country-${c.code}`}
                    className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-bold flex items-center gap-1.5 transition-all`}
                    style={{
                      background: country === c.code ? 'linear-gradient(90deg, #FFD700, #F7931A)' : 'rgba(255,255,255,0.08)',
                      color: country === c.code ? '#111' : '#fff',
                      border: `1px solid ${country === c.code ? '#FFD700' : 'rgba(255,255,255,0.15)'}`,
                    }}>
              <span>{flag(c.code)}</span>{c.name}
            </button>
          ))}
        </div>
      </div>

      {/* Champion card */}
      <div className="px-4">
        {loading ? (
          <div className="text-white/60 text-sm text-center py-12">Chargement…</div>
        ) : !champ ? (
          <div className="jp-card p-8 text-center" style={{ background: 'rgba(255,255,255,0.06)', borderColor: 'rgba(255,255,255,0.1)' }}>
            <FlagBanner size={48} weight="duotone" color="#FFD700" className="mx-auto mb-3" />
            <div className="text-white font-bold mb-1">Aucun champion pour {flag(country)} {country}</div>
            <div className="text-white/60 text-xs">Le premier à dominer le top du quiz cette semaine deviendra automatiquement champion.</div>
          </div>
        ) : (
          <div className="rounded-2xl overflow-hidden p-5"
               data-testid="quiz-champion-card"
               style={{
                 background: 'linear-gradient(135deg, rgba(255,215,0,0.18), rgba(247,147,26,0.10))',
                 border: '1px solid rgba(255,215,0,0.35)',
                 boxShadow: '0 16px 48px rgba(255,215,0,0.18)',
               }}>
            <div className="flex items-center gap-4">
              <div className="relative">
                {champ.user.avatar ? (
                  <img src={champ.user.avatar.startsWith('http') ? champ.user.avatar : `${API}${champ.user.avatar}`}
                       alt="" className="w-20 h-20 rounded-full object-cover"
                       style={{ border: '3px solid #FFD700' }} />
                ) : (
                  <div className="w-20 h-20 rounded-full flex items-center justify-center text-white text-2xl font-bold"
                       style={{ background: 'linear-gradient(135deg, #FFD700, #F7931A)', border: '3px solid #FFD700' }}>
                    {(champ.user.first_name || champ.user.username || '?')[0].toUpperCase()}
                  </div>
                )}
                <div className="absolute -top-2 -right-2 w-9 h-9 rounded-full flex items-center justify-center"
                     style={{ background: '#FFD700', border: '2px solid #0F056B' }}>
                  <Crown size={18} weight="fill" color="#111" />
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-white font-['Outfit'] text-xl font-bold truncate">
                  {champ.user.first_name || champ.user.username}
                </div>
                {champ.user.username && (
                  <div className="text-white/60 text-xs truncate">@{champ.user.username}</div>
                )}
                <div className="text-[#FFD700] text-xs font-semibold mt-1 flex items-center gap-1">
                  <Crown size={12} weight="fill" /> Champion {flag(champ.country_code)} {champ.country_code}
                </div>
              </div>
            </div>
            <div className="mt-4 flex items-center justify-between text-xs text-white/70">
              <span>Promu : {champ.promoted_at ? new Date(champ.promoted_at).toLocaleDateString('fr-FR') : '—'}</span>
              <span>Refus : {champ.refusal_count_consecutive}/4 · 30j : {champ.refusal_count_30d}/4</span>
            </div>
            {!isMe ? (
              <button onClick={() => setDefyOpen(true)}
                      data-testid="quiz-champion-defy-btn"
                      className="w-full mt-5 py-3 rounded-full font-bold text-base active:scale-[0.97]"
                      style={{ background: 'linear-gradient(90deg, #E01C2E, #B91C1C)', color: '#fff',
                               boxShadow: '0 12px 36px rgba(224,28,46,0.55)' }}>
                <Sword size={18} weight="bold" className="inline mr-2" /> Défier ce champion
              </button>
            ) : (
              <div className="mt-5 px-3 py-2 rounded-full text-center text-[#FFD700] text-xs font-bold"
                   style={{ background: 'rgba(255,215,0,0.18)', border: '1px solid rgba(255,215,0,0.35)' }}>
                👑 Vous êtes le champion. Restez vigilant !
              </div>
            )}
          </div>
        )}
      </div>

      {/* Challengers leaderboard */}
      <div className="px-4 mt-6" data-testid="quiz-champion-leaderboard">
        <div className="text-white font-['Outfit'] font-bold text-sm mb-2 flex items-center gap-1">
          <Trophy size={16} weight="fill" color="#FFD700" /> Challengers de la semaine — {country}
        </div>
        <LeaderboardBlock title="🥇 Mode gratuit" items={board.free || []} mode="free" />
        <LeaderboardBlock title="💰 Mode payant" items={board.paid || []} mode="paid" />
      </div>

      {champ && !isMe && (
        <DefyChampionModal open={defyOpen}
                           onClose={() => setDefyOpen(false)}
                           champion={champ}
                           onCreated={(cid) => {
                             toast.success('Défi envoyé !');
                             setDefyOpen(false);
                             navigate(`/games/quiz/challenges/${cid}`);
                           }} />
      )}
    </div>
  );
}

function LeaderboardBlock({ title, items, mode }) {
  return (
    <div className="mb-3">
      <div className="text-white/70 text-[11px] uppercase font-bold tracking-wider mb-1.5">{title}</div>
      {items.length === 0 ? (
        <div className="text-white/40 text-xs italic px-3 py-2">Pas encore de gagnants cette semaine.</div>
      ) : items.map((it, i) => (
        <div key={it.user_id} className="flex items-center gap-3 py-2 px-3 rounded-xl mb-1.5"
             style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}
             data-testid={`leaderboard-${mode}-${i}`}>
          <div className="w-7 h-7 rounded-full text-xs font-bold flex items-center justify-center"
               style={{ background: i === 0 ? '#FFD700' : i === 1 ? '#C0C0C0' : '#CD7F32', color: '#111' }}>
            {i + 1}
          </div>
          {it.user.avatar ? (
            <img src={it.user.avatar.startsWith('http') ? it.user.avatar : `${API}${it.user.avatar}`}
                 alt="" className="w-9 h-9 rounded-full object-cover" />
          ) : (
            <div className="w-9 h-9 rounded-full bg-white/10 flex items-center justify-center text-white text-xs font-bold">
              {(it.user.first_name || it.user.username || '?')[0].toUpperCase()}
            </div>
          )}
          <div className="flex-1 min-w-0">
            <div className="text-white text-sm font-bold truncate">
              {it.user.first_name || it.user.username}
            </div>
            <div className="text-white/50 text-[10px] truncate">
              {mode === 'paid'
                ? `${it.wins_paid} victoires · ${Math.round(it.earnings).toLocaleString('fr-FR')} XAF gagnés`
                : `${it.wins_free} victoires gratuites`}
            </div>
          </div>
          {mode === 'paid' && <Coin size={16} weight="fill" color="#FFD700" />}
        </div>
      ))}
    </div>
  );
}
