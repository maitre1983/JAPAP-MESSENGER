/**
 * uploadAvatarOrCover — iter92
 *
 * Uses the smart-image backend endpoint POST /api/upload/image?kind=profile|cover.
 * Server handles: EXIF strip · max-dims clamp · center-crop · WebP/JPEG encode
 * under size budget · thumbnail generation.
 *
 * The caller MUST still send a JPEG/PNG/WebP Blob under 10 MB.
 */
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

async function uploadSmartImage(blob, kind, filename) {
  const fd = new FormData();
  fd.append('file', blob, filename);
  const { data } = await axios.post(
    `${API}/api/upload/image?kind=${encodeURIComponent(kind)}`,
    fd,
    {
      withCredentials: true,
      headers: { 'Content-Type': 'multipart/form-data' },
    },
  );
  // Backend returns { main: {url,...}, thumb: {url,...}, source: {...} }
  return {
    url: data?.main?.url || '',
    thumbUrl: data?.thumb?.url || '',
    size: data?.main?.size || 0,
    mime: data?.main?.mime || 'image/webp',
    width: data?.main?.width,
    height: data?.main?.height,
  };
}

export async function uploadAvatar(blob) {
  const img = await uploadSmartImage(blob, 'profile', `avatar_${Date.now()}.webp`);
  const { data } = await axios.put(
    `${API}/api/users/profile`,
    { avatar: img.url, avatar_thumb: img.thumbUrl },
    { withCredentials: true },
  );
  return data;
}

export async function uploadCover(blob, positionY = 50) {
  const img = await uploadSmartImage(blob, 'cover', `cover_${Date.now()}.webp`);
  const { data } = await axios.put(
    `${API}/api/users/profile`,
    {
      cover_image: img.url,
      cover_image_mobile: img.thumbUrl,
      cover_position_y: positionY,
    },
    { withCredentials: true },
  );
  return data;
}
