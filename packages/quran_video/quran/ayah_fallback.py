from __future__ import annotations

from quran_video.models import ChapterAudio, VerseTimestamp


def concatenate_ayah_audio_timings(timings: list[ChapterAudio]) -> ChapterAudio:
    if not timings:
        raise ValueError("at least one ayah audio file is required")
    reciter_id = timings[0].reciter_id
    chapter_id = timings[0].chapter_id
    shifted: list[VerseTimestamp] = []
    cursor = 0
    for expected_verse, audio in enumerate(timings, start=1):
        if audio.reciter_id != reciter_id or audio.chapter_id != chapter_id:
            raise ValueError("cannot combine audio from different reciters, styles, or chapters")
        if len(audio.verse_timestamps) != 1:
            raise ValueError("each ayah audio object must contain exactly one verse timestamp")
        timestamp = audio.verse_timestamps[0]
        if timestamp.verse_number != expected_verse:
            raise ValueError("ayah audio files must be in exact ayah order")
        word_segments = [
            segment.model_copy(
                update={
                    "start_ms": segment.start_ms + cursor,
                    "end_ms": segment.end_ms + cursor,
                }
            )
            for segment in timestamp.word_segments
        ]
        shifted.append(
            timestamp.model_copy(
                update={
                    "start_ms": cursor,
                    "end_ms": cursor + audio.duration_ms,
                    "word_segments": word_segments,
                }
            )
        )
        cursor += audio.duration_ms
    return ChapterAudio(
        reciter_id=reciter_id,
        chapter_id=chapter_id,
        url="fixture://concatenated-ayah-audio",
        duration_ms=cursor,
        verse_timestamps=shifted,
    )
