/**
 * iter167 — Scholarship digest preference card on the Profile page.
 *
 * Lets African JAPAP users opt-out / set their preferred study level so
 * the weekly Monday digest only surfaces relevant scholarships first.
 *
 * Backend:
 *   GET /api/jobs/me/scholarship-digest
 *   PUT /api/jobs/me/scholarship-digest  { enabled, preferred_study_level }
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { GraduationCap } from "@phosphor-icons/react";

const API = process.env.REACT_APP_BACKEND_URL;

const LEVELS = [
  { id: "", label: "Toutes catégories" },
  { id: "bac", label: "Baccalauréat" },
  { id: "licence", label: "Licence" },
  { id: "master", label: "Master" },
  { id: "phd", label: "Doctorat / PhD" },
  { id: "postdoc", label: "Postdoctorat" },
  { id: "other", label: "Autres" },
];

export default function ScholarshipDigestPref() {
  const [enabled, setEnabled] = useState(true);
  const [level, setLevel] = useState("");
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    axios
      .get(`${API}/api/jobs/me/scholarship-digest`, { withCredentials: true })
      .then(({ data }) => {
        setEnabled(!!data.enabled);
        setLevel(data.preferred_study_level || "");
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  const persist = async (next) => {
    setSaving(true);
    try {
      const { data } = await axios.put(
        `${API}/api/jobs/me/scholarship-digest`,
        next,
        { withCredentials: true },
      );
      setEnabled(!!data.enabled);
      setLevel(data.preferred_study_level || "");
      if (next.enabled === true && !next.preferred_study_level) {
        toast.success("Tu recevras le digest hebdomadaire des bourses.");
      } else if (next.enabled === false) {
        toast.info("Digest désactivé. Tu peux le réactiver à tout moment.");
      } else if (next.preferred_study_level !== undefined) {
        toast.success("Niveau d'étude mis à jour.");
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "Erreur");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      data-testid="scholarship-digest-pref"
      className="p-4 rounded-xl"
      style={{
        background: "var(--jp-surface-secondary)",
        border: "1px solid var(--jp-border)",
      }}
    >
      <div className="flex items-start gap-3">
        <GraduationCap
          size={22}
          weight="duotone"
          style={{ color: "#F59E0B", flexShrink: 0, marginTop: 2 }}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-3 mb-1">
            <p
              className="text-sm font-semibold font-['Manrope']"
              style={{ color: "var(--jp-text)" }}
            >
              Bourses d'études — digest hebdo
            </p>
            <label className="inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={enabled}
                disabled={saving || !loaded}
                onChange={(e) => persist({ enabled: e.target.checked, preferred_study_level: level || null })}
                className="sr-only peer"
                data-testid="scholarship-digest-toggle"
              />
              <span
                className="w-9 h-5 bg-gray-300 rounded-full peer-checked:bg-amber-500 transition-colors relative"
                aria-hidden
              >
                <span
                  className="absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform"
                  style={{ left: enabled ? "18px" : "2px" }}
                />
              </span>
            </label>
          </div>
          <p
            className="text-[11px] font-['Manrope']"
            style={{ color: "var(--jp-text-muted)" }}
          >
            Reçois chaque lundi les nouvelles bourses publiées sur JAPAP. Réservé aux utilisateurs en Afrique.
          </p>

          {enabled && (
            <div className="mt-3">
              <label
                className="text-[11px] font-['Manrope'] mb-1 block"
                style={{ color: "var(--jp-text-muted)" }}
              >
                Mon niveau d'étude (mises en avant en priorité)
              </label>
              <select
                value={level}
                onChange={(e) =>
                  persist({ enabled: true, preferred_study_level: e.target.value || null })
                }
                disabled={saving || !loaded}
                data-testid="scholarship-digest-level"
                className="w-full text-sm rounded-lg px-2 py-1.5"
                style={{
                  background: "var(--jp-surface)",
                  color: "var(--jp-text)",
                  border: "1px solid var(--jp-border)",
                }}
              >
                {LEVELS.map((l) => (
                  <option key={l.id || "all"} value={l.id}>
                    {l.label}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
