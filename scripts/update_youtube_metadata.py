from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from typing import Any

from quran_video.config import Settings
from quran_video.metadata import generate_metadata
from quran_video.models import Chapter, ChapterReciter
from quran_video.quran.artistic_names import ARTISTIC_SURAH_NAMES
from quran_video.quran.repository import QuranRepository
from quran_video.quran.text import normalize_lookup_text, remove_arabic_diacritics
from quran_video.youtube import YouTubeClient


@dataclass(frozen=True)
class VideoMatch:
    video_id: str
    old_title: str
    chapter: Chapter
    reciter: ChapterReciter
    ayah_from: int
    ayah_to: int


def _norm(value: str) -> str:
    return normalize_lookup_text(remove_arabic_diacritics(value))


def _line_value(text: str, label: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(label)}\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _ayah_range(text: str, fallback_to: int) -> tuple[int, int]:
    patterns = [
        r"الآيات\s+(\d+)\s*[–-]\s*(\d+)",
        r"Ayahs?\s*:?\s*(\d+)\s*[–-]\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))
    return 1, fallback_to


def _chapter_keys(chapter: Chapter) -> set[str]:
    return {
        _norm(chapter.english_name),
        _norm(chapter.arabic_name),
        _norm(f"Surah {chapter.english_name}"),
        _norm(f"سورة {chapter.arabic_name}"),
        _norm(ARTISTIC_SURAH_NAMES.get(chapter.id, "")),
    } - {""}


def _reciter_keys(reciter: ChapterReciter) -> set[str]:
    english = reciter.english_name
    variants = {
        english,
        english.replace("-", " "),
        english.replace("Al-", "Al "),
        english.replace("Al-", ""),
        english.replace("'", ""),
        english.replace("’", ""),
        reciter.arabic_name,
    }
    if "minshawi" in _norm(english):
        variants.update({"Minshawi", "Al Minshawi", "المنشاوي"})
    return {_norm(value) for value in variants if value}


def _find_chapter(text: str, chapters: list[Chapter]) -> Chapter | None:
    values = [
        _line_value(text, "السورة | Surah"),
        _line_value(text, "Surah"),
    ]
    searchable = _norm("\n".join(value for value in values if value) or text)
    matches = [
        chapter
        for chapter in chapters
        if any(
            key and re.search(rf"(^|\s){re.escape(key)}(\s|$)", searchable)
            for key in _chapter_keys(chapter)
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _find_reciter(text: str, reciters: list[ChapterReciter]) -> ChapterReciter | None:
    values = [
        _line_value(text, "القارئ | Reciter"),
        _line_value(text, "Reciter"),
    ]
    searchable = _norm("\n".join(value for value in values if value) or text)
    matches = [
        reciter
        for reciter in reciters
        if any(
            key and re.search(rf"(^|\s){re.escape(key)}(\s|$)", searchable)
            for key in _reciter_keys(reciter)
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _project_video(snippet: dict[str, Any]) -> bool:
    text = f"{snippet.get('title', '')}\n{snippet.get('description', '')}"
    normalized = _norm(text)
    markers = [
        "quran foundation content api",
        "saheeh international",
        "arabic text",
        "english translation",
        "quran recitation",
        "سورة",
    ]
    return "quran" in normalized and any(_norm(marker) in normalized for marker in markers)


def _list_uploads(youtube: YouTubeClient) -> list[dict[str, Any]]:
    service = youtube.service
    channel = service.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    ids: list[str] = []
    page_token: str | None = None
    while True:
        response = (
            service.playlistItems()
            .list(
                part="contentDetails",
                playlistId=uploads,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        ids.extend(item["contentDetails"]["videoId"] for item in response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    videos: list[dict[str, Any]] = []
    for start in range(0, len(ids), 50):
        response = (
            service.videos()
            .list(part="snippet,status", id=",".join(ids[start : start + 50]), maxResults=50)
            .execute()
        )
        videos.extend(response.get("items", []))
    return videos


def _match_video(
    video: dict[str, Any], chapters: list[Chapter], reciters: list[ChapterReciter]
) -> VideoMatch | None:
    snippet = video["snippet"]
    if not _project_video(snippet):
        return None
    text = f"{snippet.get('title', '')}\n{snippet.get('description', '')}"
    chapter = _find_chapter(text, chapters)
    reciter = _find_reciter(text, reciters)
    if chapter is None or reciter is None:
        return None
    ayah_from, ayah_to = _ayah_range(text, chapter.verse_count)
    return VideoMatch(
        video_id=video["id"],
        old_title=snippet.get("title", ""),
        chapter=chapter,
        reciter=reciter,
        ayah_from=max(1, ayah_from),
        ayah_to=min(chapter.verse_count, ayah_to),
    )


def _update_video(youtube: YouTubeClient, video: dict[str, Any], match: VideoMatch) -> None:
    metadata = generate_metadata(match.chapter, match.reciter, match.ayah_from, match.ayah_to)
    snippet = video["snippet"]
    snippet_body: dict[str, Any] = {
        "title": metadata.title,
        "description": metadata.description,
        "tags": metadata.tags,
        "categoryId": snippet.get("categoryId", "27"),
    }
    body = {
        "id": match.video_id,
        "snippet": snippet_body,
    }
    if snippet.get("defaultLanguage"):
        snippet_body["defaultLanguage"] = snippet["defaultLanguage"]
    if snippet.get("defaultAudioLanguage"):
        snippet_body["defaultAudioLanguage"] = snippet["defaultAudioLanguage"]
    youtube.service.videos().update(part="snippet", body=body).execute()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply updates. Default is dry-run.")
    args = parser.parse_args()

    settings = Settings()
    youtube = YouTubeClient(
        settings.youtube_client_id or "",
        settings.youtube_client_secret or "",
        settings.youtube_refresh_token or "",
        settings.youtube_channel_id or "",
    )
    youtube.verify_channel()

    repository = QuranRepository(settings)
    chapters = await repository.chapters()
    reciters = await repository.reciters()
    if repository.client:
        await repository.client.close()

    videos = _list_uploads(youtube)
    matches: list[tuple[dict[str, Any], VideoMatch]] = []
    skipped: list[str] = []
    for video in videos:
        match = _match_video(video, chapters, reciters)
        if match is None:
            skipped.append(f"{video['id']} | {video['snippet'].get('title', '')}")
            continue
        matches.append((video, match))

    print(f"uploaded_videos={len(videos)} matched={len(matches)} skipped={len(skipped)}")
    for _, match in matches:
        metadata = generate_metadata(match.chapter, match.reciter, match.ayah_from, match.ayah_to)
        print(f"PLAN {match.video_id}")
        print(f"  old: {match.old_title}")
        print(f"  new: {metadata.title}")
        print(
            f"  parsed: surah={match.chapter.id} {match.chapter.english_name}, "
            f"reciter={match.reciter.english_name}, ayahs={match.ayah_from}-{match.ayah_to}"
        )
        if args.apply:
            _update_video(youtube, _, match)
            print(f"  updated: https://www.youtube.com/watch?v={match.video_id}")

    if skipped:
        print("SKIPPED")
        for item in skipped:
            print(f"  {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
