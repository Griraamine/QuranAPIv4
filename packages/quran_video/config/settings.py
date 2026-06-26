from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    qf_client_id: str | None = Field(default=None, alias="QF_CLIENT_ID")
    qf_client_secret: str | None = Field(default=None, alias="QF_CLIENT_SECRET")
    qf_env: str = Field(default="production", alias="QF_ENV")
    qf_auth_base: str | None = Field(default=None, alias="QF_AUTH_BASE")
    qf_api_base: str | None = Field(default=None, alias="QF_API_BASE")
    qf_connect_timeout: float = Field(default=10.0, alias="QF_CONNECT_TIMEOUT")
    qf_read_timeout: float = Field(default=30.0, alias="QF_READ_TIMEOUT")
    qf_retries: int = Field(default=3, alias="QF_RETRIES")
    qf_token_refresh_margin_seconds: int = Field(
        default=60, alias="QF_TOKEN_REFRESH_MARGIN_SECONDS"
    )
    qf_max_timing_overflow_ms: int = Field(default=2000, alias="QF_MAX_TIMING_OVERFLOW_MS")
    qf_min_timing_coverage: float = Field(default=0.90, alias="QF_MIN_TIMING_COVERAGE")
    quran_video_data_mode: str = Field(default="fixture", alias="QURAN_VIDEO_DATA_MODE")
    mp3quran_api_base: str = Field(
        default="https://www.mp3quran.net/api/v3", alias="MP3QURAN_API_BASE"
    )
    mp3quran_language: str = Field(default="eng", alias="MP3QURAN_LANGUAGE")
    mp3quran_connect_timeout: float = Field(default=10.0, alias="MP3QURAN_CONNECT_TIMEOUT")
    mp3quran_read_timeout: float = Field(default=30.0, alias="MP3QURAN_READ_TIMEOUT")
    mp3quran_retries: int = Field(default=3, alias="MP3QURAN_RETRIES")
    mp3quran_max_timing_overflow_ms: int = Field(
        default=2000, alias="MP3QURAN_MAX_TIMING_OVERFLOW_MS"
    )
    mp3quran_min_timing_coverage: float = Field(default=0.90, alias="MP3QURAN_MIN_TIMING_COVERAGE")
    mp3quran_max_timing_overlap_ms: int = Field(default=100, alias="MP3QURAN_MAX_TIMING_OVERLAP_MS")
    youtube_client_id: str | None = Field(default=None, alias="YOUTUBE_CLIENT_ID")
    youtube_client_secret: str | None = Field(default=None, alias="YOUTUBE_CLIENT_SECRET")
    youtube_refresh_token: str | None = Field(default=None, alias="YOUTUBE_REFRESH_TOKEN")
    youtube_channel_id: str | None = Field(default=None, alias="YOUTUBE_CHANNEL_ID")
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")
    api_origin: str = Field(default="http://127.0.0.1:8000", alias="API_ORIGIN")
    web_origin: str = Field(default="http://127.0.0.1:3000", alias="WEB_ORIGIN")
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", alias="REDIS_URL")
    sqlite_path: Path = Field(default=Path("data/cache/jobs.sqlite3"), alias="SQLITE_PATH")
    upload_size_limit: int = Field(default=512 * 1024 * 1024, alias="UPLOAD_SIZE_LIMIT")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def normalize_host_paths(self) -> Settings:
        docker_cache = Path("/app/data/cache")
        try:
            relative_sqlite = self.sqlite_path.relative_to(docker_cache)
        except ValueError:
            return self
        if not Path("/app").exists():
            self.sqlite_path = Path("data/cache") / relative_sqlite
        return self

    @property
    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    @property
    def backgrounds_dir(self) -> Path:
        return self.repo_root / "media" / "backgrounds"

    @property
    def renders_dir(self) -> Path:
        return self.repo_root / "renders"

    @property
    def cache_dir(self) -> Path:
        return self.repo_root / "data" / "cache"

    @property
    def mp3quran_audio_cache_dir(self) -> Path:
        return self.cache_dir / "mp3quran-audio"

    @property
    def quran_foundation_audio_cache_dir(self) -> Path:
        return self.cache_dir / "quran-foundation-audio"


@lru_cache
def get_settings() -> Settings:
    return Settings()
