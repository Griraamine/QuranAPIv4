from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from quran_video.config import get_settings
from quran_video.models import ChapterAudio, RenderRequest, VerseTimestamp, WordSegment
from quran_video.models.domain import RenderStatus
from quran_video.quran.compatibility import (
    audio_with_ayah_level_segments,
    build_range_timeline,
    validate_audio_compatibility,
    validate_audio_with_safe_fallback,
    validate_ayah_range,
)
from quran_video.quran.render_defaults import resolve_render_request_defaults
from quran_video.quran.repository import QuranRepository
from quran_video.rendering.ffmpeg import RenderCanceled, render_video
from quran_video.storage import JobStore

LOGGER = logging.getLogger(__name__)
AUDIO_DOWNLOAD_ATTEMPTS = 5
AUDIO_CACHE_METADATA_VERSION = 1


def _ensure_not_canceled(store: JobStore, job_id: str) -> None:
    if store.get(job_id).status == RenderStatus.canceled:
        raise RenderCanceled("render canceled")


def run_render_job(job_id: str) -> None:
    asyncio.run(_run_render_job(job_id))


async def _download_audio(
    audio: ChapterAudio,
    request: RenderRequest,
    output_path: Path,
) -> tuple[Path | None, ChapterAudio]:
    if audio.url.startswith("fixture://"):
        return None, audio
    if audio.provider == "mp3quran":
        return await _download_cached_full_surah_audio(
            audio, request, output_path.with_suffix(".mp3")
        )
    if audio.provider == "quranfoundation":
        return await _download_cached_full_surah_audio(
            audio, request, output_path.with_suffix(".mp3")
        )
    if audio.url.startswith("alqurancloud://"):
        return await _download_and_concatenate_ayah_audio(audio, request, output_path)
    if not audio.url.startswith("https://"):
        raise ValueError("audio download URL must use HTTPS")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0),
        follow_redirects=True,
        headers={"User-Agent": "quran-video-platform/0.1"},
    ) as client:
        await _download_url_with_retries(client, audio.url, output_path)
    return output_path, audio


async def _download_url_with_retries(
    client: httpx.AsyncClient,
    url: str,
    target: Path,
    *,
    attempts: int = AUDIO_DOWNLOAD_ATTEMPTS,
) -> Path:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        part_path = target.with_name(f"{target.name}.part-{attempt}")
        part_path.unlink(missing_ok=True)
        try:
            async with client.stream("GET", url) as response:
                if (response.status_code == 429 or 500 <= response.status_code < 600) and (
                    attempt < attempts
                ):
                    await asyncio.sleep(_retry_after_delay(response, attempt))
                    continue
                response.raise_for_status()
                with part_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        handle.write(chunk)
            if part_path.stat().st_size == 0:
                raise RuntimeError("audio download returned an empty file")
            part_path.replace(target)
            return target
        except httpx.HTTPStatusError as error:
            part_path.unlink(missing_ok=True)
            last_error = error
            if error.response.status_code < 500 or attempt == attempts:
                raise
        except (httpx.TimeoutException, httpx.TransportError, RuntimeError) as error:
            part_path.unlink(missing_ok=True)
            last_error = error
            if attempt == attempts:
                raise
        LOGGER.warning(
            "audio download failed on attempt %s/%s; retrying: %s",
            attempt,
            attempts,
            last_error,
        )
        await asyncio.sleep(min(0.75 * attempt, 3.0))
    raise RuntimeError(f"audio download failed after {attempts} attempts") from last_error


async def _download_cached_full_surah_audio(
    audio: ChapterAudio,
    request: RenderRequest,
    output_path: Path,
) -> tuple[Path, ChapterAudio]:
    settings = get_settings()
    if audio.provider == "mp3quran":
        if not audio.moshaf_id:
            raise ValueError("MP3Quran audio requires a selected moshaf_id")
        cache_root = (
            settings.mp3quran_audio_cache_dir
            / _safe_cache_part(audio.reciter_id)
            / _safe_cache_part(audio.moshaf_id)
        )
        timeout = httpx.Timeout(
            settings.mp3quran_read_timeout, connect=settings.mp3quran_connect_timeout
        )
        attempts = settings.mp3quran_retries
        user_agent = "quran-video-platform/0.1 (+https://mp3quran.net)"
    elif audio.provider == "quranfoundation":
        cache_root = settings.quran_foundation_audio_cache_dir / _safe_cache_part(audio.reciter_id)
        timeout = httpx.Timeout(settings.qf_read_timeout, connect=settings.qf_connect_timeout)
        attempts = settings.qf_retries
        user_agent = "quran-video-platform/0.1 (+https://quran.foundation)"
    else:
        raise ValueError("cached full-surah audio requires a known provider")
    cache_path = cache_root / f"{audio.chapter_id:03d}.mp3"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    full_duration_ms: int | None = None
    if cache_path.exists() and not _cache_metadata_matches(cache_path, audio):
        cache_path.unlink(missing_ok=True)
        _cache_metadata_path(cache_path).unlink(missing_ok=True)
    if cache_path.exists():
        try:
            full_duration_ms = _probe_audio_duration_ms(cache_path)
        except Exception:
            cache_path.unlink(missing_ok=True)
            _cache_metadata_path(cache_path).unlink(missing_ok=True)
    if full_duration_ms is None:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        ) as client:
            await _download_url_with_retries(
                client,
                audio.url,
                cache_path,
                attempts=attempts,
            )
        full_duration_ms = _probe_audio_duration_ms(cache_path)
        _write_cache_metadata(cache_path, audio)
    render_audio = audio.model_copy(update={"duration_ms": full_duration_ms})
    start_ms, end_ms = _selected_audio_window(render_audio, request)
    if start_ms == 0:
        return cache_path, render_audio
    duration_seconds = max((end_ms - start_ms) / 1000, 0.001)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-i",
            str(cache_path),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-ar",
            "48000",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    _probe_audio_duration_ms(output_path)
    return output_path, render_audio


async def _download_and_concatenate_ayah_audio(
    audio: ChapterAudio,
    request: RenderRequest,
    output_path: Path,
) -> tuple[Path, ChapterAudio]:
    selected = [
        (timestamp, audio.audio_urls[index])
        for index, timestamp in enumerate(audio.verse_timestamps)
        if request.ayah_from <= timestamp.verse_number <= request.ayah_to
    ]
    if not selected:
        raise ValueError("selected ayah range has no audio URLs")
    output_path = output_path.with_suffix(".mp3")
    parts_dir = output_path.parent / "ayah-audio"
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_paths: list[Path] = []
    durations: list[int] = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0),
        follow_redirects=True,
        headers={"User-Agent": "quran-video-platform/0.1"},
    ) as client:
        for timestamp, url in selected:
            target = parts_dir / f"{timestamp.verse_number:03d}.mp3"
            await _download_url_with_retries(client, url, target)
            part_paths.append(target)
            durations.append(_probe_audio_duration_ms(target))
    list_path = parts_dir / "concat.txt"
    list_path.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in part_paths),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c:a",
            "libmp3lame",
            "-ar",
            "48000",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    adjusted = _retime_selected_audio(audio, [item[0] for item in selected], durations)
    return output_path, adjusted


def _probe_audio_duration_ms(path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not any(stream.get("codec_type") == "audio" for stream in streams):
        raise RuntimeError("downloaded audio file does not contain an audio stream")
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("downloaded audio has no positive duration")
    return max(1, round(duration * 1000))


def _selected_audio_window(audio: ChapterAudio, request: RenderRequest) -> tuple[int, int]:
    selected = [
        timestamp
        for timestamp in audio.verse_timestamps
        if request.ayah_from <= timestamp.verse_number <= request.ayah_to
    ]
    if not selected:
        raise ValueError("selected ayah range has no timestamps")
    selected.sort(key=lambda item: item.start_ms)
    start_ms = selected[0].start_ms
    if audio.provider == "mp3quran" and request.ayah_from == 1 and audio.intro_timing:
        start_ms = min(audio.intro_timing.start_ms, start_ms)
    return start_ms, selected[-1].end_ms


def _safe_cache_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "unknown"


def _cache_metadata_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(f"{cache_path.suffix}.json")


def _expected_cache_metadata(audio: ChapterAudio) -> dict[str, object]:
    return {
        "version": AUDIO_CACHE_METADATA_VERSION,
        "provider": audio.provider,
        "reciter_id": audio.reciter_id,
        "moshaf_id": audio.moshaf_id,
        "chapter_id": audio.chapter_id,
        "source_url": audio.url,
    }


def _cache_metadata_matches(cache_path: Path, audio: ChapterAudio) -> bool:
    metadata_path = _cache_metadata_path(cache_path)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = _expected_cache_metadata(audio)
    return all(metadata.get(key) == value for key, value in expected.items())


def _write_cache_metadata(cache_path: Path, audio: ChapterAudio) -> None:
    _cache_metadata_path(cache_path).write_text(
        json.dumps(_expected_cache_metadata(audio), sort_keys=True),
        encoding="utf-8",
    )


def _retry_after_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), 10.0)
    return min(0.75 * attempt, 4.0)


def _retime_selected_audio(
    audio: ChapterAudio,
    selected_timestamps: list[VerseTimestamp],
    durations: list[int],
) -> ChapterAudio:
    cursor = 0
    retimed: list[VerseTimestamp] = []
    for timestamp, duration in zip(selected_timestamps, durations, strict=True):
        word_count = max(len(timestamp.word_segments), 1)
        word_segments: list[WordSegment] = []
        for position in range(1, word_count + 1):
            start_ms = cursor + round((position - 1) * duration / word_count)
            end_ms = cursor + round(position * duration / word_count)
            word_segments.append(
                WordSegment(
                    verse_number=timestamp.verse_number,
                    word_position=position,
                    start_ms=start_ms,
                    end_ms=max(end_ms, start_ms + 1),
                )
            )
        retimed.append(
            VerseTimestamp(
                verse_number=timestamp.verse_number,
                start_ms=cursor,
                end_ms=cursor + duration,
                word_segments=word_segments,
            )
        )
        cursor += duration
    return audio.model_copy(
        update={
            "url": "alqurancloud://concatenated-selection",
            "audio_urls": [],
            "duration_ms": cursor,
            "verse_timestamps": retimed,
        }
    )


async def _run_render_job(job_id: str) -> None:
    settings = get_settings()
    store = JobStore(settings.sqlite_path)
    work_dir = Path(tempfile.mkdtemp(prefix=f"render-{job_id}-"))
    try:
        request = RenderRequest.model_validate(store.get_request(job_id))
        _ensure_not_canceled(store, job_id)
        store.update(job_id, status=RenderStatus.running.value, phase="validating", progress=5)
        data_mode = request.data_mode or settings.quran_video_data_mode
        request = request.model_copy(update={"data_mode": data_mode})
        job_settings = settings.model_copy(update={"quran_video_data_mode": data_mode})
        repository = QuranRepository(job_settings)
        request = await resolve_render_request_defaults(request, repository)
        store.update(job_id, request_json=request.model_dump(mode="json"))
        chapters = await repository.chapters()
        chapter = next(item for item in chapters if item.id == request.chapter_id)
        validate_ayah_range(chapter, request.ayah_from, request.ayah_to)
        _ensure_not_canceled(store, job_id)
        store.update(job_id, phase="fetching Quran data", progress=15)
        reciter = await repository.reciter_for_request(request.reciter_id, request.moshaf_id)
        verses = await repository.verses(request.chapter_id)
        selected_verses = [
            verse for verse in verses if request.ayah_from <= verse.verse_number <= request.ayah_to
        ]
        audio = await repository.chapter_audio(
            request.chapter_id, request.reciter_id, request.moshaf_id
        )
        compatibility = await repository.compatibility(
            request.chapter_id, request.reciter_id, request.moshaf_id
        )
        if not compatibility.compatible:
            raise ValueError(compatibility.reason or "reciter and surah are incompatible")
        stored_request = store.get_request(job_id)
        stored_request["resolved_audio_metadata"] = {
            "provider": audio.provider,
            "reciter_id": audio.reciter_id,
            "reciter_name": audio.reciter_name or reciter.english_name,
            "moshaf_id": audio.moshaf_id,
            "moshaf_name": audio.moshaf_name,
            "rewaya": audio.rewaya,
            "moshaf_type": audio.moshaf_type,
            "surah_number": audio.chapter_id,
            "timing_read_id": audio.timing_read_id,
            "timing_mapping_method": audio.timing_mapping_method,
            "timing_status": audio.timing_status.value if audio.timing_status else None,
        }
        store.update(job_id, request_json=stored_request)
        _ensure_not_canceled(store, job_id)
        store.update(job_id, phase="downloading audio", progress=30)
        audio_path, render_audio = await _download_audio(
            audio, request, work_dir / "recitation_audio"
        )
        if render_audio.provider in {"mp3quran", "quranfoundation"}:
            validation_timeline = build_range_timeline(
                chapter,
                reciter,
                verses,
                render_audio,
                request.ayah_from,
                request.ayah_to,
                request.include_bismillah,
            )
            validation_audio = render_audio.model_copy(
                update={
                    "duration_ms": validation_timeline.duration_ms,
                    "verse_timestamps": validation_timeline.timestamps,
                }
            )
            tolerance_ms = (
                settings.qf_max_timing_overflow_ms
                if render_audio.provider == "quranfoundation"
                else settings.mp3quran_max_timing_overflow_ms
            )
            minimum_coverage = (
                settings.qf_min_timing_coverage
                if render_audio.provider == "quranfoundation"
                else settings.mp3quran_min_timing_coverage
            )
            if render_audio.provider == "quranfoundation":
                validated_audio, compatibility = validate_audio_with_safe_fallback(
                    validation_audio,
                    selected_verses,
                    probed_duration_ms=validation_timeline.duration_ms,
                    tolerance_ms=tolerance_ms,
                    minimum_timing_coverage=minimum_coverage,
                )
                if not validated_audio.has_word_timing:
                    render_audio = audio_with_ayah_level_segments(render_audio)
            else:
                compatibility = validate_audio_compatibility(
                    validation_audio,
                    selected_verses,
                    probed_duration_ms=validation_timeline.duration_ms,
                    tolerance_ms=tolerance_ms,
                    minimum_timing_coverage=minimum_coverage,
                )
            if not compatibility.compatible:
                raise ValueError(compatibility.reason or "timing/audio mismatch")
        _ensure_not_canceled(store, job_id)
        store.update(job_id, phase="generating subtitles", progress=45)
        timeline = build_range_timeline(
            chapter,
            reciter,
            verses,
            render_audio,
            request.ayah_from,
            request.ayah_to,
            request.include_bismillah,
        )
        _ensure_not_canceled(store, job_id)
        store.update(job_id, phase="encoding", progress=55)
        output_dir = settings.renders_dir / job_id
        encode_started = time.monotonic()

        def progress_callback(position: float, duration: float) -> None:
            ratio = min(max(position / max(duration, 0.001), 0.0), 1.0)
            elapsed = max(time.monotonic() - encode_started, 0.001)
            eta = max((elapsed / max(ratio, 0.001)) - elapsed, 0.0)
            store.update(
                job_id,
                phase="encoding",
                progress=55 + ratio * 33,
                eta_seconds=eta,
            )

        result = render_video(
            timeline,
            request,
            output_dir,
            audio_path=audio_path,
            cancel_check=lambda: store.get(job_id).status == RenderStatus.canceled,
            progress_callback=progress_callback,
        )
        store.update(job_id, phase="validating output", progress=90)
        store.mark_complete(job_id, result.video_path, result.thumbnail_path)
    except RenderCanceled:
        output_dir = settings.renders_dir / job_id
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        store.update(job_id, status=RenderStatus.canceled.value, phase="canceled", progress=0)
    except Exception as error:
        LOGGER.exception("render job failed")
        if store.get(job_id).status == RenderStatus.canceled:
            store.update(job_id, status=RenderStatus.canceled.value, phase="canceled", progress=0)
        else:
            store.update(
                job_id,
                status=RenderStatus.failed.value,
                phase="failed",
                progress=0,
                error_summary=str(error)[:500],
            )
        output_dir = settings.renders_dir / job_id
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
