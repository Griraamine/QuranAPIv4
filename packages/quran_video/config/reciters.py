from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import regex
from pydantic import BaseModel, Field, ValidationError

from quran_video.config import get_settings
from quran_video.models import ChapterReciter


class ReciterNameOverride(BaseModel):
    reciter_id: str = Field(min_length=1)
    provider: str | None = None
    reciter_english_name: str = ""
    reciter_arabic_name: str = ""


def reciter_names_path() -> Path:
    return get_settings().repo_root / "config" / "reciters.json"


@lru_cache
def load_reciter_name_overrides(path: str | None = None) -> dict[str, ReciterNameOverride]:
    target = Path(path) if path else reciter_names_path()
    if not target.exists():
        return {}
    try:
        payload: Any = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        payload = payload.get("reciters", [])
    if not isinstance(payload, list):
        return {}
    overrides: dict[str, ReciterNameOverride] = {}
    for item in payload:
        try:
            override = ReciterNameOverride.model_validate(item)
        except ValidationError:
            continue
        overrides[override.reciter_id] = override
    return overrides


def apply_reciter_name_overrides(reciters: list[ChapterReciter]) -> list[ChapterReciter]:
    overrides = load_reciter_name_overrides()
    if not overrides:
        return reciters
    updated: list[ChapterReciter] = []
    for reciter in reciters:
        override = overrides.get(reciter.id)
        if override is None or (override.provider and override.provider != reciter.provider):
            updated.append(reciter)
            continue
        updated.append(
            reciter.model_copy(
                update={
                    "english_name": override.reciter_english_name or reciter.english_name,
                    "arabic_name": override.reciter_arabic_name or reciter.arabic_name,
                }
            )
        )
    return updated


def resolve_reciter_name(reciter: ChapterReciter) -> ChapterReciter:
    return apply_reciter_name_overrides([reciter])[0]


def contains_arabic(value: str) -> bool:
    return bool(regex.search(r"\p{Script=Arabic}", value))


def require_bilingual_reciter(reciter: ChapterReciter) -> ChapterReciter:
    resolved = resolve_reciter_name(reciter)
    if not contains_arabic(resolved.arabic_name):
        raise ValueError(
            f"Arabic display name missing for reciter {resolved.id}; "
            "add its verified Arabic name to config/reciters.json before publishing"
        )
    return resolved
