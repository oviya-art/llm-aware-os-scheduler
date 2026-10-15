"""
Automated tests for the scheduling engine (Member 2).

Run from this folder:   python -m unittest test_scheduler.py -v
No Ollama, no network, no Member 1 code needed (traces are built in temp files).
"""

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest

import os_actuator
from background_load import generate_background, load_background_json
from baselines import (CFSLikeScheduler, EDFScheduler, FCFSScheduler, PriorityScheduler,
                       RoundRobinScheduler)
from decision_log import export_comparison, export_run
from main import main as cli_main
from metrics import compute_metrics, percentile
from scheduler_base import Assignment, Scheduler
from simulator import SimConfig, Simulator
from task_model import Task, TaskKind
from token_laxity_scheduler import TLSConfig, TokenLaxityScheduler
from trace_loader import LoadOptions, load_trace


def inference(tid, arrival_ms=0.0, prefill=20.0, tokens=(10.0,) * 20, D=30.0, budget=500.0,
              prio="normal", rtype="standard", prompt=100):
    return Task(tid, TaskKind.INFERENCE, arrival_ms, [prefill] + list(tokens), itl_deadline_ms=D,
                ttft_budget_ms=budget, prompt_tokens=prompt, request_type=rtype, priority_label=prio)


def hog(tid, arrival_ms=0.0, burst=1000.0, nice=0):
    return Task(tid, TaskKind.BACKGROUND, arrival_ms, [burst], nice=nice, request_type="background")


def run(tasks, scheduler, cores=1, **kw):
    return Simulator(tasks, scheduler, SimConfig(num_cores=cores, **kw)).run()


def by_id(res, tid):
    return next(t for t in res.tasks if t.task_id == tid)


# ----------------------------------------------------------------------- loader
class TestTraceLoader(unittest.TestCase):
    def _write(self, payload, name="t.json"):
        d = tempfile.mkdtemp()
        p = os.path.join(d, name)
        with open(p, "w") as fh:
            json.dump(payload, fh)
        return p

    def sim_payload(self):
        return {"experiment_config": {"avg_interference_factor": 1.4}, "requests": [{
            "request_id": "req_0001", "arrival_time_s": 0.5, "category": "short",
            "request_type": "interactive", "priority_metadata": "high", "prompt_tokens": 40,
            "requested_output_tokens": 3, "tokens_generated": 3, "deadline_target_itl_ms": 30.0,
            "ttft_ms": 100.0, "observed_avg_itl_ms": 40.0,
            "token_records": [{"itl_ms": 40.0}, {"itl_ms": 42.0}, {"itl_ms": 38.0}]}]}

    def test_simulation_json(self):
        lt = load_trace(self._write(self.sim_payload()))
        t = lt.tasks[0]
        self.assertEqual(lt.mode, "simulation")
        self.assertAlmostEqual(lt.demand_scale, 0.7 / 1.4)
        self.assertEqual(len(t.bursts_ms), 4)                      # prefill + 3 tokens
        self.assertAlmostEqual(t.bursts_ms[0], 50.0)               # 100 * 0.5
        self.assertAlmostEqual(t.bursts_ms[1], 20.0)
        self.assertEqual(t.arrival_ms, 500.0)
        self.assertEqual(t.itl_deadline_ms, 30.0)
        self.assertEqual(t.ttft_budget_ms, 300.0)                  # interactive default
        self.assertEqual(t.priority_label, "high")

    def test_demand_scale_override(self):
        lt = load_trace(self._write(self.sim_payload()), LoadOptions(demand_scale=1.0))
        self.assertAlmostEqual(lt.tasks[0].bursts_ms[1], 40.0)

    def test_realtime_json(self):
        payload = {"requests": [{
            "request_id": "real_1", "model": "llama3.2", "request_start_timestamp_s": 5000.0,
            "first_output_latency_ms": 2000.0, "output_chunks": 4, "deadline_ms": 100.0,
            "chunk_records": [{"inter_chunk_latency_ms": 2000.0}, {"inter_chunk_latency_ms": 90.0},
                              {"inter_chunk_latency_ms": 110.0}, {"inter_chunk_latency_ms": 100.0}]},
            {"request_id": "real_2", "request_start_timestamp_s": 5002.0, "first_output_latency_ms": 500.0,
             "output_chunks": 3, "deadline_ms": 100.0, "average_inter_chunk_latency_ms": 80.0}]}
        lt = load_trace(self._write(payload))
        self.assertEqual(lt.mode, "realtime")
        a, b = lt.tasks
        self.assertEqual(a.arrival_ms, 0.0)
        self.assertEqual(b.arrival_ms, 2000.0)                     # normalised to earliest request
        self.assertEqual(a.bursts_ms, [2000.0, 90.0, 110.0, 100.0][:1] + [90.0, 110.0, 100.0])
        self.assertEqual(len(b.bursts_ms), 3)                      # prefill + (3-1) synthesised chunks
        self.assertTrue(any("synthesised" in n for n in lt.notes))

    def test_csv_fallback(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "t.csv")
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["request_id", "arrival_time_s", "request_type", "tokens_generated",
                        "deadline_target_itl_ms", "ttft_ms", "observed_avg_itl_ms"])
            w.writerow(["r1", "1.0", "standard", "5", "40", "80", "30"])
        lt = load_trace(p, LoadOptions(demand_scale=1.0))
        self.assertEqual(len(lt.tasks[0].bursts_ms), 6)
        self.assertEqual(lt.tasks[0].arrival_ms, 1000.0)

    def test_replicate_and_unique_ids(self):
        lt = load_trace(self._write(self.sim_payload()), LoadOptions(replicate=3, replicate_gap_ms=100))
        self.assertEqual(len(lt.tasks), 3)
        self.assertEqual(len({t.task_id for t in lt.tasks}), 3)
        self.assertEqual(sorted(t.arrival_ms for t in lt.tasks), [500.0, 600.0, 700.0])

    def test_errors(self):
        with self.assertRaises(FileNotFoundError):
            load_trace("/nonexistent/trace.json")
        p = self._write({"requests": []})
        with self.assertRaises(ValueError):
            load_trace(p)


# --------------------------------------------------------------------- background
class TestBackground(unittest.TestCase):
    def test_deterministic_and_scaled(self):
        a = generate_background("mixed_load", 4, 60000, seed=3)
        b = generate_background("mixed_load", 4, 60000, seed=3)
        self.assertEqual([(t.arrival_ms, t.bursts_ms) for t in a], [(t.arrival_ms, t.bursts_ms) for t in b])
        self.assertEqual(generate_background("none", 4, 60000), [])
        heavy = sum(t.total_service_ms for t in generate_background("heavy_load", 4, 120000, seed=1))
        light = sum(t.total_service_ms for t in generate_background("light_load", 4, 120000, seed=1))
        self.assertGreater(heavy, light)
        with self.assertRaises(ValueError):
            generate_background("bogus", 4, 1000)

    def test_custom_json(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "bg.json")
        with open(p, "w") as fh:
            json.dump({"background_tasks": [{"id": "x", "arrival_s": 1.0, "burst_ms": 500, "nice": 5}]}, fh)
        t = load_background_json(p)[0]
        self.assertEqual((t.task_id, t.arrival_ms, t.nice), ("x", 1000.0, 5))


# ---------------------------------------------------------------------- simulator
class TestSimulatorCore(unittest.TestCase):
    def test_dedicated_core_no_misses(self):
        res = run([inference("a")], FCFSScheduler(), cores=1)
        t = by_id(res, "a")
        itls = [o.latency_ms for o in t.outcomes if o.token_index > 0]
        self.assertEqual(len(itls), 20)
        for x in itls:
            self.assertAlmostEqual(x, 10.0, delta=0.5)
        self.assertFalse(any(o.missed for o in t.outcomes))
        self.assertAlmostEqual(t.cpu_received_ms, t.total_service_ms, places=4)

    def test_cpu_conservation_and_capacity(self):
        tasks = [inference(f"i{k}", k * 30.0) for k in range(6)] + [hog("h", 0, 500)]
        for sched in (RoundRobinScheduler(), CFSLikeScheduler(), EDFScheduler(), TokenLaxityScheduler()):
            res = run(tasks, sched, cores=2)
            for t in res.tasks:
                self.assertIsNotNone(t.finish_ms, f"{res.scheduler}: {t.task_id} unfinished")
                self.assertAlmostEqual(t.cpu_received_ms, t.total_service_ms, places=3)
            busy = sum(c.busy_ms for c in res.cores)
            self.assertAlmostEqual(busy, sum(t.total_service_ms for t in tasks), places=2)
            self.assertLessEqual(busy, res.makespan_ms * 2 + 1e-6)

    def test_bad_scheduler_double_assignment_rejected(self):
        class Bad(Scheduler):
            name = "bad"
            def decide(self, now_ms, runnable, cores):
                t = runnable[0]
                return {0: Assignment(t), 1: Assignment(t)}
        with self.assertRaises(RuntimeError):
            run([hog("h")], Bad(), cores=2)

    def test_determinism(self):
        tasks = [inference(f"i{k}", k * 20.0) for k in range(5)] + generate_background("heavy_load", 2, 5000, 1)
        m1 = compute_metrics(run(tasks, TokenLaxityScheduler(), cores=2))
        m2 = compute_metrics(run(tasks, TokenLaxityScheduler(), cores=2))
        self.assertEqual(m1, m2)

    def test_input_tasks_not_mutated(self):
        tasks = [inference("a"), hog("h", 0, 100)]
        run(tasks, FCFSScheduler(), cores=1)
        self.assertEqual(tasks[0].burst_idx, 0)
        self.assertEqual(tasks[0].outcomes, [])

    def test_context_switch_costs_charged(self):
        res = run([inference("a")], FCFSScheduler(), cores=1, context_switch_ms=2.0)
        self.assertGreaterEqual(by_id(res, "a").finish_ms, by_id(res, "a").total_service_ms + 2.0 - 1e-6)
        self.assertGreater(sum(c.overhead_ms for c in res.cores), 0)


# ----------------------------------------------------------------------- baselines
class TestBaselines(unittest.TestCase):
    def test_fcfs_head_of_line_blocking(self):
        tasks = [hog("h", 0, 2000), inference("a", 10.0)]
        res = run(tasks, FCFSScheduler(), cores=1)
        self.assertGreaterEqual(by_id(res, "a").first_token_ms, 2000.0)        # waits for the hog
        self.assertEqual(res.preemptions, 0)

    def test_rr_rotates(self):
        tasks = [hog("h1", 0, 200), hog("h2", 0, 200)]
        res = run(tasks, RoundRobinScheduler(quantum_ms=20), cores=1)
        self.assertGreater(res.preemptions, 5)
        d = sorted(by_id(res, x).finish_ms for x in ("h1", "h2"))
        self.assertLess(d[1] - d[0], 60)                                       # finish close together

    def test_priority_preempts_lower(self):
        tasks = [hog("low", 0, 1000), inference("hi", 50.0, prio="high")]
        res = run(tasks, PriorityScheduler(), cores=1)
        self.assertLess(by_id(res, "hi").first_token_ms, 120.0)
        self.assertGreater(res.preemptions, 0)

    def test_cfs_fair_share(self):
        tasks = [hog("a", 0, 1000), hog("b", 0, 1000)]
        res = run(tasks, CFSLikeScheduler(), cores=1)
        fa, fb = by_id(res, "a").finish_ms, by_id(res, "b").finish_ms
        self.assertLess(abs(fa - fb), 40)

    def test_cfs_nice_weights(self):
        tasks = [hog("nice0", 0, 600, nice=0), hog("nice10", 0, 600, nice=10)]
        res = run(tasks, CFSLikeScheduler(), cores=1)
        self.assertLess(by_id(res, "nice0").finish_ms, by_id(res, "nice10").finish_ms)

    def test_edf_picks_earliest_deadline(self):
        tight = inference("tight", 0.0, D=20.0, budget=100.0)
        loose = inference("loose", 0.0, D=200.0, budget=2000.0)
        res = run([loose, tight], EDFScheduler(), cores=1)
        self.assertLess(by_id(res, "tight").first_token_ms, by_id(res, "loose").first_token_ms)


# --------------------------------------------------------------------------- TLS
class TestTokenLaxity(unittest.TestCase):
    def test_critical_inference_preempts_background(self):
        tasks = [hog("h", 0, 3000), inference("a", 100.0, budget=150.0)]
        tls = run(tasks, TokenLaxityScheduler(), cores=1)
        fcfs = run(tasks, FCFSScheduler(), cores=1)
        self.assertLess(by_id(tls, "a").first_token_ms - 100.0, 150.0)          # met TTFT budget
        self.assertGreater(by_id(fcfs, "a").first_token_ms - 100.0, 2000.0)
        reasons = {e.reason for e in tls.events if e.event == "DISPATCH"}
        self.assertTrue(reasons & {"preempt_background", "preempt_background_critical"})

    def test_decision_log_carries_laxity(self):
        res = run([inference("a")], TokenLaxityScheduler(), cores=1)
        d = [e for e in res.events if e.event == "DISPATCH" and e.task_id == "a"][0]
        self.assertIsNotNone(d.laxity_ms)
        self.assertIsNotNone(d.norm_laxity)
        self.assertIsNotNone(d.pred_service_ms)

    def test_comfortable_task_does_not_preempt(self):
        # Huge TTFT budget -> prefill laxity is enormous -> must wait for the running task.
        tasks = [inference("run", 0.0, tokens=(10.0,) * 60, budget=500.0),
                 inference("calm", 50.0, budget=1e9)]
        res = run(tasks, TokenLaxityScheduler(), cores=1)
        self.assertEqual(by_id(res, "run").preemption_count, 0)

    def test_estimator_is_causal_ewma(self):
        s = TokenLaxityScheduler(ewma_alpha=0.5)
        t = inference("a")
        s.reset()
        s.on_arrival(t, 0.0)
        s.on_token_emit(t, 0, 20, 20, 0)                  # prefill: no decode state yet
        self.assertIsNone(s.state["a"].c_hat)
        for svc, expected in ((10.0, 10.0), (20.0, 15.0), (20.0, 17.5)):
            s.on_token_emit(t, 0, svc, svc, 1)
            self.assertAlmostEqual(s.state["a"].c_hat, expected)

    def test_future_bursts_do_not_change_early_decisions(self):
        a = inference("x", tokens=(10.0,) * 10 + (10.0,) * 10)
        b = inference("x", tokens=(10.0,) * 10 + (60.0,) * 10)       # same past, different future
        h = hog("h", 5.0, 1000)
        ra, rb = run([a, h], TokenLaxityScheduler(), 1), run([b, h], TokenLaxityScheduler(), 1)
        cut = by_id(ra, "x").outcomes[10].emit_ms                     # up to the 10th token
        ea = [(e.t_ms, e.event, e.task_id, e.reason) for e in ra.events if e.t_ms < cut - 1]
        eb = [(e.t_ms, e.event, e.task_id, e.reason) for e in rb.events if e.t_ms < cut - 1]
        self.assertEqual(ea, eb)

    def test_capacity_planner_limits_protected_set(self):
        s = TokenLaxityScheduler(util_bound=0.8)
        s.reset()
        tasks = [inference(f"s{k}", float(k)) for k in range(4)]
        for t in tasks:
            s.on_arrival(t, 0.0)
        for t in tasks:                                   # each stream needs u = 0.6 of a core
            s.on_token_emit(t, 0, 18.0, 18.0, 1)
        self.assertEqual(len(s._plan(tasks, 1)), 1)       # 1 core * 0.8 fits one 0.6-stream
        self.assertEqual(len(s._plan(tasks, 2)), 2)       # 1.6 fits two (0.6+0.6=1.2), not three (1.8)
        high = inference("vip", 9.0, prio="high")
        s.on_arrival(high, 0.0)
        s.on_token_emit(high, 0, 18.0, 18.0, 1)
        self.assertIn("vip", s._plan(tasks + [high], 1))  # higher class takes the protected slot

    def test_unknown_parameter_rejected(self):
        with self.assertRaises(ValueError):
            TokenLaxityScheduler(not_a_param=1)

    def test_beats_non_deadline_aware_baselines_under_load(self):
        tasks = [inference(f"i{k}", k * 40.0, tokens=(12.0,) * 120, D=30.0) for k in range(6)]
        tasks += [hog("h1", 0, 4000), hog("h2", 0, 4000)]
        miss = {}
        for sched in (RoundRobinScheduler(), CFSLikeScheduler(), TokenLaxityScheduler()):
            miss[sched.name] = compute_metrics(run(tasks, sched, cores=4))["deadline_miss_rate_pct"]
        self.assertLess(miss["token_laxity"], miss["rr"])
        self.assertLess(miss["token_laxity"], miss["cfs"])

    def test_background_not_starved_forever(self):
        tasks = [inference(f"i{k}", k * 10.0, tokens=(12.0,) * 150) for k in range(8)] + [hog("h", 0, 300)]
        res = run(tasks, TokenLaxityScheduler(), cores=2)
        self.assertIsNotNone(by_id(res, "h").finish_ms)


# ------------------------------------------------------------------------ metrics
class TestMetrics(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(percentile([], 95), 0.0)
        self.assertEqual(percentile([5.0], 99), 5.0)
        self.assertAlmostEqual(percentile([1, 2, 3, 4], 50), 2.5)

    def test_violation_counting(self):
        # one core, two streams: the second must miss deadlines while the first runs (FCFS)
        tasks = [inference("a", 0.0, tokens=(10.0,) * 10), inference("b", 0.0, tokens=(10.0,) * 10)]
        res = run(tasks, FCFSScheduler(), cores=1)
        m = compute_metrics(res)
        self.assertEqual(m["decode_tokens"], 20)
        self.assertEqual(m["itl_violations"], 0)                     # b waits BEFORE its first token...
        self.assertEqual(m["ttft_miss_rate_pct"], 0.0)               # ...within the 500ms budget here
        tight = [inference("a", 0.0, budget=5.0), inference("b", 0.0, budget=5.0)]
        m2 = compute_metrics(run(tight, FCFSScheduler(), cores=1))
        self.assertEqual(m2["ttft_miss_rate_pct"], 100.0)            # both exceed a 5 ms budget (20 ms prefill)
        self.assertGreater(m2["deadline_miss_rate_pct"], 0)

    def test_metric_fields_present(self):
        m = compute_metrics(run([inference("a"), hog("h", 0, 100)], FCFSScheduler(), cores=1))
        for k in ("deadline_miss_rate_pct", "sla_attained_pct", "p99_itl_ms", "bg_mean_slowdown",
                  "cpu_utilization_pct", "by_request_type", "inference_fairness_jain"):
            self.assertIn(k, m)


# --------------------------------------------------------------- export / CLI / OS
class TestExportAndCLI(unittest.TestCase):
    def test_export_run_files_and_schema(self):
        res = run([inference("a"), hog("h", 0, 200)], TokenLaxityScheduler(), cores=1)
        out = tempfile.mkdtemp()
        paths = export_run(res, out, "t", "src.json")
        for p in paths.values():
            self.assertTrue(os.path.getsize(p) > 0, p)
        with open(paths["json"]) as fh:
            payload = json.load(fh)
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["scheduler"], "token_laxity")
        self.assertTrue(payload["decisions"] and payload["token_outcomes"] and payload["requests"])
        with open(paths["decisions_csv"], newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertTrue({"t_ms", "event", "core", "task_id", "reason", "laxity_ms"} <= set(rows[0]))
        self.assertTrue({r["event"] for r in rows} >= {"ARRIVE", "DISPATCH", "COMPLETE"})
        cp = export_comparison([compute_metrics(res)], out, "t")
        self.assertTrue(os.path.exists(cp["csv"]))

    def test_cli_end_to_end(self):
        d = tempfile.mkdtemp()
        trace = os.path.join(d, "t.json")
        reqs = [{"request_id": f"r{k}", "arrival_time_s": k * 0.1, "category": "short",
                 "request_type": "standard", "priority_metadata": "normal", "prompt_tokens": 30,
                 "tokens_generated": 20, "deadline_target_itl_ms": 40.0, "ttft_ms": 60.0,
                 "observed_avg_itl_ms": 40.0, "token_records": [{"itl_ms": 40.0}] * 20} for k in range(4)]
        with open(trace, "w") as fh:
            json.dump({"experiment_config": {"avg_interference_factor": 1.0}, "requests": reqs}, fh)
        out = os.path.join(d, "logs")
        with contextlib.redirect_stdout(io.StringIO()):
                rc = cli_main(["--trace", trace, "--cores", "2", "--output-dir", out, "--schedulers",
                           "fcfs,token_laxity", "--tls-set", "util_bound=0.7"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(out, "run_comparison.csv")))
        self.assertTrue(os.path.exists(os.path.join(out, "run_token_laxity_decisions.json")))
        with self.assertRaises(SystemExit):
            cli_main(["--trace", trace, "--tls-set", "bogus=1", "--no-export"])


class TestOSActuator(unittest.TestCase):
    def test_dry_run_records_only(self):
        a = os_actuator.OSActuator(dry_run=True)
        r = a.set_level(os.getpid(), "low")
        self.assertFalse(r.applied)
        self.assertTrue(r.dry_run)
        with self.assertRaises(ValueError):
            a.set_level(1, "ultra")

    def test_governor_hysteresis(self):
        a = os_actuator.OSActuator(dry_run=True)
        g = os_actuator.LiveGovernor(a, inference_pid=1, background_pids=[2], release_steps=3)
        self.assertEqual(g.step(60, 100), os_actuator.Urgency.COMFORTABLE)
        self.assertEqual(a.history, [])
        self.assertEqual(g.step(120, 100), os_actuator.Urgency.CRITICAL)
        self.assertEqual([(r.pid, r.level) for r in a.history], [(1, "high"), (2, "low")])
        g.step(60, 100)
        g.step(60, 100)
        self.assertEqual(len(a.history), 2)                          # not released yet
        g.step(60, 100)
        self.assertEqual([(r.pid, r.level) for r in a.history[2:]], [(1, "normal"), (2, "normal")])


if __name__ == "__main__":
    unittest.main()
