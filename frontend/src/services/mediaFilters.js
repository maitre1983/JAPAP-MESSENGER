/**
 * iter147 — JAPAP Media Filters (Phase 1).
 *
 * Single source of truth for all photo/video filter presets used across
 * the app: chat composer, feed composer, stories/reels, profile photo,
 * cover image. NO duplication — every JAPAP surface that wants filters
 * must consume `FILTER_PRESETS` and the helpers exported here.
 *
 * Phase 1 scope (current iteration):
 *   • CSS-based realtime preview filters (zero-cost on every device).
 *   • Canvas-baked output for IMAGES — the user's filter choice is burnt
 *     into the saved file so the receiver / server never has to re-apply.
 *   • For VIDEOS we keep the live filter as CSS during preview but ship
 *     the original bytes (server has no transcoder yet). We persist the
 *     selected preset name in the upload metadata so a later worker /
 *     ffmpeg.wasm pass can re-bake server-side without UX regression.
 *
 * Phase 2 (later iteration, opt-in only — must NOT slow Phase 1):
 *   • IA filters (cartoon, anime, oil painting, cinematic, beauty).
 *   • Routed through Gemini Nano Banana / fal.ai with a clear "loading"
 *     state so the user knows it's a heavier operation.
 *
 * Naming convention: preset ids are stable, lower-snake-case, and never
 * change once shipped (so existing posts/avatars referencing them keep
 * rendering correctly years from now).
 */

// Each preset is a fully-specified adjustment vector. Values default to
// "identity" (= no visual effect) so the preview can interpolate cleanly.
const IDENTITY = Object.freeze({
  brightness: 1,    // 0..2,    1 = unchanged
  contrast:   1,    // 0..2,    1 = unchanged
  saturate:   1,    // 0..2,    1 = unchanged
  hueRotate:  0,    // -180..180 deg
  blur:       0,    // 0..10 px
  sepia:      0,    // 0..1
  grayscale:  0,    // 0..1
  vignette:   0,    // 0..1     — drawn separately on canvas (radial gradient)
  sharpen:    0,    // 0..1     — convolution kernel applied at bake time
});

// iter150 — IA filter presets (Phase 2). These never apply a CSS preview
// (the bake is server-side via Nano Banana) — the preview falls back to
// the original image while the user waits. Each preset carries an `ai`
// flag + `style` id matching the backend `AI_FILTER_PROMPTS` mapping.
export const AI_FILTER_PRESETS = [
  { id: "ai_cartoon",      label: "Cartoon",  icon: "🎨", ai: true, style: "cartoon" },
  { id: "ai_anime",        label: "Anime",    icon: "🌸", ai: true, style: "anime" },
  { id: "ai_oil_painting", label: "Peinture", icon: "🖼️", ai: true, style: "oil_painting" },
  { id: "ai_cinematic",    label: "Cinéma",   icon: "🎬", ai: true, style: "cinematic" },
  { id: "ai_beauty",       label: "Beauté",   icon: "💎", ai: true, style: "beauty" },
];

export const FILTER_PRESETS = [
  { id: "none",     label: "Original", icon: "⊘",   adjustments: { ...IDENTITY } },
  { id: "mono",     label: "Mono",     icon: "◐",   adjustments: { ...IDENTITY, grayscale: 1, contrast: 1.15 } },
  { id: "sepia",    label: "Sépia",    icon: "🟫",  adjustments: { ...IDENTITY, sepia: 1, saturate: 1.1, contrast: 1.05 } },
  { id: "vivid",    label: "Vif",      icon: "✨",  adjustments: { ...IDENTITY, saturate: 1.5, contrast: 1.15, brightness: 1.05 } },
  { id: "warm",     label: "Chaud",    icon: "🔆",  adjustments: { ...IDENTITY, brightness: 1.05, saturate: 1.2, hueRotate: 8 } },
  { id: "cool",     label: "Froid",    icon: "🧊",  adjustments: { ...IDENTITY, brightness: 1.0,  saturate: 1.15, hueRotate: -12 } },
  { id: "vintage",  label: "Vintage",  icon: "📷",  adjustments: { ...IDENTITY, sepia: 0.35, contrast: 0.95, saturate: 0.8, vignette: 0.45 } },
  { id: "dramatic", label: "Drama",    icon: "🎭",  adjustments: { ...IDENTITY, contrast: 1.45, saturate: 0.85, brightness: 0.95, vignette: 0.3 } },
  { id: "soft",     label: "Doux",     icon: "🌸",  adjustments: { ...IDENTITY, brightness: 1.08, saturate: 0.95, blur: 0.6 } },
  { id: "sharp",    label: "Net",      icon: "🔪",  adjustments: { ...IDENTITY, sharpen: 0.6, contrast: 1.1 } },
];

export const PRESET_BY_ID = Object.fromEntries(
  [...FILTER_PRESETS, ...AI_FILTER_PRESETS].map(p => [p.id, p])
);

/**
 * Build a CSS `filter` string for the live preview. NOT used at canvas
 * bake time — see `bakeImageFilter` for that.
 */
export function cssFilterString(adj) {
  if (!adj) return "none";
  const parts = [];
  if (adj.brightness !== 1) parts.push(`brightness(${adj.brightness})`);
  if (adj.contrast !== 1)   parts.push(`contrast(${adj.contrast})`);
  if (adj.saturate !== 1)   parts.push(`saturate(${adj.saturate})`);
  if (adj.hueRotate)        parts.push(`hue-rotate(${adj.hueRotate}deg)`);
  if (adj.blur)             parts.push(`blur(${adj.blur}px)`);
  if (adj.sepia)            parts.push(`sepia(${adj.sepia})`);
  if (adj.grayscale)        parts.push(`grayscale(${adj.grayscale})`);
  return parts.length ? parts.join(" ") : "none";
}

/** Merge a preset's adjustments with user fine-tune overrides. */
export function mergeAdjustments(presetId, overrides = {}) {
  const base = PRESET_BY_ID[presetId]?.adjustments || IDENTITY;
  return { ...IDENTITY, ...base, ...overrides };
}

// ────────────────────────── Canvas bake (images) ──────────────────────────

function loadImageBitmap(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = (e) => { URL.revokeObjectURL(url); reject(e); };
    img.src = url;
  });
}

function applySharpen(ctx, w, h, amount) {
  if (!amount) return;
  // 3x3 sharpen kernel, blended with original by `amount` (0..1).
  const src = ctx.getImageData(0, 0, w, h);
  const out = ctx.createImageData(w, h);
  const k = [0, -1, 0, -1, 5, -1, 0, -1, 0];
  const sd = src.data; const od = out.data;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      for (let c = 0; c < 3; c++) {
        let v = 0;
        for (let ky = -1; ky <= 1; ky++) {
          for (let kx = -1; kx <= 1; kx++) {
            const idx = ((y + ky) * w + (x + kx)) * 4 + c;
            v += sd[idx] * k[(ky + 1) * 3 + (kx + 1)];
          }
        }
        const i = (y * w + x) * 4 + c;
        od[i] = Math.max(0, Math.min(255, sd[i] * (1 - amount) + v * amount));
      }
      const a = (y * w + x) * 4 + 3;
      od[a] = sd[a];
    }
  }
  ctx.putImageData(out, 0, 0);
}

// iter147 — Worker offload for the sharpen kernel (low-end Android perf).
// Lazily-instantiated singleton; falls back to main-thread `applySharpen`
// when Workers / OffscreenCanvas / Vite worker bundling are unavailable.
let _sharpenWorker = null;
let _sharpenWorkerSeq = 0;
function getSharpenWorker() {
  if (_sharpenWorker !== null) return _sharpenWorker; // may be `false` to mark unavailable
  if (typeof Worker === "undefined") { _sharpenWorker = false; return false; }
  try {
    // Webpack 5 / CRA 5 bundle worker via the `new URL(..., import.meta.url)` pattern.
    _sharpenWorker = new Worker(new URL("../workers/sharpenWorker.js", import.meta.url));
  } catch (_e) {
    _sharpenWorker = false;
  }
  return _sharpenWorker;
}

async function applySharpenAsync(ctx, w, h, amount) {
  if (!amount) return;
  const worker = getSharpenWorker();
  if (!worker) {
    // Fallback: synchronous main-thread kernel. Guarantees correctness on
    // browsers without Worker/OffscreenCanvas support.
    applySharpen(ctx, w, h, amount);
    return;
  }
  const src = ctx.getImageData(0, 0, w, h);
  const id = ++_sharpenWorkerSeq;
  await new Promise((resolve, reject) => {
    const onMessage = (e) => {
      if (!e.data || e.data.id !== id) return;
      worker.removeEventListener("message", onMessage);
      if (e.data.ok && e.data.imageData) {
        try { ctx.putImageData(e.data.imageData, 0, 0); resolve(); }
        catch (err) { reject(err); }
      } else {
        // On worker-side error, fall back to main-thread sharpen so we
        // never ship a broken bake to the user.
        try { applySharpen(ctx, w, h, amount); resolve(); }
        catch (err) { reject(err); }
      }
    };
    worker.addEventListener("message", onMessage);
    try {
      worker.postMessage({ id, op: "sharpen", imageData: src, amount }, [src.data.buffer]);
    } catch (e) {
      worker.removeEventListener("message", onMessage);
      applySharpen(ctx, w, h, amount);
      resolve();
    }
  });
}

function applyVignette(ctx, w, h, strength) {
  if (!strength) return;
  const grad = ctx.createRadialGradient(
    w / 2, h / 2, Math.min(w, h) * 0.35,
    w / 2, h / 2, Math.max(w, h) * 0.75,
  );
  grad.addColorStop(0, "rgba(0,0,0,0)");
  grad.addColorStop(1, `rgba(0,0,0,${Math.min(0.85, strength)})`);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
}

/**
 * Bake the given filter into the image and return a NEW File. Output
 * format is JPEG @ quality 0.92 — small enough for chat/feed, high
 * enough to keep filter detail. Original filename is preserved (with
 * `.jpg` suffix swap if needed) so server-side handlers don't choke.
 */
export async function bakeImageFilter(file, adjustments, opts = {}) {
  const maxSide = opts.maxSide || 2048;
  const quality = opts.quality ?? 0.92;
  const img = await loadImageBitmap(file);

  // Cap dimensions to keep bake fast on low-end Android.
  const ratio = Math.min(1, maxSide / Math.max(img.width, img.height));
  const w = Math.round(img.width * ratio);
  const h = Math.round(img.height * ratio);

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");

  // 1) Use CSS-equivalent filter at draw time — supported in all modern
  //    browsers including iOS 14+ Safari.
  ctx.filter = cssFilterString(adjustments);
  ctx.drawImage(img, 0, 0, w, h);

  // 2) Reset filter so subsequent operations (sharpen kernel + vignette)
  //    aren't double-applied.
  ctx.filter = "none";

  applySharpen(ctx, w, h, adjustments.sharpen);
  applyVignette(ctx, w, h, adjustments.vignette);

  const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", quality));
  const baseName = (file.name || "photo").replace(/\.(png|webp|heic|heif|jpg|jpeg)$/i, "");
  return new File([blob], `${baseName}.jpg`, { type: "image/jpeg", lastModified: Date.now() });
}

/**
 * Async variant of `bakeImageFilter` that offloads the sharpen kernel to
 * a Worker (`workers/sharpenWorker.js`) for non-blocking UX on low-end
 * Android. Falls back transparently to the synchronous bake if Workers
 * are unavailable. Use this in user-facing flows; the sync `bakeImageFilter`
 * remains exported for tests / programmatic batches.
 */
export async function bakeImageFilterAsync(file, adjustments, opts = {}) {
  const maxSide = opts.maxSide || 2048;
  const quality = opts.quality ?? 0.92;
  const img = await loadImageBitmap(file);

  const ratio = Math.min(1, maxSide / Math.max(img.width, img.height));
  const w = Math.round(img.width * ratio);
  const h = Math.round(img.height * ratio);

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.filter = cssFilterString(adjustments);
  ctx.drawImage(img, 0, 0, w, h);
  ctx.filter = "none";

  await applySharpenAsync(ctx, w, h, adjustments.sharpen);
  applyVignette(ctx, w, h, adjustments.vignette);

  const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", quality));
  const baseName = (file.name || "photo").replace(/\.(png|webp|heic|heif|jpg|jpeg)$/i, "");
  return new File([blob], `${baseName}.jpg`, { type: "image/jpeg", lastModified: Date.now() });
}

/**
 * For videos we don't have a client-side transcoder yet. Phase 1 keeps
 * the original bytes and stores the chosen preset id alongside the file
 * so the server / a future worker can re-bake. We still return a "tagged"
 * File object whose name encodes the preset for downstream visibility.
 */
export function tagVideoWithPreset(file, presetId) {
  if (!file || !presetId || presetId === "none") return file;
  const safe = String(presetId).replace(/[^a-z0-9_-]/gi, "");
  const dot = file.name?.lastIndexOf(".") ?? -1;
  const base = dot > 0 ? file.name.slice(0, dot) : (file.name || "video");
  const ext = dot > 0 ? file.name.slice(dot) : ".mp4";
  return new File([file], `${base}__filter-${safe}${ext}`, {
    type: file.type, lastModified: Date.now(),
  });
}

/**
 * iter150 — IA filter Phase 2. Posts the file to /api/media/ai-filter
 * with the chosen style and returns the resulting JPEG File. Throws
 * a friendly French error on rate-limit / generation failure so the
 * UI layer can surface it via toast/banner.
 *
 * Usage:
 *   const filtered = await applyAiFilter(originalFile, "cartoon");
 *   // filtered is a regular File ready for upload + preview
 *
 * Quota headers in the response let the UI update its counter without
 * a separate /quota round-trip.
 */
export async function applyAiFilter(file, style, opts = {}) {
  const apiBase = opts.apiBase || (typeof process !== "undefined" && process.env?.REACT_APP_BACKEND_URL) || "";
  const fd = new FormData();
  fd.append("style", style);
  fd.append("image", file, file.name || "photo.jpg");

  const resp = await fetch(`${apiBase}/api/media/ai-filter`, {
    method: "POST",
    credentials: "include",
    body: fd,
  });

  if (!resp.ok) {
    let message = "La génération IA a échoué. Réessaie.";
    let code = "AI_GENERATION_FAILED";
    try {
      const data = await resp.json();
      const detail = data?.detail;
      if (typeof detail === "object" && detail?.message) {
        message = detail.message;
        code = detail.code || code;
      } else if (typeof detail === "string") {
        message = detail;
      }
    } catch (_) { /* keep defaults */ }
    const err = new Error(message);
    err.code = code;
    err.status = resp.status;
    throw err;
  }

  const blob = await resp.blob();
  const used = parseInt(resp.headers.get("X-AI-Filter-Used") || "0", 10);
  const cap = parseInt(resp.headers.get("X-AI-Filter-Cap") || "0", 10);
  const baseName = (file.name || "photo").replace(/\.(png|webp|heic|heif|jpg|jpeg)$/i, "");
  const out = new File([blob], `${baseName}_${style}.jpg`, {
    type: "image/jpeg", lastModified: Date.now(),
  });
  out.aiQuota = { used, cap };
  return out;
}
