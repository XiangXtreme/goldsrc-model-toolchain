"""Resolve Blender, Steam SDK, compiler, and fixture source paths."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXTENSION_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_MODERN_STUDIOMDL = EXTENSION_ROOT / "bin" / "windows-x64" / "studiomdl.exe"


def enclosing_skill_root(path: Path | str) -> Path | None:
    """Return the nearest Skill root containing *path*, if one exists."""
    resolved = Path(path).expanduser().resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / "SKILL.md").is_file():
            return candidate
    return None


def ensure_outside_skill_tree(path: Path | str, *, label: str = "Path") -> Path:
    """Resolve a writable path and reject locations inside any Skill tree."""
    resolved = Path(path).expanduser().resolve()
    skill_root = enclosing_skill_root(resolved)
    if skill_root is not None:
        raise ValueError(f"{label} must be outside Skill directory: {skill_root}")
    return resolved


def resolve_artifact_root(path: Path | str) -> Path:
    return ensure_outside_skill_tree(path, label="Artifact directory")


def _unique(paths: Iterable[Path | None]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        if path is None:
            continue
        resolved = path.expanduser()
        key = str(resolved).casefold()
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def _existing_file(explicit: Path | str | None, env_name: str, candidates: Iterable[Path]) -> Path | None:
    values: list[Path | None] = []
    if explicit:
        values.append(Path(explicit))
    if os.environ.get(env_name):
        values.append(Path(os.environ[env_name]))
    values.extend(candidates)
    return next((path.resolve() for path in _unique(values) if path.is_file()), None)


def _existing_dir(explicit: Path | str | None, env_name: str, candidates: Iterable[Path]) -> Path | None:
    values: list[Path | None] = []
    if explicit:
        values.append(Path(explicit))
    if os.environ.get(env_name):
        values.append(Path(os.environ[env_name]))
    values.extend(candidates)
    return next((path.resolve() for path in _unique(values) if path.is_dir()), None)


def _registry_steam_roots() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    roots: list[Path] = []
    probes = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam", "InstallPath"),
    )
    for hive, key_name, value_name in probes:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        roots.append(Path(value))
    return roots


def _vdf_tokens(text: str) -> list[str]:
    pattern = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])')
    tokens = []
    for match in pattern.finditer(text):
        token = match.group(1) if match.group(1) is not None else match.group(2)
        tokens.append(token.replace(r"\\", "\\").replace(r'\"', '"'))
    return tokens


def _parse_vdf(text: str) -> dict:
    tokens = _vdf_tokens(text)

    def parse_object(index: int, nested: bool) -> tuple[dict, int]:
        result: dict = {}
        while index < len(tokens):
            token = tokens[index]
            if token == "}":
                if not nested:
                    raise ValueError("unexpected VDF closing brace")
                return result, index + 1
            if token == "{":
                raise ValueError("unexpected VDF opening brace")
            key = token
            index += 1
            if index >= len(tokens):
                raise ValueError(f"missing VDF value for {key}")
            if tokens[index] == "{":
                value, index = parse_object(index + 1, True)
            else:
                value = tokens[index]
                index += 1
            result[key] = value
        if nested:
            raise ValueError("unterminated VDF object")
        return result, index

    parsed, _ = parse_object(0, False)
    return parsed


def _collect_named_values(value, wanted: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() == wanted.casefold() and isinstance(child, str):
                found.append(child)
            found.extend(_collect_named_values(child, wanted))
    return found


def steam_library_roots() -> list[Path]:
    env_roots = [
        Path(value)
        for name in ("GOLDSRC_STEAM_ROOT", "STEAM_PATH", "STEAM_INSTALL_PATH")
        if (value := os.environ.get(name))
    ]
    common = [
        Path(os.environ[name]) / "Steam"
        for name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "ProgramW6432")
        if os.environ.get(name)
    ]
    drive_roots: list[Path] = []
    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            drive_roots.extend((drive / "steam", drive / "Steam", drive / "SteamLibrary"))
    steam_roots = _unique([*env_roots, *_registry_steam_roots(), *common, *drive_roots])
    libraries = list(steam_roots)
    for root in steam_roots:
        manifest = root / "steamapps" / "libraryfolders.vdf"
        if not manifest.is_file():
            continue
        try:
            parsed = _parse_vdf(manifest.read_text(encoding="utf-8-sig", errors="replace"))
        except (OSError, ValueError):
            continue
        libraries.extend(Path(value) for value in _collect_named_values(parsed, "path"))
    return [path.resolve() for path in _unique(libraries) if path.is_dir()]


def _common_game_paths(relative: str) -> list[Path]:
    return [root / "steamapps" / "common" / relative for root in steam_library_roots()]


def _blender_candidates() -> list[Path]:
    candidates: list[Path] = []
    for name in ("ProgramW6432", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        if os.environ.get(name):
            candidates.append(Path(os.environ[name]) / "Blender Foundation" / "Blender 5.2" / "blender.exe")
    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            candidates.append(Path(f"{letter}:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe"))
    return candidates


@dataclass(frozen=True)
class ToolchainPaths:
    blender: Path | None
    sven_studiomdl: Path | None
    blender_mcp_addon: Path | None
    extension_root: Path
    player_sdk: Path | None
    player_reference_smd: Path | None
    official_player_mdl: Path | None
    codex_config: Path | None
    steam_libraries: tuple[Path, ...]

    def as_dict(self) -> dict:
        return {
            key: [str(item) for item in value]
            if isinstance(value, tuple)
            else str(value) if value is not None else None
            for key, value in self.__dict__.items()
        }


def resolve_toolchain(
    *,
    blender: Path | str | None = None,
    sven_studiomdl: Path | str | None = None,
    player_sdk: Path | str | None = None,
    player_reference_smd: Path | str | None = None,
    official_player_mdl: Path | str | None = None,
) -> ToolchainPaths:
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    addon_root = appdata / "Blender Foundation" / "Blender" / "5.2" / "scripts" / "addons"
    libraries = tuple(steam_library_roots())
    sven_candidates = [
        BUNDLED_MODERN_STUDIOMDL,
        *_common_game_paths(r"Sven Co-op SDK\modelling\studiomdl.exe"),
    ]
    player_sdk_candidates = _common_game_paths(r"Half-Life SDK\Player Models\player")
    player_reference_candidates = _common_game_paths(
        r"Half-Life SDK\Player Models\DMatch\Highcount\Barney\barney_reference.smd"
    )
    official_player_candidates = _common_game_paths(r"Half-Life\valve\models\player\barney\barney.mdl")
    return ToolchainPaths(
        blender=_existing_file(blender, "GOLDSRC_BLENDER", _blender_candidates()),
        sven_studiomdl=_existing_file(sven_studiomdl, "GOLDSRC_SVEN_STUDIOMDL", sven_candidates),
        blender_mcp_addon=_existing_file(
            None,
            "GOLDSRC_BLENDER_MCP_ADDON",
            [addon_root / "addon.py", addon_root / "blender_mcp" / "addon.py"],
        ),
        extension_root=EXTENSION_ROOT,
        player_sdk=_existing_dir(player_sdk, "GOLDSRC_PLAYER_SDK", player_sdk_candidates),
        player_reference_smd=_existing_file(
            player_reference_smd, "GOLDSRC_PLAYER_REFERENCE_SMD", player_reference_candidates
        ),
        official_player_mdl=_existing_file(
            official_player_mdl, "GOLDSRC_OFFICIAL_PLAYER_MDL", official_player_candidates
        ),
        codex_config=_existing_file(None, "GOLDSRC_CODEX_CONFIG", [Path.home() / ".codex" / "config.toml"]),
        steam_libraries=libraries,
    )
