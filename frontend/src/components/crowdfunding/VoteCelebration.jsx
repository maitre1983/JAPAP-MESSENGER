import { useEffect, useState } from 'react';
import { Heart, ShareNetwork, WhatsappLogo, X } from '@phosphor-icons/react';

/**
 * iter142B — Vote celebration overlay (Phase P1 viral UX).
 *
 * Bursts confetti hearts + scales a giant "MERCI ❤️" then surfaces the
 * call-to-share. Auto-dismisses after 6s if user does nothing.
 */
const HEART_COUNT = 18;

export default function VoteCelebration({
  ownerName, onClose, onShareWhatsApp, onShareNative,
}) {
  const [hearts] = useState(() =>
    Array.from({ length: HEART_COUNT }, (_, i) => ({
      id: i,
      left: Math.random() * 80 + 10,
      delay: Math.random() * 0.6,
      duration: 2 + Math.random() * 1.5,
      size: 18 + Math.floor(Math.random() * 28),
    }))
  );

  useEffect(() => {
    const t = setTimeout(onClose, 12000);
    return () => clearTimeout(t);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center p-2 bg-black/60 backdrop-blur-sm"
      onClick={onClose}
      data-testid="cf-vote-celebration"
    >
      {/* Floating hearts */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        {hearts.map((h) => (
          <span
            key={h.id}
            className="absolute"
            style={{
              left: `${h.left}%`,
              bottom: '-40px',
              animation: `cf-heart-float ${h.duration}s ${h.delay}s ease-out forwards`,
            }}
          >
            <Heart
              weight="fill"
              size={h.size}
              color={['#fbbf24', '#f43f5e', '#ec4899', '#ffffff'][h.id % 4]}
            />
          </span>
        ))}
      </div>

      <style>{`
        @keyframes cf-heart-float {
          0%   { transform: translateY(0)   scale(0.6) rotate(-10deg); opacity: 0; }
          15%  { opacity: 1; }
          100% { transform: translateY(-110vh) scale(1.1) rotate(20deg); opacity: 0; }
        }
        @keyframes cf-pop {
          0%   { transform: scale(0.4); opacity: 0; }
          60%  { transform: scale(1.15); opacity: 1; }
          100% { transform: scale(1); opacity: 1; }
        }
      `}</style>

      <div
        className="relative bg-gradient-to-br from-indigo-900 via-fuchsia-800 to-rose-700 text-white rounded-t-3xl sm:rounded-3xl w-full max-w-md p-6 shadow-2xl border border-white/15"
        onClick={(e) => e.stopPropagation()}
        style={{ animation: 'cf-pop 0.5s ease-out' }}
      >
        <button
          onClick={onClose}
          className="absolute top-3 right-3 p-1.5 rounded-full hover:bg-white/15"
          data-testid="cf-celebration-close"
          aria-label="Fermer"
        >
          <X size={18} />
        </button>

        <div className="text-center pt-2">
          <div className="inline-block bg-white/15 border-2 border-white/30 rounded-full p-4 mb-4">
            <Heart weight="fill" size={56} className="text-rose-300" />
          </div>
          <h2 className="text-2xl font-extrabold mb-1" data-testid="cf-celebration-title">
            Merci ! Tu viens de soutenir {ownerName} ❤️
          </h2>
          <p className="text-sm text-white/85 mb-5">
            Ton vote compte. Maintenant, invite tes amis à faire pareil — chaque
            partage rapproche {ownerName} de la victoire.
          </p>

          <div className="space-y-2">
            <button
              onClick={onShareWhatsApp}
              data-testid="cf-celebration-whatsapp"
              className="w-full bg-emerald-500 hover:bg-emerald-600 text-white font-bold py-3 rounded-full flex items-center justify-center gap-2 active:scale-95 transition"
            >
              <WhatsappLogo size={20} weight="fill" /> Partager sur WhatsApp
            </button>
            <button
              onClick={onShareNative}
              data-testid="cf-celebration-share"
              className="w-full bg-white/10 border border-white/30 hover:bg-white/20 text-white font-semibold py-3 rounded-full flex items-center justify-center gap-2"
            >
              <ShareNetwork size={18} /> Plus d'options
            </button>
          </div>

          <button
            onClick={onClose}
            className="mt-4 text-xs text-white/60 hover:text-white/90 underline"
            data-testid="cf-celebration-skip"
          >
            Plus tard
          </button>
        </div>
      </div>
    </div>
  );
}
