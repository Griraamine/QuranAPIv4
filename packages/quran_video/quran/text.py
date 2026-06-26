from __future__ import annotations

import html
import re
import unicodedata

TAG_RE = re.compile(r"<[^>]+>")
SUP_RE = re.compile(r"<sup\b[^>]*>.*?</sup>", re.IGNORECASE | re.DOTALL)
FOOTNOTE_RE = re.compile(r"[\u00b9\u00b2\u00b3\u2070-\u2079]+|\[\d+]|(?<=\w)\(\d+\)")
SPACE_RE = re.compile(r"\s+")
ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")


def clean_translation_text(value: str) -> str:
    without_sup = SUP_RE.sub("", value)
    without_tags = TAG_RE.sub("", without_sup)
    decoded = html.unescape(without_tags)
    without_notes = FOOTNOTE_RE.sub("", decoded)
    return SPACE_RE.sub(" ", without_notes).strip()


def normalize_lookup_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^a-z0-9\u0600-\u06ff]+", " ", normalized)
    return SPACE_RE.sub(" ", normalized).strip()


def remove_arabic_diacritics(value: str) -> str:
    return ARABIC_DIACRITICS_RE.sub("", unicodedata.normalize("NFC", value))
