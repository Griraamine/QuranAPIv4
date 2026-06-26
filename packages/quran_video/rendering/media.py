from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from quran_video.config import get_settings
from quran_video.models import BackgroundAsset

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


@dataclass(frozen=True)
class MediaProbe:
    path: Path
    media_type: str
    width: int
    height: int
    duration_seconds: float | None
    has_audio: bool
    sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_background_path(identifier: str) -> Path:
    settings = get_settings()
    root = settings.backgrounds_dir.resolve()
    relative = Path(identifier)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("background path escapes media/backgrounds")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("background path escapes media/backgrounds") from error
    if candidate.is_symlink():
        raise ValueError("background path escapes media/backgrounds")
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError("background not found")
    return candidate


def probe_media(path: Path) -> MediaProbe:
    if not path.exists() or path.stat().st_size <= 0:
        raise ValueError("media file is missing or empty")
    ext = path.suffix.casefold()
    if ext not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
        raise ValueError("unsupported media extension")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    video_stream = next(
        (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise ValueError("media has no usable image/video stream")
    has_audio = any(stream.get("codec_type") == "audio" for stream in payload.get("streams", []))
    width = int(video_stream["width"])
    height = int(video_stream["height"])
    duration_raw = video_stream.get("duration") or payload.get("format", {}).get("duration")
    duration = float(duration_raw) if duration_raw else None
    media_type = "image" if ext in IMAGE_EXTENSIONS else "video"
    return MediaProbe(path, media_type, width, height, duration, has_audio, sha256_file(path))


def list_backgrounds() -> list[BackgroundAsset]:
    settings = get_settings()
    settings.backgrounds_dir.mkdir(parents=True, exist_ok=True)
    assets: list[BackgroundAsset] = []
    for path in sorted(settings.backgrounds_dir.rglob("*")):
        if not path.is_file() or path.name.startswith(".") or path.is_symlink():
            continue
        try:
            probe = probe_media(path)
        except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        assets.append(
            BackgroundAsset(
                id=path.relative_to(settings.backgrounds_dir).as_posix(),
                filename=path.relative_to(settings.backgrounds_dir).as_posix(),
                media_type="image" if probe.media_type == "image" else "video",
                width=probe.width,
                height=probe.height,
                duration_seconds=probe.duration_seconds,
                sha256=probe.sha256,
            )
        )
    return assets


def calculate_center_crop(
    src_w: int, src_h: int, dst_w: int = 1920, dst_h: int = 1080
) -> tuple[int, int]:
    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h
    if src_ratio > dst_ratio:
        scaled_h = dst_h
        scaled_w = round(dst_h * src_ratio)
    else:
        scaled_w = dst_w
        scaled_h = round(dst_w / src_ratio)
    return scaled_w, scaled_h
