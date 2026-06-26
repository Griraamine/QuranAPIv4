from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from quran_video.config import get_settings
from quran_video.models import ChapterReciter


class ReciterNameOverride(BaseModel):
    reciter_id: str = Field(min_length=1)
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
        if override is None:
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
