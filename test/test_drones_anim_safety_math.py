"""Checks the independent safety calculations without importing Blender."""

import importlib.util
import random
import sys
import types
import unittest
from math import dist
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "dronesAnim" / "safety_math.py"
package = types.ModuleType("drones_anim_safety_test")
package.__path__ = [str(path.parent)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(
    "drones_anim_safety_test.safety_math", path
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Limits, Snapshot, evaluate_frame = module.Limits, module.Snapshot, module.evaluate_frame
Result, warning_messages = module.Result, module.warning_messages


class TestSafetyMath(unittest.TestCase):
    def test_empty_and_single_drone_have_no_pair(self):
        empty = evaluate_frame(Snapshot(1, {}, {}), Limits(), 24)
        single = evaluate_frame(
            Snapshot(1, {"a": (0, 0, 3)}, {"a": 0}), Limits(), 24
        )
        self.assertIsNone(empty.min_distance)
        self.assertIsNone(single.min_distance)

    def test_proximity_filter_and_full_pairs(self):
        positions = {
            "a": (0, 0, 3),
            "b": (1, 0, 3),
            "c": (2, 0, 3),
            "ground": (0, 0, 2.49),
        }
        frame = Snapshot(1, positions, {})
        result = evaluate_frame(frame, Limits(), 24, all_pairs=True)
        self.assertEqual(len(result.close_pairs), 3)
        self.assertEqual(result.warnings["proximity"], {"a", "b", "c"})
        all_drones = evaluate_frame(
            frame, Limits(proximity_target="ALL"), 24, all_pairs=True
        )
        self.assertEqual(len(all_drones.close_pairs), 6)

    def test_coincident_drones_warn_and_short_series_has_no_derivatives(self):
        frame = Snapshot(
            9, {"a": (0, 0, 3), "b": (0, 0, 3)}, {"a": 0, "b": 0}
        )
        result = evaluate_frame(frame, Limits(), 24)
        self.assertEqual(result.min_distance, 0.0)
        self.assertEqual(result.warnings["proximity"], {"a", "b"})
        self.assertIsNone(result.max_velocity_xy)

    def test_full_check_includes_pairs_within_threshold_tolerance(self):
        frame = Snapshot(
            1, {"a": (0, 0, 3), "b": (2.995, 0, 3)}, {}
        )
        result = evaluate_frame(frame, Limits(), 24, all_pairs=True)
        self.assertEqual(result.close_pairs, [("a", "b")])

    def test_velocity_acceleration_navigation_altitude_and_yaw_wrap(self):
        from math import radians

        first = Snapshot(1, {"a": (0, 0, 1)}, {"a": radians(179)})
        second = Snapshot(2, {"a": (1, 0, 1)}, {"a": radians(-179)})
        third = Snapshot(3, {"a": (3, 0, 1)}, {"a": radians(-177)})
        result = evaluate_frame(
            third, Limits(proximity_target="ALL", max_yaw_rate=15), 10, second, first
        )
        self.assertEqual(result.max_velocity_xy, 20)
        self.assertEqual(result.max_acceleration, 100)
        self.assertAlmostEqual(result.max_yaw_rate, 20)
        self.assertEqual(
            set(result.warnings), {"altitude", "velocity", "acceleration", "yaw"}
        )

    def test_nonconsecutive_frames_do_not_invent_velocity(self):
        old = Snapshot(1, {"a": (0, 0, 0)}, {})
        current = Snapshot(8, {"a": (100, 0, 0)}, {})
        result = evaluate_frame(current, Limits(), 24, previous=old)
        self.assertIsNone(result.max_velocity_xy)

    def test_sorted_nearest_pair_matches_brute_force(self):
        points = {
            str(index): (random.Random(index * 17 + 1).random(),
                         random.Random(index * 17 + 2).random(),
                         random.Random(index * 17 + 3).random())
            for index in range(60)
        }
        result = evaluate_frame(
            Snapshot(1, points, {}), Limits(proximity_target="ALL"), 24
        )
        expected = min(
            dist(a, b) for i, a in enumerate(points.values())
            for b in list(points.values())[i + 1:]
        )
        self.assertAlmostEqual(result.min_distance, expected)

    def test_proximity_matches_brute_force_for_varied_formations(self):
        rng = random.Random(73)
        formations = [
            [(0.0, i * 0.7, 3.0) for i in range(140)],
            [(i * 0.7, 0.0, 3.0) for i in range(140)],
            [(0.0, 0.0, i * 0.7) for i in range(140)],
            [(rng.uniform(-8, 8), rng.uniform(-8, 8), rng.uniform(-8, 8))
             for _ in range(140)],
            [(0.0, 0.0, 3.0)] * 110,
        ]
        for formation in formations:
            with self.subTest(formation=formation[:2]):
                points = {str(i): point for i, point in enumerate(formation)}
                result = evaluate_frame(
                    Snapshot(1, points, {}),
                    Limits(proximity_target="ALL", proximity=3.0),
                    24,
                )
                expected_pairs = {
                    frozenset((str(i), str(j)))
                    for i, point in enumerate(formation)
                    for j in range(i + 1, len(formation))
                    if dist(point, formation[j]) < 3.0
                }
                self.assertEqual(
                    {frozenset(pair) for pair in result.close_pairs}, expected_pairs
                )
                self.assertEqual(len(result.close_pairs), len(expected_pairs))
                self.assertEqual(
                    result.warnings.get("proximity", set()),
                    set().union(*expected_pairs),
                )
                self.assertAlmostEqual(
                    result.min_distance,
                    min(dist(point, formation[j])
                        for i, point in enumerate(formation)
                        for j in range(i + 1, len(formation))),
                )

    def test_strict_threshold_and_closest_only_mode(self):
        positions = {
            "a": (0.0, 0.0, 3.0),
            "b": (3.0, 0.0, 3.0),
            "c": (5.0, 0.0, 3.0),
        }
        snapshot = Snapshot(1, positions, {})
        result = evaluate_frame(snapshot, Limits(), 24)
        self.assertEqual(result.close_pairs, [("b", "c")])
        closest_only = evaluate_frame(snapshot, Limits(), 24, all_pairs=False)
        self.assertEqual(closest_only.close_pairs, [])
        self.assertEqual(closest_only.warnings["proximity"], {"b", "c"})
        disabled = evaluate_frame(
            snapshot, Limits(proximity_enabled=False), 24
        )
        self.assertEqual(disabled.min_distance, 2.0)
        self.assertEqual(disabled.close_pairs, [])
        self.assertNotIn("proximity", disabled.warnings)
        zero = evaluate_frame(snapshot, Limits(proximity=0.0), 24)
        self.assertEqual(zero.close_pairs, [])
        self.assertNotIn("proximity", zero.warnings)

    def test_closest_pair_crosses_divide_and_conquer_split(self):
        positions = {str(i): (i * 10.0, 0.0, 3.0) for i in range(140)}
        positions["70"] = (690.25, 0.0, 3.0)
        result = evaluate_frame(Snapshot(1, positions, {}), Limits(), 24)
        self.assertEqual(result.closest_pair, ("69", "70"))
        self.assertEqual(result.min_distance, 0.25)
        self.assertEqual(result.close_pairs, [("69", "70")])

    def test_warning_messages_include_each_active_category(self):
        result = Result(
            frame=1,
            drone_count=3,
            min_distance=1.0,
            min_altitude=1.0,
            max_altitude=151.0,
            max_velocity_xy=11.0,
            max_velocity_z_up=3.0,
            max_velocity_z_down=0.0,
            max_acceleration=5.0,
            max_yaw_rate=35.0,
            warnings={
                category: {"a"} for category in
                ("proximity", "altitude", "velocity", "acceleration", "yaw")
            },
        )
        messages = warning_messages(result, Limits())
        self.assertEqual(
            [category for category, _ in messages],
            ["proximity", "altitude", "velocity", "velocity", "acceleration", "yaw"],
        )
        self.assertIn("1.00 m < 3.00 m", messages[0][1])
        self.assertIn("上升 3.00/2.00", messages[3][1])
        self.assertEqual(warning_messages(Result(1, 0), Limits()), [])


if __name__ == "__main__":
    unittest.main()
