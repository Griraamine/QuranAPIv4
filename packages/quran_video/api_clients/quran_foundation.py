from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx

from quran_video.config import Settings
from quran_video.models import (
    Chapter,
    ChapterAudio,
    ChapterReciter,
    QuranWord,
    RecitationStyle,
    TimingStatus,
    Verse,
    VerseTimestamp,
    WordSegment,
)
from quran_video.quran.text import clean_translation_text, normalize_lookup_text


class QuranFoundationError(RuntimeError):
    pass


class QuranFoundationConfigurationError(QuranFoundationError):
    pass


class QuranFoundationAPIError(QuranFoundationError):
    pass


class QuranFoundationValidationError(QuranFoundationError):
    pass


@dataclass(frozen=True)
class CachedToken:
    access_token: str
    expires_at: float


class QuranFoundationClient:
    AUTH_BASE: ClassVar[dict[str, str]] = {
        "production": "https://oauth2.quran.foundation",
        "prelive": "https://prelive-oauth2.quran.foundation",
    }
    API_BASE: ClassVar[dict[str, str]] = {
        "production": "https://apis.quran.foundation",
        "prelive": "https://apis-prelive.quran.foundation",
    }
    PUBLIC_QURAN_API_BASE: ClassVar[str] = "https://api.quran.com/api/v4"
    TIMING_DISAGREEMENT_THRESHOLD_MS: ClassVar[int] = 1500

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        env: str = "production",
        *,
        auth_base: str | None = None,
        api_base: str | None = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        retries: int = 3,
        token_refresh_margin_seconds: int = 60,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if env not in self.AUTH_BASE:
            raise QuranFoundationConfigurationError("QF_ENV must be production or prelive")
        if not client_id or not client_secret:
            raise QuranFoundationConfigurationError(
                "QF_CLIENT_ID and QF_CLIENT_SECRET are required for Quran.Foundation"
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.env = env
        self.auth_base = _require_https(auth_base or self.AUTH_BASE[env], "Quran.Foundation auth")
        self.api_base = _require_https(api_base or self.API_BASE[env], "Quran.Foundation API")
        self.retries = max(1, retries)
        self.token_refresh_margin_seconds = max(0, token_refresh_margin_seconds)
        self.headers = {"User-Agent": "quran-video-platform/0.1 (+https://quran.foundation)"}
        self._token: CachedToken | None = None
        self._saheeh_translation_id: int | None = None
        self.http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            headers=self.headers,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> QuranFoundationClient:
        return cls(
            settings.qf_client_id or "",
            settings.qf_client_secret or "",
            settings.qf_env,
            auth_base=settings.qf_auth_base,
            api_base=settings.qf_api_base,
            connect_timeout=settings.qf_connect_timeout,
            read_timeout=settings.qf_read_timeout,
            retries=settings.qf_retries,
            token_refresh_margin_seconds=settings.qf_token_refresh_margin_seconds,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _fetch_token(self) -> CachedToken:
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        payload = await self._request_with_retries(
            "POST",
            f"{self.auth_base}/oauth2/token",
            headers={
                **self.headers,
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"grant_type": "client_credentials", "scope": "content"},
            request_name="OAuth token",
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
            raise QuranFoundationValidationError("Quran.Foundation token response is invalid")
        try:
            expires_in = int(payload.get("expires_in", 3600))
        except (TypeError, ValueError) as error:
            raise QuranFoundationValidationError(
                "Quran.Foundation token response has invalid expiry"
            ) from error
        return CachedToken(
            access_token=payload["access_token"],
            expires_at=time.time() + max(expires_in, 1),
        )

    async def token(self) -> str:
        if (
            self._token is None
            or time.time() >= self._token.expires_at - self.token_refresh_margin_seconds
        ):
            self._token = await self._fetch_token()
        return self._token.access_token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        retry_401: bool = True,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise QuranFoundationConfigurationError("Quran.Foundation paths must start with /")
        access_token = await self.token()
        payload = await self._request_with_retries(
            method,
            f"{self.api_base}{path}",
            params=params,
            headers={
                **self.headers,
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "x-auth-token": access_token,
                "x-client-id": self.client_id,
            },
            request_name=path,
            retry_401=retry_401,
        )
        if not isinstance(payload, dict):
            raise QuranFoundationValidationError(
                f"Quran.Foundation response for {path} must be a JSON object"
            )
        return payload

    async def _request_with_retries(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        request_name: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        retry_401: bool = False,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = await self.http.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=headers,
                )
                if response.status_code == 401 and retry_401:
                    self._token = None
                    return await self._request(
                        method,
                        _path_from_url(url, self.api_base),
                        params=params,
                        retry_401=False,
                    )
                if _temporary_status(response.status_code) and attempt < self.retries:
                    await asyncio.sleep(_retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                return _json_payload(response, request_name)
            except httpx.HTTPStatusError as error:
                last_error = error
                raise QuranFoundationAPIError(
                    f"Quran.Foundation {request_name} failed with HTTP {error.response.status_code}"
                ) from error
            except (httpx.TimeoutException, httpx.TransportError) as error:
                last_error = error
                if attempt == self.retries:
                    raise QuranFoundationAPIError(
                        f"Quran.Foundation {request_name} request failed"
                    ) from error
                await asyncio.sleep(min(0.75 * attempt, 4.0))
        raise QuranFoundationAPIError(
            f"Quran.Foundation {request_name} request failed"
        ) from last_error

    async def get_chapters(self) -> list[Chapter]:
        payload = await self._request("GET", "/content/api/v4/chapters", params={"language": "en"})
        chapters = _payload_list(payload, "chapters", "data")
        result = [
            Chapter(
                id=_int(item.get("id") or item.get("chapter_number"), "chapter id"),
                arabic_name=str(
                    item.get("name_arabic") or item.get("name_ar") or item.get("arabic_name") or ""
                ),
                english_name=str(
                    item.get("name_simple")
                    or item.get("name_complex")
                    or item.get("english_name")
                    or ""
                ),
                translated_name=_translated_name(item),
                verse_count=_int(
                    item.get("verses_count") or item.get("verse_count"), "chapter verse count"
                ),
                revelation_place=item.get("revelation_place"),
            )
            for item in chapters
            if isinstance(item, dict)
        ]
        return sorted(result, key=lambda item: item.id)

    async def resolve_saheeh_international_id(self) -> int:
        if self._saheeh_translation_id is not None:
            return self._saheeh_translation_id
        payload = await self._request(
            "GET", "/content/api/v4/resources/translations", params={"language": "en"}
        )
        resources = _payload_list(payload, "translations", "resources", "data")
        matches: list[int] = []
        for item in resources:
            if not isinstance(item, dict):
                continue
            lookup = normalize_lookup_text(
                " ".join(
                    str(part)
                    for part in [
                        item.get("name"),
                        item.get("author_name"),
                        item.get("slug"),
                        item.get("language_name"),
                    ]
                    if part
                )
            )
            if "saheeh international" in lookup or "sahih international" in lookup:
                matches.append(_int(item.get("id"), "translation id"))
        unique_matches = sorted(set(matches))
        if len(unique_matches) != 1:
            raise QuranFoundationConfigurationError(
                "Could not resolve exactly one Saheeh International translation resource"
            )
        self._saheeh_translation_id = unique_matches[0]
        return self._saheeh_translation_id

    async def get_verses_by_chapter(self, chapter_id: int, translation_id: int) -> list[Verse]:
        page = 1
        verses: list[Verse] = []
        while True:
            payload = await self._request(
                "GET",
                f"/content/api/v4/verses/by_chapter/{chapter_id}",
                params={
                    "language": "en",
                    "words": "true",
                    "fields": "text_uthmani",
                    "word_fields": "position,text_uthmani,translation,char_type_name",
                    "translations": str(translation_id),
                    "page": page,
                    "per_page": 50,
                },
            )
            raw_verses = _payload_list(payload, "verses", "data")
            for raw in raw_verses:
                if isinstance(raw, dict):
                    verses.append(_parse_verse(raw, chapter_id))
            total_pages = _total_pages(payload, page)
            if page >= total_pages:
                break
            page += 1
        return sorted(verses, key=lambda item: item.verse_number)

    async def get_chapter_reciters(self) -> list[ChapterReciter]:
        english = await self._request(
            "GET", "/content/api/v4/resources/chapter_reciters", params={"language": "en"}
        )
        arabic = await self._request(
            "GET", "/content/api/v4/resources/chapter_reciters", params={"language": "ar"}
        )
        english_items = _payload_list(english, "reciters", "chapter_reciters", "data")
        arabic_items = _payload_list(arabic, "reciters", "chapter_reciters", "data")
        arabic_by_id = {
            str(item["id"]): item
            for item in arabic_items
            if isinstance(item, dict) and item.get("id") is not None
        }
        merged: list[ChapterReciter] = []
        for item in english_items:
            if not isinstance(item, dict):
                continue
            reciter_id = str(item["id"])
            ar_item = arabic_by_id.get(reciter_id, {})
            style_name = str(item.get("style") or item.get("recitation_style") or "Chapter")
            style_id = normalize_lookup_text(style_name).replace(" ", "-") or "chapter"
            merged.append(
                ChapterReciter(
                    id=reciter_id,
                    english_name=str(
                        item.get("reciter_name")
                        or item.get("name")
                        or item.get("translated_name")
                        or reciter_id
                    ),
                    arabic_name=str(
                        ar_item.get("reciter_name")
                        or ar_item.get("name")
                        or item.get("name")
                        or reciter_id
                    ),
                    style=RecitationStyle(id=style_id, name=style_name),
                    audio_source_name="Quran.Foundation Content API",
                    provider="quranfoundation",
                )
            )
        return merged

    async def get_chapter_audio(self, chapter_id: int, reciter_id: str) -> ChapterAudio:
        payload = await self._request(
            "GET",
            f"/content/api/v4/chapter_recitations/{reciter_id}/{chapter_id}",
            params={"segments": "true"},
        )
        audio = _audio_payload(payload)
        url = str(audio.get("audio_url") or audio.get("url") or audio.get("download_url") or "")
        if urlsplit(url).scheme != "https":
            raise QuranFoundationValidationError("Quran.Foundation audio URL must use HTTPS")
        timestamps = _parse_verse_timings(audio)
        if not timestamps:
            raise QuranFoundationValidationError(
                "Quran.Foundation audio response has no ayah timestamps"
            )
        timing_mapping_method = "quran_foundation_chapter_recitation_segments"
        public_timestamps = await self._public_quran_audio_timings(chapter_id, reciter_id, url)
        if public_timestamps and _timings_disagree(
            timestamps,
            public_timestamps,
            self.TIMING_DISAGREEMENT_THRESHOLD_MS,
        ):
            timestamps = public_timestamps
            timing_mapping_method = "qurancom_public_chapter_recitation_ayah_timing"
        has_word_timing = all(timestamp.word_segments for timestamp in timestamps)
        if not has_word_timing:
            timestamps = [
                timestamp.model_copy(
                    update={
                        "word_segments": [
                            WordSegment(
                                verse_number=timestamp.verse_number,
                                word_position=1,
                                start_ms=timestamp.start_ms,
                                end_ms=timestamp.end_ms,
                            )
                        ]
                    }
                )
                for timestamp in timestamps
            ]
        duration_ms = max(
            _audio_duration_ms(audio), max(timestamp.end_ms for timestamp in timestamps)
        )
        return ChapterAudio(
            reciter_id=reciter_id,
            chapter_id=chapter_id,
            url=url,
            duration_ms=duration_ms,
            verse_timestamps=timestamps,
            provider="quranfoundation",
            timing_status=TimingStatus.timing_available,
            timing_mapping_method=(
                timing_mapping_method
                if has_word_timing
                else (
                    timing_mapping_method
                    if timing_mapping_method.endswith("_ayah_timing")
                    else "quran_foundation_chapter_recitation_ayah_timing"
                )
            ),
            has_ayah_timing=True,
            has_word_timing=has_word_timing,
        )

    async def _public_quran_audio_timings(
        self, chapter_id: int, reciter_id: str, protected_audio_url: str
    ) -> list[VerseTimestamp]:
        if not _is_quranic_audio_url(protected_audio_url):
            return []
        try:
            payload = await self._request_with_retries(
                "GET",
                f"{self.PUBLIC_QURAN_API_BASE}/chapter_recitations/{reciter_id}/{chapter_id}",
                params={"segments": "true"},
                headers={**self.headers, "Accept": "application/json"},
                request_name="public Quran.com chapter recitation",
            )
        except QuranFoundationError:
            return []
        if not isinstance(payload, dict):
            return []
        try:
            audio = _audio_payload(payload)
            public_audio_url = str(
                audio.get("audio_url") or audio.get("url") or audio.get("download_url") or ""
            )
            if _canonical_audio_url(public_audio_url) != _canonical_audio_url(protected_audio_url):
                return []
            return _parse_verse_timings(audio)
        except QuranFoundationError:
            return []


def _require_https(value: str, label: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise QuranFoundationConfigurationError(f"{label} base URL must use HTTPS")
    return value.rstrip("/")


def _temporary_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        if retry_after.isdigit():
            return min(float(retry_after), 10.0)
        try:
            delta = parsedate_to_datetime(retry_after).timestamp() - time.time()
            return min(max(delta, 0.0), 10.0)
        except (TypeError, ValueError, OverflowError):
            pass
    return min(0.75 * attempt, 4.0)


def _json_payload(response: httpx.Response, request_name: str) -> Any:
    try:
        payload = response.json()
    except ValueError as error:
        raise QuranFoundationValidationError(
            f"Quran.Foundation {request_name} response is not valid JSON"
        ) from error
    if isinstance(payload, dict) and (
        payload.get("errors") or payload.get("Errors") or payload.get("error")
    ):
        raise QuranFoundationAPIError(f"Quran.Foundation {request_name} returned an API error")
    return payload


def _path_from_url(url: str, api_base: str) -> str:
    if not url.startswith(api_base):
        raise QuranFoundationConfigurationError("Cannot retry non-API Quran.Foundation URL")
    return url[len(api_base) :]


def _payload_list(payload: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _translated_name(item: dict[str, Any]) -> str | None:
    translated = item.get("translated_name")
    value = translated.get("name") if isinstance(translated, dict) else translated
    return str(value) if value else None


def _total_pages(payload: dict[str, Any], current_page: int) -> int:
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        return current_page
    return _int(pagination.get("total_pages") or current_page, "pagination total_pages")


def _parse_verse(raw: dict[str, Any], chapter_id: int) -> Verse:
    translations = raw.get("translations") or []
    translation_text = translations[0].get("text", "") if translations else ""
    words: list[QuranWord] = []
    for raw_word in raw.get("words", []):
        if not isinstance(raw_word, dict):
            continue
        char_type = str(raw_word.get("char_type_name") or "word").casefold()
        if char_type != "word":
            continue
        word_translation = raw_word.get("translation")
        if isinstance(word_translation, dict):
            word_translation_text = word_translation.get("text", "")
        else:
            word_translation_text = str(word_translation or "")
        words.append(
            QuranWord(
                position=_int(raw_word["position"], "word position"),
                text_uthmani=str(raw_word.get("text_uthmani", "")),
                translation=clean_translation_text(word_translation_text),
            )
        )
    verse_number = _verse_number(raw)
    text_uthmani = str(raw.get("text_uthmani") or " ".join(word.text_uthmani for word in words))
    return Verse(
        chapter_id=chapter_id,
        verse_number=verse_number,
        text_uthmani=text_uthmani,
        translation=clean_translation_text(translation_text),
        words=words,
    )


def _audio_payload(payload: dict[str, Any]) -> dict[str, Any]:
    audio = payload.get("audio_file", payload.get("chapter_audio", payload.get("data")))
    if isinstance(audio, list):
        audio = audio[0] if audio else None
    if not isinstance(audio, dict):
        audio_files = payload.get("audio_files")
        if isinstance(audio_files, list) and audio_files:
            audio = audio_files[0]
    if not isinstance(audio, dict):
        audio = payload
    if not isinstance(audio, dict):
        raise QuranFoundationValidationError("Quran.Foundation audio response is invalid")
    return audio


def _parse_verse_timings(audio: dict[str, Any]) -> list[VerseTimestamp]:
    raw_timings = (
        audio.get("verse_timings")
        or audio.get("verse_timestamps")
        or audio.get("ayah_timings")
        or audio.get("ayah_timestamps")
        or audio.get("timings")
        or audio.get("timestamps")
        or []
    )
    if isinstance(raw_timings, str):
        try:
            raw_timings = json.loads(raw_timings)
        except ValueError:
            raw_timings = []
    timestamps: list[VerseTimestamp] = []
    for raw in raw_timings:
        if not isinstance(raw, dict):
            continue
        verse_number = _verse_number(raw)
        start_ms = _time_ms(
            raw.get(
                "timestamp_from",
                raw.get("start_ms", raw.get("start_time", raw.get("start", raw.get("from", 0)))),
            ),
            "timestamp_from",
        )
        end_ms = _time_ms(
            raw.get(
                "timestamp_to",
                raw.get("end_ms", raw.get("end_time", raw.get("end", raw.get("to", 0)))),
            ),
            "timestamp_to",
        )
        if end_ms <= start_ms:
            duration = _time_ms(raw.get("duration", 0), "duration")
            end_ms = start_ms + max(duration, 1)
        segments = _parse_segments(
            raw.get("segments") or raw.get("word_segments") or raw.get("words") or [],
            verse_number,
            end_ms,
        )
        timestamps.append(
            VerseTimestamp(
                verse_number=verse_number,
                start_ms=start_ms,
                end_ms=end_ms,
                word_segments=[
                    segment
                    for segment in segments
                    if segment.start_ms >= start_ms and segment.end_ms <= end_ms
                ],
            )
        )
    return sorted(timestamps, key=lambda item: item.verse_number)


def _parse_segments(
    raw_segments: list[Any],
    verse_number: int,
    verse_end_ms: int | None = None,
) -> list[WordSegment]:
    if isinstance(raw_segments, str):
        try:
            raw_segments = json.loads(raw_segments)
        except ValueError:
            raw_segments = []
    parsed: list[tuple[int, int, int | None]] = []
    for raw in raw_segments:
        try:
            if isinstance(raw, list | tuple):
                if len(raw) >= 4 and _int(raw[0], "segment verse number") == verse_number:
                    word_position = _int(raw[1], "segment word position")
                    start_ms = _time_ms(raw[2], "segment start")
                    end_ms = _time_ms(raw[3], "segment end")
                elif len(raw) >= 3:
                    word_position = _int(raw[0], "segment word position")
                    start_ms = _time_ms(raw[1], "segment start")
                    end_ms = _time_ms(raw[2], "segment end")
                elif len(raw) == 2:
                    word_position = _int(raw[0], "segment word position")
                    start_ms = _time_ms(raw[1], "segment start")
                    end_ms = None
                else:
                    continue
            elif isinstance(raw, dict):
                word_position = _int(
                    raw.get("word_position", raw.get("position", raw.get("word_number"))),
                    "segment word position",
                )
                start_ms = _time_ms(
                    raw.get(
                        "start_ms",
                        raw.get(
                            "timestamp_from",
                            raw.get("start_time", raw.get("start", raw.get("from"))),
                        ),
                    ),
                    "segment start",
                )
                raw_end = raw.get(
                    "end_ms",
                    raw.get("timestamp_to", raw.get("end_time", raw.get("end", raw.get("to")))),
                )
                end_ms = _time_ms(raw_end, "segment end") if raw_end is not None else None
            else:
                continue
            parsed.append((word_position, start_ms, end_ms))
        except (TypeError, ValueError):
            continue
    parsed.sort(key=lambda item: (item[1], item[0]))
    segments: list[WordSegment] = []
    for index, (word_position, start_ms, explicit_end_ms) in enumerate(parsed):
        next_start_ms = parsed[index + 1][1] if index + 1 < len(parsed) else None
        end_ms = explicit_end_ms or next_start_ms or verse_end_ms
        if end_ms is None or end_ms <= start_ms:
            continue
        try:
            segments.append(
                WordSegment(
                    verse_number=verse_number,
                    word_position=word_position,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
            )
        except (TypeError, ValueError):
            continue
    return segments


def _audio_duration_ms(audio: dict[str, Any]) -> int:
    if audio.get("duration_ms") is not None:
        return _time_ms(audio["duration_ms"], "duration_ms")
    raw = audio.get("duration", 0)
    try:
        numeric = float(raw)
    except (TypeError, ValueError):
        return _time_ms(raw, "duration")
    return round(numeric * 1000) if numeric < 10_000 else round(numeric)


def _is_quranic_audio_url(value: str) -> bool:
    host = urlsplit(value).hostname or ""
    return host.casefold() == "download.quranicaudio.com"


def _canonical_audio_url(value: str) -> str:
    parsed = urlsplit(value)
    path = re.sub(r"/+", "/", parsed.path)
    netloc = parsed.netloc.casefold()
    return urlunsplit((parsed.scheme.casefold(), netloc, path, "", ""))


def _timings_disagree(
    protected: list[VerseTimestamp], public: list[VerseTimestamp], threshold_ms: int
) -> bool:
    protected_by_verse = {timestamp.verse_number: timestamp for timestamp in protected}
    public_by_verse = {timestamp.verse_number: timestamp for timestamp in public}
    if protected_by_verse.keys() != public_by_verse.keys():
        return False
    for verse_number, protected_timestamp in protected_by_verse.items():
        public_timestamp = public_by_verse[verse_number]
        if (
            abs(protected_timestamp.start_ms - public_timestamp.start_ms) > threshold_ms
            or abs(protected_timestamp.end_ms - public_timestamp.end_ms) > threshold_ms
        ):
            return True
    return False


def _verse_number(raw: dict[str, Any]) -> int:
    value = raw.get("verse_number", raw.get("ayah_number", raw.get("verse_id")))
    if value is not None:
        return _int(value, "verse number")
    verse_key = str(raw.get("verse_key") or raw.get("ayah_key") or raw.get("key") or "")
    if ":" not in verse_key:
        raise QuranFoundationValidationError("verse_key is missing from Quran.Foundation data")
    return _int(verse_key.split(":")[-1], "verse number")


def _int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise QuranFoundationValidationError(f"Invalid Quran.Foundation {label}") from error


def _time_ms(value: Any, label: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise QuranFoundationValidationError(f"Invalid Quran.Foundation {label}")
        if stripped.replace(".", "", 1).isdigit():
            numeric = float(stripped)
            return round(numeric)
        match = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+)(?:[.,](\d+))?", stripped)
        if match:
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2))
            seconds = int(match.group(3))
            fraction = (match.group(4) or "0")[:3].ljust(3, "0")
            return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(fraction)
    raise QuranFoundationValidationError(f"Invalid Quran.Foundation {label}")
