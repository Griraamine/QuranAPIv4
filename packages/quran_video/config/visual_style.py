from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path

from quran_video.config import get_settings
from quran_video.models import VisualStyleSettings


def visual_style_path() -> Path:
    return get_settings().repo_root / "config" / "user_visual_style.json"


def load_visual_style(path: Path | None = None) -> VisualStyleSettings:
    target = path or visual_style_path()
    if not target.exists():
        return VisualStyleSettings()
    try:
        return VisualStyleSettings.model_validate_json(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return VisualStyleSettings()


def save_visual_style(style: VisualStyleSettings, path: Path | None = None) -> VisualStyleSettings:
    target = path or visual_style_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(style.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    with suppress(OSError):
        target.chmod(0o666)
    return style
