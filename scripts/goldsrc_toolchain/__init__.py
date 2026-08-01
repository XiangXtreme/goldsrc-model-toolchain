"""Host-side import bridge to the GoldSrc Blender Extension core.

The implementation lives only in the Extension.  Keeping this package name
lets existing pipeline CLIs import the same modules without importing ``bpy``.
"""

from pathlib import Path


_CORE = (
    Path(__file__).resolve().parents[2]
    / "extension"
    / "goldsrc_model_toolchain"
    / "core"
)
if not _CORE.is_dir():
    raise ImportError(f"GoldSrc Extension core is missing: {_CORE}")
__path__ = [str(_CORE)]

from .blender_namespace import assert_exact_asset_namespace, purge_asset_namespace
from .mdl_v10 import inspect_mdl, patch_texture_flags
from .model_contract import ContractError, load_contract, render_qc, validate_contract, write_qc
from .paths import ToolchainPaths, resolve_toolchain
from .smd import read_smd, validate_smd
from .textures import convert_to_indexed_bmp, validate_indexed_bmp

__all__ = [
    "ContractError", "ToolchainPaths", "assert_exact_asset_namespace", "convert_to_indexed_bmp",
    "inspect_mdl", "load_contract", "patch_texture_flags", "purge_asset_namespace", "read_smd",
    "render_qc", "resolve_toolchain", "validate_contract", "validate_indexed_bmp", "validate_smd",
    "write_qc",
]
