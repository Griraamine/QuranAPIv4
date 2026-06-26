from __future__ import annotations

import pytest
from worker.tasks import _retime_selected_audio

from quran_video.models import Chapter, ChapterAudio, VerseTimestamp, WordSegment
from quran_video.quran import (
    bismillah_policy,
    build_range_timeline,
    clean_translation_text,
    concatenate_ayah_audio_timings,
    validate_audio_compatibility,
    validate_ayah_range,
)
from quran_video.quran.compatibility import validate_audio_with_safe_fallback
from quran_video.quran.fixtures import fixture_audio, fixture_reciters, fixture_verses


def test_clean_translation_text_removes_html_and_footnotes_without_rewording() -> None:
    assert (
        clean_translation_text("A <sup>1</sup>clear&nbsp;text[2] remains.")
        == "A clear text remains."
    )


def test_ayah_range_validation() -> None:
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    validate_ayah_range(chapter, 1, 7)
    with pytest.raises(ValueError):
        validate_ayah_range(chapter, 0, 1)
    with pytest.raises(ValueError):
        validate_ayah_range(chapter, 5, 4)


def test_bismillah_rules() -> None:
    assert not bismillah_policy(1, 1).visible
    assert not bismillah_policy(9, 1).may_include
    assert not bismillah_policy(2, 3).visible
    audio = fixture_audio(2).model_copy(
        update={
            "verse_timestamps": [
                fixture_audio(2).verse_timestamps[0].model_copy(update={"start_ms": 500}),
                *fixture_audio(2).verse_timestamps[1:],
            ]
        }
    )
    assert bismillah_policy(2, 1, audio).may_include


def test_audio_compatibility_and_word_mapping() -> None:
    verses = fixture_verses(1)
    audio = fixture_audio(1)
    assert validate_audio_compatibility(audio, verses).compatible
    broken = audio.model_copy(
        update={
            "verse_timestamps": [
                audio.verse_timestamps[0].model_copy(
                    update={
                        "word_segments": [
                            audio.verse_timestamps[0]
                            .word_segments[0]
                            .model_copy(update={"word_position": 999})
                        ]
                    }
                )
            ]
        }
    )
    result = validate_audio_compatibility(broken, [verses[0]])
    assert not result.compatible
    assert "mapped" in (result.reason or "")


def test_quran_foundation_word_timing_can_fall_back_to_ayah_timing() -> None:
    verses = fixture_verses(1)[:1]
    audio = fixture_audio(1).model_copy(
        update={
            "provider": "quranfoundation",
            "has_ayah_timing": True,
            "has_word_timing": True,
            "timing_mapping_method": "quran_foundation_chapter_recitation_segments",
            "verse_timestamps": [
                fixture_audio(1)
                .verse_timestamps[0]
                .model_copy(
                    update={
                        "word_segments": [
                            WordSegment(
                                verse_number=1,
                                word_position=1,
                                start_ms=0,
                                end_ms=fixture_audio(1).verse_timestamps[0].end_ms,
                            )
                        ]
                    }
                )
            ],
        }
    )

    render_audio, result = validate_audio_with_safe_fallback(audio, verses)

    assert result.compatible
    assert result.has_ayah_timing
    assert not result.has_word_timing
    assert not render_audio.has_word_timing
    assert render_audio.verse_timestamps[0].word_segments[0].start_ms == 0
    assert (
        render_audio.verse_timestamps[0].word_segments[0].end_ms == audio.verse_timestamps[0].end_ms
    )


def test_quran_foundation_partial_high_coverage_word_timing_is_accepted() -> None:
    verses = fixture_verses(1)[:1]
    timestamp = fixture_audio(1).verse_timestamps[0]
    audio = fixture_audio(1).model_copy(
        update={
            "provider": "quranfoundation",
            "has_ayah_timing": True,
            "has_word_timing": True,
            "timing_mapping_method": "quran_foundation_chapter_recitation_segments",
            "verse_timestamps": [
                timestamp.model_copy(
                    update={
                        "word_segments": timestamp.word_segments[1:],
                    }
                )
            ],
        }
    )

    render_audio, result = validate_audio_with_safe_fallback(audio, verses)

    assert result.compatible
    assert result.has_word_timing
    assert render_audio.has_word_timing


def test_range_time_shifting() -> None:
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    reciter = fixture_reciters()[0]
    verses = fixture_verses(1)
    audio = fixture_audio(1)
    timeline = build_range_timeline(chapter, reciter, verses, audio, 2, 2, False)
    assert timeline.timestamps[0].start_ms == 0
    assert timeline.timestamps[0].word_segments[0].start_ms == 0


def test_ayah_by_ayah_cumulative_timing() -> None:
    first = ChapterAudio(
        reciter_id="r",
        chapter_id=1,
        url="fixture://1",
        duration_ms=1000,
        verse_timestamps=[
            VerseTimestamp(
                verse_number=1,
                start_ms=0,
                end_ms=1000,
                word_segments=[
                    WordSegment(verse_number=1, word_position=1, start_ms=0, end_ms=1000)
                ],
            )
        ],
    )
    second = ChapterAudio(
        reciter_id="r",
        chapter_id=1,
        url="fixture://2",
        duration_ms=1500,
        verse_timestamps=[
            VerseTimestamp(
                verse_number=2,
                start_ms=0,
                end_ms=1500,
                word_segments=[
                    WordSegment(verse_number=2, word_position=1, start_ms=0, end_ms=1500)
                ],
            )
        ],
    )
    merged = concatenate_ayah_audio_timings([first, second])
    assert merged.duration_ms == 2500
    assert merged.verse_timestamps[1].word_segments[0].start_ms == 1000


def test_al_quran_cloud_retiming_uses_real_downloaded_part_durations() -> None:
    audio = ChapterAudio(
        reciter_id="ar.alafasy",
        chapter_id=2,
        url="alqurancloud://verse-audio",
        audio_urls=["https://example.test/1.mp3", "https://example.test/2.mp3"],
        duration_ms=3000,
        verse_timestamps=[
            VerseTimestamp(
                verse_number=3,
                start_ms=0,
                end_ms=1000,
                word_segments=[
                    WordSegment(verse_number=3, word_position=1, start_ms=0, end_ms=500),
                    WordSegment(verse_number=3, word_position=2, start_ms=500, end_ms=1000),
                ],
            ),
            VerseTimestamp(
                verse_number=4,
                start_ms=1000,
                end_ms=3000,
                word_segments=[
                    WordSegment(verse_number=4, word_position=1, start_ms=1000, end_ms=2000),
                    WordSegment(verse_number=4, word_position=2, start_ms=2000, end_ms=3000),
                ],
            ),
        ],
    )

    retimed = _retime_selected_audio(audio, audio.verse_timestamps, [900, 1300])

    assert retimed.url == "alqurancloud://concatenated-selection"
    assert retimed.audio_urls == []
    assert retimed.duration_ms == 2200
    assert [(item.start_ms, item.end_ms) for item in retimed.verse_timestamps] == [
        (0, 900),
        (900, 2200),
    ]
    assert retimed.verse_timestamps[1].word_segments[0].start_ms == 900
    assert retimed.verse_timestamps[1].word_segments[-1].end_ms == 2200
