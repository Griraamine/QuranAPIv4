from __future__ import annotations

import json
import math
import os
import select
import shutil
import signal
import subprocess
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from quran_video.models import BackgroundMode, RenderRequest, RenderResult, RenderTimeline
from quran_video.rendering.fonts import resolve_fonts
from quran_video.rendering.media import probe_media, safe_background_path
from quran_video.subtitles.ass import generate_ass
from quran_video.thumbnails.generator import generate_thumbnail

OUTPUT_FRAME_RATE = 30


class RenderCanceled(RuntimeError):
    pass


def generate_fixture_audio(path: Path, duration_ms: int) -> Path:
    duration = max(duration_ms / 1000, 1.0)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            f"{duration:.3f}",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return path


def render_video(
    timeline: RenderTimeline,
    request: RenderRequest,
    output_dir: Path,
    audio_path: Path | None = None,
    *,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[float, float], None] | None = None,
) -> RenderResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    fonts = resolve_fonts()
    ass_path = generate_ass(output_dir / "subtitles.ass", timeline, request, fonts)
    if audio_path is None:
        audio_path = generate_fixture_audio(output_dir / "fixture_audio.wav", timeline.duration_ms)
    background_paths = [safe_background_path(identifier) for identifier in request.background_ids]
    for path in background_paths:
        probe_media(path)
    video_path = output_dir / "video.mp4"
    thumbnail_path = output_dir / "thumbnail.jpg"
    duration = timeline.duration_ms / 1000
    if request.background_mode == BackgroundMode.slideshow:
        command = _slideshow_command(
            background_paths,
            audio_path,
            ass_path,
            video_path,
            duration,
            request.background_style.dim_opacity,
        )
    else:
        command = _single_background_command(
            background_paths[0],
            audio_path,
            ass_path,
            video_path,
            duration,
            request.background_style.dim_opacity,
        )
    _run_ffmpeg(
        command,
        output_dir,
        duration,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    )
    validation = validate_output(video_path)
    if not validation["valid"]:
        raw_errors = validation.get("errors", [])
        errors = raw_errors if isinstance(raw_errors, list) else [raw_errors]
        raise RuntimeError("; ".join(str(error) for error in errors))
    generate_thumbnail(
        background_paths[0],
        thumbnail_path,
        timeline,
        request,
        fonts=fonts,
    )
    return RenderResult(
        video_path=video_path,
        thumbnail_path=thumbnail_path,
        duration_seconds=duration,
        metadata={"ffprobe": validation},
    )


def _run_ffmpeg(
    command: list[str],
    cwd: Path,
    duration: float,
    *,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[float, float], None] | None = None,
) -> None:
    if cancel_check is None and progress_callback is None:
        subprocess.run(command, check=True, capture_output=True, text=True, cwd=cwd)
        return
    progress_command = [*command[:-1], "-nostats", "-progress", "pipe:1", command[-1]]
    process = subprocess.Popen(
        progress_command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    stderr_tail: list[str] = []
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        while process.poll() is None:
            if cancel_check and cancel_check():
                _terminate_process_group(process)
                raise RenderCanceled("render canceled")
            readable, _, _ = select.select([process.stdout, process.stderr], [], [], 0.5)
            for stream in readable:
                line = stream.readline()
                if not line:
                    continue
                if stream is process.stdout:
                    _handle_progress_line(line, duration, progress_callback)
                else:
                    stderr_tail.append(line)
                    stderr_tail = stderr_tail[-200:]
        stdout, stderr = process.communicate(timeout=5)
    except RenderCanceled:
        raise
    except Exception:
        _terminate_process_group(process)
        raise
    if stdout:
        for line in stdout.splitlines():
            _handle_progress_line(line, duration, progress_callback)
    if stderr:
        stderr_tail.extend(stderr.splitlines())
        stderr_tail = stderr_tail[-200:]
    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode or 1,
            progress_command,
            output="",
            stderr="".join(stderr_tail),
        )


def _handle_progress_line(
    line: str,
    duration: float,
    progress_callback: Callable[[float, float], None] | None,
) -> None:
    if progress_callback is None or not line.startswith("out_time_ms="):
        return
    try:
        seconds = max(0.0, int(line.partition("=")[2]) / 1_000_000)
    except ValueError:
        return
    progress_callback(seconds, duration)


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def _ass_filter_arg(ass_path: Path) -> str:
    escaped = str(ass_path).replace("\\", "\\\\").replace(":", "\\:")
    fonts_dir = Path(__file__).resolve().parents[1] / "assets" / "fonts"
    escaped_fonts_dir = str(fonts_dir).replace("\\", "\\\\").replace(":", "\\:")
    return f"ass={escaped}:fontsdir={escaped_fonts_dir}"


def _single_background_command(
    background_path: Path,
    audio_path: Path,
    ass_path: Path,
    video_path: Path,
    duration: float,
    dim_opacity: int,
) -> list[str]:
    probe = probe_media(background_path)
    vf = _video_filter(
        ass_path,
        dim_opacity,
        cache_static_background=probe.media_type == "image",
    )
    if probe.media_type == "image":
        return [
            "ffmpeg",
            "-y",
            "-i",
            str(background_path),
            "-i",
            str(audio_path),
            "-t",
            f"{duration:.3f}",
            "-vf",
            vf,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-r",
            str(OUTPUT_FRAME_RATE),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-af",
            _audio_filter(duration),
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(video_path),
        ]
    loop_stream = math.ceil(duration / max(probe.duration_seconds or duration, 0.1))
    return [
        "ffmpeg",
        "-y",
        "-stream_loop",
        str(max(loop_stream, 1)),
        "-i",
        str(background_path),
        "-i",
        str(audio_path),
        "-t",
        f"{duration:.3f}",
        "-vf",
        vf,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-analyzeduration",
        "100M",
        "-r",
        str(OUTPUT_FRAME_RATE),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-af",
        _audio_filter(duration),
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(video_path),
    ]


def _slideshow_command(
    background_paths: list[Path],
    audio_path: Path,
    ass_path: Path,
    video_path: Path,
    duration: float,
    dim_opacity: int,
) -> list[str]:
    list_path = video_path.parent / "slideshow.txt"
    per_image = duration / len(background_paths)
    lines = []
    for path in background_paths:
        lines.append(f"file '{path.as_posix()}'")
        lines.append(f"duration {per_image:.3f}")
    lines.append(f"file '{background_paths[-1].as_posix()}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")
    vf = _video_filter(ass_path, dim_opacity)
    return [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-i",
        str(audio_path),
        "-t",
        f"{duration:.3f}",
        "-vf",
        vf,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-r",
        str(OUTPUT_FRAME_RATE),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-af",
        _audio_filter(duration),
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(video_path),
    ]


def _video_filter(
    ass_path: Path,
    dim_opacity: int,
    *,
    cache_static_background: bool = False,
) -> str:
    filters = [
        "scale=1920:1080:force_original_aspect_ratio=increase",
        "crop=1920:1080",
    ]
    if dim_opacity > 0:
        alpha = min(max(dim_opacity, 0), 90) / 100
        filters.append("format=rgba")
        filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{alpha:.3f}:t=fill")
    if cache_static_background:
        # Scaling, cropping, and dimming a still image on every output frame is
        # prohibitively expensive for multi-hour surahs. Cache the prepared
        # frame in the filter graph, then timestamp its duplicates at 30 fps so
        # libass still renders subtitle changes at full temporal resolution.
        filters.extend(
            [
                "format=yuv420p",
                "loop=loop=-1:size=1:start=0",
                f"setpts=N/({OUTPUT_FRAME_RATE}*TB)",
            ]
        )
    filters.extend([_ass_filter_arg(ass_path), "format=yuv420p"])
    return ",".join(filters)


def _audio_filter(duration: float) -> str:
    return f"aresample=48000,apad,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS"


def validate_output(video_path: Path) -> dict[str, object]:
    if not video_path.exists() or video_path.stat().st_size == 0:
        return {"valid": False, "errors": ["output file is missing or empty"]}
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    errors: list[str] = []
    video_stream = next(
        (stream for stream in payload["streams"] if stream["codec_type"] == "video"), None
    )
    audio_stream = next(
        (stream for stream in payload["streams"] if stream["codec_type"] == "audio"), None
    )
    if not video_stream:
        errors.append("missing video stream")
    if not audio_stream:
        errors.append("missing audio stream")
    if video_stream:
        if int(video_stream.get("width", 0)) != 1920 or int(video_stream.get("height", 0)) != 1080:
            errors.append("video dimensions are not 1920x1080")
        if video_stream.get("codec_name") != "h264":
            errors.append("video codec is not h264")
        if video_stream.get("pix_fmt") != "yuv420p":
            errors.append("pixel format is not yuv420p")
        fps = _fps(video_stream.get("avg_frame_rate", "0/1"))
        if abs(fps - 30) > 0.1:
            errors.append("frame rate is not 30fps")
    if audio_stream:
        if audio_stream.get("codec_name") != "aac":
            errors.append("audio codec is not aac")
        if int(audio_stream.get("sample_rate", 0)) != 48000:
            errors.append("audio sample rate is not 48kHz")
    if video_stream and audio_stream:
        video_duration = float(
            video_stream.get("duration") or payload.get("format", {}).get("duration") or 0
        )
        audio_duration = float(
            audio_stream.get("duration") or payload.get("format", {}).get("duration") or 0
        )
        if abs(video_duration - audio_duration) > 0.25:
            errors.append("audio/video durations differ by more than 250ms")
    return {"valid": not errors, "errors": errors, "probe": payload}


def _fps(rate: str) -> float:
    numerator, denominator = rate.split("/")
    return float(numerator) / float(denominator)


def cleanup_partial(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        os.remove(path)
