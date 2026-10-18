from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from quran_video.automation import runner
from quran_video.automation.state import AutomationStateStore
from quran_video.models import AutomationState, YouTubeMetadata
from quran_video.models.domain import PendingPostUpload
from quran_video.youtube import YouTubeClient


def error(status, reason):
    return HttpError(
        httplib2.Response({"status": str(status)}),
        json.dumps(
            {
                "error": {"message": reason, "errors": [{"reason": reason}]},
            }
        ).encode(),
    )


def client(monkeypatch):
    youtube = YouTubeClient.__new__(YouTubeClient)
    youtube.service = Mock()
    sleep = Mock()
    monkeypatch.setattr("quran_video.youtube.client.time.sleep", sleep)
    return youtube, youtube.service.playlistItems.return_value, sleep


def test_new_playlist_404_retries_membership_without_inserting_early(monkeypatch):
    youtube, items, sleep = client(monkeypatch)
    items.list.return_value.execute.side_effect = [error(404, "playlistNotFound"), {"items": []}]
    youtube.add_to_playlist("new-playlist", "existing-video")
    assert items.list.call_count == 2
    assert items.insert.call_count == 1
    sleep.assert_called_once_with(1)


@pytest.mark.parametrize("status,reason", [(409, "SERVICE_UNAVAILABLE"), (503, "backendError")])
def test_temporary_insert_failure_rechecks_then_retries_same_video(monkeypatch, status, reason):
    youtube, items, sleep = client(monkeypatch)
    items.list.return_value.execute.return_value = {"items": []}
    items.insert.return_value.execute.side_effect = [error(status, reason), {}]
    youtube.add_to_playlist("playlist", "video")
    assert items.list.call_count == items.insert.call_count == 2
    sleep.assert_called_once_with(1)


def test_lost_insert_response_does_not_insert_again_if_membership_exists(monkeypatch):
    youtube, items, _sleep = client(monkeypatch)
    items.list.return_value.execute.side_effect = [{"items": []}, {"items": [{"id": "inserted"}]}]
    items.insert.return_value.execute.side_effect = ConnectionResetError("response lost")
    youtube.add_to_playlist("playlist", "video")
    assert items.insert.call_count == 1
    assert items.list.call_count == 2


def test_persistent_missing_playlist_has_bounded_retries(monkeypatch):
    youtube, items, sleep = client(monkeypatch)
    items.list.return_value.execute.side_effect = error(404, "playlistNotFound")
    with pytest.raises(HttpError):
        youtube.add_to_playlist("deleted-playlist", "video")
    assert items.list.call_count == 5
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2, 4, 8]
    items.insert.assert_not_called()


def test_auth_failure_is_not_retried_or_hidden(monkeypatch):
    youtube, items, sleep = client(monkeypatch)
    items.list.return_value.execute.side_effect = error(403, "forbidden")
    with pytest.raises(HttpError):
        youtube.add_to_playlist("playlist", "video")
    sleep.assert_not_called()
    items.insert.assert_not_called()


def metadata():
    return YouTubeMetadata(
        title="Taha",
        description="",
        tags=[],
        hashtags=[],
        thumbnail_text_arabic="",
        thumbnail_text_english="",
        playlist_title="Alafasy",
        playlist_description="",
    )


def test_recovery_reuses_performer_playlist_and_persists_it_before_insertion(tmp_path):
    current = AutomationState(
        schema_version=2,
        active_reciter_key="alafasy",
        surah_queue=[20, 1],
        playlist_ids={"alafasy": "existing-performer-playlist"},
        pending_post_upload=PendingPostUpload(
            video_id="already-uploaded",
            surah_id=20,
            pair_key="alafasy|20",
            reciter_key="173:murattal",
            thumbnail_path="",
            metadata=metadata().model_dump(),
        ),
    )
    store = AutomationStateStore(tmp_path / "state.json")
    youtube = Mock()
    youtube.find_or_create_playlist.return_value = "existing-performer-playlist"
    youtube.add_to_playlist.side_effect = RuntimeError("temporarily unavailable")
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        runner._finish_pending_post_upload(youtube, store, current)
    youtube.find_or_create_playlist.assert_called_once_with(
        metadata(), "existing-performer-playlist"
    )
    saved = store.load()
    assert saved.playlist_ids["173:murattal"] == "existing-performer-playlist"
    assert saved.pending_post_upload.video_id == "already-uploaded"
    assert saved.surah_queue == [20, 1]
    youtube.upload_video.assert_not_called()


async def test_successful_recovery_sends_clear_success_notification(monkeypatch):
    telegram = Mock(send_message=AsyncMock())
    monkeypatch.setattr(runner, "TelegramClient", Mock(return_value=telegram))
    settings = SimpleNamespace(
        telegram_bot_token="test-token", telegram_chat_id="test-chat", repo_root="/test"
    )
    current = AutomationState(last_success={"video": "https://www.youtube.com/watch?v=existing"})
    await runner._notify_recovered_upload(settings, current, metadata())
    text = telegram.send_message.call_args.args[0]
    assert "recovery completed" in text
    assert "watch?v=existing" in text
    assert "Alafasy" in text


async def test_notification_outage_does_not_fail_a_completed_recovery(monkeypatch):
    telegram = Mock(
        send_message=AsyncMock(side_effect=RuntimeError("temporary notification outage"))
    )
    monkeypatch.setattr(runner, "TelegramClient", Mock(return_value=telegram))
    settings = SimpleNamespace(
        telegram_bot_token="test-token", telegram_chat_id="test-chat", repo_root="/test"
    )
    await runner._notify_recovered_upload(settings, AutomationState(), metadata())
