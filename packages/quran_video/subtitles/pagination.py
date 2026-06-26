from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


@dataclass
class TextMeasurer:
    font_path: Path
    font_size: int

    def __post_init__(self) -> None:
        self.font = ImageFont.truetype(
            str(self.font_path), self.font_size, layout_engine=ImageFont.Layout.RAQM
        )
        self.image = Image.new("RGB", (10, 10))
        self.draw = ImageDraw.Draw(self.image)

    def width(self, text: str) -> int:
        bbox = self.draw.textbbox(
            (0, 0), text, font=self.font, direction="rtl" if _has_arabic(text) else None
        )
        return round(bbox[2] - bbox[0])


def _has_arabic(text: str) -> bool:
    return any("\u0600" <= char <= "\u06ff" for char in text)


def wrap_text(text: str, measurer: TextMeasurer, max_width: int, max_lines: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if measurer.width(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    if len(lines) <= max_lines:
        return lines
    return lines
