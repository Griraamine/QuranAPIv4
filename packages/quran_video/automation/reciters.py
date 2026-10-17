from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from quran_video.automation.state import shuffled_surah_queue
from quran_video.models import AutomationState, ChapterReciter, CompatibilityResult
from quran_video.quran.text import normalize_lookup_text

LOGGER = logging.getLogger(__name__)
RANKING_PATH = Path(__file__).resolve().parents[3] / "config" / "automation_reciters.json"


@dataclass(frozen=True)
class RankedReciter:
    key: str
    name: str
    variants: tuple[ChapterReciter, ...]


def ranked_reciters(reciters: list[ChapterReciter]) -> list[RankedReciter]:
    ranking = json.loads(RANKING_PATH.read_text(encoding="utf-8"))
    # IDs are specific to Quran.Foundation; do not reuse them for other providers.
    available = {r.id: r for r in reciters if r.provider in {"quranfoundation", "legacy"}}
    ranked: list[RankedReciter] = []
    used: set[str] = set()
    for row in ranking["reciters"]:
        variants = tuple(available[i] for i in row["provider_ids"] if i in available)
        if variants:
            ranked.append(RankedReciter(row["key"], row["name"], variants))
            used.update(r.display_key for r in variants)
    # No invented popularity scores for people absent from the published ranking.
    for reciter in sorted(reciters, key=lambda r: (normalize_lookup_text(r.english_name), r.id)):
        if reciter.display_key not in used:
            ranked.append(RankedReciter(reciter.display_key, reciter.english_name, (reciter,)))
    return ranked


def completion_key(reciter_key: str, surah_id: int) -> str:
    return f"{reciter_key}|{surah_id}"


def legacy_reciter_key(reciter_key: str) -> str:
    """Collapse old display-key/style variants into the ranked performer identity."""
    ranking = json.loads(RANKING_PATH.read_text(encoding="utf-8"))
    provider_id = reciter_key.split(":", 1)[0]
    for row in ranking["reciters"]:
        if provider_id in row["provider_ids"]:
            return str(row["key"])
    return reciter_key


def initialize_queue(state: AutomationState, ranked: list[RankedReciter]) -> None:
    if state.schema_version >= 2:
        return
    # A legacy shuffled queue spanned multiple random performers. It cannot be
    # reused as one performer's remaining work. Keep the independent background queue.
    last = state.last_success or {}
    if last.get("reciter") and last.get("surah"):
        key = completion_key(legacy_reciter_key(str(last["reciter"])), int(last["surah"]))
        state.completed_reciter_surahs = sorted({*state.completed_reciter_surahs, key})
    state.reciter_queue = [r.key for r in ranked]
    state.active_reciter_key = None
    state.surah_queue = []
    state.schema_version = 2


async def select_next_recitation(
    compatibility: Callable[[int, str, str | None], Awaitable[CompatibilityResult]],
    reciters: list[ChapterReciter],
    state: AutomationState,
    configured_query: str | None = None,
) -> tuple[int, ChapterReciter, str | None] | None:
    ranked = ranked_reciters(reciters)
    if not ranked:
        raise RuntimeError("reciter catalog is empty; refusing to advance the queue")
    if configured_query and configured_query.strip():
        # Preserve explicit reciter overrides, but never accept a random policy.
        query = normalize_lookup_text(configured_query)
        if query in {"random", "any", "*"}:
            raise ValueError("random reciters are disabled; use the ranked queue")
        matches = [
            r
            for r in ranked
            if query == normalize_lookup_text(r.key)
            or any(
                query == v.id or query in normalize_lookup_text(v.english_name) for v in r.variants
            )
        ]
        if len(matches) != 1:
            raise ValueError(
                f"reciter override must identify exactly one performer: {configured_query}"
            )
        # Overrides cannot discard or reorder persisted work.
        if (
            state.schema_version >= 2
            and state.reciter_queue
            and state.reciter_queue[0] != matches[0].key
        ):
            raise ValueError("reciter override conflicts with the active ranked queue")
        ranked = matches
    initialize_queue(state, ranked)
    by_key = {r.key: r for r in ranked}
    while state.reciter_queue:
        key = state.reciter_queue[0]
        if key not in by_key:
            raise RuntimeError(f"queued reciter {key} missing from catalog; refusing to skip")
        performer = by_key[key]
        if state.active_reciter_key != key:
            state.active_reciter_key = key
            state.surah_queue = shuffled_surah_queue()
            state.cycle_number += 1
        # An empty active queue means this performer is finished, never restart it.
        while state.surah_queue:
            surah_id = state.surah_queue[0]
            pair = completion_key(key, surah_id)
            if pair in state.completed_reciter_surahs or pair in state.skipped_reciter_surahs:
                state.surah_queue.pop(0)
                continue
            reasons: list[str] = []
            for reciter in performer.variants:
                moshafs: list[str | None] = [m.id for m in reciter.moshafs] or [None]
                for moshaf_id in moshafs:
                    result = await compatibility(surah_id, reciter.id, moshaf_id)
                    if result.compatible:
                        LOGGER.info(
                            "ranked reciter selected | performer=%s reciter_id=%s surah=%s",
                            performer.name,
                            reciter.id,
                            surah_id,
                        )
                        return surah_id, reciter, moshaf_id
                    reason = result.reason or str(result.status or "incompatible")
                    # Repository converts provider exceptions into incompatible results.
                    # Outages/auth errors must retry the same work, not consume a surah.
                    if reason.startswith("Quran.Foundation") and "HTTP 404" not in reason:
                        raise RuntimeError(f"cannot check {key} surah {surah_id}: {reason}")
                    reasons.append(reason)
            state.skipped_reciter_surahs[pair] = "; ".join(reasons)
            LOGGER.warning(
                "recitation unavailable; recorded skip | performer=%s surah=%s reasons=%s",
                key,
                surah_id,
                reasons,
            )
            state.surah_queue.pop(0)
        state.reciter_queue.pop(0)
        state.active_reciter_key = None
    return None
