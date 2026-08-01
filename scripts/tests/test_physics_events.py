import copy
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from goldsrc_toolchain.physics_events import evaluate_event_chain, validate_physics_definition


def box(center, half=(0.45, 0.45, 0.45), axes=None, rotation_delta=0.0):
    return {
        "center": list(center),
        "location": list(center),
        "rotation_delta": rotation_delta,
        "obb": {
            "center": list(center),
            "axes": axes or [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "half_sizes": list(half),
        },
    }


def event_samples():
    frames = {}
    rock_positions = [(-2.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (-0.4, 0.0, 0.0), (-0.2, 0.8, 0.0), (0.0, 1.6, 0.0), (0.0, 2.4, 0.0)]
    fragment_positions = [(0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.0, 0.0, 1.5), (0.0, 0.0, 2.0), (0.0, 0.0, 2.5)]
    for frame in range(6):
        frames[frame] = {
            "rock": box(rock_positions[frame]),
            "wall": box((0.0, 0.0, 0.0), half=(0.5, 2.0, 2.0)),
            "fragment": box(fragment_positions[frame]),
        }
    return frames


def base_physics():
    return {
        "mode": "baked_event_chain",
        "simulation": {
            "max_frame": 5,
            "sample_step": 1,
            "contact_margin": 0.02,
            "translation_epsilon": 0.01,
            "rotation_epsilon": 0.01,
            "penetration_tolerance": 0.25,
        },
        "stages": [
            {
                "name": "impact",
                "trigger": {"type": "frame", "frame": 0},
                "release": ["rock"],
                "expected_motion_window": [1, 2],
            },
            {
                "name": "fracture",
                "depends_on": ["impact"],
                "trigger": {"type": "contact", "pair": ["rock", "wall"], "offset_frames": 1, "window": [1, 4]},
                "release": ["fragment"],
                "must_be_still_before": ["fragment"],
                "expected_motion_window": [3, 4],
            },
        ],
        "interactions": [
            {
                "name": "rock_deflects",
                "pair": ["rock", "wall"],
                "window": [1, 4],
                "response": "deflect",
                "min_direction_change_deg": 30,
            }
        ],
    }


class PhysicsDefinitionTests(unittest.TestCase):
    def test_rejects_stage_dependency_cycle(self):
        physics = base_physics()
        physics["stages"][0]["depends_on"] = ["fracture"]
        errors = validate_physics_definition(physics)
        self.assertTrue(any("cycle" in error for error in errors))

    def test_rejects_invalid_response_and_frame_window(self):
        physics = base_physics()
        physics["interactions"][0]["response"] = "bounce"
        physics["interactions"][0]["window"] = [4, 2]
        errors = validate_physics_definition(physics)
        self.assertTrue(any("response" in error for error in errors))
        self.assertTrue(any("window" in error for error in errors))

    def test_accepts_contact_observation_stage_with_participants(self):
        physics = base_physics()
        physics["stages"][1]["release"] = []
        physics["stages"][1]["participants"] = ["fragment"]
        self.assertEqual(validate_physics_definition(physics), [])

    def test_rejects_ambiguous_pair_and_pairs(self):
        physics = base_physics()
        physics["stages"][1]["trigger"]["pairs"] = [["rock", "wall"]]
        errors = validate_physics_definition(physics)
        self.assertTrue(any("exactly one of pair or pairs" in error for error in errors))

    def test_accepts_deflection_without_an_artistic_threshold(self):
        physics = base_physics()
        physics["interactions"][0].pop("min_direction_change_deg")
        self.assertEqual(validate_physics_definition(physics), [])


class PhysicsEvaluationTests(unittest.TestCase):
    def test_accepts_contact_trigger_and_deflection(self):
        report = evaluate_event_chain(base_physics(), event_samples(), final_report={"settled": True})
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(report["stages"][1]["contact_frame"], 2)
        self.assertEqual(report["stages"][1]["release"], ["fragment"])
        self.assertGreaterEqual(report["interactions"][0]["direction_change_deg"], 30)
        self.assertEqual(len(report["interactions"][0]["relative_velocity_before"]), 3)
        self.assertEqual(report["final_state"]["settled"], True)

    def test_rejects_early_fragment_motion(self):
        samples = event_samples()
        samples[2]["fragment"] = box((0.0, 0.0, 1.4))
        report = evaluate_event_chain(base_physics(), samples)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any(item["code"] == "physics.early_motion" for item in report["issues"]))

    def test_rejects_missing_contact(self):
        samples = event_samples()
        for frame in samples:
            samples[frame]["rock"] = box((-4.0, 0.0, 0.0))
        report = evaluate_event_chain(base_physics(), samples)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any(item["code"] == "physics.contact_missing" for item in report["issues"]))

    def test_rejects_weak_deflection(self):
        physics = copy.deepcopy(base_physics())
        physics["interactions"][0]["min_direction_change_deg"] = 120
        report = evaluate_event_chain(physics, event_samples())
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any(item["code"] == "physics.response_weak" for item in report["issues"]))

    def test_measures_deflection_without_promoting_it_to_a_target(self):
        physics = copy.deepcopy(base_physics())
        physics["interactions"][0].pop("min_direction_change_deg")
        report = evaluate_event_chain(physics, event_samples())
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertIsNone(report["interactions"][0]["min_direction_change_deg"])
        self.assertGreater(report["interactions"][0]["direction_change_deg"], 0)

    def test_rejects_unsettled_or_overlapping_final_capture(self):
        report = evaluate_event_chain(
            base_physics(),
            event_samples(),
            final_report={
                "settled": False,
                "kinematic_at_end": ["fragment"],
                "static_collision_audit": {"max_penetration": 0.5},
            },
        )
        self.assertEqual(report["status"], "fail")
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("physics.unsettled", codes)
        self.assertIn("physics.kinematic_at_end", codes)
        self.assertIn("physics.penetration", codes)

    def test_accepts_constraint_break_after_contact(self):
        physics = copy.deepcopy(base_physics())
        fracture = physics["stages"][1]
        fracture["release"] = []
        fracture.pop("must_be_still_before")
        fracture.pop("expected_motion_window")
        fracture["break_constraints"] = ["rock_joint"]
        fracture["expected_break_window"] = [3, 4]
        report = evaluate_event_chain(physics, event_samples(), constraint_breaks={"rock_joint": 3})
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(report["stages"][1]["constraint_breaks"]["rock_joint"], 3)

    def test_rejects_early_or_missing_constraint_break(self):
        physics = copy.deepcopy(base_physics())
        fracture = physics["stages"][1]
        fracture["release"] = []
        fracture.pop("must_be_still_before")
        fracture.pop("expected_motion_window")
        fracture["break_constraints"] = ["rock_joint"]
        early = evaluate_event_chain(physics, event_samples(), constraint_breaks={"rock_joint": 2})
        self.assertTrue(any(item["code"] == "physics.early_fracture" for item in early["issues"]))
        missing = evaluate_event_chain(physics, event_samples(), constraint_breaks={"rock_joint": None})
        self.assertTrue(any(item["code"] == "physics.constraint_not_broken" for item in missing["issues"]))

    def test_rejects_stationary_overlap_as_separation(self):
        physics = copy.deepcopy(base_physics())
        physics["stages"] = [{"name": "observe", "trigger": {"type": "frame", "frame": 0}, "participants": ["rock", "wall"]}]
        physics["interactions"] = [{"name": "not_separating", "pair": ["rock", "wall"], "window": [0, 5], "response": "separate", "min_direction_change_deg": 0}]
        samples = {frame: {"rock": box((0.0, 0.0, 0.0)), "wall": box((0.0, 0.0, 0.0))} for frame in range(6)}
        report = evaluate_event_chain(physics, samples)
        self.assertTrue(any(item["code"] == "physics.response_no_separation" for item in report["issues"]))

    def test_rejects_low_speed_jitter_as_deflection(self):
        physics = copy.deepcopy(base_physics())
        physics["simulation"]["min_response_speed"] = 0.05
        samples = event_samples()
        samples[1]["rock"] = box((-0.401, 0.0, 0.0))
        samples[2]["rock"] = box((-0.4, 0.0, 0.0))
        samples[3]["rock"] = box((-0.4, 0.001, 0.0))
        report = evaluate_event_chain(physics, samples)
        self.assertTrue(any(item["code"] == "physics.response_too_slow" for item in report["issues"]))

    def test_resolves_first_observed_candidate_pair(self):
        physics = copy.deepcopy(base_physics())
        trigger = physics["stages"][1]["trigger"]
        trigger["pairs"] = [["rock_alt", "wall"], ["rock", "wall"]]
        trigger.pop("pair")
        interaction = physics["interactions"][0]
        interaction["pairs"] = [["rock_alt", "wall"], ["rock", "wall"]]
        interaction.pop("pair")
        samples = event_samples()
        for frame in samples:
            samples[frame]["rock_alt"] = box((-8.0, 0.0, 0.0))
        report = evaluate_event_chain(physics, samples)
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(report["stages"][1]["resolved_pair"], ["rock", "wall"])
        self.assertEqual(report["interactions"][0]["resolved_pair"], ["rock", "wall"])


if __name__ == "__main__":
    unittest.main()
