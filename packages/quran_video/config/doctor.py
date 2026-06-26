from __future__ import annotations

import base64
import json
import shutil
import subprocess
from typing import Any

import httpx
from PIL import features

from quran_video.config import get_settings
from quran_video.rendering.fonts import resolve_fonts


def _command_ok(command: list[str]) -> tuple[bool, str]:
    executable = shutil.which(command[0])
    if executable is None:
        return False, f"{command[0]} not found"
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode == 0, (result.stdout or result.stderr).strip()


def _http_json_ok(url: str, params: dict[str, str] | None = None) -> tuple[bool, str]:
    try:
        response = httpx.get(
            url,
            params=params,
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"User-Agent": "quran-video-platform-doctor/0.1"},
        )
        response.raise_for_status()
        response.json()
        return True, "ok"
    except Exception as error:
        return False, str(error)[:300]


def _quran_foundation_metadata_check(settings, *, ci: bool) -> dict[str, Any]:
    if not settings.qf_client_id or not settings.qf_client_secret:
        return {
            "ok": True,
            "details": "skipped; QF_CLIENT_ID/QF_CLIENT_SECRET are not configured",
            "skipped": True,
        }
    try:
        auth_base = (
            settings.qf_auth_base
            or {
                "production": "https://oauth2.quran.foundation",
                "prelive": "https://prelive-oauth2.quran.foundation",
            }[settings.qf_env]
        )
        api_base = (
            settings.qf_api_base
            or {
                "production": "https://apis.quran.foundation",
                "prelive": "https://apis-prelive.quran.foundation",
            }[settings.qf_env]
        )
        basic = base64.b64encode(
            f"{settings.qf_client_id}:{settings.qf_client_secret}".encode()
        ).decode()
        with httpx.Client(
            timeout=httpx.Timeout(settings.qf_read_timeout, connect=settings.qf_connect_timeout),
            headers={"User-Agent": "quran-video-platform-doctor/0.1"},
        ) as sync_client:
            token_response = sync_client.post(
                f"{auth_base.rstrip('/')}/oauth2/token",
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"grant_type": "client_credentials", "scope": "content"},
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            access_token = token_payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                return {"ok": False, "details": "token response did not include access_token"}
            response = sync_client.get(
                f"{api_base.rstrip('/')}/content/api/v4/chapters",
                params={"language": "en"},
                headers={
                    "Accept": "application/json",
                    "x-auth-token": access_token,
                    "x-client-id": settings.qf_client_id,
                },
            )
            reciters_response = sync_client.get(
                f"{api_base.rstrip('/')}/content/api/v4/resources/chapter_reciters",
                params={"language": "en"},
                headers={
                    "Accept": "application/json",
                    "x-auth-token": access_token,
                    "x-client-id": settings.qf_client_id,
                },
            )
            translations_response = sync_client.get(
                f"{api_base.rstrip('/')}/content/api/v4/resources/translations",
                params={"language": "en"},
                headers={
                    "Accept": "application/json",
                    "x-auth-token": access_token,
                    "x-client-id": settings.qf_client_id,
                },
            )
        response.raise_for_status()
        reciters_response.raise_for_status()
        translations_response.raise_for_status()
        payload = response.json()
        reciters_payload = reciters_response.json()
        translations_payload = translations_response.json()
        chapters = payload.get("chapters") if isinstance(payload, dict) else None
        reciters = _first_list(reciters_payload, "reciters", "chapter_reciters", "data")
        translations = _first_list(translations_payload, "translations", "resources", "data")
        has_saheeh = any(
            isinstance(item, dict)
            and "saheeh international"
            in " ".join(str(item.get(key) or "") for key in ["name", "author_name", "slug"])
            .casefold()
            .replace("-", " ")
            for item in translations
        )
        ok = isinstance(chapters, list) and len(chapters) >= 114 and bool(reciters) and has_saheeh
        return {
            "ok": ok,
            "details": (
                "authenticated and parsed Quran.Foundation chapters, reciters, "
                "and Saheeh International translation metadata"
            ),
        }
    except Exception as error:
        detail = str(error)
        for secret in [settings.qf_client_id, settings.qf_client_secret]:
            if secret:
                detail = detail.replace(secret, "[redacted]")
        return {"ok": False, "details": detail[:300], "ci": ci}


def _first_list(payload: Any, *keys: str) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def run_doctor(ci: bool = False) -> dict[str, Any]:
    settings = get_settings()
    ffmpeg_ok, ffmpeg_output = _command_ok(["ffmpeg", "-version"])
    ffprobe_ok, ffprobe_output = _command_ok(["ffprobe", "-version"])
    font_ok, font_output = _command_ok(["fc-match", "Amiri Quran"])
    fonts = resolve_fonts()
    badge_font_ok = fonts.badge_surah.path.exists()
    redis_ok = True
    redis_message = "not checked"
    if ci:
        redis_ok = True
        redis_message = "skipped in static CI doctor"
    writable = {}
    for path in [
        settings.backgrounds_dir,
        settings.renders_dir,
        settings.cache_dir,
        settings.mp3quran_audio_cache_dir,
        settings.quran_foundation_audio_cache_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".write-test"
        try:
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            writable[str(path)] = True
        except OSError:
            writable[str(path)] = False
    quran_foundation_metadata = _quran_foundation_metadata_check(settings, ci=ci)
    payload: dict[str, Any] = {
        "ffmpeg": {
            "ok": ffmpeg_ok and "libass" in ffmpeg_output,
            "details": ffmpeg_output.splitlines()[0] if ffmpeg_output else "",
        },
        "ffprobe": {
            "ok": ffprobe_ok,
            "details": ffprobe_output.splitlines()[0] if ffprobe_output else "",
        },
        "fontconfig": {"ok": font_ok, "details": font_output},
        "fonts": {
            "arabic_quran": fonts.arabic_quran.__dict__,
            "arabic_ui": fonts.arabic_ui.__dict__,
            "english": fonts.english.__dict__,
            "badge_surah": fonts.badge_surah.__dict__,
        },
        "badge_surah_font": {"ok": badge_font_ok},
        "pillow_raqm": {"ok": bool(features.check_feature("raqm"))},
        "redis": {"ok": redis_ok, "details": redis_message},
        "quran_foundation_metadata": quran_foundation_metadata,
        "writable": writable,
    }
    payload["ok"] = (
        payload["ffmpeg"]["ok"]
        and payload["ffprobe"]["ok"]
        and payload["fontconfig"]["ok"]
        and payload["badge_surah_font"]["ok"]
        and payload["pillow_raqm"]["ok"]
        and payload["quran_foundation_metadata"]["ok"]
        and all(writable.values())
    )
    return json.loads(json.dumps(payload, default=str))
