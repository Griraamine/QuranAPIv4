from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from quran_video.models import YouTubeMetadata
from quran_video.quran.text import normalize_lookup_text

LOGGER = logging.getLogger(__name__)
YOUTUBE_SCOPE = ["https://www.googleapis.com/auth/youtube"]
YOUTUBE_REFRESH_TOKEN_HELP = (
    "YouTube OAuth refresh token was rejected. In Google Auth Platform, change the app's "
    "Audience publishing status from Testing to In production, then generate a new token with "
    "scripts/generate_youtube_refresh_token.py and replace YOUTUBE_REFRESH_TOKEN."
)


def normalize_playlist_title(value: str) -> str:
    return normalize_lookup_text(value)


def classify_google_error(error: Exception) -> str:
    if isinstance(error, HttpError):
        status = getattr(error.resp, "status", None)
        text = str(error).casefold()
        if status in {429, 500, 502, 503, 504}:
            return "retryable"
        if status in {401, 403} and ("quota" in text or "rate" in text):
            return "quota"
        if status in {401, 403}:
            return "authentication"
        if status == 400:
            return "invalid_metadata"
    return "retryable"


def refresh_youtube_credentials(credentials: Credentials) -> None:
    try:
        credentials.refresh(Request())
    except RefreshError as error:
        raise RuntimeError(YOUTUBE_REFRESH_TOKEN_HELP) from error


class YouTubeClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        channel_id: str,
    ) -> None:
        self.channel_id = channel_id
        credentials = Credentials(
            None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=YOUTUBE_SCOPE,
        )
        refresh_youtube_credentials(credentials)
        self.service = build("youtube", "v3", credentials=credentials, cache_discovery=False)

    def verify_channel(self) -> None:
        response = self.service.channels().list(part="id", mine=True).execute()
        items = response.get("items", [])
        if not items or items[0]["id"] != self.channel_id:
            raise RuntimeError("authenticated YouTube channel does not match YOUTUBE_CHANNEL_ID")

    def upload_video(self, video_path: Path, metadata: YouTubeMetadata) -> str:
        body = {
            "snippet": {
                "title": metadata.title,
                "description": metadata.description,
                "tags": metadata.tags,
                "categoryId": "27",
                "defaultLanguage": "ar",
                "defaultAudioLanguage": "ar",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
                "embeddable": True,
                "license": "youtube",
            },
        }
        media = MediaFileUpload(
            str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
        )
        request = self.service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
            notifySubscribers=True,
        )
        response = None
        attempt = 0
        while response is None:
            try:
                _, response = request.next_chunk()
            except Exception as error:
                if classify_google_error(error) != "retryable" or attempt >= 4:
                    raise
                time.sleep(2**attempt)
                attempt += 1
        return response["id"]

    def set_thumbnail(self, video_id: str, thumbnail_path: Path) -> None:
        media = MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg")
        self.service.thumbnails().set(videoId=video_id, media_body=media).execute()

    def find_or_create_playlist(
        self, metadata: YouTubeMetadata, cached_id: str | None = None
    ) -> str:
        if cached_id and self._playlist_exists(cached_id):
            return cached_id
        wanted = normalize_playlist_title(metadata.playlist_title)
        page_token: str | None = None
        while True:
            response = (
                self.service.playlists()
                .list(
                    part="id,snippet",
                    mine=True,
                    maxResults=50,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in response.get("items", []):
                if normalize_playlist_title(item["snippet"]["title"]) == wanted:
                    return item["id"]
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        created = (
            self.service.playlists()
            .insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": metadata.playlist_title,
                        "description": metadata.playlist_description,
                    },
                    "status": {"privacyStatus": "public"},
                },
            )
            .execute()
        )
        return created["id"]

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        try:
            self.service.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
        except HttpError as error:
            if "duplicate" not in str(error).casefold() and "already" not in str(error).casefold():
                raise

    def verify_video(self, video_id: str) -> dict[str, Any]:
        response = self.service.videos().list(part="status", id=video_id).execute()
        items = response.get("items", [])
        if not items:
            raise RuntimeError("uploaded video could not be verified")
        return items[0]["status"]

    def _playlist_exists(self, playlist_id: str) -> bool:
        response = self.service.playlists().list(part="id", id=playlist_id).execute()
        return bool(response.get("items"))
