from __future__ import annotations

import pytest

from quran_video.config import get_settings
from quran_video.models import (
    Chapter,
    ChapterAudio,
    ChapterReciter,
    QuranWord,
    RecitationStyle,
    TimingStatus,
    Verse,
    VerseTimestamp,
    WordSegment,
)
from quran_video.quran.repository import QuranRepository


class FakeQuranFoundationClient:
    async def get_chapters(self):
        return [Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=1)]

    async def get_chapter_reciters(self):
        return [
            ChapterReciter(
                id="7",
                english_name="QF Reciter",
                arabic_name="القارئ",
                style=RecitationStyle(id="chapter", name="Chapter"),
                provider="quranfoundation",
                audio_source_name="Quran.Foundation Content API",
            )
        ]

    async def resolve_saheeh_international_id(self):
        return 20

    async def get_verses_by_chapter(self, chapter_id: int, translation_id: int):
        assert translation_id == 20
        return [
            Verse(
                chapter_id=chapter_id,
                verse_number=1,
                text_uthmani="بِسْمِ ٱللَّهِ",
                translation="In the name of Allah.",
                words=[
                    QuranWord(position=1, text_uthmani="بِسْمِ", translation="In name"),
                    QuranWord(position=2, text_uthmani="ٱللَّهِ", translation="Allah"),
                ],
            )
        ]

    async def get_chapter_audio(self, chapter_id: int, reciter_id: str):
        return ChapterAudio(
            reciter_id=reciter_id,
            chapter_id=chapter_id,
            url="https://audio.quran.foundation/7/001.mp3",
            duration_ms=3000,
            verse_timestamps=[
                VerseTimestamp(
                    verse_number=1,
                    start_ms=0,
                    end_ms=3000,
                    word_segments=[
                        WordSegment(verse_number=1, word_position=1, start_ms=0, end_ms=1200),
                        WordSegment(verse_number=1, word_position=2, start_ms=1200, end_ms=3000),
                    ],
                )
            ],
            provider="quranfoundation",
            timing_status=TimingStatus.timing_available,
            timing_mapping_method="quran_foundation_chapter_recitation_segments",
            has_ayah_timing=True,
            has_word_timing=True,
        )


@pytest.mark.asyncio
async def test_repository_uses_quran_foundation_by_default_without_legacy_fallback() -> None:
    settings = get_settings().model_copy(
        update={
            "quran_video_data_mode": "quranfoundation",
            "qf_client_id": "id",
            "qf_client_secret": "secret",
        }
    )
    repository = QuranRepository(settings, client=FakeQuranFoundationClient())

    reciters = await repository.reciters()
    verses = await repository.verses(1)
    audio = await repository.chapter_audio(1, "7")
    compatibility = await repository.compatibility(1, "7")

    assert reciters[0].provider == "quranfoundation"
    assert verses[0].translation == "In the name of Allah."
    assert audio.provider == "quranfoundation"
    assert audio.reciter_name == "QF Reciter"
    assert compatibility.compatible
    assert compatibility.has_word_timing
