from pathlib import Path

from quran_video.rendering import ffmpeg as ffmpeg_renderer
from quran_video.rendering.media import MediaProbe


def _option_value(command: list[str], option: str) -> str:
    return command[command.index(option) + 1]


def test_still_background_preparation_is_cached_before_subtitles(monkeypatch) -> None:
    background = Path("background.jpg")
    monkeypatch.setattr(
        ffmpeg_renderer,
        "probe_media",
        lambda _path: MediaProbe(
            path=background,
            media_type="image",
            width=1920,
            height=1080,
            duration_seconds=None,
            has_audio=False,
            sha256="test",
        ),
    )

    command = ffmpeg_renderer._single_background_command(
        background,
        Path("audio.mp3"),
        Path("subtitles.ass"),
        Path("video.mp4"),
        duration=60,
        dim_opacity=47,
    )

    assert "-loop" not in command
    video_filter = _option_value(command, "-vf")
    assert video_filter.index("scale=") < video_filter.index("loop=loop=-1:size=1:start=0")
    assert video_filter.index("drawbox=") < video_filter.index("loop=loop=-1:size=1:start=0")
    assert video_filter.index("loop=loop=-1:size=1:start=0") < video_filter.index("ass=")
    assert "setpts=N/(30*TB)" in video_filter
    assert _option_value(command, "-r") == "30"
