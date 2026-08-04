"""Blender 5.2 fixture for the selected-static MDL product workflow."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]


def _runtime():
    api = bpy.app.driver_namespace.get("goldsrc_model_toolchain")
    if api is not None:
        return api
    sys.path.insert(0, str(REPO_ROOT / "plugin"))
    import goldsrc_model_toolchain

    goldsrc_model_toolchain.register()
    return bpy.app.driver_namespace["goldsrc_model_toolchain"]


def _reset() -> None:
    if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for datablocks in (
        bpy.data.meshes, bpy.data.armatures, bpy.data.materials,
        bpy.data.images, bpy.data.node_groups,
    ):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def _source_scene():
    _reset()
    mesh = bpy.data.meshes.new("BranchedCaveMesh_Source")
    mesh.from_pydata(
        [
            (-2.0, -1.0, -0.6), (2.0, -1.0, -0.6), (2.0, 1.0, -0.6), (-2.0, 1.0, -0.6),
            (-2.0, -1.0, 0.6), (2.0, -1.0, 0.6), (2.0, 1.0, 0.6), (-2.0, 1.0, 0.6),
        ],
        [],
        [
            (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
            (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7),
        ],
    )
    mesh.update()
    raw_material = bpy.data.materials.new("CaveRock_Mat")
    raw_material.use_nodes = True
    raw_shader = next(
        node for node in raw_material.node_tree.nodes
        if node.type == "BSDF_PRINCIPLED"
    )
    raw_shader.inputs["Base Color"].default_value = (0.08, 0.08, 0.08, 1.0)
    material = bpy.data.materials.new("AutoTerrain_base")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    first = nodes.new("ShaderNodeBsdfPrincipled")
    first.inputs["Base Color"].default_value = (0.1, 0.25, 0.55, 1.0)
    second = nodes.new("ShaderNodeBsdfPrincipled")
    second.inputs["Base Color"].default_value = (0.65, 0.2, 0.08, 1.0)
    if os.environ.get("GOLDSRC_STATIC_ALPHA_FIXTURE") == "1":
        checker = nodes.new("ShaderNodeTexChecker")
        checker.inputs["Scale"].default_value = 8.0
        material.node_tree.links.new(checker.outputs["Fac"], first.inputs["Alpha"])
        material.node_tree.links.new(checker.outputs["Fac"], second.inputs["Alpha"])
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 2.5
    mix = nodes.new("ShaderNodeMixShader")
    material.node_tree.links.new(noise.outputs["Fac"], mix.inputs[0])
    material.node_tree.links.new(first.outputs["BSDF"], mix.inputs[1])
    material.node_tree.links.new(second.outputs["BSDF"], mix.inputs[2])
    material.node_tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
    nodes.new("ShaderNodeBsdfTransparent").name = "Unused Transparent"
    nodes.new("ShaderNodeGroup").name = "Unused Node Group"
    fingerprint_image = bpy.data.images.new("FingerprintGenerated", 16, 16, alpha=True)
    fingerprint_image.pixels.foreach_set([0.125, 0.25, 0.5, 1.0] * (16 * 16))
    fingerprint_image.update()
    fingerprint_node = nodes.new("ShaderNodeTexImage")
    fingerprint_node.name = "Fingerprint Image"
    fingerprint_node.image = fingerprint_image
    mesh.materials.append(raw_material)
    obj = bpy.data.objects.new("BranchedCaveMesh", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (7.0, -4.0, 2.5)
    obj.scale.x = -1.0

    geometry = bpy.data.node_groups.new("CaveEvaluatedGeometry", "GeometryNodeTree")
    geometry.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    geometry.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_input = geometry.nodes.new("NodeGroupInput")
    group_output = geometry.nodes.new("NodeGroupOutput")
    set_material = geometry.nodes.new("GeometryNodeSetMaterial")
    set_material.inputs["Material"].default_value = material
    geometry.links.new(group_input.outputs["Geometry"], set_material.inputs["Geometry"])
    geometry.links.new(set_material.outputs["Geometry"], group_output.inputs["Geometry"])
    modifier = obj.modifiers.new("Evaluated Cave", "NODES")
    modifier.node_group = geometry

    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()
    return obj, (raw_material, material)


def _source_state(obj, materials) -> dict:
    return {
        "object_pointer": obj.as_pointer(),
        "mesh_pointer": obj.data.as_pointer(),
        "material_pointers": [material.as_pointer() for material in materials],
        "matrix_world": [float(value) for row in obj.matrix_world for value in row],
        "modifiers": [(item.name, item.type, item.as_pointer()) for item in obj.modifiers],
        "uv_layers": [item.name for item in obj.data.uv_layers],
        "material_nodes": {
            material.name: sorted((node.name, node.bl_idname) for node in material.node_tree.nodes)
            for material in materials
        },
        "material_tokens": {
            material.name: material.get("goldsrc_texture_token") for material in materials
        },
    }


def _evaluated_material_fixture(obj) -> dict:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        return {
            "slots": [material.name if material else None for material in mesh.materials],
            "polygon_indices": sorted({int(polygon.material_index) for polygon in mesh.polygons}),
            "polygons": len(mesh.polygons),
        }
    finally:
        evaluated.to_mesh_clear()


def _assert_prepared_material_mapping(prepared_object, audit) -> None:
    slots = [material for material in prepared_object.data.materials]
    indices = sorted({int(polygon.material_index) for polygon in prepared_object.data.polygons})
    if len(slots) != 1 or indices != [0]:
        raise RuntimeError(
            f"prepared material slots were not compacted: slots={[item.name for item in slots]} "
            f"indices={indices}"
        )
    source_materials = audit["source_evaluated"]["materials"]
    used = [item for item in source_materials if item["used"]]
    if len(used) != 1 or used[0]["slot"] != 1 or used[0]["material"]["name"] != "AutoTerrain_base":
        raise RuntimeError(f"wrong evaluated source material audit: {used}")
    mapping = audit["old_to_new"]
    if len(mapping) != 1 or mapping[0]["source_slot"] != 1 or mapping[0]["prepared_slot"] != 0:
        raise RuntimeError(f"wrong evaluated material remap: {mapping}")
    prepared_materials = audit["prepared"]["materials"]
    if len(prepared_materials) != 1 or prepared_materials[0]["token"] != slots[0].get("goldsrc_texture_token"):
        raise RuntimeError(f"prepared logical token audit mismatch: {prepared_materials}")


def _assert_prepared_winding(source, prepared_object) -> None:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        normal_matrix = evaluated.matrix_world.to_3x3().inverted_safe().transposed()
        expected = [(normal_matrix @ polygon.normal).normalized() for polygon in mesh.polygons]
    finally:
        evaluated.to_mesh_clear()
    actual = [polygon.normal.normalized() for polygon in prepared_object.data.polygons]
    if len(expected) != len(actual) or any(left.dot(right) < 0.99999 for left, right in zip(expected, actual)):
        raise RuntimeError(
            "negative-determinant freeze changed face orientation: "
            f"dots={[left.dot(right) for left, right in zip(expected, actual)]}"
        )


def _session_state() -> dict:
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    active = view_layer.objects.active
    viewports = []
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            viewports.append({
                "scene": window.scene.name,
                "shading": space.shading.type,
                "overlays": bool(space.overlay.show_overlays),
            })
    return {
        "filepath": bpy.data.filepath,
        "scene": scene.name,
        "frame": scene.frame_current,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "active": active.name if active else None,
        "selected": sorted(obj.name for obj in view_layer.objects if obj.select_get()),
        "active_action": (
            active.animation_data.action.name
            if active and active.animation_data and active.animation_data.action else None
        ),
        "viewports": viewports,
    }


def main() -> dict:
    artifacts = Path(os.environ["GOLDSRC_STATIC_FIXTURE"]).expanduser().resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    texture_size = int(os.environ.get("GOLDSRC_STATIC_TEXTURE_SIZE", "2048"))
    goldsrc_modes = ["masked"] if os.environ.get("GOLDSRC_STATIC_ALPHA_FIXTURE") == "1" else None
    source, materials = _source_scene()
    api = _runtime()
    source_before = _source_state(source, materials)
    evaluated_fixture = _evaluated_material_fixture(source)
    if evaluated_fixture["slots"] != ["CaveRock_Mat", "AutoTerrain_base"]:
        raise RuntimeError(f"fixture did not create the expected evaluated material slots: {evaluated_fixture}")
    if evaluated_fixture["polygon_indices"] != [1]:
        raise RuntimeError(f"fixture did not assign evaluated slot 1 to every polygon: {evaluated_fixture}")
    counts_before = {
        "objects": len(bpy.data.objects),
        "collections": len(bpy.data.collections),
        "materials": len(bpy.data.materials),
        "images": len(bpy.data.images),
        "actions": len(bpy.data.actions),
    }
    analysis = api.analyze_selected_static()
    if analysis["summary"]["transparent_materials"]:
        raise RuntimeError(
            "unconnected Transparent or Node Group nodes changed active Surface semantics: "
            f"{analysis['summary']['transparent_materials']}"
        )
    undecided = api.export_selected_static(
        artifacts_dir=str(artifacts),
        model_name="branched_cave_2k.mdl",
        request="Export the selected object as an MDL with a 2K texture and no baked lighting.",
    )
    counts_after_undecided = {
        "objects": len(bpy.data.objects),
        "collections": len(bpy.data.collections),
        "materials": len(bpy.data.materials),
        "images": len(bpy.data.images),
        "actions": len(bpy.data.actions),
    }
    if undecided.get("status") != "needs_decision" or counts_after_undecided != counts_before:
        raise RuntimeError(
            f"missing decisions changed the Scene: result={undecided} "
            f"before={counts_before} after={counts_after_undecided}"
        )
    source.location.x += 1.0
    bpy.context.view_layer.update()
    try:
        api.prepare_static_export(
            analysis["analysis_id"],
            artifacts_dir=str(artifacts),
            model_name="branched_cave_2k.mdl",
            request="Export the selected object as an MDL with a 2K texture and no baked lighting.",
            texture_size=texture_size,
            uv_strategy="smart_project",
            origin_strategy="source_origin",
            bake_mode="unlit_color",
            goldsrc_modes=goldsrc_modes,
        )
    except Exception as exc:
        if getattr(exc, "code", None) != "static.analysis_stale":
            raise
    else:
        raise RuntimeError("stale static analysis was accepted after the source changed")
    source.location.x -= 1.0
    bpy.context.view_layer.update()
    fingerprint_image = bpy.data.images["FingerprintGenerated"]
    pixel_index = 137
    original_pixel = float(fingerprint_image.pixels[pixel_index])
    fingerprint_image.pixels[pixel_index] = 0.875 if original_pixel < 0.5 else 0.125
    fingerprint_image.update()
    try:
        api.prepare_static_export(
            analysis["analysis_id"],
            artifacts_dir=str(artifacts),
            model_name="branched_cave_2k.mdl",
            request="Export the selected object as an MDL with a 2K texture and no baked lighting.",
            texture_size=texture_size,
            uv_strategy="smart_project",
            origin_strategy="source_origin",
            bake_mode="unlit_color",
            goldsrc_modes=goldsrc_modes,
        )
    except Exception as exc:
        if getattr(exc, "code", None) != "static.analysis_stale":
            raise
    else:
        raise RuntimeError("generated-image pixel mutation did not invalidate static analysis")
    finally:
        fingerprint_image.pixels[pixel_index] = original_pixel
        fingerprint_image.update()
    analysis = api.analyze_selected_static()
    if os.environ.get("GOLDSRC_STATIC_EXPECT_AUDIT_FAILURE") == "1":
        prepared = api.prepare_static_export(
            analysis["analysis_id"],
            artifacts_dir=str(artifacts),
            model_name="branched_cave_2k.mdl",
            request="Export the selected object as an MDL with a 2K texture and no baked lighting.",
            texture_size=texture_size,
            uv_strategy="smart_project",
            origin_strategy="source_origin",
            bake_mode="unlit_color",
            goldsrc_modes=goldsrc_modes,
        )
        prepared_object = bpy.data.objects[prepared["prepared"]["object"]]
        prepared_object.data.materials.append(materials[0])
        for polygon in prepared_object.data.polygons:
            polygon.material_index = 1
        prepared_object.data.update()
        failed = api.execute_pipeline(
            prepared["contract_path"], prepared["artifacts_dir"],
            assurance="standard", preserve_author_session=True,
            visual_compare=False,
        )
        preflight = json.loads(
            (artifacts / "reports" / "preflight.json").read_text(encoding="utf-8")
        )
        issue_codes = {item.get("code") for item in preflight.get("issues", [])}
        if failed.get("status") != "fail" or failed.get("failed_stage") != "PREFLIGHT":
            raise RuntimeError(f"tampered prepared material mapping was accepted: {failed}")
        if "static.evaluated_material_mapping" not in issue_codes:
            raise RuntimeError(f"tampered mapping failed for the wrong reason: {preflight}")
        if _source_state(source, materials) != source_before:
            raise RuntimeError("tampered-audit regression changed the source asset")
        summary = {
            "status": "pass",
            "expected_failure": True,
            "failed_stage": failed["failed_stage"],
            "issue_codes": sorted(issue_codes),
            "source_unchanged": True,
        }
        (artifacts / "static_fixture_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        print("GOLDSRC_STATIC_FIXTURE", json.dumps(summary, sort_keys=True))
        return summary
    if os.environ.get("GOLDSRC_STATIC_EXPECT_SOURCE_AUDIT_FAILURE") == "1":
        prepared = api.prepare_static_export(
            analysis["analysis_id"],
            artifacts_dir=str(artifacts),
            model_name="branched_cave_2k.mdl",
            request="Export the selected object as an MDL with a 2K texture and no baked lighting.",
            texture_size=texture_size,
            uv_strategy="smart_project",
            origin_strategy="source_origin",
            bake_mode="unlit_color",
            goldsrc_modes=goldsrc_modes,
        )
        original_coordinate = source.data.vertices[0].co.copy()
        source.data.vertices[0].co.z += 0.25
        source.data.update()
        bpy.context.view_layer.update()
        try:
            failed = api.execute_pipeline(
                prepared["contract_path"], prepared["artifacts_dir"],
                assurance="standard", preserve_author_session=True,
                visual_compare=False,
            )
            preflight = json.loads(
                (artifacts / "reports" / "preflight.json").read_text(encoding="utf-8")
            )
        finally:
            source.data.vertices[0].co = original_coordinate
            source.data.update()
            bpy.context.view_layer.update()
        failures = preflight.get("facts", {}).get("static_material_audit", {}).get("failures", [])
        if failed.get("status") != "fail" or failed.get("failed_stage") != "PREFLIGHT":
            raise RuntimeError(f"source geometry mutation was accepted: {failed}")
        if not any(item.get("reason") == "geometry_signature_changed" for item in failures):
            raise RuntimeError(f"source geometry mutation failed for the wrong reason: {preflight}")
        summary = {
            "status": "pass", "expected_source_failure": True,
            "failed_stage": failed["failed_stage"], "failures": failures,
        }
        (artifacts / "static_fixture_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        print("GOLDSRC_STATIC_FIXTURE", json.dumps(summary, sort_keys=True))
        return summary
    if os.environ.get("GOLDSRC_STATIC_PREPARE_ONLY") == "1":
        prepared = api.prepare_static_export(
            analysis["analysis_id"],
            artifacts_dir=str(artifacts),
            model_name="branched_cave_2k.mdl",
            request="Export the selected object as an MDL with a 2K texture and no baked lighting.",
            texture_size=texture_size,
            uv_strategy="smart_project",
            origin_strategy="source_origin",
            bake_mode="unlit_color",
            goldsrc_modes=goldsrc_modes,
        )
        if prepared.get("status") != "pass":
            raise RuntimeError(f"static preparation failed: {prepared}")
        prepared_object = bpy.data.objects[prepared["prepared"]["object"]]
        if _source_state(source, materials) != source_before:
            raise RuntimeError("static preparation changed the source asset")
        if prepared_object is source or prepared_object.data is source.data:
            raise RuntimeError("prepared static mesh aliases the source object or mesh")
        if prepared_object.data.uv_layers.active.name != prepared["prepared"]["uv_layer"]:
            raise RuntimeError("prepared static mesh did not activate its contract UV")
        if any(modifier.type == "NODES" for modifier in prepared_object.modifiers):
            raise RuntimeError("prepared mesh retained the source Geometry Nodes modifier")
        _assert_prepared_material_mapping(
            prepared_object, prepared["prepared"]["material_audit"],
        )
        _assert_prepared_winding(source, prepared_object)
        summary = {
            "status": "pass",
            "analysis": analysis,
            "undecided": undecided,
            "prepared": prepared,
            "source_unchanged": True,
            "evaluated_material_fixture": evaluated_fixture,
        }
        (artifacts / "static_fixture_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        print("GOLDSRC_STATIC_FIXTURE", json.dumps(summary, sort_keys=True))
        return summary

    session_before = _session_state()
    preserve_session = os.environ.get("GOLDSRC_STATIC_PRESERVE_SESSION", "1") != "0"
    visual_compare = os.environ.get("GOLDSRC_STATIC_VISUAL_COMPARE", "1") != "0"
    assurance = os.environ.get("GOLDSRC_STATIC_ASSURANCE", "strict")
    result = api.export_selected_static(
        artifacts_dir=str(artifacts),
        model_name="branched_cave_2k.mdl",
        request="Export the selected object as an MDL with a 2K texture and no baked lighting.",
        texture_size=texture_size,
        uv_strategy="smart_project",
        origin_strategy="source_origin",
        bake_mode="unlit_color",
        goldsrc_modes=goldsrc_modes,
        assurance=assurance,
        preserve_author_session=preserve_session,
        visual_compare=visual_compare,
    )
    session_after = _session_state()
    if result.get("status") != "pass":
        raise RuntimeError(f"selected-static export failed: {result}")
    if result.get("facts", {}).get("material_mapping_audit") != "pass":
        raise RuntimeError(f"selected-static material mapping audit is absent: {result}")
    for field in ("author_triangles", "crossed_tile_triangles", "post_tile_triangles"):
        if field not in result.get("facts", {}):
            raise RuntimeError(f"selected-static summary is missing {field}: {result}")
    if preserve_session and session_after != session_before:
        raise RuntimeError(f"selected-static export changed author context: before={session_before} after={session_after}")
    source_after = _source_state(source, materials)
    if source_after != source_before:
        raise RuntimeError(f"source asset changed: before={source_before} after={source_after}")
    prepared_collections = [
        collection for collection in bpy.data.collections
        if collection.get("goldsrc_static_prepared")
    ]
    if len(prepared_collections) != 1:
        raise RuntimeError(f"expected one prepared collection, got {[item.name for item in prepared_collections]}")
    prepared_meshes = [obj for obj in prepared_collections[0].objects if obj.type == "MESH"]
    if len(prepared_meshes) != 1:
        raise RuntimeError(f"expected one prepared mesh, got {[item.name for item in prepared_meshes]}")
    prepared_object = prepared_meshes[0]
    if prepared_object is source or prepared_object.data is source.data:
        raise RuntimeError("prepared static mesh aliases the source object or mesh")
    if prepared_object.data.uv_layers.active.name != "GoldSrcUV":
        raise RuntimeError("prepared static mesh did not activate the generated GoldSrcUV")
    if any(modifier.type == "NODES" for modifier in prepared_object.modifiers):
        raise RuntimeError("prepared mesh retained the source Geometry Nodes modifier")
    contract_paths = sorted((artifacts / "contracts").glob("*.json"))
    if len(contract_paths) != 1:
        raise RuntimeError(f"expected one static contract, got {contract_paths}")
    contract = json.loads(contract_paths[0].read_text(encoding="utf-8"))
    _assert_prepared_material_mapping(
        prepared_object, contract["static_material_audit"],
    )
    _assert_prepared_winding(source, prepared_object)
    preflight = json.loads((artifacts / "reports" / "preflight.json").read_text(encoding="utf-8"))
    export = json.loads((artifacts / "reports" / "export.json").read_text(encoding="utf-8"))
    if preflight["facts"]["static_material_audit"]["status"] != "pass":
        raise RuntimeError("preflight evaluated material audit did not pass")
    export_audits = [item.get("static_material_audit") for item in export["references"]]
    if not export_audits or any(item is None or item.get("status") != "pass" for item in export_audits):
        raise RuntimeError(f"export evaluated material audit did not pass: {export_audits}")
    summary = {
        "status": "pass",
        "analysis": analysis,
        "undecided": undecided,
        "result": result,
        "source_unchanged": True,
        "author_session_preserved": preserve_session,
        "evaluated_material_fixture": evaluated_fixture,
    }
    (artifacts / "static_fixture_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    print("GOLDSRC_STATIC_FIXTURE", json.dumps(summary, sort_keys=True))
    return summary


if __name__ == "__main__":
    main()
