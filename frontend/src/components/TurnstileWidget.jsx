import { forwardRef, useImperativeHandle, useRef } from 'react';
import { Turnstile } from '@marsidev/react-turnstile';

/**
 * Reusable Cloudflare Turnstile widget.
 *
 * Props:
 *   - onToken(token)  : called when the challenge is successfully solved
 *   - onExpire()      : called when the challenge token expires (300s TTL)
 *   - onError()       : called on network / widget errors
 *   - theme           : "light" | "dark" | "auto" (default "auto")
 *   - className       : optional wrapper class
 *
 * Ref API:
 *   ref.current.reset()   — force the widget to regenerate a fresh token
 *                           (call this after a failed form submission so
 *                           the user can retry — tokens are single-use).
 */
const TurnstileWidget = forwardRef(function TurnstileWidget(
  { onToken, onExpire, onError, theme = 'auto', className = '' },
  ref
) {
  const siteKey = process.env.REACT_APP_TURNSTILE_SITE_KEY;
  const innerRef = useRef(null);

  useImperativeHandle(ref, () => ({
    reset: () => {
      try {
        innerRef.current?.reset?.();
      } catch (_) {
        /* swallow — widget may not yet be mounted */
      }
    },
  }));

  // Graceful fallback when key not provisioned (dev bypass).
  // Backend also fails open in this case (see middleware/turnstile.py).
  if (!siteKey) {
    // Fire a placeholder token immediately so the form is submittable.
    // The backend will skip verification when its own secret is missing.
    setTimeout(() => onToken?.('dev-bypass'), 0);
    return null;
  }

  return (
    <div
      className={`turnstile-wrapper flex justify-center ${className}`}
      data-testid="turnstile-widget"
    >
      <Turnstile
        ref={innerRef}
        siteKey={siteKey}
        onSuccess={(token) => onToken?.(token)}
        onExpire={() => {
          onExpire?.();
          onToken?.(null);
        }}
        onError={() => {
          onError?.();
          onToken?.(null);
        }}
        options={{
          theme,
          size: 'flexible',
          retry: 'auto',
          refreshExpired: 'auto',
          // iter141bis — make Turnstile invisible for legitimate users.
          // The challenge runs in the background; the visible "Vérification…"
          // spinner only appears when Cloudflare actually needs to challenge
          // the request (rare for human traffic). Massively reduces
          // perceived auth latency.
          appearance: 'interaction-only',
        }}
      />
    </div>
  );
});

export default TurnstileWidget;
