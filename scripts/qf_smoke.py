#!/usr/bin/env python
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from worker.tasks import _download_audio

from quran_video.config import get_settings
from quran_video.config.visual_style import load_visual_style
from quran_video.models import BackgroundMode, RenderRequest
from quran_video.models.domain import BadgeSettings
from quran_video.quran.compatibility import (
    build_range_timeline,
    validate_audio_with_safe_fallback,
)
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


async def run() -> dict[str, object]:
    settings = get_settings().model_copy(update={"quran_video_data_mode": "quranfoundation"})
    if not settings.qf_client_id or not settings.qf_client_secret:
        raise RuntimeError("QF_CLIENT_ID and QF_CLIENT_SECRET must be set for live smoke test")
    background_path = settings.backgrounds_dir / "sample-qf-smoke-background.jpg"
    ensure_sample_background(background_path)
    repository = QuranRepository(settings)
    chapters = await repository.chapters()
    chapter = next(item for item in chapters if item.id == 1)
    verses = await repository.verses(chapter.id)
    reciters = await repository.reciters()
    selected = None
    for reciter in reciters[:25]:
        compatibility = await repository.compatibility(chapter.id, reciter.id)
        if compatibility.compatible:
            selected = reciter
            break
    if selected is None:
        raise RuntimeError("No compatible Quran.Foundation reciter found in first 25 reciters")
    audio = await repository.chapter_audio(chapter.id, selected.id)
    visual_style = load_visual_style()
    request = RenderRequest(
        reciter_id=selected.id,
        chapter_id=chapter.id,
        ayah_from=1,
        ayah_to=min(2, chapter.verse_count),
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
            arabic_reciter=selected.arabic_name,
            english_reciter=selected.english_name,
        ),
        data_mode="quranfoundation",
    )
    with tempfile.TemporaryDirectory(prefix="qf-smoke-audio-") as temp_dir:
        audio_path, render_audio = await _download_audio(
            audio,
            request,
            Path(temp_dir) / "recitation_audio",
        )
        render_audio, compatibility = validate_audio_with_safe_fallback(
            render_audio,
            verses,
            probed_duration_ms=render_audio.duration_ms,
            tolerance_ms=settings.qf_max_timing_overflow_ms,
            minimum_timing_coverage=settings.qf_min_timing_coverage,
        )
        if not compatibility.compatible:
            raise RuntimeError(compatibility.reason or "Quran.Foundation audio/timing mismatch")
        timeline = build_range_timeline(
            chapter,
            selected,
            verses,
            render_audio,
            request.ayah_from,
            request.ayah_to,
            request.include_bismillah,
        )
        output = render_video(
            timeline,
            request,
            settings.renders_dir / "qf-smoke",
            audio_path=audio_path,
        )
    return {
        "chapter": chapter.english_name,
        "ayahs": f"{request.ayah_from}-{request.ayah_to}",
        "reciter_id": selected.id,
        "reciter": selected.english_name,
        "verses": len(verses),
        "has_word_timing": render_audio.has_word_timing,
        "has_ayah_timing": render_audio.has_ayah_timing,
        "timing_segments": sum(len(item.word_segments) for item in render_audio.verse_timestamps),
        "audio_duration_ms": render_audio.duration_ms,
        "preview_video": str(output.video_path),
        "preview_thumbnail": str(output.thumbnail_path),
        "preview_duration_seconds": output.duration_seconds,
        "uploaded": False,
        "automation_state_modified": False,
    }


def main() -> int:
    try:
        result = asyncio.run(run())
    except RuntimeError as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
