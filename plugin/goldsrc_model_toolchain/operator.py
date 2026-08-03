"""Background-only stage operator.  No menus or panels are registered."""

from __future__ import annotations

import json
from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty

from .core.errors import ToolchainError
from .core.paths import resolve_artifact_root
from .core.stages import PUBLIC_STAGES, execute_stage


def _report_path(artifacts_dir: str, report_path: str) -> Path:
    try:
        root = resolve_artifact_root(artifacts_dir)
    except ValueError as exc:
        raise ToolchainError(
            "OPERATOR", "artifacts.skill_root", str(exc), {"artifacts_dir": str(artifacts_dir)},
        ) from exc
    root.mkdir(parents=True, exist_ok=True)
    path = Path(report_path).expanduser()
    path = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ToolchainError(
            "OPERATOR", "report.escape", "Stage report must stay inside artifacts_dir",
            {"artifacts_dir": str(root), "report_path": str(path)},
        ) from exc
    return path


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


class GOLDSRC_TOOLCHAIN_OT_execute_stage(bpy.types.Operator):
    bl_idname = "goldsrc_toolchain.execute_stage"
    bl_label = "Execute GoldSrc Toolchain Stage"
    bl_options = {"INTERNAL"}

    stage: EnumProperty(items=[(stage, stage.title(), "") for stage in PUBLIC_STAGES])
    contract_path: StringProperty(subtype="FILE_PATH")
    artifacts_dir: StringProperty(subtype="DIR_PATH")
    report_path: StringProperty(subtype="FILE_PATH")

    def execute(self, _context):
        path = None
        try:
            path = _report_path(self.artifacts_dir, self.report_path)
            result = execute_stage(self.stage, self.contract_path, self.artifacts_dir)
            _write(path, result)
            return {"FINISHED"}
        except ToolchainError as exc:
            failure = {"status": "fail", "phase": exc.phase, "error": exc.as_dict(), "issues": [exc.as_dict()]}
        except Exception as exc:
            failure = {
                "status": "fail",
                "phase": self.stage,
                "error": {
                    "phase": self.stage, "code": "operator.unhandled", "message": str(exc),
                    "details": {"type": type(exc).__name__},
                },
                "issues": [],
            }
        if path is None:
            try:
                path = _report_path(self.artifacts_dir, Path(self.report_path).name or "stage_failure.json")
            except Exception:
                path = None
        if path is not None:
            _write(path, failure)
        self.report({"ERROR"}, failure["error"]["message"])
        return {"CANCELLED"}


CLASSES = (GOLDSRC_TOOLCHAIN_OT_execute_stage,)
