/**
 * iter218 — Centralized upload / mutation error message helper.
 *
 * Replaces the legacy generic toasts ("Échec de la publication. Réessaie.",
 * "Erreur upload") with contextual, actionable messages so users
 * immediately understand WHY their action failed.
 *
 * Use across the entire app for any catch block on POST/PUT/PATCH:
 *
 *     import { uploadErrorMessage } from '@/utils/uploadErrorMessage';
 *     ...
 *     } catch (err) {
 *       toast.error(uploadErrorMessage(err));
 *     }
 */
export function uploadErrorMessage(err, fallback = 'Problème technique. Réessayez dans quelques instants.') {
  const status = err?.response?.status;
  const detail = (err?.response?.data?.detail || err?.message || '').toString();
  const lower = detail.toLowerCase();

  if (status === 401) return 'Vous devez être connecté pour effectuer cette action.';
  if (status === 403 && lower.includes('csrf'))
    return 'Session expirée. Recharge la page (⌘R / F5).';
  if (status === 403) return 'Action refusée. Permissions manquantes.';
  if (status === 413) return 'Fichier trop volumineux.';
  if (status === 415 || lower.includes('format') || lower.includes('non supporté'))
    return detail || 'Format non supporté. Utilisez mp4, webm, mov, jpg, png ou heic.';
  if (status === 429) return 'Trop de requêtes. Patiente quelques secondes.';
  if (status === 400 && (lower.includes('durée') || lower.includes('duration')))
    return detail; // already explicit ("Vidéo trop longue (62s). Maximum: 60s")
  if (status === 400 && detail) return detail;
  if (status === 404) return 'Ressource introuvable.';
  if (status === 422) return detail || 'Données invalides.';
  if (status >= 500) return 'Le serveur a un souci. Réessaie dans quelques secondes.';
  if (!err?.response) return 'Réseau indisponible. Vérifie ta connexion.';
  return fallback;
}

export default uploadErrorMessage;
