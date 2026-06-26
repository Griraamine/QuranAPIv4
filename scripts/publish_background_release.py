#!/usr/bin/env python
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from quran_video.backgrounds.release import build_background_release


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="media/backgrounds")
    parser.add_argument("--output", default="data/cache/background-release")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    manifest = build_background_release(Path(args.source), Path(args.output))
    print(f"created {manifest}")
    if args.publish:
        subprocess.run(
            [
                "gh",
                "release",
                "create",
                "backgrounds-latest",
                "--title",
                "Backgrounds latest",
                "--notes",
                "Public channel-owned backgrounds",
                "--latest=false",
            ],
            check=False,
        )
        for asset in sorted(Path(args.output).glob("backgrounds-*")):
            subprocess.run(
                ["gh", "release", "upload", "backgrounds-latest", str(asset), "--clobber"],
                check=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
