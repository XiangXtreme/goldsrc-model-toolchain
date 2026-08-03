"""GoldSrc player/NPC baseline and player portrait compatibility checks."""

from __future__ import annotations

from math import isclose
from pathlib import Path
from typing import Any

from .mdl_v10 import inspect_mdl
from .textures import inspect_indexed_bmp


ROLES = {"player", "npc"}


class CompatibilityError(ValueError):
    """Raised when a candidate fails a requested compatibility policy."""

    def __init__(self, report: dict[str, Any]):
        self.report = report
        messages = [item["message"] for item in report.get("issues", []) if item.get("severity") == "error"]
        super().__init__("model compatibility failed: " + "; ".join(messages))


def _issue(code: str, message: str, *, severity: str = "error", **context: Any) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "context": context}


def _bone_graph(inspection: dict[str, Any]) -> list[tuple[str, str | None]]:
    bones = inspection["bones"]
    return [
        (bone["name"], bones[bone["parent"]]["name"] if bone["parent"] >= 0 else None)
        for bone in bones
    ]


def _hitbox_signature(inspection: dict[str, Any]) -> list[dict[str, Any]]:
    bones = inspection["bones"]
    return [
        {
            "group": hitbox["group"],
            "bone": bones[hitbox["bone"]]["name"] if 0 <= hitbox["bone"] < len(bones) else f"<invalid:{hitbox['bone']}>",
            "min": hitbox["min"],
            "max": hitbox["max"],
        }
        for hitbox in inspection["hitboxes"]
    ]


def _events(sequence: dict[str, Any]) -> list[tuple[int, int, str]]:
    return [
        (event["frame"], event["id"], event.get("options", ""))
        for event in sequence.get("events", [])
    ]


def _sequence_metadata_differences(candidate: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    fields = (
        ("activity", lambda value: value.get("activity")),
        ("activity_weight", lambda value: value.get("activity_weight")),
        ("events", _events),
        ("linear_movement", lambda value: value.get("linear_movement")),
    )
    differences = []
    for candidate_sequence, baseline_sequence in zip(candidate["sequences"], baseline["sequences"]):
        for field, getter in fields:
            actual = getter(candidate_sequence)
            expected = getter(baseline_sequence)
            if actual != expected:
                differences.append({
                    "sequence": baseline_sequence["name"],
                    "field": field,
                    "baseline": expected,
                    "candidate": actual,
                })
    return differences


def _compare_blend_counts(candidate: dict[str, Any], baseline: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    for candidate_sequence, baseline_sequence in zip(candidate["sequences"], baseline["sequences"]):
        if candidate_sequence["name"].casefold() != baseline_sequence["name"].casefold():
            continue
        if candidate_sequence.get("blend_count") != baseline_sequence.get("blend_count"):
            issues.append(_issue(
                "compat.sequence_blend_count",
                f"sequence blend count differs: {baseline_sequence['name']}",
                severity="warning",
                sequence=baseline_sequence["name"],
                baseline=baseline_sequence.get("blend_count"),
                candidate=candidate_sequence.get("blend_count"),
                limitation="dual-source and four-source blend authoring is not validated in API version 1",
            ))


def _compare_player(candidate: dict[str, Any], baseline: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    candidate_sequences = candidate["sequences"]
    baseline_sequences = baseline["sequences"]
    candidate_names = [item["name"].casefold() for item in candidate_sequences]
    baseline_names = [item["name"].casefold() for item in baseline_sequences]
    if candidate_names != baseline_names:
        issues.append(_issue(
            "player.sequence_order",
            "player sequence names, count, or order differs from the baseline",
            baseline=[item["name"] for item in baseline_sequences],
            candidate=[item["name"] for item in candidate_sequences],
        ))
    for index, (actual, expected) in enumerate(zip(candidate_sequences, baseline_sequences)):
        if actual["name"].casefold() != expected["name"].casefold():
            continue
        if not isclose(float(actual["fps"]), float(expected["fps"]), abs_tol=0.01):
            issues.append(_issue(
                "player.sequence_fps", f"player sequence FPS differs: {expected['name']}",
                index=index, baseline=expected["fps"], candidate=actual["fps"],
            ))
        if int(actual["frame_count"]) > int(expected["frame_count"]):
            issues.append(_issue(
                "player.sequence_frames", f"player sequence exceeds baseline frame count: {expected['name']}",
                index=index, baseline=expected["frame_count"], candidate=actual["frame_count"],
            ))

    candidate_bones = _bone_graph(candidate)
    baseline_bones = _bone_graph(baseline)
    baseline_prefix = [
        (name.casefold(), parent.casefold() if parent else None)
        for name, parent in baseline_bones
    ]
    candidate_prefix = [
        (name.casefold(), parent.casefold() if parent else None)
        for name, parent in candidate_bones[: len(baseline_bones)]
    ]
    if len(candidate_bones) < len(baseline_bones) or candidate_prefix != baseline_prefix:
        issues.append(_issue(
            "player.bone_prefix",
            "baseline player bones must remain an unchanged ordered prefix",
            baseline=baseline_bones,
            candidate=candidate_bones,
        ))
    elif len(candidate_bones) > len(baseline_bones):
        baseline_names_set = {name.casefold() for name, _parent in baseline_bones}
        baseline_parent_names = {parent.casefold() for _name, parent in baseline_bones if parent}
        baseline_leaves = baseline_names_set - baseline_parent_names
        new_names = {name.casefold() for name, _parent in candidate_bones[len(baseline_bones):]}
        for name, parent in candidate_bones[len(baseline_bones):]:
            parent_key = parent.casefold() if parent else None
            if parent_key not in new_names and parent_key not in baseline_leaves:
                issues.append(_issue(
                    "player.bone_appendage",
                    f"new player bone is not attached below a baseline leaf: {name}",
                    bone=name, parent=parent, baseline_leaves=sorted(baseline_leaves),
                ))

    candidate_hitboxes = _hitbox_signature(candidate)
    baseline_hitboxes = _hitbox_signature(baseline)
    if len(candidate_hitboxes) != len(baseline_hitboxes):
        issues.append(_issue(
            "player.hitboxes", "player hitbox count differs from the baseline",
            baseline=len(baseline_hitboxes), candidate=len(candidate_hitboxes),
        ))
    else:
        for index, (actual, expected) in enumerate(zip(candidate_hitboxes, baseline_hitboxes)):
            names_match = actual["bone"].casefold() == expected["bone"].casefold()
            vectors_match = all(
                isclose(float(left), float(right), abs_tol=0.01)
                for key in ("min", "max")
                for left, right in zip(actual[key], expected[key])
            )
            if actual["group"] != expected["group"] or not names_match or not vectors_match:
                issues.append(_issue(
                    "player.hitboxes", f"player hitbox differs at index {index}",
                    index=index, baseline=expected, candidate=actual,
                ))

    if len(candidate["skin_families"]) > 1:
        issues.append(_issue(
            "player.skin_families", "player models may not contain multiple skin families",
            candidate=len(candidate["skin_families"]),
        ))
    bodyparts = candidate["bodyparts"]
    if len(bodyparts) != 1 or bodyparts[0]["name"].casefold() != "body" or bodyparts[0]["model_count"] not in {1, 2}:
        issues.append(_issue(
            "player.bodypart", "player model must contain only the standard body bodypart with one or two choices",
            candidate=[(item["name"], item["model_count"]) for item in bodyparts],
        ))


def _compare_npc(candidate: dict[str, Any], baseline: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    baseline_names = [item["name"].casefold() for item in baseline["sequences"]]
    candidate_prefix = [item["name"].casefold() for item in candidate["sequences"][: len(baseline_names)]]
    if len(candidate["sequences"]) < len(baseline_names) or candidate_prefix != baseline_names:
        issues.append(_issue(
            "npc.sequence_prefix",
            "baseline NPC sequences must remain an unchanged ordered prefix; new sequences may only be appended",
            baseline=[item["name"] for item in baseline["sequences"]],
            candidate=[item["name"] for item in candidate["sequences"]],
        ))


def compare_model_compatibility(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
    role: str,
) -> dict[str, Any]:
    """Compare parsed MDL inspections without coupling tests to filesystem fixtures."""

    normalized_role = str(role).casefold()
    if normalized_role not in ROLES:
        raise ValueError(f"unsupported compatibility role: {role}")
    issues: list[dict[str, Any]] = []
    if normalized_role == "player":
        _compare_player(candidate, baseline, issues)
    else:
        _compare_npc(candidate, baseline, issues)
    _compare_blend_counts(candidate, baseline, issues)
    differences = _sequence_metadata_differences(candidate, baseline)
    errors = [item for item in issues if item["severity"] == "error"]
    return {
        "status": "pass" if not errors else "fail",
        "role": normalized_role,
        "issues": issues,
        "differences": differences,
        "facts": {
            "baseline_sequences": len(baseline["sequences"]),
            "candidate_sequences": len(candidate["sequences"]),
            "baseline_bones": len(baseline["bones"]),
            "candidate_bones": len(candidate["bones"]),
            "appended_sequences": [
                item["name"] for item in candidate["sequences"][len(baseline["sequences"]):]
            ],
        },
    }


def validate_model_compatibility(candidate_mdl: Path | str, baseline_mdl: Path | str, role: str) -> dict[str, Any]:
    candidate_path = Path(candidate_mdl).expanduser().resolve()
    baseline_path = Path(baseline_mdl).expanduser().resolve()
    report = compare_model_compatibility(inspect_mdl(candidate_path), inspect_mdl(baseline_path), role)
    report["candidate_mdl"] = str(candidate_path)
    report["baseline_mdl"] = str(baseline_path)
    if report["status"] != "pass":
        raise CompatibilityError(report)
    return report


def validate_player_portrait(path: Path | str, remapped: bool = False) -> dict[str, Any]:
    facts = inspect_indexed_bmp(path, require_model_dimensions=False)
    issues = []
    if (facts["width"], facts["height"]) != (164, 200):
        issues.append(_issue(
            "player.portrait_dimensions", "player portrait must be 164x200",
            expected=[164, 200], candidate=[facts["width"], facts["height"]],
        ))
    if not remapped and facts["used_color_count"] > 160:
        issues.append(_issue(
            "player.portrait_colors", "non-remapped player portrait may use at most 160 colors",
            limit=160, candidate=facts["used_color_count"],
        ))
    report = {
        "status": "pass" if not issues else "fail",
        "path": str(Path(path).expanduser().resolve()),
        "remapped": bool(remapped),
        "issues": issues,
        "facts": {key: value for key, value in facts.items() if key != "palette"},
    }
    if issues:
        raise CompatibilityError(report)
    return report
