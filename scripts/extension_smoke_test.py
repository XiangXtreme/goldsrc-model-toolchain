"""Run inside Blender 5.2 after installing the Extension ZIP."""

from __future__ import annotations

import json
import os
from pathlib import Path

import bpy


def main() -> dict:
    enabled = sorted(bpy.context.preferences.addons.keys())
    extension_modules = [name for name in enabled if name.endswith(".goldsrc_model_toolchain")]
    if len(extension_modules) != 1:
        raise RuntimeError(f"expected one enabled GoldSrc Extension, found {extension_modules}")
    if any(name in enabled for name in ("io_scene_valvesource", "SourceIO")):
        raise RuntimeError("legacy Source Tools or SourceIO is enabled in the clean profile")
    api = bpy.app.driver_namespace.get("goldsrc_model_toolchain")
    if api is None:
        raise RuntimeError("goldsrc_model_toolchain runtime API is not published")
    capabilities = api.capabilities()
    if capabilities.get("ui") is not False:
        raise RuntimeError("Extension unexpectedly reports a UI surface")
    if not hasattr(bpy.ops.goldsrc_toolchain, "execute_stage"):
        raise RuntimeError("goldsrc_toolchain.execute_stage operator is not registered")
    try:
        api.inspect_mdl("missing-smoke-fixture.mdl")
    except Exception as exc:
        error_protocol = {
            "phase": getattr(exc, "phase", None),
            "code": getattr(exc, "code", None),
            "message": getattr(exc, "message", None),
            "details": getattr(exc, "details", None),
        }
        if not all(error_protocol.values()):
            raise RuntimeError(f"runtime API did not use the unified error protocol: {error_protocol}") from exc
    else:
        raise RuntimeError("invalid MDL unexpectedly passed runtime API inspection")
    import PIL

    report = {
        "status": "pass",
        "blender": bpy.app.version_string,
        "python": tuple(__import__("sys").version_info[:3]),
        "enabled_extension": extension_modules[0],
        "legacy_enabled": [],
        "capabilities": capabilities,
        "pillow": PIL.__version__,
        "operator": "goldsrc_toolchain.execute_stage",
        "error_protocol": error_protocol,
    }
    output = os.environ.get("GOLDSRC_EXTENSION_SMOKE_REPORT")
    if output:
        path = Path(output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("GOLDSRC_EXTENSION_SMOKE", json.dumps(report, sort_keys=True))
    return report


if __name__ == "__main__":
    main()
