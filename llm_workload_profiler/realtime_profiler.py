"""
Real-Time LLM Workload Profiler Module using Ollama.

Part of Member 1: Real-Time Workload Profiling & Latency Measurement Subsystem.
Project: LLM-Aware OS Scheduler for Latency-Sensitive Local AI Inference.

Features:
- Connects to Ollama local API (http://localhost:11434 by default).
- Streams responses incrementally via Ollama's HTTP streaming API.
- Measures wall-clock Time To First Output (TTFO) in milliseconds.
- Measures Inter-Chunk Latency (ICL) for each arriving stream chunk.
- Calculates statistics (average, median, p95, p99 ICL).
- Measures chunks per second throughput and character counts.
- Captures system resource telemetry via WindowsWorkloadMonitor.
- Integrates with DeadlineRiskModel to evaluate target SLA and predicted slack.
- Distinguishes measured wall-clock chunks from simulated token counts.
- Exports real measurements to CSV and JSON for Member 2's scheduler.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.error
import urllib.request

from latency_profiler import calculate_percentile
from deadline_model import DeadlineRiskModel, RiskLevel, DeadlineEvaluationResult
from windows_monitor import WindowsWorkloadMonitor, SystemMetrics


@dataclass
class StreamChunkRecord:
    """Record of an individual streaming output event from the LLM runtime."""
    chunk_index: int
    timestamp_s: float
    inter_chunk_latency_ms: float
    chunk_text: str
    chunk_char_count: int


@dataclass
class RealtimeProfileResult:
    """Comprehensive latency profile and metrics from a live Ollama inference run."""
    request_id: str
    mode: str  # Always "realtime"
    model: str
    prompt: str
    request_start_timestamp_s: float
    request_start_iso: str
    ttfo_ms: float  # Time To First Output (first chunk)
    generation_duration_ms: float
    output_chunks: int
    output_characters: int
    chunks_per_second: float
    generated_text: str
    chunk_records: List[StreamChunkRecord] = field(default_factory=list)
    icl_samples_ms: List[float] = field(default_factory=list)
    average_inter_chunk_latency_ms: float = 0.0
    median_inter_chunk_latency_ms: float = 0.0
    p95_inter_chunk_latency_ms: float = 0.0
    p99_inter_chunk_latency_ms: float = 0.0

    # System metrics snapshot (CPU, RAM, GPU, Ollama process)
    system_metrics: Optional[Dict[str, Any]] = None

    # Deadline & risk analysis (integrated from deadline_model.py)
    deadline_target_ms: float = 50.0
    deadline_missed: bool = False
    deadline_violation_count: int = 0
    deadline_violation_rate: float = 0.0
    min_observed_slack_ms: float = 0.0
    average_observed_slack_ms: float = 0.0
    predicted_slack_ms: float = 0.0
    predicted_risk_level: str = "low"
    risk_reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the result into a clean dictionary."""
        data = asdict(self)
        # Drop large text or format cleanly
        return data

    def to_scheduler_record(self) -> Dict[str, Any]:
        """Standard interface record consumed directly by Member 2's scheduler."""
        cpu_util = None
        ram_util = None
        gpu_util = None
        if self.system_metrics:
            cpu_util = self.system_metrics.get("cpu_percent")
            ram_util = self.system_metrics.get("ram_percent")
            gpu_util = self.system_metrics.get("gpu_utilization_percent")

        return {
            "request_id": self.request_id,
            "mode": self.mode,
            "model": self.model,
            "request_start_time": self.request_start_iso,
            "request_start_timestamp_s": round(self.request_start_timestamp_s, 4),
            "first_output_latency_ms": round(self.ttfo_ms, 2),
            "average_inter_chunk_latency_ms": round(self.average_inter_chunk_latency_ms, 2),
            "p95_inter_chunk_latency_ms": round(self.p95_inter_chunk_latency_ms, 2),
            "p99_inter_chunk_latency_ms": round(self.p99_inter_chunk_latency_ms, 2),
            "generation_duration_ms": round(self.generation_duration_ms, 2),
            "output_chunks": self.output_chunks,
            "output_characters": self.output_characters,
            "chunks_per_second": round(self.chunks_per_second, 2),
            "deadline_ms": round(self.deadline_target_ms, 2),
            "deadline_missed": self.deadline_missed,
            "observed_violation_count": self.deadline_violation_count,
            "min_observed_slack_ms": round(self.min_observed_slack_ms, 2),
            "average_observed_slack_ms": round(self.average_observed_slack_ms, 2),
            "predicted_slack_ms": round(self.predicted_slack_ms, 2),
            "predicted_risk_level": self.predicted_risk_level,
            "cpu_utilization_percent": cpu_util,
            "ram_utilization_percent": ram_util,
            "gpu_utilization_percent": gpu_util,
            "is_simulated": False,
            "measurement_type": "actual_wall_clock_chunks",
        }


class OllamaClient:
    """HTTP client for Ollama local inference API."""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url.rstrip("/")

    def check_health(self, timeout_s: float = 2.0) -> bool:
        """Checks if Ollama service is reachable."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=timeout_s) as response:
                return response.status == 200
        except Exception:
            return False

    def list_models(self, timeout_s: float = 3.0) -> List[str]:
        """Lists installed models in Ollama."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=timeout_s) as response:
                data = json.loads(response.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
                return models
        except Exception:
            return []

    def stream_generate(
        self,
        model: str,
        prompt: str,
        chunk_callback: Optional[Callable[[str], None]] = None,
        timeout_s: float = 120.0,
    ) -> List[Dict[str, Any]]:
        """Sends prompt to Ollama /api/generate and yields parsed stream events.
        
        Returns a list of raw events containing response text and timestamps.
        """
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        events: List[Dict[str, Any]] = []

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                for line in resp:
                    line_str = line.decode("utf-8").strip()
                    if not line_str:
                        continue
                    event_time = time.perf_counter()
                    try:
                        event_data = json.loads(line_str)
                        event_data["_arrival_perf_counter"] = event_time
                        events.append(event_data)
                        text_piece = event_data.get("response", "")
                        if chunk_callback and text_piece:
                            chunk_callback(text_piece)
                        if event_data.get("done", False):
                            break
                    except json.JSONDecodeError:
                        continue
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Could not connect to Ollama at {self.base_url}. "
                "Ensure Ollama is installed and running (`ollama serve`). "
                f"Details: {str(e)}"
            )

        return events


class RealtimeProfiler:
    """Profiles real-time LLM inference requests against Ollama."""

    def __init__(
        self,
        ollama_client: Optional[OllamaClient] = None,
        monitor: Optional[WindowsWorkloadMonitor] = None,
        deadline_model: Optional[DeadlineRiskModel] = None,
    ):
        self.client = ollama_client or OllamaClient()
        self.monitor = monitor or WindowsWorkloadMonitor()
        self.deadline_model = deadline_model or DeadlineRiskModel()

    def profile_request(
        self,
        prompt: str,
        model: str = "llama3.2",
        request_id: Optional[str] = None,
        target_latency_ms: float = 50.0,
        chunk_callback: Optional[Callable[[str], None]] = None,
        stream_events_override: Optional[List[Dict[str, Any]]] = None,
        start_time_override: Optional[float] = None,
    ) -> RealtimeProfileResult:
        """Profiles a single inference request.

        Args:
            prompt: Text prompt to submit.
            model: Model name.
            request_id: Optional identifier.
            target_latency_ms: Maximum acceptable inter-chunk latency SLA.
            chunk_callback: Callback triggered for each streamed token/chunk.
            stream_events_override: Mock stream events for unit testing.
            start_time_override: Mock start time for unit testing.
        """
        req_id = request_id or f"real_{int(time.time()*1000)%1000000:06d}"
        iso_start = datetime.now(timezone.utc).isoformat()

        # Capture initial resource snapshot
        system_snapshot = self.monitor.capture_snapshot()

        start_perf = start_time_override if start_time_override is not None else time.perf_counter()

        # Execute streaming request (or use mock events if testing)
        if stream_events_override is not None:
            raw_events = stream_events_override
            for ev in raw_events:
                txt = ev.get("response", "")
                if chunk_callback and txt:
                    chunk_callback(txt)
        else:
            raw_events = self.client.stream_generate(
                model=model,
                prompt=prompt,
                chunk_callback=chunk_callback,
            )

        end_perf = time.perf_counter() if start_time_override is None else (
            raw_events[-1]["_arrival_perf_counter"] if raw_events else start_perf
        )

        total_gen_duration_ms = max(0.0, (end_perf - start_perf) * 1000.0)

        # Process streamed chunks
        chunk_records: List[StreamChunkRecord] = []
        icl_samples: List[float] = []
        generated_text_parts = []
        ttfo_ms = 0.0

        prev_timestamp_s = start_perf

        chunk_idx = 0
        for ev in raw_events:
            text_piece = ev.get("response", "")
            # Only count events that contain output text or mark completion
            arrival_perf = ev.get("_arrival_perf_counter", prev_timestamp_s)
            
            chunk_latency_ms = (arrival_perf - prev_timestamp_s) * 1000.0

            if chunk_idx == 0:
                ttfo_ms = chunk_latency_ms
            else:
                icl_samples.append(chunk_latency_ms)

            prev_timestamp_s = arrival_perf
            chunk_idx += 1

            chunk_records.append(
                StreamChunkRecord(
                    chunk_index=chunk_idx,
                    timestamp_s=round(arrival_perf - start_perf, 4),
                    inter_chunk_latency_ms=round(chunk_latency_ms, 2),
                    chunk_text=text_piece,
                    chunk_char_count=len(text_piece),
                )
            )
            if text_piece:
                generated_text_parts.append(text_piece)

        generated_text = "".join(generated_text_parts)
        num_chunks = len(chunk_records)
        num_chars = len(generated_text)

        # Calculate throughput
        duration_s = total_gen_duration_ms / 1000.0
        chunks_per_sec = (num_chunks / duration_s) if duration_s > 0 else 0.0

        # Percentiles
        if icl_samples:
            avg_icl = sum(icl_samples) / len(icl_samples)
            median_icl = calculate_percentile(icl_samples, 50.0)
            p95_icl = calculate_percentile(icl_samples, 95.0)
            p99_icl = calculate_percentile(icl_samples, 99.0)
        else:
            avg_icl = ttfo_ms
            median_icl = ttfo_ms
            p95_icl = ttfo_ms
            p99_icl = ttfo_ms

        # Evaluate deadlines & risk using existing deadline_model logic
        violations = [c for c in icl_samples if c > target_latency_ms]
        violation_count = len(violations)
        violation_rate = (violation_count / len(icl_samples)) if icl_samples else 0.0
        deadline_missed = violation_count > 0

        min_slack = (target_latency_ms - max(icl_samples)) if icl_samples else (target_latency_ms - ttfo_ms)
        avg_slack = target_latency_ms - avg_icl

        # Calculate predicted slack using sequential EWMA
        predicted_next = self.deadline_model.predict_next_itl(
            icl_samples,
            fallback_target_ms=target_latency_ms,
        )
        predicted_slack = target_latency_ms - predicted_next
        slack_ratio = predicted_slack / target_latency_ms if target_latency_ms > 0 else 0.0

        if predicted_slack <= 0 or slack_ratio < self.deadline_model.high_risk_threshold:
            risk_level = "high"
            reason = f"Predicted inter-chunk slack deficit: {predicted_slack:.1f}ms"
        elif slack_ratio < self.deadline_model.medium_risk_threshold:
            risk_level = "medium"
            reason = f"Moderate predicted buffer: {predicted_slack:.1f}ms"
        else:
            risk_level = "low"
            reason = f"Healthy predicted buffer: {predicted_slack:.1f}ms"

        return RealtimeProfileResult(
            request_id=req_id,
            mode="realtime",
            model=model,
            prompt=prompt,
            request_start_timestamp_s=round(start_perf, 4),
            request_start_iso=iso_start,
            ttfo_ms=round(ttfo_ms, 2),
            generation_duration_ms=round(total_gen_duration_ms, 2),
            output_chunks=num_chunks,
            output_characters=num_chars,
            chunks_per_second=round(chunks_per_sec, 2),
            generated_text=generated_text,
            chunk_records=chunk_records,
            icl_samples_ms=[round(x, 2) for x in icl_samples],
            average_inter_chunk_latency_ms=round(avg_icl, 2),
            median_inter_chunk_latency_ms=round(median_icl, 2),
            p95_inter_chunk_latency_ms=round(p95_icl, 2),
            p99_inter_chunk_latency_ms=round(p99_icl, 2),
            system_metrics=system_snapshot.to_dict(),
            deadline_target_ms=round(target_latency_ms, 2),
            deadline_missed=deadline_missed,
            deadline_violation_count=violation_count,
            deadline_violation_rate=round(violation_rate, 4),
            min_observed_slack_ms=round(min_slack, 2),
            average_observed_slack_ms=round(avg_slack, 2),
            predicted_slack_ms=round(predicted_slack, 2),
            predicted_risk_level=risk_level,
            risk_reasoning=reason,
        )


if __name__ == "__main__":
    print("--- Realtime Profiler Check ---")
    client = OllamaClient()
    alive = client.check_health()
    print(f"Ollama running on localhost:11434: {alive}")
    if alive:
        models = client.list_models()
        print(f"Installed models: {models}")
    else:
        print("Note: Ollama is not active. Use simulation mode or start Ollama (`ollama serve`).")
