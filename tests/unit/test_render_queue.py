from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from quran_video_api import main

from quran_video.api_clients.quran_foundation import QuranFoundationConfigurationError
from quran_video.models.domain import RenderJobRecord, RenderRequest, RenderStatus


class DummyWorker:
    def __init__(self, queues: list[str]) -> None:
        self.queues = queues

    def queue_names(self) -> list[str]:
        return self.queues


def test_queue_requires_worker_listening_to_render_queue(monkeypatch) -> None:
    monkeypatch.setattr(
        main.Worker,
        "all",
        lambda connection: [DummyWorker(["other"]), DummyWorker(["renders"])],
    )

    assert main._queue_has_worker(object(), "renders")
    assert not main._queue_has_worker(object(), "missing")


def test_queue_without_render_worker_falls_back_to_inline(monkeypatch) -> None:
    monkeypatch.setattr(main.Worker, "all", lambda connection: [DummyWorker(["other"])])

    assert not main._queue_has_worker(object(), "renders")


def test_render_request_uses_server_data_mode_when_omitted(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        main.settings.model_copy(update={"quran_video_data_mode": "fixture"}),
    )

    assert main._request_data_mode(RenderRequest()) == "fixture"
    assert main._request_data_mode(RenderRequest(data_mode="mp3quran")) == "mp3quran"


@pytest.mark.asyncio
async def test_quran_foundation_configuration_errors_are_structured() -> None:
    response = await main.quran_foundation_configuration_error(
        None, QuranFoundationConfigurationError("missing credentials")
    )
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["detail"]["code"] == "quran_foundation_configuration"
    assert "missing credentials" in payload["detail"]["message"]
    assert "QURAN_VIDEO_DATA_MODE=fixture" in payload["detail"]["message"]


@pytest.mark.asyncio
async def test_render_events_waits_on_transient_sqlite_lock(monkeypatch) -> None:
    class LockedOnceStore:
        calls = 0

        def get(self, job_id: str) -> RenderJobRecord:
            self.calls += 1
            if self.calls == 1:
                raise sqlite3.OperationalError("database is locked")
            now = datetime.now(UTC)
            return RenderJobRecord(
                job_id=job_id,
                created_at=now,
                updated_at=now,
                status=RenderStatus.complete,
                phase="complete",
                progress=100,
            )

    async def no_sleep(_seconds: float) -> None:
        return None

    store = LockedOnceStore()
    monkeypatch.setattr(main, "_store", lambda: store)
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)

    response = await main.render_events("job-1")
    chunk = await anext(response.body_iterator)

    assert store.calls == 2
    assert '"status": "complete"' in chunk


@pytest.mark.asyncio
async def test_render_outputs_page_uses_http_asset_links(monkeypatch) -> None:
    class OutputStore:
        def get(self, job_id: str) -> RenderJobRecord:
            now = datetime.now(UTC)
            return RenderJobRecord(
                job_id=job_id,
                created_at=now,
                updated_at=now,
                status=RenderStatus.complete,
                phase="complete",
                progress=100,
                video_path="/tmp/render/video.mp4",
                thumbnail_path="/tmp/render/thumbnail.jpg",
            )

    monkeypatch.setattr(main, "_store", lambda: OutputStore())

    response = await main.render_outputs("job-1")
    body = response.body.decode()

    assert "file://" not in body
    assert "/api/v1/renders/job-1/video" in body
    assert "/api/v1/renders/job-1/thumbnail" in body
