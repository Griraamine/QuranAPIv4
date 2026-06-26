#!/usr/bin/env python
from __future__ import annotations

import argparse
import json

from quran_video.config.doctor import run_doctor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    result = run_doctor(ci=args.ci)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
