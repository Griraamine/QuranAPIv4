from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BackgroundMode(StrEnum):
    single = "single"
    slideshow = "slideshow"


class RenderStatus(StrEnum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"
    canceled = "canceled"


class TimingStatus(StrEnum):
    timing_available = "timing_available"
    timing_unavailable = "timing_unavailable"
    timing_ambiguous = "timing_ambiguous"
    timing_invalid = "timing_invalid"
    surah_unavailable = "surah_unavailable"
    text_timing_mismatch = "text_timing_mismatch"
    audio_timing_mismatch = "audio_timing_mismatch"


class RenderPhase(StrEnum):
    validating = "validating"
    fetching_quran_data = "fetching Quran data"
    downloading_audio = "downloading audio"
    preparing_background = "preparing background"
    generating_subtitles = "generating subtitles"
    encoding = "encoding"
    validating_output = "validating output"
    generating_thumbnail = "generating thumbnail"
    complete = "complete"


class Chapter(BaseModel):
    id: int = Field(ge=1, le=114)
    arabic_name: str
    english_name: str
    translated_name: str | None = None
    verse_count: int = Field(gt=0)
    revelation_place: str | None = None


class QuranWord(BaseModel):
    position: int = Field(ge=1)
    text_uthmani: str
    translation: str


class Verse(BaseModel):
    chapter_id: int = Field(ge=1, le=114)
    verse_number: int = Field(ge=1)
    text_uthmani: str
    translation: str
    words: list[QuranWord]


class RecitationStyle(BaseModel):
    id: str
    name: str


class ReciterMoshaf(BaseModel):
    id: str
    name: str
    rewaya_id: int | None = None
    rewaya: str | None = None
    moshaf_type: int | None = None
    server: str
    surah_total: int = Field(ge=0, le=114)
    available_surahs: list[int]
    timing_status: TimingStatus | None = None
    timing_read_id: int | None = None
    timing_mapping_method: str | None = None

    @field_validator("available_surahs")
    @classmethod
    def valid_surahs(cls, value: list[int]) -> list[int]:
        cleaned = sorted({int(item) for item in value if 1 <= int(item) <= 114})
        return cleaned

    @property
    def label(self) -> str:
        return self.name


class ChapterReciter(BaseModel):
    id: str
    english_name: str
    arabic_name: str
    style: RecitationStyle
    audio_source_name: str = "Quran Foundation / Quran.com"
    provider: str = "legacy"
    moshafs: list[ReciterMoshaf] = Field(default_factory=list)

    @property
    def display_key(self) -> str:
        return f"{self.id}:{self.style.id}"


class WordSegment(BaseModel):
    verse_number: int = Field(ge=1)
    word_position: int = Field(ge=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> WordSegment:
        if self.end_ms <= self.start_ms:
            raise ValueError("word segment end_ms must be greater than start_ms")
        return self


class VerseTimestamp(BaseModel):
    verse_number: int = Field(ge=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    word_segments: list[WordSegment]

    @model_validator(mode="after")
    def validate_bounds(self) -> VerseTimestamp:
        if self.end_ms <= self.start_ms:
            raise ValueError("verse timestamp end_ms must be greater than start_ms")
        for segment in self.word_segments:
            if segment.start_ms < self.start_ms or segment.end_ms > self.end_ms:
                raise ValueError("word segment is outside the verse timestamp")
        return self


class IntroTiming(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> IntroTiming:
        if self.end_ms <= self.start_ms:
            raise ValueError("intro timing end_ms must be greater than start_ms")
        return self


class ChapterAudio(BaseModel):
    reciter_id: str
    chapter_id: int = Field(ge=1, le=114)
    url: str
    audio_urls: list[str] = Field(default_factory=list)
    duration_ms: int = Field(gt=0)
    verse_timestamps: list[VerseTimestamp]
    provider: str = "legacy"
    moshaf_id: str | None = None
    reciter_name: str | None = None
    moshaf_name: str | None = None
    rewaya: str | None = None
    moshaf_type: int | None = None
    server: str | None = None
    available_surahs: list[int] = Field(default_factory=list)
    timing_status: TimingStatus | None = None
    timing_read_id: int | None = None
    timing_mapping_method: str | None = None
    intro_timing: IntroTiming | None = None
    has_ayah_timing: bool = False
    has_word_timing: bool = True

    @field_validator("url")
    @classmethod
    def require_https(cls, value: str) -> str:
        allowed = ("https://", "fixture://", "alqurancloud://")
        if not value.startswith(allowed):
            raise ValueError("audio URL must use HTTPS or a supported internal scheme")
        return value


class CompatibilityResult(BaseModel):
    reciter_id: str
    chapter_id: int = Field(ge=1, le=114)
    compatible: bool
    reason: str | None = None
    has_word_timing: bool = False
    has_ayah_timing: bool = False
    moshaf_id: str | None = None
    status: TimingStatus | None = None
    timing_read_id: int | None = None
    timing_mapping_method: str | None = None


class BackgroundAsset(BaseModel):
    id: str
    filename: str
    media_type: Literal["image", "video"]
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    sha256: str | None = None


class TypographySettings(BaseModel):
    arabic_font_size: int = Field(default=68, ge=36, le=96)
    gloss_font_size: int = Field(default=38, ge=24, le=60)
    translation_font_size: int = Field(default=35, ge=22, le=58)
    text_shade: str = "#FFFFFF"
    secondary_shade: str = "#FFFFFF"
    outline_px: int = Field(default=0, ge=0, le=8)
    shadow_px: int = Field(default=0, ge=0, le=12)
    line_spacing: float = Field(default=1.2, ge=1.0, le=1.8)
    position: Literal["top", "center", "bottom"] = "center"
    arabic_y: int = Field(default=500, ge=220, le=780)
    gloss_y: int = Field(default=650, ge=280, le=880)
    translation_y: int = Field(default=760, ge=340, le=980)
    arabic_box_x: int = Field(default=960, ge=0, le=1920)
    arabic_box_y: int = Field(default=500, ge=0, le=1080)
    arabic_box_width: int = Field(default=1620, ge=120, le=1920)
    arabic_box_height: int = Field(default=170, ge=40, le=520)
    translation_box_x: int = Field(default=960, ge=0, le=1920)
    translation_box_y: int = Field(default=760, ge=0, le=1080)
    translation_box_width: int = Field(default=1620, ge=120, le=1920)
    translation_box_height: int = Field(default=130, ge=40, le=520)
    text_transition: Literal["none", "fade"] = "none"
    fade_duration_ms: int = Field(default=350, ge=0, le=3000)
    arabic_font_key: Literal[
        "uthmanic",
        "amiri",
        "noto_naskh",
        "scheherazade",
        "scheherazade_b",
        "lateef",
        "indo_pak",
        "al_mushaf",
        "poetry",
        "hafs_ex1",
        "muhammadi",
        "me_quran",
        "nabi",
        "aref_ruqaa",
        "mirza",
        "reem_kufi",
        "harmattan",
        "system",
    ] = "amiri"
    english_font_key: Literal["system", "georgia", "palatino", "times", "avenir", "didot"] = "times"

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_y_positions(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        if "arabic_box_y" not in migrated and "arabic_y" in migrated:
            migrated["arabic_box_y"] = migrated["arabic_y"]
        if "translation_box_y" not in migrated and "translation_y" in migrated:
            migrated["translation_box_y"] = migrated["translation_y"]
        return migrated

    @model_validator(mode="after")
    def sync_legacy_y_positions(self) -> TypographySettings:
        self.arabic_y = self.arabic_box_y
        self.translation_y = self.translation_box_y
        self.gloss_y = self.translation_box_y
        return self

    @field_validator("text_shade", "secondary_shade")
    @classmethod
    def grayscale_only(cls, value: str) -> str:
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError("shade must be a #RRGGBB value")
        r = int(value[1:3], 16)
        g = int(value[3:5], 16)
        b = int(value[5:7], 16)
        if len({r, g, b}) != 1:
            raise ValueError("shade must be grayscale")
        return value.upper()


def _hex_color(value: str) -> str:
    if len(value) != 7 or not value.startswith("#"):
        raise ValueError("color must be a #RRGGBB value")
    int(value[1:], 16)
    return value.upper()


class BadgeStyleSettings(BaseModel):
    x: int = Field(default=126, ge=0, le=1600)
    y: int = Field(default=88, ge=0, le=360)
    artistic_surah_size: int = Field(default=58, ge=28, le=110)
    english_size: int = Field(default=40, ge=20, le=78)
    range_size: int = Field(default=34, ge=18, le=64)
    line_gap: int = Field(default=10, ge=0, le=60)
    shade: str = "#FFFFFF"
    secondary_shade: str = "#FFFFFF"
    show_reciter: bool = False

    @field_validator("shade", "secondary_shade")
    @classmethod
    def valid_color(cls, value: str) -> str:
        return _hex_color(value)


class ThumbnailStyleSettings(BaseModel):
    artistic_surah_size: int = Field(default=105, ge=12, le=220)
    artistic_y: int = Field(default=285, ge=0, le=720)
    artistic_shade: str = "#FFFFFF"
    show_english: bool = True
    english_size: int = Field(default=62, ge=12, le=140)
    english_y: int = Field(default=402, ge=0, le=720)
    english_shade: str = "#FFFFFF"
    shadow_px: int = Field(default=0, ge=0, le=14)

    @field_validator("artistic_shade", "english_shade")
    @classmethod
    def valid_color(cls, value: str) -> str:
        return _hex_color(value)


class BackgroundStyleSettings(BaseModel):
    dim_opacity: int = Field(default=35, ge=0, le=90)


class VisualStyleSettings(BaseModel):
    background_style: BackgroundStyleSettings = Field(default_factory=BackgroundStyleSettings)
    typography: TypographySettings = Field(default_factory=TypographySettings)
    badge_style: BadgeStyleSettings = Field(default_factory=BadgeStyleSettings)
    thumbnail_style: ThumbnailStyleSettings = Field(default_factory=ThumbnailStyleSettings)


class BadgeSettings(BaseModel):
    enabled: bool = True
    arabic_surah: str = ""
    english_surah: str = ""
    arabic_reciter: str = ""
    english_reciter: str = ""

    @field_validator("arabic_surah", "english_surah", "arabic_reciter", "english_reciter")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        stripped = value.strip()
        if any(ord(ch) < 32 for ch in stripped):
            raise ValueError("badge labels cannot contain control characters")
        return stripped


class RenderRequest(BaseModel):
    reciter_id: str = ""
    moshaf_id: str | None = None
    chapter_id: int = Field(default=1, ge=1, le=114)
    ayah_from: int = Field(default=1, ge=1)
    ayah_to: int = Field(default=1, ge=1)
    include_bismillah: bool = True
    background_mode: BackgroundMode = BackgroundMode.single
    background_ids: list[str] = Field(default_factory=list)
    background_style: BackgroundStyleSettings = Field(default_factory=BackgroundStyleSettings)
    typography: TypographySettings = Field(default_factory=TypographySettings)
    badge: BadgeSettings = Field(default_factory=BadgeSettings)
    badge_style: BadgeStyleSettings = Field(default_factory=BadgeStyleSettings)
    thumbnail_style: ThumbnailStyleSettings = Field(default_factory=ThumbnailStyleSettings)
    preset_name: str = "classic_centered_bilingual"
    data_mode: Literal["quranfoundation", "live", "fixture", "alqurancloud", "mp3quran"] | None = (
        None
    )

    @model_validator(mode="after")
    def validate_range_and_backgrounds(self) -> RenderRequest:
        if self.ayah_to < self.ayah_from:
            raise ValueError("ayah_to must be greater than or equal to ayah_from")
        if self.background_mode == BackgroundMode.single and len(self.background_ids) > 1:
            self.background_ids = self.background_ids[:1]
        if self.background_mode == BackgroundMode.slideshow and len(self.background_ids) == 1:
            self.background_mode = BackgroundMode.single
        if self.background_mode == BackgroundMode.slideshow and len(self.background_ids) == 0:
            self.background_mode = BackgroundMode.single
        return self


class RenderTimeline(BaseModel):
    chapter: Chapter
    reciter: ChapterReciter
    verses: list[Verse]
    timestamps: list[VerseTimestamp]
    duration_ms: int
    include_bismillah: bool = False
    bismillah_duration_ms: int = 0


class RenderResult(BaseModel):
    video_path: Path
    thumbnail_path: Path
    duration_seconds: float
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RenderJobRecord(BaseModel):
    job_id: str
    created_at: datetime
    updated_at: datetime
    status: RenderStatus
    phase: str
    progress: float = Field(ge=0, le=100)
    eta_seconds: float | None = None
    error_summary: str | None = None
    video_path: str | None = None
    thumbnail_path: str | None = None
    expires_at: datetime | None = None


class YouTubeMetadata(BaseModel):
    title: str
    description: str
    tags: list[str]
    hashtags: list[str]
    thumbnail_text_arabic: str
    thumbnail_text_english: str
    playlist_title: str
    playlist_description: str


class PendingPostUpload(BaseModel):
    video_id: str
    thumbnail_path: str
    reciter_key: str
    metadata: dict[str, Any]


class AutomationState(BaseModel):
    schema_version: int = 1
    surah_queue: list[int] = Field(default_factory=list)
    cycle_number: int = 0
    playlist_ids: dict[str, str] = Field(default_factory=dict)
    pending_post_upload: PendingPostUpload | None = None
    last_success: dict[str, Any] | None = None
    recent_failures: list[dict[str, Any]] = Field(default_factory=list)
