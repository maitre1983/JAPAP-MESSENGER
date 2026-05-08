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

// Options agressives pour connexion faible
const DEFAULT_OPTIONS = {
  maxSizeMB: 0.3,            // 300KB max — adapté 2G/3G africain
  maxWidthOrHeight: 1280,    // suffisant pour mobile, réduit le poids
  useWebWorker: true,
  initialQuality: 0.75,      // qualité réduite vs 0.82 pour plus de compression
  fileType: 'image/jpeg',    // toujours convertir en JPEG (format universel)
};

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
    const outName = name.replace(/\.[^.]+$/, '') + '.jpg';
    if (compressed instanceof File) return new File([compressed], outName, { type: 'image/jpeg' });
    return new File([compressed], outName, { type: 'image/jpeg' });
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
