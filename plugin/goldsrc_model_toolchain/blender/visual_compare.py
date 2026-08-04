"""Canonical unlit author/readback rendering and pixel-level comparison."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Iterable

import bpy
from mathutils import Matrix, Vector
from PIL import Image

from ..core.errors import ToolchainError
from ..core.model_contract import load_contract
from ..core.visual_evidence import (
    choose_front_axis,
    create_labeled_contact_sheet,
    decoded_pixel_sha256,
)


ROOT_AXIS = Matrix.Rotation(math.radians(90.0), 4, "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contract_bounds(contract: dict[str, Any]) -> tuple[Vector, Vector, Vector, dict[str, Any]]:
    box = contract["bounds"]["bbox"]
    low = box["min"]
    high = box["max"]
    points = [
        ROOT_AXIS @ Vector((x, y, z))
        for x in (low[0], high[0])
        for y in (low[1], high[1])
        for z in (low[2], high[2])
    ]
    minimum = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    maximum = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    center = (minimum + maximum) * 0.5
    framing = choose_front_axis(minimum, maximum)
    return minimum, maximum, center, framing


def _material_image(material):
    if material is None or not material.use_nodes or material.node_tree is None:
        return None
    images = [
        node.image for node in material.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image is not None
    ]
    if not images:
        return None
    token = material.get("goldsrc_texture_token")
    if isinstance(token, str):
        for image in images:
            if image.get("goldsrc_texture_token") == token:
                return image
    return images[0]


def _texture_specs(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for texture in [*contract.get("textures", []), *contract.get("large_textures", [])]:
        name = texture.get("name")
        if not isinstance(name, str):
            continue
        result[Path(name).name.casefold()] = {
            "modes": {str(mode).casefold() for mode in texture.get("modes", [])},
            "alpha_threshold": int(texture.get("alpha_threshold", 128)),
            "width": int(texture.get("width", 0)),
            "height": int(texture.get("height", 0)),
        }
    return result


def _canonical_material(source, created_materials: list[Any], texture_specs: dict[str, dict[str, Any]]):
    material = bpy.data.materials.new(f"GoldSrcVisual_{len(created_materials):03d}")
    created_materials.append(material)
    material.use_nodes = True
    material.surface_render_method = "DITHERED"
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    image = _material_image(source)
    token = source.get("goldsrc_texture_token") if source is not None else None
    spec = texture_specs.get(Path(token).name.casefold(), {}) if isinstance(token, str) else {}
    modes = spec.get("modes", set())
    if image is not None:
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = image
        texture.interpolation = "Closest"
        texture.extension = "EXTEND"
        material.node_tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
        if "masked" in modes:
            threshold = max(0, min(255, int(spec.get("alpha_threshold", 128))))
            cutoff = max(0.0, (threshold - 0.5) / 255.0)
            compare = nodes.new("ShaderNodeMath")
            compare.operation = "GREATER_THAN"
            compare.inputs[1].default_value = cutoff
            material.node_tree.links.new(texture.outputs["Alpha"], compare.inputs[0])
            material.node_tree.links.new(compare.outputs[0], mix.inputs[0])
        else:
            mix.inputs[0].default_value = 1.0
    else:
        color = source.diffuse_color if source is not None else (0.8, 0.8, 0.8, 1.0)
        emission.inputs["Color"].default_value = color
        if "masked" in modes:
            threshold = max(0, min(255, int(spec.get("alpha_threshold", 128))))
            mix.inputs[0].default_value = float(round(float(color[3]) * 255.0) >= threshold)
        else:
            mix.inputs[0].default_value = 1.0
    emission.inputs["Strength"].default_value = 1.0
    material.node_tree.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    material.node_tree.links.new(emission.outputs["Emission"], mix.inputs[2])
    material.node_tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
    return material


def _explicit_loop_triangles(
    mesh,
    *,
    quantize_smd: bool,
    texture_specs: dict[str, dict[str, Any]],
):
    if not quantize_smd and all(len(polygon.vertices) == 3 for polygon in mesh.polygons):
        return mesh
    mesh.calc_loop_triangles()
    active_uv = mesh.uv_layers.active
    vertices = []
    faces = []
    uvs = []
    material_indices = []
    for triangle in mesh.loop_triangles:
        start = len(vertices)
        polygon = mesh.polygons[triangle.polygon_index]
        source_material = (
            mesh.materials[polygon.material_index]
            if polygon.material_index < len(mesh.materials) else None
        )
        token = source_material.get("goldsrc_texture_token") if source_material is not None else None
        texture = texture_specs.get(Path(token).name.casefold(), {}) if isinstance(token, str) else {}
        for loop_index in triangle.loops:
            loop = mesh.loops[loop_index]
            coordinate = tuple(mesh.vertices[loop.vertex_index].co)
            vertices.append(tuple(round(float(value), 6) for value in coordinate) if quantize_smd else coordinate)
            if active_uv is not None:
                uv = tuple(active_uv.data[loop_index].uv)
                if quantize_smd:
                    u, v = (round(float(value), 6) for value in uv)
                    width = int(texture.get("width", 0))
                    height = int(texture.get("height", 0))
                    if width > 0 and height > 0:
                        u = math.floor(u * width + 0.5) / width
                        v = 1.0 - math.floor((1.0 - v) * height + 0.5) / height
                    uv = (u, v)
                uvs.append(uv)
        faces.append((start, start + 1, start + 2))
        material_indices.append(polygon.material_index)
    result = bpy.data.meshes.new(f"{mesh.name}_LoopTriangles")
    result.from_pydata(vertices, [], faces)
    for material in mesh.materials:
        result.materials.append(material)
    for polygon, material_index in zip(result.polygons, material_indices):
        polygon.material_index = material_index
    if active_uv is not None:
        layer = result.uv_layers.new(name=active_uv.name)
        for item, coordinate in zip(layer.data, uvs):
            item.uv = coordinate
        result.uv_layers.active = layer
        layer.active_render = True
    result.update()
    return result


def _freeze_objects(
    objects: Iterable[Any],
    scene,
    *,
    apply_root_axis: bool,
    created_objects: list[Any],
    created_meshes: list[Any],
    created_materials: list[Any],
    texture_specs: dict[str, dict[str, Any]],
) -> list[Any]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    material_cache = {}
    result = []
    for source in objects:
        evaluated = source.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(
            evaluated, preserve_all_data_layers=True, depsgraph=depsgraph,
        )
        transform = evaluated.matrix_world.copy()
        if apply_root_axis:
            transform = ROOT_AXIS @ transform
        mesh.transform(transform)
        mesh.update()
        render_mesh = _explicit_loop_triangles(
            mesh,
            quantize_smd=apply_root_axis,
            texture_specs=texture_specs,
        )
        if render_mesh is not mesh:
            bpy.data.meshes.remove(mesh)
            mesh = render_mesh
        created_meshes.append(mesh)
        source_materials = list(mesh.materials) or [slot.material for slot in source.material_slots]
        for material_index, source_material in enumerate(source_materials):
            pointer = source_material.as_pointer() if source_material is not None else 0
            if pointer not in material_cache:
                material_cache[pointer] = _canonical_material(
                    source_material, created_materials, texture_specs,
                )
            canonical = material_cache[pointer]
            if material_index < len(mesh.materials):
                mesh.materials[material_index] = canonical
            else:
                mesh.materials.append(canonical)
        obj = bpy.data.objects.new(f"GoldSrcVisualObject_{len(created_objects):03d}", mesh)
        created_objects.append(obj)
        scene.collection.objects.link(obj)
        result.append(obj)
    return result


def _configure_scene(scene, contract: dict[str, Any]) -> dict[str, Any]:
    minimum, maximum, center, framing = _contract_bounds(contract)
    axis = int(framing["axis"])
    spans = maximum - minimum
    projected_span = max(max(framing["projected_spans"]), 0.25)
    max_span = max(max(spans), 0.25)
    direction = Vector(tuple(1.0 if index == axis else 0.0 for index in range(3)))
    camera_data = bpy.data.cameras.new(f"{scene.name}_CameraData")
    camera = bpy.data.objects.new(f"{scene.name}_Camera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = center + direction * max(max_span * 2.0, 1.0)
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = projected_span * 1.25
    camera_data.clip_start = max(max_span * 0.0001, 0.01)
    camera_data.clip_end = max(max_span * 4.0, 1000.0)
    scene.camera = camera
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.film_transparent = True
    return {
        "minimum": list(minimum),
        "maximum": list(maximum),
        "center": list(center),
        "view_axis": framing["axis_name"],
        "camera_location": list(camera.location),
        "orthographic_scale": float(camera_data.ortho_scale),
        "resolution": [512, 512],
        "root_axis_mapping": "+90deg_Z",
    }


def render_canonical_objects(
    objects: Iterable[Any],
    contract: dict[str, Any],
    destination: str | Path,
    *,
    apply_root_axis: bool,
) -> dict[str, Any]:
    source_objects = list(objects)
    if not source_objects:
        raise ToolchainError("VISUAL", "visual.objects", "Canonical render has no mesh objects", {})
    path = Path(destination).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    scene = bpy.data.scenes.new(f"GoldSrcVisual_{hashlib.sha1(str(path).encode()).hexdigest()[:10]}")
    created_objects = []
    created_meshes = []
    created_materials = []
    camera_data = None
    camera_object = None
    try:
        frozen = _freeze_objects(
            source_objects,
            scene,
            apply_root_axis=apply_root_axis,
            created_objects=created_objects,
            created_meshes=created_meshes,
            created_materials=created_materials,
            texture_specs=_texture_specs(contract),
        )
        framing = _configure_scene(scene, contract)
        camera_object = scene.camera
        camera_data = scene.camera.data
        scene.render.filepath = str(path)
        result = bpy.ops.render.render(write_still=True, scene=scene.name)
        if result != {"FINISHED"} or not path.is_file():
            raise ToolchainError(
                "VISUAL", "visual.render", "Canonical visual render did not finish",
                {"result": sorted(result), "path": str(path)},
            )
        with Image.open(path) as opened:
            rgba = opened.convert("RGBA")
            alpha = rgba.getchannel("A")
            foreground = sum(1 for value in alpha.getdata() if value > 0)
        if foreground == 0:
            raise ToolchainError(
                "VISUAL", "visual.blank", "Canonical visual render has no foreground pixels",
                {"path": str(path), "objects": [obj.name for obj in frozen]},
            )
        return {
            "path": str(path),
            "sha256": _sha256(path),
            "pixel_sha256": decoded_pixel_sha256(path),
            "bytes": path.stat().st_size,
            "foreground_pixels": foreground,
            "foreground_fraction": foreground / float(512 * 512),
            "framing": framing,
        }
    finally:
        bpy.data.scenes.remove(scene)
        if camera_object is not None and camera_object.name in bpy.data.objects:
            bpy.data.objects.remove(camera_object, do_unlink=True)
        for obj in reversed(created_objects):
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        for material in reversed(created_materials):
            if material.name in bpy.data.materials:
                bpy.data.materials.remove(material)
        for mesh in reversed(created_meshes):
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)
        if camera_data is not None and camera_data.name in bpy.data.cameras:
            bpy.data.cameras.remove(camera_data)


def _owned_author_objects(contract: dict[str, Any]) -> list[Any]:
    names = [body["object"] for body in contract.get("bodies", [])]
    for group in contract.get("bodygroups", []):
        names.extend(
            choice["object"] for choice in group.get("choices", [])
            if isinstance(choice, dict) and isinstance(choice.get("object"), str)
        )
    missing = [name for name in names if bpy.data.objects.get(name) is None]
    if missing:
        raise ToolchainError(
            "VISUAL", "visual.author_objects", "Contract-owned author objects are missing",
            {"missing": missing},
        )
    return [bpy.data.objects[name] for name in names]


def create_static_author_preview(
    contract_path: str | Path,
    artifacts_dir: str | Path,
) -> dict[str, Any]:
    root = Path(artifacts_dir).expanduser().resolve()
    contract = load_contract(contract_path, artifact_dir=root, require_files=False)
    return render_canonical_objects(
        _owned_author_objects(contract),
        contract,
        root / "visual_compare" / "author_canonical.png",
        apply_root_axis=True,
    )


def _validated_preview_path(
    preview: dict[str, Any],
    root: Path,
    *,
    code: str,
    label: str,
) -> Path:
    if not isinstance(preview, dict) or not isinstance(preview.get("path"), str):
        raise ToolchainError(
            "VISUAL", code, f"{label} canonical preview is unavailable", {},
        )
    path = Path(preview["path"]).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ToolchainError(
            "VISUAL", code + "_escape", f"{label} preview escaped artifacts_dir",
            {"path": str(path)},
        ) from exc
    if not path.is_file():
        raise ToolchainError(
            "VISUAL", code + "_missing", f"{label} canonical preview is missing",
            {"path": str(path)},
        )
    return path


def _bbox(mask: list[bool], width: int, height: int) -> list[int] | None:
    points = [(index % width, index // width) for index, value in enumerate(mask) if value]
    if not points:
        return None
    return [
        min(point[0] for point in points), min(point[1] for point in points),
        max(point[0] for point in points), max(point[1] for point in points),
    ]


def _centroid(mask: list[bool], width: int) -> list[float] | None:
    indices = [index for index, value in enumerate(mask) if value]
    if not indices:
        return None
    return [
        sum(index % width for index in indices) / len(indices),
        sum(index // width for index in indices) / len(indices),
    ]


def _erode(mask: list[bool], width: int, height: int, radius: int) -> list[bool]:
    output = [False] * len(mask)
    for y in range(radius, height - radius):
        for x in range(radius, width - radius):
            index = y * width + x
            if not mask[index]:
                continue
            output[index] = all(
                mask[(y + dy) * width + (x + dx)]
                for dy in range(-radius, radius + 1)
                for dx in range(-radius, radius + 1)
            )
    return output


def _fidelity_limits(export_report: dict[str, Any]) -> dict[str, float]:
    means = []
    maxima = []

    def visit(value):
        if isinstance(value, dict):
            mean = value.get("mean_absolute_channel_error")
            maximum = value.get("max_absolute_channel_error")
            if isinstance(mean, (int, float)):
                means.append(float(mean))
            if isinstance(maximum, (int, float)):
                maxima.append(float(maximum))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(export_report.get("textures", []))
    return {
        "quantization_mean": max(means, default=0.0),
        "quantization_max": max(maxima, default=0.0),
        "mean_tolerance": max(means, default=0.0) + 2.0,
        "max_tolerance": max(maxima, default=0.0) + 4.0,
    }


def _compare_images(author_path: Path, readback_path: Path, export_report: dict[str, Any]) -> dict[str, Any]:
    with Image.open(author_path) as opened:
        author = opened.convert("RGBA")
    with Image.open(readback_path) as opened:
        readback = opened.convert("RGBA")
    if author.size != (512, 512) or readback.size != author.size:
        raise ToolchainError(
            "VISUAL", "visual.resolution", "Canonical images must both be 512x512",
            {"author": list(author.size), "readback": list(readback.size)},
        )
    width, height = author.size
    author_pixels = list(author.getdata())
    readback_pixels = list(readback.getdata())
    author_mask = [pixel[3] > 0 for pixel in author_pixels]
    readback_mask = [pixel[3] > 0 for pixel in readback_pixels]
    intersection = sum(left and right for left, right in zip(author_mask, readback_mask))
    union = sum(left or right for left, right in zip(author_mask, readback_mask))
    iou = intersection / union if union else 0.0
    author_box = _bbox(author_mask, width, height)
    readback_box = _bbox(readback_mask, width, height)
    author_center = _centroid(author_mask, width)
    readback_center = _centroid(readback_mask, width)
    bbox_delta = max(
        (abs(left - right) for left, right in zip(author_box or [], readback_box or [])),
        default=float("inf"),
    )
    centroid_delta = max(
        (abs(left - right) for left, right in zip(author_center or [], readback_center or [])),
        default=float("inf"),
    )
    eroded = _erode(author_mask, width, height, 2)
    missing_interior = sum(left and not right for left, right in zip(eroded, readback_mask))
    compared = [
        index for index, value in enumerate(eroded)
        if value and readback_mask[index]
    ]
    differences = [
        abs(author_pixels[index][channel] - readback_pixels[index][channel])
        for index in compared
        for channel in range(3)
    ]
    mean_error = sum(differences) / len(differences) if differences else float("inf")
    max_error = max(differences, default=float("inf"))
    limits = _fidelity_limits(export_report)
    checks = {
        "nonempty_foreground": bool(sum(author_mask) and sum(readback_mask)),
        "outline_iou": iou >= 0.98,
        "bounds_delta": bbox_delta <= 2.0,
        "centroid_delta": centroid_delta <= 2.0,
        "no_interior_cracks": missing_interior == 0,
        "mean_rgb_error": mean_error <= limits["mean_tolerance"],
        "max_rgb_error": max_error <= limits["max_tolerance"],
    }
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "metrics": {
            "author_foreground_pixels": sum(author_mask),
            "readback_foreground_pixels": sum(readback_mask),
            "outline_iou": iou,
            "author_bounds": author_box,
            "readback_bounds": readback_box,
            "bounds_max_delta_px": bbox_delta,
            "author_centroid": author_center,
            "readback_centroid": readback_center,
            "centroid_max_delta_px": centroid_delta,
            "author_interior_pixels": sum(eroded),
            "missing_readback_interior_pixels": missing_interior,
            "rgb_compared_pixels": len(compared),
            "mean_absolute_rgb_error": mean_error,
            "max_absolute_rgb_error": max_error,
            **limits,
        },
    }


def create_static_visual_comparison(
    contract_path: str | Path,
    artifacts_dir: str | Path,
    roundtrip_report: dict[str, Any],
    export_report: dict[str, Any],
    *,
    author_preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(artifacts_dir).expanduser().resolve()
    contract = load_contract(contract_path, artifact_dir=root, require_files=False)
    material_audits = [
        reference.get("static_material_audit")
        for reference in export_report.get("references", [])
        if isinstance(reference, dict) and reference.get("static_material_audit") is not None
    ]
    if contract.get("static_material_audit") is not None and (
        not material_audits
        or any(not isinstance(item, dict) or item.get("status") != "pass" for item in material_audits)
    ):
        raise ToolchainError(
            "VISUAL", "visual.source_material_audit",
            "Canonical comparison requires a passing source-evaluated material audit",
            {"material_audits": material_audits},
        )
    visual_root = root / "visual_compare"
    author_path = visual_root / "author_canonical.png"
    author = author_preview or render_canonical_objects(
        _owned_author_objects(contract), contract, author_path, apply_root_axis=True,
    )
    author_path = _validated_preview_path(
        author, root, code="visual.author_source", label="Author",
    )
    readback = roundtrip_report.get("canonical_preview")
    readback_path = _validated_preview_path(
        readback, root, code="visual.readback_source", label="Readback",
    )
    comparison = _compare_images(author_path, readback_path, export_report)
    sheet = create_labeled_contact_sheet(
        [
            {"path": str(author_path), "label": "AUTHOR", "detail": "StudioMDL +90 Z mapping"},
            {"path": str(readback_path), "label": "MDL READBACK", "detail": "Decoded indexed textures"},
        ],
        visual_root / "author_readback_compare.png",
        title="GoldSrc static visual comparison",
        columns=2,
        tile_width=512,
        tile_height=512,
    )
    return {
        **comparison,
        "phase": "visual_review",
        "author": author,
        "readback": readback,
        "source_evaluated_material_audits": material_audits,
        "contact_sheet": sheet,
    }
