/**
 * iter147 — JAPAP MediaFilterEditor.
 *
 * SHARED component used everywhere photo/video filters are needed:
 *   • Feed composer
 *   • Chat attachment picker
 *   • Stories / Reels capture
 *   • Profile photo + cover image picker
 *
 * Modes:
 *   • mode="upload"   — user picks file(s) from gallery first, then tunes filter
 *   • mode="capture"  — opens device camera (getUserMedia) with realtime preview
 *
 * Output: array of File objects (filter baked-in for images, original
 * bytes + tagged filename for videos in Phase 1).
 *
 * Props:
 *   open          : boolean
 *   onClose       : ()=>void
 *   files         : File[] (optional — pre-loaded gallery selection)
 *   mode          : "upload" | "capture"  (default: "upload")
 *   accept        : string                 (default: "image/*,video/*")
 *   multiple      : boolean                (default: false in capture, true in upload)
 *   onApply       : (files: File[], presetId: string) => void
 *
 * data-testid:
 *   media-filter-editor                — root
 *   media-filter-preview               — preview surface
 *   media-filter-preset-{id}           — preset chip
 *   media-filter-apply                 — apply button
 *   media-filter-cancel                — cancel button
 *   media-filter-camera-shutter        — camera capture button
 *   media-filter-camera-flip           — front/back toggle
 *   media-filter-fine-{name}           — fine-tune sliders
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { useTranslation } from 'react-i18next';
import {
  FILTER_PRESETS,
  cssFilterString,
  mergeAdjustments,
  bakeImageFilterAsync,
  tagVideoWithPreset,
} from "@/services/mediaFilters";

const FINE_TUNES = [
  { key: "brightness", label: "Luminosité", min: 0.5, max: 1.6, step: 0.01 },
  { key: "contrast",   label: "Contraste",  min: 0.5, max: 1.6, step: 0.01 },
  { key: "saturate",   label: "Saturation", min: 0,   max: 2.0, step: 0.01 },
];

export default function MediaFilterEditor({
  open,
  onClose,
  files: initialFiles,
  mode = "upload",
  accept = "image/*,video/*",
  multiple = mode === "upload",
  onApply,
}) {
  const { t } = useTranslation();
  const [files, setFiles] = useState(initialFiles || []);
  const [activeIdx, setActiveIdx] = useState(0);
  // perFileChoice[i] = { presetId, overrides: { brightness?, contrast?, saturate? } }
  const [perFileChoice, setPerFileChoice] = useState({});
  const [busy, setBusy] = useState(false);

  // Camera mode state
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraFacing, setCameraFacing] = useState("user"); // "user" | "environment"
  const [cameraError, setCameraError] = useState("");
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  // iter215 — IA filter state removed along with the UI strip.
  // The AI_FILTER_PRESETS / applyAiFilter pipeline still lives in
  // mediaFilters.js for a future re-introduction in a dedicated bottom
  // sheet; for now we keep aiResults so external code that may have
  // populated it doesn't crash. Loading / error state are gone since
  // there is no entry point left.
  const [aiResults] = useState({}); // { [fileIdx]: File } — read-only now

  // Reset selection when modal opens with new files.
  useEffect(() => {
    if (!open) return;
    if (initialFiles && initialFiles.length) {
      setFiles(initialFiles);
      setActiveIdx(0);
      setPerFileChoice({});
    }
  }, [open, initialFiles]);

  // ─── Camera lifecycle ──────────────────────────────────────────────
  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setCameraReady(false);
  }, []);

  const startCamera = useCallback(async (facing) => {
    setCameraError("");
    stopCamera();
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraError("Caméra non supportée sur ce navigateur.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: facing, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => {});
      }
      setCameraReady(true);
    } catch (e) {
      setCameraError(
        e?.name === "NotAllowedError"
          ? "Autorise l'accès à la caméra dans les paramètres du navigateur."
          : "Impossible d'ouvrir la caméra. Réessaie.",
      );
    }
  }, [stopCamera]);

  useEffect(() => {
    if (!open || mode !== "capture") return;
    startCamera(cameraFacing);
    return stopCamera;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, mode, cameraFacing]);

  // ⚠ All hooks must run BEFORE any conditional return — keep the
  //   useMemoFileUrl call here even when `open===false`. The hook
  //   handles a null `activeFile` gracefully.
  const activeFile = files[activeIdx];
  const previewUrl = useMemoFileUrl(activeFile);
  // iter147 — stable thumbstrip URLs cleaned up on `files` change to
  // avoid object-URL leaks across re-renders.
  const thumbUrls = useFileListUrls(files);

  if (!open) return null;

  const choice = perFileChoice[activeIdx] || { presetId: "none", overrides: {} };
  const adjustments = mergeAdjustments(choice.presetId, choice.overrides);
  const css = cssFilterString(adjustments);
  const isVideo = activeFile?.type?.startsWith("video/");

  function setActiveChoice(next) {
    setPerFileChoice((prev) => ({
      ...prev,
      [activeIdx]: { ...choice, ...next, overrides: { ...choice.overrides, ...(next.overrides || {}) } },
    }));
  }

  function selectPreset(presetId) {
    setPerFileChoice((prev) => ({
      ...prev,
      [activeIdx]: { presetId, overrides: {} },
    }));
    setAiError("");
  }

  // iter150 — apply a Nano Banana IA filter on the active file. Replaces
  // the file in-place with the AI-generated result so the rest of the
  // flow (preview + apply button) treats it like any other filtered file.
  // ─── Camera shutter ────────────────────────────────────────────────
  async function captureFromCamera() {
    if (!videoRef.current || !cameraReady) return;
    const video = videoRef.current;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 720;
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.92));
    const f = new File([blob], `japap_capture_${Date.now()}.jpg`, { type: "image/jpeg" });
    setFiles([f]);
    setActiveIdx(0);
    setPerFileChoice({});
    stopCamera(); // switch to filter-tuning view
  }

  // ─── Apply ─────────────────────────────────────────────────────────
  async function handleApply() {
    if (!files.length || busy) return;
    setBusy(true);
    try {
      const out = [];
      for (let i = 0; i < files.length; i++) {
        const f = files[i];
        const c = perFileChoice[i] || { presetId: "none", overrides: {} };
        const adj = mergeAdjustments(c.presetId, c.overrides);
        if (f.type?.startsWith("image/")) {
          out.push(await bakeImageFilterAsync(f, adj));
        } else {
          out.push(tagVideoWithPreset(f, c.presetId));
        }
      }
      onApply?.(out, perFileChoice[0]?.presetId || "none");
      onClose?.();
    } finally {
      setBusy(false);
    }
  }

  // Pre-capture screen (no file yet, mode=capture)
  const showCameraView = mode === "capture" && files.length === 0;

  return (
    <div
      data-testid="media-filter-editor"
      className="fixed inset-0 z-[10000] flex flex-col bg-black/95 overflow-hidden"
      role="dialog"
      aria-modal="true"
      style={{
        height: '100dvh',           // dvh = dynamic viewport height, iOS Safari safe
        paddingTop: 'env(safe-area-inset-top, 0px)',
        paddingBottom: 'env(safe-area-inset-bottom, 0px)',
      }}
    >
      {/* ── Header (iter210 — sticky with safe-area guard) ──────── */}
      <div
        className="flex items-center justify-between px-4 py-3 text-white flex-shrink-0 sticky top-0 z-10"
        style={{
          minHeight: 56,
          background: 'rgba(0,0,0,0.85)',
          /* Reinforce: ensure the Apply button always clears the
             Dynamic Island / notch. Parent <div> already has
             paddingTop env(safe-area-inset-top) but we also lift the
             header's own top padding so it's never tucked under the
             status bar even if paddingTop computes to 0 (desktop). */
        }}
      >
        <button
          type="button"
          onClick={() => { stopCamera(); onClose?.(); }}
          data-testid="media-filter-cancel"
          className="px-3 py-1.5 rounded-full text-sm font-['Manrope']"
          style={{ background: "rgba(255,255,255,0.12)" }}
        >
          Annuler
        </button>
        <span className="text-sm font-['Manrope'] font-semibold">
          {showCameraView ? "Photo" : isVideo ? "Vidéo" : "Image"}
        </span>
        <button
          type="button"
          onClick={handleApply}
          disabled={busy || files.length === 0}
          data-testid="media-filter-apply"
          className="px-4 py-1.5 rounded-full text-sm font-['Manrope'] font-bold text-white disabled:opacity-40"
          style={{ background: "#22c55e" }}
        >
          {busy ? "Application…" : showCameraView ? "—" : "Appliquer"}
        </button>
      </div>

      {/* ── Preview / Camera ────────────────────────────────────── */}
      <div className="flex-1 min-h-0 flex items-center justify-center overflow-hidden relative">
        {showCameraView ? (
          <>
            {cameraError ? (
              <div className="text-white/80 text-sm font-['Manrope'] text-center px-6" data-testid="media-filter-camera-error">
                {cameraError}
              </div>
            ) : (
              <>
                <video
                  ref={videoRef}
                  data-testid="media-filter-camera-preview"
                  playsInline
                  muted
                  className="max-h-full max-w-full object-contain"
                  style={{ filter: css, transform: cameraFacing === "user" ? "scaleX(-1)" : "none" }}
                />
                <div
                  className="absolute left-1/2 -translate-x-1/2 flex items-center gap-6"
                  style={{ bottom: 'calc(24px + env(safe-area-inset-bottom, 0px))' }}
                  data-testid="media-filter-camera-controls"
                >
                  <button
                    type="button"
                    onClick={() => setCameraFacing((f) => (f === "user" ? "environment" : "user"))}
                    data-testid="media-filter-camera-flip"
                    className="w-11 h-11 rounded-full text-white"
                    style={{ background: "rgba(255,255,255,0.18)" }}
                    aria-label={t('media_filter_editor.camera_avant_arriere')}
                  >
                    ⇅
                  </button>
                  <button
                    type="button"
                    onClick={captureFromCamera}
                    data-testid="media-filter-camera-shutter"
                    className="w-16 h-16 rounded-full border-4 border-white"
                    style={{ background: "rgba(255,255,255,0.95)" }}
                    aria-label={t('media_filter_editor.prendre_la_photo')}
                  />
                </div>
              </>
            )}
          </>
        ) : activeFile ? (
          isVideo ? (
            <video
              key={previewUrl}
              data-testid="media-filter-preview"
              src={previewUrl}
              controls
              playsInline
              className="max-h-full max-w-full object-contain"
              style={{ filter: css }}
            />
          ) : (
            <img
              data-testid="media-filter-preview"
              src={previewUrl}
              alt=""
              className="max-h-full max-w-full object-contain select-none"
              style={{ filter: css }}
              draggable={false}
            />
          )
        ) : (
          <FileFallbackInput
            accept={accept}
            multiple={multiple}
            onPick={(picked) => {
              setFiles(picked);
              setActiveIdx(0);
              setPerFileChoice({});
            }}
          />
        )}

      </div>

      {/* ── Multi-file thumbstrip (only if >1 file) ─────────────── */}
      {files.length > 1 && (
        <div className="flex gap-2 px-4 py-2 overflow-x-auto" data-testid="media-filter-strip">
          {files.map((f, i) => {
            const u = thumbUrls[i];
            return (
              <button
                key={i}
                onClick={() => setActiveIdx(i)}
                data-testid={`media-filter-thumb-${i}`}
                className={`relative flex-shrink-0 w-14 h-14 rounded-lg overflow-hidden border-2 ${i === activeIdx ? "border-emerald-400" : "border-transparent"}`}
              >
                {f.type.startsWith("image/") ? (
                  <img src={u} alt="" className="w-full h-full object-cover" />
                ) : (
                  <span className="w-full h-full flex items-center justify-center text-white/70 text-xs">VID</span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* ── Filter presets ─────────────────────────────────────── */}
      {!showCameraView && activeFile && (
        <div className="bg-black/80 border-t border-white/10 flex-shrink-0 overflow-y-auto"
          style={{ maxHeight: '45vh' }}>
          <div className="flex gap-2 px-4 py-3 overflow-x-auto" data-testid="media-filter-presets">
            {FILTER_PRESETS.map((p) => (
              <button
                key={p.id}
                onClick={() => selectPreset(p.id)}
                data-testid={`media-filter-preset-${p.id}`}
                className={`flex-shrink-0 flex flex-col items-center gap-1 px-2 py-1 rounded-xl transition-all ${choice.presetId === p.id ? "bg-emerald-500/25 ring-1 ring-emerald-400" : "bg-white/5"}`}
              >
                <div
                  className="w-12 h-12 rounded-lg overflow-hidden bg-white/10"
                  style={{ filter: cssFilterString(p.adjustments) }}
                >
                  {!isVideo && previewUrl && (
                    <img src={previewUrl} alt="" className="w-full h-full object-cover" />
                  )}
                </div>
                <span className="text-[10px] font-['Manrope'] text-white/85">{p.label}</span>
              </button>
            ))}
          </div>

          {/* iter215 — IA filters section removed for UX reasons:
              the 5 buttons (Cartoon/Anime/Peinture/Cinéma/Beauté)
              pushed the "Appliquer" button below the visible viewport
              on iPhone 13 Pro Max in portrait mode. The IA filter
              backend route /api/media/ai-filter and AI_FILTER_PRESETS
              remain available for future re-introduction in a
              dedicated bottom sheet. */}

          {/* Fine tune sliders */}
          <div className="px-4 py-3 space-y-2 border-t border-white/10">
            {FINE_TUNES.map((ft) => (
              <div key={ft.key} className="flex items-center gap-3">
                <span className="text-[11px] text-white/70 font-['Manrope'] w-20">{ft.label}</span>
                <input
                  type="range"
                  min={ft.min}
                  max={ft.max}
                  step={ft.step}
                  value={adjustments[ft.key]}
                  onChange={(e) =>
                    setActiveChoice({ overrides: { [ft.key]: parseFloat(e.target.value) } })
                  }
                  data-testid={`media-filter-fine-${ft.key}`}
                  className="flex-1"
                />
                <span className="text-[10px] tabular-nums text-white/60 w-10 text-right">
                  {Number(adjustments[ft.key]).toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────
function useMemoFileUrl(file) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!file) { setUrl(null); return; }
    const u = URL.createObjectURL(file);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [file]);
  return url;
}

// iter147 — stable URLs for the multi-file thumbstrip. Object URLs are
// revoked when the file list changes (or the modal unmounts) so we never
// accumulate dead references.
function useFileListUrls(files) {
  const [urls, setUrls] = useState([]);
  useEffect(() => {
    const created = (files || []).map((f) => URL.createObjectURL(f));
    setUrls(created);
    return () => created.forEach((u) => URL.revokeObjectURL(u));
  }, [files]);
  return urls;
}

function FileFallbackInput({ accept, multiple, onPick }) {
  const { t } = useTranslation();
  const ref = useRef(null);
  return (
    <div className="text-center text-white/70 text-sm font-['Manrope'] flex flex-col items-center gap-3">
      <p>{t('media_filter_editor.selectionne_un_fichier_pour_appliqu')}</p>
      <button
        type="button"
        onClick={() => ref.current?.click()}
        data-testid="media-filter-pick-file"
        className="px-4 py-2 rounded-full text-white font-bold"
        style={{ background: "#0F056B" }}
      >
        Choisir
      </button>
      <input
        ref={ref}
        type="file"
        accept={accept}
        multiple={multiple}
        className="hidden"
        onChange={(e) => {
          const arr = Array.from(e.target.files || []).slice(0, 10);
          if (arr.length) onPick(arr);
        }}
      />
    </div>
  );
}
