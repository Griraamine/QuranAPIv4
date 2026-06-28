from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

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
