/**
 * iter150 — Profile Photo Gallery (mini-portfolio).
 *
 * Renders a grid of the profile owner's filtered photos aggregated from
 * posts + stories (`GET /api/users/{id}/photo-gallery`). Each thumbnail
 * shows a small badge in the corner with the chosen filter preset
 * label (Vintage, Drama, Mono, …) — visual proof that the filters
 * shipped in iter147 are being used in the wild.
 *
 * Empty state: hidden entirely. Profile pages without photos look
 * exactly like before.
 *
 * Privacy: backend already enforces follower-gating for private
 * accounts; we just render whatever the API returns.
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { PRESET_BY_ID } from "@/services/mediaFilters";

const API = process.env.REACT_APP_BACKEND_URL;

export default function PhotoGallery({ userId }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [isPrivate, setIsPrivate] = useState(false);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    setLoading(true);
    axios
      .get(`${API}/api/users/${userId}/photo-gallery`, { withCredentials: true })
      .then(({ data }) => {
        if (cancelled) return;
        setItems(data?.items || []);
        setIsPrivate(!!data?.private);
      })
      .catch(() => { if (!cancelled) setItems([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [userId]);

  if (loading) return null;
  // Hide the whole section when empty so non-photographer accounts
  // don't get a hollow placeholder.
  if (!items.length && !isPrivate) return null;

  return (
    <section className="mt-6" data-testid="profile-photo-gallery">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-['Outfit'] font-bold uppercase tracking-wider"
            style={{ color: "var(--jp-text)" }}>
          Galerie
        </h3>
        <span className="text-[11px] font-['Manrope']" style={{ color: "var(--jp-text-muted)" }}
              data-testid="profile-photo-gallery-count">
          {items.length} photo{items.length > 1 ? "s" : ""}
        </span>
      </div>

      {isPrivate ? (
        <p className="text-xs font-['Manrope'] py-6 text-center"
           style={{ color: "var(--jp-text-muted)" }}
           data-testid="profile-photo-gallery-private">
          🔒 Galerie privée — abonne-toi pour voir les photos.
        </p>
      ) : (
        <div className="grid grid-cols-3 gap-1.5" data-testid="profile-photo-gallery-grid">
          {items.map((it, i) => {
            const preset = PRESET_BY_ID[it.filter_preset];
            return (
              <a
                key={`${it.source_id}-${i}`}
                href={
                  it.source === "story"
                    ? `/feed?story=${encodeURIComponent(it.source_id)}`
                    : `/post/${encodeURIComponent(it.source_id)}`
                }
                data-testid={`profile-photo-${i}`}
                className="relative block aspect-square overflow-hidden rounded-md group"
                style={{ background: "rgba(0,0,0,0.06)" }}
              >
                <img
                  src={it.image_url}
                  alt=""
                  loading="lazy"
                  className="w-full h-full object-cover transition-transform duration-200 group-hover:scale-105"
                />
                {preset && preset.id !== "none" && (
                  <span
                    data-testid={`profile-photo-${i}-badge`}
                    className="absolute top-1 left-1 px-1.5 py-0.5 rounded-md text-[9px] font-['Manrope'] font-bold uppercase tracking-wide"
                    style={{
                      background: "rgba(0,0,0,0.55)",
                      color: "white",
                      backdropFilter: "blur(4px)",
                    }}
                  >
                    {preset.label}
                  </span>
                )}
                {it.source === "story" && (
                  <span
                    className="absolute bottom-1 right-1 text-[9px] font-['Manrope'] px-1.5 py-0.5 rounded-md"
                    style={{ background: "rgba(255,255,255,0.85)", color: "#0F056B" }}
                  >
                    Story
                  </span>
                )}
              </a>
            );
          })}
        </div>
      )}
    </section>
  );
}
