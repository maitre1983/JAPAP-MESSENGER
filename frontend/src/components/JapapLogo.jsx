/**
 * JAPAP Logo component
 * ====================
 * Single source of truth for the JAPAP brand mark. Uses the real logo file
 * served from /japap-logo.jpg (public/ folder).
 *
 * Props:
 *  - size: one of 'sm' | 'md' | 'lg' | 'xl' (default 'md')
 *  - className: pass-through for extra tailwind classes
 *  - white: if true, wraps the logo in a subtle white backdrop (useful on dark gradients)
 */
const SIZE_MAP = {
  sm: 'h-8',
  md: 'h-12',
  lg: 'h-16',
  xl: 'h-24',
};

export default function JapapLogo({ size = 'md', className = '', white = false }) {
  const h = SIZE_MAP[size] || SIZE_MAP.md;
  if (white) {
    return (
      <div className={`inline-flex items-center justify-center rounded-2xl bg-white px-3 py-2 shadow-sm ${className}`}>
        <img src="/japap-logo.jpg" alt="JAPAP" className={`${h} w-auto`} data-testid="japap-logo" />
      </div>
    );
  }
  return (
    <img src="/japap-logo.jpg" alt="JAPAP"
      className={`${h} w-auto ${className}`} data-testid="japap-logo" />
  );
}
