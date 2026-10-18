import json
import os
import tempfile
import unittest

from benchmark.common import SCENARIOS, SCHEDULERS, build_workload, simulate
from benchmark.metrics_ext import evaluate, jain
from benchmark.replay import build_replay
from benchmark.stats_tests import holm, paired_compare


class StatsTests(unittest.TestCase):
    def test_jain_bounds(self):
        self.assertAlmostEqual(jain([1, 1, 1, 1]), 1.0)
        self.assertAlmostEqual(jain([1, 0, 0, 0]), 0.25)
        self.assertEqual(jain([]), 1.0)

    def test_holm_monotone_and_bounded(self):
        adj = holm([0.01, 0.04, 0.03, 0.5])
        self.assertTrue(all(0 <= a <= 1 for a in adj))
        self.assertAlmostEqual(adj[0], 0.04)
        self.assertTrue(adj[3] >= adj[1] >= adj[0])

    def test_paired_compare_direction(self):
        a = [1, 2, 1, 2, 1, 2, 1, 2]
        b = [5, 6, 5, 6, 5, 6, 5, 6]
        lower = paired_compare(a, b, higher_better=False)
        self.assertEqual(lower["wins"], 8)
        self.assertGreater(lower["rel_improvement_pct"], 0)
        higher = paired_compare(a, b, higher_better=True)
        self.assertEqual(higher["losses"], 8)
        self.assertLess(higher["p_wilcoxon"], 0.05)

    def test_identical_samples_not_significant(self):
        r = paired_compare([1.0] * 6, [1.0] * 6, False)
        self.assertEqual(r["ties"], 6)
        self.assertEqual(r["p_wilcoxon"], 1.0)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.w = build_workload(SCENARIOS["mixed"], 2, cls.dir)

    def test_trace_written_by_member1(self):
        data = json.load(open(self.w.trace_path))
        self.assertEqual(data["module"].split(":")[0], "Member 1")
        self.assertEqual(len(data["requests"]), SCENARIOS["mixed"].requests)

    def test_all_schedulers_finish_and_metrics_sane(self):
        for s in SCHEDULERS:
            res = simulate(self.w.tasks, s, 4)
            self.assertFalse(res.truncated, s)
            m = evaluate(res)
            self.assertTrue(0 <= m["request_miss_rate"] <= 100)
            self.assertTrue(0 < m["jain_progress"] <= 1.0001)
            self.assertGreater(m["throughput_tok_s"], 0)
            self.assertTrue(all(t.is_done for t in res.tasks), s)

    def test_deterministic(self):
        a = evaluate(simulate(self.w.tasks, "token_laxity", 4))
        b = evaluate(simulate(self.w.tasks, "token_laxity", 4))
        self.assertEqual(a, b)

    def test_same_workload_for_every_scheduler(self):
        counts = {evaluate(simulate(self.w.tasks, s, 4))["decode_tokens"] for s in SCHEDULERS}
        self.assertEqual(len(counts), 1)

    def test_replay_segments_do_not_overlap_per_core(self):
        rp = build_replay(self.w, 4, ["token_laxity", "rr"])
        for run in rp["runs"].values():
            by_core = {}
            for core, _, s, e, _ in run["segments"]:
                by_core.setdefault(core, []).append((s, e))
            for segs in by_core.values():
                segs.sort()
                for (s1, e1), (s2, e2) in zip(segs, segs[1:]):
                    self.assertLessEqual(e1, s2 + 0.02)

    def test_tls_beats_round_robin_under_load(self):
        tls = evaluate(simulate(self.w.tasks, "token_laxity", 4))
        rr = evaluate(simulate(self.w.tasks, "rr", 4))
        self.assertLess(tls["token_miss_rate"], rr["token_miss_rate"])


if __name__ == "__main__":
    unittest.main()
