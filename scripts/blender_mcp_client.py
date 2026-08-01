#!/usr/bin/env python3
"""Minimal loopback client for the Blender MCP add-on socket."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path


def request(host: str, port: int, payload: dict, timeout: float) -> dict:
    decoder = json.JSONDecoder()
    received = ""
    with socket.create_connection((host, port), timeout=timeout) as client:
        client.settimeout(timeout)
        client.sendall(json.dumps(payload).encode("utf-8"))
        while True:
            chunk = client.recv(65536)
            if not chunk:
                raise RuntimeError("Blender MCP closed the connection without a complete response")
            received += chunk.decode("utf-8")
            try:
                result, end = decoder.raw_decode(received)
            except json.JSONDecodeError:
                continue
            if received[end:].strip():
                raise RuntimeError("Blender MCP returned trailing non-JSON data")
            return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("info", "execute"))
    parser.add_argument("--code-file", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.command == "info":
        payload = {"type": "get_scene_info", "params": {}}
    else:
        if not args.code_file or not args.code_file.is_file():
            parser.error("execute requires --code-file")
        code = f"__file__ = {str(args.code_file.resolve())!r}\n" + args.code_file.read_text(encoding="utf-8")
        payload = {"type": "execute_code", "params": {"code": code}}
    try:
        response = request(args.host, args.port, payload, args.timeout)
    except (OSError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
