"""Blender 5.2 regression for rigid-body world to pose-local transfer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from goldsrc_toolchain.rigidbody_bake import (
    apply_rigidbody_world_transform,
    audit_armature_rigidbody_transfer,
)


def main() -> dict:
    original_scene = bpy.context.window.scene
    scene = bpy.data.scenes.new("RigidBodyTransferRegression")
    bpy.context.window.scene = scene
    collection = scene.collection
    mesh_data = None
    armature_data = None
    action = None
    try:
        initial = Matrix.LocRotScale(
            Vector((1.25, -2.5, 3.75)),
            Euler((0.23, -0.31, 0.42), "XYZ").to_quaternion(),
            Vector((1.0, 1.0, 1.0)),
        )
        current = Matrix.LocRotScale(
            Vector((-3.0, 4.5, 1.2)),
            Euler((-0.47, 0.19, 1.1), "XYZ").to_quaternion(),
            Vector((1.0, 1.0, 1.0)),
        )
        local_vertices = [
            Vector((-0.7, -0.2, -0.3)),
            Vector((0.9, -0.1, -0.25)),
            Vector((0.15, 0.8, -0.1)),
            Vector((-0.2, 0.05, 1.1)),
        ]
        mesh_data = bpy.data.meshes.new("RigidBodyTransferMeshData")
        mesh_data.from_pydata([initial @ point for point in local_vertices], [], [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)])
        mesh = bpy.data.objects.new("RigidBodyTransferMesh", mesh_data)
        collection.objects.link(mesh)

        armature_data = bpy.data.armatures.new("RigidBodyTransferRigData")
        armature = bpy.data.objects.new("RigidBodyTransferRig", armature_data)
        collection.objects.link(armature)
        bpy.context.view_layer.objects.active = armature
        armature.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        root = armature_data.edit_bones.new("root")
        root.head = (0.0, 0.0, 0.0)
        root.tail = (0.0, 0.0, 1.0)
        piece = armature_data.edit_bones.new("piece")
        piece.head = (0.0, 0.0, 0.0)
        piece.tail = (0.0, 0.0, 0.4)
        piece.parent = root
        bpy.ops.object.mode_set(mode="OBJECT")
        armature.select_set(False)

        group = mesh.vertex_groups.new(name="piece")
        group.add(range(len(local_vertices)), 1.0, "REPLACE")
        modifier = mesh.modifiers.new("RigidBodyTransferArmature", "ARMATURE")
        modifier.object = armature
        armature.animation_data_create()
        action = bpy.data.actions.new("RigidBodyTransferAction")
        armature.animation_data.action = action
        pose = armature.pose.bones["piece"]
        previous = None
        for frame, matrix in ((0, initial), (1, current)):
            apply_rigidbody_world_transform(pose, initial, matrix, armature_world=armature.matrix_world)
            if previous is not None:
                pose.rotation_quaternion.make_compatible(previous)
            previous = pose.rotation_quaternion.copy()
            pose.keyframe_insert("location", frame=frame, group="piece")
            pose.keyframe_insert("rotation_quaternion", frame=frame, group="piece")
        report = audit_armature_rigidbody_transfer(
            scene,
            mesh,
            armature,
            action,
            {0: {"piece": initial}, 1: {"piece": current}},
            {0: 0, 1: 1},
            ["piece"],
            position_tolerance=0.00001,
        )
        if report["status"] != "pass":
            raise RuntimeError(json.dumps(report, indent=2))
        report["blender"] = bpy.app.version_string
        return report
    finally:
        bpy.context.window.scene = original_scene
        bpy.data.scenes.remove(scene)
        if action is not None and action.users == 0:
            bpy.data.actions.remove(action)
        if mesh_data is not None and mesh_data.users == 0:
            bpy.data.meshes.remove(mesh_data)
        if armature_data is not None and armature_data.users == 0:
            bpy.data.armatures.remove(armature_data)


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
