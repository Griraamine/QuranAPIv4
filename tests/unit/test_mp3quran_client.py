from __future__ import annotations

import httpx
import pytest

from quran_video.api_clients.mp3quran import (
    MP3QuranAPIError,
    MP3QuranClient,
    MP3QuranValidationError,
    build_audio_url,
    parse_ayah_timing_payload,
    resolve_timing_mapping,
)
from quran_video.models import (
    Chapter,
    ChapterAudio,
    ChapterReciter,
    QuranWord,
    RecitationStyle,
    TimingStatus,
    Verse,
)
from quran_video.quran.compatibility import bismillah_policy, build_range_timeline
from quran_video.quran.fixtures import fixture_verses


def _reciter_payload() -> dict:
    return {
        "reciters": [
            {
                "id": 102,
                "name": "Maher Al Meaqli",
                "moshaf": [
                    {
                        "id": 133,
                        "name": "Almusshaf Al Mojawwad - Almusshaf Al Mojawwad",
                        "rewaya_id": 22,
                        "server": "https://server12.mp3quran.net/maher/Almusshaf-Al-Mojawwad/",
                        "surah_total": 3,
                        "moshaf_type": 222,
                        "surah_list": " 1, 2,2, 114, bad, 0,115,,",
                    },
                    {
                        "id": 102,
                        "name": "Rewayat Hafs A'n Assem - Murattal",
                        "rewaya_id": 1,
                        "server": "https://server12.mp3quran.net/maher/",
                        "surah_total": 2,
                        "moshaf_type": 11,
                        "surah_list": "1,2",
                    },
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_mp3quran_reciter_parsing_supports_multiple_moshafs_and_partial_surahs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["language"] == "eng"
        assert request.url.params["reciter"] == "102"
        assert request.headers["User-Agent"].startswith("quran-video-platform")
        return httpx.Response(200, json=_reciter_payload(), request=request)

    client = MP3QuranClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    reciters = await client.get_reciters(reciter_id=102)

    assert reciters[0].id == "102"
    assert len(reciters[0].moshafs) == 2
    assert reciters[0].moshafs[0].available_surahs == [1, 2, 114]
    assert reciters[0].moshafs[0].surah_total == 3


@pytest.mark.asyncio
async def test_mp3quran_uses_sura_parameter_for_reciter_filter_and_retries_429() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert "sura=1" in str(request.url)
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"reciters": []}, request=request)

    client = MP3QuranClient(
        retries=2,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await client.get_reciters(rewaya_id=1, surah_number=1) == []
    assert calls == 2


@pytest.mark.asyncio
async def test_mp3quran_detects_api_errors_payloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Errors": ["bad request"]}, request=request)

    client = MP3QuranClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(MP3QuranAPIError):
        await client.get_reciters()


def test_audio_url_preserves_server_path_and_rejects_unexpected_hosts() -> None:
    assert (
        build_audio_url("https://server12.mp3quran.net/maher/Almusshaf-Al-Mojawwad", 2)
        == "https://server12.mp3quran.net/maher/Almusshaf-Al-Mojawwad/002.mp3"
    )
    assert build_audio_url("https://server12.mp3quran.net/maher/", 1).endswith("/001.mp3")
    with pytest.raises(MP3QuranValidationError):
        build_audio_url("http://server12.mp3quran.net/maher/", 1)
    with pytest.raises(MP3QuranValidationError):
        build_audio_url("https://example.test/maher/", 1)


def test_timing_read_mapping_uses_server_not_matching_ids() -> None:
    reciter = ChapterReciter(
        id="102",
        english_name="Maher Al Meaqli",
        arabic_name="Maher Al Meaqli",
        style=RecitationStyle(id="mp3quran", name="MP3Quran"),
        provider="mp3quran",
        moshafs=_reciter_payload_to_moshafs(),
    )
    moshaf = reciter.moshafs[0]
    reads = [
        type(
            "Read",
            (),
            {
                "id": 501,
                "name": "Other",
                "rewaya": "Hafs",
                "folder_url": "https://server12.mp3quran.net/maher/Almusshaf-Al-Mojawwad/",
                "soar_count": 114,
            },
        )()
    ]
    mapping = resolve_timing_mapping(moshaf=moshaf, reciter=reciter, reads=reads)
    assert mapping.status == TimingStatus.timing_available
    assert mapping.timing_read_id == 501
    assert mapping.method == "server_folder_url"


def test_timing_mapping_reports_unavailable_and_ambiguous() -> None:
    reciter = ChapterReciter(
        id="1",
        english_name="Ibrahim Al-Akdar",
        arabic_name="Ibrahim Al-Akdar",
        style=RecitationStyle(id="mp3quran", name="MP3Quran"),
        provider="mp3quran",
        moshafs=_reciter_payload_to_moshafs(),
    )
    moshaf = reciter.moshafs[0]
    unavailable = resolve_timing_mapping(moshaf=moshaf, reciter=reciter, reads=[])
    assert unavailable.status == TimingStatus.timing_unavailable
    ambiguous_reads = [
        type(
            "Read",
            (),
            {"id": 1, "name": "", "rewaya": None, "folder_url": moshaf.server, "soar_count": 1},
        )(),
        type(
            "Read",
            (),
            {"id": 2, "name": "", "rewaya": None, "folder_url": moshaf.server, "soar_count": 1},
        )(),
    ]
    ambiguous = resolve_timing_mapping(moshaf=moshaf, reciter=reciter, reads=ambiguous_reads)
    assert ambiguous.status == TimingStatus.timing_ambiguous


def test_parse_timing_preserves_ayah_zero_without_subtitle_timing() -> None:
    timing = parse_ayah_timing_payload(
        [
            {"ayah": 0, "start_time": 0, "end_time": 3000},
            {"ayah": 1, "start_time": 3000, "end_time": 9000},
            {"ayah": 2, "start_time": 9000, "end_time": 13000},
        ],
        surah_number=2,
        expected_ayah_count=2,
    )

    assert timing.status == TimingStatus.timing_available
    assert timing.intro_timing is not None
    assert [item.verse_number for item in timing.timings] == [1, 2]


def test_parse_timing_rejects_duplicates_and_mismatches() -> None:
    duplicate = parse_ayah_timing_payload(
        [
            {"ayah": 1, "start_time": 0, "end_time": 1000},
            {"ayah": 1, "start_time": 1000, "end_time": 2000},
        ],
        surah_number=1,
        expected_ayah_count=1,
    )
    assert duplicate.status == TimingStatus.timing_invalid
    mismatch = parse_ayah_timing_payload(
        [{"ayah": 1, "start_time": 0, "end_time": 1000}],
        surah_number=1,
        expected_ayah_count=2,
    )
    assert mismatch.status == TimingStatus.text_timing_mismatch


def test_mp3quran_intro_does_not_insert_or_duplicate_bismillah() -> None:
    verses = fixture_verses(1)
    audio = ChapterAudio(
        reciter_id="1",
        moshaf_id="1",
        chapter_id=1,
        url="https://server6.mp3quran.net/akdr/001.mp3",
        duration_ms=13000,
        provider="mp3quran",
        verse_timestamps=parse_ayah_timing_payload(
            [
                {"ayah": 0, "start_time": 0, "end_time": 1000},
                {"ayah": 1, "start_time": 1000, "end_time": 4000},
                {"ayah": 2, "start_time": 4000, "end_time": 7000},
            ],
            surah_number=1,
            expected_ayah_count=2,
        ).timings,
        intro_timing=parse_ayah_timing_payload(
            [
                {"ayah": 0, "start_time": 0, "end_time": 1000},
                {"ayah": 1, "start_time": 1000, "end_time": 4000},
                {"ayah": 2, "start_time": 4000, "end_time": 7000},
            ],
            surah_number=1,
            expected_ayah_count=2,
        ).intro_timing,
        has_ayah_timing=True,
        has_word_timing=False,
        timing_status=TimingStatus.timing_available,
    )
    reciter = ChapterReciter(
        id="1",
        english_name="Ibrahim Al-Akdar",
        arabic_name="Ibrahim Al-Akdar",
        style=RecitationStyle(id="mp3quran", name="MP3Quran"),
    )

    policy = bismillah_policy(1, 1, audio)
    timeline = build_range_timeline(
        Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7),
        reciter,
        verses,
        audio,
        1,
        2,
        True,
    )

    assert not policy.may_include
    assert not timeline.include_bismillah
    assert timeline.timestamps[0].start_ms == 1000
    assert bismillah_policy(9, 1, audio).may_include is False


def test_mp3quran_intro_moves_leading_bismillah_before_ayah_text() -> None:
    verses = [
        Verse(
            chapter_id=92,
            verse_number=1,
            text_uthmani="بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ وَٱلَّيْلِ إِذَا يَغْشَىٰ",
            translation="By the night when it covers",
            words=[
                QuranWord(position=1, text_uthmani="بِسْمِ", translation=""),
                QuranWord(position=2, text_uthmani="ٱللَّهِ", translation=""),
                QuranWord(position=3, text_uthmani="ٱلرَّحْمَـٰنِ", translation=""),
                QuranWord(position=4, text_uthmani="ٱلرَّحِيمِ", translation=""),
                QuranWord(position=5, text_uthmani="وَٱلَّيْلِ", translation=""),
                QuranWord(position=6, text_uthmani="إِذَا", translation=""),
                QuranWord(position=7, text_uthmani="يَغْشَىٰ", translation=""),
            ],
        )
    ]
    timing = parse_ayah_timing_payload(
        [
            {"ayah": 0, "start_time": 0, "end_time": 1400},
            {"ayah": 1, "start_time": 1400, "end_time": 4200},
        ],
        surah_number=92,
        expected_ayah_count=1,
    )
    audio = ChapterAudio(
        reciter_id="1",
        moshaf_id="1",
        chapter_id=92,
        url="https://server6.mp3quran.net/akdr/092.mp3",
        duration_ms=5000,
        provider="mp3quran",
        verse_timestamps=timing.timings,
        intro_timing=timing.intro_timing,
        has_ayah_timing=True,
        has_word_timing=False,
        timing_status=TimingStatus.timing_available,
    )
    reciter = ChapterReciter(
        id="1",
        english_name="Ibrahim Al-Akdar",
        arabic_name="Ibrahim Al-Akdar",
        style=RecitationStyle(id="mp3quran", name="MP3Quran"),
    )

    timeline = build_range_timeline(
        Chapter(id=92, arabic_name="الليل", english_name="Al-Layl", verse_count=21),
        reciter,
        verses,
        audio,
        1,
        1,
        True,
    )

    assert timeline.include_bismillah
    assert timeline.bismillah_duration_ms == 1400
    assert timeline.timestamps[0].start_ms == 1400
    assert timeline.verses[0].text_uthmani == "وَٱلَّيْلِ إِذَا يَغْشَىٰ"
    assert [word.position for word in timeline.verses[0].words] == [1, 2, 3]


def test_mp3quran_without_intro_strips_unheard_leading_bismillah() -> None:
    verses = [
        Verse(
            chapter_id=99,
            verse_number=1,
            text_uthmani="بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ إِذَا زُلْزِلَتِ ٱلْأَرْضُ زِلْزَالَهَا",
            translation="When the earth is shaken with its final earthquake",
            words=[
                QuranWord(position=1, text_uthmani="بِسْمِ", translation=""),
                QuranWord(position=2, text_uthmani="ٱللَّهِ", translation=""),
                QuranWord(position=3, text_uthmani="ٱلرَّحْمَـٰنِ", translation=""),
                QuranWord(position=4, text_uthmani="ٱلرَّحِيمِ", translation=""),
                QuranWord(position=5, text_uthmani="إِذَا", translation=""),
                QuranWord(position=6, text_uthmani="زُلْزِلَتِ", translation=""),
                QuranWord(position=7, text_uthmani="ٱلْأَرْضُ", translation=""),
                QuranWord(position=8, text_uthmani="زِلْزَالَهَا", translation=""),
            ],
        )
    ]
    timing = parse_ayah_timing_payload(
        [{"ayah": 1, "start_time": 0, "end_time": 3000}],
        surah_number=99,
        expected_ayah_count=1,
    )
    audio = ChapterAudio(
        reciter_id="1",
        moshaf_id="1",
        chapter_id=99,
        url="https://server6.mp3quran.net/akdr/099.mp3",
        duration_ms=4000,
        provider="mp3quran",
        verse_timestamps=timing.timings,
        intro_timing=timing.intro_timing,
        has_ayah_timing=True,
        has_word_timing=False,
        timing_status=TimingStatus.timing_available,
    )
    reciter = ChapterReciter(
        id="1",
        english_name="Ibrahim Al-Akdar",
        arabic_name="Ibrahim Al-Akdar",
        style=RecitationStyle(id="mp3quran", name="MP3Quran"),
    )

    timeline = build_range_timeline(
        Chapter(id=99, arabic_name="الزلزلة", english_name="Az-Zalzalah", verse_count=8),
        reciter,
        verses,
        audio,
        1,
        1,
        True,
    )

    assert not timeline.include_bismillah
    assert timeline.bismillah_duration_ms == 0
    assert timeline.timestamps[0].start_ms == 0
    assert timeline.verses[0].text_uthmani == "إِذَا زُلْزِلَتِ ٱلْأَرْضُ زِلْزَالَهَا"
    assert [word.position for word in timeline.verses[0].words] == [1, 2, 3, 4]


def _reciter_payload_to_moshafs():
    client_reciter = httpx.Response(200, json=_reciter_payload()).json()["reciters"][0]
    from quran_video.api_clients.mp3quran import _parse_reciter

    return _parse_reciter(client_reciter).moshafs
