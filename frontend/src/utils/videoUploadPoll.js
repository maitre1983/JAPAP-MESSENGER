/**
 * iter190b — Async video transcode job poller (frontend opt-in).
 *
 * Backend returns `{ status: 'processing', job_id, poll_url }` for any
 * video upload above ~50 MB. This helper drains the job to completion
 * with a friendly toast progression and resolves with the final URL.
 *
 * Usage:
 *   const u = await axios.post('/api/upload/', fd, { withCredentials: true });
 *   if (u.data?.status === 'processing') {
 *     const ready = await pollVideoJob(u.data.job_id, { onTick });
 *     mediaUrls.push(ready.url);
 *   } else {
 *     mediaUrls.push(u.data.url);
 *   }
 */
import axios from 'axios';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;
const POLL_INTERVAL_MS = 4000;
const MAX_POLLS = 90;            // 90 × 4s = 6 min ceiling

/**
 * Poll a video transcode job until it flips to `ready` or `failed`.
 * Returns `{ url, thumbnail_url, duration }` on success.
 * Throws `Error(message)` on failure or timeout.
 */
export async function pollVideoJob(jobId, { onTick, toastId } = {}) {
  if (!jobId) throw new Error('Missing job_id');
  let polls = 0;
  while (polls < MAX_POLLS) {
    polls += 1;
    let data;
    try {
      const r = await axios.get(`${API}/api/upload/video-job/${jobId}`,
        { withCredentials: true });
      data = r.data;
    } catch (e) {
      // Transient network errors — keep polling, give up after 3 in a row.
      if (polls > 3) throw new Error('Connexion perdue pendant le traitement vidéo');
      await sleep(POLL_INTERVAL_MS);
      continue;
    }
    if (typeof onTick === 'function') {
      try { onTick(data, polls); } catch (_) {}
    }
    if (data.status === 'ready' && data.url) {
      return data;
    }
    if (data.status === 'failed') {
      throw new Error(data.error || 'La vidéo n\'a pas pu être optimisée');
    }
    await sleep(POLL_INTERVAL_MS);
  }
  throw new Error('Délai d\'optimisation vidéo dépassé');
}

/**
 * Convenience wrapper that shows a sticky sonner toast while the job runs
 * and returns the resolved URL. Use this from a click handler when you
 * want zero ceremony.
 */
export async function pollVideoJobWithToast(jobId, label = 'Ta vidéo') {
  const tid = toast.loading('🎬 Optimisation en cours…', {
    description: `${label} sera prête dans quelques secondes.`,
    duration: Infinity,
  });
  try {
    const ready = await pollVideoJob(jobId, {
      onTick: (data, polls) => {
        // Soft progress hint — backend doesn't ship a real % yet, but we
        // can reassure the user the job is still alive.
        if (data.status === 'processing' && polls > 1) {
          toast.message('🎬 Encodage vidéo en cours…', {
            id: tid,
            description: 'Quelques secondes encore…',
          });
        }
      },
    });
    toast.success('🎬 Vidéo prête !', { id: tid, duration: 2500 });
    return ready;
  } catch (e) {
    toast.error(e.message || 'Échec optimisation vidéo', { id: tid, duration: 4000 });
    throw e;
  }
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}
