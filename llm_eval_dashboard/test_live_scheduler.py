"""
Automated unit tests for the Live Application Workload Scheduler.

Does NOT require Ollama to be running (all network and streaming calls are mocked).
Covers:
- FCFS arrival ordering
- Priority ordering and tie-breaking
- EDF ordering
- Token Laxity scoring & normalization
- Single active request execution constraint
- Thread-safe concurrent submissions
- Streaming chunks, TTFO, and inter-chunk measurements
- Queue recovery after errors
- Request state transitions & cancellation
- Deadline met/missed logic
- Metrics computation and CSV export
"""

import csv
import io
import json
import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from live_scheduler import (
    LiveRequest,
    LiveSchedulerController,
    OllamaService,
    FCFSPolicy,
    PriorityPolicy,
    EDFPolicy,
    TokenLaxityPolicy,
    POLICIES,
)


class MockOllamaService(OllamaService):
    """Mocked Ollama service for predictable unit tests without network."""

    def __init__(self, chunks=None, delay=0.01, fail_on=None):
        super().__init__(base_url="http://mock-ollama:11434")
        self.chunks = chunks or ["Hello", " world", " from", " local", " AI!"]
        self.delay = delay
        self.fail_on = fail_on or set()
        self.call_count = 0

    def check_health(self, timeout_s=2.0):
        return True

    def list_models(self, timeout_s=3.0):
        return ["mock-llama:latest", "mock-mistral:latest"]

    def stream_chat(self, model, messages, chunk_callback, timeout_s=180.0):
        self.call_count += 1
        if self.call_count in self.fail_on or model in self.fail_on:
            raise ConnectionError(f"Simulated connection failure for call {self.call_count}")

        for chunk in self.chunks:
            if self.delay > 0:
                time.sleep(self.delay)
            chunk_callback(chunk, time.perf_counter())

        return {
            "total_duration": 100000000,
            "eval_count": len(self.chunks),
            "eval_duration": 80000000,
        }


# ============================================================================
# 1. Queue Policy Ordering Tests
# ============================================================================

class TestQueuePolicies(unittest.TestCase):

    def setUp(self):
        self.now = 1000.0

    def test_fcfs_arrival_ordering(self):
        policy = FCFSPolicy()
        r1 = LiveRequest("r1", "c1", "p1", "m", arrival_perf=100.0, arrival_index=1)
        r2 = LiveRequest("r2", "c1", "p2", "m", arrival_perf=105.0, arrival_index=2)
        r3 = LiveRequest("r3", "c1", "p3", "m", arrival_perf=95.0, arrival_index=3)

        ranked = policy.rank([r1, r2, r3], self.now)
        self.assertEqual([r.request_id for r in ranked], ["r3", "r1", "r2"])

    def test_priority_ordering_and_tie_breaking(self):
        policy = PriorityPolicy()
        r_low = LiveRequest("r_low", "c1", "p", "m", priority="low", arrival_perf=10.0, arrival_index=1)
        r_norm1 = LiveRequest("r_norm1", "c1", "p", "m", priority="normal", arrival_perf=20.0, arrival_index=2)
        r_norm2 = LiveRequest("r_norm2", "c1", "p", "m", priority="normal", arrival_perf=25.0, arrival_index=3)
        r_high = LiveRequest("r_high", "c1", "p", "m", priority="high", arrival_perf=30.0, arrival_index=4)

        # High should come first, then normals in FIFO arrival order, then low
        ranked = policy.rank([r_low, r_norm1, r_norm2, r_high], self.now)
        self.assertEqual([r.request_id for r in ranked], ["r_high", "r_norm1", "r_norm2", "r_low"])

    def test_edf_ordering(self):
        policy = EDFPolicy()
        # r1 arrives at t=100, deadline_budget=5000ms -> abs deadline = 105.0
        r1 = LiveRequest("r1", "c1", "p", "m", arrival_perf=100.0, deadline_ms=5000.0, arrival_index=1)
        # r2 arrives at t=102, deadline_budget=1000ms -> abs deadline = 103.0 (sooner!)
        r2 = LiveRequest("r2", "c1", "p", "m", arrival_perf=102.0, deadline_ms=1000.0, arrival_index=2)
        # r3 arrives at t=90, deadline_budget=20000ms -> abs deadline = 110.0
        r3 = LiveRequest("r3", "c1", "p", "m", arrival_perf=90.0, deadline_ms=20000.0, arrival_index=3)

        ranked = policy.rank([r1, r2, r3], self.now)
        self.assertEqual([r.request_id for r in ranked], ["r2", "r1", "r3"])

    def test_token_laxity_scoring(self):
        policy = TokenLaxityPolicy(avg_ms_per_char=10.0, base_overhead_s=0.5)
        # Urgent request: 2000ms deadline, high priority
        r_urgent = LiveRequest("urgent", "c1", "short", "m", priority="high", deadline_ms=2000.0, arrival_perf=100.0, arrival_index=1)
        # Relaxed request: 30000ms deadline, low priority
        r_relaxed = LiveRequest("relaxed", "c1", "long prompt with many words", "m", priority="low", deadline_ms=30000.0, arrival_perf=100.0, arrival_index=2)

        ranked = policy.rank([r_relaxed, r_urgent], now_perf=101.0)
        self.assertEqual(ranked[0].request_id, "urgent")


# ============================================================================
# 2. Concurrency, Queue Execution & State Transition Tests
# ============================================================================

class TestSchedulerConcurrencyAndExecution(unittest.TestCase):

    def test_single_active_request_concurrency(self):
        """Verifies only one request executes at a time while others wait in queue."""
        mock_ollama = MockOllamaService(delay=0.08)
        controller = LiveSchedulerController(ollama_service=mock_ollama, active_policy="fcfs")

        try:
            r1 = controller.submit_request("prompt 1", "mock-llama:latest")
            r2 = controller.submit_request("prompt 2", "mock-llama:latest")
            r3 = controller.submit_request("prompt 3", "mock-llama:latest")

            time.sleep(0.04)  # Give worker time to pick up r1
            status = controller.get_queue_status()
            self.assertTrue(status["is_busy"])
            self.assertEqual(status["active_request"]["request_id"], r1.request_id)
            self.assertEqual(status["queued_count"], 2)

            # Wait for all to complete
            for r in (r1, r2, r3):
                item = r.chunk_queue.get(timeout=3.0)
                while item.get("type") == "chunk":
                    item = r.chunk_queue.get(timeout=3.0)
                self.assertEqual(item.get("type"), "done")
                self.assertEqual(r.status, "completed")

            final_status = controller.get_queue_status()
            self.assertEqual(final_status["queued_count"], 0)
            self.assertFalse(final_status["is_busy"])
        finally:
            controller.shutdown()

    def test_thread_safe_concurrent_submissions(self):
        """Simultaneous browser submissions cannot bypass or corrupt the queue."""
        mock_ollama = MockOllamaService(delay=0.01)
        controller = LiveSchedulerController(ollama_service=mock_ollama)

        submitted_ids = []
        threads = []

        def submit_worker(i):
            req = controller.submit_request(f"concurrent prompt {i}", "mock-llama:latest")
            submitted_ids.append(req.request_id)

        try:
            for i in range(15):
                t = threading.Thread(target=submit_worker, args=(i,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            self.assertEqual(len(submitted_ids), 15)
            self.assertEqual(len(set(submitted_ids)), 15)  # All IDs unique
        finally:
            controller.shutdown()

    def test_cancellation_of_queued_request(self):
        mock_ollama = MockOllamaService(delay=0.1)
        controller = LiveSchedulerController(ollama_service=mock_ollama)

        try:
            r1 = controller.submit_request("slow 1", "mock-llama:latest")
            r2 = controller.submit_request("slow 2", "mock-llama:latest")

            time.sleep(0.03)  # r1 starts running, r2 is in queue
            cancelled = controller.cancel_request(r2.request_id)
            self.assertTrue(cancelled)
            self.assertEqual(r2.status, "cancelled")

            # Check queue item
            cancel_msg = r2.chunk_queue.get(timeout=1.0)
            self.assertEqual(cancel_msg["type"], "cancelled")
        finally:
            controller.shutdown()


# ============================================================================
# 3. Streaming, Measurements & Error Recovery Tests
# ============================================================================

class TestStreamingAndRecovery(unittest.TestCase):

    def test_streaming_success_and_latency_measurements(self):
        mock_ollama = MockOllamaService(chunks=["Alpha", "Beta", "Gamma"], delay=0.02)
        controller = LiveSchedulerController(ollama_service=mock_ollama)

        try:
            req = controller.submit_request("stream test", "mock-llama:latest", deadline_ms=5000.0)
            chunks = []

            while True:
                ev = req.chunk_queue.get(timeout=2.0)
                if ev["type"] == "chunk":
                    chunks.append(ev)
                elif ev["type"] == "done":
                    break

            self.assertEqual(len(chunks), 3)
            self.assertTrue(chunks[0]["first_chunk"])
            self.assertFalse(chunks[1]["first_chunk"])

            # Verify metrics
            m = req.metrics
            self.assertGreater(m["ttfo_ms"], 0.0)
            self.assertGreater(m["average_icl_ms"], 0.0)
            self.assertEqual(m["total_chunks"], 3)
            self.assertFalse(m["deadline_missed"])
            self.assertEqual(req.status, "completed")
        finally:
            controller.shutdown()

    def test_queue_recovery_after_ollama_failure(self):
        """If Ollama throws an error on request 1, request 2 must still run cleanly."""
        mock_ollama = MockOllamaService(fail_on={1}, delay=0.01)  # Fail call 1
        controller = LiveSchedulerController(ollama_service=mock_ollama)

        try:
            r_fail = controller.submit_request("will fail", "mock-llama:latest")
            r_pass = controller.submit_request("will succeed", "mock-llama:latest")

            # First should fail
            ev1 = r_fail.chunk_queue.get(timeout=2.0)
            self.assertEqual(ev1["type"], "error")
            self.assertEqual(r_fail.status, "failed")

            # Second should succeed immediately after
            ev2 = r_pass.chunk_queue.get(timeout=2.0)
            while ev2["type"] == "chunk":
                ev2 = r_pass.chunk_queue.get(timeout=2.0)
            self.assertEqual(ev2["type"], "done")
            self.assertEqual(r_pass.status, "completed")
        finally:
            controller.shutdown()

    def test_deadline_miss_calculation(self):
        """Simulate a short SLA budget that gets exceeded."""
        mock_ollama = MockOllamaService(chunks=["A", "B"], delay=0.05)
        controller = LiveSchedulerController(ollama_service=mock_ollama)

        try:
            # 50ms deadline, but mock takes >100ms
            req = controller.submit_request("deadline test", "mock-llama:latest", deadline_ms=50.0)
            while True:
                ev = req.chunk_queue.get(timeout=2.0)
                if ev["type"] == "done":
                    break
            self.assertTrue(req.metrics["deadline_missed"])
        finally:
            controller.shutdown()


# ============================================================================
# 4. Conversation History & Evaluation Metrics Tests
# ============================================================================

class TestConversationAndEvaluationMetrics(unittest.TestCase):

    def test_conversation_history_persistence_and_isolation(self):
        mock_ollama = MockOllamaService(chunks=["Response A"], delay=0.01)
        controller = LiveSchedulerController(ollama_service=mock_ollama)

        try:
            r1 = controller.submit_request("Turn 1", "mock-llama:latest", conversation_id="user_123")
            while r1.chunk_queue.get(timeout=2.0)["type"] != "done":
                pass

            hist = controller.get_conversation_history("user_123")
            self.assertEqual(len(hist), 2)
            self.assertEqual(hist[0], {"role": "user", "content": "Turn 1"})
            self.assertEqual(hist[1], {"role": "assistant", "content": "Response A"})

            # Another user's conversation is empty
            self.assertEqual(controller.get_conversation_history("user_456"), [])

            # Clear user_123
            controller.clear_conversation("user_123")
            self.assertEqual(controller.get_conversation_history("user_123"), [])
        finally:
            controller.shutdown()

    def test_metrics_summary_and_csv_export(self):
        mock_ollama = MockOllamaService(chunks=["Test"], delay=0.01)
        controller = LiveSchedulerController(ollama_service=mock_ollama)

        try:
            r1 = controller.submit_request("test eval", "mock-llama:latest", policy="fcfs")
            while r1.chunk_queue.get(timeout=2.0)["type"] != "done":
                pass

            summary = controller.get_metrics_summary()
            self.assertEqual(summary["total_requests"], 1)
            self.assertEqual(summary["completed_requests"], 1)
            self.assertEqual(summary["source"], "live_ollama")
            self.assertIn("median_wait_ms", summary)
            self.assertIn("median_ttfo_ms", summary)
            self.assertIn("sample_counts", summary)
            self.assertEqual(summary["sample_counts"]["completed"], 1)
            self.assertIn("fcfs", summary["runs_by_policy"])

            csv_text = controller.export_metrics_csv()
            reader = csv.DictReader(io.StringIO(csv_text))
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["request_id"], r1.request_id)
            self.assertEqual(rows[0]["source"], "live_ollama")
            self.assertEqual(rows[0]["status"], "completed")

            json_text = controller.export_metrics_json()
            data = json.loads(json_text)
            self.assertEqual(data["source"], "live_ollama")
            self.assertIn("metrics", data)
            self.assertIn("timeline", data)
        finally:
            controller.shutdown()

    def test_timeline_events_and_dispatch_order(self):
        """Verifies dispatch_index and events are recorded accurately."""
        mock_ollama = MockOllamaService(chunks=["Chunk 1", "Chunk 2"], delay=0.04)
        # Use priority policy
        controller = LiveSchedulerController(ollama_service=mock_ollama, active_policy="priority")

        try:
            # First submit a normal request to start running
            r_first = controller.submit_request("first", "mock-llama:latest", priority="normal")
            time.sleep(0.02)  # Give worker time to pick it up

            # Now while r_first is running, enqueue low then high
            r_low = controller.submit_request("low priority", "mock-llama:latest", priority="low")
            r_high = controller.submit_request("high priority", "mock-llama:latest", priority="high")

            # Wait for all to complete
            for r in (r_first, r_high, r_low):
                while r.chunk_queue.get(timeout=3.0)["type"] != "done":
                    pass

            tl = controller.get_timeline()
            self.assertEqual(tl["source"], "live_ollama")
            self.assertGreaterEqual(len(tl["events"]), 3)

            # High should have dispatched BEFORE low, even though low arrived first
            self.assertIsNotNone(r_first.dispatch_index)
            self.assertIsNotNone(r_high.dispatch_index)
            self.assertIsNotNone(r_low.dispatch_index)

            self.assertEqual(r_first.dispatch_index, 1)
            self.assertEqual(r_high.dispatch_index, 2)
            self.assertEqual(r_low.dispatch_index, 3)

            # Arrival index preserves submission order: r_first=1, r_low=2, r_high=3
            self.assertEqual(r_first.arrival_index, 1)
            self.assertEqual(r_low.arrival_index, 2)
            self.assertEqual(r_high.arrival_index, 3)
        finally:
            controller.shutdown()

    def test_batch_submission(self):
        """Tests quick batch injection functionality."""
        mock_ollama = MockOllamaService(chunks=["OK"], delay=0.01)
        controller = LiveSchedulerController(ollama_service=mock_ollama)

        try:
            batch_res = controller.submit_batch()
            self.assertEqual(len(batch_res), 4)
            req_ids = [b["request_id"] for b in batch_res]
            for rid in req_ids:
                req = controller.get_request(rid)
                self.assertIsNotNone(req)
        finally:
            controller.shutdown()


if __name__ == "__main__":
    unittest.main()
