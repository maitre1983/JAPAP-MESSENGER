/**
 * RideReviewModal — Phase A (Driver Rating & Review).
 *
 * Auto-popped right after a ride flips to `completed` to capture the rider's
 * 1-5 stars + optional comment. Submitting calls
 *   POST /api/transport/{ride_id}/review
 * and updates the driver's rolling average. The "Plus tard" button writes a
 * skipped flag in localStorage so the modal does not nag on every reload —
 * the rider can always come back from the ride history later (P2).
 */
import { useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Star, X } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

export default function RideReviewModal({ ride, onSubmitted, onSkip }) {
  const { t } = useTranslation();
  const [hover, setHover] = useState(0);
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);

  if (!ride) return null;

  const submit = async () => {
    if (!rating) {
      toast.error('Choisissez une note de 1 à 5 étoiles.');
      return;
    }
    setBusy(true);
    try {
      await axios.post(
        `${API}/api/transport/${ride.ride_id}/review`,
        { rating, comment },
        { withCredentials: true },
      );
      toast.success('Merci pour votre avis !');
      onSubmitted?.();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Impossible d\'envoyer l\'avis');
    } finally {
      setBusy(false);
    }
  };

  const skip = () => {
    try {
      localStorage.setItem(`ride_review_skipped:${ride.ride_id}`, String(Date.now()));
    } catch { /* localStorage unavailable */ }
    onSkip?.();
  };

  const driverName = ride.driver?.name || ride.driver?.first_name || 'votre chauffeur';
  const stars = [1, 2, 3, 4, 5];

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center p-0 sm:p-4"
      style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(6px)' }}
      data-testid="ride-review-modal"
    >
      <div
        className="w-full sm:max-w-md rounded-t-3xl sm:rounded-3xl overflow-hidden flex flex-col"
        style={{ background: 'var(--jp-surface)' }}
      >
        <header className="px-5 py-4 flex items-start justify-between"
                style={{ borderBottom: '1px solid var(--jp-border)' }}>
          <div>
            <h2 className="font-['Outfit'] font-extrabold text-base">Notez votre course</h2>
            <p className="text-xs opacity-70 mt-0.5">
              Comment s'est passé votre trajet avec {driverName} ?
            </p>
          </div>
          <button onClick={skip} className="p-1.5 rounded-full hover:bg-white/10"
                  data-testid="ride-review-close">
            <X size={16} />
          </button>
        </header>

        <div className="px-5 py-6 space-y-5">
          {/* Stars */}
          <div className="flex justify-center gap-1.5"
               onMouseLeave={() => setHover(0)}
               data-testid="ride-review-stars">
            {stars.map((s) => {
              const filled = (hover || rating) >= s;
              return (
                <button
                  key={s}
                  type="button"
                  onMouseEnter={() => setHover(s)}
                  onClick={() => setRating(s)}
                  className="transition-transform hover:scale-110 p-1"
                  data-testid={`ride-review-star-${s}`}
                  aria-label={`${s} étoile${s > 1 ? 's' : ''}`}
                >
                  <Star
                    size={40}
                    weight={filled ? 'fill' : 'regular'}
                    style={{ color: filled ? '#F59E0B' : 'var(--jp-text-muted)' }}
                  />
                </button>
              );
            })}
          </div>
          <p className="text-center text-xs opacity-60">
            {rating === 0 && 'Touchez une étoile'}
            {rating === 1 && 'Très décevant'}
            {rating === 2 && 'Décevant'}
            {rating === 3 && 'Correct'}
            {rating === 4 && 'Bonne course'}
            {rating === 5 && 'Excellente course !'}
          </p>

          {/* Comment */}
          <div>
            <label className="text-[10px] uppercase font-bold opacity-60 mb-1.5 block">
              Commentaire (optionnel)
            </label>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              maxLength={500}
              rows={3}
              placeholder={t('ride_review_modal.conduite_proprete_ponctualite')}
              className="jp-input text-sm w-full"
              data-testid="ride-review-comment"
            />
            <div className="text-[10px] opacity-50 text-right mt-1">{comment.length}/500</div>
          </div>
        </div>

        <footer className="px-5 py-3 grid grid-cols-2 gap-2"
                style={{ borderTop: '1px solid var(--jp-border)' }}>
          <button
            onClick={skip}
            disabled={busy}
            className="jp-btn jp-btn-ghost text-xs font-bold"
            data-testid="ride-review-skip"
          >
            Plus tard
          </button>
          <button
            onClick={submit}
            disabled={busy || rating === 0}
            className="jp-btn jp-btn-primary text-xs font-bold"
            data-testid="ride-review-submit"
          >
            Envoyer
          </button>
        </footer>
      </div>
    </div>
  );
}
