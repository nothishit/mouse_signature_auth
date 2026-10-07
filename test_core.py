"""Автотесты ядра и контроллера:  python -m unittest -v test_core"""

import os
import random
import tempfile
import unittest

import controller
import experiment as ex
import signature_core as core


def make_user(seed=1):
    rng = random.Random(seed)
    return ex.SyntheticUser(rng), rng


class TestPreprocessing(unittest.TestCase):
    def test_normalization_invariant_to_shift_and_scale(self):
        u, rng = make_user()
        s = ex.genuine(u, rng)
        moved = [[(x * 1.7 + 300, y * 1.7 - 50, t) for x, y, t in st] for st in s]
        a = core.normalize_shape(s)
        b = core.normalize_shape(moved)
        self.assertLess(max(abs(p[0] - q[0]) + abs(p[1] - q[1])
                            for p, q in zip(a, b)), 1e-6)

    def test_resample_length(self):
        self.assertEqual(len(core.resample([(0, 0), (1, 0), (1, 1)], 10)), 10)

    def test_dtw_zero_for_identical(self):
        u, rng = make_user()
        sh = core.normalize_shape(ex.genuine(u, rng))
        self.assertAlmostEqual(core.dtw_distance(sh, sh), 0.0, places=9)

    def test_short_signature_rejected(self):
        self.assertFalse(core.is_valid([[(0, 0, 0.0), (1, 1, 0.01)]]))


class TestVerification(unittest.TestCase):
    def setUp(self):
        self.u, self.rng = make_user(7)
        self.profile = core.Profile.enroll(
            "t", [ex.genuine(self.u, self.rng) for _ in range(5)])

    def test_genuine_mostly_accepted(self):
        ok = sum(self.profile.verify(ex.genuine(self.u, self.rng))[0]
                 for _ in range(30))
        self.assertGreaterEqual(ok, 22)

    def test_random_forgery_rejected(self):
        other, _ = make_user(99)
        bad = sum(self.profile.verify(ex.genuine(other, self.rng))[0]
                  for _ in range(30))
        self.assertEqual(bad, 0)

    def test_serialization_roundtrip(self):
        p2 = core.Profile.from_dict(self.profile.to_dict())
        probe = ex.genuine(self.u, random.Random(5))
        self.assertAlmostEqual(self.profile.score(probe), p2.score(probe), 9)

    def test_enroll_needs_three_samples(self):
        with self.assertRaises(ValueError):
            core.Profile.enroll("x", [ex.genuine(self.u, self.rng)] * 2)


class TestLimiter(unittest.TestCase):
    def test_lock_after_three_failures(self):
        now = [0.0]
        lim = core.AttemptLimiter(clock=lambda: now[0])
        for _ in range(3):
            lim.register_failure()
        self.assertTrue(lim.is_locked())
        now[0] += core.LOCK_SECONDS + 1
        self.assertFalse(lim.is_locked())


class TestController(unittest.TestCase):
    def test_full_scenario(self):
        with tempfile.TemporaryDirectory() as d:
            store = core.ProfileStore(os.path.join(d, "p.json"))
            now = [0.0]
            c = controller.AuthController(store, clock=lambda: now[0])
            u, rng = make_user(3)
            other, _ = make_user(11)

            self.assertEqual(c.submit([]).kind, "idle")
            c.start_enroll("vitaliy")
            self.assertEqual(c.submit([[(0, 0, 0.0), (1, 1, 0.01)]]).kind,
                             "too_short")
            for i in range(core.ENROLL_SAMPLES):
                r = c.submit(ex.genuine(u, rng))
            self.assertEqual(r.kind, "enroll_done")
            self.assertIn("vitaliy", store.names())

            # профиль сохраняется на диск и загружается заново
            store2 = core.ProfileStore(os.path.join(d, "p.json"))
            self.assertIn("vitaliy", store2.names())

            c.start_login("vitaliy")
            kinds = []
            for _ in range(3):
                kinds.append(c.submit(ex.genuine(other, rng)).kind)
            self.assertEqual(kinds, ["denied", "denied", "locked"])
            self.assertEqual(c.submit(ex.genuine(u, rng)).kind, "locked")
            now[0] += core.LOCK_SECONDS + 1
            for _ in range(10):
                r = c.submit(ex.genuine(u, rng))
                if r.kind == "granted":
                    break
                c.start_login("vitaliy")
            self.assertEqual(r.kind, "granted")

    def test_unknown_user(self):
        with tempfile.TemporaryDirectory() as d:
            c = controller.AuthController(core.ProfileStore(os.path.join(d, "p.json")))
            with self.assertRaises(KeyError):
                c.start_login("nobody")


if __name__ == "__main__":
    unittest.main()
