#!/usr/bin/env python3
"""Check the resolved Blender/GoldSrc toolchain and live MCP socket."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bootstrap_environment import inspect_environment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--sven-studiomdl", type=Path)
    parser.add_argument("--mcp-host", default="127.0.0.1")
    parser.add_argument("--mcp-port", type=int, default=9876)
    args = parser.parse_args()
    result = inspect_environment(
        blender_override=args.blender,
        sven_override=args.sven_studiomdl,
        mcp_host=args.mcp_host,
        mcp_port=args.mcp_port,
    )
    result["repair"] = {
        "configure": "python scripts/bootstrap_environment.py --apply",
        "configure_and_launch": "python scripts/bootstrap_environment.py --apply --launch-blender",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["configured"] and result["live_mcp_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
