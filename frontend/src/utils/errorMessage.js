/**
 * iter141bis — Defensive error message extraction.
 *
 * Goals :
 *   1. Never leak server internals (nginx versions, stack traces, raw HTML)
 *      to end-users — these are scary AND a vulnerability disclosure to
 *      malicious actors.
 *   2. Always return a single, user-friendly French string regardless of
 *      the upstream shape (FastAPI HTTPException / Pydantic 422 array /
 *      nginx HTML / network error / abort).
 *   3. Map HTTP 5xx + network errors to short reassuring messages so a
 *      transient blip doesn't surface as "<html>...502 Bad Gateway...nginx".
 *
 * Use everywhere instead of `e.response?.data?.detail`.
 */

// iter237 — Soft-fail copy. The previous wording ("Une nouvelle tentative
// est en cours…") looked alarming and confusing on the login page when the
// user hadn't done anything. We now use neutral, reassuring language.
const GENERIC_5XX = "Le service est occupé. Réessaie dans quelques secondes.";
const GENERIC_NETWORK = "Connexion instable. Vérifie ton réseau et réessaie.";
const GENERIC_TIMEOUT = "La requête a pris trop de temps. Réessaie.";
const GENERIC_429 = "Trop de tentatives. Patiente un moment puis réessaie.";

// Looks like an HTML payload (nginx error pages, Cloudflare interstitials,
// any upstream proxy's verbose response). We refuse to ever surface this.
function isHtmlLike(s) {
  if (typeof s !== "string") return false;
  const trimmed = s.trim().toLowerCase();
  return (
    trimmed.startsWith("<!doctype") ||
    trimmed.startsWith("<html") ||
    /<\/?(html|body|head|title|h1|center|pre)\b/i.test(trimmed)
  );
}

// Some backends send long stack traces / generic Internal Server Error
// strings as `data.detail`. Don't show these either — replace with a
// friendly message keyed by status code.
function looksLikeStackTrace(s) {
  if (typeof s !== "string") return false;
  return (
    s.length > 240 ||
    /traceback|exception|stack trace|error at|^\s*at\s+\w+/i.test(s) ||
    /file \"[^\"]+\", line \d+/i.test(s)
  );
}

function friendlyForStatus(status) {
  if (!status) return null;
  if (status === 0)   return GENERIC_NETWORK;
  if (status === 408) return GENERIC_TIMEOUT;
  if (status === 429) return GENERIC_429;
  if (status === 502 || status === 503 || status === 504) return GENERIC_5XX;
  if (status >= 500)  return GENERIC_5XX;
  return null;
}

export function extractErrorMessage(error, fallback = "Une erreur est survenue.") {
  if (!error) return fallback;

  // ─── Network / timeout / abort (no HTTP response at all) ───
  // axios sets `error.code` to ERR_NETWORK / ECONNABORTED, and
  // `error.response` is undefined.
  if (!error.response && (error.code || error.message)) {
    const code = error.code || "";
    if (/^ECONNABORTED$/i.test(code) || /timeout/i.test(error.message || "")) {
      return GENERIC_TIMEOUT;
    }
    if (/^ERR_NETWORK$/i.test(code) || /network/i.test(error.message || "")) {
      return GENERIC_NETWORK;
    }
  }

  const status = error.response?.status;
  const data = error.response?.data ?? error.data ?? error;

  // ─── Body is a string ───
  if (typeof data === "string") {
    if (isHtmlLike(data) || looksLikeStackTrace(data)) {
      return friendlyForStatus(status) || GENERIC_5XX;
    }
    // Final guard: status-based override beats verbose plain-text
    return friendlyForStatus(status) || data;
  }

  // ─── FastAPI HTTPException : { detail: string } ───
  if (typeof data?.detail === "string") {
    if (isHtmlLike(data.detail) || looksLikeStackTrace(data.detail)) {
      return friendlyForStatus(status) || GENERIC_5XX;
    }
    return data.detail;
  }

  // ─── FastAPI 422 : { detail: [{type, loc, msg, input}, ...] } ───
  if (Array.isArray(data?.detail)) {
    const parts = data.detail.map(d => {
      if (typeof d === "string") return d;
      const loc = Array.isArray(d?.loc) ? d.loc.filter(x => x !== "body").join(".") : "";
      const msg = d?.msg || d?.message || "invalide";
      return loc ? `${loc} : ${msg}` : msg;
    });
    return parts.join(" · ");
  }

  // ─── Bare array (sometimes returned without `detail`) ───
  if (Array.isArray(data)) {
    return data
      .map(d => (typeof d === "string" ? d : d?.msg || JSON.stringify(d)))
      .join(" · ");
  }

  // ─── Misc shapes ───
  if (typeof data?.message === "string" && !isHtmlLike(data.message) && !looksLikeStackTrace(data.message)) {
    return data.message;
  }
  if (typeof error?.message === "string" && !looksLikeStackTrace(error.message)) {
    // e.g., "Network Error" → already mapped above; otherwise let through
    const friendly = friendlyForStatus(status);
    if (friendly) return friendly;
    return error.message;
  }
  return friendlyForStatus(status) || fallback;
}

export default extractErrorMessage;
