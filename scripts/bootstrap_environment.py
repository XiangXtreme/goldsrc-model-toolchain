#!/usr/bin/env python3
"""Inspect or install the public GoldSrc Blender Extension."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from build_extension import build as build_extension
from goldsrc_toolchain.paths import resolve_toolchain


SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
MANIFEST_PATH = REPO_ROOT / "tool-manifest.json"
EXTENSION_SOURCE = REPO_ROOT / "extension" / "goldsrc_model_toolchain"
EXTENSION_ID = "goldsrc_model_toolchain"
MCP_SERVER_BASELINE = "blender-mcp==1.6.0"
MCP_PROTOCOL_BASELINE = "mcp==1.29.0"
MCP_ADDON_BASELINE_SHA256 = "1ee6747df73f26e6660c6cd8955b83b4258288082d55921c3cf7d9f72b5a3c9f"


def _payload_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() not in {".pyc", ".pyo"}
        and "__pycache__" not in path.parts
        and ".git" not in path.parts
        and path.name != "error.log"
    )


def _canonical_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if b"\0" in data:
        return data
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return data.replace(b"\r\n", b"\n")


def _tree_digest(root: Path) -> tuple[str | None, int, int]:
    if not root.is_dir():
        return None, 0, 0
    digest = hashlib.sha256()
    files = _payload_files(root)
    total = 0
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = _canonical_bytes(path)
        digest.update(len(relative).to_bytes(4, "little"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "little"))
        digest.update(data)
        total += len(data)
    return digest.hexdigest(), len(files), total


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(_canonical_bytes(path)).hexdigest() if path.is_file() else None


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def verify_bundles() -> dict:
    manifest = _load_manifest()
    expected = manifest.get("bundles", {}).get("goldsrc_model_toolchain", {})
    root = REPO_ROOT / expected.get("root", "missing")
    digest, files, byte_count = _tree_digest(root)
    bundle = {
        "root": str(root), "exists": root.is_dir(), "version": expected.get("version"),
        "files": files, "bytes": byte_count, "sha256_tree": digest,
        "valid": digest == expected.get("sha256_tree")
        and files == expected.get("files") and byte_count == expected.get("bytes"),
    }
    critical = {}
    for relative, facts in manifest.get("critical_files", {}).items():
        path = REPO_ROOT / relative
        critical[relative] = {
            "exists": path.is_file(), "bytes": path.stat().st_size if path.is_file() else 0,
            "sha256": _sha256(path),
            "valid": path.is_file() and path.stat().st_size == facts.get("bytes") and _sha256(path) == facts.get("sha256"),
        }
    valid = bundle["valid"] and all(item["valid"] for item in critical.values())
    return {"status": "pass" if valid else "fail", "bundles": {"goldsrc_model_toolchain": bundle}, "critical_files": critical}


def _repository_layout_fact() -> dict:
    manifests = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in REPO_ROOT.rglob("SKILL.md")
    )
    runtime_directories = [
        name for name in ("artifacts", "dist", "outputs", "work")
        if (REPO_ROOT / name).exists()
    ]
    valid = not manifests and not runtime_directories
    return {
        "valid": valid,
        "skill_manifests": manifests,
        "runtime_directories": runtime_directories,
    }


def _socket_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _blender_version(blender: Path | None) -> dict:
    if blender is None:
        return {"path": None, "version": None, "valid": False}
    completed = subprocess.run([str(blender), "--version"], capture_output=True, text=True, errors="replace", timeout=30)
    line = (completed.stdout or completed.stderr).splitlines()[0] if completed.stdout or completed.stderr else ""
    match = re.search(r"Blender\s+(\d+\.\d+(?:\.\d+)?)", line)
    version = match.group(1) if match else None
    return {"path": str(blender), "version": version, "valid": bool(version and version.startswith("5.2"))}


def _query_blender_state(blender: Path) -> dict:
    expression = (
        "import bpy,json,sys;"
        "e=sorted(bpy.context.preferences.addons.keys());"
        "print('GOLDSRC_BLENDER_STATE='+json.dumps({"
        "'enabled':e,'module_files':{n:getattr(sys.modules.get(n),'__file__',None) for n in e},"
        "'addon_root':bpy.utils.user_resource('SCRIPTS',path='addons')}))"
    )
    completed = subprocess.run(
        [str(blender), "--background", "--python-expr", expression],
        capture_output=True, text=True, errors="replace", timeout=90,
    )
    match = re.search(r"^GOLDSRC_BLENDER_STATE=(.+)$", completed.stdout, re.MULTILINE)
    if completed.returncode or not match:
        raise RuntimeError(f"Could not query Blender state:\n{completed.stdout}\n{completed.stderr}")
    return json.loads(match.group(1))


def _codex_config_path() -> Path:
    return Path(os.environ.get("GOLDSRC_CODEX_CONFIG", Path.home() / ".codex" / "config.toml")).expanduser().resolve()


def _codex_fact() -> dict:
    path = _codex_config_path()
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    table = re.search(r"(?ms)^\[mcp_servers\.blender-mcp\]\s*$.*?(?=^\[|\Z)", text)
    body = table.group(0) if table else ""
    server = re.search(r"blender-mcp(?:==|@)([0-9.]+)", body)
    protocol = re.search(r"mcp==([0-9.]+)", body)
    return {
        "config": str(path), "configured": bool(table), "table": body,
        "server_version": server.group(1) if server else None,
        "protocol_version": protocol.group(1) if protocol else None,
        "verified_baseline": MCP_SERVER_BASELINE in body and MCP_PROTOCOL_BASELINE in body,
        "verified_baseline_specs": [MCP_SERVER_BASELINE, MCP_PROTOCOL_BASELINE],
    }


def _mcp_fact(state: dict) -> dict:
    path_text = state.get("module_files", {}).get("addon")
    path = Path(path_text).resolve() if path_text else None
    text = path.read_text(encoding="utf-8", errors="replace") if path and path.is_file() else ""
    digest = _sha256(path) if path else None
    identity = "github.com/ahujasid" in text[:4000] and '"name": "Blender MCP"' in text[:8000]
    return {
        "module": "addon", "path": str(path) if path else None,
        "enabled": "addon" in state.get("enabled", []), "official_identity": identity,
        "sha256": digest, "verified_baseline": digest == MCP_ADDON_BASELINE_SHA256,
        "compatibility": "verified_baseline" if digest == MCP_ADDON_BASELINE_SHA256 else "official_unregressed_version" if identity else "unverified_identity",
    }


def _extension_fact(state: dict, manifest: dict) -> dict:
    modules = [name for name in state.get("enabled", []) if name.endswith(".goldsrc_model_toolchain")]
    module = modules[0] if len(modules) == 1 else None
    module_file = state.get("module_files", {}).get(module) if module else None
    root = Path(module_file).resolve().parent if module_file else None
    digest, files, byte_count = _tree_digest(root) if root else (None, 0, 0)
    expected = manifest["bundles"]["goldsrc_model_toolchain"]
    return {
        "module": module, "root": str(root) if root else None, "enabled": module is not None,
        "files": files, "bytes": byte_count, "sha256_tree": digest,
        "matches_bundle": digest == expected["sha256_tree"] and files == expected["files"] and byte_count == expected["bytes"],
    }


def inspect_environment(
    *, blender_override: Path | None = None, sven_override: Path | None = None,
    mcp_host: str = "127.0.0.1", mcp_port: int = 9876,
) -> dict:
    manifest = _load_manifest()
    bundles = verify_bundles()
    repository_layout = _repository_layout_fact()
    paths = resolve_toolchain(blender=blender_override, sven_studiomdl=sven_override)
    blender = _blender_version(paths.blender)
    state = {}
    query_error = None
    if blender["valid"]:
        try:
            state = _query_blender_state(paths.blender)
        except RuntimeError as exc:
            query_error = str(exc)
    extension = _extension_fact(state, manifest) if state else {"enabled": False, "matches_bundle": False}
    mcp = _mcp_fact(state) if state else {"enabled": False, "official_identity": False, "verified_baseline": False}
    codex = _codex_fact()
    legacy = {name: name in state.get("enabled", []) for name in ("io_scene_valvesource", "SourceIO")}
    socket_ready = _socket_listening(mcp_host, mcp_port)
    actions = []
    if bundles["status"] != "pass":
        actions.append("repair_extension_bundle")
    if not blender["valid"]:
        actions.append("install_or_select_blender_5_2")
    if not extension.get("matches_bundle"):
        actions.append("install_goldsrc_model_toolchain_extension")
    if any(legacy.values()):
        actions.append("disable_legacy_goldsrc_addons")
    if not mcp.get("official_identity"):
        actions.append("externally_install_official_ahujasid_blender_mcp")
    if not codex["configured"]:
        actions.append("externally_configure_blender_mcp_service")
    elif not codex["verified_baseline"]:
        actions.append("run_mcp_compatibility_regression")
    if not socket_ready:
        actions.append("launch_blender_mcp")
    if not repository_layout["valid"]:
        actions.append("relocate_runtime_artifacts_outside_repository")
    configured = bool(
        bundles["status"] == "pass" and repository_layout["valid"] and blender["valid"]
        and extension.get("matches_bundle") and mcp.get("official_identity") and mcp.get("enabled")
        and codex["configured"] and not any(legacy.values())
        and paths.sven_studiomdl and paths.sven_studiomdl.is_file()
    )
    return {
        "status": "pass" if configured and socket_ready else "needs_action",
        "configured": configured,
        "live_mcp_ready": socket_ready,
        "bundled_tools": bundles,
        "repository_layout": repository_layout,
        "blender": blender,
        "blender_state_error": query_error,
        "extension": extension,
        "repository_managed_blender_plugin_count": 1,
        "legacy_addons": legacy,
        "blender_mcp": mcp,
        "codex": codex,
        "compilers": {"sven": str(paths.sven_studiomdl) if paths.sven_studiomdl else None},
        "mcp_socket": {"host": mcp_host, "port": mcp_port, "listening": socket_ready},
        "actions": list(dict.fromkeys(actions)),
    }


def _install_extension(blender: Path, archive: Path) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        [str(blender), "--command", "extension", "install-file", "-r", "user_default", "-e", str(archive)],
        capture_output=True, text=True, errors="replace", timeout=300,
    )
    if completed.returncode:
        raise RuntimeError(f"Extension install failed:\n{completed.stdout}\n{completed.stderr}")
    return completed


def _configure_blender(blender: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="goldsrc_extension_config_") as temporary:
        report_path = Path(temporary) / "report.json"
        environment = os.environ.copy()
        environment["GOLDSRC_BOOTSTRAP_REPORT"] = str(report_path)
        completed = subprocess.run(
            [str(blender), "--background", "--python", str(SCRIPTS / "configure_blender_addons.py")],
            env=environment, capture_output=True, text=True, errors="replace", timeout=180,
        )
        if completed.returncode or not report_path.is_file():
            raise RuntimeError(f"Blender Extension configuration failed:\n{completed.stdout}\n{completed.stderr}")
        return json.loads(report_path.read_text(encoding="utf-8"))


def _launch_blender(blender: Path, host: str, port: int) -> bool:
    if _socket_listening(host, port):
        return False
    subprocess.Popen(
        [str(blender), "--python", str(SCRIPTS / "start_blender_52_mcp.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _index in range(40):
        if _socket_listening(host, port):
            return True
        time.sleep(0.5)
    raise RuntimeError("Blender launched but the official MCP socket did not bind within 20 seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--launch-blender", action="store_true")
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--sven-studiomdl", type=Path)
    parser.add_argument("--mcp-host", default="127.0.0.1")
    parser.add_argument("--mcp-port", type=int, default=9876)
    args = parser.parse_args()
    before = inspect_environment(
        blender_override=args.blender, sven_override=args.sven_studiomdl,
        mcp_host=args.mcp_host, mcp_port=args.mcp_port,
    )
    if not args.apply:
        print(json.dumps(before, indent=2, ensure_ascii=False))
        return 0 if before["configured"] and before["live_mcp_ready"] else 1
    if before["bundled_tools"]["status"] != "pass":
        raise RuntimeError("GoldSrc Extension bundle integrity failed")
    if not before["blender"]["valid"]:
        raise RuntimeError("Blender 5.2 was not found; set GOLDSRC_BLENDER and rerun")
    if not before["blender_mcp"].get("official_identity"):
        raise RuntimeError("Install the official ahujasid/blender-mcp add-on externally before bootstrap")
    if not before["codex"]["configured"]:
        raise RuntimeError("Configure the official blender-mcp service externally before bootstrap")
    blender = Path(before["blender"]["path"])
    with tempfile.TemporaryDirectory(prefix="goldsrc_extension_build_") as temporary:
        archive = Path(temporary) / "goldsrc_model_toolchain-1.3.3-windows-x64.zip"
        build_report = build_extension(archive, blender=blender)
        installed = _install_extension(blender, archive)
    configured = _configure_blender(blender)
    launched = _launch_blender(blender, args.mcp_host, args.mcp_port) if args.launch_blender else False
    after = inspect_environment(
        blender_override=args.blender, sven_override=args.sven_studiomdl,
        mcp_host=args.mcp_host, mcp_port=args.mcp_port,
    )
    result = {
        "status": "pass" if after["configured"] else "fail",
        "before": before,
        "applied": {
            "extension_build": build_report,
            "extension_install_stdout": installed.stdout[-4000:],
            "blender_preferences": configured,
            "blender_launched": launched,
            "mcp_files_or_config_modified": False,
        },
        "after": after,
        "restart": {
            "blender_required": before["live_mcp_ready"] and not launched,
            "codex_required": False,
        },
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
