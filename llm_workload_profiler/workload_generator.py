"""
Workload Generator Module for LLM Inference Workloads.

Part of Member 1: Workload Simulation & Latency Profiling Module.
Project: LLM-Aware OS Scheduler for Local AI Inference Workloads.

This module generates synthetic, configurable LLM inference requests with metadata
suitable for profiling, deadline evaluation, and feeding downstream scheduling algorithms
(developed by Member 2).
"""

from dataclasses import dataclass, asdict
from enum import Enum
from typing import List, Optional
import random


class RequestType(str, Enum):
    """Category of LLM inference requests reflecting user-facing SLA expectations."""
    INTERACTIVE = "interactive"  # e.g., real-time chat, autocomplete (strict latency)
    STANDARD = "standard"        # e.g., standard question-answering, summarization
    BATCH = "batch"              # e.g., bulk document indexing, background processing


class RequestPriority(str, Enum):
    """Descriptive priority metadata.
    
    NOTE: This is purely application-level descriptive metadata and does NOT
    represent OS scheduling priorities (such as Windows thread priorities or nice values).
    Member 2's scheduling algorithm may use this metadata for policy decisions.
    """
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class InferenceRequest:
    """Represents a single LLM inference request."""
    request_id: str
    arrival_time_s: float
    prompt_tokens: int
    output_tokens: int
    target_itl_ms: float
    request_type: RequestType
    priority: RequestPriority
    category: str  # 'short', 'medium', 'long'

    def to_dict(self) -> dict:
        """Serializes the request into a JSON-friendly dictionary."""
        data = asdict(self)
        data["request_type"] = self.request_type.value
        data["priority"] = self.priority.value
        return data


class WorkloadGenerator:
    """Generates synthetic LLM inference workloads based on configurable distributions."""

    # Default token range configurations for prompt and output tokens
    CATEGORY_PROFILES = {
        "short": {
            "prompt_range": (16, 64),
            "output_range": (16, 64),
            "default_type": RequestType.INTERACTIVE,
            "default_priority": RequestPriority.HIGH,
            "target_itl_ms": 30.0,  # e.g., tight ~33 tokens/sec target
        },
        "medium": {
            "prompt_range": (128, 512),
            "output_range": (64, 256),
            "default_type": RequestType.STANDARD,
            "default_priority": RequestPriority.NORMAL,
            "target_itl_ms": 50.0,  # e.g., ~20 tokens/sec target
        },
        "long": {
            "prompt_range": (512, 2048),
            "output_range": (256, 1024),
            "default_type": RequestType.BATCH,
            "default_priority": RequestPriority.LOW,
            "target_itl_ms": 100.0, # e.g., relaxed ~10 tokens/sec target
        },
    }

    def __init__(self, seed: Optional[int] = 42):
        """Initializes the generator with a deterministic random seed for reproducibility."""
        self.seed = seed
        self.rng = random.Random(seed)

    def set_seed(self, seed: Optional[int]) -> None:
        """Updates the random seed."""
        self.seed = seed
        self.rng = random.Random(seed)

    def generate_single_request(
        self,
        request_id: str,
        arrival_time_s: float,
        category: str = "medium",
        target_itl_ms: Optional[float] = None,
        request_type: Optional[RequestType] = None,
        priority: Optional[RequestPriority] = None,
    ) -> InferenceRequest:
        """Generates a single inference request with randomized or overridden attributes."""
        if category not in self.CATEGORY_PROFILES:
            raise ValueError(f"Unknown category '{category}'. Available: {list(self.CATEGORY_PROFILES.keys())}")

        profile = self.CATEGORY_PROFILES[category]
        prompt_tokens = self.rng.randint(*profile["prompt_range"])
        output_tokens = self.rng.randint(*profile["output_range"])

        chosen_target_itl = target_itl_ms if target_itl_ms is not None else profile["target_itl_ms"]
        chosen_type = request_type if request_type is not None else profile["default_type"]
        chosen_priority = priority if priority is not None else profile["default_priority"]

        return InferenceRequest(
            request_id=request_id,
            arrival_time_s=round(arrival_time_s, 4),
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            target_itl_ms=round(chosen_target_itl, 2),
            request_type=chosen_type,
            priority=chosen_priority,
            category=category,
        )

    def generate_workload(
        self,
        total_requests: int = 15,
        arrival_rate_rps: float = 2.0,
        category_weights: Optional[dict] = None,
        concurrency_limit: Optional[int] = None,
    ) -> List[InferenceRequest]:
        """Generates a sequence of inference requests with simulated arrival timestamps.

        Args:
            total_requests: Total number of requests to generate.
            arrival_rate_rps: Average requests per second (Poisson/exponential inter-arrival).
            category_weights: Distribution weights for short, medium, and long requests.
            concurrency_limit: If set, spaces out arrivals so at most `concurrency_limit`
                               requests arrive in a burst window.

        Returns:
            List of generated InferenceRequest instances ordered by arrival time.
        """
        if total_requests <= 0:
            raise ValueError("total_requests must be positive.")
        if arrival_rate_rps <= 0:
            raise ValueError("arrival_rate_rps must be positive.")

        if category_weights is None:
            # Default distribution: 50% short, 35% medium, 15% long
            category_weights = {"short": 0.50, "medium": 0.35, "long": 0.15}

        categories = list(category_weights.keys())
        weights = list(category_weights.values())

        requests: List[InferenceRequest] = []
        current_time_s = 0.0

        for idx in range(total_requests):
            # Inter-arrival time sampled from exponential distribution (Poisson process)
            inter_arrival_s = self.rng.expovariate(arrival_rate_rps)
            
            # Apply concurrency-aware spacing if requested
            if concurrency_limit and concurrency_limit > 0 and (idx % concurrency_limit == 0) and idx > 0:
                current_time_s += inter_arrival_s + (0.05 * concurrency_limit)
            else:
                current_time_s += inter_arrival_s

            selected_category = self.rng.choices(categories, weights=weights, k=1)[0]
            req_id = f"req_{idx + 1:04d}"

            req = self.generate_single_request(
                request_id=req_id,
                arrival_time_s=current_time_s,
                category=selected_category,
            )
            requests.append(req)

        return requests


if __name__ == "__main__":
    # Self-test demonstration
    print("--- Workload Generator Demonstration ---")
    gen = WorkloadGenerator(seed=100)
    sample_workload = gen.generate_workload(total_requests=6, arrival_rate_rps=1.5)
    for r in sample_workload:
        print(f"[{r.request_id}] Arrival: {r.arrival_time_s:6.3f}s | Type: {r.request_type.value:11s} | "
              f"Priority: {r.priority.value:6s} | Prompt: {r.prompt_tokens:4d} toks | "
              f"Output: {r.output_tokens:4d} toks | ITL Target: {r.target_itl_ms:5.1f} ms")
