from __future__ import annotations

import json
from pathlib import Path

import pytest
import regex
from PIL import Image

from quran_video.config.reciters import (
    apply_reciter_name_overrides,
    contains_arabic,
    load_reciter_name_overrides,
    require_bilingual_reciter,
)
from quran_video.metadata import generate_metadata
from quran_video.metadata.generator import generate_title
from quran_video.models import (
    Chapter,
    ChapterReciter,
    RecitationStyle,
    RenderRequest,
    RenderTimeline,
)
from quran_video.models.domain import BadgeSettings
from quran_video.thumbnails import generator as thumbnails

ROOT = Path(__file__).resolve().parents[2]
NAMES = json.loads((ROOT / "config/reciters.json").read_text())["reciters"]


def voice(reciter_id="3", provider="quranfoundation"):
    return ChapterReciter(
        id=reciter_id,
        english_name="Abdur-Rahman as-Sudais",
        arabic_name="Abdur-Rahman as-Sudais",
        provider=provider,
        style=RecitationStyle(id="murattal", name="Murattal"),
    )


@pytest.fixture(autouse=True)
def clear_names_cache():
    load_reciter_name_overrides.cache_clear()
    yield
    load_reciter_name_overrides.cache_clear()


@pytest.mark.parametrize("row", NAMES, ids=[r["reciter_id"] for r in NAMES])
def test_provider_english_fallback_is_replaced_across_bilingual_metadata(row):
    reciter = voice(row["reciter_id"])
    chapter = Chapter(id=5, english_name="Al-Ma'idah", arabic_name="المائدة", verse_count=120)
    resolved = require_bilingual_reciter(reciter)
    assert contains_arabic(resolved.arabic_name)
    metadata = generate_metadata(chapter, reciter, 1, 120)
    assert resolved.arabic_name in metadata.title
    assert resolved.arabic_name in metadata.thumbnail_text_arabic
    assert resolved.arabic_name in metadata.description
    assert resolved.arabic_name in metadata.playlist_title
    assert resolved.english_name in metadata.thumbnail_text_english
    assert len(regex.findall(r"\X", metadata.title)) <= 100


def test_provider_id_collisions_do_not_rename_other_provider_reciters():
    reciter = voice(provider="mp3quran").model_copy(update={"arabic_name": "قارئ آخر"})
    assert apply_reciter_name_overrides([reciter])[0] == reciter


def test_unknown_missing_arabic_name_blocks_publication():
    with pytest.raises(ValueError, match="Arabic display name missing"):
        require_bilingual_reciter(voice("new-provider-id"))


def test_unknown_real_arabic_name_remains_usable():
    reciter = voice("new-provider-id").model_copy(update={"arabic_name": "عبد الرحمن السديس"})
    assert require_bilingual_reciter(reciter) == reciter


def test_long_titles_drop_descriptor_before_either_language():
    chapter = Chapter(id=51, english_name="Adh-Dhariyat", arabic_name="الذاريات", verse_count=60)
    reciter = voice("175").model_copy(update={"english_name": "Abdullah Hamad Abu Sharida"})
    title = generate_title(chapter, reciter)
    assert "عبد الله حمد أبو شريدة" in title
    assert "Sharida" in title
    assert "الذاريات" in title
    assert len(regex.findall(r"\X", title)) <= 100


def test_extremely_long_custom_names_still_keep_both_scripts():
    chapter = Chapter(
        id=1,
        english_name="Very Long Chapter Name " * 5,
        arabic_name="اسم سورة طويلة " * 5,
        verse_count=7,
    )
    reciter = voice("custom").model_copy(
        update={
            "english_name": "A Very Long Reciter Name " * 5,
            "arabic_name": "اسم القارئ العربي الطويل " * 5,
        }
    )
    title = generate_title(chapter, reciter)
    assert contains_arabic(title)
    assert regex.search(r"[a-zA-Z]", title)
    assert len(regex.findall(r"\X", title)) <= 100


def test_thumbnail_corrects_stale_latin_arabic_badge_and_renders_both_lines(tmp_path, monkeypatch):
    reciter = voice()
    chapter = Chapter(id=1, english_name="Al-Fatihah", arabic_name="الفاتحة", verse_count=7)
    timeline = RenderTimeline(
        chapter=chapter, reciter=reciter, verses=[], timestamps=[], duration_ms=1000
    )
    request = RenderRequest(
        reciter_id="3",
        chapter_id=1,
        ayah_to=7,
        badge=BadgeSettings(arabic_reciter=reciter.english_name),
    )
    background = tmp_path / "background.jpg"
    Image.new("RGB", (1280, 720), (30, 45, 60)).save(background)
    drawn = []
    original = thumbnails._draw_centered

    def capture(*args, **kwargs):
        drawn.append((args[3], args[5]))
        return original(*args, **kwargs)

    monkeypatch.setattr(thumbnails, "_draw_centered", capture)
    output = thumbnails.generate_thumbnail(
        background, tmp_path / "thumbnail.jpg", timeline, request
    )
    assert any("عبد الرحمن السديس" in text and direction == "rtl" for text, direction in drawn)
    assert any(reciter.english_name in text and direction is None for text, direction in drawn)
    assert not any(reciter.english_name in text and direction == "rtl" for text, direction in drawn)
    with Image.open(output) as image:
        assert image.size == (1280, 720)
    assert output.stat().st_size < 2 * 1024 * 1024


def test_short_title_does_not_split_recording_annotation():
    chapter = Chapter(id=107, english_name="Al-Ma'un", arabic_name="الماعون", verse_count=7)
    reciter = voice("168").model_copy(
        update={"english_name": "Muhammad Siddiq al-Minshawi(with kids)"}
    )
    title = generate_title(chapter, reciter)
    assert title.count("(") == title.count(")")
    assert "al-Minshawi(with |" not in title
    assert "al-Minshawi" in title
    assert "المنشاوي" in title


def test_short_title_uses_reciter_name_instead_of_beta_label():
    chapter = Chapter(id=51, english_name="Adh-Dhariyat", arabic_name="الذاريات", verse_count=60)
    reciter = voice("176").model_copy(update={"english_name": "Ahmed Abdelhamid Tahoun - beta"})
    title = generate_title(chapter, reciter)
    assert "Tahoun" in title
    assert "| beta |" not in title
    assert "أحمد عبد الحميد طاحون" in title
