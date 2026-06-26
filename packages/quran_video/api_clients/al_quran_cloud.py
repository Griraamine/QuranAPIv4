from __future__ import annotations

from typing import Any, ClassVar

import httpx

from quran_video.models import (
    Chapter,
    ChapterAudio,
    ChapterReciter,
    QuranWord,
    RecitationStyle,
    Verse,
    VerseTimestamp,
    WordSegment,
)
from quran_video.quran.text import clean_translation_text


class AlQuranCloudClient:
    API_BASE: ClassVar[str] = "https://api.alquran.cloud/v1"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def close(self) -> None:
        await self.http.aclose()

    async def _request(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = await self.http.get(f"{self.API_BASE}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 200:
            raise RuntimeError(payload.get("status") or "Al Quran Cloud request failed")
        return payload["data"]

    async def get_chapters(self) -> list[Chapter]:
        chapters = await self._request("/surah")
        return [
            Chapter(
                id=int(item["number"]),
                arabic_name=_clean_text(item.get("name", "")),
                english_name=str(item.get("englishName", "")),
                translated_name=item.get("englishNameTranslation"),
                verse_count=int(item["numberOfAyahs"]),
                revelation_place=str(item.get("revelationType", "")).lower() or None,
            )
            for item in chapters
        ]

    async def get_chapter_reciters(self) -> list[ChapterReciter]:
        editions = await self._request(
            "/edition",
            params={"format": "audio", "type": "versebyverse"},
        )
        reciters: list[ChapterReciter] = []
        for item in editions:
            if item.get("language") != "ar":
                continue
            reciters.append(
                ChapterReciter(
                    id=item["identifier"],
                    english_name=item.get("englishName") or item["identifier"],
                    arabic_name=item.get("name") or item.get("englishName") or item["identifier"],
                    style=RecitationStyle(id="versebyverse", name="Verse by verse"),
                    audio_source_name="Al Quran Cloud / Islamic Network",
                )
            )
        return reciters

    async def get_verses_by_chapter(self, chapter_id: int) -> list[Verse]:
        editions = await self._request(f"/surah/{chapter_id}/editions/quran-uthmani,en.sahih")
        arabic = _edition_by_identifier(editions, "quran-uthmani")
        english = _edition_by_identifier(editions, "en.sahih")
        translations = {
            int(item["numberInSurah"]): clean_translation_text(item.get("text", ""))
            for item in english.get("ayahs", [])
        }
        verses: list[Verse] = []
        for item in arabic.get("ayahs", []):
            verse_number = int(item["numberInSurah"])
            text = _clean_text(item.get("text", ""))
            words = [
                QuranWord(position=index, text_uthmani=word, translation="")
                for index, word in enumerate(text.split(), start=1)
            ]
            verses.append(
                Verse(
                    chapter_id=chapter_id,
                    verse_number=verse_number,
                    text_uthmani=text,
                    translation=translations.get(verse_number, ""),
                    words=words,
                )
            )
        return verses

    async def get_chapter_audio(self, chapter_id: int, reciter_id: str) -> ChapterAudio:
        data = await self._request(f"/surah/{chapter_id}/{reciter_id}")
        timestamps: list[VerseTimestamp] = []
        audio_urls: list[str] = []
        cursor = 0
        for item in data.get("ayahs", []):
            verse_number = int(item["numberInSurah"])
            text = _clean_text(item.get("text", ""))
            words = text.split()
            duration = max(1800, len(words) * 700)
            word_segments = _even_word_segments(verse_number, len(words), cursor, duration)
            timestamps.append(
                VerseTimestamp(
                    verse_number=verse_number,
                    start_ms=cursor,
                    end_ms=cursor + duration,
                    word_segments=word_segments,
                )
            )
            audio_urls.append(str(item["audio"]))
            cursor += duration
        return ChapterAudio(
            reciter_id=reciter_id,
            chapter_id=chapter_id,
            url="alqurancloud://verse-audio",
            audio_urls=audio_urls,
            duration_ms=cursor,
            verse_timestamps=timestamps,
        )


def _edition_by_identifier(editions: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    for edition in editions:
        if edition.get("edition", {}).get("identifier") == identifier:
            return edition
    raise RuntimeError(f"Al Quran Cloud edition is missing: {identifier}")


def _clean_text(value: str) -> str:
    return value.replace("\ufeff", "").strip()


def _even_word_segments(
    verse_number: int,
    word_count: int,
    start_ms: int,
    duration_ms: int,
) -> list[WordSegment]:
    count = max(word_count, 1)
    segments: list[WordSegment] = []
    for position in range(1, count + 1):
        segment_start = start_ms + round((position - 1) * duration_ms / count)
        segment_end = start_ms + round(position * duration_ms / count)
        segments.append(
            WordSegment(
                verse_number=verse_number,
                word_position=position,
                start_ms=segment_start,
                end_ms=max(segment_end, segment_start + 1),
            )
        )
    return segments
