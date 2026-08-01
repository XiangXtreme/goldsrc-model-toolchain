"""Host-safe GoldSrc model production core."""

from .blender_namespace import assert_exact_asset_namespace, purge_asset_namespace
from .errors import ToolchainError
from .mdl_v10 import inspect_mdl, patch_texture_flags
from .model_contract import ContractError, load_contract, render_qc, validate_contract, write_qc
from .paths import ToolchainPaths, resolve_toolchain
from .smd import read_smd, validate_smd
from .textures import convert_to_indexed_bmp, validate_indexed_bmp

__all__ = [
    "ContractError", "ToolchainError", "ToolchainPaths", "assert_exact_asset_namespace",
    "convert_to_indexed_bmp", "inspect_mdl", "load_contract", "patch_texture_flags",
    "purge_asset_namespace", "read_smd", "render_qc", "resolve_toolchain",
    "validate_contract", "validate_indexed_bmp", "validate_smd", "write_qc",
]
