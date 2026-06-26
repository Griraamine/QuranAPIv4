from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import worker.tasks as tasks
from worker.tasks import (
    _download_cached_full_surah_audio,
    _download_url_with_retries,
    _probe_audio_duration_ms,
)

from quran_video.models import ChapterAudio, RenderRequest, VerseTimestamp, WordSegment
from quran_video.rendering.ffmpeg import generate_fixture_audio


@pytest.mark.asyncio
async def test_audio_download_retries_remote_disconnect(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError("server disconnected", request=request)
        return httpx.Response(200, content=b"audio")

    target = tmp_path / "ayah.mp3"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await _download_url_with_retries(
            client, "https://example.test/ayah.mp3", target, attempts=2
        )

    assert calls == 2
    assert target.read_bytes() == b"audio"
    assert not list(tmp_path.glob("*.part-*"))


@pytest.mark.asyncio
async def test_audio_download_does_not_retry_client_errors(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, request=request)

    target = tmp_path / "ayah.mp3"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await _download_url_with_retries(
                client, "https://example.test/missing.mp3", target, attempts=3
            )

    assert calls == 1
    assert not target.exists()


@pytest.mark.asyncio
async def test_audio_download_retries_temporary_http_status(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, content=b"audio", request=request)

    target = tmp_path / "ayah.mp3"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await _download_url_with_retries(
            client, "https://server6.mp3quran.net/akdr/001.mp3", target, attempts=2
        )

    assert calls == 2
    assert target.read_bytes() == b"audio"


def test_ffprobe_audio_validation_accepts_real_audio_and_rejects_empty(tmp_path: Path) -> None:
    audio_path = generate_fixture_audio(tmp_path / "sample.mp3", 1000)
    assert _probe_audio_duration_ms(audio_path) >= 900

    empty_path = tmp_path / "empty.mp3"
    empty_path.write_bytes(b"")
    with pytest.raises(subprocess.CalledProcessError):
        _probe_audio_duration_ms(empty_path)


def _chapter_audio(url: str = "https://download.quranicaudio.com/quran/ali_jaber/002.mp3"):
    return ChapterAudio(
        reciter_id="158",
        chapter_id=2,
        url=url,
        duration_ms=20_000,
        verse_timestamps=[
            VerseTimestamp(
                verse_number=1,
                start_ms=0,
                end_ms=20_000,
                word_segments=[
                    WordSegment(
                        verse_number=1,
                        word_position=1,
                        start_ms=0,
                        end_ms=20_000,
                    )
                ],
            )
        ],
        provider="quranfoundation",
        has_ayah_timing=True,
        has_word_timing=False,
    )


@pytest.mark.asyncio
async def test_cached_full_surah_audio_reuses_cache_with_matching_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = _chapter_audio()
    settings = SimpleNamespace(
        quran_foundation_audio_cache_dir=tmp_path / "quran-foundation-audio",
        qf_read_timeout=30.0,
        qf_connect_timeout=10.0,
        qf_retries=3,
    )
    cache_path = settings.quran_foundation_audio_cache_dir / "158" / "002.mp3"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"cached")
    tasks._write_cache_metadata(cache_path, audio)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "_probe_audio_duration_ms", lambda _path: 20_000)

    async def fail_download(*_args, **_kwargs):
        raise AssertionError("cache should have been reused")

    monkeypatch.setattr(tasks, "_download_url_with_retries", fail_download)

    path, render_audio = await _download_cached_full_surah_audio(
        audio,
        RenderRequest(reciter_id="158", chapter_id=2, ayah_from=1, ayah_to=1),
        tmp_path / "selected.mp3",
    )

    assert path == cache_path
    assert path.read_bytes() == b"cached"
    assert render_audio.duration_ms == 20_000


@pytest.mark.asyncio
async def test_cached_full_surah_audio_redownloads_when_metadata_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = _chapter_audio()
    settings = SimpleNamespace(
        quran_foundation_audio_cache_dir=tmp_path / "quran-foundation-audio",
        qf_read_timeout=30.0,
        qf_connect_timeout=10.0,
        qf_retries=3,
    )
    cache_path = settings.quran_foundation_audio_cache_dir / "158" / "002.mp3"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"stale")
    (cache_path.with_suffix(".mp3.json")).write_text(
        '{"version": 1, "provider": "quranfoundation", "reciter_id": "158", '
        '"moshaf_id": null, "chapter_id": 2, "source_url": "https://old.example/002.mp3"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "_probe_audio_duration_ms", lambda _path: 20_000)
    calls = 0

    async def download(_client, _url, target, *, attempts):
        nonlocal calls
        calls += 1
        assert attempts == 3
        target.write_bytes(b"fresh")
        return target

    monkeypatch.setattr(tasks, "_download_url_with_retries", download)

    path, _render_audio = await _download_cached_full_surah_audio(
        audio,
        RenderRequest(reciter_id="158", chapter_id=2, ayah_from=1, ayah_to=1),
        tmp_path / "selected.mp3",
    )

    assert path == cache_path
    assert calls == 1
    assert path.read_bytes() == b"fresh"
    assert tasks._cache_metadata_matches(cache_path, audio)
