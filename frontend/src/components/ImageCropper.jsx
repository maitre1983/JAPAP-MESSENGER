/**
 * ImageCropper — reusable full-screen modal wrapping react-easy-crop.
 *
 * Iter83.6 rebuild — social-grade UX (FB/IG/LinkedIn level):
 *   • Client-side compression (browser-image-compression) BEFORE the canvas
 *     step so a 20 MB iPhone HEIC/JPEG doesn't lock the main thread.
 *   • 1:1 circle crop for avatars, 3:1 rect crop for covers.
 *   • Drag + pinch zoom + range slider, rotation, pan — all via react-easy-crop.
 *   • Live preview of the cover positioning through the dedicated Y slider.
 *   • Mobile-first: camera vs gallery picker buttons; 44 px tap targets.
 *   • Progress toast + robust error feedback.
 *   • Output: WebP 85 quality, capped at 512 px (avatar) / 1600 px (cover).
 *
 * Accepts:
 *   - `open` boolean, shape `'round'|'rect3x1'|'rect16x9'`
 *   - `initialPositionY` (0-100) for rect modes
 *   - `onCrop(blob, { positionY })` async — caller uploads the blob.
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import Cropper from 'react-easy-crop';
import imageCompression from 'browser-image-compression';
import { X, Check, CameraPlus, Image as ImageIcon, MagnifyingGlassPlus, MagnifyingGlassMinus, ArrowsOutCardinal } from '@phosphor-icons/react';
import { toast } from 'sonner';

const SHAPES = {
  round:    { aspect: 1,      cropShape: 'round', label: 'Photo de profil',     help: 'Cadrez votre portrait dans le cercle.' },
  rect3x1:  { aspect: 3 / 1,  cropShape: 'rect',  label: 'Photo de couverture', help: 'Ajustez le cadrage et la position verticale.' },
  rect16x9: { aspect: 16 / 9, cropShape: 'rect',  label: 'Image',               help: 'Cadrez votre image.' },
};

const MAX_INPUT_BYTES = 10 * 1024 * 1024;           // reject > 10 MB
const ACCEPTED_MIME = ['image/jpeg', 'image/png', 'image/webp'];

/** Canvas-crop the source image (already decoded) into a WebP blob. */
async function canvasCrop(imageSrc, cropAreaPx, maxOutputPx, quality = 0.85) {
  const img = await new Promise((resolve, reject) => {
    const el = new window.Image();
    el.crossOrigin = 'anonymous';
    el.onload = () => resolve(el);
    el.onerror = reject;
    el.src = imageSrc;
  });
  const canvas = document.createElement('canvas');
  const scale = Math.min(1, maxOutputPx / Math.max(cropAreaPx.width, cropAreaPx.height));
  canvas.width = Math.round(cropAreaPx.width * scale);
  canvas.height = Math.round(cropAreaPx.height * scale);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, cropAreaPx.x, cropAreaPx.y, cropAreaPx.width, cropAreaPx.height,
                     0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), 'image/webp', quality));
}

export default function ImageCropper({
  open,
  shape = 'round',
  title,
  initialPositionY = 50,
  onCancel,
  onCrop,
  // iter147 — when provided, skip the internal file picker and start
  // directly on this file (used when the parent has already routed the
  // user through MediaFilterEditor for photo filters).
  initialFile = null,
}) {
  const cfg = SHAPES[shape] || SHAPES.round;
  const [imageSrc, setImageSrc] = useState(null);
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [croppedAreaPixels, setCroppedAreaPixels] = useState(null);
  const [positionY, setPositionY] = useState(initialPositionY);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(''); // human-readable status
  const fileInputRef = useRef(null);
  const cameraInputRef = useRef(null);

  useEffect(() => {
    if (!open) {
      setImageSrc(null);
      setZoom(1);
      setCrop({ x: 0, y: 0 });
      setCroppedAreaPixels(null);
      setPositionY(initialPositionY);
      setBusy(false);
      setProgress('');
    }
  }, [open, initialPositionY]);

  // Prevent body scroll while open (mobile UX).
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  const pickFile = (capture) => (capture ? cameraInputRef : fileInputRef).current?.click();

  const onFileChosen = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';                // allow re-selecting same file
    if (!file) return;
    await ingestFile(file);
  };

  // iter147 — extracted so we can also be fed by `initialFile` (parent has
  // already routed the user through MediaFilterEditor and hands us the
  // filtered File ready for cropping).
  const ingestFile = async (file) => {
    if (!ACCEPTED_MIME.includes(file.type)) {
      toast.error('Format non supporté. Utilisez JPG, PNG ou WEBP.');
      return;
    }
    if (file.size > MAX_INPUT_BYTES) {
      toast.error('Image trop volumineuse (max 10 Mo).');
      return;
    }

    setBusy(true);
    setProgress('Préparation de l\'image…');
    try {
      // Pre-compress huge files (e.g. 20 MB iPhone JPEG) to keep the canvas
      // step fast and peak memory low. This NEVER runs on the final blob —
      // the canvas crop recompresses at higher quality on the desired size.
      const shouldPrecompress = file.size > 2 * 1024 * 1024;
      const workingFile = shouldPrecompress
        ? await imageCompression(file, {
            maxSizeMB: 2,
            maxWidthOrHeight: 2400,
            useWebWorker: true,
            initialQuality: 0.92,
          })
        : file;

      const reader = new FileReader();
      reader.onload = () => {
        setImageSrc(reader.result);
        setBusy(false);
        setProgress('');
      };
      reader.onerror = () => {
        toast.error("Impossible de lire l'image.");
        setBusy(false);
        setProgress('');
      };
      reader.readAsDataURL(workingFile);
    } catch (err) {
      toast.error(err?.message || 'Échec de la préparation de l\'image.');
      setBusy(false);
      setProgress('');
    }
  };

  // iter147 — auto-load `initialFile` when the parent supplies it.
  useEffect(() => {
    if (open && initialFile && !imageSrc) {
      ingestFile(initialFile);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialFile]);

  const onCropComplete = useCallback((_, pixels) => setCroppedAreaPixels(pixels), []);

  const confirm = async () => {
    if (!imageSrc || !croppedAreaPixels) return;
    setBusy(true);
    setProgress('Traitement…');
    try {
      const maxOut = shape === 'round' ? 512 : 1600;
      let blob = await canvasCrop(imageSrc, croppedAreaPixels, maxOut);
      if (!blob) throw new Error('crop failed');

      // Defensive 2nd pass: if the final output is still > 800 KB, re-
      // compress to stay polite with the upload endpoint (Cloudflare
      // limits + mobile networks).
      if (blob.size > 800 * 1024) {
        setProgress('Compression…');
        const compressed = await imageCompression(
          new File([blob], 'crop.webp', { type: 'image/webp' }),
          { maxSizeMB: 0.8, maxWidthOrHeight: maxOut, useWebWorker: true, fileType: 'image/webp' },
        );
        blob = compressed;
      }

      setProgress('Envoi…');
      await onCrop?.(blob, { positionY });
    } catch (e) {
      toast.error(e?.message || "Échec du traitement de l'image");
    } finally {
      setBusy(false);
      setProgress('');
    }
  };

  if (!open) return null;

  const isRound = shape === 'round';
  const isWide = !isRound;

  return (
    <div
      className="fixed inset-0 z-[120] bg-black/95 flex flex-col jp-full-dynamic-height"
      style={{
        paddingTop: 'env(safe-area-inset-top, 0px)',
        paddingBottom: 'env(safe-area-inset-bottom, 0px)',
      }}
      data-testid="image-cropper-modal"
    >
      <header
        className="flex items-center justify-between p-3 text-white sticky top-0 z-10"
        style={{ minHeight: 56, background: 'rgba(0,0,0,0.85)' }}
      >
        <button onClick={onCancel} disabled={busy}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full hover:bg-white/10 transition-colors text-sm disabled:opacity-40"
          aria-label="Annuler" data-testid="cropper-cancel">
          <X size={18} /> Annuler
        </button>
        <h2 className="font-['Outfit'] font-bold text-sm">{title || cfg.label}</h2>
        <button onClick={confirm} disabled={!imageSrc || !croppedAreaPixels || busy}
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full transition-all text-sm font-bold disabled:opacity-40"
          style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)', color: '#fff' }}
          aria-label="Valider" data-testid="cropper-confirm">
          {busy
            ? <><span className="block w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" /> {progress || '…'}</>
            : <><Check size={16} /> Valider</>}
        </button>
      </header>

      <div className="flex-1 relative" data-testid="cropper-canvas-wrap">
        {imageSrc ? (
          <Cropper
            image={imageSrc}
            crop={crop}
            zoom={zoom}
            aspect={cfg.aspect}
            cropShape={cfg.cropShape}
            showGrid={!isRound}
            onCropChange={setCrop}
            onZoomChange={setZoom}
            onCropComplete={onCropComplete}
            objectFit={isRound ? 'contain' : 'horizontal-cover'}
            classes={{ containerClassName: 'cropper-container' }}
            style={{ containerStyle: { background: '#000' } }}
          />
        ) : (
          <div className="h-full flex flex-col items-center justify-center gap-6 px-6">
            <div className="w-20 h-20 rounded-full flex items-center justify-center"
              style={{ background: 'rgba(224,28,46,0.15)', border: '2px dashed rgba(255,255,255,0.3)' }}>
              {isRound ? <CameraPlus size={36} className="text-white/80" /> : <ImageIcon size={36} className="text-white/80" />}
            </div>
            <div className="text-center max-w-xs">
              <h3 className="text-white font-['Outfit'] font-bold text-base mb-1">
                {isRound ? 'Ajoutez votre photo de profil' : 'Ajoutez votre photo de couverture'}
              </h3>
              <p className="text-white/60 text-xs font-['Manrope']">
                {cfg.help}
              </p>
            </div>
            <div className="flex flex-col sm:flex-row gap-2.5 w-full max-w-xs">
              <button onClick={() => pickFile(true)} data-testid="cropper-open-camera"
                className="flex-1 inline-flex items-center justify-center gap-2 px-5 py-3.5 rounded-full text-white font-['Manrope'] font-semibold"
                style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}>
                <CameraPlus size={18} weight="fill" /> Caméra
              </button>
              <button onClick={() => pickFile(false)} data-testid="cropper-choose-file"
                className="flex-1 inline-flex items-center justify-center gap-2 px-5 py-3.5 rounded-full text-white font-['Manrope'] font-semibold"
                style={{ background: 'rgba(255,255,255,0.12)', border: '1px solid rgba(255,255,255,0.25)' }}>
                <ImageIcon size={18} weight="fill" /> Galerie
              </button>
            </div>
            <p className="text-white/40 text-[10px] font-['Manrope'] text-center">
              JPG · PNG · WEBP · max 10 Mo
            </p>
          </div>
        )}

        {/* Hidden inputs */}
        <input ref={fileInputRef} type="file" accept="image/jpeg,image/png,image/webp"
          onChange={onFileChosen} className="hidden" data-testid="cropper-file-input" />
        <input ref={cameraInputRef} type="file" accept="image/jpeg,image/png,image/webp"
          capture="environment" onChange={onFileChosen} className="hidden" data-testid="cropper-camera-input" />
      </div>

      {imageSrc && (
        <div
          className="p-3 sm:p-4 text-white space-y-2.5 bg-black/80 backdrop-blur-sm jp-safe-bottom"
          style={{ paddingBottom: 'max(12px, env(safe-area-inset-bottom, 0px))' }}
        >
          <div className="flex items-center gap-2.5">
            <MagnifyingGlassMinus size={16} className="flex-shrink-0 opacity-70" />
            <input type="range" min={1} max={4} step={0.02}
              value={zoom} onChange={(e) => setZoom(parseFloat(e.target.value))}
              className="flex-1 accent-red-500" data-testid="cropper-zoom" aria-label="Zoom" />
            <MagnifyingGlassPlus size={16} className="flex-shrink-0 opacity-70" />
          </div>
          {isWide && (
            <div className="flex items-center gap-2.5">
              <ArrowsOutCardinal size={14} className="flex-shrink-0 opacity-70" />
              <input type="range" min={0} max={100} step={1}
                value={positionY} onChange={(e) => setPositionY(parseInt(e.target.value, 10))}
                className="flex-1 accent-red-500" data-testid="cropper-position-y" aria-label="Position verticale" />
              <span className="text-[10px] opacity-60 font-mono w-8 text-right">{positionY}%</span>
            </div>
          )}
          {/* Change image button */}
          <div className="flex justify-center pt-1">
            <button onClick={() => setImageSrc(null)} disabled={busy}
              data-testid="cropper-change-image"
              className="text-xs opacity-70 hover:opacity-100 underline font-['Manrope']">
              Changer d'image
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
