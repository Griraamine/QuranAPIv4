from __future__ import annotations

import httpx
import pytest

from quran_video.api_clients.al_quran_cloud import AlQuranCloudClient


def _response(data):
    return httpx.Response(200, json={"code": 200, "status": "OK", "data": data})


@pytest.mark.asyncio
async def test_al_quran_cloud_client_maps_no_key_quran_and_audio_payloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/surah":
            return _response(
                [
                    {
                        "number": 1,
                        "name": "سُورَةُ ٱلْفَاتِحَةِ",
                        "englishName": "Al-Fatihah",
                        "englishNameTranslation": "The Opening",
                        "numberOfAyahs": 7,
                        "revelationType": "Meccan",
                    }
                ]
            )
        if request.url.path == "/v1/edition":
            assert request.url.params["format"] == "audio"
            assert request.url.params["type"] == "versebyverse"
            return _response(
                [
                    {
                        "identifier": "ar.alafasy",
                        "language": "ar",
                        "englishName": "Mishary Rashid Alafasy",
                        "name": "مشاري العفاسي",
                    },
                    {
                        "identifier": "en.walk",
                        "language": "en",
                        "englishName": "Ignored translation audio",
                    },
                ]
            )
        if request.url.path == "/v1/surah/1/editions/quran-uthmani,en.sahih":
            return _response(
                [
                    {
                        "edition": {"identifier": "quran-uthmani"},
                        "ayahs": [
                            {"numberInSurah": 1, "text": "بِسْمِ اللَّهِ"},
                        ],
                    },
                    {
                        "edition": {"identifier": "en.sahih"},
                        "ayahs": [
                            {"numberInSurah": 1, "text": "In <b>the</b> name[1]"},
                        ],
                    },
                ]
            )
        if request.url.path == "/v1/surah/1/ar.alafasy":
            return _response(
                {
                    "ayahs": [
                        {
                            "numberInSurah": 1,
                            "text": "بِسْمِ اللَّهِ",
                            "audio": "https://cdn.islamic.network/quran/audio/128/ar.alafasy/1.mp3",
                        }
                    ]
                }
            )
        return httpx.Response(404)

    client = AlQuranCloudClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    chapters = await client.get_chapters()
    reciters = await client.get_chapter_reciters()
    verses = await client.get_verses_by_chapter(1)
    audio = await client.get_chapter_audio(1, "ar.alafasy")

    assert chapters[0].english_name == "Al-Fatihah"
    assert reciters[0].id == "ar.alafasy"
    assert reciters[0].audio_source_name == "Al Quran Cloud / Islamic Network"
    assert verses[0].translation == "In the name"
    assert [word.position for word in verses[0].words] == [1, 2]
    assert audio.url == "alqurancloud://verse-audio"
    assert audio.audio_urls == ["https://cdn.islamic.network/quran/audio/128/ar.alafasy/1.mp3"]
    assert audio.verse_timestamps[0].word_segments[-1].end_ms == audio.verse_timestamps[0].end_ms

    await client.close()
