from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

from quran_video.metadata import generate_metadata, pack_tags
from quran_video.models import (
    BackgroundMode,
    BackgroundStyleSettings,
    Chapter,
    ChapterReciter,
    QuranWord,
    RecitationStyle,
    RenderRequest,
    RenderTimeline,
    Verse,
    VerseTimestamp,
    WordSegment,
)
from quran_video.models.domain import BadgeSettings, ThumbnailStyleSettings, TypographySettings
from quran_video.quran.compatibility import build_range_timeline
from quran_video.quran.fixtures import fixture_audio, fixture_reciters, fixture_verses
from quran_video.rendering import calculate_center_crop, resolve_fonts, video_loop_crossfade_plan
from quran_video.rendering.ffmpeg import _audio_filter, _video_filter
from quran_video.subtitles.ass import (
    ARABIC_AYAH_MARKER,
    _arabic_ass_font_size,
    _arabic_ass_line_spacing,
    _arabic_ayah_number,
    _fit_arabic_ass_lines,
    _line_y_values,
    _paginate_arabic_ass_words,
    ass_escape,
    ass_time,
    generate_ass,
)
from quran_video.thumbnails import generator as thumbnail_generator
from quran_video.thumbnails.generator import _dim_background, _draw_centered, generate_thumbnail


def request() -> RenderRequest:
    reciter = fixture_reciters()[0]
    return RenderRequest(
        reciter_id=reciter.id,
        chapter_id=1,
        ayah_from=1,
        ayah_to=2,
        include_bismillah=False,
        background_mode=BackgroundMode.single,
        background_ids=["sample.jpg"],
        typography=TypographySettings(),
        badge=BadgeSettings(
            enabled=True,
            arabic_surah="الفاتحة",
            english_surah="Al-Fatihah",
            arabic_reciter=reciter.arabic_name,
            english_reciter=reciter.english_name,
        ),
    )


def test_ass_escaping_and_event_generation(tmp_path: Path) -> None:
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    reciter = fixture_reciters()[0]
    timeline = build_range_timeline(
        chapter, reciter, fixture_verses(1), fixture_audio(1), 1, 2, False
    )
    assert ass_escape("{x}\\y") == "\\{x\\}\\\\y"
    output = generate_ass(tmp_path / "sample.ass", timeline, request(), resolve_fonts())
    text = output.read_text(encoding="utf-8")
    assert "Dialogue:" in text
    assert "Saheeh" not in text
    assert "Style: Arabic,Amiri Quran,190" in text
    assert "Style: BadgeArt,surah-name-v4" in text
    assert "\ue001" in text
    assert "BadgeRange" in text
    assert "BadgeEnglish" not in text
    assert "Al-Fatihah  1-2" not in text
    assert "ٱلْحَمْدُ" in text
    assert "Style: AyahNumber,Open Sans" in text
    assert ARABIC_AYAH_MARKER in text
    assert f"}}{_arabic_ayah_number(1)}" in text
    assert f"}}{_arabic_ayah_number(2)}" in text


def test_ass_groups_unchanged_single_page_subtitle_events(tmp_path: Path) -> None:
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    reciter = fixture_reciters()[0]
    timeline = build_range_timeline(
        chapter, reciter, fixture_verses(1), fixture_audio(1), 1, 1, False
    )

    output = generate_ass(tmp_path / "word-segments.ass", timeline, request(), resolve_fonts())
    text = output.read_text(encoding="utf-8")

    assert "Dialogue: 2,0:00:00.00,0:00:02.80,Arabic" in text
    assert "Dialogue: 2,0:00:00.70,0:00:01.40,Arabic" not in text


def _long_ayah_timeline(
    *,
    ayah_level_timing: bool = False,
    translation: str = "First second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth.",
) -> RenderTimeline:
    words = [
        "أَوَّلُ",
        "ثَانِي",
        "ثَالِثُ",
        "رَابِعُ",
        "خَامِسُ",
        "سَادِسُ",
        "سَابِعُ",
        "ثَامِنُ",
        "تَاسِعُ",
        "عَاشِرُ",
        "حَادِي",
        "ثَانِيَ",
    ]
    verse = Verse(
        chapter_id=2,
        verse_number=282,
        text_uthmani=" ".join(words),
        translation=translation,
        words=[
            QuranWord(position=index, text_uthmani=word, translation="")
            for index, word in enumerate(words, start=1)
        ],
    )
    if ayah_level_timing:
        segments = [
            WordSegment(
                verse_number=282,
                word_position=1,
                start_ms=0,
                end_ms=6000,
            )
        ]
    else:
        segments = [
            WordSegment(
                verse_number=282,
                word_position=index,
                start_ms=(index - 1) * 500,
                end_ms=index * 500,
            )
            for index in range(1, len(words) + 1)
        ]
    return RenderTimeline(
        chapter=Chapter(id=2, arabic_name="البقرة", english_name="Al-Baqarah", verse_count=286),
        reciter=fixture_reciters()[0],
        verses=[verse],
        timestamps=[
            VerseTimestamp(
                verse_number=282,
                start_ms=0,
                end_ms=6000,
                word_segments=segments,
            )
        ],
        duration_ms=6000,
    )


def test_long_ayah_is_displayed_as_timed_two_line_pages(tmp_path: Path) -> None:
    timeline = _long_ayah_timeline()
    render_request = request().model_copy(
        update={
            "chapter_id": 2,
            "ayah_from": 282,
            "ayah_to": 282,
            "typography": TypographySettings(
                arabic_font_size=86,
                arabic_box_width=120,
                arabic_box_height=420,
                translation_box_width=420,
                translation_box_height=160,
            ),
        }
    )
    fonts = resolve_fonts()
    pages = _paginate_arabic_ass_words(
        [word.text_uthmani for word in timeline.verses[0].words],
        fonts.arabic_quran.path,
        render_request.typography.arabic_font_size,
        render_request.typography.arabic_box_width,
        render_request.typography.arabic_box_height,
        render_request.typography.line_spacing,
    )
    assert len(pages) > 1

    output = generate_ass(tmp_path / "long-pages.ass", timeline, render_request, fonts)
    text = output.read_text(encoding="utf-8")
    first_event_lines = [
        line
        for line in text.splitlines()
        if line.startswith("Dialogue: 2,0:00:00.00,0:00:01.00,Arabic")
    ]
    assert 1 <= len(first_event_lines) <= 2
    second_page_first_word = timeline.verses[0].words[pages[1].start_word - 1].text_uthmani
    second_page_start = ass_time((pages[1].start_word - 1) * 500)

    assert second_page_first_word not in "\n".join(first_event_lines)
    assert f"Dialogue: 2,{second_page_start}," in text
    assert second_page_first_word in text
    assert ARABIC_AYAH_MARKER in text
    assert _arabic_ayah_number(282) in text


def test_long_ayah_pages_advance_with_proportional_ayah_timing(tmp_path: Path) -> None:
    timeline = _long_ayah_timeline(ayah_level_timing=True)
    render_request = request().model_copy(
        update={
            "chapter_id": 2,
            "ayah_from": 282,
            "ayah_to": 282,
            "typography": TypographySettings(
                arabic_font_size=86,
                arabic_box_width=120,
                arabic_box_height=420,
                translation_box_width=420,
                translation_box_height=160,
            ),
        }
    )
    output = generate_ass(
        tmp_path / "long-pages-ayah-timing.ass", timeline, render_request, resolve_fonts()
    )
    text = output.read_text(encoding="utf-8")

    arabic_starts = sorted(
        {
            match.group(1)
            for match in re.finditer(r"Dialogue: 2,(0:00:\d{2}\.\d{2}),.*?,Arabic", text)
        }
    )
    assert len(arabic_starts) > 1
    assert arabic_starts[0] == "0:00:00.00"


def test_long_ayah_translation_can_have_fewer_words_than_arabic_pages(tmp_path: Path) -> None:
    timeline = _long_ayah_timeline(translation="Short")
    render_request = request().model_copy(
        update={
            "chapter_id": 2,
            "ayah_from": 282,
            "ayah_to": 282,
            "typography": TypographySettings(
                arabic_font_size=86,
                arabic_box_width=120,
                arabic_box_height=420,
                translation_box_width=420,
                translation_box_height=160,
            ),
        }
    )

    output = generate_ass(
        tmp_path / "long-pages-short-translation.ass", timeline, render_request, resolve_fonts()
    )
    text = output.read_text(encoding="utf-8")

    assert "Dialogue:" in text
    assert ARABIC_AYAH_MARKER in text
    assert _arabic_ayah_number(282) in text


def test_ass_text_boxes_clip_and_shrink_to_fit(tmp_path: Path) -> None:
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    reciter = fixture_reciters()[0]
    timeline = build_range_timeline(
        chapter, reciter, fixture_verses(1), fixture_audio(1), 1, 2, False
    )
    render_request = request().model_copy(
        update={
            "typography": TypographySettings(
                arabic_font_size=96,
                translation_font_size=58,
                arabic_box_width=220,
                arabic_box_height=60,
                translation_box_width=260,
                translation_box_height=56,
            )
        }
    )
    output = generate_ass(tmp_path / "boxed.ass", timeline, render_request, resolve_fonts())
    text = output.read_text(encoding="utf-8")

    assert "\\clip(" in text
    arabic_sizes = [int(value) for value in re.findall(r"Arabic.*?\\fs(\d+)", text)]
    translation_sizes = [int(value) for value in re.findall(r"Translation.*?\\fs(\d+)", text)]
    assert arabic_sizes
    assert translation_sizes
    assert max(arabic_sizes) < _arabic_ass_font_size(96)
    assert max(translation_sizes) < 58


def test_arabic_box_fitting_uses_render_scaled_font_size(tmp_path: Path) -> None:
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    reciter = fixture_reciters()[0]
    timeline = build_range_timeline(
        chapter, reciter, fixture_verses(1), fixture_audio(1), 1, 1, False
    )
    render_request = request().model_copy(
        update={
            "typography": TypographySettings(
                arabic_font_size=96,
                arabic_box_width=1920,
                arabic_box_height=120,
            )
        }
    )
    output = generate_ass(tmp_path / "scaled-arabic.ass", timeline, render_request, resolve_fonts())
    text = output.read_text(encoding="utf-8")

    arabic_sizes = [int(value) for value in re.findall(r"Arabic.*?\\fs(\d+)", text)]
    assert arabic_sizes
    assert max(arabic_sizes) <= 120


def test_ass_bismillah_intro_renders_translation_and_fade(tmp_path: Path) -> None:
    chapter = Chapter(id=2, arabic_name="البقرة", english_name="Al-Baqarah", verse_count=286)
    reciter = fixture_reciters()[0]
    timeline = build_range_timeline(
        chapter, reciter, fixture_verses(2), fixture_audio(2), 1, 1, False
    ).model_copy(
        update={
            "include_bismillah": True,
            "bismillah_duration_ms": 1000,
            "duration_ms": 2800,
        }
    )
    render_request = request().model_copy(
        update={
            "chapter_id": 2,
            "include_bismillah": True,
            "typography": TypographySettings(text_transition="fade", fade_duration_ms=250),
        }
    )

    output = generate_ass(
        tmp_path / "bismillah-fade.ass", timeline, render_request, resolve_fonts()
    )
    text = output.read_text(encoding="utf-8")

    assert "بِسْمِ" in text
    assert "Entirely Merciful" in text
    assert "\\fad(250,250)" in text


def test_ass_fades_visible_text_screen_in_and_out(tmp_path: Path) -> None:
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    reciter = fixture_reciters()[0]
    audio = fixture_audio(1)
    first_timestamp = audio.verse_timestamps[0]
    shortened_last_segment = first_timestamp.word_segments[-1].model_copy(
        update={"end_ms": first_timestamp.end_ms - 350}
    )
    audio = audio.model_copy(
        update={
            "verse_timestamps": [
                first_timestamp.model_copy(
                    update={
                        "word_segments": [
                            *first_timestamp.word_segments[:-1],
                            shortened_last_segment,
                        ]
                    }
                ),
                *audio.verse_timestamps[1:],
            ]
        }
    )
    timeline = build_range_timeline(chapter, reciter, fixture_verses(1), audio, 1, 1, False)
    render_request = request().model_copy(
        update={"typography": TypographySettings(text_transition="fade", fade_duration_ms=250)}
    )

    output = generate_ass(tmp_path / "fade-out.ass", timeline, render_request, resolve_fonts())
    text = output.read_text(encoding="utf-8")

    assert "Dialogue: 2,0:00:00.00,0:00:02.80,Arabic" in text
    assert "\\fad(250,250)" in text


def test_text_box_vertical_alignment_anchors_to_requested_edges() -> None:
    arabic_y = _line_y_values(
        500,
        3,
        50,
        _arabic_ass_line_spacing(1.47),
        box_height=300,
        vertical_align="bottom",
    )
    translation_y = _line_y_values(
        880,
        3,
        40,
        1.47,
        box_height=200,
        vertical_align="top",
    )

    assert arabic_y[-1] == 625
    assert translation_y[0] == 800
    assert _arabic_ass_line_spacing(1.47) < 1.1


def test_long_arabic_ayah_uses_wide_box_before_shrinking() -> None:
    text = (
        "۞ إِنَّ رَبَّكَ يَعْلَمُ أَنَّكَ تَقُومُ أَدْنَىٰ مِن ثُلُثَىِ ٱلَّيْلِ "
        "وَنِصْفَهُۥ وَثُلُثَهُۥ وَطَآئِفَةٌۭ مِّنَ ٱلَّذِينَ مَعَكَ ۚ وَٱللَّهُ "
        "يُقَدِّرُ ٱلَّيْلَ وَٱلنَّهَارَ ۚ عَلِمَ أَن لَّن تُحْصُوهُ فَتَابَ عَلَيْكُمْ ۖ "
        "فَٱقْرَءُوا۟ مَا تَيَسَّرَ مِنَ ٱلْقُرْءَانِ ۚ عَلِمَ أَن سَيَكُونُ مِنكُم "
        "مَّرْضَىٰ ۙ وَءَاخَرُونَ يَضْرِبُونَ فِى ٱلْأَرْضِ يَبْتَغُونَ مِن فَضْلِ "
        "ٱللَّهِ ۙ وَءَاخَرُونَ يُقَٰتِلُونَ فِى سَبِيلِ ٱللَّهِ ۖ فَٱقْرَءُوا۟ مَا "
        "تَيَسَّرَ مِنْهُ ۚ وَأَقِيمُوا۟ ٱلصَّلَوٰةَ وَءَاتُوا۟ ٱلزَّكَوٰةَ "
        "وَأَقْرِضُوا۟ ٱللَّهَ قَرْضًا حَسَنًۭا ۚ وَمَا تُقَدِّمُوا۟ لِأَنفُسِكُم "
        "مِّنْ خَيْرٍۢ تَجِدُوهُ عِندَ ٱللَّهِ هُوَ خَيْرًۭا وَأَعْظَمَ أَجْرًۭا ۚ "
        "وَٱسْتَغْفِرُوا۟ ٱللَّهَ ۖ إِنَّ ٱللَّهَ غَفُورٌۭ رَّحِيمٌۢ"
    )
    layout = _fit_arabic_ass_lines(
        text,
        resolve_fonts().arabic_quran.path,
        86,
        1920,
        382,
        1.47,
    )

    assert layout.font_size >= 91
    assert len(layout.lines) <= 4


def test_badge_accepts_empty_english_label_after_arabic_only_badge_change() -> None:
    reciter = fixture_reciters()[0]
    badge = BadgeSettings(
        enabled=True,
        arabic_surah="الفاتحة",
        english_surah="",
        arabic_reciter=reciter.arabic_name,
        english_reciter="",
    )
    assert badge.english_surah == ""


def test_single_background_slideshow_request_is_normalized_to_single_mode() -> None:
    body = request().model_copy(update={"background_mode": BackgroundMode.slideshow})
    normalized = RenderRequest.model_validate(body.model_dump())
    assert normalized.background_mode == BackgroundMode.single


def test_font_fallback_and_background_plans() -> None:
    fonts = resolve_fonts()
    assert fonts.english.family
    assert fonts.badge_surah.path.exists()
    assert calculate_center_crop(3840, 2160) == (1920, 1080)
    plan = video_loop_crossfade_plan(2.0, 5.0)
    assert plan[1].crossfade_seconds == 0.75


def test_thumbnail_fitting_and_compression(tmp_path: Path) -> None:
    background = tmp_path / "sample.jpg"
    Image.new("RGB", (1920, 1080), (180, 180, 180)).save(background)
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    reciter = fixture_reciters()[0]
    timeline = build_range_timeline(
        chapter, reciter, fixture_verses(1), fixture_audio(1), 1, 2, False
    )
    output = generate_thumbnail(background, tmp_path / "thumb.jpg", timeline, request())
    with Image.open(output) as image:
        assert image.size == (1280, 720)
    assert output.stat().st_size < 2 * 1024 * 1024


def test_thumbnail_english_size_follows_translation_typography(tmp_path: Path, monkeypatch) -> None:
    background = tmp_path / "sample.jpg"
    Image.new("RGB", (1920, 1080), (180, 180, 180)).save(background)
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    reciter = fixture_reciters()[0]
    timeline = build_range_timeline(
        chapter, reciter, fixture_verses(1), fixture_audio(1), 1, 2, False
    )
    render_request = request().model_copy(
        update={
            "typography": TypographySettings(translation_font_size=27),
            "thumbnail_style": ThumbnailStyleSettings(english_size=120),
        }
    )
    targets: list[tuple[str, int]] = []
    original_fit_font = thumbnail_generator._fit_font

    def tracking_fit_font(*args, **kwargs):
        text = args[1]
        target = args[2]
        targets.append((text, target))
        return original_fit_font(*args, **kwargs)

    monkeypatch.setattr(thumbnail_generator, "_fit_font", tracking_fit_font)

    generate_thumbnail(background, tmp_path / "thumb.jpg", timeline, render_request)

    english_targets = [target for text, target in targets if text.startswith("Surah ")]
    assert english_targets == [27]


def test_background_dim_blends_image_toward_black() -> None:
    image = Image.new("RGB", (10, 10), (200, 200, 200))
    assert _dim_background(image, 0).getpixel((0, 0)) == (200, 200, 200)
    assert _dim_background(image, 50).getpixel((0, 0)) == (100, 100, 100)
    assert BackgroundStyleSettings(dim_opacity=90).dim_opacity == 90


def test_video_dim_filter_preserves_color_before_subtitles(tmp_path: Path) -> None:
    filter_chain = _video_filter(tmp_path / "subtitles.ass", 35)
    assert "format=rgba,drawbox=" in filter_chain
    assert filter_chain.index("format=rgba") < filter_chain.index("drawbox=")
    assert filter_chain.index("drawbox=") < filter_chain.index("ass=")


def test_audio_filter_pads_or_trims_to_timeline_duration() -> None:
    assert _audio_filter(12.345) == (
        "aresample=48000,apad,atrim=duration=12.345,asetpts=PTS-STARTPTS"
    )


def test_arabic_ass_font_size_is_calibrated_to_preview_scale() -> None:
    assert _arabic_ass_font_size(91) == 255


def test_thumbnail_text_draws_around_requested_center() -> None:
    fonts = resolve_fonts()
    font = ImageFont.truetype(
        str(fonts.arabic_quran.path),
        72,
        layout_engine=ImageFont.Layout.RAQM,
    )
    image = Image.new("RGB", (640, 320), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    center = (320, 160)
    _draw_centered(
        image,
        draw,
        center,
        "سورة الْفَاتِحَة",
        font,
        "rtl",
        0,
        (255, 255, 255),
    )
    bbox = ImageChops.difference(image, Image.new("RGB", image.size, (0, 0, 0))).getbbox()
    assert bbox is not None
    visible_center_x = (bbox[0] + bbox[2]) / 2
    visible_center_y = (bbox[1] + bbox[3]) / 2
    assert abs(visible_center_x - center[0]) <= 4
    assert abs(visible_center_y - center[1]) <= 4


def test_metadata_title_and_tags_limits() -> None:
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    metadata = generate_metadata(chapter, fixture_reciters()[0], 1, 7)
    assert len(metadata.title) <= 100
    assert len(",".join(metadata.tags)) <= 500
    assert pack_tags(["Quran", "quran", "Quran recitation"]) == ["Quran", "Quran recitation"]


def test_quran_foundation_metadata_attribution() -> None:
    chapter = Chapter(id=1, arabic_name="الفاتحة", english_name="Al-Fatihah", verse_count=7)
    reciter = fixture_reciters()[0].model_copy(
        update={"provider": "quranfoundation", "audio_source_name": "Quran.Foundation Content API"}
    )
    metadata = generate_metadata(chapter, reciter, 1, 7)

    assert "Quran.Foundation Content API" in metadata.description
    assert "Saheeh International translation" in metadata.description
    assert "Audio recitation source: Quran.Foundation Content API" in metadata.description


def test_metadata_formats_nested_recitation_name_as_plain_text() -> None:
    chapter = Chapter(id=51, arabic_name="الذاريات", english_name="Adh-Dhariyat", verse_count=60)
    reciter = ChapterReciter(
        id="9",
        english_name="Al-Minshawi",
        arabic_name="المنشاوي",
        style=RecitationStyle(
            id="murattal",
            name="{'name': 'Murattal', 'translated_name': {'name': 'Murattal', 'language_name': 'english'}}",
        ),
        provider="quranfoundation",
    )

    metadata = generate_metadata(chapter, reciter, 1, 60)

    assert "Recitation/Rewaya: Murattal" in metadata.description
    assert "translated_name" not in metadata.description
    assert "{'name':" not in metadata.description
