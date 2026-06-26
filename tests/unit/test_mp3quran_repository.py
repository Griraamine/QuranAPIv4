from __future__ import annotations

import pytest

from quran_video.api_clients.mp3quran import _parse_reciter
from quran_video.config import get_settings
from quran_video.config import reciters as reciter_config
from quran_video.models import (
    BackgroundAsset,
    Chapter,
    ChapterReciter,
    RecitationStyle,
    ReciterMoshaf,
)
from quran_video.models.domain import RenderRequest, TimingStatus
from quran_video.quran.fixtures import fixture_verses
from quran_video.quran.render_defaults import resolve_render_request_defaults
from quran_video.quran.repository import QuranRepository, choose_default_moshaf


class FakeTextClient:
    async def get_chapters(self):
        return [Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=2)]

    async def get_verses_by_chapter(self, chapter_id: int):
        return fixture_verses(chapter_id)


class FakeMP3QuranClient:
    async def get_reciters(self, **kwargs):
        return [
            _parse_reciter(
                {
                    "id": 1,
                    "name": "Ibrahim Al-Akdar",
                    "moshaf": [
                        {
                            "id": 1,
                            "name": "Rewayat Hafs A'n Assem - Murattal",
                            "rewaya_id": 1,
                            "server": "https://server6.mp3quran.net/akdr/",
                            "surah_total": 1,
                            "moshaf_type": 11,
                            "surah_list": "1",
                        }
                    ],
                }
            )
        ]

    async def get_timing_reads(self):
        return []

    async def get_timed_surahs(self, timing_read_id: int):
        return []

    async def get_ayah_timing(self, **kwargs):
        raise AssertionError("timing should not be fetched when mapping is unavailable")


class DefaultsRepo:
    async def chapters(self):
        return [Chapter(id=2, arabic_name="البقرة", english_name="Al-Baqarah", verse_count=286)]

    async def reciters(self):
        return [
            ChapterReciter(
                id="10",
                english_name="Long English Name",
                arabic_name="اسم طويل",
                style=RecitationStyle(id="mp3quran", name="MP3Quran"),
                provider="mp3quran",
                moshafs=[
                    ReciterMoshaf(
                        id="mojawwad",
                        name="Rewayat Hafs A'n Assem - Mujawwad",
                        server="https://server.example.test/mojawwad/",
                        surah_total=1,
                        available_surahs=[2],
                    ),
                    ReciterMoshaf(
                        id="murattal",
                        name="Rewayat Hafs A'n Assem - Murattal",
                        server="https://server.example.test/murattal/",
                        surah_total=1,
                        available_surahs=[2],
                    ),
                ],
            )
        ]


@pytest.mark.asyncio
async def test_repository_returns_structured_timing_unavailable_status() -> None:
    settings = get_settings().model_copy(update={"quran_video_data_mode": "mp3quran"})
    repository = QuranRepository(
        settings,
        al_quran_cloud_client=FakeTextClient(),
        mp3quran_client=FakeMP3QuranClient(),
    )

    result = await repository.compatibility(1, "1", "1")

    assert not result.compatible
    assert result.status == TimingStatus.timing_unavailable
    assert result.moshaf_id == "1"


def test_default_moshaf_prefers_murattal_then_available_surah() -> None:
    choices = [
        ReciterMoshaf(
            id="mojawwad",
            name="Mujawwad",
            server="https://server.example.test/mojawwad/",
            surah_total=1,
            available_surahs=[2],
        ),
        ReciterMoshaf(
            id="murattal",
            name="Murattal",
            server="https://server.example.test/murattal/",
            surah_total=1,
            available_surahs=[1],
        ),
    ]

    assert choose_default_moshaf(choices, 1).id == "murattal"
    assert choose_default_moshaf(choices, 2).id == "mojawwad"


@pytest.mark.asyncio
async def test_render_defaults_make_minimal_api_request_complete() -> None:
    request = RenderRequest(chapter_id=2)
    resolved = await resolve_render_request_defaults(
        request,
        DefaultsRepo(),
        backgrounds=[
            BackgroundAsset(
                id="sample.jpg",
                filename="sample.jpg",
                media_type="image",
                width=1920,
                height=1080,
            )
        ],
    )

    assert resolved.reciter_id == "10"
    assert resolved.moshaf_id == "murattal"
    assert resolved.ayah_from == 1
    assert resolved.ayah_to == 286
    assert resolved.background_ids == ["sample.jpg"]
    assert resolved.badge.english_surah == "Al-Baqarah"
    assert resolved.badge.english_reciter == "Long English Name"


@pytest.mark.asyncio
async def test_reciter_names_can_be_overridden_from_json(tmp_path, monkeypatch) -> None:
    path = tmp_path / "reciters.json"
    path.write_text(
        '[{"reciter_id":"1","reciter_english_name":"Akdar","reciter_arabic_name":"الأخضر"}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(reciter_config, "reciter_names_path", lambda: path)
    reciter_config.load_reciter_name_overrides.cache_clear()
    settings = get_settings().model_copy(update={"quran_video_data_mode": "mp3quran"})
    repository = QuranRepository(
        settings,
        al_quran_cloud_client=FakeTextClient(),
        mp3quran_client=FakeMP3QuranClient(),
    )

    reciters = await repository.reciters()

    assert reciters[0].english_name == "Akdar"
    assert reciters[0].arabic_name == "الأخضر"
    reciter_config.load_reciter_name_overrides.cache_clear()
