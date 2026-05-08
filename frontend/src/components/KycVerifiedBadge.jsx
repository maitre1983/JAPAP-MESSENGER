/**
 * KycVerifiedBadge — Trust signal pill (iter173)
 * ================================================
 * Compact "✅ Identité vérifiée" badge rendered next to a user's name on
 * profile, marketplace seller chips, post author chips and DMs.
 *
 * Always renders nothing when `verified` is falsy — drop-in safe.
 */
import { CheckCircle } from '@phosphor-icons/react';

const SIZES = {
  sm: { padX: '5px', padY: '1px', font: '10px', icon: 11, gap: '3px' },
  md: { padX: '7px', padY: '2px', font: '11px', icon: 13, gap: '4px' },
  lg: { padX: '9px', padY: '3px', font: '12px', icon: 15, gap: '5px' },
};

export default function KycVerifiedBadge({
  verified, size = 'md', label = 'Identité vérifiée', iconOnly = false,
  className = '',
}) {
  if (!verified) return null;
  const s = SIZES[size] || SIZES.md;
  return (
    <span
      title="Cet utilisateur a soumis une pièce d'identité vérifiée par l'équipe JAPAP"
      data-testid="kyc-verified-badge"
      className={`inline-flex items-center font-semibold ${className}`}
      style={{
        gap: s.gap,
        padding: iconOnly ? '2px' : `${s.padY} ${s.padX}`,
        fontSize: s.font,
        background: iconOnly ? 'transparent' : 'linear-gradient(135deg,#3B82F6,#22C55E)',
        color: iconOnly ? '#22C55E' : 'white',
        borderRadius: '9999px',
        whiteSpace: 'nowrap',
        lineHeight: 1.2,
      }}>
      <CheckCircle size={s.icon} weight="fill" />
      {!iconOnly && label}
    </span>
  );
}
