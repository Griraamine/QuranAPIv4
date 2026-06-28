from __future__ import annotations

import httpx
import pytest
import respx

from quran_video.api_clients.quran_foundation import (
    QuranFoundationAPIError,
    QuranFoundationClient,
    QuranFoundationConfigurationError,
)


@pytest.mark.asyncio
@respx.mock
async def test_token_caching_and_one_time_401_refresh() -> None:
    client = QuranFoundationClient("id", "secret", http_client=httpx.AsyncClient())
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "first", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "second", "expires_in": 3600}),
        ]
    )
    route = respx.get("https://apis.quran.foundation/content/api/v4/chapters").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(
                200,
                json={
                    "chapters": [
                        {
                            "id": 1,
                            "name_arabic": "الفاتحة",
                            "name_simple": "Al-Fatihah",
                            "verses_count": 7,
                        }
                    ]
                },
            ),
        ]
    )
    chapters = await client.get_chapters()
    assert chapters[0].id == 1
    assert route.call_count == 2
    assert route.calls[-1].request.headers["x-client-id"] == "id"
    assert route.calls[-1].request.headers["x-auth-token"] == "second"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_pagination_and_saheeh_resolution() -> None:
    client = QuranFoundationClient("id", "secret", http_client=httpx.AsyncClient())
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
    )
    respx.get("https://apis.quran.foundation/content/api/v4/resources/translations").mock(
        return_value=httpx.Response(
            200,
            json={"translations": [{"id": 20, "name": "Saheeh International"}]},
        )
    )
    assert await client.resolve_saheeh_international_id() == 20
    respx.get("https://apis.quran.foundation/content/api/v4/verses/by_chapter/1").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "verses": [
                        {
                            "verse_key": "1:1",
                            "text_uthmani": "بسم",
                            "translations": [{"text": "In <b>the</b> name"}],
                            "words": [
                                {
                                    "position": 1,
                                    "text_uthmani": "بسم",
                                    "translation": {"text": "name"},
                                    "char_type_name": "word",
                                },
                                {
                                    "position": 2,
                                    "text_uthmani": "١",
                                    "translation": {"text": ""},
                                    "char_type_name": "end",
                                },
                            ],
                        }
                    ],
                    "pagination": {"total_pages": 2},
                },
            ),
            httpx.Response(
                200,
                json={
                    "verses": [
                        {
                            "verse_key": "1:2",
                            "text_uthmani": "الحمد",
                            "translations": [{"text": "Praise"}],
                            "words": [
                                {
                                    "position": 1,
                                    "text_uthmani": "الحمد",
                                    "translation": {"text": "praise"},
                                }
                            ],
                        }
                    ],
                    "pagination": {"total_pages": 2},
                },
            ),
        ]
    )
    verses = await client.get_verses_by_chapter(1, 20)
    assert [verse.verse_number for verse in verses] == [1, 2]
    assert [word.position for word in verses[0].words] == [1]
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_saheeh_resolution_requires_exactly_one_match() -> None:
    client = QuranFoundationClient("id", "secret", http_client=httpx.AsyncClient())
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
    )
    respx.get("https://apis.quran.foundation/content/api/v4/resources/translations").mock(
        return_value=httpx.Response(200, json={"translations": []})
    )
    with pytest.raises(QuranFoundationConfigurationError):
        await client.resolve_saheeh_international_id()
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_retries_temporary_failures_and_sends_content_headers() -> None:
    client = QuranFoundationClient("id", "secret", http_client=httpx.AsyncClient(), retries=2)
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
    )
    route = respx.get("https://apis.quran.foundation/content/api/v4/chapters").mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "0"}),
            httpx.Response(
                200,
                json={
                    "chapters": [
                        {
                            "id": 1,
                            "name_arabic": "الفاتحة",
                            "name_simple": "Al-Fatihah",
                            "verses_count": 7,
                        }
                    ]
                },
            ),
        ]
    )

    chapters = await client.get_chapters()

    assert chapters[0].english_name == "Al-Fatihah"
    assert route.call_count == 2
    assert route.calls[-1].request.headers["x-client-id"] == "id"
    assert route.calls[-1].request.headers["x-auth-token"] == "token"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_sanitized_api_errors_do_not_expose_credentials() -> None:
    client = QuranFoundationClient("client-id", "super-secret", http_client=httpx.AsyncClient())
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        return_value=httpx.Response(500, text="temporary")
    )

    with pytest.raises(QuranFoundationAPIError) as error:
        await client.get_chapters()

    message = str(error.value)
    assert "client-id" not in message
    assert "super-secret" not in message
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_api_error_payloads_are_rejected() -> None:
    client = QuranFoundationClient("id", "secret", http_client=httpx.AsyncClient())
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
    )
    respx.get("https://apis.quran.foundation/content/api/v4/chapters").mock(
        return_value=httpx.Response(200, json={"error": "not allowed"})
    )

    with pytest.raises(QuranFoundationAPIError):
        await client.get_chapters()

    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_chapter_reciters_normalize_nested_style_name() -> None:
    client = QuranFoundationClient("id", "secret", http_client=httpx.AsyncClient())
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
    )
    respx.get("https://apis.quran.foundation/content/api/v4/resources/chapter_reciters").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "reciters": [
                        {
                            "id": 9,
                            "reciter_name": "Al-Minshawi",
                            "style": {
                                "name": "Murattal",
                                "translated_name": {
                                    "name": "Murattal",
                                    "language_name": "english",
                                },
                            },
                        }
                    ]
                },
            ),
            httpx.Response(
                200,
                json={"reciters": [{"id": 9, "reciter_name": "المنشاوي"}]},
            ),
        ]
    )

    reciters = await client.get_chapter_reciters()

    assert reciters[0].english_name == "Al-Minshawi"
    assert reciters[0].arabic_name == "المنشاوي"
    assert reciters[0].style.name == "Murattal"
    assert reciters[0].style.id == "murattal"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_chapter_audio_parses_word_segments_and_uses_segments_parameter() -> None:
    client = QuranFoundationClient("id", "secret", http_client=httpx.AsyncClient())
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
    )
    route = respx.get("https://apis.quran.foundation/content/api/v4/chapter_recitations/7/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "audio_file": {
                    "audio_url": "https://audio.quran.foundation/7/001.mp3",
                    "duration": 7,
                    "verse_timings": [
                        {
                            "verse_key": "1:1",
                            "timestamp_from": 0,
                            "timestamp_to": 3000,
                            "segments": [[1, 0], [2, 1200], [3, 2400]],
                        },
                        {
                            "verse_key": "1:2",
                            "timestamp_from": 3000,
                            "timestamp_to": 7000,
                            "segments": [[1, 3000, 7000]],
                        },
                    ],
                }
            },
        )
    )

    audio = await client.get_chapter_audio(1, "7")

    assert route.calls[0].request.url.params["segments"] == "true"
    assert audio.provider == "quranfoundation"
    assert audio.has_word_timing
    assert audio.has_ayah_timing
    assert audio.duration_ms == 7000
    assert [segment.word_position for segment in audio.verse_timestamps[0].word_segments] == [
        1,
        2,
        3,
    ]
    assert [
        (segment.start_ms, segment.end_ms) for segment in audio.verse_timestamps[0].word_segments
    ] == [
        (0, 1200),
        (1200, 2400),
        (2400, 3000),
    ]
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_chapter_audio_falls_back_to_ayah_timing_when_segments_are_missing() -> None:
    client = QuranFoundationClient("id", "secret", http_client=httpx.AsyncClient())
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
    )
    respx.get("https://apis.quran.foundation/content/api/v4/chapter_recitations/7/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "audio_file": {
                    "audio_url": "https://audio.quran.foundation/7/001.mp3",
                    "duration": 3,
                    "verse_timings": [
                        {
                            "verse_key": "1:1",
                            "timestamp_from": 0,
                            "timestamp_to": 3000,
                            "segments": [],
                        }
                    ],
                }
            },
        )
    )

    audio = await client.get_chapter_audio(1, "7")

    assert not audio.has_word_timing
    assert audio.has_ayah_timing
    assert len(audio.verse_timestamps[0].word_segments) == 1
    assert audio.verse_timestamps[0].word_segments[0].start_ms == 0
    assert audio.verse_timestamps[0].word_segments[0].end_ms == 3000
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_chapter_audio_prefers_public_ayah_timing_when_quranic_audio_timings_disagree() -> (
    None
):
    client = QuranFoundationClient("id", "secret", http_client=httpx.AsyncClient())
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
    )
    respx.get("https://apis.quran.foundation/content/api/v4/chapter_recitations/158/2").mock(
        return_value=httpx.Response(
            200,
            json={
                "audio_file": {
                    "audio_url": "https://download.quranicaudio.com/quran/ali_jaber//002.mp3",
                    "duration": 13,
                    "verse_timings": [
                        {
                            "verse_key": "2:1",
                            "timestamp_from": 0,
                            "timestamp_to": 3000,
                            "segments": [[1, 0, 3000]],
                        },
                        {
                            "verse_key": "2:2",
                            "timestamp_from": 3000,
                            "timestamp_to": 6000,
                            "segments": [[1, 3000, 6000]],
                        },
                    ],
                }
            },
        )
    )
    public_route = respx.get("https://api.quran.com/api/v4/chapter_recitations/158/2").mock(
        return_value=httpx.Response(
            200,
            json={
                "audio_file": {
                    "audio_url": "https://download.quranicaudio.com/quran/ali_jaber//002.mp3",
                    "timestamps": [
                        {
                            "verse_key": "2:1",
                            "timestamp_from": 1000,
                            "timestamp_to": 5000,
                        },
                        {
                            "verse_key": "2:2",
                            "timestamp_from": 5000,
                            "timestamp_to": 13000,
                        },
                    ],
                }
            },
        )
    )

    audio = await client.get_chapter_audio(2, "158")

    assert public_route.calls[0].request.url.params["segments"] == "true"
    assert not audio.has_word_timing
    assert audio.has_ayah_timing
    assert audio.timing_mapping_method == "qurancom_public_chapter_recitation_ayah_timing"
    assert [(item.verse_number, item.start_ms, item.end_ms) for item in audio.verse_timestamps] == [
        (1, 1000, 5000),
        (2, 5000, 13000),
    ]
    assert [item.word_segments[0].start_ms for item in audio.verse_timestamps] == [1000, 5000]
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_chapter_audio_accepts_alternate_live_timing_payload_shapes() -> None:
    client = QuranFoundationClient("id", "secret", http_client=httpx.AsyncClient())
    respx.post("https://oauth2.quran.foundation/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
    )
    respx.get("https://apis.quran.foundation/content/api/v4/chapter_recitations/7/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "audio_files": [
                    {
                        "url": "https://audio.quran.foundation/7/001.mp3",
                        "duration": 7,
                        "timings": [
                            {
                                "ayah_key": "1:1",
                                "start": 0,
                                "end": 3000,
                                "words": [[1, 1, 0, 1200], [1, 2, 1200, 3000]],
                            },
                            {
                                "ayah_number": 2,
                                "start": 3000,
                                "end": 7000,
                                "words": [{"word_number": 1, "start": 3000, "end": 7000}],
                            },
                        ],
                    }
                ]
            },
        )
    )

    audio = await client.get_chapter_audio(1, "7")

    assert audio.url == "https://audio.quran.foundation/7/001.mp3"
    assert audio.has_word_timing
    assert [segment.word_position for segment in audio.verse_timestamps[0].word_segments] == [1, 2]
    assert audio.verse_timestamps[1].word_segments[0].end_ms == 7000
    await client.close()
