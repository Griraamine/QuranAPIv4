from __future__ import annotations

import json
import subprocess
from unittest.mock import Mock

import pytest

from quran_video.automation import runner
from quran_video.automation.checkpoint import (
    marked_description,
    pair_marker,
)
from quran_video.automation.state import AutomationStateStore
from quran_video.models import AutomationState, YouTubeMetadata
from quran_video.models.domain import PendingPostUpload
from quran_video.youtube import YouTubeClient


@pytest.fixture(autouse=True)
def local_checkpoint(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def pending() -> PendingPostUpload:
    metadata = YouTubeMetadata(
        title="Taha",
        description=marked_description("Description", "sudais|20"),
        tags=[],
        hashtags=[],
        thumbnail_text_arabic="",
        thumbnail_text_english="",
        playlist_title="Sudais",
        playlist_description="",
    )
    return PendingPostUpload(
        pair_key="sudais|20",
        surah_id=20,
        reciter_key="3:murattal",
        thumbnail_path="",
        metadata=metadata.model_dump(),
    )


def state() -> AutomationState:
    return AutomationState(
        schema_version=2, active_reciter_key="sudais", reciter_queue=["sudais"], surah_queue=[20, 1]
    )


def test_upload_cannot_start_until_reservation_is_saved(tmp_path):
    store = AutomationStateStore(tmp_path / "state.json")
    youtube = Mock()

    def upload(_path, metadata):
        durable = store.load().pending_post_upload
        assert durable is not None and durable.pair_key == "sudais|20"
        assert durable.video_id is None
        assert pair_marker("sudais|20") in metadata.description
        return "created"

    youtube.upload_video.side_effect = upload
    assert (
        runner._upload_reserved_pair(youtube, store, state(), pending(), tmp_path / "video.mp4")
        == "created"
    )
    assert store.load().pending_post_upload.video_id == "created"


def test_failed_remote_reservation_prevents_any_upload(monkeypatch, tmp_path):
    store = AutomationStateStore(tmp_path / "state.json")
    youtube = Mock()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(subprocess, "run", Mock(return_value=Mock(returncode=1)))
    with pytest.raises(RuntimeError, match="checkpoint could not be published"):
        runner._upload_reserved_pair(youtube, store, state(), pending(), tmp_path / "video.mp4")
    youtube.upload_video.assert_not_called()


@pytest.mark.parametrize("found", [None, "created-before-response-was-lost"])
def test_lost_upload_response_never_starts_a_second_upload(tmp_path, found):
    store = AutomationStateStore(tmp_path / "state.json")
    youtube = Mock()
    youtube.upload_video.side_effect = TimeoutError("YouTube accepted video; response lost")
    with pytest.raises(TimeoutError):
        runner._upload_reserved_pair(youtube, store, state(), pending(), tmp_path / "video.mp4")
    recovered = store.load()
    assert recovered.pending_post_upload.video_id is None
    youtube.find_pair_video.return_value = found
    youtube.find_or_create_playlist.return_value = "playlist"
    if found:
        runner._finish_pending_post_upload(youtube, store, recovered)
        assert store.load().completed_reciter_surahs == ["sudais|20"]
        assert store.load().surah_queue == [1]
    else:
        with pytest.raises(RuntimeError, match="refusing to re-upload"):
            runner._finish_pending_post_upload(youtube, store, recovered)
        assert store.load().pending_post_upload is not None
        assert not store.load().completed_reciter_surahs
    assert youtube.upload_video.call_count == 1
    with pytest.raises(RuntimeError, match=r"reservation already exists|already completed"):
        runner._upload_reserved_pair(
            youtube, store, store.load(), pending(), tmp_path / "video.mp4"
        )
    assert youtube.upload_video.call_count == 1


def test_remote_video_id_checkpoint_failure_still_cannot_duplicate(monkeypatch, tmp_path):
    store = AutomationStateStore(tmp_path / "state.json")
    youtube = Mock()
    youtube.upload_video.return_value = "created"
    real_persist = runner.persist_upload_checkpoint
    attempts = 0

    def persist(store, current):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            # Simulate runner loss before the returned ID can be saved.
            raise RuntimeError("runner lost")
        real_persist(store, current)

    monkeypatch.setattr(runner, "persist_upload_checkpoint", persist)
    with pytest.raises(RuntimeError, match="runner lost"):
        runner._upload_reserved_pair(youtube, store, state(), pending(), tmp_path / "video.mp4")
    assert store.load().pending_post_upload.video_id is None
    youtube.find_pair_video.return_value = "created"
    youtube.find_or_create_playlist.return_value = "playlist"
    runner._finish_pending_post_upload(youtube, store, store.load())
    assert store.load().completed_reciter_surahs == ["sudais|20"]
    assert youtube.upload_video.call_count == 1


def test_completed_pair_cannot_enter_upload_even_if_called_directly(tmp_path):
    current = state()
    current.completed_reciter_surahs = ["sudais|20"]
    youtube = Mock()
    with pytest.raises(RuntimeError, match="already completed"):
        runner._upload_reserved_pair(
            youtube,
            AutomationStateStore(tmp_path / "s.json"),
            current,
            pending(),
            tmp_path / "video.mp4",
        )
    youtube.upload_video.assert_not_called()


def test_git_remote_contains_reservation_before_upload(monkeypatch, tmp_path):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"

    def git(*args, cwd=None):
        return subprocess.run(
            ["git", *map(str, args)], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "--bare", remote)
    git("clone", remote, checkout)
    git("config", "user.name", "Test", cwd=checkout)
    git("config", "user.email", "test@example.com", cwd=checkout)
    store = AutomationStateStore(checkout / "automation/state.json")
    current = state()
    store.save(current)
    git("add", ".", cwd=checkout)
    git("commit", "-m", "initial", cwd=checkout)
    git("push", "-u", "origin", "HEAD", cwd=checkout)
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    youtube = Mock()

    def upload(*_args):
        # Inspect the remote directly, not the temporary runner's working copy.
        saved = json.loads(git("--git-dir", remote, "show", "HEAD:automation/state.json"))
        assert saved["pending_post_upload"]["pair_key"] == "sudais|20"
        assert saved["pending_post_upload"]["video_id"] is None
        return "created"

    youtube.upload_video.side_effect = upload
    runner._upload_reserved_pair(youtube, store, current, pending(), tmp_path / "video.mp4")
    saved = json.loads(git("--git-dir", remote, "show", "HEAD:automation/state.json"))
    assert saved["pending_post_upload"]["video_id"] == "created"


def youtube_with_pages(pages, videos):
    youtube = YouTubeClient.__new__(YouTubeClient)
    youtube.channel_id = "target"
    youtube.service = Mock()
    youtube.service.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "uploads"}}}],
    }
    youtube.service.playlistItems.return_value.list.return_value.execute.side_effect = pages
    youtube.service.videos.return_value.list.return_value.execute.side_effect = videos
    return youtube


def video(video_id, pair="sudais|20", channel="target"):
    return {"id": video_id, "snippet": {"channelId": channel, "description": pair_marker(pair)}}


def test_pair_lookup_scans_pages_and_matches_exact_pair():
    youtube = youtube_with_pages(
        [
            {"items": [{"contentDetails": {"videoId": "other"}}], "nextPageToken": "next"},
            {"items": [{"contentDetails": {"videoId": "match"}}]},
        ],
        [{"items": [video("other", "sudais|200")]}, {"items": [video("match")]}],
    )
    assert youtube.find_pair_video(pair_marker("sudais|20")) == "match"
    assert youtube.service.playlistItems.return_value.list.call_args.kwargs["pageToken"] == "next"


def test_multiple_existing_videos_fail_closed():
    youtube = youtube_with_pages(
        [
            {"items": [{"contentDetails": {"videoId": "a"}}, {"contentDetails": {"videoId": "b"}}]},
        ],
        [{"items": [video("a"), video("b")]}],
    )
    with pytest.raises(RuntimeError, match="multiple videos already exist"):
        youtube.find_pair_video(pair_marker("sudais|20"))


def test_lookup_error_is_never_treated_as_no_match():
    youtube = youtube_with_pages([], [])
    youtube.service.channels.return_value.list.return_value.execute.side_effect = RuntimeError(
        "outage"
    )
    with pytest.raises(RuntimeError, match="outage"):
        youtube.find_pair_video(pair_marker("sudais|20"))


def test_playlist_recovery_does_not_insert_existing_video():
    youtube = YouTubeClient.__new__(YouTubeClient)
    youtube.service = Mock()
    youtube.service.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "existing"}]
    }
    youtube.add_to_playlist("playlist", "video")
    youtube.service.playlistItems.return_value.insert.assert_not_called()


def test_marker_survives_long_arabic_descriptions():
    description = marked_description("سورة" * 3000, "sudais|20")
    assert len(description.encode("utf-8")) <= 5000
    assert pair_marker("sudais|20") in description.splitlines()


async def test_existing_pair_is_recovered_before_rendering(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from quran_video.models import Chapter, ChapterReciter, CompatibilityResult, RecitationStyle

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv("AUTH_CHECK_ONLY", raising=False)
    monkeypatch.delenv("AUTOMATION_RECITER_QUERY", raising=False)
    voice = ChapterReciter(
        id="3",
        english_name="Sudais",
        arabic_name="Sudais",
        provider="quranfoundation",
        style=RecitationStyle(id="murattal", name="Murattal"),
    )
    chapter = Chapter(id=20, arabic_name="طه", english_name="Taha", verse_count=135)
    repository = Mock()
    repository.chapters = AsyncMock(return_value=[chapter])
    repository.reciters = AsyncMock(return_value=[voice])
    repository.reciter_for_request = AsyncMock(return_value=voice)
    repository.compatibility = AsyncMock(
        return_value=CompatibilityResult(
            chapter_id=20,
            reciter_id="3",
            compatible=True,
        )
    )
    monkeypatch.setattr(runner, "QuranRepository", Mock(return_value=repository))
    monkeypatch.setattr(
        runner,
        "generate_metadata",
        Mock(return_value=YouTubeMetadata.model_validate(pending().metadata)),
    )
    renderer = Mock(side_effect=AssertionError("existing pair must not render again"))
    monkeypatch.setattr(runner, "render_video", renderer)
    youtube = Mock()
    youtube.find_pair_video.return_value = "already-uploaded"
    youtube.find_or_create_playlist.return_value = "playlist"
    monkeypatch.setattr(runner, "YouTubeClient", Mock(return_value=youtube))
    store = AutomationStateStore(tmp_path / "automation/state.json")
    store.save(state())
    settings = SimpleNamespace(
        youtube_client_id="client",
        youtube_client_secret="secret",
        youtube_refresh_token="refresh",
        youtube_channel_id="channel",
    )
    assert await runner._run(settings, {}) == 0
    renderer.assert_not_called()
    youtube.upload_video.assert_not_called()
    assert store.load().completed_reciter_surahs == ["sudais|20"]


def test_metadata_updates_preserve_pair_marker(monkeypatch):
    from scripts import update_youtube_metadata as updater

    monkeypatch.setattr(
        updater,
        "generate_metadata",
        Mock(return_value=YouTubeMetadata.model_validate(pending().metadata)),
    )
    youtube = Mock()
    match = Mock(video_id="video", chapter=Mock(), reciter=Mock(), ayah_from=1, ayah_to=135)
    updater._update_video(youtube, {"snippet": {"description": pair_marker("sudais|20")}}, match)
    body = youtube.service.videos.return_value.update.call_args.kwargs["body"]
    assert pair_marker("sudais|20") in body["snippet"]["description"].splitlines()
