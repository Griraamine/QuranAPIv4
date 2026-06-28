from __future__ import annotations

import ast
from typing import Any

import regex

from quran_video.models import Chapter, ChapterReciter, YouTubeMetadata
from quran_video.quran.text import normalize_lookup_text, remove_arabic_diacritics

ARABIC_RECITER_OVERRIDES = {
    "mahmoud khalil al husary": "الحصري",
    "mahmoud khalil al hussary": "الحصري",
    "al husary": "الحصري",
    "al hussary": "الحصري",
}


def _hashtags(value: str) -> str:
    cleaned = regex.sub(r"[^\p{Letter}\p{Number}]+", "_", value.strip())
    return cleaned.strip("_")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = " ".join(item.split()).strip()
        if not cleaned:
            continue
        key = normalize_lookup_text(cleaned)
        if key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _english_variants(name: str) -> list[str]:
    variants = {
        name,
        name.replace("-", ""),
        name.replace("-", " "),
        name.replace("Al-", "Al "),
        name.replace("Al-", "El "),
        regex.sub(r"^Al[- ]", "", name),
        name.replace("'", ""),
        name.replace("’", ""),
    }
    tokens = [token for token in regex.split(r"[\s-]+", name) if token]
    if len(tokens) >= 2:
        variants.add(" ".join(tokens[:2]))
        variants.add(" ".join(tokens[-2:]))
    return _dedupe(list(variants))


def pack_tags(candidates: list[str], limit: int = 450) -> list[str]:
    packed: list[str] = []
    total = 0
    for tag in _dedupe(candidates):
        next_total = total + len(tag.encode("utf-8")) + (1 if packed else 0)
        if next_total <= limit:
            packed.append(tag)
            total = next_total
    return packed


def generate_title(chapter: Chapter, reciter: ChapterReciter) -> str:
    short_reciter = _short_english_reciter(reciter.english_name)
    arabic_reciter = _arabic_reciter_name(reciter)
    title = (
        f"Surah {chapter.english_name} | {reciter.english_name} | "
        f"Arabic & English Subtitles | سورة {chapter.arabic_name} {arabic_reciter}"
    )
    if len(regex.findall(r"\X", title)) <= 100:
        return title
    bilingual_compact = (
        f"Surah {chapter.english_name} | {short_reciter} | "
        f"Arabic & English Subtitles | سورة {chapter.arabic_name} {arabic_reciter}"
    )
    if len(regex.findall(r"\X", bilingual_compact)) <= 100:
        return bilingual_compact
    compact = f"Surah {chapter.english_name} | {reciter.english_name} | Arabic & English Subtitles"
    if len(regex.findall(r"\X", compact)) <= 100:
        return compact
    graphemes = regex.findall(r"\X", compact)
    return "".join(graphemes[:100])


def _short_english_reciter(name: str) -> str:
    tokens = [token for token in regex.split(r"\s+", name.strip()) if token]
    for token in reversed(tokens):
        if token.casefold().startswith("al"):
            return token
    return tokens[-1] if tokens else name


def _arabic_reciter_name(reciter: ChapterReciter) -> str:
    if regex.search(r"\p{Script=Arabic}", reciter.arabic_name):
        return reciter.arabic_name
    normalized = normalize_lookup_text(reciter.english_name)
    return ARABIC_RECITER_OVERRIDES.get(normalized, reciter.arabic_name)


def _display_recitation_name(value: Any) -> str:
    if isinstance(value, dict):
        translated = value.get("translated_name")
        if isinstance(translated, dict) and translated.get("name"):
            return str(translated["name"])
        if translated:
            return str(translated)
        if value.get("name"):
            return str(value["name"])
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.startswith("{") and cleaned.endswith("}"):
            try:
                return _display_recitation_name(ast.literal_eval(cleaned))
            except (SyntaxError, ValueError):
                pass
        return cleaned
    return str(value).strip() if value else ""


def generate_metadata(
    chapter: Chapter,
    reciter: ChapterReciter,
    ayah_from: int,
    ayah_to: int,
) -> YouTubeMetadata:
    english_surah = chapter.english_name
    arabic_surah = chapter.arabic_name
    english_reciter = reciter.english_name
    arabic_reciter = _arabic_reciter_name(reciter)
    selected_moshaf = reciter.moshafs[0] if reciter.moshafs else None
    recitation_name = _display_recitation_name(
        selected_moshaf.name if selected_moshaf else reciter.style.name
    )
    if reciter.provider == "mp3quran":
        text_source = "Al Quran Cloud / Islamic Network"
        timing_source = "MP3Quran.net"
        audio_source = "MP3Quran.net"
        source_lines = f"""مصدر النص العربي والترجمة الإنجليزية: {text_source}
Arabic Quran text and English translation source: {text_source}

مصدر توقيت الآيات | Ayah timing source: {timing_source}
Audio recitation source: {audio_source}
Reciter: {english_reciter}
Recitation/Rewaya: {recitation_name}"""
    elif reciter.provider == "quranfoundation":
        source_lines = f"""مصدر النص العربي والترجمة الإنجليزية وتوقيت الآيات والكلمات: Quran.Foundation Content API
Arabic Quran text, Saheeh International translation, ayah timing, and word timing source: Quran.Foundation Content API

مصدر التلاوة | Audio recitation source: Quran.Foundation Content API
Reciter: {english_reciter}
Recitation/Rewaya: {recitation_name}"""
    else:
        source_lines = f"""مصدر بيانات النص والتوقيت والترجمة: {reciter.audio_source_name}
Quran text, timing, and translation data: {reciter.audio_source_name}

مصدر التلاوة | Recitation source: {reciter.audio_source_name}"""
    description = f"""سورة {arabic_surah} بصوت القارئ {arabic_reciter}
الآيات {ayah_from}–{ayah_to}

Surah {english_surah}, recited by {english_reciter}
Ayahs {ayah_from}–{ayah_to}

استمع إلى تلاوة القرآن الكريم مع النص العربي المتزامن، معاني الكلمات باللغة الإنجليزية، وترجمة معاني القرآن باللغة الإنجليزية.

Listen to the Quran with synchronized Arabic text, word-by-word English meanings, and the Saheeh International translation of the meanings.

peaceful Quran recitation with Arabic and English subtitles for reflection, focus, and calm listening.
beautiful Quran recitation / quran tilawat relaxing for daily listening, study, and sleep.

اللهم اجعل القرآن ربيع قلوبنا ونور صدورنا وذهاب همومنا.

القارئ | Reciter: {arabic_reciter} | {english_reciter}
الرواية | Recitation/Rewaya: {recitation_name}
السورة | Surah: {arabic_surah} | {english_surah}
الآيات | Ayahs: {ayah_from}–{ayah_to}
النص العربي | Arabic text: Uthmani script
الترجمة الإنجليزية | English translation: Saheeh International

{source_lines}
الخلفية مقدمة من صاحب القناة | Background media provided by the channel owner

#Quran #القرآن_الكريم #{_hashtags(english_surah)} #{_hashtags(arabic_surah)} #{_hashtags(english_reciter)} #{_hashtags(arabic_reciter)}
#QuranRecitation #PeacefulQuran #BeautifulQuranRecitation #Surah{_hashtags(english_surah)}
"""
    candidates = [
        english_surah,
        f"Surah {english_surah}",
        f"Surat {english_surah}",
        arabic_surah,
        f"سورة {arabic_surah}",
        english_reciter,
        arabic_reciter,
        *_english_variants(english_reciter),
        remove_arabic_diacritics(arabic_reciter),
        "Quran",
        "Quran recitation",
        "Quran recitation for sleep",
        "peaceful Quran recitation",
        "beautiful Quran recitation",
        "quran tilawat relaxing",
        "relaxing Quran recitation",
        "Holy Quran",
        "القرآن الكريم",
        "تلاوة القرآن",
        "full surah",
        "Quran with English subtitles",
        "Quran English translation",
        "Quran word by word",
        "Arabic Quran",
        "Arabic and English subtitles",
        "Quran with Arabic and English subtitles",
        "Quran recitation with translation",
        "Quran recitation English subtitles",
        "Quran for reflection",
        f"{english_surah} full",
        f"{english_surah} Quran",
        f"Surah {english_surah} Arabic English subtitles",
        f"Surah {english_surah} beautiful recitation",
        f"Surah {english_surah} peaceful recitation",
        f"{english_reciter} Quran",
        f"{english_reciter} {english_surah}",
        f"{english_reciter} Quran recitation",
        f"{english_reciter} Arabic English subtitles",
        f"{arabic_reciter} {arabic_surah}",
    ]
    return YouTubeMetadata(
        title=generate_title(chapter, reciter),
        description=" ".join(description.splitlines()).replace("  ", "\n").strip()
        if len(description) > 5000
        else description.strip(),
        tags=pack_tags(candidates),
        hashtags=[
            "#Quran",
            "#القرآن_الكريم",
            f"#{_hashtags(english_surah)}",
            f"#{_hashtags(arabic_surah)}",
        ],
        thumbnail_text_arabic=f"سورة {arabic_surah} | {arabic_reciter}",
        thumbnail_text_english=f"Surah {english_surah} | {english_reciter}",
        playlist_title=f"{english_reciter} | {arabic_reciter} Quran Recitations",
        playlist_description=f"Quran recitations by {english_reciter} | تلاوات القرآن الكريم بصوت {arabic_reciter}",
    )
