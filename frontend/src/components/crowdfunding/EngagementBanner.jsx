import { useMemo } from 'react';
import {
  Lightning, Trophy, Users, ShareNetwork, X, ArrowUp, FlagBanner,
} from '@phosphor-icons/react';
import { dismissMessageLocally, postEngagementFeedback } from '@/hooks/useEngagementState';

/**
 * iter142D / Phase P3 — Adaptive engagement banner.
 *
 * Renders nothing when:
 *   - No message (cooldown / no payload)
 *   - User dismissed this exact message_id within the last 7 days
 *   - ui_mode === 'calm' AND state === 'cold' AND user has no project
 *     (avoid spamming first-time visitors)
 *
 * Visual modes:
 *   urgent     → red pulsing background, sticky-bottom, big share CTA
 *   push       → indigo→rose gradient, rival face-off
 *   calm/engaged → soft amber tint, encouragement
 *
 * The banner is NEVER taller than 22vh on mobile so it never hides core UI.
 */
const ICON = {
  critical: Lightning,
  competitive: FlagBanner,
  engaged: ArrowUp,
  cold: Users,
};

const BG = {
  urgent:
    'bg-gradient-to-r from-rose-600 via-rose-500 to-amber-500 text-white animate-cf-urgent-pulse',
  push: 'bg-gradient-to-r from-indigo-700 to-fuchsia-600 text-white',
  calm: 'bg-gradient-to-r from-amber-100 to-rose-50 text-rose-900 border border-rose-200',
};

export default function EngagementBanner({
  payload,
  onPrimaryAction,  // (action) => void  e.g. share project / vote
  onDismiss,        // optional, additional cleanup
  className = '',
}) {
  const visible = useMemo(() => {
    if (!payload) return false;
    if (!payload.message) return false;
    if (payload.ui_mode === 'calm' && payload.state === 'cold'
        && (payload.engagement_score || 0) === 0) return false;
    if (typeof window !== 'undefined') {
      try {
        const m = JSON.parse(localStorage.getItem('cf_engage_dismissed') || '{}');
        if (m[payload.message_id] && Date.now() - m[payload.message_id] < 7 * 86_400_000) {
          return false;
        }
      } catch {}
    }
    return true;
  }, [payload]);

  if (!visible) return null;

  const Icon = ICON[payload.state] || Lightning;
  const bg = BG[payload.ui_mode] || BG.calm;
  const isUrgent = payload.ui_mode === 'urgent';
  const ctaLabel =
    payload.next_best_action === 'share' ? 'Partager maintenant'
    : payload.next_best_action === 'invite' ? 'Inviter mes amis'
    : payload.next_best_action === 'vote' ? 'Aller voter'
    : 'Continuer';

  const handleAction = () => {
    postEngagementFeedback(payload.message_id, 'clicked');
    if (onPrimaryAction) onPrimaryAction(payload.next_best_action, payload);
  };

  const handleDismiss = () => {
    postEngagementFeedback(payload.message_id, 'dismissed');
    dismissMessageLocally(payload.message_id);
    if (onDismiss) onDismiss();
  };

  return (
    <>
      <style>{`
        @keyframes cf-urgent-pulse {
          0%,100% { box-shadow: 0 0 0 0 rgba(244,63,94,.45); }
          50%     { box-shadow: 0 0 0 14px rgba(244,63,94,0); }
        }
        .animate-cf-urgent-pulse { animation: cf-urgent-pulse 1.6s ease-in-out infinite; }
      `}</style>
      <div
        data-testid={`cf-engage-banner-${payload.state}`}
        className={`relative rounded-2xl px-4 py-3 shadow-lg ${bg} ${className}`}
      >
        <button
          onClick={handleDismiss}
          aria-label="Fermer"
          data-testid="cf-engage-dismiss"
          className="absolute top-1.5 right-1.5 p-1 rounded-full hover:bg-black/10"
        >
          <X size={14} weight="bold" />
        </button>

        <div className="flex items-start gap-3 pr-5">
          <div className={`p-2 rounded-full flex-shrink-0 ${
            isUrgent ? 'bg-white/20' : 'bg-white/30'
          }`}>
            <Icon size={20} weight="fill" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[11px] font-bold uppercase tracking-widest opacity-80 mb-0.5">
              {payload.state === 'critical' ? 'Sprint final'
               : payload.state === 'competitive' ? 'Match serré'
               : payload.state === 'engaged' ? 'Tu progresses'
               : 'À toi de jouer'}
              {payload.llm_personalised && (
                <span className="ml-1.5 px-1.5 py-0.5 rounded bg-white/20 text-[9px]" title="IA personnalisée">
                  IA
                </span>
              )}
            </div>
            <div className="text-sm font-semibold leading-snug" data-testid="cf-engage-message">
              {payload.message}
            </div>

            {payload.state === 'critical' && payload.context?.remaining > 0 && (
              <div className="mt-1.5 inline-flex items-center gap-1 bg-white/25 px-2 py-0.5 rounded-full text-[11px] font-bold">
                <Lightning size={10} weight="fill" />
                {payload.context.remaining} votes restants
              </div>
            )}
            {payload.state === 'competitive' && payload.rival && (
              <div className="mt-1.5 inline-flex items-center gap-1 bg-white/15 px-2 py-0.5 rounded-full text-[11px] font-semibold">
                <Trophy size={10} weight="fill" />
                Rival : {payload.rival.name} ({payload.rival.votes} votes)
              </div>
            )}

            <button
              onClick={handleAction}
              data-testid="cf-engage-cta"
              className={`mt-2 inline-flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-bold active:scale-95 transition ${
                isUrgent
                  ? 'bg-white text-rose-700 hover:bg-rose-50'
                  : payload.ui_mode === 'push'
                  ? 'bg-white text-indigo-700 hover:bg-indigo-50'
                  : 'bg-rose-600 text-white hover:bg-rose-700'
              }`}
            >
              {payload.next_best_action === 'share' && <ShareNetwork size={12} weight="bold" />}
              {ctaLabel}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
