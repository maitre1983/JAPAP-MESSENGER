"""
JAPAP — Video transcoding pipeline (iter190)
============================================
P0 — Tolerant video upload: accept ANY common container (.mov, .mp4, .avi,
.mkv, .webm, .3gp, .m4v, .hevc, .mpg, .mpeg) and transcode to a single
web-friendly canonical format:

    H.264 (libx264) + AAC + +faststart, .mp4

Why this design:
  • iPhone .mov is rejected by Safari/Chrome on Android — always transcode.
  • Single output container = simpler frontend (<video src=...mp4>).
  • +faststart = MOOV atom up front → instant streaming start.

Public API:
  is_video_ext(ext) -> bool
  probe(path) -> dict (codec, duration, w, h, has_audio)
  transcode_to_mp4(src_path, out_path, max_height=1080, crf=23) -> dict
  generate_thumbnail(src_path, out_jpg_path, ts="00:00:01") -> bool

The transcode runs as a subprocess and is bounded by `TRANSCODE_TIMEOUT`
(default 5 min). For very large files we cap to 1080p to keep storage and
bandwidth costs bounded — sources above 1080p are downscaled keeping
aspect ratio.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Format support ──────────────────────────────────────────────────────
# We accept these containers on upload. The pipeline always normalises to
# canonical .mp4 / H.264 / AAC. Extensions are checked case-insensitively.
SUPPORTED_VIDEO_EXTS: set[str] = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".3gp", ".3g2", ".m4v", ".hevc", ".h265",
    ".mpeg", ".mpg", ".m2ts", ".ts", ".flv", ".wmv",
}
CANONICAL_VIDEO_EXT = ".mp4"
CANONICAL_VIDEO_CT = "video/mp4"
# Tunables — keep at module level so admin_settings can override later.
TRANSCODE_TIMEOUT = 300            # seconds
DEFAULT_MAX_HEIGHT = 1080
DEFAULT_CRF = 23                   # lower = better quality (18-28 sane)
DEFAULT_PRESET = "fast"            # speed/size tradeoff


def is_video_ext(ext: str) -> bool:
    return (ext or "").lower() in SUPPORTED_VIDEO_EXTS


# iter218 — bundled static FFmpeg binaries.
# `static_ffmpeg` ships pre-compiled ffmpeg + ffprobe inside the pip
# package, so production deployments don't need an `apt-get install
# ffmpeg` step on the host. The fetch-on-demand only runs once per
# container lifetime (cached on disk afterwards).
def _arch_dir_for_static_ffmpeg() -> Optional[str]:
    """Return the static_ffmpeg subfolder name matching the current CPU.

    static_ffmpeg 2.13 ships TWO Linux variants (`bin/linux/` for x86_64,
    `bin/linux_arm64/` for aarch64) but its `get_or_fetch_*` helper has
    a bug: it always returns `bin/linux/` even on aarch64 hosts. Running
    that x86_64 binary on ARM64 raises `OSError: [Errno 8] Exec format
    error` deep inside subprocess, surfaced to the user as a generic
    "Erreur upload". We sidestep the bug by selecting the right folder
    ourselves based on `platform.machine()`.
    """
    sys_name = platform.system().lower()
    machine = (platform.machine() or "").lower()
    if sys_name != "linux":
        return None  # Mac / Windows — let the lib's resolver run.
    # Normalise common ARM aliases.
    if machine in ("aarch64", "arm64"):
        return "linux_arm64"
    if machine in ("x86_64", "amd64"):
        return "linux"
    return None


def _resolve_ffmpeg_paths() -> tuple[str, str]:
    """Return (ffmpeg_bin, ffprobe_bin), preferring system PATH first
    (cheaper) and falling back to the static pip-bundled binaries with
    explicit per-arch lookup. Cached at module level.
    """
    # 1. System PATH (apt-installed) — preferred for speed & smaller RAM.
    sys_ff = shutil.which("ffmpeg")
    sys_fp = shutil.which("ffprobe")
    if sys_ff and sys_fp and _verify_executable(sys_ff) and _verify_executable(sys_fp):
        return sys_ff, sys_fp
    # 2. Static pip package bundled binaries — explicit arch selection
    #    to dodge the static_ffmpeg<=2.13 wrong-arch bug on aarch64.
    try:
        import static_ffmpeg  # noqa: F401  (just to locate the package dir)
        from static_ffmpeg.run import (
            get_or_fetch_platform_executables_else_raise,
        )
        # First, ensure the per-platform zip is downloaded/extracted.
        get_or_fetch_platform_executables_else_raise()
        pkg_root = Path(static_ffmpeg.__file__).parent
        arch_dir = _arch_dir_for_static_ffmpeg()
        candidates = []
        if arch_dir:
            candidates.append(pkg_root / "bin" / arch_dir)
        # Fallback to whatever the lib originally returned (last resort).
        try:
            ff, fp = get_or_fetch_platform_executables_else_raise()
            candidates.append(Path(ff).parent)
        except Exception:
            pass
        for d in candidates:
            ff = d / "ffmpeg"
            fp = d / "ffprobe"
            if ff.exists() and fp.exists() and _verify_executable(str(ff)) and _verify_executable(str(fp)):
                # Make sure they're +x — pip extraction occasionally
                # drops the executable bit on some filesystems.
                for p in (ff, fp):
                    try:
                        st = p.stat()
                        os.chmod(p, st.st_mode | 0o111)
                    except Exception:
                        pass
                return str(ff), str(fp)
    except Exception as e:  # pragma: no cover — last resort logging
        logger.error(f"[video] static_ffmpeg fallback failed: {e}")
    # 3. Give up — let the subprocess raise the real error.
    return "ffmpeg", "ffprobe"


def _verify_executable(path: str) -> bool:
    """Run `<bin> -version` to make sure the binary is actually
    executable on this CPU. Catches the ARM64-running-x86_64 trap
    (Exec format error) at startup instead of at first user upload.
    """
    try:
        r = subprocess.run(
            [path, "-version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=4, check=False,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_FFMPEG_BIN, _FFPROBE_BIN = _resolve_ffmpeg_paths()
logger.info(
    f"[video] ffmpeg={_FFMPEG_BIN} ffprobe={_FFPROBE_BIN} "
    f"arch={platform.machine()}"
)


def has_ffmpeg() -> bool:
    """Verified at module init via `_verify_executable`. We don't repeat
    the subprocess here on every call — too expensive for a hot path."""
    return bool(_FFMPEG_BIN and _FFPROBE_BIN
                and os.path.exists(_FFMPEG_BIN)
                and os.path.exists(_FFPROBE_BIN))


async def probe(src_path: str | Path) -> dict:
    """Return basic stream info (codec, duration, dims, has_audio).
    Raises ValueError if ffprobe fails — caller should treat that as
    "not a parseable video" and reject the upload.
    """
    src = str(src_path)
    proc = await asyncio.create_subprocess_exec(
        _FFPROBE_BIN, "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", src,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise ValueError(f"ffprobe failed: {err.decode(errors='ignore')[:200]}")
    data = json.loads(out.decode(errors="ignore") or "{}")
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or v.get("duration") or 0.0)
    return {
        "codec": v.get("codec_name"),
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "duration": duration,
        "has_audio": a is not None,
        "size_bytes": int(fmt.get("size") or 0),
        "format_name": fmt.get("format_name"),
    }


async def transcode_to_mp4(
    src: str | Path, out: str | Path,
    max_height: int = DEFAULT_MAX_HEIGHT,
    crf: int = DEFAULT_CRF, preset: str = DEFAULT_PRESET,
    timeout: int = TRANSCODE_TIMEOUT,
) -> dict:
    """Transcode any input container to a web-friendly .mp4.

    • Video: libx264, scaled to fit `max_height` keeping AR, even-pixel
      enforced (filter `pad=ceil(iw/2)*2:ceil(ih/2)*2`).
    • Audio: AAC 128k stereo (or copy-stripped if source has none).
    • Container: mp4 with `+faststart` so MOOV atom is at the head.

    Returns the probe of the OUTPUT file. Raises RuntimeError on failure.
    """
    src, out = str(src), str(out)
    # Build filter:  -2 means "auto-compute keeping AR, divisible by 2"
    vf = f"scale=-2:'min({max_height},ih)'"
    cmd = [
        _FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
        "-i", src,
        "-vf", vf,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",          # max compatibility (Safari, IG, etc.)
        "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-max_muxing_queue_size", "1024",
        out,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Transcode timed out after {timeout}s")
    if proc.returncode != 0:
        snippet = err.decode(errors="ignore")[:500]
        raise RuntimeError(f"Transcode failed (code={proc.returncode}): {snippet}")
    return await probe(out)


async def generate_thumbnail(
    src: str | Path, out_jpg: str | Path,
    ts: str = "00:00:01", width: int = 720,
    timeout: int = 30,
) -> bool:
    """Grab a single JPEG frame at `ts` and downscale to `width` px.
    Returns True if the file was created. Never raises — failure is
    swallowed and logged so a bad thumbnail never blocks a publish.
    """
    src, out_jpg = str(src), str(out_jpg)
    cmd = [
        _FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", ts, "-i", src,
        "-vf", f"scale={width}:-2",
        "-vframes", "1", "-q:v", "3",
        out_jpg,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0 and os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0:
            return True
    except Exception as e:
        logger.warning(f"[video] thumbnail generation failed: {e}")
    # Try a fallback at t=0 if t=1s produced nothing (very short clips)
    if ts != "00:00:00":
        return await generate_thumbnail(src, out_jpg, ts="00:00:00",
                                         width=width, timeout=timeout)
    return False
