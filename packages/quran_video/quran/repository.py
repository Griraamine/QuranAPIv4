from __future__ import annotations

from quran_video.api_clients.al_quran_cloud import AlQuranCloudClient
from quran_video.api_clients.mp3quran import (
    MP3QuranClient,
    MP3QuranError,
    build_audio_url,
    resolve_timing_mapping,
)
from quran_video.api_clients.quran_foundation import QuranFoundationClient, QuranFoundationError
from quran_video.config import Settings, get_settings
from quran_video.config.reciters import apply_reciter_name_overrides
from quran_video.models import (
    Chapter,
    ChapterAudio,
    ChapterReciter,
    CompatibilityResult,
    ReciterMoshaf,
    TimingStatus,
    Verse,
)
from quran_video.quran.compatibility import (
    validate_audio_compatibility,
    validate_audio_with_safe_fallback,
)
from quran_video.quran.fixtures import (
    fixture_audio,
    fixture_chapters,
    fixture_reciters,
    fixture_verses,
)


class QuranRepository:
    def __init__(
        self,
        settings: Settings | None = None,
        client: QuranFoundationClient | None = None,
        al_quran_cloud_client: AlQuranCloudClient | None = None,
        mp3quran_client: MP3QuranClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self.al_quran_cloud_client = al_quran_cloud_client
        self.mp3quran_client = mp3quran_client
        self.mode = self.settings.quran_video_data_mode

    def _client(self) -> QuranFoundationClient:
        if self.client is None:
            self.client = QuranFoundationClient.from_settings(self.settings)
        return self.client

    def _al_quran_cloud(self) -> AlQuranCloudClient:
        if self.al_quran_cloud_client is None:
            self.al_quran_cloud_client = AlQuranCloudClient()
        return self.al_quran_cloud_client

    def _mp3quran(self) -> MP3QuranClient:
        if self.mp3quran_client is None:
            self.mp3quran_client = MP3QuranClient(
                api_base=self.settings.mp3quran_api_base,
                language=self.settings.mp3quran_language,
                connect_timeout=self.settings.mp3quran_connect_timeout,
                read_timeout=self.settings.mp3quran_read_timeout,
                retries=self.settings.mp3quran_retries,
            )
        return self.mp3quran_client

    def _use_al_quran_cloud_for_text(self) -> bool:
        return self.mode in {"alqurancloud", "mp3quran"}

    def _use_quran_foundation(self) -> bool:
        return self.mode in {"quranfoundation", "live"}

    async def chapters(self) -> list[Chapter]:
        if self.mode == "fixture":
            return fixture_chapters()
        if self._use_al_quran_cloud_for_text():
            return await self._al_quran_cloud().get_chapters()
        if self._use_quran_foundation():
            return await self._client().get_chapters()
        return await self._client().get_chapters()

    async def reciters(self) -> list[ChapterReciter]:
        if self.mode == "fixture":
            return apply_reciter_name_overrides(fixture_reciters())
        if self.mode == "mp3quran":
            return apply_reciter_name_overrides(await self._mp3quran().get_reciters())
        if self.mode == "alqurancloud":
            return apply_reciter_name_overrides(await self._al_quran_cloud().get_chapter_reciters())
        if self._use_quran_foundation():
            return apply_reciter_name_overrides(await self._client().get_chapter_reciters())
        return apply_reciter_name_overrides(await self._client().get_chapter_reciters())

    async def verses(self, chapter_id: int) -> list[Verse]:
        if self.mode == "fixture":
            return fixture_verses(chapter_id)
        if self._use_al_quran_cloud_for_text():
            return await self._al_quran_cloud().get_verses_by_chapter(chapter_id)
        if self._use_quran_foundation():
            translation_id = await self._client().resolve_saheeh_international_id()
            return await self._client().get_verses_by_chapter(chapter_id, translation_id)
        translation_id = await self._client().resolve_saheeh_international_id()
        return await self._client().get_verses_by_chapter(chapter_id, translation_id)

    async def chapter_audio(
        self, chapter_id: int, reciter_id: str, moshaf_id: str | None = None
    ) -> ChapterAudio:
        if self.mode == "fixture":
            return fixture_audio(chapter_id, reciter_id)
        if self.mode == "mp3quran":
            return await self._mp3quran_chapter_audio(chapter_id, reciter_id, moshaf_id)
        if self.mode == "alqurancloud":
            return await self._al_quran_cloud().get_chapter_audio(chapter_id, reciter_id)
        if self._use_quran_foundation():
            audio = await self._client().get_chapter_audio(chapter_id, reciter_id)
            reciter = await self.reciter_for_request(reciter_id)
            return audio.model_copy(
                update={
                    "reciter_name": reciter.english_name,
                    "moshaf_name": reciter.style.name,
                }
            )
        return await self._client().get_chapter_audio(chapter_id, reciter_id)

    async def compatibility(
        self, chapter_id: int, reciter_id: str, moshaf_id: str | None = None
    ) -> CompatibilityResult:
        if self.mode == "fixture" and reciter_id == "fixture-incompatible":
            return CompatibilityResult(
                reciter_id=reciter_id,
                moshaf_id=moshaf_id,
                chapter_id=chapter_id,
                compatible=False,
                reason="fixture reciter intentionally lacks complete word timing",
            )
        verses = await self.verses(chapter_id)
        if self.mode == "mp3quran":
            try:
                audio = await self._mp3quran_chapter_audio(chapter_id, reciter_id, moshaf_id)
            except (MP3QuranError, ValueError) as error:
                return CompatibilityResult(
                    reciter_id=reciter_id,
                    moshaf_id=moshaf_id,
                    chapter_id=chapter_id,
                    compatible=False,
                    reason=str(error),
                    status=TimingStatus.timing_unavailable,
                )
            return validate_audio_compatibility(
                audio,
                verses,
                tolerance_ms=self.settings.mp3quran_max_timing_overflow_ms,
                minimum_timing_coverage=self.settings.mp3quran_min_timing_coverage,
            )
        if self._use_quran_foundation():
            try:
                audio = await self.chapter_audio(chapter_id, reciter_id, moshaf_id)
            except QuranFoundationError as error:
                return CompatibilityResult(
                    reciter_id=reciter_id,
                    moshaf_id=moshaf_id,
                    chapter_id=chapter_id,
                    compatible=False,
                    reason=str(error),
                    status=TimingStatus.timing_unavailable,
                )
            _audio, result = validate_audio_with_safe_fallback(
                audio,
                verses,
                tolerance_ms=self.settings.qf_max_timing_overflow_ms,
                minimum_timing_coverage=self.settings.qf_min_timing_coverage,
            )
            return result
        audio = await self.chapter_audio(chapter_id, reciter_id, moshaf_id)
        return validate_audio_compatibility(audio, verses)

    async def reciter_for_request(
        self, reciter_id: str, moshaf_id: str | None = None
    ) -> ChapterReciter:
        if self.mode != "mp3quran":
            reciters = await self.reciters()
            return next(item for item in reciters if item.id == reciter_id)
        reciter, moshaf = await self._selected_mp3quran_moshaf(reciter_id, moshaf_id)
        return reciter.model_copy(
            update={
                "style": reciter.style.model_copy(update={"name": moshaf.name}),
                "audio_source_name": "MP3Quran.net",
                "moshafs": [moshaf],
            }
        )

    async def _selected_mp3quran_moshaf(
        self, reciter_id: str, moshaf_id: str | None, chapter_id: int | None = None
    ) -> tuple[ChapterReciter, ReciterMoshaf]:
        reciters = apply_reciter_name_overrides(
            await self._mp3quran().get_reciters(reciter_id=reciter_id)
        )
        reciter = next((item for item in reciters if item.id == str(reciter_id)), None)
        if reciter is None:
            raise ValueError("reciter_id is not available from MP3Quran")
        if moshaf_id:
            moshaf = next((item for item in reciter.moshafs if item.id == str(moshaf_id)), None)
            if moshaf is None:
                raise ValueError("moshaf_id does not belong to the selected reciter")
            return reciter, moshaf
        moshaf = choose_default_moshaf(reciter.moshafs, chapter_id)
        if moshaf is None:
            raise ValueError("selected reciter has no available moshafs")
        return reciter, moshaf

    async def _mp3quran_chapter_audio(
        self, chapter_id: int, reciter_id: str, moshaf_id: str | None
    ) -> ChapterAudio:
        reciter, moshaf = await self._selected_mp3quran_moshaf(reciter_id, moshaf_id, chapter_id)
        audio_url = build_audio_url(moshaf.server, chapter_id)
        if chapter_id not in moshaf.available_surahs:
            return _mp3quran_audio_shell(
                chapter_id,
                reciter,
                moshaf,
                audio_url,
                TimingStatus.surah_unavailable,
                None,
                None,
            )
        verses = await self.verses(chapter_id)
        reads = await self._mp3quran().get_timing_reads()
        mapping = resolve_timing_mapping(moshaf=moshaf, reciter=reciter, reads=reads)
        if mapping.status != TimingStatus.timing_available or mapping.timing_read_id is None:
            return _mp3quran_audio_shell(
                chapter_id,
                reciter,
                moshaf,
                audio_url,
                mapping.status,
                mapping.timing_read_id,
                mapping.method,
            )
        timed_surahs = await self._mp3quran().get_timed_surahs(mapping.timing_read_id)
        if chapter_id not in timed_surahs:
            return _mp3quran_audio_shell(
                chapter_id,
                reciter,
                moshaf,
                audio_url,
                TimingStatus.timing_unavailable,
                mapping.timing_read_id,
                mapping.method,
            )
        timing = await self._mp3quran().get_ayah_timing(
            surah_number=chapter_id,
            timing_read_id=mapping.timing_read_id,
            expected_ayah_count=len(verses),
            maximum_overlap_ms=self.settings.mp3quran_max_timing_overlap_ms,
        )
        if timing.status != TimingStatus.timing_available:
            return _mp3quran_audio_shell(
                chapter_id,
                reciter,
                moshaf,
                audio_url,
                timing.status,
                mapping.timing_read_id,
                mapping.method,
            )
        duration_ms = max(
            [timestamp.end_ms for timestamp in timing.timings]
            + ([timing.intro_timing.end_ms] if timing.intro_timing else [1])
        )
        return ChapterAudio(
            reciter_id=reciter.id,
            moshaf_id=moshaf.id,
            chapter_id=chapter_id,
            url=audio_url,
            duration_ms=duration_ms,
            verse_timestamps=timing.timings,
            provider="mp3quran",
            reciter_name=reciter.english_name,
            moshaf_name=moshaf.name,
            rewaya=moshaf.rewaya,
            moshaf_type=moshaf.moshaf_type,
            server=moshaf.server,
            available_surahs=moshaf.available_surahs,
            timing_status=TimingStatus.timing_available,
            timing_read_id=mapping.timing_read_id,
            timing_mapping_method=mapping.method,
            intro_timing=timing.intro_timing,
            has_ayah_timing=True,
            has_word_timing=False,
        )


def _mp3quran_audio_shell(
    chapter_id: int,
    reciter: ChapterReciter,
    moshaf: ReciterMoshaf,
    audio_url: str,
    status: TimingStatus,
    timing_read_id: int | None,
    timing_mapping_method: str | None,
) -> ChapterAudio:
    return ChapterAudio(
        reciter_id=reciter.id,
        moshaf_id=moshaf.id,
        chapter_id=chapter_id,
        url=audio_url,
        duration_ms=1,
        verse_timestamps=[],
        provider="mp3quran",
        reciter_name=reciter.english_name,
        moshaf_name=moshaf.name,
        rewaya=moshaf.rewaya,
        moshaf_type=moshaf.moshaf_type,
        server=moshaf.server,
        available_surahs=moshaf.available_surahs,
        timing_status=status,
        timing_read_id=timing_read_id,
        timing_mapping_method=timing_mapping_method,
        has_ayah_timing=False,
        has_word_timing=False,
    )


def choose_default_moshaf(
    moshafs: list[ReciterMoshaf], chapter_id: int | None = None
) -> ReciterMoshaf | None:
    if not moshafs:
        return None
    available = [
        moshaf for moshaf in moshafs if chapter_id is None or chapter_id in moshaf.available_surahs
    ]
    candidates = available or moshafs
    murattal = [moshaf for moshaf in candidates if is_murattal_moshaf(moshaf)]
    return sorted(murattal or candidates, key=lambda item: (item.name.casefold(), item.id))[0]


def is_murattal_moshaf(moshaf: ReciterMoshaf) -> bool:
    label = " ".join(part for part in [moshaf.name, moshaf.rewaya] if part).casefold()
    return any(token in label for token in ("murattal", "murattel", "muratal", "مرتل", "مرتّل"))
