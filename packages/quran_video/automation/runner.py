from __future__ import annotations

import asyncio
import logging
import os
import secrets
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from worker.tasks import _download_audio

from quran_video.automation.state import AutomationStateStore, choose_surah
from quran_video.backgrounds.release import download_random_release_background
from quran_video.config import get_settings
from quran_video.config.logging import redact_secret_text
from quran_video.config.visual_style import load_visual_style
from quran_video.metadata import generate_metadata
from quran_video.models import BackgroundMode, ChapterReciter, CompatibilityResult, RenderRequest
from quran_video.models.domain import (
    BadgeSettings,
    PendingPostUpload,
    YouTubeMetadata,
)
from quran_video.notifications import TelegramClient
from quran_video.quran.compatibility import build_range_timeline
from quran_video.quran.repository import QuranRepository
from quran_video.quran.text import normalize_lookup_text
from quran_video.rendering.ffmpeg import render_video
from quran_video.rendering.media import list_backgrounds
from quran_video.youtube import YouTubeClient

LOGGER = logging.getLogger(__name__)
DEFAULT_AUTOMATION_RECITER_QUERY = "minshawi"
MINSHAWI_ALIASES = (
    "minshawi",
    "menshawi",
    "menshawy",
    "manshawi",
    "منشاوي",
    "المنشاوي",
)


async def main_async() -> int:
    _configure_logging()
    settings = get_settings()
    context: dict[str, str] = {
        "phase": "starting",
        "surah": "not selected",
        "reciter": "not selected",
        "attempt": "1/1",
    }
    try:
        return await _run(settings, context)
    except Exception as error:
        await _send_failure(settings, context, error)
        return 1


async def _run(settings, context: dict[str, str]) -> int:
    store = AutomationStateStore(Path("automation/state.json"))
    _log_step("loading automation state", path=str(store.path))
    state = store.load()
    dry_run = os.getenv("DRY_RUN", "false").casefold() == "true"
    configured_reciter_query = os.getenv("AUTOMATION_RECITER_QUERY")
    _log_step(
        "automation mode",
        dry_run=dry_run,
        advance_state=os.getenv("ADVANCE_STATE", "false"),
        configured_reciter=configured_reciter_query or "cycle default",
    )
    if state.pending_post_upload and not dry_run:
        context["phase"] = "recovering post-upload checkpoint"
        _log_step("recovering post-upload checkpoint", video_id=state.pending_post_upload.video_id)
        _finish_pending_post_upload(settings, store, state)
    surah_id, state = choose_surah(state)
    context["surah"] = str(surah_id)
    _log_step("picked surah", surah_id=surah_id, cycle=state.cycle_number)
    reciter_query = _reciter_query_for_cycle(state.cycle_number, configured_reciter_query)
    _log_step("selected reciter policy", cycle=state.cycle_number, preferred_reciter=reciter_query)
    repository = QuranRepository(settings)
    context["phase"] = "fetching Quran data"
    _log_step("fetching chapters")
    chapters = await repository.chapters()
    chapter = next(item for item in chapters if item.id == surah_id)
    _log_step(
        "selected chapter",
        surah_id=chapter.id,
        english=chapter.english_name,
        arabic=chapter.arabic_name,
        ayahs=chapter.verse_count,
    )
    _log_step("fetching reciters")
    reciters = await repository.reciters()
    _log_step("reciters loaded", count=len(reciters))
    reciter, selected_moshaf_id = await _select_compatible_reciter(
        repository.compatibility,
        chapter.id,
        reciters,
        reciter_query,
    )
    render_reciter = await repository.reciter_for_request(reciter.id, selected_moshaf_id)
    reciter_key = (
        f"{reciter.display_key}:{selected_moshaf_id}" if selected_moshaf_id else reciter.display_key
    )
    context["reciter"] = reciter_key
    _log_step(
        "selected reciter",
        reciter_id=reciter.id,
        english=render_reciter.english_name,
        arabic=render_reciter.arabic_name,
        moshaf_id=selected_moshaf_id or "none",
        style=render_reciter.style.name,
    )
    context["phase"] = "selecting background"
    background_path = _select_background(settings)
    _log_step("selected background", path=str(background_path))
    _log_step("fetching verses")
    verses = await repository.verses(chapter.id)
    _log_step(
        "fetching chapter audio", reciter_id=reciter.id, moshaf_id=selected_moshaf_id or "none"
    )
    audio = await repository.chapter_audio(chapter.id, reciter.id, selected_moshaf_id)
    last_ayah = min(chapter.verse_count, max(verse.verse_number for verse in verses))
    _log_step("render range", ayah_from=1, ayah_to=last_ayah, include_bismillah=True)
    visual_style = load_visual_style()
    request = RenderRequest(
        reciter_id=reciter.id,
        moshaf_id=selected_moshaf_id,
        chapter_id=chapter.id,
        ayah_from=1,
        ayah_to=last_ayah,
        include_bismillah=True,
        background_mode=BackgroundMode.single,
        background_ids=[background_path.relative_to(settings.backgrounds_dir).as_posix()],
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
        data_mode=settings.quran_video_data_mode
        if settings.quran_video_data_mode in {"quranfoundation", "live", "alqurancloud", "mp3quran"}
        else "fixture",
    )
    context["phase"] = "downloading audio"
    with tempfile.TemporaryDirectory(prefix="quran-video-automation-") as temp_dir:
        _log_step("downloading audio")
        audio_path, render_audio = await _download_audio(
            audio,
            request,
            Path(temp_dir) / "recitation_audio",
        )
        _log_step(
            "audio ready",
            path=str(audio_path),
            duration_ms=render_audio.duration_ms,
            ayah_timestamps=len(render_audio.verse_timestamps),
        )
        context["phase"] = "building timeline"
        _log_step("building timeline")
        timeline = build_range_timeline(
            chapter, render_reciter, verses, render_audio, 1, request.ayah_to, True
        )
        _log_step("timeline ready", duration_ms=timeline.duration_ms, ayahs=len(timeline.verses))
        context["phase"] = "rendering"
        _log_step("rendering video")
        output = render_video(
            timeline,
            request,
            settings.renders_dir / "automation",
            audio_path=audio_path,
        )
        _log_step(
            "render complete",
            video=str(output.video_path),
            thumbnail=str(output.thumbnail_path),
            duration_seconds=f"{output.duration_seconds:.1f}",
        )
    _log_step("generating metadata")
    metadata = generate_metadata(chapter, render_reciter, 1, request.ayah_to)
    _log_step("metadata ready", title=metadata.title, tag_count=len(metadata.tags))
    video_url = "dry-run"
    playlist_title = metadata.playlist_title
    actual_status = "dry-run"
    if not dry_run:
        context["phase"] = "uploading to YouTube"
        _log_step("verifying YouTube channel")
        youtube = YouTubeClient(
            settings.youtube_client_id or "",
            settings.youtube_client_secret or "",
            settings.youtube_refresh_token or "",
            settings.youtube_channel_id or "",
        )
        youtube.verify_channel()
        _log_step("uploading video to YouTube", video=str(output.video_path))
        video_id = youtube.upload_video(output.video_path, metadata)
        _log_step("YouTube upload complete", video_id=video_id)
        state.pending_post_upload = PendingPostUpload(
            video_id=video_id,
            thumbnail_path=str(output.thumbnail_path),
            reciter_key=reciter_key,
            metadata=metadata.model_dump(),
        )
        store.save(state)
        context["phase"] = "post-upload YouTube steps"
        _log_step("setting YouTube thumbnail", thumbnail=str(output.thumbnail_path))
        youtube.set_thumbnail(video_id, output.thumbnail_path)
        _log_step("finding or creating YouTube playlist", playlist=metadata.playlist_title)
        playlist_id = youtube.find_or_create_playlist(metadata, state.playlist_ids.get(reciter_key))
        state.playlist_ids[reciter_key] = playlist_id
        _log_step("adding video to playlist", playlist_id=playlist_id, video_id=video_id)
        youtube.add_to_playlist(playlist_id, video_id)
        actual_status = youtube.verify_video(video_id).get("privacyStatus", "unknown")
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        _log_step("verified uploaded video", url=video_url, visibility=actual_status)
        output.video_path.unlink(missing_ok=True)
        state.pending_post_upload = None
    if not dry_run or os.getenv("ADVANCE_STATE", "false").casefold() == "true":
        _log_step("saving automation success state", surah_id=surah_id)
        store.mark_success(
            state,
            surah_id,
            {
                "surah": surah_id,
                "reciter": reciter_key,
                "video": video_url,
            },
        )
    if settings.telegram_bot_token and settings.telegram_chat_id:
        client = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        lead = "Quran video uploaded"
        if dry_run:
            lead = "Quran automation dry run completed"
        if actual_status == "private":
            lead = "Quran video uploaded with private visibility. Google API audit may be required"
        _log_step("sending Telegram success notification")
        await client.send_message(
            f"{lead}\n\nSurah: {chapter.english_name} | {chapter.arabic_name}\n"
            f"Reciter: {render_reciter.english_name} | {render_reciter.arabic_name}\n"
            f"Ayahs: 1–{request.ayah_to}\nVideo: {video_url}\nPlaylist: {playlist_title}\n"
            f"Visibility: {actual_status}\nDuration: {output.duration_seconds:.1f}s\n"
            f"Workflow: {os.getenv('GITHUB_RUN_URL', 'local')}"
        )
    else:
        _log_step("Telegram notification skipped", reason="TELEGRAM_BOT_TOKEN/CHAT_ID not set")
    _log_step("automation complete", status=actual_status, video=video_url)
    return 0


async def _select_compatible_reciter(
    compatibility: Callable[[int, str, str | None], Awaitable[CompatibilityResult]],
    chapter_id: int,
    reciters: list[ChapterReciter],
    preferred_query: str | None,
) -> tuple[ChapterReciter, str | None]:
    preferred_reciters = _preferred_reciters(reciters, preferred_query)
    if preferred_reciters:
        _log_step(
            "checking preferred reciter compatibility",
            preferred=preferred_query or "random",
            candidates=len(preferred_reciters),
        )
        compatible = await _compatible_reciter_options(
            compatibility,
            chapter_id,
            preferred_reciters,
            stop_after_first=False,
        )
        if compatible:
            selected = sorted(compatible, key=lambda item: _reciter_preference_score(*item))[0]
            _log_step(
                "preferred reciter is compatible",
                reciter_id=selected[0].id,
                english=selected[0].english_name,
                moshaf_id=selected[1] or "none",
            )
            return selected
        raise RuntimeError(
            f"preferred reciter {preferred_query!r} is not compatible with surah {chapter_id}"
        )

    _log_step("checking reciter compatibility", preferred="random", candidates=len(reciters))
    compatible = await _compatible_reciter_options(
        compatibility,
        chapter_id,
        reciters,
        stop_after_first=False,
    )
    if not compatible:
        raise RuntimeError(f"no compatible reciters for surah {chapter_id}")
    selected = secrets.SystemRandom().choice(compatible)
    _log_step(
        "random compatible reciter selected",
        compatible_count=len(compatible),
        reciter_id=selected[0].id,
        english=selected[0].english_name,
        moshaf_id=selected[1] or "none",
    )
    return selected


async def _compatible_reciter_options(
    compatibility: Callable[[int, str, str | None], Awaitable[CompatibilityResult]],
    chapter_id: int,
    reciters: list[ChapterReciter],
    *,
    stop_after_first: bool,
) -> list[tuple[ChapterReciter, str | None]]:
    compatible: list[tuple[ChapterReciter, str | None]] = []
    for reciter in reciters:
        moshaf_ids: list[str | None] = (
            [None] if not reciter.moshafs else [moshaf.id for moshaf in reciter.moshafs]
        )
        for moshaf_id in moshaf_ids:
            _log_step(
                "checking compatibility",
                reciter_id=reciter.id,
                english=reciter.english_name,
                moshaf_id=moshaf_id or "none",
            )
            result = await compatibility(chapter_id, reciter.id, moshaf_id)
            if result.compatible:
                compatible.append((reciter, moshaf_id))
                if stop_after_first:
                    return compatible
            else:
                _log_step(
                    "reciter incompatible",
                    reciter_id=reciter.id,
                    moshaf_id=moshaf_id or "none",
                    reason=result.reason or result.status or "unknown",
                )
    return compatible


def _preferred_reciters(
    reciters: list[ChapterReciter], preferred_query: str | None
) -> list[ChapterReciter]:
    query = normalize_lookup_text(preferred_query or "")
    if not query or query in {"random", "*", "any"}:
        return []
    aliases = _reciter_query_aliases(query)
    matches = [reciter for reciter in reciters if _reciter_matches(reciter, aliases)]
    return sorted(matches, key=lambda reciter: _reciter_preference_score(reciter, None))


def _reciter_query_for_cycle(cycle_number: int, configured_query: str | None) -> str:
    if configured_query and configured_query.strip():
        return configured_query.strip()
    if cycle_number <= 1:
        return DEFAULT_AUTOMATION_RECITER_QUERY
    return "random"


def _reciter_query_aliases(query: str) -> tuple[str, ...]:
    if query in {normalize_lookup_text(alias) for alias in MINSHAWI_ALIASES}:
        return tuple(normalize_lookup_text(alias) for alias in MINSHAWI_ALIASES)
    return (query,)


def _reciter_matches(reciter: ChapterReciter, aliases: tuple[str, ...]) -> bool:
    haystack = normalize_lookup_text(
        " ".join(
            [
                reciter.id,
                reciter.english_name,
                reciter.arabic_name,
                reciter.style.id,
                reciter.style.name,
            ]
        )
    )
    return any(alias and alias in haystack for alias in aliases)


def _reciter_preference_score(
    reciter: ChapterReciter, moshaf_id: str | None
) -> tuple[int, int, str]:
    haystack = normalize_lookup_text(
        " ".join(
            [
                reciter.id,
                reciter.english_name,
                reciter.arabic_name,
                reciter.style.id,
                reciter.style.name,
                moshaf_id or "",
            ]
        )
    )
    kids_penalty = 1 if "kids" in haystack or "repeat" in haystack else 0
    murattal_bonus = 0 if "murattal" in haystack else 1
    return (kids_penalty, murattal_bonus, reciter.english_name.casefold())


def _select_background(settings) -> Path:
    if os.getenv("GITHUB_ACTIONS") == "true":
        repository = os.getenv("GITHUB_REPOSITORY")
        if not repository:
            raise RuntimeError("GITHUB_REPOSITORY is required to download background release")
        return download_random_release_background(
            repository,
            settings.backgrounds_dir,
            settings.cache_dir / "background-release-download",
        )
    backgrounds = list_backgrounds()
    if not backgrounds:
        raise RuntimeError("no local or release-selected background is available")
    selected = secrets.SystemRandom().choice(backgrounds)
    return settings.backgrounds_dir / selected.id


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("AUTOMATION_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _log_step(message: str, **fields: object) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    LOGGER.info("%s%s", message, f" | {details}" if details else "")


def _finish_pending_post_upload(settings, store: AutomationStateStore, state) -> None:
    pending = state.pending_post_upload
    if pending is None:
        return
    youtube = YouTubeClient(
        settings.youtube_client_id or "",
        settings.youtube_client_secret or "",
        settings.youtube_refresh_token or "",
        settings.youtube_channel_id or "",
    )
    youtube.verify_channel()
    metadata = YouTubeMetadata.model_validate(pending.metadata)
    thumbnail_path = Path(pending.thumbnail_path)
    if thumbnail_path.exists():
        youtube.set_thumbnail(pending.video_id, thumbnail_path)
    playlist_id = youtube.find_or_create_playlist(
        metadata,
        state.playlist_ids.get(pending.reciter_key),
    )
    state.playlist_ids[pending.reciter_key] = playlist_id
    youtube.add_to_playlist(playlist_id, pending.video_id)
    youtube.verify_video(pending.video_id)
    state.pending_post_upload = None
    store.save(state)


async def _send_failure(settings, context: dict[str, str], error: Exception) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        _log_step(
            "Telegram failure notification skipped", reason="TELEGRAM_BOT_TOKEN/CHAT_ID not set"
        )
        return
    message = _safe_error(settings, error)
    _log_step(
        "sending Telegram failure notification",
        phase=context.get("phase", "unknown"),
        surah=context.get("surah", "not selected"),
        reciter=context.get("reciter", "not selected"),
    )
    client = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    await client.send_message(
        "Quran automation failed\n\n"
        f"Phase: {context.get('phase', 'unknown')}\n"
        f"Surah: {context.get('surah', 'not selected')}\n"
        f"Reciter: {context.get('reciter', 'not selected')}\n"
        "Error code: automation_failed\n"
        f"Reason: {message}\n"
        f"Attempt: {context.get('attempt', 'unknown')}\n"
        f"Workflow: {os.getenv('GITHUB_RUN_URL', 'local')}"
    )


def _safe_error(settings, error: Exception) -> str:
    text = redact_secret_text(str(error) or error.__class__.__name__)
    text = text.replace(str(settings.repo_root), "[repo]")
    return text[:500]


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
