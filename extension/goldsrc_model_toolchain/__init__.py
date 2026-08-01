"""Blender 5.2 Extension for the GoldSrc MDL v10 toolchain."""

from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from .api import RuntimeAPI
from .operator import CLASSES


_API = RuntimeAPI()


def _publish_api() -> None:
    bpy.app.driver_namespace["goldsrc_model_toolchain"] = _API


@persistent
def _load_post(_unused) -> None:
    _publish_api()


def register() -> None:
    if bpy.app.version[:2] != (5, 2):
        raise RuntimeError(f"goldsrc_model_toolchain requires Blender 5.2.x, got {bpy.app.version_string}")
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
    _publish_api()


def unregister() -> None:
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if bpy.app.driver_namespace.get("goldsrc_model_toolchain") is _API:
        del bpy.app.driver_namespace["goldsrc_model_toolchain"]
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
