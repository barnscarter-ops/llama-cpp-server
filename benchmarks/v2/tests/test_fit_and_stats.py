import unittest

import lane_fixture  # noqa: F401
from bench_v2 import stats
from bench_v2.config import load_candidates, load_lanes
from bench_v2.fitcheck import MIN_CTX, check_fit, parse_buffers, check_loaded


def cand(cid):
    lanes = load_lanes()
    _, cands = load_candidates(None, lanes)
    return next(c for c in cands if c.id == cid), lanes


class FitCheck(unittest.TestCase):
    def test_context_floor_is_131072(self):
        c, lanes = cand("qwen36-35b-a3b-iq3")
        fi = c.fit_input("q8_0", 65536)
        r = check_fit(fi, lanes["workbench"].fit_lane)
        self.assertFalse(r.fits)
        self.assertIn("below the hard minimum", r.reason)
        self.assertEqual(MIN_CTX, 131072)

    def test_qwen36_iq3_f16_fails_headroom_q8_fits(self):
        c, lanes = cand("qwen36-35b-a3b-iq3")
        lane = lanes["workbench"].fit_lane
        f16 = check_fit(c.fit_input("f16"), lane)
        q8 = check_fit(c.fit_input("q8_0"), lane)
        self.assertFalse(f16.fits)
        self.assertAlmostEqual(f16.total_gib, 15.7, delta=0.15)
        self.assertTrue(q8.fits)
        self.assertAlmostEqual(q8.total_gib, 14.5, delta=0.15)

    def test_qwen38_q6_f16_fails_q8_fits_with_headroom(self):
        c, lanes = cand("qwen3.8-27b-q6k")
        lane = lanes["r9700"].fit_lane
        self.assertFalse(check_fit(c.fit_input("f16"), lane).fits)
        r = check_fit(c.fit_input("q8_0"), lane)
        self.assertTrue(r.fits)
        self.assertAlmostEqual(r.headroom_gib, 2.35, delta=0.3)

    def test_moe_offload_only_when_window_allows(self):
        c, lanes = cand("qwen36-35b-a3b-q4km-offload")
        lane = lanes["workbench"].fit_lane
        self.assertFalse(check_fit(c.fit_input("q8_0"), lane, allow_moe_offload=False).fits)
        r = check_fit(c.fit_input("q8_0"), lane, allow_moe_offload=True)
        self.assertTrue(r.fits)
        self.assertGreater(r.n_cpu_moe, 0)

    def test_dense_model_never_gets_offload(self):
        c, lanes = cand("qwen3.8-27b-q6k")
        r = check_fit(c.fit_input("f16"), lanes["r9700"].fit_lane, allow_moe_offload=True)
        self.assertFalse(r.fits)
        self.assertEqual(r.n_cpu_moe, 0)

    def test_loaded_log_with_cpu_weights_is_rejected(self):
        _, lanes = cand("qwen36-35b-a3b-iq3")
        lane = lanes["workbench"].fit_lane
        log = ["CUDA0 model buffer size = 12000.00 MiB", "CPU_Mapped model buffer size = 4000.00 MiB", "CUDA0 KV buffer size = 1000.00 MiB"]
        res = check_loaded(parse_buffers(log), 13500.0, 16384.0, 0.0, lane, False)
        self.assertFalse(res[0] if isinstance(res, tuple) else res.ok)


class Stats(unittest.TestCase):
    def test_noise_floor_has_three_percent_minimum(self):
        self.assertGreaterEqual(stats.noise_floor([10.0, 10.0, 10.0]), 0.03)

    def test_noise_floor_grows_with_spread(self):
        self.assertGreater(stats.noise_floor([10.0, 11.0, 9.0]), 0.03)

    def test_wilson_bounds(self):
        lo, hi = stats.wilson(95, 100)
        self.assertLess(lo, 0.95)
        self.assertGreater(hi, 0.95)

    def test_median(self):
        self.assertEqual(stats.median([3, 1, 2]), 2)


if __name__ == "__main__":
    unittest.main()
