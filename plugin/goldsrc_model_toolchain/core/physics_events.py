"""Validation and evaluation for pre-baked multi-stage rigid-body events."""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping


TRIGGER_TYPES = {"frame", "contact"}
RESPONSE_TYPES = {"deflect", "reverse", "separate"}
DEFAULT_SIMULATION = {
    "fps": 60.0,
    "source_fps": 60.0,
    "export_fps": None,
    "sequence": None,
    "max_frame": 480,
    "sample_step": 1,
    "contact_margin": 0.02,
    "penetration_tolerance": 0.25,
    "receiver_margin": 0.05,
    "translation_epsilon": 0.002,
    "rotation_epsilon": 0.002,
    "min_response_speed": None,
    "separation_epsilon": 0.002,
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _vec3(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_is_number(item) for item in value)


def _frame_range(value: Any, label: str, max_frame: int, errors: list[str]) -> None:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        errors.append(f"{label} must be [start, end] integer frames")
        return
    if value[0] < 0 or value[0] > value[1] or value[1] > max_frame:
        errors.append(f"{label} must stay inside 0..{max_frame}")


def _validate_contact_pairs(
    value: Mapping[str, Any],
    label: str,
    known_objects: set[str] | None,
    errors: list[str],
) -> None:
    has_pair = "pair" in value
    has_pairs = "pairs" in value
    if has_pair == has_pairs:
        errors.append(f"{label} must declare exactly one of pair or pairs")
        return
    pairs = [value.get("pair")] if has_pair else value.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        errors.append(f"{label}.pairs must be a non-empty list of object pairs")
        return
    known = {item.casefold() for item in known_objects} if known_objects is not None else None
    seen: set[tuple[str, str]] = set()
    for index, pair in enumerate(pairs):
        pair_label = f"{label}.pair" if has_pair else f"{label}.pairs[{index}]"
        if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(item, str) or not item.strip() for item in pair):
            errors.append(f"{pair_label} must contain two object names")
            continue
        pair_key = (pair[0].casefold(), pair[1].casefold())
        if pair_key[0] == pair_key[1]:
            errors.append(f"{pair_label} must contain two distinct object names")
        if pair_key in seen:
            errors.append(f"{label} contains duplicate object pair: {pair}")
        seen.add(pair_key)
        if known is not None:
            for object_name in pair:
                if object_name.casefold() not in known:
                    errors.append(f"{pair_label} references unknown object: {object_name}")


def _contact_pairs(value: Mapping[str, Any]) -> list[list[str]]:
    if "pair" in value:
        return [list(value["pair"])]
    return [list(pair) for pair in value.get("pairs", [])]


def normalize_physics(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with stable defaults while preserving user-defined checks."""

    physics = copy.deepcopy(dict(value))
    simulation = physics.setdefault("simulation", {})
    if isinstance(simulation, dict):
        for key, default in DEFAULT_SIMULATION.items():
            simulation.setdefault(key, default)
        simulation.setdefault("settlement", {})
    physics.setdefault("stages", [])
    physics.setdefault("interactions", [])
    return physics


def validate_physics_definition(
    value: Any,
    *,
    known_objects: set[str] | None = None,
) -> list[str]:
    """Validate the optional contract physics block without requiring Blender."""

    if value is None:
        return []
    if not isinstance(value, dict):
        return ["physics must be an object"]
    physics = normalize_physics(value)
    errors: list[str] = []
    if physics.get("mode") != "baked_event_chain":
        errors.append("physics.mode must be baked_event_chain")

    simulation = physics.get("simulation")
    if not isinstance(simulation, dict):
        errors.append("physics.simulation must be an object")
        simulation = {}
    max_frame = simulation.get("max_frame", DEFAULT_SIMULATION["max_frame"])
    if not isinstance(max_frame, int) or isinstance(max_frame, bool) or max_frame <= 0:
        errors.append("physics.simulation.max_frame must be a positive integer")
        max_frame = DEFAULT_SIMULATION["max_frame"]
    fps = simulation.get("fps")
    if not _is_number(fps) or not 0 < float(fps) <= 240:
        errors.append("physics.simulation.fps must be within 0..240")
    sample_step = simulation.get("sample_step")
    if not isinstance(sample_step, int) or isinstance(sample_step, bool) or not 1 <= sample_step <= 16:
        errors.append("physics.simulation.sample_step must be an integer within 1..16")
    for key in ("fps", "source_fps"):
        if not _is_number(simulation.get(key)) or not 0 < float(simulation[key]) <= 240:
            errors.append(f"physics.simulation.{key} must be within 0..240")
    if simulation.get("export_fps") is not None and (not _is_number(simulation.get("export_fps")) or not 0 < float(simulation["export_fps"]) <= 240):
        errors.append("physics.simulation.export_fps must be within 0..240 when provided")
    if simulation.get("sequence") is not None and (not isinstance(simulation.get("sequence"), str) or not simulation["sequence"].strip()):
        errors.append("physics.simulation.sequence must be a non-empty name when provided")
    source_fps_value = float(simulation.get("source_fps")) if _is_number(simulation.get("source_fps")) else float(DEFAULT_SIMULATION["source_fps"])
    sample_step_value = int(simulation.get("sample_step")) if isinstance(simulation.get("sample_step"), int) and not isinstance(simulation.get("sample_step"), bool) and simulation.get("sample_step") > 0 else 1
    if source_fps_value / sample_step_value > 240:
        errors.append("physics.simulation source_fps/sample_step must be within 0..240")
    if simulation.get("export_fps") is not None:
        expected = source_fps_value / sample_step_value
        if _is_number(simulation.get("export_fps")) and abs(float(simulation["export_fps"]) - expected) > 1e-6:
            errors.append("physics.simulation.export_fps must equal source_fps/sample_step to preserve duration")
    for key in (
        "contact_margin",
        "penetration_tolerance",
        "receiver_margin",
        "translation_epsilon",
        "rotation_epsilon",
        "separation_epsilon",
    ):
        if not _is_number(simulation.get(key)) or float(simulation[key]) < 0:
            errors.append(f"physics.simulation.{key} must be non-negative")
    if simulation.get("min_response_speed") is not None and (
        not _is_number(simulation["min_response_speed"]) or float(simulation["min_response_speed"]) < 0
    ):
        errors.append("physics.simulation.min_response_speed must be non-negative when provided")
    settlement = simulation.get("settlement", {})
    if not isinstance(settlement, dict):
        errors.append("physics.simulation.settlement must be an object")
    else:
        for key in ("activity_after_frame", "consecutive_frames", "hold_frames"):
            if not isinstance(settlement.get(key, 0), int) or isinstance(settlement.get(key, 0), bool) or settlement.get(key, 0) < 0:
                errors.append(f"physics.simulation.settlement.{key} must be a non-negative integer")
        if settlement.get("consecutive_frames", 2) < 2:
            errors.append("physics.simulation.settlement.consecutive_frames must be at least 2")
    receiver = simulation.get("receiver_bounds")
    if receiver is not None:
        if not isinstance(receiver, dict) or not _vec3(receiver.get("min")) or not _vec3(receiver.get("max")):
            errors.append("physics.simulation.receiver_bounds requires min and max vec3")
        elif any(receiver["min"][axis] > receiver["max"][axis] for axis in range(3)):
            errors.append("physics.simulation.receiver_bounds.min must not exceed max")

    stages = physics.get("stages")
    if not isinstance(stages, list):
        errors.append("physics.stages must be a list")
        stages = []
    stage_names: set[str] = set()
    release_names: set[str] = set()
    stage_dependencies: dict[str, list[str]] = {}
    for index, stage in enumerate(stages):
        label = f"physics.stages[{index}]"
        if not isinstance(stage, dict):
            errors.append(f"{label} must be an object")
            continue
        name = stage.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.name is required")
            continue
        key = name.casefold()
        if key in stage_names:
            errors.append(f"duplicate physics stage name: {name}")
        stage_names.add(key)
        dependencies = stage.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(item, str) or not item.strip() for item in dependencies):
            errors.append(f"{label}.depends_on must be a list of names")
            dependencies = []
        stage_dependencies[key] = [item.casefold() for item in dependencies]
        release = stage.get("release", [])
        if not isinstance(release, list) or any(not isinstance(item, str) or not item.strip() for item in release):
            errors.append(f"{label}.release must be a list of object names")
            release = []
        break_constraints = stage.get("break_constraints", [])
        if not isinstance(break_constraints, list) or any(not isinstance(item, str) or not item.strip() for item in break_constraints):
            errors.append(f"{label}.break_constraints must be a list of constraint names")
            break_constraints = []
        participants = stage.get("participants", [])
        if not release and not break_constraints and not participants:
            errors.append(f"{label} must declare release objects, break_constraints, or participants")
        for object_name in release:
            object_key = object_name.casefold()
            if object_key in release_names:
                errors.append(f"physics object is released by more than one stage: {object_name}")
            release_names.add(object_key)
            if known_objects is not None and object_key not in {item.casefold() for item in known_objects}:
                errors.append(f"{label}.release references unknown object: {object_name}")
        trigger = stage.get("trigger")
        if not isinstance(trigger, dict) or trigger.get("type") not in TRIGGER_TYPES:
            errors.append(f"{label}.trigger.type must be frame or contact")
        else:
            trigger_type = trigger["type"]
            if trigger_type == "frame":
                frame = trigger.get("frame")
                if not isinstance(frame, int) or isinstance(frame, bool) or not 0 <= frame <= max_frame:
                    errors.append(f"{label}.trigger.frame must stay inside 0..{max_frame}")
            else:
                _validate_contact_pairs(trigger, f"{label}.trigger", known_objects, errors)
                offset = trigger.get("offset_frames", 0)
                if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                    errors.append(f"{label}.trigger.offset_frames must be a non-negative integer")
                if "window" in trigger:
                    _frame_range(trigger["window"], f"{label}.trigger.window", max_frame, errors)
        for optional in ("expected_motion_window",):
            if optional in stage:
                _frame_range(stage[optional], f"{label}.{optional}", max_frame, errors)
        if "expected_break_window" in stage:
            _frame_range(stage["expected_break_window"], f"{label}.expected_break_window", max_frame, errors)
        for optional in ("must_be_still_before", "participants"):
            values = stage.get(optional, [])
            if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
                errors.append(f"{label}.{optional} must be a list of object names")

    for stage_name, dependencies in stage_dependencies.items():
        for dependency in dependencies:
            if dependency not in stage_names:
                errors.append(f"physics stage {stage_name} depends on missing stage {dependency}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            errors.append(f"physics stage dependency cycle at {name}")
            return
        if name in visited:
            return
        visiting.add(name)
        for dependency in stage_dependencies.get(name, []):
            if dependency in stage_dependencies:
                visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in stage_dependencies:
        visit(name)

    interactions = physics.get("interactions")
    if not isinstance(interactions, list):
        errors.append("physics.interactions must be a list")
        interactions = []
    interaction_names: set[str] = set()
    for index, interaction in enumerate(interactions):
        label = f"physics.interactions[{index}]"
        if not isinstance(interaction, dict):
            errors.append(f"{label} must be an object")
            continue
        name = interaction.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.name is required")
        elif name.casefold() in interaction_names:
            errors.append(f"duplicate physics interaction name: {name}")
        elif name:
            interaction_names.add(name.casefold())
        _validate_contact_pairs(interaction, label, known_objects, errors)
        if "window" not in interaction:
            errors.append(f"{label}.window is required")
        else:
            _frame_range(interaction["window"], f"{label}.window", max_frame, errors)
        response = interaction.get("response")
        if response not in RESPONSE_TYPES:
            errors.append(f"{label}.response must be deflect, reverse, or separate")
        if "min_direction_change_deg" in interaction:
            angle = interaction["min_direction_change_deg"]
            if not _is_number(angle) or not 0 <= float(angle) <= 180:
                errors.append(f"{label}.min_direction_change_deg must be within 0..180")
            elif response in {"deflect", "reverse"} and float(angle) <= 0:
                errors.append(f"{label}.min_direction_change_deg must be positive when provided for {response}")
        if "min_speed" in interaction and (not _is_number(interaction["min_speed"]) or float(interaction["min_speed"]) <= 0):
            errors.append(f"{label}.min_speed must be positive when provided")
        if "response_window_frames" in interaction:
            response_window = interaction["response_window_frames"]
            if not isinstance(response_window, int) or isinstance(response_window, bool) or response_window < 1:
                errors.append(f"{label}.response_window_frames must be a positive integer")
    return errors


def _dot(a: list[float], b: list[float]) -> float:
    return sum(a[index] * b[index] for index in range(3))


def _sub(a: list[float], b: list[float]) -> list[float]:
    return [a[index] - b[index] for index in range(3)]


def _add(a: list[float], b: list[float]) -> list[float]:
    return [a[index] + b[index] for index in range(3)]


def _scale(value: list[float], factor: float) -> list[float]:
    return [component * factor for component in value]


def _length(value: list[float]) -> float:
    return math.sqrt(_dot(value, value))


def _normalize(value: list[float]) -> list[float]:
    length = _length(value)
    return _scale(value, 1.0 / length) if length > 1e-9 else [0.0, 0.0, 0.0]


def _cross(first: list[float], second: list[float]) -> list[float]:
    return [
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    ]


def _convex_axes(convex: Mapping[str, Any]) -> tuple[list[list[float]], list[list[float]]]:
    vertices = convex.get("vertices", [])
    face_axes: list[list[float]] = []
    edge_axes: list[list[float]] = []
    seen_faces: set[tuple[float, float, float]] = set()
    seen_edges: set[tuple[float, float, float]] = set()

    def append_unique(target, seen, vector):
        if not _vec3(vector) or _length(vector) <= 1e-9:
            return
        axis = _normalize(vector)
        first_nonzero = next((component for component in axis if abs(component) > 1e-8), 1.0)
        if first_nonzero < 0.0:
            axis = _scale(axis, -1.0)
        key = tuple(round(component, 5) for component in axis)
        if key not in seen:
            seen.add(key)
            target.append(axis)

    for face in convex.get("faces", []):
        if not isinstance(face, list) or len(face) < 3 or any(not isinstance(index, int) or not 0 <= index < len(vertices) for index in face):
            continue
        points = [list(vertices[index]) for index in face]
        append_unique(face_axes, seen_faces, _cross(_sub(points[1], points[0]), _sub(points[2], points[0])))
        for index, point in enumerate(points):
            append_unique(edge_axes, seen_edges, _sub(points[(index + 1) % len(points)], point))
    return face_axes, edge_axes


def _convex_overlap(first: Mapping[str, Any], second: Mapping[str, Any], margin: float) -> bool:
    first_convex = first.get("convex")
    second_convex = second.get("convex")
    if not isinstance(first_convex, Mapping) or not isinstance(second_convex, Mapping):
        return True
    first_vertices = first_convex.get("vertices", [])
    second_vertices = second_convex.get("vertices", [])
    if not first_vertices or not second_vertices:
        return True
    first_faces, first_edges = _convex_axes(first_convex)
    second_faces, second_edges = _convex_axes(second_convex)
    axes = [*first_faces, *second_faces]
    seen = {tuple(round(component, 5) for component in axis) for axis in axes}
    for first_edge in first_edges:
        for second_edge in second_edges:
            axis = _cross(first_edge, second_edge)
            if _length(axis) <= 1e-9:
                continue
            axis = _normalize(axis)
            first_nonzero = next((component for component in axis if abs(component) > 1e-8), 1.0)
            if first_nonzero < 0.0:
                axis = _scale(axis, -1.0)
            key = tuple(round(component, 5) for component in axis)
            if key not in seen:
                seen.add(key)
                axes.append(axis)
    for axis in axes:
        first_projection = [_dot(axis, list(point)) for point in first_vertices]
        second_projection = [_dot(axis, list(point)) for point in second_vertices]
        if max(first_projection) + margin < min(second_projection) or max(second_projection) + margin < min(first_projection):
            return False
    return True


def _sample_center(sample: Mapping[str, Any]) -> list[float]:
    value = sample.get("center", sample.get("location"))
    return list(value) if _vec3(value) else [0.0, 0.0, 0.0]


def _obb_overlap(first: Mapping[str, Any], second: Mapping[str, Any], margin: float) -> bool:
    first_obb = first.get("obb", first)
    second_obb = second.get("obb", second)
    axes_a = first_obb.get("axes")
    axes_b = second_obb.get("axes")
    half_a = first_obb.get("half_sizes")
    half_b = second_obb.get("half_sizes")
    if not isinstance(axes_a, list) or not isinstance(axes_b, list) or not isinstance(half_a, list) or not isinstance(half_b, list):
        return False
    center_delta = _sub(_sample_center(first), _sample_center(second))
    axes = [list(axis) for axis in axes_a] + [list(axis) for axis in axes_b]
    for axis in axes:
        if not _vec3(axis) or _length(axis) <= 1e-9:
            continue
        axis = _normalize(axis)
        projection_a = sum(float(half_a[index]) * abs(_dot(axis, axes_a[index])) for index in range(3))
        projection_b = sum(float(half_b[index]) * abs(_dot(axis, axes_b[index])) for index in range(3))
        if abs(_dot(axis, center_delta)) > projection_a + projection_b + margin:
            return False
    for axis_a in axes_a:
        for axis_b in axes_b:
            cross = [
                axis_a[1] * axis_b[2] - axis_a[2] * axis_b[1],
                axis_a[2] * axis_b[0] - axis_a[0] * axis_b[2],
                axis_a[0] * axis_b[1] - axis_a[1] * axis_b[0],
            ]
            if _length(cross) <= 1e-9:
                continue
            axis = _normalize(cross)
            projection_a = sum(float(half_a[index]) * abs(_dot(axis, axes_a[index])) for index in range(3))
            projection_b = sum(float(half_b[index]) * abs(_dot(axis, axes_b[index])) for index in range(3))
            if abs(_dot(axis, center_delta)) > projection_a + projection_b + margin:
                return False
    return _convex_overlap(first, second, margin)


def _contact_event(
    samples: Mapping[int, Mapping[str, Mapping[str, Any]]],
    pairs: list[list[str]],
    window: list[int],
    margin: float,
) -> tuple[int | None, list[str] | None]:
    for frame in sorted(frame for frame in samples if window[0] <= frame <= window[1]):
        current = samples[frame]
        for pair in pairs:
            first, second = pair
            if first in current and second in current and _obb_overlap(current[first], current[second], margin):
                return frame, list(pair)
    return None, None


def _contact_frame(samples: Mapping[int, Mapping[str, Mapping[str, Any]]], pair: list[str], window: list[int], margin: float) -> int | None:
    frame, _ = _contact_event(samples, [pair], window, margin)
    return frame


def _velocity(samples: Mapping[int, Mapping[str, Mapping[str, Any]]], frame: int, name: str) -> list[float]:
    previous = samples.get(frame - 1, {}).get(name)
    current = samples.get(frame, {}).get(name)
    if previous is None or current is None:
        return [0.0, 0.0, 0.0]
    return _sub(_sample_center(current), _sample_center(previous))


def _direction_change(before: list[float], after: list[float]) -> float:
    before_length = _length(before)
    after_length = _length(after)
    if before_length <= 1e-9 or after_length <= 1e-9:
        return 0.0
    cosine = max(-1.0, min(1.0, _dot(before, after) / (before_length * after_length)))
    return math.degrees(math.acos(cosine))


def evaluate_event_chain(
    physics: Mapping[str, Any],
    samples: Mapping[int, Mapping[str, Mapping[str, Any]]],
    final_report: Mapping[str, Any] | None = None,
    constraint_breaks: Mapping[str, int | None] | None = None,
) -> dict[str, Any]:
    """Evaluate stage ordering and interaction responses from captured transforms."""

    normalized = normalize_physics(physics)
    errors = validate_physics_definition(normalized, known_objects={name for frame in samples.values() for name in frame})
    simulation = normalized.get("simulation", {})
    max_frame = int(simulation.get("max_frame", DEFAULT_SIMULATION["max_frame"]))
    if errors:
        return {"status": "fail", "issues": [{"code": "physics.contract", "message": item} for item in errors], "stages": [], "interactions": []}
    first_motion: dict[str, int | None] = {}
    translation_epsilon = float(simulation.get("translation_epsilon", DEFAULT_SIMULATION["translation_epsilon"]))
    rotation_epsilon = float(simulation.get("rotation_epsilon", DEFAULT_SIMULATION["rotation_epsilon"]))
    names = sorted({name for frame in samples.values() for name in frame})
    for name in names:
        first_motion[name] = None
        ordered_frames = sorted(samples)
        for frame in ordered_frames[1:]:
            sample = samples[frame].get(name)
            previous = samples.get(frame - 1, {}).get(name)
            if not sample or not previous:
                continue
            translation = _length(_sub(_sample_center(sample), _sample_center(previous)))
            rotation = float(sample.get("rotation_delta", 0.0))
            if translation > translation_epsilon or rotation > rotation_epsilon:
                first_motion[name] = frame
                break
    issues: list[dict[str, Any]] = []
    stage_reports: list[dict[str, Any]] = []
    resolved: dict[str, int] = {}
    pending_stages = list(normalized.get("stages", []))
    observed_breaks = {str(name).casefold(): frame for name, frame in (constraint_breaks or {}).items()}
    processed_stages: set[str] = set()
    while pending_stages:
        ready = [
            stage for stage in pending_stages
            if all(dependency.casefold() in processed_stages for dependency in stage.get("depends_on", []))
        ]
        if not ready:
            issues.append({"code": "physics.stage_order", "message": "stage dependencies could not be resolved"})
            break
        stage = ready[0]
        pending_stages.remove(stage)
        name = stage["name"]
        trigger = stage["trigger"]
        if trigger["type"] == "frame":
            resolved_frame = int(trigger["frame"])
            contact_frame = None
        else:
            window = trigger.get("window", [0, max_frame])
            candidate_pairs = _contact_pairs(trigger)
            contact_frame, resolved_pair = _contact_event(samples, candidate_pairs, window, float(simulation.get("contact_margin", DEFAULT_SIMULATION["contact_margin"])))
            if contact_frame is None:
                issues.append({"code": "physics.contact_missing", "stage": name, "message": "contact trigger was not observed"})
                stage_reports.append({
                    "name": name,
                    "depends_on": list(stage.get("depends_on", [])),
                    "release": list(stage.get("release", [])),
                    "resolved_frame": None,
                    "contact_frame": None,
                    "resolved_pair": None,
                    "candidate_pairs": candidate_pairs,
                    "trigger_window": list(window),
                    "first_motion": {item: first_motion.get(item) for item in stage.get("release", [])},
                    "constraint_breaks": {item: observed_breaks.get(item.casefold()) for item in stage.get("break_constraints", [])},
                })
                continue
            resolved_frame = contact_frame + int(trigger.get("offset_frames", 0))
        for dependency in stage.get("depends_on", []):
            dependency_frame = resolved.get(dependency.casefold())
            if dependency_frame is not None and resolved_frame < dependency_frame:
                issues.append({"code": "physics.stage_order", "stage": name, "message": f"stage resolves before dependency {dependency}"})
        for object_name in stage.get("must_be_still_before", []):
            motion_frame = first_motion.get(object_name)
            if motion_frame is not None and motion_frame < resolved_frame:
                issues.append({"code": "physics.early_motion", "stage": name, "object": object_name, "message": f"object moved at frame {motion_frame} before release {resolved_frame}"})
        for object_name in stage.get("release", []):
            if object_name not in first_motion:
                issues.append({"code": "physics.object_missing", "stage": name, "object": object_name, "message": "released object has no captured samples"})
                continue
            motion_frame = first_motion[object_name]
            if motion_frame is not None and motion_frame < resolved_frame:
                issues.append({"code": "physics.early_motion", "stage": name, "object": object_name, "message": f"released object moved at frame {motion_frame} before release {resolved_frame}"})
            expected = stage.get("expected_motion_window")
            if expected and (motion_frame is None or not expected[0] <= motion_frame <= expected[1]):
                issues.append({"code": "physics.motion_window", "stage": name, "object": object_name, "message": f"first motion frame {motion_frame} is outside {expected}"})
        expected_break = stage.get("expected_break_window")
        for constraint_name in stage.get("break_constraints", []):
            break_frame = observed_breaks.get(constraint_name.casefold())
            if break_frame is None:
                issues.append({"code": "physics.constraint_not_broken", "stage": name, "constraint": constraint_name, "message": "declared breakable constraint did not separate"})
                continue
            if break_frame < resolved_frame:
                issues.append({"code": "physics.early_fracture", "stage": name, "constraint": constraint_name, "message": f"constraint separated at frame {break_frame} before trigger resolution {resolved_frame}"})
            if expected_break and not expected_break[0] <= break_frame <= expected_break[1]:
                issues.append({"code": "physics.break_window", "stage": name, "constraint": constraint_name, "message": f"constraint break frame {break_frame} is outside {expected_break}"})
        resolved[name.casefold()] = resolved_frame
        stage_reports.append({
            "name": name,
            "depends_on": list(stage.get("depends_on", [])),
            "release": list(stage.get("release", [])),
            "resolved_frame": resolved_frame,
            "contact_frame": contact_frame,
            "resolved_pair": resolved_pair if trigger.get("type") == "contact" else None,
            "candidate_pairs": _contact_pairs(trigger) if trigger.get("type") == "contact" else None,
            "trigger_window": list(trigger.get("window", [])) if trigger.get("type") == "contact" and trigger.get("window") else None,
            "first_motion": {item: first_motion.get(item) for item in stage.get("release", [])},
            "constraint_breaks": {item: observed_breaks.get(item.casefold()) for item in stage.get("break_constraints", [])},
        })
        processed_stages.add(name.casefold())

    interaction_reports: list[dict[str, Any]] = []
    for interaction in normalized.get("interactions", []):
        candidate_pairs = _contact_pairs(interaction)
        window = interaction["window"]
        contact_frame, pair = _contact_event(samples, candidate_pairs, window, float(simulation.get("contact_margin", DEFAULT_SIMULATION["contact_margin"])))
        response_report = {
            "name": interaction["name"],
            "pair": list(pair) if pair is not None else None,
            "resolved_pair": list(pair) if pair is not None else None,
            "candidate_pairs": candidate_pairs,
            "window": list(window),
            "contact_frame": contact_frame,
            "response": interaction["response"],
        }
        if contact_frame is None:
            issues.append({"code": "physics.interaction_missing", "interaction": interaction["name"], "message": "interaction contact was not observed"})
            interaction_reports.append(response_report)
            continue
        assert pair is not None
        before = _sub(_velocity(samples, contact_frame, pair[0]), _velocity(samples, contact_frame, pair[1]))
        response_window = max(1, int(interaction.get("response_window_frames", 1)))
        response_end = min(max_frame, contact_frame + response_window)
        response_candidates: list[dict[str, Any]] = []
        for response_frame in range(contact_frame + 1, response_end + 1):
            if response_frame not in samples or response_frame - 1 not in samples:
                continue
            candidate_after = _sub(
                _velocity(samples, response_frame, pair[0]),
                _velocity(samples, response_frame, pair[1]),
            )
            candidate_change = _direction_change(before, candidate_after)
            response_candidates.append({
                "frame": response_frame,
                "velocity": candidate_after,
                "direction_change": candidate_change,
            })
        if not response_candidates:
            response_candidates.append({
                "frame": contact_frame + 1,
                "velocity": [0.0, 0.0, 0.0],
                "direction_change": 0.0,
            })
        if interaction["response"] == "reverse":
            reversed_candidates = [item for item in response_candidates if _dot(before, item["velocity"]) < 0.0]
            selected = reversed_candidates[0] if reversed_candidates else max(response_candidates, key=lambda item: item["direction_change"])
        else:
            selected = max(response_candidates, key=lambda item: item["direction_change"])
        response_frame = int(selected["frame"])
        after = list(selected["velocity"])
        response_report["relative_velocity_before"] = before
        response_report["relative_velocity_after"] = after
        response_report["response_frame"] = response_frame
        response_report["response_window_frames"] = response_window
        before_speed = _length(before)
        after_speed = _length(after)
        response_report["relative_speed_before"] = round(before_speed, 6)
        response_report["relative_speed_after"] = round(after_speed, 6)
        change = _direction_change(before, after)
        response_report["direction_change_deg"] = round(change, 4)
        min_speed_value = interaction.get("min_speed", simulation.get("min_response_speed"))
        min_speed = float(min_speed_value) if min_speed_value is not None else None
        response_report["min_speed"] = min_speed
        if interaction["response"] in {"deflect", "reverse"}:
            if min_speed is not None and (before_speed < min_speed or after_speed < min_speed):
                issues.append({"code": "physics.response_too_slow", "interaction": interaction["name"], "message": f"relative speed {before_speed:.6f}->{after_speed:.6f} is below {min_speed}"})
            elif min_speed is None and (before_speed <= 1e-9 or after_speed <= 1e-9):
                issues.append({"code": "physics.response_unmeasurable", "interaction": interaction["name"], "message": "relative velocity is numerically zero before or after contact"})
        minimum_change = interaction.get("min_direction_change_deg")
        response_report["min_direction_change_deg"] = minimum_change
        if minimum_change is not None and interaction["response"] in {"deflect", "reverse"} and change < float(minimum_change):
            issues.append({"code": "physics.response_weak", "interaction": interaction["name"], "message": f"direction change {change:.3f} is below {minimum_change} degrees"})
        elif minimum_change is None and interaction["response"] == "deflect" and change <= 1e-6:
            issues.append({"code": "physics.response_not_deflected", "interaction": interaction["name"], "message": "post-contact direction did not change"})
        if interaction["response"] == "reverse" and _dot(before, after) >= 0.0:
            issues.append({"code": "physics.response_not_reversed", "interaction": interaction["name"], "message": "post-contact relative velocity did not reverse"})
        if interaction["response"] == "separate":
            current = samples.get(contact_frame, {}).get(pair[0])
            other = samples.get(contact_frame, {}).get(pair[1])
            if not current or not other:
                issues.append({"code": "physics.response_unmeasurable", "interaction": interaction["name"], "message": "separation response lacks a post-contact sample"})
            else:
                current_delta = _sub(_sample_center(current), _sample_center(other))
                distance_before = _length(current_delta)
                separation_candidates = []
                for candidate_frame in range(contact_frame + 1, response_end + 1):
                    previous_current = samples.get(candidate_frame - 1, {}).get(pair[0])
                    previous_other = samples.get(candidate_frame - 1, {}).get(pair[1])
                    next_current = samples.get(candidate_frame, {}).get(pair[0])
                    next_other = samples.get(candidate_frame, {}).get(pair[1])
                    if not previous_current or not previous_other or not next_current or not next_other:
                        continue
                    previous_delta = _sub(
                        _sample_center(previous_current),
                        _sample_center(previous_other),
                    )
                    next_delta = _sub(_sample_center(next_current), _sample_center(next_other))
                    local_normal = _normalize(previous_delta)
                    separation_candidates.append((
                        _length(next_delta),
                        _dot(_sub(next_delta, previous_delta), local_normal),
                        candidate_frame,
                    ))
                if not separation_candidates:
                    distance_after = distance_before
                    outward_speed = 0.0
                    selected_separation_frame = None
                else:
                    distance_after, outward_speed, selected_separation_frame = max(
                        separation_candidates,
                        key=lambda item: item[0],
                    )
                separation_epsilon = float(simulation.get("separation_epsilon", DEFAULT_SIMULATION["separation_epsilon"]))
                response_report["distance_before"] = round(distance_before, 6)
                response_report["distance_after"] = round(distance_after, 6)
                response_report["outward_speed"] = round(outward_speed, 6)
                response_report["response_frame"] = selected_separation_frame
                outward_floor = min_speed if min_speed is not None else 1e-9
                if distance_after - distance_before <= separation_epsilon or outward_speed <= outward_floor:
                    issues.append({"code": "physics.response_no_separation", "interaction": interaction["name"], "message": "post-contact distance and outward relative speed did not increase enough"})
        interaction_reports.append(response_report)
    if final_report is not None:
        if final_report.get("settled") is False:
            issues.append({"code": "physics.unsettled", "message": "simulation did not reach a stable window"})
        for key in ("kinematic_at_end", "potentially_unwoken", "outside_receiver"):
            if final_report.get(key):
                issues.append({"code": f"physics.{key}", "message": f"simulation report contains {key}"})
        audit = final_report.get("static_collision_audit", {})
        if isinstance(audit, Mapping) and audit.get("max_penetration") is not None:
            tolerance = float(simulation.get("penetration_tolerance", DEFAULT_SIMULATION["penetration_tolerance"]))
            if float(audit["max_penetration"]) > tolerance:
                issues.append({"code": "physics.penetration", "message": f"max penetration {audit['max_penetration']} exceeds {tolerance}"})
    final_state = None
    if final_report is not None:
        final_state = {
            "settled": final_report.get("settled"),
            "kinematic_at_end": list(final_report.get("kinematic_at_end", [])),
            "potentially_unwoken": list(final_report.get("potentially_unwoken", [])),
            "outside_receiver": list(final_report.get("outside_receiver", [])),
            "static_collision_audit": final_report.get("static_collision_audit", {}),
        }
    return {
        "status": "pass" if not issues else "fail",
        "simulation_source": final_report.get("simulation_source") if final_report is not None else None,
        "issues": issues,
        "stages": stage_reports,
        "interactions": interaction_reports,
        "first_motion_frame": first_motion,
        "final_state": final_state,
        "final_frame": max(samples) if samples else None,
    }
