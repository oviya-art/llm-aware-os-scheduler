"""
Mixed Workload Simulation Module.

Part of Member 1: Workload Simulation & Latency Profiling Module.
Project: LLM-Aware OS Scheduler for Local AI Inference Workloads.

This module simulates LLM inference requests running alongside configurable
CPU-heavy background tasks (e.g., matrix computation, data compression, or background indexing).

Supported Load Scenarios:
1. LIGHT_LOAD: Low CPU interference (baseline LLM inference running with plenty of headroom).
2. HEAVY_LOAD: High CPU interference (CPU contention slowing down prompt prefill and token generation).
3. MIXED_LOAD: Dynamic/bursty interference (fluctuating background load across time).

IMPORTANT:
- Simulated CPU interference factor is explicitly documented and tracked.
- Real hardware measurements are never fabricated.
- OS scheduling policies or Windows process priorities are NOT modified.
"""

from dataclasses import dataclass, field
from enum import Enum
import math
import random
import time
from typing import Dict, List, Optional, Tuple

from workload_generator import InferenceRequest, WorkloadGenerator
from latency_profiler import LatencyProfiler, RequestProfileResult, SimulatedInferenceBackend
from deadline_model import DeadlineRiskModel, DeadlineEvaluationResult


class LoadScenario(str, Enum):
    """Predefined load environments simulating various OS resource contention levels."""
    LIGHT_LOAD = "light_load"    # ~5-15% CPU contention, interference factor 1.0 - 1.08
    HEAVY_LOAD = "heavy_load"    # ~70-95% CPU contention, interference factor 1.35 - 1.70
    MIXED_LOAD = "mixed_load"    # Variable/bursty contention, factor fluctuates 1.05 - 1.55


@dataclass
class BackgroundTaskConfig:
    """Configuration for simulated or optional physical background tasks."""
    task_name: str
    intensity: float  # 0.0 (idle) to 1.0 (saturating CPU)
    duration_s: float
    is_simulated_interference: bool = True


@dataclass
class MixedWorkloadResult:
    """Aggregated output of a mixed workload simulation execution."""
    scenario: LoadScenario
    total_requests: int
    interference_factor: float
    is_simulated: bool
    requests: List[InferenceRequest]
    profiles: List[RequestProfileResult]
    evaluations: List[DeadlineEvaluationResult]


class MixedWorkloadRunner:
    """Executes mixed inference and background load scenarios."""

    SCENARIO_PROFILES: Dict[LoadScenario, dict] = {
        LoadScenario.LIGHT_LOAD: {
            "base_interference": 1.02,
            "variance": 0.03,
            "description": "Minimal background OS activity; near-native inference throughput.",
        },
        LoadScenario.HEAVY_LOAD: {
            "base_interference": 1.45,
            "variance": 0.10,
            "description": "Sustained CPU-heavy background tasks; noticeable prefill & token latency inflation.",
        },
        LoadScenario.MIXED_LOAD: {
            "base_interference": 1.25,
            "variance": 0.20,
            "description": "Bursty background workloads causing intermittent token deadline misses.",
        },
    }

    def __init__(self, seed: Optional[int] = 42):
        self.seed = seed
        self.rng = random.Random(seed)

    def compute_interference_factor(self, scenario: LoadScenario, current_time_s: float = 0.0) -> float:
        """Calculates a realistic interference multiplier for the scenario."""
        cfg = self.SCENARIO_PROFILES[scenario]
        base = cfg["base_interference"]
        jitter = self.rng.uniform(-cfg["variance"], cfg["variance"])

        if scenario == LoadScenario.MIXED_LOAD:
            # Dynamic wave/burst function: sinusoidal surge + jitter
            burst = 0.18 * math.sin(current_time_s * 1.5)
            factor = base + burst + jitter
        else:
            factor = base + jitter

        return max(1.0, round(factor, 3))

    def run_scenario(
        self,
        scenario: LoadScenario = LoadScenario.MIXED_LOAD,
        num_requests: int = 10,
        arrival_rate_rps: float = 2.0,
    ) -> MixedWorkloadResult:
        """Generates requests, profiles latency under the specified load scenario, and evaluates risk."""
        # 1. Generate requests
        generator = WorkloadGenerator(seed=self.seed)
        requests = generator.generate_workload(
            total_requests=num_requests,
            arrival_rate_rps=arrival_rate_rps,
        )

        # 2. Profile latency with load interference
        profiler = LatencyProfiler(backend=SimulatedInferenceBackend(seed=self.seed))
        deadline_model = DeadlineRiskModel()

        profiles: List[RequestProfileResult] = []
        evaluations: List[DeadlineEvaluationResult] = []

        total_interference = 0.0

        for req in requests:
            interference = self.compute_interference_factor(scenario, req.arrival_time_s)
            total_interference += interference

            profile = profiler.profile_request(req, interference_factor=interference)
            evaluation = deadline_model.evaluate_request(req, profile)

            profiles.append(profile)
            evaluations.append(evaluation)

        avg_interference = round(total_interference / len(requests), 3) if requests else 1.0

        return MixedWorkloadResult(
            scenario=scenario,
            total_requests=len(requests),
            interference_factor=avg_interference,
            is_simulated=True,
            requests=requests,
            profiles=profiles,
            evaluations=evaluations,
        )


if __name__ == "__main__":
    print("--- Mixed Workload Simulation Demonstration ---")
    runner = MixedWorkloadRunner(seed=123)

    for scn in [LoadScenario.LIGHT_LOAD, LoadScenario.HEAVY_LOAD, LoadScenario.MIXED_LOAD]:
        res = runner.run_scenario(scenario=scn, num_requests=3, arrival_rate_rps=1.0)
        print(f"\nScenario: {scn.value.upper()} (Avg Interference Multiplier: {res.interference_factor}x)")
        for req, prof, eval_res in zip(res.requests, res.profiles, res.evaluations):
            print(f"  [{req.request_id}] {req.category:6s} | "
                  f"Deadline: {eval_res.deadline_target_ms:4.1f}ms | "
                  f"Avg ITL: {prof.avg_itl_ms:5.1f}ms | "
                  f"Violations: {eval_res.observed_violation_count:2d}/{eval_res.total_tokens_evaluated:2d} | "
                  f"Risk: {eval_res.predicted_risk_level.value.upper():6s}")
