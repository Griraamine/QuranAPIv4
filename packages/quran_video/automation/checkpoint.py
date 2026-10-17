from __future__ import annotations

import os
import subprocess

from quran_video.automation.state import AutomationStateStore
from quran_video.models import AutomationState


def persist_upload_checkpoint(store: AutomationStateStore, state: AutomationState) -> None:
    """Publish the reservation before YouTube is allowed to accept any upload bytes."""
    store.save(state)
    if os.getenv("GITHUB_ACTIONS") != "true":
        return
    commands = [
        ["git", "add", "--", str(store.path)],
        [
            "git",
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit",
            "-m",
            "automation: checkpoint YouTube upload [skip ci]",
        ],
        ["git", "pull", "--rebase"],
        ["git", "push"],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(
                "upload checkpoint could not be published; refusing to start or repeat an upload"
            )


def pair_marker(pair_key: str) -> str:
    return f"[quran-video-pair:{pair_key}]"


def marked_description(description: str, pair_key: str) -> str:
    marker = pair_marker(pair_key)
    # Keep room for the identifier even when the existing description is near its limit.
    suffix = f"\n\n{marker}"
    available = 5000 - len(suffix.encode("utf-8"))
    return (
        description.encode("utf-8")[:available].decode("utf-8", errors="ignore").rstrip() + suffix
    )
