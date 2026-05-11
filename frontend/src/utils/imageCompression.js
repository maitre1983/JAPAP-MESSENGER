/**
 * Compression universelle images — optimisé connexion faible (2G/3G Afrique)
 * Formats acceptés : JPEG, PNG, WebP, GIF, HEIC, HEIF, BMP, TIFF, AVIF
 * Cible : 300KB max (vs 1MB avant) pour upload rapide sur réseau lent
 */
import imageCompression from 'browser-image-compression';

// Tous les formats images acceptés
const IMAGE_RX = /^image\/(jpeg|jpg|png|webp|gif|heic|heif|bmp|tiff|tif|avif)$/i;

// Extensions de secours quand le navigateur ne renseigne pas le MIME type
const IMAGE_EXT_RX = /\.(jpg|jpeg|png|webp|gif|heic|heif|bmp|tiff|tif|avif)$/i;

// iter239e — Détection support WebP (true sur tous browsers modernes 2020+).
// HEIC est converti automatiquement par browser-image-compression vers le format
// cible — utiliser WebP réduit le poids 25-40% vs JPEG à qualité visuellement
// équivalente. Fallback JPEG en cas d'erreur d'encode (très rare).
function _supportsWebP() {
  if (typeof document === 'undefined') return false;
  try {
    const c = document.createElement('canvas');
    return c.toDataURL('image/webp').startsWith('data:image/webp');
  } catch (_) { return false; }
}
const _WEBP_OK = _supportsWebP();

// Options agressives pour connexion faible
const DEFAULT_OPTIONS = {
  maxSizeMB: 0.3,            // 300KB max — adapté 2G/3G africain
  maxWidthOrHeight: 1280,    // suffisant pour mobile, réduit le poids
  useWebWorker: true,
  initialQuality: _WEBP_OK ? 0.82 : 0.75, // WebP supporte une qualité plus haute pour le même poids
  fileType: _WEBP_OK ? 'image/webp' : 'image/jpeg',
};

const _OUT_EXT = _WEBP_OK ? '.webp' : '.jpg';
const _OUT_MIME = _WEBP_OK ? 'image/webp' : 'image/jpeg';

/**
 * Options selon la qualité de connexion détectée
 */
function getCompressionOptions() {
  try {
    const conn = navigator?.connection || navigator?.mozConnection || navigator?.webkitConnection;
    const type = conn?.effectiveType || '4g';
    if (type === '2g' || type === 'slow-2g') {
      return { ...DEFAULT_OPTIONS, maxSizeMB: 0.15, maxWidthOrHeight: 800, initialQuality: 0.65 };
    }
    if (type === '3g') {
      return { ...DEFAULT_OPTIONS, maxSizeMB: 0.25, maxWidthOrHeight: 1024, initialQuality: 0.70 };
    }
  } catch (_) {}
  return DEFAULT_OPTIONS;
}

/**
 * Compresser un fichier image quel que soit son format.
 * Les vidéos et autres types sont retournés sans modification.
 */
export async function compressFile(file, overrides = {}) {
  if (!file) return file;
  const mime = file.type || '';
  const name = file.name || '';
  const isImage = IMAGE_RX.test(mime) || IMAGE_EXT_RX.test(name);
  if (!isImage) return file;         // vidéos, PDF, etc. → pas touché
  if (file.size < 80 * 1024) return file;  // < 80KB déjà petit
  try {
    const options = { ...getCompressionOptions(), ...overrides };
    const compressed = await imageCompression(file, options);
    const outName = name.replace(/\.[^.]+$/, '') + _OUT_EXT;
    if (compressed instanceof File) return new File([compressed], outName, { type: _OUT_MIME });
    return new File([compressed], outName, { type: _OUT_MIME });
  } catch (err) {
    console.warn('[compressFile] fallback original:', err?.message || err);
    return file;
  }
}

/** Compresser un tableau de fichiers en parallèle. Ne lève jamais d'exception. */
export async function compressFiles(files, overrides = {}) {
  if (!files || !files.length) return [];
  return Promise.all(Array.from(files).map(f => compressFile(f, overrides)));
}

export default compressFiles;
