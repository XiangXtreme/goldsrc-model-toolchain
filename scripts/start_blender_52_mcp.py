"""Start an externally installed official Blender MCP with the GoldSrc Extension."""

from __future__ import annotations

import addon_utils
import bpy


if bpy.app.version[:2] != (5, 2):
    raise RuntimeError(f"This launcher requires Blender 5.2, got {bpy.app.version_string}")

for legacy in ("io_scene_valvesource", "SourceIO"):
    if legacy in bpy.context.preferences.addons:
        addon_utils.disable(legacy, default_set=True)

mcp = addon_utils.enable("addon", default_set=True, persistent=True)
if mcp is None:
    raise RuntimeError("Official ahujasid Blender MCP must be installed externally as module 'addon'")
extensions = [
    module.__name__ for module in addon_utils.modules(refresh=True)
    if module.__name__.endswith(".goldsrc_model_toolchain")
]
if len(extensions) != 1 or addon_utils.enable(extensions[0], default_set=True, persistent=True) is None:
    raise RuntimeError(f"Expected one installed GoldSrc Extension, found {extensions}")
bpy.ops.wm.save_userpref()

scene = bpy.context.scene
desired_port = 9876
server = getattr(bpy.types, "blendermcp_server", None)
if server is not None and server.running and server.port != desired_port:
    result = bpy.ops.blendermcp.stop_server()
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender MCP stop-before-rebind failed: {result}")
scene.blendermcp_port = desired_port
scene.blendermcp_auto_start_server = True
scene.blendermcp_use_polyhaven = False
scene.blendermcp_use_hyper3d = False
scene.blendermcp_use_sketchfab = False
if not scene.blendermcp_server_running:
    result = bpy.ops.blendermcp.start_server()
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender MCP start failed: {result}")
server = getattr(bpy.types, "blendermcp_server", None)
if server is None or not server.running or server.port != desired_port:
    raise RuntimeError("Blender MCP server did not bind the requested port")
print("BLENDER_MCP_READY", bpy.app.version_string, server.port)
