"""
Unit Tests for LLM Workload Simulation & Latency Profiling Module.

Tests cover:
1. Workload generation and reproducibility.
2. Latency percentiles and TTFT/ITL profiling logic.
3. Deadline detection, slack computation, and risk classification.
4. Trace export in JSON and CSV formats.
"""

import os
import tempfile
import unittest

from workload_generator import InferenceRequest, WorkloadGenerator, RequestType, RequestPriority
from latency_profiler import LatencyProfiler, SimulatedInferenceBackend, calculate_percentile
from deadline_model import DeadlineRiskModel, RiskLevel
from trace_exporter import TraceExporter


class TestWorkloadGenerator(unittest.TestCase):
    def test_reproducibility_with_seed(self):
        gen1 = WorkloadGenerator(seed=99)
        gen2 = WorkloadGenerator(seed=99)
        wl1 = gen1.generate_workload(total_requests=5)
        wl2 = gen2.generate_workload(total_requests=5)

        for r1, r2 in zip(wl1, wl2):
            self.assertEqual(r1.request_id, r2.request_id)
            self.assertAlmostEqual(r1.arrival_time_s, r2.arrival_time_s, places=4)
            self.assertEqual(r1.prompt_tokens, r2.prompt_tokens)
            self.assertEqual(r1.output_tokens, r2.output_tokens)
            self.assertEqual(r1.target_itl_ms, r2.target_itl_ms)

    def test_invalid_parameters(self):
        gen = WorkloadGenerator()
        with self.assertRaises(ValueError):
            gen.generate_workload(total_requests=0)
        with self.assertRaises(ValueError):
            gen.generate_workload(arrival_rate_rps=-1.0)


class TestLatencyProfiler(unittest.TestCase):
    def test_percentile_calculation(self):
        data = [10.0, 20.0, 30.0, 40.0, 50.0]
        p50 = calculate_percentile(data, 50.0)
        p90 = calculate_percentile(data, 90.0)
        self.assertAlmostEqual(p50, 30.0, places=2)
        self.assertAlmostEqual(p90, 46.0, places=2)

    def test_simulated_backend_output(self):
        gen = WorkloadGenerator(seed=42)
        req = gen.generate_single_request("req_0001", arrival_time_s=0.5, category="short")
        profiler = LatencyProfiler(backend=SimulatedInferenceBackend(seed=42))

        profile = profiler.profile_request(req)
        self.assertTrue(profile.is_simulated)
        self.assertGreater(profile.ttft_ms, 0)
        self.assertEqual(len(profile.token_records), req.output_tokens)
        self.assertEqual(len(profile.itl_samples_ms), req.output_tokens)
        self.assertGreater(profile.avg_itl_ms, 0)
        self.assertGreaterEqual(profile.p99_itl_ms, profile.median_itl_ms)


class TestDeadlineModel(unittest.TestCase):
    def test_deadline_violation_and_slack(self):
        gen = WorkloadGenerator(seed=42)
        req = gen.generate_single_request("req_0001", arrival_time_s=0.0, category="short", target_itl_ms=30.0)
        profiler = LatencyProfiler(backend=SimulatedInferenceBackend(seed=42))
        profile = profiler.profile_request(req, interference_factor=1.5)

        model = DeadlineRiskModel()
        evaluation = model.evaluate_request(req, profile)

        self.assertEqual(evaluation.deadline_target_ms, 30.0)
        self.assertEqual(evaluation.total_tokens_evaluated, req.output_tokens)
        self.assertTrue(isinstance(evaluation.has_observed_deadline_violation, bool))
        self.assertIn(evaluation.predicted_risk_level, [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH])
        # Slack must be Deadline - Profile ITL
        self.assertAlmostEqual(evaluation.avg_observed_slack_ms, 30.0 - profile.avg_itl_ms, places=2)

    def test_minimum_and_percentile_slack(self):
        """Verifies that observed min slack strictly equals deadline minus max ITL sample."""
        from latency_profiler import RequestProfileResult
        req = InferenceRequest(
            request_id="req_test",
            arrival_time_s=1.0,
            prompt_tokens=10,
            output_tokens=4,
            target_itl_ms=50.0,
            request_type=RequestType.STANDARD,
            priority=RequestPriority.NORMAL,
            category="short",
        )
        # Explicit ITL samples
        samples = [40.0, 55.0, 30.0, 70.0]
        # max ITL is 70.0 -> min slack should be 50.0 - 70.0 = -20.0
        # avg ITL is 48.75 -> avg slack should be 50.0 - 48.75 = 1.25
        p95_itl = calculate_percentile(samples, 95.0)

        profile = RequestProfileResult(
            request_id=req.request_id,
            is_simulated=True,
            data_source="Test",
            submission_timestamp_s=1.0,
            ttft_ms=25.0,
            total_generation_time_ms=25.0 + sum(samples),
            total_tokens_generated=len(samples),
            itl_samples_ms=samples,
            avg_itl_ms=sum(samples) / len(samples),
            median_itl_ms=47.5,
            p95_itl_ms=p95_itl,
            p99_itl_ms=p95_itl,
        )

        model = DeadlineRiskModel()
        eval_res = model.evaluate_request(req, profile)

        # 1. Min slack verification
        expected_min_slack = 50.0 - max(samples)
        self.assertAlmostEqual(eval_res.min_observed_slack_ms, expected_min_slack, places=2)
        self.assertEqual(eval_res.min_observed_slack_ms, -20.0)

        # 2. Percentile slack verification
        expected_p95_slack = 50.0 - p95_itl
        self.assertAlmostEqual(eval_res.p95_observed_slack_ms, expected_p95_slack, places=2)

    def test_zero_and_one_token_edge_cases(self):
        """Verifies robust handling of empty profiles (0 tokens) and single-token outputs."""
        from latency_profiler import RequestProfileResult
        req = InferenceRequest(
            request_id="req_edge",
            arrival_time_s=0.0,
            prompt_tokens=10,
            output_tokens=0,
            target_itl_ms=40.0,
            request_type=RequestType.BATCH,
            priority=RequestPriority.LOW,
            category="short",
        )
        model = DeadlineRiskModel()

        # Zero-token profile
        zero_profile = RequestProfileResult(
            request_id=req.request_id,
            is_simulated=True,
            data_source="Test",
            submission_timestamp_s=0.0,
            ttft_ms=10.0,
            total_generation_time_ms=10.0,
            total_tokens_generated=0,
            itl_samples_ms=[],
            avg_itl_ms=0.0,
            median_itl_ms=0.0,
            p95_itl_ms=0.0,
            p99_itl_ms=0.0,
        )
        zero_eval = model.evaluate_request(req, zero_profile)
        self.assertEqual(zero_eval.total_tokens_evaluated, 0)
        self.assertEqual(zero_eval.observed_violation_count, 0)
        self.assertEqual(zero_eval.observed_violation_rate, 0.0)
        self.assertFalse(zero_eval.has_observed_deadline_violation)
        self.assertEqual(zero_eval.min_observed_slack_ms, 40.0)
        self.assertEqual(zero_eval.prediction_method, "StaticTargetPrior")

        # One-token profile
        one_profile = RequestProfileResult(
            request_id=req.request_id,
            is_simulated=True,
            data_source="Test",
            submission_timestamp_s=0.0,
            ttft_ms=15.0,
            total_generation_time_ms=15.0 + 45.0,
            total_tokens_generated=1,
            itl_samples_ms=[45.0],
            avg_itl_ms=45.0,
            median_itl_ms=45.0,
            p95_itl_ms=45.0,
            p99_itl_ms=45.0,
        )
        one_eval = model.evaluate_request(req, one_profile)
        self.assertEqual(one_eval.total_tokens_evaluated, 1)
        self.assertEqual(one_eval.observed_violation_count, 1)
        self.assertTrue(one_eval.has_observed_deadline_violation)
        self.assertEqual(one_eval.min_observed_slack_ms, -5.0)
        self.assertEqual(one_eval.prediction_method, "ProfileMean(sparse_tokens)")

    def test_deadline_violation_boundaries(self):
        """Tests exact boundary conditions: itl == deadline, itl > deadline, and itl < deadline."""
        from latency_profiler import RequestProfileResult
        deadline = 30.0
        req = InferenceRequest(
            request_id="req_boundary",
            arrival_time_s=0.0,
            prompt_tokens=5,
            output_tokens=3,
            target_itl_ms=deadline,
            request_type=RequestType.INTERACTIVE,
            priority=RequestPriority.HIGH,
            category="short",
        )
        # Sample 1: Exactly at deadline (30.0 -> Slack 0.0 -> NOT a violation)
        # Sample 2: Slightly below deadline (29.99 -> Slack +0.01 -> NOT a violation)
        # Sample 3: Slightly above deadline (30.01 -> Slack -0.01 -> IS a violation)
        samples = [30.0, 29.99, 30.01]
        profile = RequestProfileResult(
            request_id=req.request_id,
            is_simulated=True,
            data_source="Test",
            submission_timestamp_s=0.0,
            ttft_ms=10.0,
            total_generation_time_ms=10.0 + sum(samples),
            total_tokens_generated=len(samples),
            itl_samples_ms=samples,
            avg_itl_ms=sum(samples) / len(samples),
            median_itl_ms=30.0,
            p95_itl_ms=30.01,
            p99_itl_ms=30.01,
        )

        model = DeadlineRiskModel()
        eval_res = model.evaluate_request(req, profile)

        # Exactly 1 sample missed the deadline (30.01)
        self.assertEqual(eval_res.observed_violation_count, 1)
        self.assertAlmostEqual(eval_res.observed_violation_rate, 1 / 3, places=3)
        self.assertTrue(eval_res.has_observed_deadline_violation)
        # Worst slack is 30.0 - 30.01 = -0.01
        self.assertAlmostEqual(eval_res.min_observed_slack_ms, -0.01, places=2)

    def test_ewma_causality_and_no_leakage(self):
        """Verifies that EWMA operates strictly sequentially with zero lookahead."""
        model = DeadlineRiskModel(ewma_alpha=0.5)
        history = [20.0, 30.0, 40.0]
        # Step 1: 20.0
        # Step 2: 0.5 * 30.0 + 0.5 * 20.0 = 25.0
        # Step 3: 0.5 * 40.0 + 0.5 * 25.0 = 32.5
        predicted = model.predict_next_itl(history)
        self.assertAlmostEqual(predicted, 32.5, places=2)

        # Empty history should fall back to target without raising error
        self.assertEqual(model.predict_next_itl([], fallback_target_ms=30.0), 30.0)


class TestMixedWorkload(unittest.TestCase):
    def test_scenarios_and_interference_scaling(self):
        from mixed_workload import MixedWorkloadRunner, LoadScenario
        runner = MixedWorkloadRunner(seed=42)

        light = runner.run_scenario(LoadScenario.LIGHT_LOAD, num_requests=2)
        heavy = runner.run_scenario(LoadScenario.HEAVY_LOAD, num_requests=2)

        self.assertGreater(heavy.interference_factor, light.interference_factor)
        # Heavy workload should result in higher average ITL than light workload
        light_avg_itl = sum(p.avg_itl_ms for p in light.profiles) / len(light.profiles)
        heavy_avg_itl = sum(p.avg_itl_ms for p in heavy.profiles) / len(heavy.profiles)
        self.assertGreater(heavy_avg_itl, light_avg_itl)


class TestTraceExporter(unittest.TestCase):
    def test_export_json_and_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            gen = WorkloadGenerator(seed=42)
            requests = gen.generate_workload(total_requests=2)
            profiler = LatencyProfiler(backend=SimulatedInferenceBackend(seed=42))
            model = DeadlineRiskModel()

            profiles = [profiler.profile_request(r) for r in requests]
            evaluations = [model.evaluate_request(r, p) for r, p in zip(requests, profiles)]

            exporter = TraceExporter(output_dir=temp_dir)
            json_file = exporter.export_json("test_exp", requests, profiles, evaluations)
            csv_file = exporter.export_csv("test_exp", requests, profiles, evaluations)

            self.assertTrue(os.path.exists(json_file))
            self.assertTrue(os.path.exists(csv_file))
            self.assertGreater(os.path.getsize(json_file), 100)
            self.assertGreater(os.path.getsize(csv_file), 100)


class TestRealtimeProfiler(unittest.TestCase):
    """Tests for RealtimeProfiler using mocked Ollama stream events."""

    def test_mocked_ollama_streaming_and_metrics(self):
        """Verifies wall-clock chunk latency calculations, throughput, and deadline evaluations."""
        from realtime_profiler import RealtimeProfiler, OllamaClient
        from windows_monitor import WindowsWorkloadMonitor

        profiler = RealtimeProfiler()

        # Mock stream events arriving at t = 0.05, 0.08, 0.12, 0.15
        mock_events = [
            {"response": "Operating", "_arrival_perf_counter": 100.05},
            {"response": " systems", "_arrival_perf_counter": 100.08},
            {"response": " manage", "_arrival_perf_counter": 100.12},
            {"response": " threads.", "_arrival_perf_counter": 100.15, "done": True},
        ]

        result = profiler.profile_request(
            prompt="Hello",
            model="llama3.2",
            target_latency_ms=35.0,
            stream_events_override=mock_events,
            start_time_override=100.0,
        )

        self.assertEqual(result.mode, "realtime")
        self.assertEqual(result.model, "llama3.2")
        self.assertEqual(result.generated_text, "Operating systems manage threads.")
        self.assertEqual(result.output_chunks, 4)
        self.assertEqual(result.output_characters, len("Operating systems manage threads."))

        # TTFO: 100.05 - 100.00 = 0.05s = 50.0ms
        self.assertAlmostEqual(result.ttfo_ms, 50.0, places=1)
        # Total duration: 100.15 - 100.00 = 0.15s = 150.0ms
        self.assertAlmostEqual(result.generation_duration_ms, 150.0, places=1)

        # ICL samples (chunks 2, 3, 4):
        # chunk 2: 100.08 - 100.05 = 30.0ms
        # chunk 3: 100.12 - 100.08 = 40.0ms
        # chunk 4: 100.15 - 100.12 = 30.0ms
        self.assertEqual(len(result.icl_samples_ms), 3)
        self.assertAlmostEqual(result.icl_samples_ms[0], 30.0, places=1)
        self.assertAlmostEqual(result.icl_samples_ms[1], 40.0, places=1)
        self.assertAlmostEqual(result.icl_samples_ms[2], 30.0, places=1)
        self.assertAlmostEqual(result.average_inter_chunk_latency_ms, (30.0 + 40.0 + 30.0) / 3, places=1)

        # Deadline is 35.0ms: chunk 3 (40.0ms) misses deadline
        self.assertTrue(result.deadline_missed)
        self.assertEqual(result.deadline_violation_count, 1)

        # Scheduler export record verification
        rec = result.to_scheduler_record()
        self.assertEqual(rec["mode"], "realtime")
        self.assertEqual(rec["model"], "llama3.2")
        self.assertFalse(rec["is_simulated"])
        self.assertIn("cpu_utilization_percent", rec)
        self.assertIn("ram_utilization_percent", rec)
        self.assertIn("gpu_utilization_percent", rec)

    def test_empty_and_single_chunk_responses(self):
        """Verifies handling of empty stream and single-chunk completions."""
        from realtime_profiler import RealtimeProfiler

        profiler = RealtimeProfiler()

        # Empty stream
        empty_res = profiler.profile_request(
            prompt="Empty",
            stream_events_override=[],
            start_time_override=100.0,
        )
        self.assertEqual(empty_res.output_chunks, 0)
        self.assertEqual(empty_res.output_characters, 0)
        self.assertEqual(empty_res.generated_text, "")
        self.assertFalse(empty_res.deadline_missed)

        # Single chunk (only TTFO, zero ICL samples)
        single_events = [{"response": "Hi", "_arrival_perf_counter": 100.02, "done": True}]
        single_res = profiler.profile_request(
            prompt="Hi",
            stream_events_override=single_events,
            start_time_override=100.0,
        )
        self.assertEqual(single_res.output_chunks, 1)
        self.assertEqual(len(single_res.icl_samples_ms), 0)
        self.assertAlmostEqual(single_res.ttfo_ms, 20.0, places=1)

    def test_realtime_trace_export(self):
        """Tests JSON and CSV trace exports for realtime profiling results."""
        from realtime_profiler import RealtimeProfiler
        from trace_exporter import TraceExporter

        profiler = RealtimeProfiler()
        mock_events = [{"response": "Test", "_arrival_perf_counter": 10.05, "done": True}]
        result = profiler.profile_request("prompt", stream_events_override=mock_events, start_time_override=10.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            exporter = TraceExporter(output_dir=temp_dir)
            json_file = exporter.export_realtime_json("realtime_test", [result])
            csv_file = exporter.export_realtime_csv("realtime_test", [result])

            self.assertTrue(os.path.exists(json_file))
            self.assertTrue(os.path.exists(csv_file))
            self.assertGreater(os.path.getsize(json_file), 50)
            self.assertGreater(os.path.getsize(csv_file), 50)


class TestWindowsMonitor(unittest.TestCase):
    """Tests system monitoring on Windows."""

    def test_snapshot_capture_graceful_fallbacks(self):
        from windows_monitor import WindowsWorkloadMonitor

        monitor = WindowsWorkloadMonitor()
        snap = monitor.capture_snapshot()
        self.assertIsNotNone(snap.monitoring_backend)
        # RAM and CPU percent should be valid floats or None if psutil missing
        if monitor.has_psutil:
            self.assertIsInstance(snap.cpu_percent, float)
            self.assertIsInstance(snap.ram_percent, float)
        # GPU utilization should be float or None (graceful fallback)
        self.assertIn(type(snap.gpu_utilization_percent), [float, int, type(None)])


if __name__ == "__main__":
    unittest.main()


