import { useEffect } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';

/**
 * Short-link landing for shared referral codes: /r/:code
 *
 * Behaviour:
 *  - Persists the code in localStorage so that a user who detours through
 *    the landing page or login flow doesn't lose their attribution.
 *  - Forwards UTM tracking params to /register so analytics can attribute
 *    the conversion to the right channel (whatsapp / telegram / x / sms / native).
 *  - Authenticated users are sent to /referral so they can apply the code
 *    via the existing apply-referral flow if not yet bound.
 */
export default function ReferralRedirectPage() {
  const { code } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const clean = (code || '').trim().toUpperCase().slice(0, 16);
    if (clean) {
      try {
        localStorage.setItem('japap_pending_ref', clean);
        localStorage.setItem('japap_pending_ref_at', String(Date.now()));
      } catch { /* private mode */ }
    }
    // Preserve any UTM-style query params from the original short-link visit.
    const params = new URLSearchParams(location.search);
    if (clean) params.set('ref', clean);
    // iter110 — Persist UTM in localStorage so they survive the OTP detour.
    try {
      const utmSource = params.get('utm_source');
      const utmMedium = params.get('utm_medium');
      const utmCampaign = params.get('utm_campaign');
      if (utmSource) localStorage.setItem('japap_pending_utm_source', utmSource.slice(0, 40));
      if (utmMedium) localStorage.setItem('japap_pending_utm_medium', utmMedium.slice(0, 40));
      if (utmCampaign) localStorage.setItem('japap_pending_utm_campaign', utmCampaign.slice(0, 80));
    } catch { /* private mode */ }
    const isAuthed = (() => {
      try {
        return Boolean(JSON.parse(localStorage.getItem('user') || 'null'));
      } catch { return false; }
    })();
    const dest = isAuthed ? `/referral?${params.toString()}` : `/register?${params.toString()}`;
    navigate(dest, { replace: true });
  }, [code, location.search, navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center" data-testid="referral-redirect">
      <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Redirection…</div>
    </div>
  );
}
