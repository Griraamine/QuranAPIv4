from __future__ import annotations

import json
import secrets
from pathlib import Path

from quran_video.models import AutomationState


def load_state(path: Path) -> AutomationState:
    if not path.exists():
        return AutomationState()
    return AutomationState.model_validate_json(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: AutomationState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def shuffled_surah_queue() -> list[int]:
    values = list(range(1, 115))
    rng = secrets.SystemRandom()
    rng.shuffle(values)
    return values


def choose_surah(state: AutomationState) -> tuple[int, AutomationState]:
    if not state.surah_queue:
        state.surah_queue = shuffled_surah_queue()
        state.cycle_number += 1
    return state.surah_queue[0], state


class AutomationStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AutomationState:
        return load_state(self.path)

    def save(self, state: AutomationState) -> None:
        save_state(self.path, state)

    def mark_success(
        self, state: AutomationState, surah_id: int, payload: dict[str, object]
    ) -> AutomationState:
        if state.surah_queue and state.surah_queue[0] == surah_id:
            state.surah_queue.pop(0)
        else:
            state.surah_queue = [item for item in state.surah_queue if item != surah_id]
        state.last_success = payload
        state.recent_failures = []
        self.save(state)
        return state
