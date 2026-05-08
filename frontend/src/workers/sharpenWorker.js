/**
 * iter147 — Sharpen worker (Phase 1 perf opt for low-end Android).
 *
 * The sharpen 3x3 convolution kernel is O(w*h*9) on the main thread which
 * jank-blocks the UI on phones with ≤2 GB RAM. Offloading to a Worker
 * with `OffscreenCanvas` makes the bake feel instant — the main thread
 * stays free for animations and tap responsiveness.
 *
 * Protocol:
 *    main → worker:  { id, op:"sharpen", imageData, amount }
 *    worker → main:  { id, ok:true, imageData }   on success
 *    worker → main:  { id, ok:false, error }       on failure
 *
 * `imageData` is a transferable `ImageData` object. We serialise via
 * `postMessage(msg, [imageData.data.buffer])` so there's zero copy.
 */
/* eslint-disable no-restricted-globals */

self.onmessage = (e) => {
  const { id, op, imageData, amount } = e.data || {};
  try {
    if (op === "sharpen") {
      const out = sharpen(imageData, amount || 0);
      self.postMessage({ id, ok: true, imageData: out }, [out.data.buffer]);
      return;
    }
    self.postMessage({ id, ok: false, error: `unknown_op:${op}` });
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err && err.message || err) });
  }
};

function sharpen(imageData, amount) {
  const { width: w, height: h, data: src } = imageData;
  const out = new ImageData(w, h);
  const od = out.data;
  const k = [0, -1, 0, -1, 5, -1, 0, -1, 0];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      // Border pixels just copy through (kernel needs 1px halo).
      if (x === 0 || y === 0 || x === w - 1 || y === h - 1) {
        od[i] = src[i]; od[i + 1] = src[i + 1]; od[i + 2] = src[i + 2]; od[i + 3] = src[i + 3];
        continue;
      }
      for (let c = 0; c < 3; c++) {
        let v = 0;
        for (let ky = -1; ky <= 1; ky++) {
          for (let kx = -1; kx <= 1; kx++) {
            const nIdx = ((y + ky) * w + (x + kx)) * 4 + c;
            v += src[nIdx] * k[(ky + 1) * 3 + (kx + 1)];
          }
        }
        od[i + c] = Math.max(0, Math.min(255, src[i + c] * (1 - amount) + v * amount));
      }
      od[i + 3] = src[i + 3];
    }
  }
  return out;
}
