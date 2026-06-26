from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from quran_video.models import (
    ChapterReciter,
    IntroTiming,
    RecitationStyle,
    ReciterMoshaf,
    TimingStatus,
    VerseTimestamp,
    WordSegment,
)
from quran_video.quran.text import normalize_lookup_text

LOGGER = logging.getLogger(__name__)


class MP3QuranError(RuntimeError):
    pass


class MP3QuranConfigurationError(MP3QuranError):
    pass


class MP3QuranAPIError(MP3QuranError):
    pass


class MP3QuranValidationError(MP3QuranError):
    pass


@dataclass(frozen=True)
class MP3QuranTimingRead:
    id: int
    name: str
    rewaya: str | None
    folder_url: str
    soar_count: int


@dataclass(frozen=True)
class MP3QuranTimingMapping:
    status: TimingStatus
    timing_read_id: int | None = None
    method: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MP3QuranTimingPayload:
    status: TimingStatus
    timings: list[VerseTimestamp]
    intro_timing: IntroTiming | None
    reason: str | None = None


class MP3QuranClient:
    def __init__(
        self,
        *,
        api_base: str = "https://www.mp3quran.net/api/v3",
        language: str = "eng",
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        retries: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(api_base)
        if parsed.scheme != "https" or not parsed.netloc:
            raise MP3QuranConfigurationError("MP3Quran API base must be an HTTPS URL")
        self.api_base = api_base.rstrip("/")
        self.language = language
        self.retries = max(1, retries)
        self.headers = {"User-Agent": "quran-video-platform/0.1 (+https://mp3quran.net)"}
        self.http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            headers=self.headers,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _request(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/"):
            raise MP3QuranConfigurationError("MP3Quran request paths must start with /")
        url = f"{self.api_base}{path}"
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = await self.http.get(url, params=params, headers=self.headers)
                if (response.status_code in {429} or 500 <= response.status_code < 600) and (
                    attempt < self.retries
                ):
                    await asyncio.sleep(_retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                return _json_payload(response)
            except (httpx.TimeoutException, httpx.TransportError) as error:
                last_error = error
                if attempt == self.retries:
                    raise MP3QuranAPIError(f"MP3Quran request failed: {error}") from error
                await asyncio.sleep(min(0.75 * attempt, 4.0))
            except httpx.HTTPStatusError as error:
                last_error = error
                raise MP3QuranAPIError(
                    f"MP3Quran request failed with HTTP {error.response.status_code}"
                ) from error
        raise MP3QuranAPIError("MP3Quran request failed") from last_error

    async def get_reciters(
        self,
        *,
        reciter_id: int | str | None = None,
        rewaya_id: int | str | None = None,
        surah_number: int | None = None,
    ) -> list[ChapterReciter]:
        params: dict[str, Any] = {"language": self.language}
        if reciter_id is not None:
            params["reciter"] = str(reciter_id)
        if rewaya_id is not None:
            params["rewaya"] = str(rewaya_id)
        if surah_number is not None:
            params["sura"] = str(surah_number)
        payload = await self._request("/reciters", params=params)
        reciters = _expect_dict_list(payload, "reciters")
        return [_parse_reciter(item) for item in reciters]

    async def get_timing_reads(self) -> list[MP3QuranTimingRead]:
        payload = await self._request("/ayat_timing/reads")
        if not isinstance(payload, list):
            raise MP3QuranValidationError("MP3Quran timing reads response must be a list")
        return [_parse_timing_read(item) for item in payload]

    async def get_timed_surahs(self, timing_read_id: int) -> list[int]:
        payload = await self._request("/ayat_timing/soar", params={"read": timing_read_id})
        if not isinstance(payload, list):
            raise MP3QuranValidationError("MP3Quran timed-surah response must be a list")
        return sorted(
            {
                int(item["id"])
                for item in payload
                if isinstance(item, dict) and _is_valid_surah_number(item.get("id"))
            }
        )

    async def get_ayah_timing(
        self,
        *,
        surah_number: int,
        timing_read_id: int,
        expected_ayah_count: int,
        maximum_overlap_ms: int = 100,
    ) -> MP3QuranTimingPayload:
        payload = await self._request(
            "/ayat_timing", params={"surah": surah_number, "read": timing_read_id}
        )
        if not isinstance(payload, list):
            raise MP3QuranValidationError("MP3Quran ayah timing response must be a list")
        return parse_ayah_timing_payload(
            payload,
            surah_number=surah_number,
            expected_ayah_count=expected_ayah_count,
            maximum_overlap_ms=maximum_overlap_ms,
        )


def resolve_timing_mapping(
    *,
    moshaf: ReciterMoshaf,
    reciter: ChapterReciter,
    reads: list[MP3QuranTimingRead],
) -> MP3QuranTimingMapping:
    server_key = normalize_mp3quran_url(moshaf.server)
    exact = [read for read in reads if normalize_mp3quran_url(read.folder_url) == server_key]
    if len(exact) == 1:
        return MP3QuranTimingMapping(
            status=TimingStatus.timing_available,
            timing_read_id=exact[0].id,
            method="server_folder_url",
        )
    if len(exact) > 1:
        return MP3QuranTimingMapping(
            status=TimingStatus.timing_ambiguous,
            method="server_folder_url",
            reason="multiple timing reads match the selected moshaf server",
        )

    moshaf_name = normalize_lookup_text(" ".join([reciter.english_name, moshaf.name]))
    moshaf_rewaya = normalize_lookup_text(
        " ".join(part for part in [reciter.english_name, moshaf.rewaya or moshaf.name] if part)
    )
    candidates = [
        read
        for read in reads
        if normalize_lookup_text(" ".join(part for part in [read.name, read.rewaya] if part))
        in {moshaf_name, moshaf_rewaya}
    ]
    if len(candidates) == 1:
        return MP3QuranTimingMapping(
            status=TimingStatus.timing_available,
            timing_read_id=candidates[0].id,
            method="reciter_rewaya_name",
        )
    if len(candidates) > 1:
        return MP3QuranTimingMapping(
            status=TimingStatus.timing_ambiguous,
            method="reciter_rewaya_name",
            reason="multiple timing reads match the selected moshaf name",
        )
    return MP3QuranTimingMapping(
        status=TimingStatus.timing_unavailable,
        reason="no MP3Quran timing read matches the selected moshaf",
    )


def parse_ayah_timing_payload(
    payload: list[Any],
    *,
    surah_number: int,
    expected_ayah_count: int,
    maximum_overlap_ms: int = 100,
) -> MP3QuranTimingPayload:
    intro_timing: IntroTiming | None = None
    timings: list[VerseTimestamp] = []
    seen: set[int] = set()
    previous_end = 0
    for raw in payload:
        if not isinstance(raw, dict):
            return MP3QuranTimingPayload(
                status=TimingStatus.timing_invalid,
                timings=[],
                intro_timing=None,
                reason="timing entries must be objects",
            )
        try:
            ayah = int(raw["ayah"])
            start_ms = int(raw["start_time"])
            end_ms = int(raw["end_time"])
        except (KeyError, TypeError, ValueError):
            return MP3QuranTimingPayload(
                status=TimingStatus.timing_invalid,
                timings=[],
                intro_timing=None,
                reason="timing entries must contain integer ayah/start/end values",
            )
        if start_ms < 0 or end_ms <= start_ms:
            return MP3QuranTimingPayload(
                status=TimingStatus.timing_invalid,
                timings=[],
                intro_timing=None,
                reason="timing entry has invalid bounds",
            )
        if ayah == 0:
            intro_timing = IntroTiming(start_ms=start_ms, end_ms=end_ms)
            previous_end = max(previous_end, end_ms)
            continue
        if ayah < 1 or ayah > expected_ayah_count:
            return MP3QuranTimingPayload(
                status=TimingStatus.timing_invalid,
                timings=[],
                intro_timing=intro_timing,
                reason=f"timing ayah {ayah} is outside surah {surah_number}",
            )
        if ayah in seen:
            return MP3QuranTimingPayload(
                status=TimingStatus.timing_invalid,
                timings=[],
                intro_timing=intro_timing,
                reason=f"timing ayah {ayah} is duplicated",
            )
        if timings and start_ms < previous_end - maximum_overlap_ms:
            return MP3QuranTimingPayload(
                status=TimingStatus.timing_invalid,
                timings=[],
                intro_timing=intro_timing,
                reason="timing entries overlap excessively",
            )
        seen.add(ayah)
        timings.append(
            VerseTimestamp(
                verse_number=ayah,
                start_ms=start_ms,
                end_ms=end_ms,
                word_segments=[
                    WordSegment(
                        verse_number=ayah,
                        word_position=1,
                        start_ms=start_ms,
                        end_ms=end_ms,
                    )
                ],
            )
        )
        previous_end = max(previous_end, end_ms)
    expected = set(range(1, expected_ayah_count + 1))
    if seen != expected:
        return MP3QuranTimingPayload(
            status=TimingStatus.text_timing_mismatch,
            timings=[],
            intro_timing=intro_timing,
            reason="timing ayah numbers do not match the expected surah range",
        )
    timings.sort(key=lambda item: item.verse_number)
    return MP3QuranTimingPayload(
        status=TimingStatus.timing_available,
        timings=timings,
        intro_timing=intro_timing,
    )


def parse_surah_list(value: Any) -> list[int]:
    if not isinstance(value, str):
        return []
    result: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            number = int(token)
        except ValueError:
            continue
        if 1 <= number <= 114:
            result.add(number)
    return sorted(result)


def build_audio_url(server: str, surah_number: int) -> str:
    if not 1 <= surah_number <= 114:
        raise MP3QuranValidationError("surah_number must be between 1 and 114")
    parsed = urlsplit(server)
    if parsed.scheme != "https" or not parsed.netloc:
        raise MP3QuranValidationError("MP3Quran server URL must be HTTPS")
    host = parsed.hostname or ""
    if not (host == "mp3quran.net" or host.endswith(".mp3quran.net")):
        raise MP3QuranValidationError(f"unexpected MP3Quran audio host: {host}")
    path = parsed.path.rstrip("/")
    filename = f"{surah_number:03d}.mp3"
    return urlunsplit(("https", parsed.netloc, f"{path}/{filename}", "", ""))


def normalize_mp3quran_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        return value.strip()
    host = (parsed.hostname or "").casefold()
    netloc = host
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    path = re.sub(r"/+", "/", parsed.path or "/")
    if not path.endswith("/"):
        path += "/"
    return urlunsplit(("https", netloc, path, "", ""))


def _parse_reciter(item: dict[str, Any]) -> ChapterReciter:
    reciter_id = str(item["id"])
    name = str(item.get("name") or reciter_id)
    moshafs: list[ReciterMoshaf] = []
    for raw_moshaf in item.get("moshaf") or []:
        if not isinstance(raw_moshaf, dict):
            continue
        available = parse_surah_list(raw_moshaf.get("surah_list"))
        surah_total = int(raw_moshaf.get("surah_total") or len(available))
        if len(available) != surah_total:
            LOGGER.warning(
                "MP3Quran moshaf %s for reciter %s declares surah_total=%s but surah_list has %s valid entries",
                raw_moshaf.get("id"),
                reciter_id,
                surah_total,
                len(available),
            )
        moshafs.append(
            ReciterMoshaf(
                id=str(raw_moshaf["id"]),
                name=str(raw_moshaf.get("name") or raw_moshaf["id"]),
                rewaya_id=_optional_int(raw_moshaf.get("rewaya_id")),
                rewaya=str(raw_moshaf.get("name") or "") or None,
                moshaf_type=_optional_int(raw_moshaf.get("moshaf_type")),
                server=str(raw_moshaf.get("server") or ""),
                surah_total=surah_total,
                available_surahs=available,
            )
        )
    return ChapterReciter(
        id=reciter_id,
        english_name=name,
        arabic_name=name,
        style=RecitationStyle(id="mp3quran", name="MP3Quran"),
        audio_source_name="MP3Quran.net",
        provider="mp3quran",
        moshafs=moshafs,
    )


def _parse_timing_read(item: Any) -> MP3QuranTimingRead:
    if not isinstance(item, dict):
        raise MP3QuranValidationError("timing read entries must be objects")
    return MP3QuranTimingRead(
        id=int(item["id"]),
        name=str(item.get("name") or item["id"]),
        rewaya=str(item.get("rewaya") or "") or None,
        folder_url=str(item.get("folder_url") or ""),
        soar_count=int(item.get("soar_count") or 0),
    )


def _expect_dict_list(payload: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise MP3QuranValidationError("MP3Quran response must be a JSON object")
    value = payload.get(key)
    if not isinstance(value, list):
        raise MP3QuranValidationError(f"MP3Quran response is missing list field {key}")
    return [item for item in value if isinstance(item, dict)]


def _json_payload(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError as error:
        raise MP3QuranValidationError("MP3Quran response is not valid JSON") from error
    if isinstance(payload, dict) and payload.get("Errors"):
        raise MP3QuranAPIError(f"MP3Quran API error: {payload['Errors']}")
    return payload


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


def _is_valid_surah_number(value: Any) -> bool:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return False
    return 1 <= number <= 114


def _optional_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
