from __future__ import annotations

from typing import Protocol

from quran_video.models import BackgroundAsset, Chapter, ChapterReciter, RenderRequest
from quran_video.models.domain import BadgeSettings
from quran_video.quran.repository import choose_default_moshaf, is_murattal_moshaf
from quran_video.rendering.media import list_backgrounds


class RenderDefaultsRepository(Protocol):
    async def chapters(self) -> list[Chapter]: ...

    async def reciters(self) -> list[ChapterReciter]: ...


async def resolve_render_request_defaults(
    request: RenderRequest,
    repository: RenderDefaultsRepository,
    *,
    backgrounds: list[BackgroundAsset] | None = None,
    require_background: bool = True,
) -> RenderRequest:
    chapters = await repository.chapters()
    chapter = next((item for item in chapters if item.id == request.chapter_id), None)
    if chapter is None:
        raise ValueError("chapter_id is not available")

    updates: dict[str, object] = {}
    if "ayah_to" not in request.model_fields_set:
        updates["ayah_to"] = chapter.verse_count

    reciters = await repository.reciters()
    reciter = _select_reciter(reciters, request.reciter_id, chapter.id)
    updates["reciter_id"] = reciter.id

    moshaf_id = _select_moshaf_id(reciter, request.moshaf_id, chapter.id)
    if moshaf_id is not None:
        updates["moshaf_id"] = moshaf_id

    if require_background and not request.background_ids:
        available_backgrounds = backgrounds if backgrounds is not None else list_backgrounds()
        if not available_backgrounds:
            raise ValueError("at least one background is required")
        updates["background_ids"] = [available_backgrounds[0].id]

    updates["badge"] = _default_badge(request.badge, chapter, reciter)
    payload = request.model_dump(mode="json")
    payload.update(updates)
    return RenderRequest.model_validate(payload)


def _select_reciter(
    reciters: list[ChapterReciter], reciter_id: str, chapter_id: int
) -> ChapterReciter:
    if reciter_id:
        reciter = next((item for item in reciters if item.id == reciter_id), None)
        if reciter is None:
            raise ValueError("reciter_id is not available")
        return reciter
    if not reciters:
        raise ValueError("no reciters are available")
    return sorted(reciters, key=lambda item: _reciter_default_key(item, chapter_id))[0]


def _reciter_default_key(reciter: ChapterReciter, chapter_id: int) -> tuple[int, int, str, str]:
    if not reciter.moshafs:
        return (0, 1, reciter.english_name.casefold(), reciter.id)
    moshaf = choose_default_moshaf(reciter.moshafs, chapter_id)
    has_surah = moshaf is not None and chapter_id in moshaf.available_surahs
    is_murattal = moshaf is not None and is_murattal_moshaf(moshaf)
    return (
        0 if has_surah else 1,
        0 if is_murattal else 1,
        reciter.english_name.casefold(),
        reciter.id,
    )


def _select_moshaf_id(
    reciter: ChapterReciter, requested_moshaf_id: str | None, chapter_id: int
) -> str | None:
    if not reciter.moshafs:
        return None
    if requested_moshaf_id:
        requested = next(
            (item for item in reciter.moshafs if item.id == str(requested_moshaf_id)), None
        )
        if requested and chapter_id in requested.available_surahs:
            return requested.id
    fallback = choose_default_moshaf(reciter.moshafs, chapter_id)
    if fallback is None:
        raise ValueError("selected reciter has no available moshafs")
    return fallback.id


def _default_badge(
    badge: BadgeSettings, chapter: Chapter, reciter: ChapterReciter
) -> BadgeSettings:
    return badge.model_copy(
        update={
            "arabic_surah": badge.arabic_surah or chapter.arabic_name,
            "english_surah": badge.english_surah or chapter.english_name,
            "arabic_reciter": badge.arabic_reciter or reciter.arabic_name,
            "english_reciter": badge.english_reciter or reciter.english_name,
        }
    )
