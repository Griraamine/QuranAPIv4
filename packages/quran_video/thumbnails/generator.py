from __future__ import annotations

import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from quran_video.models import RenderRequest, RenderTimeline
from quran_video.quran.artistic_names import surah_artistic_name
from quran_video.rendering.fonts import (
    FontResolution,
    resolve_arabic_choice,
    resolve_english_choice,
    resolve_fonts,
)
from quran_video.rendering.media import probe_media


def generate_thumbnail(
    background_path: Path,
    output_path: Path,
    timeline: RenderTimeline,
    request: RenderRequest,
    *,
    fonts: FontResolution | None = None,
    seed: int = 118,
) -> Path:
    fonts = fonts or resolve_fonts()
    source = _thumbnail_source(background_path, output_path.parent, seed)
    with Image.open(source) as image:
        image = image.convert("RGB")
        image = _center_crop(image, 1280, 720)
        image = _dim_background(image, request.background_style.dim_opacity)
        draw = ImageDraw.Draw(image)
        style = request.thumbnail_style
        arabic_font_choice = resolve_arabic_choice(
            request.typography.arabic_font_key, fonts.arabic_quran
        )
        english_font_choice = resolve_english_choice(
            request.typography.english_font_key, fonts.english, italic=True
        )
        arabic_surah = request.badge.arabic_surah or timeline.chapter.arabic_name
        arabic_reciter = request.badge.arabic_reciter or timeline.reciter.arabic_name
        english_surah = request.badge.english_surah or timeline.chapter.english_name
        english_reciter = request.badge.english_reciter or timeline.reciter.english_name
        arabic_text = (
            f"سورة {surah_artistic_name(request.chapter_id, arabic_surah)} | {arabic_reciter}"
        )
        arabic_font = _fit_font(
            arabic_font_choice.path,
            arabic_text,
            style.artistic_surah_size,
            12,
            1120,
            "rtl",
        )
        shadow = style.shadow_px
        _draw_centered(
            image,
            draw,
            (640, style.artistic_y),
            arabic_text,
            arabic_font,
            "rtl",
            shadow,
            (255, 255, 255),
        )
        if style.show_english:
            english_text = f"Surah {english_surah} | {english_reciter}"
            english_font = _fit_font(
                english_font_choice.path,
                english_text,
                request.typography.translation_font_size,
                12,
                1120,
                None,
            )
            _draw_centered(
                image,
                draw,
                (640, style.english_y),
                english_text,
                english_font,
                None,
                shadow,
                (255, 255, 255),
            )
        _save_under_2mb(image, output_path)
    if source != background_path and source.exists():
        source.unlink(missing_ok=True)
    return output_path


def _thumbnail_source(background_path: Path, work_dir: Path, seed: int) -> Path:
    probe = probe_media(background_path)
    if probe.media_type == "image":
        return background_path
    duration = probe.duration_seconds or 1.0
    timestamp = (
        duration / 2
        if duration < 2
        else random.Random(seed).uniform(duration * 0.1, duration * 0.9)
    )
    frame = work_dir / "thumbnail_frame.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(background_path),
            "-frames:v",
            "1",
            str(frame),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return frame


def _center_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    src_w, src_h = image.size
    scale = max(width / src_w, height / src_h)
    resized = image.resize((round(src_w * scale), round(src_h * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _dim_background(image: Image.Image, dim_opacity: int) -> Image.Image:
    if dim_opacity <= 0:
        return image
    alpha = min(max(dim_opacity, 0), 90) / 100
    return Image.blend(image, Image.new("RGB", image.size, (0, 0, 0)), alpha)


def _fit_font(
    path: Path, text: str, target: int, minimum: int, max_width: int, direction: str | None
) -> ImageFont.FreeTypeFont:
    probe = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(probe)
    for size in range(target, minimum - 1, -2):
        font = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.RAQM)
        bbox = draw.textbbox((0, 0), text, font=font, direction=direction)
        if bbox[2] - bbox[0] <= max_width:
            return font
    return ImageFont.truetype(str(path), minimum, layout_engine=ImageFont.Layout.RAQM)


def _draw_centered(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    direction: str | None,
    shadow: int,
    fill: tuple[int, int, int],
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font, direction=direction)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = center[0] - width / 2 - bbox[0]
    y = center[1] - height / 2 - bbox[1]
    for radius in range(shadow, 0, -1):
        shadow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        shadow_draw.text((x, y), text, font=font, fill=(0, 0, 0, 210), direction=direction)
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius))
        image.paste(Image.alpha_composite(image.convert("RGBA"), shadow_layer).convert("RGB"))
    draw.text((x, y), text, font=font, fill=fill, direction=direction)


def _save_under_2mb(image: Image.Image, output_path: Path) -> None:
    for quality in range(94, 54, -4):
        image.save(output_path, "JPEG", quality=quality, optimize=True, progressive=True)
        if output_path.stat().st_size < 2 * 1024 * 1024:
            return
    image.save(output_path, "JPEG", quality=50, optimize=True, progressive=True)
    if output_path.stat().st_size >= 2 * 1024 * 1024:
        raise RuntimeError("thumbnail could not be compressed below 2 MB without resizing")
