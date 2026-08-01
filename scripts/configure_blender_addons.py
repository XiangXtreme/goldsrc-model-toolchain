"""Enable one GoldSrc Extension and leave official Blender MCP externally owned."""

from __future__ import annotations

import addon_utils
import bpy
import json
import os
from pathlib import Path


if bpy.app.version[:2] != (5, 2):
    raise RuntimeError(f"Expected Blender 5.2, got {bpy.app.version_string}")

disabled = []
for module_name in ("io_scene_valvesource", "SourceIO"):
    if module_name in bpy.context.preferences.addons:
        addon_utils.disable(module_name, default_set=True)
        disabled.append(module_name)

mcp = addon_utils.enable("addon", default_set=True, persistent=True)
if mcp is None:
    raise RuntimeError("Official ahujasid Blender MCP add-on is not installed as module 'addon'")

extension_modules = [
    module.__name__ for module in addon_utils.modules(refresh=True)
    if module.__name__.endswith(".goldsrc_model_toolchain")
]
if len(extension_modules) != 1:
    raise RuntimeError(f"Expected one installed GoldSrc Extension module, found {extension_modules}")
extension = addon_utils.enable(extension_modules[0], default_set=True, persistent=True)
if extension is None:
    raise RuntimeError(f"Could not enable GoldSrc Extension: {extension_modules[0]}")

bpy.ops.wm.save_userpref()
report = {
    "status": "pass",
    "blender": bpy.app.version_string,
    "enabled": {
        "goldsrc_model_toolchain": extension_modules[0],
        "blender_mcp": "addon",
    },
    "disabled_legacy": disabled,
}
report_path = os.environ.get("GOLDSRC_BOOTSTRAP_REPORT")
if report_path:
    Path(report_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print("GOLDSRC_EXTENSION_CONFIGURED", json.dumps(report, sort_keys=True))
