"""Synthetic geometry contracts at the MotionHistory interface, the worker's deadline gate and
its use by the deadline engine.

Ported from IRL Shield tests/test_motion.py and tests/test_motion_worker.py (MIT); the engine
tests use the multi-lane ``DeadlineEngine`` API.
"""

import dataclasses
import threading
import time
import unittest

import cv2
import numpy as np

from contracts import Lane
from engine.deadline import DeadlineEngine
from engine.motion import IdentityMotion, MotionHistory, MotionWorker
from engine.testing import lane_result, make_frame

FAST, ACC = Lane.FAST, Lane.ACCURATE


def scene(seed=7, width=640, height=400):
    random = np.random.default_rng(seed)
    image = np.full((height, width, 3), 35, np.uint8)
    for _ in range(180):
        x, y = random.integers(18, [width - 18, height - 18])
        radius = int(random.integers(3, 9))
        shade = int(random.integers(80, 255))
        cv2.circle(image, (int(x), int(y)), radius, (shade, shade, shade), -1)
    cv2.putText(image, "SYNTHETIC 1234", (50, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (240, 240, 240), 2)
    return image


class MotionGeometry(unittest.TestCase):
    def test_batch_projects_all_quads_and_empty_receipts_still_require_a_connected_path(self):
        frame = scene()
        moved = cv2.warpAffine(frame, np.float32([[1, 0, 14], [0, 1, -8]]), (640, 400))
        history = MotionHistory(max_frames=3, analysis_width=320)
        history.add(0, frame)
        history.add(1, moved)
        quads = [
            [[100, 70], [300, 70], [300, 190], [100, 190]],
            [[-30, 20], [50, 20], [50, 45], [-30, 45]],
        ]
        original = np.asarray(quads).copy()
        expected = original + [14, -8]
        projected = history.project_many(quads, 0, 1)
        np.testing.assert_allclose(projected, expected, atol=1.5)
        np.testing.assert_allclose(history.project_many(projected, 1, 0), original, atol=1e-6)
        np.testing.assert_array_equal(quads, original)
        projected[0][0][0] = -9999
        np.testing.assert_allclose(history.project_many(quads, 0, 1), expected, atol=1.5)
        self.assertEqual(history.project_many([], 0, 1), [])
        self.assertIsNone(history.project_many([], 0, 99))
        history.add(2, scene(seed=777))
        self.assertIsNone(history.project_many([], 0, 2))
        self.assertIsNone(history.project_many(quads, 0, 2))
        history.add(3, scene(seed=777))
        self.assertIsNone(history.project_many([], 0, 3))
        self.assertEqual(history.project_many([], 2, 3), [])
        with self.assertRaises(ValueError):
            history.project_many(quads + [[[0, 0], [1, 0], [float("nan"), 1], [0, 1]]], 2, 3)

    def test_composes_translation_and_projects_back_in_original_pixels(self):
        frame = scene()
        history = MotionHistory(analysis_width=320)
        history.add(0, frame)
        history.add(1, cv2.warpAffine(frame, np.float32([[1, 0, 14], [0, 1, -8]]), (640, 400)))
        history.add(2, cv2.warpAffine(frame, np.float32([[1, 0, 24], [0, 1, -2]]), (640, 400)))
        quad = [[100, 70], [300, 70], [300, 190], [100, 190]]
        expected = [[124, 68], [324, 68], [324, 188], [124, 188]]
        np.testing.assert_allclose(history.project(quad, 0, 2), expected, atol=1.5)
        np.testing.assert_allclose(history.project(expected, 2, 0), quad, atol=1.5)
        self.assertTrue(history.diagnostics(2)["valid"])

    def test_perspective_preserves_vertex_order_across_frame_resolutions(self):
        frame = scene(seed=11, width=800, height=480)
        perspective = np.array([[1.02, 0.08, 18], [-0.035, 0.97, 14], [0.00018, -0.00011, 1]])
        ground_truth = np.diag([1.25, 1.25, 1.0]) @ perspective
        target = cv2.warpPerspective(frame, ground_truth, (1000, 600))
        quad = np.array([[150, 100], [470, 100], [470, 300], [150, 300]], dtype=float)
        homogeneous = np.column_stack([quad, np.ones(4)]) @ ground_truth.T
        expected = homogeneous[:, :2] / homogeneous[:, 2:]
        history = MotionHistory(analysis_width=320)
        history.add(10, frame)
        history.add(11, target)
        projected = history.project(quad.tolist(), 10, 11)
        self.assertIsNotNone(projected)
        np.testing.assert_allclose(projected, expected, atol=3.0)
        np.testing.assert_allclose(history.project(expected.tolist(), 11, 10), quad, atol=3.0)

    def test_cut_and_texture_guards_do_not_fabricate_valid_motion(self):
        quad = [[100, 70], [300, 70], [300, 190], [100, 190]]
        history = MotionHistory()
        history.add(0, scene(seed=7))
        replacement = scene(seed=777)
        history.add(1, replacement)
        self.assertIsNone(history.project(quad, 0, 1))
        self.assertFalse(history.diagnostics(1)["valid"])
        self.assertTrue(history.diagnostics(1)["burst"])
        self.assertEqual(history.diagnostics(1)["confidence"], 0.0)
        history.add(2, replacement.copy())
        self.assertIsNone(history.project(quad, 0, 2))
        np.testing.assert_allclose(history.project(quad, 1, 2), quad, atol=1e-6)
        self.assertTrue(history.diagnostics(2)["valid"])

        empty = MotionHistory(mode="phase")
        empty.add(0, np.zeros((400, 640, 3), np.uint8))
        empty.add(1, np.zeros((400, 640, 3), np.uint8))
        self.assertIsNone(empty.project(quad, 0, 1))
        self.assertFalse(empty.diagnostics(1)["valid"])
        self.assertEqual(empty.diagnostics(1)["confidence"], 0.0)
        self.assertEqual(empty.project(quad, 1, 1), quad)

        phase = MotionHistory(mode="phase")
        phase.add(0, replacement)
        phase.add(1, cv2.warpAffine(replacement, np.float32([[1, 0, 8], [0, 1, 2]]), (640, 400)))
        expected = np.asarray(quad) + [8, 2]
        np.testing.assert_allclose(phase.project(quad, 0, 1), expected, atol=1.5)

    def test_configured_phase_threshold_can_track_occlusion_without_accepting_a_cut(self):
        frame = scene()
        moved = cv2.warpAffine(frame, np.float32([[1, 0, 8], [0, 1, 2]]), (640, 400))
        moved[:, :512] = 35
        quad = [[540, 70], [610, 70], [610, 190], [540, 190]]
        strict = MotionHistory(mode="phase")
        strict.add(0, frame)
        strict.add(1, moved)
        self.assertIsNone(strict.project(quad, 0, 1))
        tuned = MotionHistory(mode="phase", phase_min_response=0.2)
        tuned.add(0, frame)
        tuned.add(1, moved)
        np.testing.assert_allclose(tuned.project(quad, 0, 1), np.asarray(quad) + [8, 2], atol=1.5)
        tuned.add(2, scene(seed=777))
        self.assertIsNone(tuned.project(quad, 1, 2))
        for value in [float("nan"), -0.1, 1.1]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                MotionHistory(mode="phase", phase_min_response=value)

    def test_missing_and_evicted_frames_cannot_reuse_a_transform(self):
        frame = scene()
        quad = [[100, 70], [300, 70], [300, 190], [100, 190]]
        history = MotionHistory(max_frames=3)
        for index in range(5):
            moved = cv2.warpAffine(frame, np.float32([[1, 0, 4 * index], [0, 1, 2 * index]]), (640, 400))
            history.add(index, moved)
        self.assertIsNone(history.project(quad, 0, 4))
        self.assertFalse(history.diagnostics(0)["valid"])
        self.assertIsNone(history.project(quad, 2, 99))
        expected = np.asarray(quad) + [8, 4]
        np.testing.assert_allclose(history.project(quad, 2, 4), expected, atol=1.5)

        gap = MotionHistory()
        gap.add(10, frame)
        gap.add(12, frame.copy())
        self.assertIsNone(gap.project(quad, 10, 12))
        self.assertFalse(gap.diagnostics(12)["valid"])
        gap.add(13, frame.copy())
        self.assertIsNone(gap.project(quad, 10, 13))
        np.testing.assert_allclose(gap.project(quad, 12, 13), quad, atol=1e-6)

    def test_reset_forgets_everything_so_a_new_session_can_restart_indices(self):
        frame = scene()
        history = MotionHistory(mode="phase")
        history.add(0, frame)
        history.add(1, frame.copy())
        history.reset()
        self.assertIsNone(history.project_many([], 0, 1))
        history.add(0, frame)  # would raise "indices must increase" without the reset
        self.assertEqual(history.project_many([], 0, 0), [])


class IdentityMotionModel(unittest.TestCase):
    def test_identity_projects_unchanged_copies_and_rejects_nonfinite_deadlines(self):
        motion = IdentityMotion()
        quad = [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]]
        projected = motion.project_many([quad], 0, 9, deadline=10.0)
        self.assertEqual(projected, [quad])
        projected[0][0][0] = -1
        self.assertEqual(quad[0][0], 1.0)
        self.assertEqual(motion.project_many([], 3, 3), [])
        self.assertIsNone(motion.project_many([quad], 0, 1, deadline=float("nan")))


class MotionScheduling(unittest.TestCase):
    def test_batch_and_empty_receipts_reject_a_late_interior_motion_frame(self):
        completed = threading.Event()
        stamps = iter([100.0, 100.2, 100.0])
        calls = [0]

        def clock():
            stamp = next(stamps)
            calls[0] += 1
            if calls[0] == 3:
                completed.set()
            return stamp

        worker = MotionWorker(MotionHistory(mode="phase"), clock=clock)
        source = np.random.default_rng(33).integers(0, 256, (32, 32, 3), dtype=np.uint8)
        quad = [[4, 4], [20, 4], [20, 16], [4, 16]]
        try:
            for index in range(3):
                worker.add(index, source)
            self.assertTrue(completed.wait(1))
            projected = worker.project_many([quad], 0, 0, deadline=100.1)
            self.assertEqual(projected, [quad])
            projected[0][0][0] = -9999
            self.assertEqual(worker.project_many([quad], 0, 0, deadline=100.1), [quad])
            self.assertEqual(worker.project_many([], 0, 0, deadline=100.1), [])
            self.assertIsNone(worker.project_many([quad], 0, 2, deadline=100.1))
            self.assertIsNone(worker.project_many([], 0, 2, deadline=100.1))
            self.assertIsNone(worker.project_many([], 2, 0, deadline=100.1))
            self.assertIsNone(worker.project(quad, 0, 2, deadline=100.1))
            self.assertIsNone(worker.project_many([], 0, 1000000, deadline=100.1))
            self.assertIsNone(worker.project_many([], 0, 0, deadline=float("nan")))
        finally:
            worker.close()
        self.assertFalse(worker.stats["running"])

    def test_worker_reset_drops_state_and_accepts_restarted_indices(self):
        source = np.random.default_rng(5).integers(0, 256, (32, 32, 3), dtype=np.uint8)
        worker = MotionWorker(MotionHistory(mode="phase"))
        try:
            for index in range(3):
                worker.add(index, source)
            self._wait(worker, 3)
            worker.reset()
            self.assertIsNone(worker.project_many([], 0, 0, deadline=time.monotonic() + 1))
            worker.add(0, source)
            self._wait(worker, 4)
            self.assertEqual(worker.project_many([], 0, 0, deadline=time.monotonic() + 1), [])
            self.assertEqual(worker.stats["errors"], [])
        finally:
            worker.close()

    @staticmethod
    def _wait(worker, count):
        for _ in range(400):
            if worker.stats["completed"] >= count:
                return
            time.sleep(0.005)
        raise AssertionError("Motion worker did not complete the controlled operation")

    def test_motion_completed_after_deadline_cannot_retroactively_allow_live(self):
        entered, release = threading.Event(), threading.Event()
        clock = [100.0]

        class HeldSecond(MotionHistory):
            def add(self, index, pixels):
                if index == 1:
                    entered.set()
                    if not release.wait(1):
                        raise TimeoutError("Late motion probe was not released")
                super().add(index, pixels)

        worker = MotionWorker(HeldSecond(mode="phase"), clock=lambda: clock[0])
        engine = DeadlineEngine((FAST, ACC), fps=30, delay_seconds=0.1, motion=worker)
        for lane in (FAST, ACC):
            engine.submit_result(lane_result(lane, 0, 100.01))
        source = np.random.default_rng(29).integers(0, 256, (32, 32, 3), dtype=np.uint8)
        frames = [dataclasses.replace(make_frame(i, t0=100.0), image=source) for i in range(2)]
        try:
            engine.submit(frames[0], now=100)
            self._wait(worker, 1)
            engine.submit(frames[1], now=100 + 1 / 30)
            self.assertTrue(entered.wait(1))
            clock[0] = 100.2
            release.set()
            self._wait(worker, 2)
            engine.end(1)
            first, second = engine.poll(now=100.2)
            self.assertEqual(first.state, "LIVE")
            self.assertEqual((second.state, second.reason), ("PROTECT", "unresolved-motion-fast"))
        finally:
            release.set()
            engine.close()

    def test_stalled_motion_does_not_block_ingest_deadline_or_hold(self):
        entered, release = threading.Event(), threading.Event()

        class HeldHistory(MotionHistory):
            def add(self, index, pixels):
                entered.set()
                if not release.wait(1):
                    raise TimeoutError("Motion probe was not released")
                super().add(index, pixels)

        worker = MotionWorker(HeldHistory(mode="phase"), capacity=4)
        engine = DeadlineEngine((FAST, ACC), fps=30, delay_seconds=1, motion=worker)
        for lane in (FAST, ACC):
            engine.submit_result(lane_result(lane, 0, 10.1))
        source = np.random.default_rng(71).integers(0, 256, (32, 32, 3), dtype=np.uint8)
        try:
            engine.submit(dataclasses.replace(make_frame(0), image=source), now=10)
            self.assertTrue(entered.wait(1))
            result = engine.poll(now=11)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].state, "PROTECT")
            self.assertEqual(result[0].reason, "unresolved-motion-fast")
            engine.hold("hotkey", True, now=11)
        finally:
            release.set()
            engine.close()
        self.assertEqual(worker.stats["completed"], 1)
        self.assertFalse(worker.stats["running"])


if __name__ == "__main__":
    unittest.main()
