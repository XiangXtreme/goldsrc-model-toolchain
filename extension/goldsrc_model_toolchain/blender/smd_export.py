"""Contract-driven GoldSrc SMD and indexed texture export for Blender 5.2."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import bpy
from mathutils import Matrix, Vector

from ..core.errors import ToolchainError
from ..core.model_contract import load_contract, write_qc
from ..core.smd import animation_budget_hint, audit_loop_endpoint, read_smd, validate_smd
from ..core.textures import convert_rgba_to_indexed_bmp, convert_to_indexed_bmp, validate_indexed_bmp


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _artifact_path(root: Path, relative: str, phase: str) -> Path:
    path = (root / relative).resolve()
    if not _inside(path, root):
        raise ToolchainError(phase, "path.escape", "Artifact path escapes its explicit directory", {"path": str(path)})
    return path


def _float(value: float) -> str:
    if abs(value) < 0.0000005:
        value = 0.0
    return f"{value:.6f}"


def _matrix_pose(matrix: Matrix) -> str:
    position = matrix.to_translation()
    rotation = matrix.to_euler("XYZ")
    return " ".join(_float(value) for value in (*position, *rotation))


def _write_nodes(handle, contract: dict) -> dict[str, int]:
    ids = {bone["name"]: index for index, bone in enumerate(contract["bones"])}
    handle.write("version 1\nnodes\n")
    for index, bone in enumerate(contract["bones"]):
        parent = ids.get(bone.get("parent"), -1)
        escaped = bone["name"].replace("\\", "\\\\").replace('"', '\\"')
        handle.write(f'{index} "{escaped}" {parent}\n')
    handle.write("end\n")
    return ids


def _armature_for_contract(contract: dict, objects: Iterable[bpy.types.Object]):
    required = {bone["name"] for bone in contract["bones"]}
    candidates = []
    for obj in objects:
        if obj.parent and obj.parent.type == "ARMATURE":
            candidates.append(obj.parent)
        candidates.extend(
            modifier.object for modifier in obj.modifiers
            if modifier.type == "ARMATURE" and modifier.object is not None
        )
    candidates.extend(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    seen = set()
    for armature in candidates:
        if armature.name in seen:
            continue
        seen.add(armature.name)
        if required.issubset({bone.name for bone in armature.data.bones}):
            return armature
    if len(contract["bones"]) == 1 and contract["bones"][0].get("parent") is None:
        return None
    raise ToolchainError(
        "EXPORT", "export.armature", "No Armature resolves every contract bone",
        {"required_bones": sorted(required)},
    )


def _rest_matrices(contract: dict, armature) -> dict[str, Matrix]:
    if armature is None:
        return {contract["bones"][0]["name"]: Matrix.Identity(4)}
    matrices = {}
    for item in contract["bones"]:
        bone = armature.data.bones.get(item["name"])
        if bone is None:
            raise ToolchainError("EXPORT", "export.bone", "Contract bone is absent from Armature", {"bone": item["name"]})
        if item.get("parent"):
            parent = armature.data.bones[item["parent"]]
            matrices[item["name"]] = parent.matrix_local.inverted_safe() @ bone.matrix_local
        else:
            matrices[item["name"]] = armature.matrix_world @ bone.matrix_local
    return matrices


def _pose_matrices(contract: dict, armature) -> dict[str, Matrix]:
    matrices = {}
    for item in contract["bones"]:
        bone = armature.pose.bones.get(item["name"])
        if bone is None:
            raise ToolchainError("EXPORT", "export.pose_bone", "Contract bone has no PoseBone", {"bone": item["name"]})
        if item.get("parent"):
            parent = armature.pose.bones[item["parent"]]
            matrices[item["name"]] = parent.matrix.inverted_safe() @ bone.matrix
        else:
            matrices[item["name"]] = armature.matrix_world @ bone.matrix
    return matrices


def _write_skeleton(handle, contract: dict, frames: list[tuple[int, dict[str, Matrix]]], bone_ids: dict[str, int]) -> None:
    handle.write("skeleton\n")
    for frame, matrices in frames:
        handle.write(f"time {frame}\n")
        for bone in contract["bones"]:
            handle.write(f"{bone_ids[bone['name']]} {_matrix_pose(matrices[bone['name']])}\n")
    handle.write("end\n")


def _image_hints(material) -> list[str]:
    hints = []
    if material and material.use_nodes and material.node_tree:
        for node in material.node_tree.nodes:
            image = getattr(node, "image", None)
            if image:
                hints.extend((image.name, Path(bpy.path.abspath(image.filepath or image.name)).name))
    return hints


def _material_token(material, contract: dict) -> str:
    names = {texture["name"].casefold(): texture["name"] for texture in contract["textures"]}
    sources = {Path(texture["source"]).name.casefold(): texture["name"] for texture in contract["textures"]}
    candidates = []
    if material:
        custom = material.get("goldsrc_texture_token")
        if isinstance(custom, str):
            candidates.append(custom)
        candidates.extend(_image_hints(material))
        candidates.extend((material.name, material.name + ".bmp"))
    for candidate in candidates:
        key = Path(str(candidate)).name.casefold()
        if key in names:
            return names[key]
        if key in sources:
            return sources[key]
    raise ToolchainError(
        "EXPORT", "export.material", "Mesh material does not resolve to a contract texture",
        {"material": material.name if material else None, "image_hints": _image_hints(material)},
    )


def _vertex_bone(obj, vertex, bone_ids: dict[str, int]) -> int:
    weights = []
    for group in vertex.groups:
        if group.weight <= 0:
            continue
        if group.group >= len(obj.vertex_groups):
            continue
        name = obj.vertex_groups[group.group].name
        if name in bone_ids:
            weights.append((group.weight, bone_ids[name], name))
    if not weights:
        if len(bone_ids) == 1:
            return next(iter(bone_ids.values()))
        raise ToolchainError(
            "EXPORT", "export.unweighted_vertex", "Exported vertex has no contract bone influence",
            {"object": obj.name, "vertex": vertex.index},
        )
    weights.sort(reverse=True)
    if len(weights) != 1 or abs(weights[0][0] - 1.0) > 0.0001:
        raise ToolchainError(
            "EXPORT", "export.goldsrc_weights", "GoldSrc vertices require exactly one 1.0 bone influence",
            {"object": obj.name, "vertex": vertex.index, "weights": weights},
        )
    return weights[0][1]


def _evaluated_uv_facts(mesh, obj) -> dict:
    active = getattr(getattr(mesh, "uv_layers", None), "active", None)
    if active is None:
        raise ToolchainError("EXPORT", "export.uv", "Exported mesh has no active UV map", {"object": obj.name})
    data = active.data
    coordinates = []
    for item in data:
        uv = item.uv
        x = float(uv.x)
        y = float(uv.y)
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ToolchainError(
                "EXPORT", "export.uv_nonfinite", "Exported mesh has non-finite UV coordinates",
                {"object": obj.name, "uv_layer": active.name},
            )
        coordinates.append((x, y))
    return {
        "layer": active.name,
        "loop_count": len(data),
        "bounds": {
            "min": [min(value[0] for value in coordinates), min(value[1] for value in coordinates)],
            "max": [max(value[0] for value in coordinates), max(value[1] for value in coordinates)],
        } if coordinates else None,
    }


def _write_triangles(handle, obj, contract: dict, bone_ids: dict[str, int]) -> dict:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    if mesh is None:
        raise ToolchainError("EXPORT", "export.mesh_eval", "Could not evaluate mesh", {"object": obj.name})
    try:
        uv_facts = _evaluated_uv_facts(mesh, obj)
        mesh.calc_loop_triangles()
        world = evaluated.matrix_world.copy()
        normal_matrix = world.to_3x3().inverted_safe().transposed()
        reverse = world.to_3x3().determinant() < 0
        uv_data = mesh.uv_layers.active.data
        materials = list(getattr(mesh, "materials", ()))
        if not materials:
            materials = [slot.material for slot in obj.material_slots]
        count = 0
        tokens = set()
        handle.write("triangles\n")
        for triangle in mesh.loop_triangles:
            polygon = mesh.polygons[triangle.polygon_index]
            material = materials[polygon.material_index] if polygon.material_index < len(materials) else None
            token = _material_token(material, contract)
            tokens.add(token)
            handle.write(token + "\n")
            loop_indices = list(triangle.loops)
            if reverse:
                loop_indices[1], loop_indices[2] = loop_indices[2], loop_indices[1]
            for loop_index in loop_indices:
                loop = mesh.loops[loop_index]
                vertex = mesh.vertices[loop.vertex_index]
                position = world @ vertex.co
                normal = (normal_matrix @ loop.normal).normalized()
                uv = uv_data[loop_index].uv
                bone = _vertex_bone(evaluated, vertex, bone_ids)
                handle.write(
                    f"{bone} "
                    + " ".join(_float(value) for value in (*position, *normal, uv.x, uv.y))
                    + "\n"
                )
            count += 1
        handle.write("end\n")
        if not count:
            raise ToolchainError("EXPORT", "export.empty_mesh", "Exported object has no triangles", {"object": obj.name})
        return {
            "object": obj.name,
            "triangles": count,
            "materials": sorted(tokens),
            "evaluated_uv": uv_facts,
            "evaluated_material_slots": [
                material.name for material in materials if material is not None
            ],
        }
    finally:
        evaluated.to_mesh_clear()


def _export_reference(path: Path, contract: dict, obj, armature) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        bone_ids = _write_nodes(handle, contract)
        _write_skeleton(handle, contract, [(0, _rest_matrices(contract, armature))], bone_ids)
        facts = _write_triangles(handle, obj, contract, bone_ids)
    document = read_smd(path)
    issues = validate_smd(document, require_triangles=True)
    if issues:
        raise ToolchainError("EXPORT", "export.smd_validation", "Reference SMD failed validation", {"path": str(path), "issues": issues})
    facts["path"] = str(path)
    facts["bones"] = len(document.bones)
    return facts


def _bind_action(armature, action) -> None:
    if not armature.animation_data:
        armature.animation_data_create()
    armature.animation_data.action = action
    if action.slots:
        armature.animation_data.action_slot = action.slots[0]


def _export_animation(path: Path, contract: dict, sequence: dict, armature) -> dict:
    action = bpy.data.actions.get(sequence["action"])
    if action is None:
        raise ToolchainError("EXPORT", "export.action", "Contract Action is missing", {"action": sequence["action"]})
    if armature is None:
        raise ToolchainError("EXPORT", "export.animation_armature", "An animated sequence requires an Armature")
    start, end = sequence.get("frame", [int(action.frame_range[0]), int(action.frame_range[1])])
    _bind_action(armature, action)
    frames = []
    for frame in range(start, end + 1):
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        frames.append((frame, _pose_matrices(contract, armature)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        bone_ids = _write_nodes(handle, contract)
        _write_skeleton(handle, contract, frames, bone_ids)
    document = read_smd(path)
    issues = validate_smd(document, require_triangles=False)
    if issues:
        raise ToolchainError("EXPORT", "export.animation_validation", "Animation SMD failed validation", {"path": str(path), "issues": issues})
    loop_endpoint = audit_loop_endpoint(document) if sequence.get("loop") else None
    if loop_endpoint and loop_endpoint["status"] != "pass":
        raise ToolchainError(
            "EXPORT", "export.loop_endpoint",
            "Looped sequence must duplicate its first pose at the final exported frame",
            {"sequence": sequence["name"], "path": str(path), "audit": loop_endpoint},
        )
    return {
        "path": str(path), "action": action.name, "frames": len(frames),
        "frame_range": [start, end], "budget": animation_budget_hint(document),
        "loop_endpoint": loop_endpoint,
    }


def _texture_image(texture: dict):
    wanted = {texture["name"].casefold(), Path(texture["source"]).name.casefold()}
    for image in bpy.data.images:
        candidates = {image.name.casefold(), Path(bpy.path.abspath(image.filepath or image.name)).name.casefold()}
        if wanted & candidates:
            return image
    for material in bpy.data.materials:
        token = material.get("goldsrc_texture_token")
        if isinstance(token, str) and token.casefold() == texture["name"].casefold():
            for node in material.node_tree.nodes if material.use_nodes and material.node_tree else []:
                image = getattr(node, "image", None)
                if image:
                    return image
    return None


def _export_texture(root: Path, texture: dict) -> dict:
    destination = _artifact_path(root, texture["source"], "EXPORT")
    image = _texture_image(texture)
    if image is None:
        if destination.is_file():
            return validate_indexed_bmp(
                destination, width=texture["width"], height=texture["height"], modes=texture.get("modes", []),
                require_masked_pixels=texture.get("require_masked_pixels", True),
            )
        raise ToolchainError(
            "EXPORT", "export.texture_image", "Contract texture has no Blender image and no valid artifact",
            {"texture": texture["name"]},
        )
    image.update()
    modes = texture.get("modes", [])
    alpha_threshold = int(texture.get("alpha_threshold", 128))
    require_masked_pixels = bool(texture.get("require_masked_pixels", True))
    filepath = bpy.path.abspath(image.filepath or "")
    disk_source = Path(filepath).expanduser().resolve() if filepath else None
    if (
        image.source in {"FILE", "SEQUENCE"}
        and disk_source is not None
        and disk_source.is_file()
        and not bool(getattr(image, "is_dirty", False))
    ):
        # File pixels are already display-encoded. Reading the source file also
        # avoids stale datablocks and any second linear-to-sRGB conversion.
        facts = convert_to_indexed_bmp(
            disk_source, destination, width=texture["width"], height=texture["height"],
            modes=modes, alpha_threshold=alpha_threshold, require_masked_pixels=require_masked_pixels,
        )
        facts["conversion"]["blender_image"] = image.name
    else:
        copy = None
        try:
            source = image
            if tuple(image.size) != (texture["width"], texture["height"]):
                copy = image.copy()
                copy.scale(texture["width"], texture["height"])
                copy.update()
                source = copy
            facts = convert_rgba_to_indexed_bmp(
                source.pixels[:], destination, width=texture["width"], height=texture["height"],
                modes=modes, alpha_threshold=alpha_threshold,
                input_color_space="linear", row_origin="bottom-left",
                require_masked_pixels=require_masked_pixels,
            )
            facts["conversion"]["blender_image"] = image.name
        finally:
            if copy is not None:
                bpy.data.images.remove(copy)
    facts = {
        **facts,
        "required_masked_pixels": require_masked_pixels,
    }
    hard_risks = sorted(set(facts["risk_labels"]) & {"no_visible_pixels", "all_visible_pixels_black"})
    if hard_risks:
        raise ToolchainError(
            "EXPORT", "export.texture_luminance", "Exported indexed texture has no usable visible luminance",
            {"texture": texture["name"], "risk_labels": hard_risks},
        )
    return facts


def _requirement_evidence(contract: dict, phase: str, evidence: dict) -> list[dict]:
    return [
        {
            "id": requirement["id"],
            "status": "pass",
            "summary": f"{phase} resolved the contract-owned Blender data and artifacts",
            "evidence": evidence,
        }
        for requirement in contract.get("intent", {}).get("requirements", [])
        if phase in requirement.get("evidence_phases", [])
    ]


def export_contract(contract_path: str | Path, artifacts_dir: str | Path) -> dict:
    root = Path(artifacts_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    contract = load_contract(contract_path, artifact_dir=root, require_files=False)
    object_specs = [(body["source"], body["object"], f"body:{body['name']}") for body in contract["bodies"]]
    for group in contract["bodygroups"]:
        for index, choice in enumerate(group["choices"]):
            if not choice.get("blank"):
                object_specs.append((choice["studio"], choice["object"], f"bodygroup:{group['name']}:{index}"))
    resolved = []
    for source, object_name, label in object_specs:
        obj = bpy.data.objects.get(object_name)
        if obj is None or obj.type != "MESH":
            raise ToolchainError("EXPORT", "export.object", "Contract object is missing or not a mesh", {"label": label, "object": object_name})
        resolved.append((source, obj, label))
    armature = _armature_for_contract(contract, [obj for _source, obj, _label in resolved])
    scene = bpy.context.scene
    previous_frame = scene.frame_current
    previous_action = armature.animation_data.action if armature and armature.animation_data else None
    previous_basis = {
        bone.name: bone.matrix_basis.copy() for bone in armature.pose.bones
    } if armature else {}
    references = []
    animations = []
    try:
        if armature and armature.animation_data:
            armature.animation_data.action = None
        if armature:
            for bone in armature.pose.bones:
                bone.matrix_basis.identity()
        scene.frame_set(scene.frame_start)
        bpy.context.view_layer.update()
        for source, obj, label in resolved:
            facts = _export_reference(_artifact_path(root, source, "EXPORT"), contract, obj, armature)
            facts["label"] = label
            references.append(facts)
        for sequence in contract["sequences"]:
            animations.append(_export_animation(_artifact_path(root, sequence["source"], "EXPORT"), contract, sequence, armature))
    finally:
        if armature:
            for bone in armature.pose.bones:
                bone.matrix_basis = previous_basis[bone.name]
        if armature and previous_action:
            _bind_action(armature, previous_action)
        scene.frame_set(previous_frame)
    textures = [{"name": texture["name"], **_export_texture(root, texture)} for texture in contract["textures"]]
    qc = write_qc(contract, root)
    load_contract(contract_path, artifact_dir=root, require_files=True)
    evidence = {
        "objects": [obj.name for _source, obj, _label in resolved],
        "actions": [sequence["action"] for sequence in contract["sequences"]],
        "reference_smds": references,
        "animation_smds": animations,
        "textures": [
            {
                "name": item["name"],
                "used_color_count": item["used_color_count"],
                "conversion_method": item.get("conversion", {}).get("method"),
                "fidelity": item.get("conversion", {}).get("fidelity"),
            }
            for item in textures
        ],
    }
    return {
        "status": "pass",
        "phase": "export",
        "contract_version": contract["version"],
        "armature": armature.name if armature else None,
        "references": references,
        "animations": animations,
        "textures": textures,
        "qc": str(qc),
        "requirement_evidence": _requirement_evidence(contract, "export", evidence),
    }
