from __future__ import annotations

from dataclasses import dataclass

from quran_video.models import (
    Chapter,
    ChapterAudio,
    CompatibilityResult,
    QuranWord,
    RenderTimeline,
    TimingStatus,
    Verse,
    WordSegment,
)
from quran_video.quran.text import remove_arabic_diacritics

BISMILLAH_ARABIC = "بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ"
BISMILLAH_TRANSLATION = "In the name of Allah, the Entirely Merciful, the Especially Merciful."
BISMILLAH_WORDS = ("بسم", "الله", "الرحمن", "الرحيم")


@dataclass(frozen=True)
class BismillahDecision:
    visible: bool
    enabled_by_default: bool
    may_include: bool
    reason: str


def validate_ayah_range(chapter: Chapter, ayah_from: int, ayah_to: int) -> None:
    if ayah_from < 1 or ayah_to < ayah_from or ayah_to > chapter.verse_count:
        raise ValueError(
            f"ayah range must be between 1 and {chapter.verse_count}, with first <= last"
        )


def bismillah_policy(
    chapter_id: int, ayah_from: int, audio: ChapterAudio | None = None
) -> BismillahDecision:
    if chapter_id == 1:
        return BismillahDecision(False, False, False, "Surah 1 contains Bismillah as ayah 1")
    if chapter_id == 9:
        return BismillahDecision(False, False, False, "Surah 9 never has a separate Bismillah")
    if ayah_from != 1:
        return BismillahDecision(
            False,
            False,
            False,
            "Bismillah is available only when range starts at ayah 1",
        )
    if audio and audio.provider == "mp3quran":
        may_include = audio.intro_timing is not None
        return BismillahDecision(
            may_include,
            may_include,
            may_include,
            "MP3Quran ayah-0 intro can show a separate Basmalah when ayah text includes it"
            if may_include
            else "MP3Quran timing does not expose an ayah-0 intro",
        )
    leading_ms = 0
    if audio and audio.verse_timestamps:
        leading_ms = audio.verse_timestamps[0].start_ms
    may_include = leading_ms > 250
    reason = (
        "Source audio has measurable leading Bismillah"
        if may_include
        else "Source audio does not expose a measurable leading Bismillah"
    )
    return BismillahDecision(True, True, may_include, reason)


def _normalize_arabic_word(value: str) -> str:
    normalized = remove_arabic_diacritics(value).replace("ـ", "")
    normalized = normalized.replace("ٱ", "ا").replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    return "".join(character for character in normalized if "\u0600" <= character <= "\u06ff")


def _split_leading_bismillah(verse: Verse) -> tuple[Verse, bool]:
    if len(verse.words) < len(BISMILLAH_WORDS):
        return verse, False
    leading = tuple(
        _normalize_arabic_word(word.text_uthmani) for word in verse.words[: len(BISMILLAH_WORDS)]
    )
    if leading != BISMILLAH_WORDS:
        return verse, False
    remaining_words = [
        QuranWord(
            position=index,
            text_uthmani=word.text_uthmani,
            translation=word.translation,
        )
        for index, word in enumerate(verse.words[len(BISMILLAH_WORDS) :], start=1)
    ]
    return (
        verse.model_copy(
            update={
                "text_uthmani": " ".join(word.text_uthmani for word in remaining_words),
                "words": remaining_words,
            }
        ),
        True,
    )


def validate_audio_compatibility(
    audio: ChapterAudio,
    verses: list[Verse],
    probed_duration_ms: int | None = None,
    tolerance_ms: int = 1250,
    minimum_timing_coverage: float = 0.90,
) -> CompatibilityResult:
    if audio.timing_status and audio.timing_status != TimingStatus.timing_available:
        return CompatibilityResult(
            reciter_id=audio.reciter_id,
            moshaf_id=audio.moshaf_id,
            chapter_id=audio.chapter_id,
            compatible=False,
            reason=audio.timing_status.value,
            status=audio.timing_status,
            timing_read_id=audio.timing_read_id,
            timing_mapping_method=audio.timing_mapping_method,
        )
    expected_verses = {verse.verse_number for verse in verses}
    timestamp_verses = [timestamp.verse_number for timestamp in audio.verse_timestamps]
    if set(timestamp_verses) != expected_verses or len(timestamp_verses) != len(expected_verses):
        return CompatibilityResult(
            reciter_id=audio.reciter_id,
            moshaf_id=audio.moshaf_id,
            chapter_id=audio.chapter_id,
            compatible=False,
            reason="verse timestamps do not cover every selected ayah exactly once",
            status=TimingStatus.text_timing_mismatch,
            timing_read_id=audio.timing_read_id,
            timing_mapping_method=audio.timing_mapping_method,
        )
    last_end = 0
    word_lookup = {(verse.verse_number, word.position) for verse in verses for word in verse.words}
    for timestamp in sorted(audio.verse_timestamps, key=lambda item: item.start_ms):
        if timestamp.start_ms < last_end:
            return CompatibilityResult(
                reciter_id=audio.reciter_id,
                moshaf_id=audio.moshaf_id,
                chapter_id=audio.chapter_id,
                compatible=False,
                reason="verse timestamps overlap or are not monotonic",
                status=TimingStatus.timing_invalid,
                timing_read_id=audio.timing_read_id,
                timing_mapping_method=audio.timing_mapping_method,
            )
        if audio.has_ayah_timing and not audio.has_word_timing:
            if len(timestamp.word_segments) != 1:
                return CompatibilityResult(
                    reciter_id=audio.reciter_id,
                    moshaf_id=audio.moshaf_id,
                    chapter_id=audio.chapter_id,
                    compatible=False,
                    reason="ayah-level timing must contain one render segment per ayah",
                    status=TimingStatus.timing_invalid,
                    timing_read_id=audio.timing_read_id,
                    timing_mapping_method=audio.timing_mapping_method,
                )
            segment = timestamp.word_segments[0]
            if (
                segment.verse_number != timestamp.verse_number
                or segment.start_ms != timestamp.start_ms
                or segment.end_ms != timestamp.end_ms
            ):
                return CompatibilityResult(
                    reciter_id=audio.reciter_id,
                    moshaf_id=audio.moshaf_id,
                    chapter_id=audio.chapter_id,
                    compatible=False,
                    reason="ayah-level segment must exactly match the ayah timing",
                    status=TimingStatus.timing_invalid,
                    timing_read_id=audio.timing_read_id,
                    timing_mapping_method=audio.timing_mapping_method,
                )
            last_end = timestamp.end_ms
            continue
        last_word_end = timestamp.start_ms
        seen_words: set[tuple[int, int]] = set()
        for segment in sorted(timestamp.word_segments, key=lambda item: item.start_ms):
            key = (segment.verse_number, segment.word_position)
            if key not in word_lookup:
                return CompatibilityResult(
                    reciter_id=audio.reciter_id,
                    moshaf_id=audio.moshaf_id,
                    chapter_id=audio.chapter_id,
                    compatible=False,
                    reason="word timing cannot be mapped to fetched Quran words",
                    status=TimingStatus.text_timing_mismatch,
                    timing_read_id=audio.timing_read_id,
                    timing_mapping_method=audio.timing_mapping_method,
                )
            if segment.start_ms < last_word_end:
                return CompatibilityResult(
                    reciter_id=audio.reciter_id,
                    moshaf_id=audio.moshaf_id,
                    chapter_id=audio.chapter_id,
                    compatible=False,
                    reason="word timing intervals overlap",
                    status=TimingStatus.timing_invalid,
                    timing_read_id=audio.timing_read_id,
                    timing_mapping_method=audio.timing_mapping_method,
                )
            seen_words.add(key)
            last_word_end = segment.end_ms
        expected_words = {
            (verse.verse_number, word.position)
            for verse in verses
            if verse.verse_number == timestamp.verse_number
            for word in verse.words
        }
        if seen_words != expected_words and not (
            audio.provider == "quranfoundation"
            and seen_words.issubset(expected_words)
            and len(seen_words) / max(len(expected_words), 1) >= 0.75
        ):
            return CompatibilityResult(
                reciter_id=audio.reciter_id,
                moshaf_id=audio.moshaf_id,
                chapter_id=audio.chapter_id,
                compatible=False,
                reason="word timing is incomplete for one or more ayahs",
                status=TimingStatus.text_timing_mismatch,
                timing_read_id=audio.timing_read_id,
                timing_mapping_method=audio.timing_mapping_method,
            )
        last_end = timestamp.end_ms
    duration = probed_duration_ms or audio.duration_ms
    if last_end > duration + tolerance_ms:
        return CompatibilityResult(
            reciter_id=audio.reciter_id,
            moshaf_id=audio.moshaf_id,
            chapter_id=audio.chapter_id,
            compatible=False,
            reason="final timestamp exceeds probed audio duration",
            status=TimingStatus.audio_timing_mismatch,
            timing_read_id=audio.timing_read_id,
            timing_mapping_method=audio.timing_mapping_method,
        )
    if probed_duration_ms and last_end < duration * minimum_timing_coverage:
        return CompatibilityResult(
            reciter_id=audio.reciter_id,
            moshaf_id=audio.moshaf_id,
            chapter_id=audio.chapter_id,
            compatible=False,
            reason="timing covers too little of the probed audio duration",
            status=TimingStatus.audio_timing_mismatch,
            timing_read_id=audio.timing_read_id,
            timing_mapping_method=audio.timing_mapping_method,
        )
    return CompatibilityResult(
        reciter_id=audio.reciter_id,
        moshaf_id=audio.moshaf_id,
        chapter_id=audio.chapter_id,
        compatible=True,
        has_word_timing=audio.has_word_timing,
        has_ayah_timing=audio.has_ayah_timing,
        status=TimingStatus.timing_available,
        timing_read_id=audio.timing_read_id,
        timing_mapping_method=audio.timing_mapping_method,
    )


def audio_with_ayah_level_segments(audio: ChapterAudio) -> ChapterAudio:
    return audio.model_copy(
        update={
            "verse_timestamps": [
                timestamp.model_copy(
                    update={
                        "word_segments": [
                            WordSegment(
                                verse_number=timestamp.verse_number,
                                word_position=1,
                                start_ms=timestamp.start_ms,
                                end_ms=timestamp.end_ms,
                            )
                        ]
                    }
                )
                for timestamp in audio.verse_timestamps
            ],
            "has_word_timing": False,
            "has_ayah_timing": True,
            "timing_mapping_method": (
                f"{audio.timing_mapping_method}_ayah_fallback"
                if audio.timing_mapping_method
                else "ayah_timing_fallback"
            ),
        }
    )


def validate_audio_with_safe_fallback(
    audio: ChapterAudio,
    verses: list[Verse],
    probed_duration_ms: int | None = None,
    tolerance_ms: int = 1250,
    minimum_timing_coverage: float = 0.90,
) -> tuple[ChapterAudio, CompatibilityResult]:
    result = validate_audio_compatibility(
        audio,
        verses,
        probed_duration_ms=probed_duration_ms,
        tolerance_ms=tolerance_ms,
        minimum_timing_coverage=minimum_timing_coverage,
    )
    if result.compatible:
        return audio, result
    can_fallback_to_ayah_timing = (
        audio.provider == "quranfoundation"
        and audio.has_ayah_timing
        and audio.has_word_timing
        and result.status in {TimingStatus.text_timing_mismatch, TimingStatus.timing_invalid}
    )
    if not can_fallback_to_ayah_timing:
        return audio, result
    fallback_audio = audio_with_ayah_level_segments(audio)
    fallback_result = validate_audio_compatibility(
        fallback_audio,
        verses,
        probed_duration_ms=probed_duration_ms,
        tolerance_ms=tolerance_ms,
        minimum_timing_coverage=minimum_timing_coverage,
    )
    if fallback_result.compatible:
        return fallback_audio, fallback_result
    return audio, result


def build_range_timeline(
    chapter: Chapter,
    reciter,
    verses: list[Verse],
    audio: ChapterAudio,
    ayah_from: int,
    ayah_to: int,
    include_bismillah: bool,
) -> RenderTimeline:
    selected_verses = [verse for verse in verses if ayah_from <= verse.verse_number <= ayah_to]
    stripped_bismillah = False
    if audio.provider == "mp3quran" and chapter.id not in {1, 9} and ayah_from == 1:
        adjusted_verses = []
        for verse in selected_verses:
            if verse.verse_number == 1:
                verse, stripped_bismillah = _split_leading_bismillah(verse)
            adjusted_verses.append(verse)
        selected_verses = adjusted_verses
    selected_numbers = {verse.verse_number for verse in selected_verses}
    selected_timestamps = [
        timestamp
        for timestamp in audio.verse_timestamps
        if timestamp.verse_number in selected_numbers
    ]
    if not selected_timestamps:
        raise ValueError("selected range has no timestamps")
    selected_timestamps.sort(key=lambda item: item.start_ms)
    policy = bismillah_policy(chapter.id, ayah_from, audio)
    start_ms = selected_timestamps[0].start_ms
    include_intro = include_bismillah and policy.may_include
    bismillah_duration_ms = selected_timestamps[0].start_ms if include_intro else 0
    if audio.provider == "mp3quran":
        include_intro = False
        if ayah_from == 1 and audio.intro_timing:
            start_ms = min(audio.intro_timing.start_ms, selected_timestamps[0].start_ms)
            if include_bismillah and stripped_bismillah:
                bismillah_duration_ms = selected_timestamps[0].start_ms - start_ms
                include_intro = bismillah_duration_ms > 0
    if include_intro:
        start_ms = 0
    end_ms = selected_timestamps[-1].end_ms
    shifted = []
    for timestamp in selected_timestamps:
        shifted_words = []
        for segment in timestamp.word_segments:
            shifted_segment = segment.model_copy(
                update={
                    "start_ms": segment.start_ms - start_ms,
                    "end_ms": segment.end_ms - start_ms,
                }
            )
            if shifted_segment.start_ms < 0 or shifted_segment.end_ms > end_ms - start_ms:
                raise ValueError("shifted word segment is outside output duration")
            shifted_words.append(shifted_segment)
        shifted.append(
            timestamp.model_copy(
                update={
                    "start_ms": timestamp.start_ms - start_ms,
                    "end_ms": timestamp.end_ms - start_ms,
                    "word_segments": shifted_words,
                }
            )
        )
    return RenderTimeline(
        chapter=chapter,
        reciter=reciter,
        verses=selected_verses,
        timestamps=shifted,
        duration_ms=end_ms - start_ms,
        include_bismillah=include_intro,
        bismillah_duration_ms=bismillah_duration_ms if include_intro else 0,
    )
