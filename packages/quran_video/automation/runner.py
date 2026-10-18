from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from worker.tasks import _download_audio

from quran_video.automation.checkpoint import (
    marked_description,
    pair_marker,
    persist_upload_checkpoint,
)
from quran_video.automation.reciters import completion_key, select_next_recitation
from quran_video.automation.state import AutomationStateStore, choose_background
from quran_video.backgrounds.release import download_random_release_background
from quran_video.config import get_settings
from quran_video.config.logging import redact_secret_text
from quran_video.config.reciters import require_bilingual_reciter
from quran_video.config.visual_style import load_visual_style
from quran_video.metadata import generate_metadata
from quran_video.models import (
    AutomationState,
    BackgroundMode,
    RenderRequest,
)
from quran_video.models.domain import (
    BadgeSettings,
    PendingPostUpload,
    YouTubeMetadata,
)
from quran_video.notifications import TelegramClient
from quran_video.quran.compatibility import build_range_timeline
from quran_video.quran.repository import QuranRepository
from quran_video.rendering.ffmpeg import render_video
from quran_video.rendering.media import list_backgrounds
from quran_video.youtube import YouTubeClient

LOGGER = logging.getLogger(__name__)
LOW_BACKGROUND_REMAINING_THRESHOLD = 10


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
        LOGGER.error(
            "automation failed | phase=%s reason=%s",
            context.get("phase", "unknown"),
            _safe_error(settings, error),
        )
        await _send_failure(settings, context, error)
        return 1


async def _run(settings, context: dict[str, str]) -> int:
    store = AutomationStateStore(Path("automation/state.json"))
    _log_step("loading automation state", path=str(store.path))
    state = store.load()
    dry_run = os.getenv("DRY_RUN", "false").casefold() == "true"
    auth_check_only = os.getenv("AUTH_CHECK_ONLY", "false").casefold() == "true"
    configured_reciter_query = os.getenv("AUTOMATION_RECITER_QUERY")
    _log_step(
        "automation mode",
        dry_run=dry_run,
        auth_check_only=auth_check_only,
        advance_state=os.getenv("ADVANCE_STATE", "false"),
        configured_reciter=configured_reciter_query or "ranked queue",
    )
    youtube: YouTubeClient | None = None
    if not dry_run or auth_check_only:
        context["phase"] = "verifying YouTube authorization"
        _log_step("preflight YouTube authorization")
        youtube = YouTubeClient(
            settings.youtube_client_id or "",
            settings.youtube_client_secret or "",
            settings.youtube_refresh_token or "",
            settings.youtube_channel_id or "",
        )
        youtube.verify_channel()
        _log_step("YouTube authorization verified")
    if auth_check_only:
        _log_step("YouTube authorization check complete")
        return 0
    if state.pending_post_upload and not dry_run:
        context["phase"] = "recovering post-upload checkpoint"
        _log_step("recovering post-upload checkpoint", video_id=state.pending_post_upload.video_id)
        if youtube is None:
            raise RuntimeError("YouTube authorization preflight did not initialize a client")
        recovered_metadata = YouTubeMetadata.model_validate(state.pending_post_upload.metadata)
        context["surah"] = str(state.pending_post_upload.surah_id or "unknown")
        context["reciter"] = state.pending_post_upload.reciter_key
        _finish_pending_post_upload(youtube, store, state)
        await _notify_recovered_upload(settings, state, recovered_metadata)
        _log_step("recovered existing upload; next render waits for the next interval")
        return 0
    repository = QuranRepository(settings)
    context["phase"] = "fetching Quran data"
    _log_step("fetching chapters")
    chapters = await repository.chapters()
    reciters = await repository.reciters()
    context["phase"] = "selecting ranked reciter and surah"
    selection = await select_next_recitation(
        repository.compatibility, reciters, state, configured_reciter_query
    )
    if selection is None:
        _log_step("all ranked reciters completed; no upload required")
        if not dry_run or os.getenv("ADVANCE_STATE", "false").casefold() == "true":
            store.save(state)
        return 0
    surah_id, reciter, selected_moshaf_id = selection
    context["surah"] = str(surah_id)
    _log_step(
        "picked ranked reciter-surah job", surah_id=surah_id, reciter=state.active_reciter_key
    )
    chapter = next(item for item in chapters if item.id == surah_id)
    _log_step(
        "selected chapter",
        surah_id=chapter.id,
        english=chapter.english_name,
        arabic=chapter.arabic_name,
        ayahs=chapter.verse_count,
    )
    render_reciter = require_bilingual_reciter(
        await repository.reciter_for_request(reciter.id, selected_moshaf_id)
    )
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
    pair_key = completion_key(state.active_reciter_key or reciter_key, surah_id)
    if not dry_run:
        if youtube is None:
            raise RuntimeError("YouTube authorization preflight did not initialize a client")
        existing_id = youtube.find_pair_video(pair_marker(pair_key))
        if existing_id:
            _log_step("pair already uploaded; recovering without rendering", video_id=existing_id)
            existing_metadata = generate_metadata(chapter, render_reciter, 1, chapter.verse_count)
            state.pending_post_upload = PendingPostUpload(
                video_id=existing_id,
                pair_key=pair_key,
                surah_id=surah_id,
                thumbnail_path="",
                reciter_key=reciter_key,
                metadata=existing_metadata.model_dump(),
            )
            persist_upload_checkpoint(store, state)
            _finish_pending_post_upload(youtube, store, state)
            return 0
    context["phase"] = "selecting background"
    background_path, background_id, state = _select_background(settings, state)
    _log_step("selected background", path=str(background_path), background_id=background_id)
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
            arabic_reciter=render_reciter.arabic_name,
            english_reciter=render_reciter.english_name,
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
    metadata = metadata.model_copy(
        update={
            "description": marked_description(metadata.description, pair_key),
        }
    )
    _log_step("metadata ready", title=metadata.title, tag_count=len(metadata.tags))
    video_url = "dry-run"
    playlist_title = metadata.playlist_title
    actual_status = "dry-run"
    background_inventory_warning = ""
    if not dry_run:
        context["phase"] = "uploading to YouTube"
        if youtube is None:
            raise RuntimeError("YouTube authorization preflight did not initialize a client")
        pending = PendingPostUpload(
            pair_key=pair_key,
            surah_id=surah_id,
            background_id=background_id,
            thumbnail_path=str(output.thumbnail_path),
            reciter_key=reciter_key,
            metadata=metadata.model_dump(),
        )
        video_id = _upload_reserved_pair(youtube, store, state, pending, output.video_path)
        _log_step("YouTube upload complete", video_id=video_id)
        context["phase"] = "post-upload YouTube steps"
        _log_step("setting YouTube thumbnail", thumbnail=str(output.thumbnail_path))
        youtube.set_thumbnail(video_id, output.thumbnail_path)
        _log_step("finding or creating YouTube playlist", playlist=metadata.playlist_title)
        playlist_key = state.active_reciter_key or reciter_key
        playlist_id = youtube.find_or_create_playlist(
            metadata,
            state.playlist_ids.get(playlist_key) or state.playlist_ids.get(reciter_key),
        )
        state.playlist_ids[playlist_key] = playlist_id
        state.playlist_ids[reciter_key] = playlist_id
        store.save(state)
        _log_step("adding video to playlist", playlist_id=playlist_id, video_id=video_id)
        youtube.add_to_playlist(playlist_id, video_id)
        actual_status = youtube.verify_video(video_id).get("privacyStatus", "unknown")
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        _log_step("verified uploaded video", url=video_url, visibility=actual_status)
        output.video_path.unlink(missing_ok=True)
        state.pending_post_upload = None
    if not dry_run or os.getenv("ADVANCE_STATE", "false").casefold() == "true":
        _log_step("saving automation success state", surah_id=surah_id)
        backgrounds_before_success = len(state.background_queue)
        store.mark_success(
            state,
            surah_id,
            {
                "surah": surah_id,
                "reciter": reciter_key,
                "video": video_url,
                "background": background_id,
            },
            background_id,
        )
        if not dry_run:
            background_inventory_warning = _background_inventory_warning(
                backgrounds_before_success,
                len(state.background_queue),
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
            f"{background_inventory_warning}"
        )
    else:
        _log_step("Telegram notification skipped", reason="TELEGRAM_BOT_TOKEN/CHAT_ID not set")
    _log_step("automation complete", status=actual_status, video=video_url)
    return 0


def _background_inventory_warning(previous_remaining: int, remaining: int) -> str:
    crossed_threshold = (
        previous_remaining >= LOW_BACKGROUND_REMAINING_THRESHOLD
        and remaining < LOW_BACKGROUND_REMAINING_THRESHOLD
    )
    if not crossed_threshold:
        return ""
    return (
        "\n\n⚠️ Low background inventory\n"
        f"Only {remaining} unused backgrounds remain in the current no-repeat cycle."
    )


def _select_background(settings, state: AutomationState) -> tuple[Path, str, AutomationState]:
    backgrounds = list_backgrounds()
    if backgrounds:
        selected_id, state = choose_background(state, [item.id for item in backgrounds])
        return settings.backgrounds_dir / selected_id, selected_id, state
    if os.getenv("GITHUB_ACTIONS") == "true":
        repository = os.getenv("GITHUB_REPOSITORY")
        if not repository:
            raise RuntimeError("GITHUB_REPOSITORY is required to download background release")
        selected = download_random_release_background(
            repository,
            settings.backgrounds_dir,
            settings.cache_dir / "background-release-download",
        )
        try:
            background_id = selected.relative_to(settings.backgrounds_dir).as_posix()
        except ValueError:
            background_id = selected.name
        return selected, background_id, state
    raise RuntimeError("no local or release-selected background is available")


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("AUTOMATION_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _log_step(message: str, **fields: object) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    LOGGER.info("%s%s", message, f" | {details}" if details else "")


def _upload_reserved_pair(
    youtube: YouTubeClient,
    store: AutomationStateStore,
    state: AutomationState,
    pending: PendingPostUpload,
    video_path: Path,
) -> str:
    if state.pending_post_upload is not None:
        raise RuntimeError("an upload reservation already exists; refusing another upload")
    if not pending.pair_key or pending.pair_key in state.completed_reciter_surahs:
        raise RuntimeError("pair is missing or already completed; refusing another upload")
    state.pending_post_upload = pending
    # A runner loss or lost YouTube acknowledgement now leaves a durable reservation.
    persist_upload_checkpoint(store, state)
    metadata = YouTubeMetadata.model_validate(pending.metadata)
    _log_step("uploading reserved pair to YouTube", pair=pending.pair_key)
    video_id = youtube.upload_video(video_path, metadata)
    pending.video_id = video_id
    persist_upload_checkpoint(store, state)
    return video_id


def _finish_pending_post_upload(
    youtube: YouTubeClient, store: AutomationStateStore, state: AutomationState
) -> None:
    pending = state.pending_post_upload
    if pending is None:
        return
    if pending.video_id is None:
        if not pending.pair_key:
            raise RuntimeError("unresolved upload reservation has no pair identifier")
        video_id = youtube.find_pair_video(pair_marker(pending.pair_key))
        if video_id is None:
            raise RuntimeError(
                "previous upload outcome is unresolved; reservation retained, refusing to re-upload"
            )
        pending.video_id = video_id
        persist_upload_checkpoint(store, state)
    metadata = YouTubeMetadata.model_validate(pending.metadata)
    thumbnail_path = Path(pending.thumbnail_path)
    if thumbnail_path.is_file():
        youtube.set_thumbnail(pending.video_id, thumbnail_path)
    playlist_key = (
        pending.pair_key.split("|", 1)[0]
        if pending.pair_key
        else state.active_reciter_key or pending.reciter_key
    )
    playlist_id = youtube.find_or_create_playlist(
        metadata,
        state.playlist_ids.get(playlist_key) or state.playlist_ids.get(pending.reciter_key),
    )
    state.playlist_ids[playlist_key] = playlist_id
    state.playlist_ids[pending.reciter_key] = playlist_id
    store.save(state)
    youtube.add_to_playlist(playlist_id, pending.video_id)
    youtube.verify_video(pending.video_id)
    surah_id = pending.surah_id
    if surah_id is None:
        # Old checkpoints predate the explicit surah field; the unconsumed queue
        # still starts with the uploaded candidate.
        if not state.surah_queue:
            raise RuntimeError("cannot recover legacy upload without its surah queue")
        surah_id = state.surah_queue[0]
    state.pending_post_upload = None
    store.mark_success(
        state,
        surah_id,
        {
            "surah": surah_id,
            "reciter": pending.reciter_key,
            "video": f"https://www.youtube.com/watch?v={pending.video_id}",
            "background": pending.background_id,
        },
        pending.background_id,
    )


async def _notify_recovered_upload(
    settings, state: AutomationState, metadata: YouTubeMetadata
) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    video_url = (state.last_success or {}).get("video", "unknown")
    try:
        await TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id).send_message(
            "Quran upload recovery completed\n\n"
            f"Video: {metadata.title}\nURL: {video_url}\nPlaylist: {metadata.playlist_title}\n"
            f"Workflow: {os.getenv('GITHUB_RUN_URL', 'local')}"
        )
    except Exception as error:
        # The upload and playlist are already complete; a notification outage
        # must not turn that successful recovery into another upload-failure alert.
        LOGGER.warning(
            "recovery completed but Telegram notification failed: %s", _safe_error(settings, error)
        )


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
