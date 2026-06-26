from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
import respx

from quran_video.notifications import TelegramClient
from quran_video.storage import JobStore


@pytest.mark.asyncio
@respx.mock
async def test_telegram_success_request() -> None:
    route = respx.post("https://api.telegram.org/bottoken/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = TelegramClient("token", "chat", http_client=httpx.AsyncClient())
    await client.send_message("Quran video uploaded | safe")
    assert route.called
    payload = route.calls[0].request.content.decode()
    assert "\\|" in payload


def test_sqlite_job_lifecycle(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    record = store.create("job", {"hello": "world"})
    assert record.status == "queued"
    store.append_log("job", "started")
    assert store.logs("job")[0]["message"] == "started"
    updated = store.update("job", status="running", phase="encoding", progress=50)
    assert updated.progress == 50


def test_readonly_sqlite_job_database_is_rotated(tmp_path, monkeypatch) -> None:
    path = tmp_path / "jobs.sqlite3"
    path.write_text("old readonly database", encoding="utf-8")
    real_access = os.access

    def fake_access(candidate: str | Path, mode: int) -> bool:
        if Path(candidate) == path and mode == os.W_OK:
            return False
        return real_access(candidate, mode)

    monkeypatch.setattr("quran_video.storage.jobs.os.access", fake_access)

    store = JobStore(path)
    record = store.create("job", {"hello": "world"})

    assert record.status == "queued"
    assert list(tmp_path.glob("jobs.sqlite3.readonly-*"))
