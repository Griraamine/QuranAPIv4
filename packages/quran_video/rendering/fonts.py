from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

ARABIC_FONT_CHOICES: dict[str, tuple[str, str]] = {
    "uthmanic": ("Uthmanic", "KFGQPC Uthmanic Script HAFS"),
    "amiri": ("Amiri", "Amiri Quran"),
    "noto_naskh": ("Noto Naskh", "Noto Naskh Arabic"),
    "scheherazade": ("Scheherazade", "Scheherazade New"),
    "scheherazade_b": ("Scheherazade B", "Scheherazade New Bold"),
    "lateef": ("Lateef", "Lateef"),
    "indo_pak": ("Indo-Pak", "Jameel Noori Nastaleeq"),
    "al_mushaf": ("Al Mushaf", "Al Mushaf"),
    "poetry": ("Poetry", "Arabic Typesetting"),
    "hafs_ex1": ("Hafs Ex1", "Hafs"),
    "muhammadi": ("Muhammadi", "Muhammadi Quranic"),
    "me_quran": ("Me Quran", "Me Quran"),
    "nabi": ("Nabi", "Nabi"),
    "aref_ruqaa": ("Aref Ruqaa", "Aref Ruqaa"),
    "mirza": ("Mirza", "Mirza"),
    "reem_kufi": ("Reem Kufi", "Reem Kufi"),
    "harmattan": ("Harmattan", "Harmattan"),
    "system": ("System", "sans-serif"),
}

ENGLISH_FONT_CHOICES: dict[str, tuple[str, str]] = {
    "system": ("System", "sans-serif"),
    "georgia": ("Georgia", "Georgia"),
    "palatino": ("Palatino", "Palatino Linotype"),
    "times": ("Times", "Times New Roman"),
    "avenir": ("Avenir", "Avenir"),
    "didot": ("Didot", "Didot"),
}


@dataclass(frozen=True)
class ResolvedFont:
    requested: str
    family: str
    path: Path
    warning: str | None = None


@dataclass(frozen=True)
class FontResolution:
    arabic_quran: ResolvedFont
    arabic_ui: ResolvedFont
    english: ResolvedFont
    badge_surah: ResolvedFont


def _bundled_font_path(filename: str) -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "fonts" / filename


def _fc_match(family: str) -> ResolvedFont:
    result = subprocess.run(
        ["fc-match", "-f", "%{family}\n%{file}\n", family],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    resolved_family = lines[0].split(",", 1)[0] if lines else family
    path = Path(lines[1]) if len(lines) > 1 else Path()
    return ResolvedFont(requested=family, family=resolved_family, path=path)


def resolve_arabic_choice(font_key: str, fallback: ResolvedFont) -> ResolvedFont:
    choice = ARABIC_FONT_CHOICES.get(font_key)
    if choice is None or choice[1] == "sans-serif":
        return fallback
    resolved = _fc_match(choice[1])
    if resolved.path:
        return resolved
    return fallback


def resolve_english_choice(
    font_key: str, fallback: ResolvedFont, *, italic: bool = False
) -> ResolvedFont:
    choice = ENGLISH_FONT_CHOICES.get(font_key)
    if choice is None or choice[1] == "sans-serif":
        if italic:
            resolved = _fc_match(f"{fallback.family}:style=Italic")
            if resolved.path:
                return resolved
        return fallback
    pattern = f"{choice[1]}:style=Italic" if italic else choice[1]
    resolved = _fc_match(pattern)
    if resolved.path:
        return resolved
    return fallback


def _exact_or_none(family: str) -> ResolvedFont | None:
    resolved = _fc_match(family)
    if resolved.family.casefold() == family.casefold():
        return resolved
    return None


def resolve_fonts() -> FontResolution:
    badge_surah = ResolvedFont(
        requested="surah-name-v4",
        family="surah-name-v4",
        path=_bundled_font_path("surah-name-v4.ttf"),
    )
    arabic_quran = _exact_or_none("Amiri Quran")
    if arabic_quran is None:
        fallback = _exact_or_none("Amiri") or _fc_match("Amiri")
        arabic_quran = ResolvedFont(
            requested="Amiri Quran",
            family=fallback.family,
            path=fallback.path,
            warning="Amiri Quran unavailable; falling back to Amiri",
        )
    arabic_ui = _exact_or_none("Amiri") or arabic_quran
    english = (
        _exact_or_none("Times New Roman")
        or _exact_or_none("Times")
        or _fc_match("Liberation Serif")
    )
    return FontResolution(
        arabic_quran=arabic_quran,
        arabic_ui=arabic_ui,
        english=english,
        badge_surah=badge_surah,
    )
