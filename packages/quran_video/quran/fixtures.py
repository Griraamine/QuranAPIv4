from __future__ import annotations

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


def fixture_chapters() -> list[Chapter]:
    names = [
        (1, "الفاتحة", "Al-Fatihah", "The Opener", 7, "makkah"),
        (2, "البقرة", "Al-Baqarah", "The Cow", 286, "madinah"),
        (3, "آل عمران", "Ali 'Imran", "Family of Imran", 200, "madinah"),
    ]
    chapters = [
        Chapter(
            id=chapter_id,
            arabic_name=arabic,
            english_name=english,
            translated_name=meaning,
            verse_count=count,
            revelation_place=place,
        )
        for chapter_id, arabic, english, meaning, count, place in names
    ]
    for chapter_id in range(4, 115):
        chapters.append(
            Chapter(
                id=chapter_id,
                arabic_name=f"سورة {chapter_id}",
                english_name=f"Surah {chapter_id}",
                translated_name=None,
                verse_count=3,
                revelation_place=None,
            )
        )
    return chapters


def fixture_reciters() -> list[ChapterReciter]:
    return [
        ChapterReciter(
            id="fixture-reciter",
            english_name="Fixture Reciter",
            arabic_name="القارئ التجريبي",
            style=RecitationStyle(id="murattal", name="Murattal"),
        ),
        ChapterReciter(
            id="fixture-incompatible",
            english_name="Incomplete Fixture",
            arabic_name="تجريبي غير مكتمل",
            style=RecitationStyle(id="murattal", name="Murattal"),
        ),
    ]


def fixture_verses(chapter_id: int) -> list[Verse]:
    if chapter_id == 1:
        return [
            Verse(
                chapter_id=1,
                verse_number=1,
                text_uthmani="بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ",
                translation="In the name of Allah, the Entirely Merciful, the Especially Merciful.",
                words=[
                    QuranWord(position=1, text_uthmani="بِسْمِ", translation="In (the) name"),
                    QuranWord(position=2, text_uthmani="ٱللَّهِ", translation="of Allah"),
                    QuranWord(
                        position=3, text_uthmani="ٱلرَّحْمَـٰنِ", translation="the Entirely Merciful"
                    ),
                    QuranWord(
                        position=4, text_uthmani="ٱلرَّحِيمِ", translation="the Especially Merciful"
                    ),
                ],
            ),
            Verse(
                chapter_id=1,
                verse_number=2,
                text_uthmani="ٱلْحَمْدُ لِلَّهِ رَبِّ ٱلْعَـٰلَمِينَ",
                translation="[All] praise is [due] to Allah, Lord of the worlds.",
                words=[
                    QuranWord(position=1, text_uthmani="ٱلْحَمْدُ", translation="All praise"),
                    QuranWord(position=2, text_uthmani="لِلَّهِ", translation="is for Allah"),
                    QuranWord(position=3, text_uthmani="رَبِّ", translation="Lord"),
                    QuranWord(position=4, text_uthmani="ٱلْعَـٰلَمِينَ", translation="of the worlds"),
                ],
            ),
        ]
    return [
        Verse(
            chapter_id=chapter_id,
            verse_number=1,
            text_uthmani="نَصٌّ عَرَبِيٌّ تَجْرِيبِيٌّ",
            translation="Non-production fixture text for renderer validation.",
            words=[
                QuranWord(position=1, text_uthmani="نَصٌّ", translation="text"),
                QuranWord(position=2, text_uthmani="عَرَبِيٌّ", translation="Arabic"),
                QuranWord(position=3, text_uthmani="تَجْرِيبِيٌّ", translation="fixture"),
            ],
        ),
        Verse(
            chapter_id=chapter_id,
            verse_number=2,
            text_uthmani="لِفَحْصِ تَزَامُنِ ٱلْكَلِمَاتِ",
            translation="For checking synchronized word timing.",
            words=[
                QuranWord(position=1, text_uthmani="لِفَحْصِ", translation="for checking"),
                QuranWord(position=2, text_uthmani="تَزَامُنِ", translation="synchronization"),
                QuranWord(position=3, text_uthmani="ٱلْكَلِمَاتِ", translation="of words"),
            ],
        ),
    ]


def fixture_audio(chapter_id: int, reciter_id: str = "fixture-reciter") -> ChapterAudio:
    verses = fixture_verses(chapter_id)
    timestamps: list[VerseTimestamp] = []
    cursor = 0
    for verse in verses:
        verse_duration = max(1800, len(verse.words) * 700)
        word_duration = verse_duration // len(verse.words)
        segments: list[WordSegment] = []
        for word in verse.words:
            start = cursor + (word.position - 1) * word_duration
            end = cursor + word.position * word_duration
            segments.append(
                WordSegment(
                    verse_number=verse.verse_number,
                    word_position=word.position,
                    start_ms=start,
                    end_ms=end,
                )
            )
        timestamps.append(
            VerseTimestamp(
                verse_number=verse.verse_number,
                start_ms=cursor,
                end_ms=cursor + verse_duration,
                word_segments=segments,
            )
        )
        cursor += verse_duration
    return ChapterAudio(
        reciter_id=reciter_id,
        chapter_id=chapter_id,
        url=f"fixture://audio/{chapter_id}.wav",
        duration_ms=cursor,
        verse_timestamps=timestamps,
    )
