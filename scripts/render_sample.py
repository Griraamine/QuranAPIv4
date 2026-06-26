#!/usr/bin/env python
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PIL import Image, ImageDraw

from quran_video.config import get_settings
from quran_video.config.visual_style import load_visual_style
from quran_video.models import BackgroundMode, RenderRequest
from quran_video.models.domain import BadgeSettings
from quran_video.quran.compatibility import build_range_timeline
from quran_video.quran.repository import QuranRepository
from quran_video.rendering.ffmpeg import render_video


def ensure_sample_background(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1920, 1080), (58, 68, 72))
    draw = ImageDraw.Draw(image)
    for y in range(1080):
        shade = int(40 + y / 1080 * 80)
        draw.line((0, y, 1920, y), fill=(shade, shade + 10, shade + 14))
    draw.ellipse((90, 80, 430, 420), fill=(116, 128, 126))
    draw.rectangle((0, 780, 1920, 1080), fill=(45, 55, 54))
    image.save(path, "JPEG", quality=92)


async def run() -> dict[str, str]:
    settings = get_settings().model_copy(update={"quran_video_data_mode": "fixture"})
    background_path = settings.backgrounds_dir / "sample-fixture-background.jpg"
    ensure_sample_background(background_path)
    repository = QuranRepository(settings)
    chapter = (await repository.chapters())[0]
    reciter = (await repository.reciters())[0]
    verses = await repository.verses(chapter.id)
    audio = await repository.chapter_audio(chapter.id, reciter.id)
    visual_style = load_visual_style()
    request = RenderRequest(
        reciter_id=reciter.id,
        chapter_id=chapter.id,
        ayah_from=1,
        ayah_to=2,
        include_bismillah=False,
        background_mode=BackgroundMode.single,
        background_ids=[background_path.name],
        background_style=visual_style.background_style,
        typography=visual_style.typography,
        badge_style=visual_style.badge_style,
        thumbnail_style=visual_style.thumbnail_style,
        badge=BadgeSettings(
            enabled=True,
            arabic_surah=chapter.arabic_name,
            english_surah=chapter.english_name,
            arabic_reciter=reciter.arabic_name,
            english_reciter=reciter.english_name,
        ),
        data_mode="fixture",
    )
    timeline = build_range_timeline(chapter, reciter, verses, audio, 1, 2, False)
    result = render_video(timeline, request, settings.renders_dir / "sample")
    return {
        "video": str(result.video_path),
        "thumbnail": str(result.thumbnail_path),
        "duration_seconds": str(result.duration_seconds),
    }


def main() -> int:
    result = asyncio.run(run())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
