"""
JAPAP — WiFi Hotspot Share Card generator (iter141nineJ)

Renders a 1080×1920 vertical PNG suitable for WhatsApp Status / Instagram
Stories / TikTok of any country. Layout:

    ┌──────────────────────┐
    │ JAPAP WiFi           │  ← top brand bar
    │                      │
    │ 🌐 <Carrier slug>    │  ← isp/carrier hint (auto-detected)
    │ <Alias in bold>      │
    │ <Address line>       │
    │                      │
    │     [QR CODE 540×540]│  ← center scannable QR → /connect/h/<id>
    │                      │
    │ Scanne pour           │
    │ te connecter          │
    │                      │
    │ Powered by JAPAP     │  ← watermark + japap.app
    └──────────────────────┘

Design choices:
- All copy is country-neutral. The carrier slug surfaces whatever was
  detected (Vodacom, Safaricom, Free, Orange, AT&T, T-Mobile, …) so the
  card feels native to its market without any hardcoding.
- Liberation Sans is bundled with debian-slim → always available.
- Pillow + qrcode are already in requirements.txt (used by /og + /qr).
"""
from __future__ import annotations

import io
from typing import Optional

import qrcode
from PIL import Image, ImageDraw, ImageFont

# Liberation Sans is shipped with debian-slim base (fonts-liberation pkg).
_FONT_REGULAR = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
_FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

# Brand palette (hex → RGB)
_BG_TOP = (15, 5, 107)        # #0F056B JAPAP indigo
_BG_BOTTOM = (224, 28, 46)    # #E01C2E JAPAP red
_FG = (255, 255, 255)
_MUTED = (255, 255, 255, 180)
_QR_BG = (255, 255, 255)


def _load_font(path: str, size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()


def _vertical_gradient(width: int, height: int,
                       top: tuple, bottom: tuple) -> Image.Image:
    """Cheap vertical gradient — one row at a time. Fast enough at 1080×1920."""
    img = Image.new("RGB", (width, height), top)
    pixels = img.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        r = int(top[0] + (bottom[0] - top[0]) * ratio)
        g = int(top[1] + (bottom[1] - top[1]) * ratio)
        b = int(top[2] + (bottom[2] - top[2]) * ratio)
        for x in range(width):
            pixels[x, y] = (r, g, b)
    return img


def _wrap(draw: ImageDraw.ImageDraw, text: str,
          font: ImageFont.ImageFont, max_width: int) -> list:
    """Greedy word-wrap into lines that fit within max_width."""
    if not text:
        return []
    words = text.split()
    lines, line = [], ""
    for w in words:
        candidate = (line + " " + w).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def render_share_card(
    *,
    pay_url: str,
    alias: str,
    address: str = "",
    carrier_slug: str = "",
    country_code: str = "",
) -> bytes:
    """Render the 1080×1920 PNG card. Returns raw PNG bytes."""
    W, H = 1080, 1920
    img = _vertical_gradient(W, H, _BG_TOP, _BG_BOTTOM)
    draw = ImageDraw.Draw(img, "RGBA")

    # Subtle radial-style highlight at the top-left for depth.
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for r, alpha in ((900, 24), (650, 32), (400, 48)):
        od.ellipse((-r // 2, -r // 2, r // 2, r // 2),
                   fill=(255, 255, 255, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    # ── Top brand bar ───────────────────────────────────────────────
    f_brand = _load_font(_FONT_BOLD, 56)
    draw.text((60, 70), "JAPAP", font=f_brand, fill=_FG)
    f_brand_sub = _load_font(_FONT_REGULAR, 36)
    draw.text((250, 92), "WiFi", font=f_brand_sub, fill=_FG)

    # Country flag emoji slot (top-right) — uses the ISO country code if
    # provided. Pillow can't render colored emojis on debian without the
    # emoji font, so we render the 2-letter code in a small pill.
    if country_code:
        cc = country_code.upper()[:2]
        f_cc = _load_font(_FONT_BOLD, 32)
        bbox = draw.textbbox((0, 0), cc, font=f_cc)
        cw = bbox[2] - bbox[0]
        pill_w = cw + 36
        pill_x = W - 60 - pill_w
        draw.rounded_rectangle((pill_x, 80, pill_x + pill_w, 130),
                               radius=20,
                               fill=(255, 255, 255, 38))
        draw.text((pill_x + 18, 86), cc, font=f_cc, fill=_FG)

    # ── Carrier hint (auto-detected ISP) ────────────────────────────
    y = 280
    if carrier_slug:
        f_carrier = _load_font(_FONT_BOLD, 44)
        carrier_label = f"📡 {carrier_slug}"
        draw.text((60, y), carrier_label, font=f_carrier, fill=(255, 255, 255, 220))
        y += 70

    # ── Hotspot alias (the headline) ────────────────────────────────
    f_alias = _load_font(_FONT_BOLD, 96)
    alias_text = alias or "Mon WiFi JAPAP"
    alias_lines = _wrap(draw, alias_text, f_alias, W - 120)[:2]
    for line in alias_lines:
        draw.text((60, y), line, font=f_alias, fill=_FG)
        y += 110

    # Address subtitle
    if address:
        f_addr = _load_font(_FONT_REGULAR, 38)
        for line in _wrap(draw, address, f_addr, W - 120)[:2]:
            draw.text((60, y), line, font=f_addr, fill=(255, 255, 255, 200))
            y += 50

    # ── QR Code (centered in the lower half) ────────────────────────
    qr_size = 720
    qr_box = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=18, border=2)
    qr_box.add_data(pay_url)
    qr_box.make(fit=True)
    qr_img = qr_box.make_image(fill_color="#0F056B", back_color="white").convert("RGB")
    qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)

    # White rounded card behind the QR for max contrast on any background
    card_pad = 40
    card_w = qr_size + card_pad * 2
    card_x = (W - card_w) // 2
    card_y = max(y + 60, H // 2 - qr_size // 2)
    draw.rounded_rectangle(
        (card_x, card_y, card_x + card_w, card_y + card_w),
        radius=48, fill=_QR_BG,
    )
    img.paste(qr_img, (card_x + card_pad, card_y + card_pad))

    # ── CTA below the QR ────────────────────────────────────────────
    cta_y = card_y + card_w + 60
    f_cta = _load_font(_FONT_BOLD, 56)
    cta = "Scanne pour te connecter"
    bbox = draw.textbbox((0, 0), cta, font=f_cta)
    cta_w = bbox[2] - bbox[0]
    draw.text(((W - cta_w) // 2, cta_y), cta, font=f_cta, fill=_FG)

    # ── Watermark (bottom) ──────────────────────────────────────────
    f_wm = _load_font(_FONT_BOLD, 44)
    f_wm_sub = _load_font(_FONT_REGULAR, 32)
    wm = "Powered by JAPAP"
    bbox = draw.textbbox((0, 0), wm, font=f_wm)
    ww = bbox[2] - bbox[0]
    draw.text(((W - ww) // 2, H - 180), wm, font=f_wm, fill=_FG)
    # iter168 — brand-correct domain
    from utils.public_url import short_domain
    sub = short_domain()
    bbox = draw.textbbox((0, 0), sub, font=f_wm_sub)
    sw = bbox[2] - bbox[0]
    draw.text(((W - sw) // 2, H - 120), sub, font=f_wm_sub,
              fill=(255, 255, 255, 200))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
