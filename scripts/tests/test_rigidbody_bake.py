from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from goldsrc_toolchain.action_curves import iter_action_fcurves, representative_frame_samples
from goldsrc_toolchain.physics_config import audit_constraint_topology, configure_rigidbody_world


class ActionCurveCompatibilityTests(unittest.TestCase):
    def test_samples_loop_quarters_to_avoid_neutral_pose_aliasing(self) -> None:
        self.assertEqual(representative_frame_samples((0.0, 180.0)), [0, 45, 90, 135, 180])
        self.assertEqual(representative_frame_samples((0.0, 2.0)), [0, 1, 2])

    def test_reads_legacy_direct_fcurves(self) -> None:
        curves = [SimpleNamespace(data_path="location")]
        action = SimpleNamespace(fcurves=curves)
        self.assertEqual(list(iter_action_fcurves(action)), curves)

    def test_reads_blender_52_channelbags(self) -> None:
        curves = [SimpleNamespace(data_path="rotation_quaternion")]
        action = SimpleNamespace(
            layers=[
                SimpleNamespace(
                    strips=[
                        SimpleNamespace(
                            channelbags=[SimpleNamespace(fcurves=curves)]
                        )
                    ]
                )
            ]
        )
        self.assertEqual(list(iter_action_fcurves(action)), curves)


class RigidBodyWorldSetupTests(unittest.TestCase):
    def test_initializes_gravity_and_cache_for_a_fresh_scene(self) -> None:
        cache = SimpleNamespace(frame_start=None, frame_end=None)
        world = SimpleNamespace(
            point_cache=cache,
            substeps_per_frame=1,
            solver_iterations=10,
            time_scale=0.5,
        )
        scene = SimpleNamespace(rigidbody_world=world, frame_start=None, frame_end=None, use_gravity=False)
        report = configure_rigidbody_world(
            scene,
            frame_start=0,
            frame_end=120,
            use_gravity=True,
            substeps_per_frame=16,
            solver_iterations=32,
            time_scale=1.0,
        )
        self.assertEqual([scene.frame_start, scene.frame_end], [0, 120])
        self.assertEqual([cache.frame_start, cache.frame_end], [0, 120])
        self.assertTrue(scene.use_gravity)
        self.assertEqual(report["substeps_per_frame"], 16)
        self.assertEqual(report["solver_iterations"], 32)


class ConstraintTopologyAuditTests(unittest.TestCase):
    def test_reports_components_without_rejecting_a_valid_topology(self) -> None:
        first = SimpleNamespace(name="first")
        second = SimpleNamespace(name="second")
        constraint = SimpleNamespace(
            rigid_body_constraint=SimpleNamespace(object1=first, object2=second)
        )
        report = audit_constraint_topology([first, second], [constraint])
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["component_count"], 1)
        self.assertTrue(report["advisory_only"])

    def test_warns_on_dense_island_but_does_not_make_it_a_hard_failure(self) -> None:
        objects = [SimpleNamespace(name=f"piece_{index}") for index in range(4)]
        constraints = [
            SimpleNamespace(
                object1=objects[0],
                object2=objects[index],
            )
            for index in range(1, 4)
        ]
        report = audit_constraint_topology(
            objects,
            constraints,
            degree_warning_threshold=2,
        )
        self.assertEqual(report["status"], "warn")
        self.assertTrue(any(item["code"] == "constraint.degree_dense" for item in report["warnings"]))
        self.assertTrue(report["advisory_only"])

if __name__ == "__main__":
    unittest.main()
