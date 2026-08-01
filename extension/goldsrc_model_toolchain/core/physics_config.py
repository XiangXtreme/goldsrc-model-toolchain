"""Pure-Python rigid-body session configuration and topology diagnostics."""

from __future__ import annotations

from typing import Any, Sequence


def configure_rigidbody_world(
    scene: Any,
    *,
    frame_start: int,
    frame_end: int,
    use_gravity: bool = True,
    substeps_per_frame: int | None = None,
    solver_iterations: int | None = None,
    time_scale: float | None = None,
) -> dict[str, Any]:
    """Initialize the Blender 5.2 rigid-body world before evaluation."""

    start = int(frame_start)
    end = int(frame_end)
    if end <= start:
        raise ValueError("frame_end must be greater than frame_start")
    world = getattr(scene, "rigidbody_world", None)
    if world is None:
        raise RuntimeError("scene has no rigid-body world")

    scene.frame_start = start
    scene.frame_end = end
    scene.use_gravity = bool(use_gravity)
    cache = getattr(world, "point_cache", None)
    if cache is not None:
        cache.frame_start = start
        cache.frame_end = end
    if substeps_per_frame is not None:
        world.substeps_per_frame = int(substeps_per_frame)
    if solver_iterations is not None:
        world.solver_iterations = int(solver_iterations)
    if time_scale is not None:
        world.time_scale = float(time_scale)
    return {
        "frame_range": [start, end],
        "gravity_enabled": bool(scene.use_gravity),
        "point_cache_frame_range": [
            int(getattr(cache, "frame_start", start)),
            int(getattr(cache, "frame_end", end)),
        ],
        "substeps_per_frame": int(getattr(world, "substeps_per_frame", 0)),
        "solver_iterations": int(getattr(world, "solver_iterations", 0)),
        "time_scale": float(getattr(world, "time_scale", 1.0)),
    }


def audit_constraint_topology(
    objects: Sequence[Any],
    constraints: Sequence[Any],
    *,
    constraint_warning_threshold: int = 32,
    degree_warning_threshold: int = 8,
    component_warning_threshold: int = 16,
) -> dict[str, Any]:
    """Report dense constraint islands as recoverable pre-solve warnings."""

    names = [_object_name(obj) for obj in objects]
    graph: dict[str, set[str]] = {name: set() for name in names}
    warnings: list[dict[str, Any]] = []
    invalid_endpoints = 0
    self_links = 0
    for item in constraints:
        constraint = getattr(item, "rigid_body_constraint", item)
        first = getattr(constraint, "object1", None)
        second = getattr(constraint, "object2", None)
        first_name = _object_name(first) if first is not None else None
        second_name = _object_name(second) if second is not None else None
        if first_name not in graph or second_name not in graph:
            invalid_endpoints += 1
            continue
        if first_name == second_name:
            self_links += 1
            continue
        graph[first_name].add(second_name)
        graph[second_name].add(first_name)

    components: list[list[str]] = []
    unseen = set(graph)
    while unseen:
        root = sorted(unseen)[0]
        stack = [root]
        unseen.remove(root)
        component: list[str] = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(graph[current], reverse=True):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))

    degrees = {name: len(neighbors) for name, neighbors in graph.items()}
    max_degree = max(degrees.values(), default=0)
    largest_component = max((len(component) for component in components), default=0)
    count = len(constraints)
    if count > int(constraint_warning_threshold):
        warnings.append({"code": "constraint.count_dense", "count": count, "threshold": int(constraint_warning_threshold)})
    if max_degree > int(degree_warning_threshold):
        warnings.append({"code": "constraint.degree_dense", "max_degree": max_degree, "threshold": int(degree_warning_threshold)})
    if largest_component > int(component_warning_threshold):
        warnings.append({"code": "constraint.island_large", "largest_component": largest_component, "threshold": int(component_warning_threshold)})
    if invalid_endpoints:
        warnings.append({"code": "constraint.invalid_endpoint", "count": invalid_endpoints})
    if self_links:
        warnings.append({"code": "constraint.self_link", "count": self_links})
    return {
        "status": "warn" if warnings else "pass",
        "constraint_count": count,
        "object_count": len(names),
        "component_count": len(components),
        "components": sorted(components, key=lambda value: (-len(value), value)),
        "degrees": degrees,
        "max_degree": max_degree,
        "largest_component": largest_component,
        "warnings": warnings,
        "advisory_only": True,
    }


def _object_name(value: Any) -> str:
    return str(getattr(value, "name", value))
