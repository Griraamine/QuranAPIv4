from __future__ import annotations

import asyncio
import html
import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Annotated, Any

import redis
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from rq import Queue, Worker
from worker.tasks import run_render_job

from quran_video.api_clients.quran_foundation import (
    QuranFoundationConfigurationError,
    QuranFoundationError,
)
from quran_video.config import get_settings
from quran_video.config.doctor import run_doctor
from quran_video.config.visual_style import load_visual_style, save_visual_style
from quran_video.models import RenderRequest, VisualStyleSettings
from quran_video.models.domain import RenderStatus
from quran_video.quran.artistic_names import surah_artistic_name
from quran_video.quran.compatibility import bismillah_policy, validate_ayah_range
from quran_video.quran.render_defaults import resolve_render_request_defaults
from quran_video.quran.repository import QuranRepository
from quran_video.rendering.media import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    list_backgrounds,
    probe_media,
    safe_background_path,
)
from quran_video.storage import JobStore

settings = get_settings()
app = FastAPI(title="Quran Video Platform", version="0.1.0")
_background_tasks: set[asyncio.Task[None]] = set()
_job_store: JobStore | None = None
MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(QuranFoundationConfigurationError)
async def quran_foundation_configuration_error(
    _request: Request, error: QuranFoundationConfigurationError
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "quran_foundation_configuration",
                "message": (
                    f"{error}. Set QF_CLIENT_ID and QF_CLIENT_SECRET, or set "
                    "QURAN_VIDEO_DATA_MODE=fixture for offline local development."
                ),
            }
        },
    )


@app.exception_handler(QuranFoundationError)
async def quran_foundation_error(_request: Request, error: QuranFoundationError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "detail": {
                "code": "quran_foundation_unavailable",
                "message": str(error),
            }
        },
    )


class StructuredError(BaseModel):
    code: str
    message: str


def _repo() -> QuranRepository:
    return QuranRepository(settings)


def _request_data_mode(request: RenderRequest) -> str:
    return request.data_mode or settings.quran_video_data_mode


def _repo_for_render_request(request: RenderRequest) -> QuranRepository:
    return QuranRepository(
        settings.model_copy(update={"quran_video_data_mode": _request_data_mode(request)})
    )


def _store() -> JobStore:
    global _job_store
    if _job_store is None or _job_store.path != settings.sqlite_path:
        _job_store = JobStore(settings.sqlite_path)
    return _job_store


def _queue_has_worker(connection: redis.Redis, queue_name: str) -> bool:
    return any(queue_name in worker.queue_names() for worker in Worker.all(connection=connection))


def _start_inline_render(job_id: str) -> None:
    task = asyncio.create_task(asyncio.to_thread(run_render_job, job_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _cleanup_expired_outputs() -> None:
    _store().cleanup_expired_outputs()


def _safe_upload_name(filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail={"code": "unsupported_media", "message": "Unsupported media extension"},
        )
    return f"{uuid.uuid4().hex}{suffix}"


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/system/doctor")
async def doctor() -> dict[str, Any]:
    return run_doctor(ci=False)


@app.get("/api/v1/chapters")
async def chapters() -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in await _repo().chapters():
        chapter = item.model_dump()
        chapter["artistic_arabic_name"] = surah_artistic_name(item.id, item.arabic_name)
        payload.append(chapter)
    return payload


@app.get("/api/v1/style")
async def visual_style() -> dict[str, Any]:
    return load_visual_style().model_dump(mode="json")


@app.put("/api/v1/style")
async def update_visual_style(style: VisualStyleSettings) -> dict[str, Any]:
    return save_visual_style(style).model_dump(mode="json")


@app.get("/api/v1/reciters")
async def reciters() -> list[dict[str, Any]]:
    return [item.model_dump() for item in await _repo().reciters()]


@app.get("/api/v1/compatibility")
async def compatibility(
    reciter_id: str = "", chapter_id: int = 1, moshaf_id: str | None = None
) -> dict[str, Any]:
    repo = _repo()
    request = await _resolve_render_request(
        RenderRequest(reciter_id=reciter_id, chapter_id=chapter_id, moshaf_id=moshaf_id),
        repo,
        require_background=False,
    )
    return (
        await repo.compatibility(request.chapter_id, request.reciter_id, request.moshaf_id)
    ).model_dump()


@app.get("/api/v1/chapters/{chapter_id}/verses")
async def chapter_verses(chapter_id: int) -> list[dict[str, Any]]:
    chapters_data = await _repo().chapters()
    chapter = next((item for item in chapters_data if item.id == chapter_id), None)
    if chapter is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "chapter_not_found", "message": "Chapter was not found"},
        )
    return [item.model_dump() for item in await _repo().verses(chapter_id)]


@app.get("/api/v1/backgrounds")
async def backgrounds() -> list[dict[str, Any]]:
    return [item.model_dump() for item in list_backgrounds()]


@app.get("/api/v1/backgrounds/file/{background_id:path}")
async def background_file(background_id: str) -> FileResponse:
    try:
        path = safe_background_path(background_id)
        probe_media(path)
    except Exception as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "background_not_found", "message": "Background was not found"},
        ) from error
    media_type = MEDIA_TYPES.get(path.suffix.casefold(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.post("/api/v1/backgrounds/upload")
async def upload_background(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    safe_name = _safe_upload_name(file.filename or "background")
    target = settings.backgrounds_dir / safe_name
    settings.backgrounds_dir.mkdir(parents=True, exist_ok=True)
    size = 0
    with target.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.upload_size_limit:
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "upload_too_large",
                        "message": "Background upload is too large",
                    },
                )
            handle.write(chunk)
    try:
        probe = probe_media(target)
    except Exception as error:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_media", "message": "Uploaded media cannot be decoded"},
        ) from error
    return {"id": target.name, "filename": target.name, "media_type": probe.media_type}


@app.post("/api/v1/render/validate")
async def validate_render(request: RenderRequest) -> dict[str, Any]:
    request = request.model_copy(update={"data_mode": _request_data_mode(request)})
    repo = _repo_for_render_request(request)
    request = await _resolve_render_request(request, repo)
    chapters_data = await repo.chapters()
    chapter = next((item for item in chapters_data if item.id == request.chapter_id), None)
    if chapter is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "chapter_not_found", "message": "Chapter was not found"},
        )
    try:
        validate_ayah_range(chapter, request.ayah_from, request.ayah_to)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_ayah_range", "message": str(error)},
        ) from error
    compatibility_result = await repo.compatibility(
        request.chapter_id, request.reciter_id, request.moshaf_id
    )
    audio = None
    if compatibility_result.compatible:
        audio = await repo.chapter_audio(request.chapter_id, request.reciter_id, request.moshaf_id)
    return {
        "compatible": compatibility_result.compatible,
        "reason": compatibility_result.reason,
        "bismillah": bismillah_policy(request.chapter_id, request.ayah_from, audio).__dict__,
        "resolved_request": request.model_dump(mode="json"),
    }


@app.post("/api/v1/renders")
async def create_render(request: RenderRequest) -> dict[str, Any]:
    _cleanup_expired_outputs()
    request = request.model_copy(update={"data_mode": _request_data_mode(request)})
    repo = _repo_for_render_request(request)
    request = await _resolve_render_request(request, repo)
    job_id = str(uuid.uuid4())
    store = _store()
    store.create(job_id, request.model_dump(mode="json"))
    try:
        connection = redis.from_url(settings.redis_url)
        connection.ping()
        if not _queue_has_worker(connection, "renders"):
            raise RuntimeError("no active render worker")
        Queue("renders", connection=connection).enqueue(run_render_job, job_id, job_timeout="6h")
    except Exception:
        _start_inline_render(job_id)
    return store.get(job_id).model_dump(mode="json")


async def _resolve_render_request(
    request: RenderRequest,
    repo: QuranRepository,
    *,
    require_background: bool = True,
) -> RenderRequest:
    try:
        return await resolve_render_request_defaults(
            request,
            repo,
            require_background=require_background,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_render_request", "message": str(error)},
        ) from error


@app.get("/api/v1/renders/{job_id}")
async def get_render(job_id: str) -> dict[str, Any]:
    _cleanup_expired_outputs()
    try:
        record = _store().get(job_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail={"code": "job_not_found", "message": "Render job was not found"}
        ) from error
    payload = record.model_dump(mode="json")
    payload["logs"] = _store().logs(job_id)
    return payload


@app.get("/api/v1/renders/{job_id}/events")
async def render_events(job_id: str) -> StreamingResponse:
    store = _store()

    async def stream():
        while True:
            try:
                record = store.get(job_id)
            except KeyError:
                yield 'event: error\ndata: {"code":"job_not_found"}\n\n'
                return
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower():
                    raise
                await asyncio.sleep(0.25)
                continue
            yield f"data: {json.dumps(record.model_dump(mode='json'))}\n\n"
            if record.status in {RenderStatus.complete, RenderStatus.failed, RenderStatus.canceled}:
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/v1/renders/{job_id}/cancel")
async def cancel_render(job_id: str) -> dict[str, Any]:
    store = _store()
    try:
        record = store.update(
            job_id, status=RenderStatus.canceled.value, phase="canceled", progress=0
        )
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail={"code": "job_not_found", "message": "Render job was not found"}
        ) from error
    output_dir = settings.renders_dir / job_id
    shutil.rmtree(output_dir, ignore_errors=True)
    return record.model_dump(mode="json")


def _download_path(job_id: str, kind: str) -> Path:
    record = _store().get(job_id)
    path_value = record.video_path if kind == "video" else record.thumbnail_path
    if not path_value:
        raise HTTPException(
            status_code=404, detail={"code": "output_not_ready", "message": "Output is not ready"}
        )
    path = Path(path_value).resolve()
    root = settings.renders_dir.resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise HTTPException(
            status_code=404, detail={"code": "output_missing", "message": "Output file is missing"}
        ) from error
    if not path.exists():
        raise HTTPException(
            status_code=404, detail={"code": "output_missing", "message": "Output file is missing"}
        )
    return path


@app.get("/api/v1/renders/{job_id}/video")
async def download_video(job_id: str) -> FileResponse:
    return FileResponse(
        _download_path(job_id, "video"), media_type="video/mp4", filename="quran-video.mp4"
    )


@app.get("/api/v1/renders/{job_id}/thumbnail")
async def download_thumbnail(job_id: str) -> FileResponse:
    return FileResponse(
        _download_path(job_id, "thumbnail"), media_type="image/jpeg", filename="thumbnail.jpg"
    )


@app.get("/api/v1/renders/{job_id}/outputs")
async def render_outputs(job_id: str) -> HTMLResponse:
    try:
        record = _store().get(job_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail={"code": "job_not_found", "message": "Render job was not found"}
        ) from error
    title = html.escape(f"Render {job_id}")
    status = html.escape(record.status.value)
    if record.status != RenderStatus.complete:
        return HTMLResponse(
            f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:system-ui;margin:32px;background:#111;color:#eee">
<h1>{title}</h1><p>Status: {status}</p>
</body></html>"""
        )
    video_url = f"/api/v1/renders/{html.escape(job_id)}/video"
    thumbnail_url = f"/api/v1/renders/{html.escape(job_id)}/thumbnail"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; background: #101411; color: #f3f4ef; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px; }}
    video, img {{ width: 100%; border: 1px solid #394238; background: #000; }}
    .actions {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 18px 0 28px; }}
    a {{ color: #f3f4ef; background: #667b62; padding: 10px 14px; border-radius: 6px; text-decoration: none; }}
    section {{ margin-top: 28px; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <div class="actions">
      <a href="{video_url}" download>Download MP4</a>
      <a href="{thumbnail_url}" download>Download thumbnail</a>
    </div>
    <section>
      <h2>Video</h2>
      <video controls src="{video_url}"></video>
    </section>
    <section>
      <h2>Thumbnail</h2>
      <img src="{thumbnail_url}" alt="Rendered thumbnail">
    </section>
  </main>
</body>
</html>"""
    )


@app.delete("/api/v1/renders/{job_id}")
async def delete_render(job_id: str) -> dict[str, bool]:
    output_dir = settings.renders_dir / job_id
    shutil.rmtree(output_dir, ignore_errors=True)
    _store().delete(job_id)
    return {"deleted": True}
