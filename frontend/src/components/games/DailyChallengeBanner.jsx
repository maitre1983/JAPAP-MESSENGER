import { useEffect, useState } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import { Lightning, Flame, ShareNetwork, Clock, Star, CurrencyDollar } from '@phosphor-icons/react';
import { toast } from 'sonner';
import PaidDailyChallengeFlow from '@/components/games/PaidDailyChallengeFlow';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * iter130 — Phase 3.E: Daily Challenge entry tile + banner.
 * Shows:
 *   - Available  → flashy CTA "Jouer le défi du jour"
 *   - Played     → result + countdown + share button
 *   - Disabled   → small grey state (admin kill-switch)
 */
export default function DailyChallengeBanner() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [countdown, setCountdown] = useState('');
  // iter237k — Paid daily challenge state.
  const [paidConfig, setPaidConfig] = useState(null);
  const [paidOpen, setPaidOpen] = useState(false);

  const load = async () => {
    try {
      const { data } = await axios.get(`${API}/api/quiz/daily-challenge/status`, {
        withCredentials: true,
      });
      setData(data);
    } catch (e) {
      // iter237k — keep banner resilient: log silent failure so devs can
      // diagnose why the free-mode card disappeared, without breaking
      // the paid-mode fallback render below.
      console.warn('[DailyChallengeBanner] status fetch failed', e?.response?.status || e?.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  // iter237k — Load paid config (best-effort, silent if unavailable).
  const loadPaidConfig = async () => {
    try {
      const { data } = await axios.get(`${API}/api/quiz/daily-challenge/paid/config`,
                                         { withCredentials: true });
      setPaidConfig(data);
    } catch (_) { setPaidConfig(null); }
  };

  useEffect(() => { load(); loadPaidConfig(); }, []);

  // Countdown to next eligible play
  useEffect(() => {
    if (!data?.next_eligible_at) return;
    const target = new Date(data.next_eligible_at).getTime();
    const tick = () => {
      const now = Date.now();
      const diff = target - now;
      if (diff <= 0) {
        setCountdown('Disponible !');
        load();
        return;
      }
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      setCountdown(`${String(h).padStart(2, '0')}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [data?.next_eligible_at]);

  if (loading) return null;
  // iter237k — When status fetch failed (data null) OR free DC disabled,
  // we still want to surface the PAID daily challenge tile if available.
  if (!data || !data.enabled) {
    if (paidConfig?.enabled && !paidConfig?.played_today) {
      return (
        <div
          data-testid="daily-challenge-banner-paid-only"
          className="rounded-2xl p-5 mb-4 text-white relative overflow-hidden"
          style={{
            background: 'linear-gradient(135deg, #B45309 0%, #F7931A 60%, #FFD700 100%)',
            boxShadow: '0 8px 24px rgba(247, 147, 26, 0.35)',
          }}
        >
          <div className="absolute top-0 right-0 opacity-10">
            <CurrencyDollar size={140} weight="fill" />
          </div>
          <div className="relative">
            <h3 className="font-['Outfit'] text-2xl font-extrabold mb-1">
              💰 Défi expert payant
            </h3>
            <p className="text-sm opacity-90 mb-3">
              Mise libre · 5 questions · Score 5/5 = +50% du gain
            </p>
            <button
              onClick={() => setPaidOpen(true)}
              data-testid="daily-challenge-paid-btn"
              className="px-6 py-3 rounded-xl font-['Outfit'] font-bold text-sm transition-all hover:scale-105 active:scale-95 inline-flex items-center gap-1.5"
              style={{ background: 'white', color: '#B45309' }}
            >
              <CurrencyDollar size={14} weight="fill" /> Miser
            </button>
            <PaidDailyChallengeFlow
              open={paidOpen}
              onClose={() => { setPaidOpen(false); loadPaidConfig(); }}
              onPlayed={() => { loadPaidConfig(); load(); }}
            />
          </div>
        </div>
      );
    }
    return null;
  }

  const streak = data.streak?.current_streak || 0;
  const longest = data.streak?.longest_streak || 0;

  // ============ AVAILABLE STATE — "play now" CTA ============
  if (data.available) {
    return (
      <div
        data-testid="daily-challenge-banner-available"
        className="rounded-2xl p-5 mb-4 text-white relative overflow-hidden"
        style={{
          background: 'linear-gradient(135deg, #FF6B00 0%, #E01C2E 50%, #8B5CF6 100%)',
          boxShadow: '0 8px 24px rgba(224, 28, 46, 0.35)',
        }}
      >
        <div className="absolute top-0 right-0 opacity-10">
          <Lightning size={140} weight="fill" />
        </div>
        <div className="relative z-10">
          <div className="flex items-center gap-2 mb-1">
            <Lightning size={20} weight="fill" />
            <span className="text-[11px] uppercase tracking-widest font-bold font-['Manrope'] opacity-90">
              Défi quotidien
            </span>
          </div>
          <h3 className="font-['Outfit'] text-2xl font-extrabold mb-1">
            Le défi du jour t'attend
          </h3>
          <p className="text-sm font-['Manrope'] opacity-90 mb-4">
            5 questions fraîches · jamais vues · jusqu'à <strong>225 pts</strong> + bonus série
          </p>
          {streak > 0 && (
            <div className="flex items-center gap-2 mb-3 text-xs">
              <Flame size={16} weight="fill" style={{ color: '#FFE066' }} />
              <span className="font-['Manrope'] font-bold">
                Série actuelle : {streak} jour{streak > 1 ? 's' : ''}
                {longest > streak && ` · Record : ${longest}`}
              </span>
            </div>
          )}
          <button
            onClick={() => navigate('/games/quiz/daily')}
            data-testid="daily-challenge-play-btn"
            className="px-6 py-3 rounded-xl font-['Outfit'] font-bold text-sm transition-all hover:scale-105 active:scale-95"
            style={{ background: 'white', color: '#E01C2E' }}
          >
            Jouer maintenant →
          </button>
          {/* iter237k — Mode payant : 2e CTA en dessous, only if backend says enabled */}
          {paidConfig?.enabled && !paidConfig?.played_today && (
            <button
              onClick={() => setPaidOpen(true)}
              data-testid="daily-challenge-paid-btn"
              className="ml-2 px-6 py-3 rounded-xl font-['Outfit'] font-bold text-sm transition-all hover:scale-105 active:scale-95 inline-flex items-center gap-1.5"
              style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}
            >
              <CurrencyDollar size={14} weight="fill" /> Miser
            </button>
          )}
          <PaidDailyChallengeFlow open={paidOpen}
                                   onClose={() => { setPaidOpen(false); loadPaidConfig(); }}
                                   onPlayed={() => { loadPaidConfig(); load(); }} />
        </div>
      </div>
    );
  }

  // ============ PLAYED STATE — countdown + share ============
  const result = data.result || {};
  const correct = result.correct_count ?? 0;
  const points = result.points_awarded ?? 0;
  const isPerfect = correct === 5;

  const shareText = `Je viens de scorer ${correct}/5 au défi quotidien JAPAP (${points} pts) ! Série de ${streak} jours 🔥 Joue avec moi sur JAPAP.`;
  const shareUrl = `${window.location.origin}/games`;

  const onShare = async () => {
    try {
      if (navigator.share) {
        await navigator.share({ title: 'Défi JAPAP', text: shareText, url: shareUrl });
      } else {
        await navigator.clipboard.writeText(`${shareText} ${shareUrl}`);
        toast.success('Lien copié !');
      }
    } catch (_) {
      try {
        await navigator.clipboard.writeText(`${shareText} ${shareUrl}`);
        toast.success('Lien copié !');
      } catch {}
    }
  };

  const onShareWhatsApp = () => {
    const url = `https://wa.me/?text=${encodeURIComponent(`${shareText} ${shareUrl}`)}`;
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  return (
    <div
      data-testid="daily-challenge-banner-played"
      className="rounded-2xl p-5 mb-4 text-white relative overflow-hidden"
      style={{
        background: isPerfect
          ? 'linear-gradient(135deg, #FFD700 0%, #FFA500 100%)'
          : 'linear-gradient(135deg, #0F056B 0%, #4338CA 100%)',
      }}
    >
      <div className="absolute top-0 right-0 opacity-10">
        {isPerfect ? <Star size={140} weight="fill" /> : <Clock size={140} weight="fill" />}
      </div>
      <div className="relative z-10">
        <div className="flex items-center gap-2 mb-1">
          <Lightning size={18} weight="fill" />
          <span className="text-[11px] uppercase tracking-widest font-bold font-['Manrope'] opacity-90">
            Défi quotidien · joué
          </span>
        </div>
        <h3 className="font-['Outfit'] text-2xl font-extrabold mb-1">
          {isPerfect ? '🏆 Sans-faute légendaire !' : `${correct}/5 · ${points} pts`}
        </h3>
        <p className="text-sm font-['Manrope'] opacity-90 mb-3">
          {isPerfect
            ? 'Tu es au sommet aujourd\'hui — reviens demain pour étendre ta série !'
            : 'Reviens demain pour un nouveau défi — ne casse pas ta série !'}
        </p>

        <div className="flex items-center gap-2 mb-3">
          <Flame size={16} weight="fill" style={{ color: '#FFE066' }} />
          <span className="text-xs font-['Manrope'] font-bold">
            Série : {streak} jour{streak > 1 ? 's' : ''}
            {longest > streak && ` · Record : ${longest}`}
          </span>
        </div>

        <div className="flex items-center gap-2 mb-4 text-xs">
          <Clock size={14} />
          <span className="font-['Manrope']" data-testid="daily-challenge-countdown">
            Prochain défi dans <strong>{countdown}</strong>
          </span>
        </div>

        <div className="flex gap-2">
          <button
            onClick={onShareWhatsApp}
            data-testid="daily-challenge-share-whatsapp"
            className="flex-1 px-4 py-2.5 rounded-xl font-['Outfit'] font-bold text-sm transition-all hover:scale-105 active:scale-95 flex items-center justify-center gap-2"
            style={{ background: '#25D366', color: 'white' }}
          >
            <ShareNetwork size={16} weight="fill" /> WhatsApp
          </button>
          <button
            onClick={onShare}
            data-testid="daily-challenge-share-native"
            className="flex-1 px-4 py-2.5 rounded-xl font-['Outfit'] font-bold text-sm transition-all hover:scale-105 active:scale-95 flex items-center justify-center gap-2"
            style={{ background: 'white', color: isPerfect ? '#E01C2E' : '#0F056B' }}
          >
            <ShareNetwork size={16} weight="fill" /> Partager
          </button>
        </div>
        {/* iter237k — paid mode is independent of free; offer it even after free played */}
        {paidConfig?.enabled && !paidConfig?.played_today && (
          <button
            onClick={() => setPaidOpen(true)}
            data-testid="daily-challenge-paid-btn-played"
            className="w-full mt-2 px-4 py-2.5 rounded-xl font-['Outfit'] font-bold text-sm transition-all hover:scale-105 active:scale-95 flex items-center justify-center gap-2"
            style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111' }}>
            <CurrencyDollar size={16} weight="fill" /> Miser sur le défi expert
          </button>
        )}
        <PaidDailyChallengeFlow open={paidOpen}
                                 onClose={() => { setPaidOpen(false); loadPaidConfig(); }}
                                 onPlayed={() => { loadPaidConfig(); load(); }} />
      </div>
    </div>
  );
}
