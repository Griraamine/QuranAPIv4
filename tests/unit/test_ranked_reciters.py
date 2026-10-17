from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from quran_video.automation import reciters as selection
from quran_video.automation.reciters import completion_key, select_next_recitation
from quran_video.automation.runner import _finish_pending_post_upload
from quran_video.automation.state import AutomationStateStore, load_state, save_state
from quran_video.models import AutomationState, ChapterReciter, CompatibilityResult, RecitationStyle
from quran_video.models.domain import PendingPostUpload, YouTubeMetadata


def reciter(reciter_id: str, name: str = "Reciter") -> ChapterReciter:
    return ChapterReciter(
        id=reciter_id,
        english_name=name,
        arabic_name=name,
        provider="quranfoundation",
        style=RecitationStyle(id="murattal", name="Murattal"),
    )


async def compatible(chapter_id: int, reciter_id: str, moshaf_id: str | None):
    return CompatibilityResult(chapter_id=chapter_id, reciter_id=reciter_id, compatible=True)


async def test_complete_all_114_before_next_reciter_across_restarts(tmp_path: Path):
    catalog = [reciter("13", "Saad al-Ghamdi"), reciter("3", "Sudais")]
    path = tmp_path / "state.json"
    state = AutomationState()
    store = AutomationStateStore(path)
    uploaded = set()
    for _ in range(114):
        chosen = await select_next_recitation(compatible, catalog, state)
        assert chosen is not None
        chapter_id, voice, _ = chosen
        assert voice.id == "3"
        assert chapter_id not in uploaded
        uploaded.add(chapter_id)
        store.mark_success(state, chapter_id, {"surah": chapter_id, "reciter": voice.display_key})
        state = store.load()
    assert uploaded == set(range(1, 115))
    chosen = await select_next_recitation(compatible, catalog, state)
    assert chosen is not None and chosen[1].id == "13"
    assert len(state.completed_reciter_surahs) == 114


async def test_completion_never_reinitializes_exhausted_queue(tmp_path: Path):
    catalog = [reciter("3")]
    state = AutomationState(
        schema_version=2, reciter_queue=["sudais"], active_reciter_key="sudais", surah_queue=[114]
    )
    store = AutomationStateStore(tmp_path / "state.json")
    chosen = await select_next_recitation(compatible, catalog, state)
    assert chosen is not None
    store.mark_success(state, 114, {})
    assert await select_next_recitation(compatible, catalog, store.load()) is None
    state = store.load()
    assert await select_next_recitation(compatible, catalog, state) is None
    store.save(state)
    assert await select_next_recitation(compatible, catalog, store.load()) is None


async def test_failure_retries_same_candidate_without_advancing(tmp_path: Path):
    catalog = [reciter("3"), reciter("13")]
    state = AutomationState(
        schema_version=2,
        reciter_queue=["sudais", "ghamdi"],
        active_reciter_key="sudais",
        surah_queue=[20, 1],
    )
    store = AutomationStateStore(tmp_path / "state.json")
    store.save(state)
    first = await select_next_recitation(compatible, catalog, state)
    # Render/upload failed; there was no mark_success call.
    second = await select_next_recitation(compatible, catalog, store.load())
    assert first == second
    assert store.load().surah_queue == [20, 1]
    assert not store.load().completed_reciter_surahs


async def test_legacy_migration_does_not_assign_random_queue_to_first_reciter(monkeypatch):
    monkeypatch.setattr(selection, "shuffled_surah_queue", lambda: [1, 20, 15])
    state = AutomationState(
        surah_queue=[15],
        cycle_number=2,
        background_queue=["bg.jpg"],
        last_success={"surah": 20, "reciter": "5:murattal"},
    )
    chosen = await select_next_recitation(compatible, [reciter("3"), reciter("5")], state)
    assert chosen is not None and chosen[0] == 1 and chosen[1].id == "3"
    assert state.schema_version == 2
    assert "rifai|20" in state.completed_reciter_surahs
    assert state.background_queue == ["bg.jpg"]
    assert state.surah_queue == [1, 20, 15]


async def test_variants_share_completion_and_stable_preference():
    state = AutomationState(
        schema_version=2,
        reciter_queue=["alafasy"],
        active_reciter_key="alafasy",
        surah_queue=[20, 1],
        completed_reciter_surahs=["alafasy|20"],
    )
    chosen = await select_next_recitation(compatible, [reciter("173"), reciter("7")], state)
    assert chosen is not None and chosen[0] == 1 and chosen[1].id == "7"


@pytest.mark.parametrize(
    "reason",
    [
        "Quran.Foundation audio failed with HTTP 404",
        "Quran.Foundation audio response has no ayah timestamps",
    ],
)
async def test_unavailable_surah_is_recorded_then_reciter_continues(reason):
    async def check(chapter_id, reciter_id, moshaf_id):
        return CompatibilityResult(
            chapter_id=chapter_id,
            reciter_id=reciter_id,
            compatible=chapter_id != 20,
            reason=reason,
        )

    state = AutomationState(
        schema_version=2,
        reciter_queue=["sudais", "ghamdi"],
        active_reciter_key="sudais",
        surah_queue=[20, 1],
    )
    chosen = await select_next_recitation(check, [reciter("3"), reciter("13")], state)
    assert chosen is not None and chosen[0] == 1 and chosen[1].id == "3"
    assert "sudais|20" in state.skipped_reciter_surahs


@pytest.mark.parametrize(
    "reason",
    [
        "Quran.Foundation audio failed with HTTP 503",
        "Quran.Foundation audio request failed",
        "Quran.Foundation OAuth failed with HTTP 401",
    ],
)
async def test_provider_outage_never_skips_surah_or_changes_reciter(reason):
    async def unavailable(chapter_id, reciter_id, moshaf_id):
        return CompatibilityResult(
            chapter_id=chapter_id, reciter_id=reciter_id, compatible=False, reason=reason
        )

    state = AutomationState(
        schema_version=2,
        reciter_queue=["sudais", "ghamdi"],
        active_reciter_key="sudais",
        surah_queue=[20, 1],
    )
    with pytest.raises(RuntimeError, match="cannot check"):
        await select_next_recitation(unavailable, [reciter("3"), reciter("13")], state)
    assert state.reciter_queue == ["sudais", "ghamdi"]
    assert state.surah_queue == [20, 1]
    assert not state.skipped_reciter_surahs


async def test_missing_active_reciter_fails_without_losing_progress():
    state = AutomationState(
        schema_version=2,
        reciter_queue=["sudais", "ghamdi"],
        active_reciter_key="sudais",
        surah_queue=[20],
    )
    with pytest.raises(RuntimeError, match="missing from catalog"):
        await select_next_recitation(compatible, [reciter("13")], state)
    assert state.reciter_queue == ["sudais", "ghamdi"]


async def test_random_override_is_rejected():
    with pytest.raises(ValueError, match="random reciters are disabled"):
        await select_next_recitation(compatible, [reciter("3")], AutomationState(), "random")


async def test_selection_preview_does_not_write_state(tmp_path: Path):
    path = tmp_path / "state.json"
    save_state(path, AutomationState(surah_queue=[20]))
    before = path.read_bytes()
    await select_next_recitation(compatible, [reciter("3")], load_state(path))
    assert path.read_bytes() == before


def test_recovery_advances_uploaded_surah_once_and_preserves_next_reciter(tmp_path: Path):
    metadata = YouTubeMetadata(
        title="Taha",
        description="",
        tags=[],
        hashtags=[],
        thumbnail_text_arabic="",
        thumbnail_text_english="",
        playlist_title="Sudais",
        playlist_description="",
    )
    state = AutomationState(
        schema_version=2,
        reciter_queue=["sudais", "ghamdi"],
        active_reciter_key="sudais",
        surah_queue=[20, 1],
        background_queue=["bg.jpg", "next.jpg"],
        pending_post_upload=PendingPostUpload(
            video_id="existing",
            surah_id=20,
            background_id="bg.jpg",
            thumbnail_path=str(tmp_path / "missing.jpg"),
            reciter_key="3:murattal",
            metadata=metadata.model_dump(),
        ),
    )
    youtube = Mock()
    youtube.find_or_create_playlist.return_value = "playlist"
    store = AutomationStateStore(tmp_path / "state.json")
    _finish_pending_post_upload(youtube, store, state)
    _finish_pending_post_upload(youtube, store, store.load())
    youtube.upload_video.assert_not_called()
    youtube.add_to_playlist.assert_called_once_with("playlist", "existing")
    recovered = store.load()
    assert recovered.surah_queue == [1]
    assert recovered.completed_reciter_surahs == [completion_key("sudais", 20)]
    assert recovered.reciter_queue == ["sudais", "ghamdi"]
    assert recovered.background_queue == ["next.jpg"]
    assert recovered.pending_post_upload is None
    assert recovered.last_success_at


@pytest.mark.parametrize(
    "pending,old_success,expected",
    [
        (False, False, False),
        (False, True, True),
        (True, False, True),
    ],
)
def test_actual_workflow_gate_respects_success_time_and_pending_checkpoint(
    tmp_path: Path, pending, old_success, expected
):
    workflow = yaml.safe_load(Path(".github/workflows/daily-quran-video.yml").read_text())
    steps = workflow["jobs"]["render-and-upload"]["steps"]
    gate = next(s for s in steps if s.get("id") == "gate")["run"]
    script = gate.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    (tmp_path / "automation").mkdir()
    from datetime import UTC, datetime, timedelta

    last = datetime.now(UTC) - timedelta(hours=19 if old_success else 1)
    (tmp_path / "automation/state.json").write_text(
        json.dumps(
            {
                "last_success_at": last.isoformat(),
                "pending_post_upload": {"video_id": "x"} if pending else None,
            }
        )
    )
    output = tmp_path / "output"
    env = dict(
        os.environ, EVENT_NAME="schedule", EVENT_SCHEDULE="37 */3 * * *", GITHUB_OUTPUT=str(output)
    )
    subprocess.run(["python", "-c", script], cwd=tmp_path, env=env, check=True)
    assert output.read_text().strip() == f"run={str(expected).lower()}"
    commit = next(s for s in steps if s.get("name") == "Commit automation state")
    assert "!cancelled()" in commit["if"]
    assert "success()" not in commit["if"]
