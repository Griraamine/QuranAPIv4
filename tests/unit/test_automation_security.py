from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.auth.exceptions import RefreshError
from PIL import Image

from quran_video.automation import runner as automation_runner
from quran_video.automation.schedule import AUTOMATION_SCHEDULE_CRON, should_run_for_schedule
from quran_video.automation.state import (
    AutomationStateStore,
    choose_background,
    choose_surah,
    load_state,
    save_state,
)
from quran_video.backgrounds.release import (
    BackgroundManifestEntry,
    build_background_release,
    extract_manifest_entry,
)
from quran_video.config.logging import redact_secret_text
from quran_video.models import AutomationState
from quran_video.youtube import classify_google_error, normalize_playlist_title
from quran_video.youtube.client import refresh_youtube_credentials


def test_surah_queue_no_repeat_and_schedule_gating(tmp_path: Path) -> None:
    state = AutomationState(surah_queue=[5, 6], cycle_number=3)
    chosen, next_state = choose_surah(state)
    assert chosen == 5
    save_state(tmp_path / "state.json", next_state)
    assert load_state(tmp_path / "state.json").surah_queue == [5, 6]
    assert should_run_for_schedule(
        "schedule", AUTOMATION_SCHEDULE_CRON, datetime(2026, 1, 1, 0, 37, tzinfo=UTC)
    )
    assert should_run_for_schedule(
        "schedule", AUTOMATION_SCHEDULE_CRON, datetime(2026, 1, 1, 18, 37, tzinfo=UTC)
    )
    assert not should_run_for_schedule(
        "schedule", "0 * * * *", datetime(2026, 1, 1, 6, 37, tzinfo=UTC)
    )


def test_background_queue_no_repeat_until_consumed(tmp_path: Path) -> None:
    state = AutomationState()
    first, state = choose_background(state, ["a.jpg", "b.jpg", "c.jpg"])
    assert first in {"a.jpg", "b.jpg", "c.jpg"}
    assert sorted(state.background_queue) == ["a.jpg", "b.jpg", "c.jpg"]

    store = AutomationStateStore(tmp_path / "state.json")
    state = store.mark_success(state, surah_id=1, payload={}, background_id=first)
    second, state = choose_background(state, ["a.jpg", "b.jpg", "c.jpg"])

    assert second != first
    state = store.mark_success(state, surah_id=2, payload={}, background_id=second)
    third, state = choose_background(state, ["a.jpg", "b.jpg", "c.jpg"])

    assert third not in {first, second}
    state = store.mark_success(state, surah_id=3, payload={}, background_id=third)
    next_cycle, state = choose_background(state, ["a.jpg", "b.jpg", "c.jpg"])

    assert next_cycle in {"a.jpg", "b.jpg", "c.jpg"}


def test_secret_redaction_and_retry_classification(monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "secret-refresh")
    assert "secret-refresh" not in redact_secret_text("token secret-refresh Bearer abc.def")
    assert classify_google_error(RuntimeError("temporary backend")) == "retryable"


def test_refresh_token_failure_has_actionable_safe_guidance() -> None:
    credentials = Mock()
    credentials.refresh.side_effect = RefreshError("invalid_grant: revoked token")

    with pytest.raises(RuntimeError) as error:
        refresh_youtube_credentials(credentials)

    message = str(error.value)
    assert "In production" in message
    assert "YOUTUBE_REFRESH_TOKEN" in message
    assert "revoked token" not in message


async def test_production_authorization_fails_before_quran_fetch_or_render(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setattr(
        automation_runner.AutomationStateStore,
        "load",
        lambda _store: AutomationState(),
    )
    rejected_client = Mock(side_effect=RuntimeError("rejected before render"))
    repository = Mock(side_effect=AssertionError("Quran data must not load before preflight"))
    monkeypatch.setattr(automation_runner, "YouTubeClient", rejected_client)
    monkeypatch.setattr(automation_runner, "QuranRepository", repository)
    settings = SimpleNamespace(
        youtube_client_id="client",
        youtube_client_secret="secret",
        youtube_refresh_token="refresh",
        youtube_channel_id="channel",
    )

    with pytest.raises(RuntimeError, match="rejected before render"):
        await automation_runner._run(settings, {})

    rejected_client.assert_called_once_with("client", "secret", "refresh", "channel")
    repository.assert_not_called()


async def test_auth_check_only_verifies_dry_run_secret_without_rendering(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("AUTH_CHECK_ONLY", "true")
    monkeypatch.setattr(
        automation_runner.AutomationStateStore,
        "load",
        lambda _store: AutomationState(),
    )
    client = Mock()
    client_factory = Mock(return_value=client)
    repository = Mock(side_effect=AssertionError("auth-only check must not load Quran data"))
    monkeypatch.setattr(automation_runner, "YouTubeClient", client_factory)
    monkeypatch.setattr(automation_runner, "QuranRepository", repository)
    settings = SimpleNamespace(
        youtube_client_id="client",
        youtube_client_secret="secret",
        youtube_refresh_token="refresh",
        youtube_channel_id="channel",
    )

    assert await automation_runner._run(settings, {}) == 0

    client.verify_channel.assert_called_once_with()
    repository.assert_not_called()


def test_playlist_normalized_matching() -> None:
    assert normalize_playlist_title(
        " Reciter | قارئ Quran Recitations "
    ) == normalize_playlist_title("reciter قارئ quran recitations")


def test_background_release_and_safe_extraction(tmp_path: Path) -> None:
    source = tmp_path / "backgrounds"
    source.mkdir()
    Image.new("RGB", (32, 32), (20, 30, 40)).save(source / "a.jpg")
    manifest_path = build_background_release(
        source, tmp_path / "release", max_part_bytes=10_000_000
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = BackgroundManifestEntry.model_validate(manifest["entries"][0])
    extracted = extract_manifest_entry(
        tmp_path / "release" / entry.zip_part, entry, tmp_path / "out"
    )
    assert extracted.exists()

    bad_zip = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as archive:
        archive.writestr("../evil.jpg", b"bad")
    bad_entry = entry.model_copy(update={"relative_path": "../evil.jpg", "zip_part": "bad.zip"})
    try:
        extract_manifest_entry(bad_zip, bad_entry, tmp_path / "bad-out")
    except ValueError as error:
        assert "escapes" in str(error)
    else:
        raise AssertionError("path traversal was not rejected")
