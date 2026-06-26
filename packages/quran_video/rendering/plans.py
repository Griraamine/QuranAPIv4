from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoopSegment:
    source_start: float
    source_end: float
    output_start: float
    output_end: float
    crossfade_seconds: float


def video_loop_crossfade_plan(
    source_duration: float, output_duration: float, crossfade: float = 0.75
) -> list[LoopSegment]:
    if source_duration <= 0 or output_duration <= 0:
        raise ValueError("durations must be positive")
    segments: list[LoopSegment] = []
    cursor = 0.0
    first = True
    while cursor < output_duration:
        overlap = 0.0 if first else min(crossfade, source_duration / 2, output_duration - cursor)
        start = max(0.0, cursor - overlap)
        end = min(output_duration, start + source_duration)
        segments.append(
            LoopSegment(
                source_start=0.0,
                source_end=end - start,
                output_start=start,
                output_end=end,
                crossfade_seconds=overlap,
            )
        )
        cursor = end
        first = False
    return segments
