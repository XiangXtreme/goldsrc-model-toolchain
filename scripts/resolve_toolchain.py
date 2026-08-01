#!/usr/bin/env python3
"""Print the resolved portable GoldSrc/Blender toolchain paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from goldsrc_toolchain.paths import resolve_toolchain


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--sven-studiomdl", type=Path)
    parser.add_argument("--player-sdk", type=Path)
    parser.add_argument("--player-reference-smd", type=Path)
    parser.add_argument("--official-player-mdl", type=Path)
    args = parser.parse_args()
    paths = resolve_toolchain(**vars(args))
    print(json.dumps(paths.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
