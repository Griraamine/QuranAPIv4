from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from quran_video.models import RenderRequest, RenderTimeline, Verse, WordSegment
from quran_video.quran.artistic_names import surah_badge_glyph
from quran_video.quran.compatibility import BISMILLAH_ARABIC, BISMILLAH_TRANSLATION
from quran_video.rendering.fonts import (
    FontResolution,
    resolve_arabic_choice,
    resolve_english_choice,
)
from quran_video.subtitles.pagination import TextMeasurer, wrap_text

ARABIC_ASS_FONT_SCALE = 2.8
ARABIC_ASS_WRAP_WIDTH_SCALE = 2.0
ARABIC_ASS_RENDER_WIDTH_SCALE = 0.36
ARABIC_ASS_LINE_SPACING_SCALE = 0.72
ARABIC_ASS_MIN_LINE_SPACING = 0.92
ARABIC_AYAH_MARKER = "\u06dd"
ARABIC_INDIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


@dataclass(frozen=True)
class TextLayout:
    lines: list[str]
    font_size: int


@dataclass(frozen=True)
class TextPage:
    lines: list[str]
    font_size: int
    start_word: int
    end_word: int


@dataclass(frozen=True)
class SubtitlePageEvent:
    page_index: int
    start_ms: int
    end_ms: int
    active_word_position: int | None


@dataclass(frozen=True)
class AyahMarkerOverlay:
    x: int
    marker_y: int
    number_y: int
    marker_font_size: int
    number_font_size: int


MAX_TIMED_PAGE_LINES = 2


def ass_time(ms: int) -> str:
    total_cs = round(ms / 10)
    cs = total_cs % 100
    total_seconds = total_cs // 100
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _color(hex_color: str) -> str:
    value = hex_color.lstrip("#")
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H00{b}{g}{r}"


def _highlight_words(words: list[str], active_index: int, primary: str, secondary: str) -> str:
    parts = []
    for index, word in enumerate(words, start=1):
        color = primary if index == active_index else secondary
        parts.append(f"{{\\c{_color(color)}&}}{ass_escape(word)}")
    return " ".join(parts)


def _arabic_ayah_number(value: int) -> str:
    return str(value).translate(ARABIC_INDIC_DIGITS)


def _arabic_ayah_marker(verse_number: int) -> str:
    return f"{ARABIC_AYAH_MARKER}{_arabic_ayah_number(verse_number)}"


def _words_with_ayah_marker(words: list[str], verse_number: int) -> list[str]:
    marker = _arabic_ayah_marker(verse_number)
    if not words:
        return [marker]
    if ARABIC_AYAH_MARKER in words[-1]:
        return words
    marked_words = [*words]
    marked_words[-1] = f"{marked_words[-1]}{marker}"
    return marked_words


def _ass_word_text(word: str) -> str:
    return re.sub(r"\s+", "\u2060", word.strip())


def _verse_by_number(timeline: RenderTimeline) -> dict[int, Verse]:
    return {verse.verse_number: verse for verse in timeline.verses}


def _line_y_values(
    center_y: int,
    line_count: int,
    font_size: int,
    line_spacing: float,
    *,
    box_height: int | None = None,
    vertical_align: str = "middle",
) -> list[int]:
    if line_count <= 1:
        if box_height is None or vertical_align == "middle":
            return [center_y]
        _left, top, _right, bottom = _box_bounds(0, center_y, 1, box_height)
        if vertical_align == "top":
            return [top + round(font_size / 2)]
        if vertical_align == "bottom":
            return [bottom - round(font_size / 2)]
        return [center_y]
    step = round(font_size * line_spacing)
    if box_height is not None and vertical_align != "middle":
        _left, top, _right, bottom = _box_bounds(0, center_y, 1, box_height)
        if vertical_align == "top":
            first = top + round(font_size / 2)
        elif vertical_align == "bottom":
            first = bottom - round(font_size / 2) - step * (line_count - 1)
        else:
            first = center_y - round(step * (line_count - 1) / 2)
    else:
        first = center_y - round(step * (line_count - 1) / 2)
    return [first + step * index for index in range(line_count)]


def _arabic_ass_font_size(css_font_size: int) -> int:
    return round(css_font_size * ARABIC_ASS_FONT_SCALE)


def _arabic_ass_line_spacing(line_spacing: float) -> float:
    return max(ARABIC_ASS_MIN_LINE_SPACING, line_spacing * ARABIC_ASS_LINE_SPACING_SCALE)


def _block_height(line_count: int, font_size: int, line_spacing: float) -> int:
    if line_count <= 1:
        return font_size
    return round(font_size * line_spacing) * (line_count - 1) + font_size


def _fit_text_lines(
    text: str,
    font_path: Path,
    base_font_size: int,
    box_width: int,
    box_height: int,
    line_spacing: float,
    *,
    max_lines: int = 100,
    min_font_size: int = 1,
) -> TextLayout:
    width = max(1, box_width)
    height = max(1, box_height)
    for font_size in range(max(base_font_size, min_font_size), min_font_size - 1, -1):
        measurer = TextMeasurer(font_path, font_size)
        lines = wrap_text(text, measurer, width, max_lines)
        if (
            len(lines) <= max_lines
            and _block_height(len(lines), font_size, line_spacing) <= height
            and all(measurer.width(line) <= width for line in lines)
        ):
            return TextLayout(lines=lines, font_size=font_size)
    measurer = TextMeasurer(font_path, min_font_size)
    return TextLayout(lines=wrap_text(text, measurer, width, max_lines), font_size=min_font_size)


def _paginate_text_lines(
    text: str,
    font_path: Path,
    base_font_size: int,
    box_width: int,
    box_height: int,
    line_spacing: float,
    *,
    max_lines: int = MAX_TIMED_PAGE_LINES,
    min_font_size: int = 1,
) -> list[TextPage]:
    width = max(1, box_width)
    base_font_size = max(base_font_size, min_font_size)
    measurer = TextMeasurer(font_path, base_font_size)
    wrapped_lines = wrap_text(text, measurer, width, 100)
    pages: list[TextPage] = []
    cursor = 1
    for line_index in range(0, len(wrapped_lines), max_lines):
        source_lines = wrapped_lines[line_index : line_index + max_lines]
        word_count = sum(len(line.split()) for line in source_lines)
        page_text = " ".join(source_lines)
        layout = _fit_text_lines(
            page_text,
            font_path,
            base_font_size,
            box_width,
            box_height,
            line_spacing,
            max_lines=max_lines,
            min_font_size=min_font_size,
        )
        end_word = cursor + max(word_count, 1) - 1
        pages.append(
            TextPage(
                lines=layout.lines,
                font_size=layout.font_size,
                start_word=cursor,
                end_word=end_word,
            )
        )
        cursor = end_word + 1
    return pages or [TextPage(lines=[""], font_size=base_font_size, start_word=1, end_word=1)]


def _paginate_arabic_ass_words(
    words: list[str],
    font_path: Path,
    css_font_size: int,
    box_width: int,
    box_height: int,
    line_spacing: float,
) -> list[TextPage]:
    return _paginate_text_lines(
        " ".join(words),
        font_path,
        _arabic_ass_font_size(css_font_size),
        round(box_width * ARABIC_ASS_WRAP_WIDTH_SCALE),
        box_height,
        _arabic_ass_line_spacing(line_spacing),
    )


def _translation_pages_for_arabic_pages(
    translation: str,
    arabic_pages: list[TextPage],
    arabic_word_count: int,
    font_path: Path,
    base_font_size: int,
    box_width: int,
    box_height: int,
    line_spacing: float,
) -> list[TextPage]:
    if len(arabic_pages) <= 1:
        layout = _fit_text_lines(
            translation,
            font_path,
            base_font_size,
            box_width,
            box_height,
            line_spacing,
        )
        return [
            TextPage(
                lines=layout.lines,
                font_size=layout.font_size,
                start_word=1,
                end_word=max(len(translation.split()), 1),
            )
        ]
    translation_words = translation.split()
    if not translation_words:
        return [TextPage(lines=[""], font_size=base_font_size, start_word=1, end_word=1)]
    total_translation_words = len(translation_words)
    arabic_word_count = max(arabic_word_count, 1)
    pages: list[TextPage] = []
    previous_end = 0
    for index, arabic_page in enumerate(arabic_pages):
        if previous_end >= total_translation_words:
            end = total_translation_words
            chunk_text = ""
        elif index + 1 == len(arabic_pages):
            end = total_translation_words
            chunk_text = " ".join(translation_words[previous_end:end])
        else:
            end = round(arabic_page.end_word / arabic_word_count * total_translation_words)
            end = min(max(end, previous_end + 1), total_translation_words)
            chunk_text = " ".join(translation_words[previous_end:end])
        layout = _fit_text_lines(
            chunk_text,
            font_path,
            base_font_size,
            box_width,
            box_height,
            line_spacing,
            max_lines=MAX_TIMED_PAGE_LINES,
        )
        pages.append(
            TextPage(
                lines=layout.lines,
                font_size=layout.font_size,
                start_word=min(previous_end + 1, total_translation_words),
                end_word=end,
            )
        )
        previous_end = end
    return pages


def _page_index_for_word(pages: list[TextPage], word_position: int) -> int:
    for index, page in enumerate(pages):
        if page.start_word <= word_position <= page.end_word:
            return index
    if word_position < pages[0].start_word:
        return 0
    return len(pages) - 1


def _uses_ayah_level_segment(
    timestamp_start_ms: int, timestamp_end_ms: int, segments: list[WordSegment]
) -> bool:
    if len(segments) != 1:
        return False
    segment = segments[0]
    return (
        segment.word_position == 1
        and segment.start_ms == timestamp_start_ms
        and segment.end_ms == timestamp_end_ms
    )


def _subtitle_page_events(
    pages: list[TextPage],
    segments: list[WordSegment],
    verse_start_ms: int,
    verse_end_ms: int,
) -> list[SubtitlePageEvent]:
    if not segments:
        return [
            SubtitlePageEvent(
                page_index=0,
                start_ms=verse_start_ms,
                end_ms=verse_end_ms,
                active_word_position=None,
            )
        ]
    if len(pages) > 1 and _uses_ayah_level_segment(verse_start_ms, verse_end_ms, segments):
        total_words = max(pages[-1].end_word, 1)
        duration = max(verse_end_ms - verse_start_ms, 1)
        return [
            SubtitlePageEvent(
                page_index=index,
                start_ms=verse_start_ms + round((page.start_word - 1) / total_words * duration),
                end_ms=verse_start_ms + round(page.end_word / total_words * duration),
                active_word_position=None,
            )
            for index, page in enumerate(pages)
        ]
    if segments:
        events: list[SubtitlePageEvent] = []
        current_page_index: int | None = None
        current_start_ms = verse_start_ms
        current_end_ms = verse_start_ms
        current_active_word_position: int | None = None
        for index, segment in enumerate(segments):
            page_index = _page_index_for_word(pages, segment.word_position)
            segment_end_ms = _segment_display_end(segment, segments, index, verse_end_ms)
            if current_page_index is None:
                current_page_index = page_index
                current_start_ms = segment.start_ms
                current_active_word_position = segment.word_position
            elif page_index != current_page_index:
                events.append(
                    SubtitlePageEvent(
                        page_index=current_page_index,
                        start_ms=current_start_ms,
                        end_ms=max(current_end_ms, segment.start_ms),
                        active_word_position=current_active_word_position,
                    )
                )
                current_page_index = page_index
                current_start_ms = segment.start_ms
                current_active_word_position = segment.word_position
            current_end_ms = segment_end_ms
        if current_page_index is not None:
            events.append(
                SubtitlePageEvent(
                    page_index=current_page_index,
                    start_ms=current_start_ms,
                    end_ms=max(current_end_ms, current_start_ms + 1),
                    active_word_position=current_active_word_position,
                )
            )
        return events
    return [
        SubtitlePageEvent(
            page_index=_page_index_for_word(pages, segment.word_position),
            start_ms=segment.start_ms,
            end_ms=_segment_display_end(segment, segments, index, verse_end_ms),
            active_word_position=segment.word_position,
        )
        for index, segment in enumerate(segments)
    ]


def _fit_arabic_ass_lines(
    text: str,
    font_path: Path,
    css_font_size: int,
    box_width: int,
    box_height: int,
    line_spacing: float,
) -> TextLayout:
    return _fit_text_lines(
        text,
        font_path,
        _arabic_ass_font_size(css_font_size),
        round(box_width * ARABIC_ASS_WRAP_WIDTH_SCALE),
        box_height,
        _arabic_ass_line_spacing(line_spacing),
    )


def _box_bounds(center_x: int, center_y: int, width: int, height: int) -> tuple[int, int, int, int]:
    half_width = round(width / 2)
    half_height = round(height / 2)
    return (
        center_x - half_width,
        center_y - half_height,
        center_x + half_width,
        center_y + half_height,
    )


def _box_override(
    center_x: int,
    center_y: int,
    width: int,
    height: int,
    font_size: int,
    *,
    line_y: int,
    extra: str = "",
) -> str:
    left, top, right, bottom = _box_bounds(center_x, center_y, width, height)
    return (
        f"{{\\clip({left},{top},{right},{bottom})\\pos({center_x},{line_y})\\fs{font_size}{extra}}}"
    )


def _ayah_marker_overlay(
    line: str,
    font_path: Path,
    line_font_size: int,
    box_center_x: int,
    box_center_y: int,
    box_width: int,
    box_height: int,
    line_y: int,
) -> AyahMarkerOverlay:
    marker_font_size = max(48, round(line_font_size * 0.58))
    number_font_size = max(18, round(marker_font_size * 0.29))
    line_width = round(
        TextMeasurer(font_path, line_font_size).width(line) * ARABIC_ASS_RENDER_WIDTH_SCALE
    )
    marker_width = round(
        TextMeasurer(font_path, marker_font_size).width(ARABIC_AYAH_MARKER)
        * ARABIC_ASS_RENDER_WIDTH_SCALE
    )
    half_marker_width = max(1, round(marker_width / 2))
    left, _top, right, _bottom = _box_bounds(box_center_x, box_center_y, box_width, box_height)
    x = round(box_center_x - line_width / 2 - half_marker_width - marker_font_size * 0.08)
    x = min(max(x, left + half_marker_width), right - half_marker_width)
    return AyahMarkerOverlay(
        x=x,
        marker_y=line_y,
        number_y=line_y + round(marker_font_size * 0.13),
        marker_font_size=marker_font_size,
        number_font_size=number_font_size,
    )


def _transition_tag(
    request: RenderRequest,
    event_start_ms: int,
    event_end_ms: int,
    verse_start_ms: int,
    verse_end_ms: int,
) -> str:
    typography = request.typography
    if typography.text_transition != "fade" or typography.fade_duration_ms <= 0:
        return ""
    event_duration = max(0, event_end_ms - event_start_ms)
    if event_duration <= 0:
        return ""
    max_fade = event_duration // 2
    fade = min(typography.fade_duration_ms, max_fade)
    if fade == 0:
        return ""
    return f"\\fad({fade},{fade})"


def generate_ass(
    output_path: Path,
    timeline: RenderTimeline,
    request: RenderRequest,
    fonts: FontResolution,
) -> Path:
    typography = request.typography
    badge_style = request.badge_style
    arabic_font = resolve_arabic_choice(typography.arabic_font_key, fonts.arabic_quran)
    badge_arabic_font = fonts.badge_surah
    english_font = resolve_english_choice(typography.english_font_key, fonts.english)
    arabic_ass_font_size = _arabic_ass_font_size(typography.arabic_font_size)
    badge_measurer = TextMeasurer(badge_arabic_font.path, badge_style.artistic_surah_size)
    verse_lookup = _verse_by_number(timeline)
    header = f"""[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Arabic,{arabic_font.family},{arabic_ass_font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,0,0,5,150,150,0,1
Style: Translation,{english_font.family},{typography.translation_font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,1,0,0,100,100,0,0,1,0,0,5,150,150,0,1
Style: BadgeArt,{badge_arabic_font.family},{badge_style.artistic_surah_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,0,0,7,64,64,52,1
Style: BadgeRange,Open Sans,{badge_style.range_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,600,0,0,0,100,100,0,0,1,0,0,7,64,64,96,1
Style: AyahNumber,Open Sans,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,700,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    duration = ass_time(timeline.duration_ms)
    if request.badge.enabled:
        range_text = f"{request.ayah_from}-{request.ayah_to}"
        artistic_name = surah_badge_glyph(
            request.chapter_id,
            request.badge.arabic_surah or timeline.chapter.arabic_name,
        )
        range_x = badge_style.x + badge_measurer.width(artistic_name) + badge_style.line_gap
        range_y = badge_style.y + round(badge_style.artistic_surah_size * 0.16)
        events.extend(
            [
                f"Dialogue: 5,0:00:00.00,{duration},BadgeArt,,0,0,0,,{{\\pos({badge_style.x},{badge_style.y})}}{ass_escape(artistic_name)}",
                f"Dialogue: 5,0:00:00.00,{duration},BadgeRange,,0,0,0,,{{\\pos({range_x},{range_y})}}{ass_escape(range_text)}",
            ]
        )
    if timeline.include_bismillah and timeline.bismillah_duration_ms > 0:
        bismillah = BISMILLAH_ARABIC
        bismillah_transition = _transition_tag(
            request, 0, timeline.bismillah_duration_ms, 0, timeline.bismillah_duration_ms
        )
        bismillah_layout = _fit_arabic_ass_lines(
            bismillah,
            arabic_font.path,
            typography.arabic_font_size,
            typography.arabic_box_width,
            typography.arabic_box_height,
            typography.line_spacing,
        )
        for line, y in zip(
            bismillah_layout.lines,
            _line_y_values(
                typography.arabic_box_y,
                len(bismillah_layout.lines),
                bismillah_layout.font_size,
                _arabic_ass_line_spacing(typography.line_spacing),
                box_height=typography.arabic_box_height,
                vertical_align="bottom",
            ),
            strict=True,
        ):
            override = _box_override(
                typography.arabic_box_x,
                typography.arabic_box_y,
                typography.arabic_box_width,
                typography.arabic_box_height,
                bismillah_layout.font_size,
                line_y=y,
                extra=bismillah_transition,
            )
            events.append(
                f"Dialogue: 1,0:00:00.00,{ass_time(timeline.bismillah_duration_ms)},Arabic,,0,0,0,,{override}{ass_escape(line)}"
            )
        bismillah_translation_layout = _fit_text_lines(
            BISMILLAH_TRANSLATION,
            english_font.path,
            typography.translation_font_size,
            typography.translation_box_width,
            typography.translation_box_height,
            typography.line_spacing,
        )
        for line, y in zip(
            bismillah_translation_layout.lines,
            _line_y_values(
                typography.translation_box_y,
                len(bismillah_translation_layout.lines),
                bismillah_translation_layout.font_size,
                typography.line_spacing,
                box_height=typography.translation_box_height,
                vertical_align="top",
            ),
            strict=True,
        ):
            override = _box_override(
                typography.translation_box_x,
                typography.translation_box_y,
                typography.translation_box_width,
                typography.translation_box_height,
                bismillah_translation_layout.font_size,
                line_y=y,
                extra=bismillah_transition,
            )
            events.append(
                f"Dialogue: 1,0:00:00.00,{ass_time(timeline.bismillah_duration_ms)},Translation,,0,0,0,,{override}{ass_escape(line)}"
            )
    for timestamp in timeline.timestamps:
        verse = verse_lookup[timestamp.verse_number]
        segments = sorted(timestamp.word_segments, key=lambda item: item.start_ms)
        arabic_words = [_ass_word_text(word.text_uthmani) for word in verse.words]
        arabic_pages = _paginate_arabic_ass_words(
            arabic_words,
            arabic_font.path,
            typography.arabic_font_size,
            typography.arabic_box_width,
            typography.arabic_box_height,
            typography.line_spacing,
        )
        translation_pages = _translation_pages_for_arabic_pages(
            verse.translation,
            arabic_pages,
            len(arabic_words),
            english_font.path,
            typography.translation_font_size,
            typography.translation_box_width,
            typography.translation_box_height,
            typography.line_spacing,
        )
        for page_event in _subtitle_page_events(
            arabic_pages,
            segments,
            timestamp.start_ms,
            timestamp.end_ms,
        ):
            arabic_page = arabic_pages[page_event.page_index]
            translation_page = translation_pages[
                min(page_event.page_index, len(translation_pages) - 1)
            ]
            start = ass_time(page_event.start_ms)
            end = ass_time(page_event.end_ms)
            transition = _transition_tag(
                request,
                page_event.start_ms,
                page_event.end_ms,
                timestamp.start_ms,
                timestamp.end_ms,
            )
            line_y_values = _line_y_values(
                typography.arabic_box_y,
                len(arabic_page.lines),
                arabic_page.font_size,
                _arabic_ass_line_spacing(typography.line_spacing),
                box_height=typography.arabic_box_height,
                vertical_align="bottom",
            )
            word_offset = arabic_page.start_word - 1
            final_word_line: tuple[str, int] | None = None
            for line, y in zip(arabic_page.lines, line_y_values, strict=True):
                line_words = line.split()
                active_index = (
                    page_event.active_word_position - word_offset
                    if page_event.active_word_position is not None
                    else 0
                )
                line_start_word = word_offset + 1
                word_offset += len(line_words)
                if line_start_word <= len(arabic_words) <= word_offset:
                    final_word_line = (line, y)
                arabic_line = _highlight_words(
                    line_words,
                    active_index,
                    "#FFFFFF",
                    "#FFFFFF",
                )
                override = _box_override(
                    typography.arabic_box_x,
                    typography.arabic_box_y,
                    typography.arabic_box_width,
                    typography.arabic_box_height,
                    arabic_page.font_size,
                    line_y=y,
                    extra=transition,
                )
                events.append(f"Dialogue: 2,{start},{end},Arabic,,0,0,0,,{override}{arabic_line}")
            if final_word_line is not None:
                marker_line, marker_line_y = final_word_line
                marker = _ayah_marker_overlay(
                    marker_line,
                    arabic_font.path,
                    arabic_page.font_size,
                    typography.arabic_box_x,
                    typography.arabic_box_y,
                    typography.arabic_box_width,
                    typography.arabic_box_height,
                    marker_line_y,
                )
                events.append(
                    f"Dialogue: 3,{start},{end},Arabic,,0,0,0,,{{\\pos({marker.x},{marker.marker_y})\\fs{marker.marker_font_size}{transition}}}{ARABIC_AYAH_MARKER}"
                )
                events.append(
                    f"Dialogue: 4,{start},{end},AyahNumber,,0,0,0,,{{\\pos({marker.x},{marker.number_y})\\fs{marker.number_font_size}{transition}}}{_arabic_ayah_number(verse.verse_number)}"
                )
            for line, y in zip(
                translation_page.lines,
                _line_y_values(
                    typography.translation_box_y,
                    len(translation_page.lines),
                    translation_page.font_size,
                    typography.line_spacing,
                    box_height=typography.translation_box_height,
                    vertical_align="top",
                ),
                strict=True,
            ):
                override = _box_override(
                    typography.translation_box_x,
                    typography.translation_box_y,
                    typography.translation_box_width,
                    typography.translation_box_height,
                    translation_page.font_size,
                    line_y=y,
                    extra=transition,
                )
                events.append(
                    f"Dialogue: 2,{start},{end},Translation,,0,0,0,,{override}{ass_escape(line)}"
                )
    output_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return output_path


def _segment_display_end(
    segment: WordSegment,
    segments: list[WordSegment],
    index: int,
    verse_end_ms: int,
) -> int:
    if index + 1 < len(segments) and segments[index + 1].start_ms > segment.end_ms:
        return min(segments[index + 1].start_ms, verse_end_ms)
    if index + 1 == len(segments):
        return verse_end_ms
    return min(segment.end_ms, verse_end_ms)
