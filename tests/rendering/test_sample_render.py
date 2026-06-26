from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.rendering
def test_sample_render_script_outputs_valid_files() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/render_sample.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    video = Path(payload["video"])
    thumbnail = Path(payload["thumbnail"])
    assert video.exists()
    assert thumbnail.exists()
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name,pix_fmt",
            "-of",
            "json",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["width"] == 1920
    assert stream["height"] == 1080
    assert stream["codec_name"] == "h264"
    assert stream["pix_fmt"] == "yuv420p"
