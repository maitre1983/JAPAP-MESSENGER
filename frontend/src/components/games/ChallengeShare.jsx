/**
 * ChallengeShare — viral share buttons for a completed quiz challenge.
 * Builds a dynamic message with country flag, scores, gain (if paid) and
 * a deep-link to the country's champion page so each victory becomes a
 * user-acquisition channel.
 *
 * Priorities: WhatsApp > Telegram > Copy link. Never link to homepage —
 * always to /games/quiz/champion/{country} (or fallback /p/{ref_code}).
 */
import { useState } from 'react';
import { WhatsappLogo, TelegramLogo, Copy, Check } from '@phosphor-icons/react';
import { toast } from 'sonner';

function flag(cc) {
  if (!cc || cc.length !== 2) return '🏳️';
  return cc.toUpperCase().replace(/./g, c => String.fromCodePoint(127397 + c.charCodeAt(0)));
}

export default function ChallengeShare({ challenge, userId, refCode }) {
  const [copied, setCopied] = useState(false);

  const isWinner = challenge.winner_user_id === userId;
  const isTied   = challenge.winner_user_id == null;
  const isChallenger = (challenge.role === 'challenger') || (challenge.challenger_user_id === userId);
  const myScore  = isChallenger ? challenge.challenger_score : challenge.champion_score;
  const oppScore = isChallenger ? challenge.champion_score   : challenge.challenger_score;
  const isPaid   = challenge.mode === 'paid';
  const stake    = Number(challenge.stake_amount || 0);
  const winnings = isPaid ? stake * 1.8 : 0; // pot 2× minus 10% commission

  // Deep-link priority: country champion page > referral preview > fallback
  const origin = (typeof window !== 'undefined') ? window.location.origin : '';
  const deepLink = challenge.country_code
    ? `${origin}/games/quiz/champion/${challenge.country_code}`
    : (refCode ? `${origin}/p/${refCode}` : `${origin}/games/quiz/champion`);

  // Compose dynamic message based on outcome
  let headline;
  if (isWinner) {
    headline = `🏆 Je viens de battre le champion ${flag(challenge.country_code)} ${challenge.country_code}`;
  } else if (isTied) {
    headline = `🤝 Égalité épique vs le champion ${flag(challenge.country_code)} ${challenge.country_code}`;
  } else {
    // For losers we still encourage virality — challenge.role flips
    headline = `⚔ J'ai défié le champion ${flag(challenge.country_code)} ${challenge.country_code}`;
  }
  const scoreLine = `Score : ${myScore ?? '−'}/5 vs ${oppScore ?? '−'}/5`;
  const gainLine = (isWinner && isPaid)
    ? `Gain : ${winnings.toLocaleString('fr-FR')} ${challenge.stake_currency}`
    : (isPaid ? `Mise : ${stake.toLocaleString('fr-FR')} ${challenge.stake_currency}` : '');
  const message = [
    headline,
    scoreLine,
    gainLine,
    '',
    'Penses-tu pouvoir faire mieux ? 👀',
    `👉 ${deepLink}`,
  ].filter(Boolean).join('\n');

  const shareWhatsApp = () => {
    window.open(`https://wa.me/?text=${encodeURIComponent(message)}`, '_blank');
  };
  const shareTelegram = () => {
    window.open(`https://t.me/share/url?url=${encodeURIComponent(deepLink)}&text=${encodeURIComponent(message)}`, '_blank');
  };
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(message);
      setCopied(true);
      toast.success('Message copié — collez-le où vous voulez !');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Impossible de copier');
    }
  };

  return (
    <div className="px-4 mt-5" data-testid="challenge-share">
      <div className="text-white/70 text-[10px] uppercase font-bold tracking-wider mb-2 text-center">
        Partager mon défi
      </div>
      <div className="grid grid-cols-3 gap-2">
        <button onClick={shareWhatsApp}
                data-testid="challenge-share-whatsapp"
                className="py-3 rounded-xl flex flex-col items-center justify-center gap-1 active:scale-95 transition-transform"
                style={{ background: '#25D366', color: '#fff', boxShadow: '0 8px 24px rgba(37,211,102,0.45)' }}>
          <WhatsappLogo size={22} weight="fill" />
          <span className="text-[11px] font-bold">WhatsApp</span>
        </button>
        <button onClick={shareTelegram}
                data-testid="challenge-share-telegram"
                className="py-3 rounded-xl flex flex-col items-center justify-center gap-1 active:scale-95 transition-transform"
                style={{ background: '#0088CC', color: '#fff', boxShadow: '0 8px 24px rgba(0,136,204,0.45)' }}>
          <TelegramLogo size={22} weight="fill" />
          <span className="text-[11px] font-bold">Telegram</span>
        </button>
        <button onClick={copyLink}
                data-testid="challenge-share-copy"
                className="py-3 rounded-xl flex flex-col items-center justify-center gap-1 active:scale-95 transition-transform"
                style={{ background: copied ? '#10B981' : 'rgba(255,255,255,0.08)', color: '#fff',
                         border: '1px solid rgba(255,255,255,0.15)' }}>
          {copied ? <Check size={22} weight="bold" /> : <Copy size={22} weight="fill" />}
          <span className="text-[11px] font-bold">{copied ? 'Copié !' : 'Copier'}</span>
        </button>
      </div>
      <div className="text-center text-white/40 text-[10px] mt-2">
        Lien direct vers le champion {flag(challenge.country_code)} {challenge.country_code} — vos amis arrivent en 1 clic.
      </div>
    </div>
  );
}
