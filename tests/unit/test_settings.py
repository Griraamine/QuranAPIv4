from __future__ import annotations

from pathlib import Path

from quran_video.config.settings import Settings


def test_docker_sqlite_path_is_normalized_outside_container(monkeypatch) -> None:
    real_exists = Path.exists

    def fake_exists(path: Path) -> bool:
        if path == Path("/app"):
            return False
        return real_exists(path)

    monkeypatch.setattr("quran_video.config.settings.Path.exists", fake_exists)

    settings = Settings(SQLITE_PATH="/app/data/cache/jobs.sqlite3")

    assert settings.sqlite_path == Path("data/cache/jobs.sqlite3")
