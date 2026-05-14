"""
iter240l — Service de génération du certificat SVG premium pour les membres
du Jury Crowdfunding JAPAP. 100% additif (le PNG PIL legacy reste actif).

• 1200×850 paysage, dégradé bleu-nuit, double bordure dorée
• 5 langues (FR/EN/ES/AR-RTL/RU)
• Upload R2 (deterministic key) — overwrite à la régénération
• Aucune dépendance externe au-delà de boto3 (déjà installé)
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from services.r2_storage_service import (
    _get_r2_media_client,
    R2_MEDIA_BUCKET,
    R2_MEDIA_PUBLIC_URL,
    R2ConfigError,
)

logger = logging.getLogger(__name__)

SUPPORTED_LANGS = ("fr", "en", "es", "ar", "ru")
DEFAULT_LANG = "fr"

# ── Strings centralisés ─────────────────────────────────────────────────────
STRINGS = {
    "fr": {
        "header":    "JAPAP MESSENGER",
        "title":     "CERTIFICAT D'EXCELLENCE",
        "subtitle":  "Membre du Jury Crowdfunding",
        "awarded":   "Ce certificat est décerné à",
        "cycle":     "Pour avoir remporté le Cycle #{n} du Crowdfunding JAPAP",
        "prize":     "🏆 Prix remporté : {amount} $",
        "issued":    "Décerné le {date}",
        "admin":     "Admin JAPAP",
        "seal":      "✓ Certifié JAPAP",
        "domain":    "japapmessenger.com",
        "no_prize":  "Récompense honorifique",
    },
    "en": {
        "header":    "JAPAP MESSENGER",
        "title":     "CERTIFICATE OF EXCELLENCE",
        "subtitle":  "Crowdfunding Jury Member",
        "awarded":   "This certificate is awarded to",
        "cycle":     "For winning Cycle #{n} of JAPAP Crowdfunding",
        "prize":     "🏆 Prize won: ${amount}",
        "issued":    "Issued on {date}",
        "admin":     "JAPAP Admin",
        "seal":      "✓ Certified by JAPAP",
        "domain":    "japapmessenger.com",
        "no_prize":  "Honorary award",
    },
    "es": {
        "header":    "JAPAP MESSENGER",
        "title":     "CERTIFICADO DE EXCELENCIA",
        "subtitle":  "Miembro del Jurado Crowdfunding",
        "awarded":   "Este certificado se otorga a",
        "cycle":     "Por ganar el Ciclo #{n} del Crowdfunding JAPAP",
        "prize":     "🏆 Premio ganado: ${amount}",
        "issued":    "Emitido el {date}",
        "admin":     "Admin JAPAP",
        "seal":      "✓ Certificado por JAPAP",
        "domain":    "japapmessenger.com",
        "no_prize":  "Premio honorífico",
    },
    "ar": {
        "header":    "جابب مسنجر",
        "title":     "شهادة التميز",
        "subtitle":  "عضو لجنة تحكيم التمويل الجماعي",
        "awarded":   "تُمنح هذه الشهادة إلى",
        "cycle":     "لفوزه في الدورة رقم {n} من التمويل الجماعي JAPAP",
        "prize":     "🏆 الجائزة: {amount} دولار",
        "issued":    "صدر في {date}",
        "admin":     "إدارة JAPAP",
        "seal":      "✓ مُعتمد من JAPAP",
        "domain":    "japapmessenger.com",
        "no_prize":  "جائزة فخرية",
    },
    "ru": {
        "header":    "JAPAP MESSENGER",
        "title":     "СЕРТИФИКАТ ПРЕВОСХОДСТВА",
        "subtitle":  "Член жюри краудфандинга",
        "awarded":   "Данный сертификат присуждается",
        "cycle":     "За победу в Цикле #{n} краудфандинга JAPAP",
        "prize":     "🏆 Выигрыш: ${amount}",
        "issued":    "Выдан {date}",
        "admin":     "Администрация JAPAP",
        "seal":      "✓ Сертифицировано JAPAP",
        "domain":    "japapmessenger.com",
        "no_prize":  "Почётная награда",
    },
}


def _xml_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_date(dt: Optional[datetime], lang: str) -> str:
    dt = dt or datetime.now(timezone.utc)
    locales = {
        "fr": "%d/%m/%Y",
        "en": "%B %d, %Y",
        "es": "%d/%m/%Y",
        "ar": "%Y/%m/%d",
        "ru": "%d.%m.%Y",
    }
    return dt.strftime(locales.get(lang, "%d/%m/%Y"))


def _format_amount(amount) -> str:
    try:
        n = float(amount or 0)
        if n == int(n):
            return f"{int(n):,}".replace(",", " ")
        return f"{n:,.2f}".replace(",", " ")
    except Exception:
        return str(amount or 0)


def render_jury_certificate_svg(
    *,
    full_name: str,
    username: Optional[str] = None,
    cycle_number: int,
    prize_amount: Optional[float] = None,
    prize_currency: str = "USD",
    issued_at: Optional[datetime] = None,
    lang: str = DEFAULT_LANG,
    certificate_id: Optional[str] = None,
) -> str:
    """Generate a premium SVG certificate. Returns the SVG markup as str."""
    lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    s = STRINGS[lang]
    is_rtl = lang == "ar"
    direction_attr = ' direction="rtl"' if is_rtl else ""

    safe_name = _xml_escape(full_name or "—")
    safe_user = _xml_escape(f"@{username}") if username else ""
    cycle_txt = _xml_escape(s["cycle"].format(n=cycle_number))
    if prize_amount and float(prize_amount) > 0:
        prize_txt = _xml_escape(s["prize"].format(amount=_format_amount(prize_amount)))
    else:
        prize_txt = _xml_escape(s["no_prize"])
    issued_txt = _xml_escape(s["issued"].format(date=_format_date(issued_at, lang)))
    title_txt = _xml_escape(s["title"])
    subtitle_txt = _xml_escape(s["subtitle"])
    awarded_txt = _xml_escape(s["awarded"])
    header_txt = _xml_escape(s["header"])
    admin_txt = _xml_escape(s["admin"])
    seal_txt = _xml_escape(s["seal"])
    domain_txt = _xml_escape(s["domain"])
    cert_id_txt = _xml_escape(certificate_id or "")

    # Decorative inline particles (positions vary; deterministic for reproducibility)
    particles = []
    for i, (cx, cy, r, o) in enumerate([
        (140, 160, 1.5, 0.7), (260, 90, 1.0, 0.5), (380, 200, 2.0, 0.6),
        (1060, 130, 1.5, 0.7), (1100, 240, 1.0, 0.5), (900, 70, 2.0, 0.6),
        (180, 700, 1.5, 0.5), (320, 760, 1.0, 0.4), (1000, 720, 1.5, 0.6),
        (1080, 760, 1.0, 0.4), (240, 410, 1.0, 0.3), (1080, 420, 1.0, 0.3),
    ]):
        particles.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#FFD700" opacity="{o}"/>'
        )
    particles_svg = "\n  ".join(particles)

    # ─ Build the SVG ───────────────────────────────────────────────────────
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="850"
     viewBox="0 0 1200 850" role="img" aria-label="{title_txt} — {safe_name}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%"  stop-color="#1a1a2e"/>
      <stop offset="55%" stop-color="#16213e"/>
      <stop offset="100%" stop-color="#0f3460"/>
    </linearGradient>
    <linearGradient id="gold" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#FFE57F"/>
      <stop offset="50%" stop-color="#FFD700"/>
      <stop offset="100%" stop-color="#FFA500"/>
    </linearGradient>
    <radialGradient id="seal" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#FFD700" stop-opacity="0.12"/>
      <stop offset="70%" stop-color="#FFD700" stop-opacity="0.04"/>
      <stop offset="100%" stop-color="#FFD700" stop-opacity="0"/>
    </radialGradient>
    <filter id="shimmer" x="-5%" y="-50%" width="110%" height="200%">
      <feGaussianBlur stdDeviation="0.6"/>
    </filter>
    <style>
      .title-font   {{ font-family: "Times New Roman", "Cinzel", "Amiri", serif; }}
      .body-font    {{ font-family: "Helvetica", "Segoe UI", "Noto Sans Arabic", sans-serif; }}
      .mono         {{ font-family: "Courier New", monospace; }}
    </style>
  </defs>

  <!-- Background gradient -->
  <rect width="1200" height="850" fill="url(#bg)"/>

  <!-- Decorative particles -->
  {particles_svg}

  <!-- Watermark seal centered -->
  <circle cx="600" cy="425" r="260" fill="url(#seal)"/>
  <text x="600" y="430" text-anchor="middle" class="title-font"
        font-size="180" font-weight="900" fill="#FFD700" opacity="0.06">JAPAP</text>

  <!-- Double border (gold) -->
  <rect x="30" y="30" width="1140" height="790" rx="16" ry="16"
        fill="none" stroke="#FFD700" stroke-width="4"/>
  <rect x="44" y="44" width="1112" height="762" rx="12" ry="12"
        fill="none" stroke="#FFA500" stroke-width="2" opacity="0.85"/>

  <!-- 4 corner ornaments (gold diamonds + lines) -->
  <g fill="#FFD700">
    <!-- top-left -->
    <polygon points="60,60 80,40 100,60 80,80"/>
    <rect x="60" y="58" width="3" height="40" />
    <rect x="58" y="60" width="40" height="3" />
    <!-- top-right -->
    <polygon points="1100,60 1120,40 1140,60 1120,80"/>
    <rect x="1137" y="58" width="3" height="40" />
    <rect x="1102" y="60" width="40" height="3" />
    <!-- bottom-left -->
    <polygon points="60,790 80,770 100,790 80,810"/>
    <rect x="60" y="752" width="3" height="40" />
    <rect x="58" y="787" width="40" height="3" />
    <!-- bottom-right -->
    <polygon points="1100,790 1120,770 1140,790 1120,810"/>
    <rect x="1137" y="752" width="3" height="40" />
    <rect x="1102" y="787" width="40" height="3" />
  </g>

  <!-- HEADER: JAPAP logo + name -->
  <g transform="translate(600 130)">
    <circle cx="-150" cy="0" r="32" fill="#E63946" stroke="#FFD700" stroke-width="2"/>
    <text x="-150" y="11" text-anchor="middle" font-size="38" font-weight="900"
          fill="#ffffff" class="title-font">J</text>
    <text x="40" y="8" text-anchor="middle" font-size="22" font-weight="700"
          fill="#ffffff" letter-spacing="6" class="body-font"{direction_attr}>{header_txt}</text>
  </g>
  <line x1="320" y1="180" x2="880" y2="180" stroke="url(#gold)" stroke-width="2"/>

  <!-- TITLE -->
  <text x="600" y="248" text-anchor="middle" class="title-font"
        font-size="42" font-weight="700" fill="#FFD700"
        filter="url(#shimmer)"{direction_attr}>{title_txt}</text>
  <text x="600" y="284" text-anchor="middle" class="body-font"
        font-size="18" fill="#ffffff" opacity="0.9"{direction_attr}>{subtitle_txt}</text>

  <!-- 3 stars -->
  <g transform="translate(600 320)" fill="#FFD700">
    <text x="-60" y="0" text-anchor="middle" font-size="22">★</text>
    <text x="0"   y="0" text-anchor="middle" font-size="22">★</text>
    <text x="60"  y="0" text-anchor="middle" font-size="22">★</text>
  </g>

  <!-- BODY -->
  <text x="600" y="380" text-anchor="middle" class="body-font"
        font-size="14" fill="#cccccc" font-style="italic"{direction_attr}>{awarded_txt}</text>

  <!-- Recipient name -->
  <text x="600" y="438" text-anchor="middle" class="title-font"
        font-size="48" font-weight="700" fill="#ffffff"
        filter="url(#shimmer)"{direction_attr}>{safe_name}</text>
  {f'<text x="600" y="465" text-anchor="middle" class="mono" font-size="13" fill="#a0a0c0">{safe_user}</text>' if safe_user else ''}

  <!-- Cycle line -->
  <text x="600" y="510" text-anchor="middle" class="body-font"
        font-size="17" fill="#ffffff" opacity="0.95"{direction_attr}>{cycle_txt}</text>

  <!-- Prize -->
  <text x="600" y="560" text-anchor="middle" class="body-font"
        font-size="26" font-weight="700" fill="#FFD700"
        filter="url(#shimmer)"{direction_attr}>{prize_txt}</text>

  <!-- Issued date -->
  <text x="600" y="608" text-anchor="middle" class="body-font"
        font-size="13" fill="#a0a0c0"{direction_attr}>{issued_txt}</text>

  <!-- Footer gold separator -->
  <line x1="120" y1="680" x2="1080" y2="680" stroke="url(#gold)" stroke-width="1.5" opacity="0.7"/>

  <!-- FOOTER: signature left -->
  <g transform="translate(180 730)">
    <path d="M 0 10 C 20 -10, 40 30, 70 5 S 110 25, 140 0" stroke="#FFD700"
          stroke-width="1.6" fill="none"/>
    <text x="0" y="38" class="body-font" font-size="12" fill="#cccccc">{admin_txt}</text>
  </g>

  <!-- FOOTER: seal/badge right -->
  <g transform="translate(1020 730)">
    <circle cx="0" cy="6" r="34" fill="none" stroke="#FFD700" stroke-width="2"/>
    <circle cx="0" cy="6" r="28" fill="none" stroke="#FFA500" stroke-width="1" opacity="0.7"/>
    <text x="0" y="2" text-anchor="middle" class="body-font"
          font-size="9" font-weight="700" fill="#FFD700">✓</text>
    <text x="0" y="15" text-anchor="middle" class="body-font"
          font-size="6" font-weight="700" fill="#FFD700"
          letter-spacing="0.5">JAPAP</text>
    <text x="0" y="58" text-anchor="middle" class="body-font"
          font-size="10" fill="#cccccc"{direction_attr}>{seal_txt}</text>
  </g>

  <!-- Domain centered + cert id -->
  <text x="600" y="795" text-anchor="middle" class="body-font"
        font-size="12" fill="#a0a0c0" letter-spacing="2">{domain_txt}</text>
  {f'<text x="600" y="812" text-anchor="middle" class="mono" font-size="9" fill="#666688">ID: {cert_id_txt}</text>' if cert_id_txt else ''}
</svg>
"""


# ── R2 upload (deterministic key) ───────────────────────────────────────────
def upload_certificate_to_r2(svg_bytes: bytes, user_id: str, lang: str,
                              fmt: str = "svg") -> str:
    """Upload a certificate (SVG or PDF) to R2 at a deterministic key.

    Key pattern: `certificates/{user_id}_{lang}.{fmt}`
    Overwrites on regenerate. Returns the public URL.
    `fmt` must be 'svg' or 'pdf'. Raises R2ConfigError if credentials missing.
    """
    lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    fmt = fmt if fmt in ("svg", "pdf") else "svg"
    key = f"certificates/{user_id}_{lang}.{fmt}"
    ctype = "image/svg+xml; charset=utf-8" if fmt == "svg" else "application/pdf"
    client = _get_r2_media_client()
    client.put_object(
        Bucket=R2_MEDIA_BUCKET,
        Key=key,
        Body=svg_bytes,
        ContentType=ctype,
        CacheControl="public, max-age=3600",
    )
    return f"{R2_MEDIA_PUBLIC_URL}/{key}"


def render_jury_certificate_pdf(svg: str) -> bytes:
    """iter240l-pdf — Convert an SVG certificate to a printable PDF using cairosvg.

    The output preserves vector quality so users can print at any size or share
    on LinkedIn. Raises ImportError if cairosvg is not installed."""
    try:
        import cairosvg  # type: ignore
    except ImportError as e:
        raise ImportError("cairosvg required for PDF certificate generation. "
                          "Run: pip install cairosvg") from e
    return cairosvg.svg2pdf(bytestring=svg.encode("utf-8"))


def generate_and_upload_jury_certificate(
    *,
    user_id: str,
    full_name: str,
    username: Optional[str],
    cycle_number: int,
    prize_amount: Optional[float],
    prize_currency: str = "USD",
    issued_at: Optional[datetime] = None,
    lang: str = DEFAULT_LANG,
    certificate_id: Optional[str] = None,
    also_pdf: bool = True,
) -> Optional[dict]:
    """High-level helper. Generates SVG (+ PDF) and uploads to R2.

    Returns a dict {"svg_url": ..., "pdf_url": ...} on success, None on failure
    (best-effort, never raises). Callers should persist svg_url in
    `crowdfunding_jury_members.certificate_url`."""
    try:
        svg = render_jury_certificate_svg(
            full_name=full_name, username=username,
            cycle_number=cycle_number, prize_amount=prize_amount,
            prize_currency=prize_currency, issued_at=issued_at,
            lang=lang, certificate_id=certificate_id,
        )
        svg_bytes = svg.encode("utf-8")
        svg_url = upload_certificate_to_r2(svg_bytes, user_id, lang, fmt="svg")
        pdf_url = None
        if also_pdf:
            try:
                pdf_bytes = render_jury_certificate_pdf(svg)
                pdf_url = upload_certificate_to_r2(pdf_bytes, user_id, lang, fmt="pdf")
            except Exception as pe:
                logger.warning(f"[jury_cert] PDF generation failed user={user_id} lang={lang}: {pe}")
        logger.info(f"[jury_cert] uploaded R2 cert user={user_id} lang={lang} "
                    f"svg={svg_url} pdf={pdf_url}")
        return {"svg_url": svg_url, "pdf_url": pdf_url}
    except R2ConfigError as e:
        logger.warning(f"[jury_cert] R2 not configured, skipping upload: {e}")
        return None
    except Exception as e:
        logger.error(f"[jury_cert] upload failed user={user_id} lang={lang}: {e}", exc_info=True)
        return None
