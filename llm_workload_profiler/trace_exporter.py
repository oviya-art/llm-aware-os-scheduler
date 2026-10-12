"""
Trace Exporter Module for LLM Inference Workloads.

Part of Member 1: Workload Simulation & Latency Profiling Module.
Project: LLM-Aware OS Scheduler for Local AI Inference Workloads.

This module exports standardized workload traces in both CSV and JSON formats
into the `traces/` directory for consumption by Member 2's scheduling algorithms.

Schema Design:
- High-level Experiment Metadata (scenario, random seed, timestamp, simulation flags)
- Request Metadata (request_id, arrival_time, prompt/output tokens, priority, type)
- Raw Observed Measurements (TTFT, ITL percentiles, observed violations, observed slacks)
- Derived / Predicted Analytics (predicted ITL, predicted slack, risk classification)
- Per-token Granular Records (token index, timestamp, ITL)
"""

import csv
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from workload_generator import InferenceRequest
from latency_profiler import RequestProfileResult
from deadline_model import DeadlineEvaluationResult


class TraceExporter:
    """Exports structured simulation traces into standardized CSV and JSON formats."""

    def __init__(self, output_dir: str = "traces"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def _build_combined_record(
        self,
        request: InferenceRequest,
        profile: RequestProfileResult,
        evaluation: DeadlineEvaluationResult,
    ) -> Dict[str, Any]:
        """Merges request metadata, observed profile, and deadline evaluation into one schema."""
        return {
            # 1. Request Identification & Metadata
            "request_id": request.request_id,
            "arrival_time_s": request.arrival_time_s,
            "category": request.category,
            "request_type": request.request_type.value,
            "priority_metadata": request.priority.value,
            "prompt_tokens": request.prompt_tokens,
            "requested_output_tokens": request.output_tokens,
            "deadline_target_itl_ms": evaluation.deadline_target_ms,

            # 2. Raw Measurements & Observed Metrics (Ground Truth)
            "is_simulated": profile.is_simulated,
            "data_source": profile.data_source,
            "ttft_ms": profile.ttft_ms,
            "total_generation_time_ms": profile.total_generation_time_ms,
            "tokens_generated": profile.total_tokens_generated,
            "observed_avg_itl_ms": profile.avg_itl_ms,
            "observed_median_itl_ms": profile.median_itl_ms,
            "observed_p95_itl_ms": profile.p95_itl_ms,
            "observed_p99_itl_ms": profile.p99_itl_ms,
            "observed_violation_count": evaluation.observed_violation_count,
            "observed_violation_rate": evaluation.observed_violation_rate,
            "has_observed_deadline_violation": evaluation.has_observed_deadline_violation,
            "observed_min_slack_ms": evaluation.min_observed_slack_ms,
            "observed_avg_slack_ms": evaluation.avg_observed_slack_ms,
            "observed_p95_slack_ms": evaluation.p95_observed_slack_ms,

            # 3. Derived Predictions & Risk Analysis (Member 2 Input)
            "prediction_method": evaluation.prediction_method,
            "predicted_itl_ms": evaluation.predicted_itl_ms,
            "predicted_slack_ms": evaluation.predicted_slack_ms,
            "predicted_risk_level": evaluation.predicted_risk_level.value,
            "risk_reasoning": evaluation.risk_reasoning,
        }

    def export_json(
        self,
        filename_prefix: str,
        requests: List[InferenceRequest],
        profiles: List[RequestProfileResult],
        evaluations: List[DeadlineEvaluationResult],
        experiment_config: Optional[Dict[str, Any]] = None,
        include_per_token_samples: bool = True,
    ) -> str:
        """Exports complete experiment trace with optional token samples to a JSON file."""
        filepath = os.path.join(self.output_dir, f"{filename_prefix}.json")

        records: List[Dict[str, Any]] = []
        for req, prof, ev in zip(requests, profiles, evaluations):
            rec = self._build_combined_record(req, prof, ev)
            if include_per_token_samples:
                rec["token_records"] = [
                    {
                        "token_index": t.token_index,
                        "timestamp_s": t.timestamp_s,
                        "itl_ms": t.inter_token_latency_ms,
                        "is_simulated": t.is_simulated,
                    }
                    for t in prof.token_records
                ]
            records.append(rec)

        payload = {
            "schema_version": "1.0",
            "module": "Member 1: Workload Simulation & Latency Profiling",
            "export_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "experiment_config": experiment_config or {},
            "total_requests": len(records),
            "requests": records,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return filepath

    def export_csv(
        self,
        filename_prefix: str,
        requests: List[InferenceRequest],
        profiles: List[RequestProfileResult],
        evaluations: List[DeadlineEvaluationResult],
    ) -> str:
        """Exports tabular summary trace to a CSV file."""
        filepath = os.path.join(self.output_dir, f"{filename_prefix}.csv")

        records: List[Dict[str, Any]] = [
            self._build_combined_record(req, prof, ev)
            for req, prof, ev in zip(requests, profiles, evaluations)
        ]

        if not records:
            return filepath

        fieldnames = list(records[0].keys())

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)

        return filepath

    def export_realtime_json(
        self,
        filename_prefix: str,
        results: List[Any],
        experiment_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Exports real-time Ollama profiling results to a structured JSON file."""
        filepath = os.path.join(self.output_dir, f"{filename_prefix}.json")
        records = [r.to_scheduler_record() for r in results]
        
        # Also include chunk records for deep analysis
        detailed_records = []
        for r, rec in zip(results, records):
            item = dict(rec)
            item["chunk_records"] = [
                {
                    "chunk_index": c.chunk_index,
                    "timestamp_s": c.timestamp_s,
                    "inter_chunk_latency_ms": c.inter_chunk_latency_ms,
                    "chunk_char_count": c.chunk_char_count,
                }
                for c in getattr(r, "chunk_records", [])
            ]
            item["generated_text"] = getattr(r, "generated_text", "")
            detailed_records.append(item)

        payload = {
            "schema_version": "2.0",
            "module": "Member 1: Real-Time Workload Profiling & Latency Measurement",
            "export_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "realtime",
            "experiment_config": experiment_config or {},
            "total_requests": len(detailed_records),
            "requests": detailed_records,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return filepath

    def export_realtime_csv(
        self,
        filename_prefix: str,
        results: List[Any],
    ) -> str:
        """Exports real-time Ollama profiling results to a standardized scheduler CSV file."""
        filepath = os.path.join(self.output_dir, f"{filename_prefix}.csv")
        records = [r.to_scheduler_record() for r in results]

        if not records:
            return filepath

        fieldnames = list(records[0].keys())

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)

        return filepath


if __name__ == "__main__":
    from workload_generator import WorkloadGenerator
    from latency_profiler import LatencyProfiler, SimulatedInferenceBackend
    from deadline_model import DeadlineRiskModel

    print("--- Trace Exporter Demonstration ---")
    gen = WorkloadGenerator(seed=42)
    sample_requests = gen.generate_workload(total_requests=2, arrival_rate_rps=1.0)
    profiler = LatencyProfiler(backend=SimulatedInferenceBackend(seed=42))
    model = DeadlineRiskModel()

    sample_profiles = [profiler.profile_request(r) for r in sample_requests]
    sample_evals = [model.evaluate_request(r, p) for r, p in zip(sample_requests, sample_profiles)]

    exporter = TraceExporter(output_dir="traces")
    json_path = exporter.export_json(
        "test_trace", sample_requests, sample_profiles, sample_evals,
        experiment_config={"seed": 42, "scenario": "test_demo"}
    )
    csv_path = exporter.export_csv("test_trace", sample_requests, sample_profiles, sample_evals)

    print(f"Exported JSON trace to: {json_path}")
    print(f"Exported CSV trace to:  {csv_path}")
