"""
Core data model for the scheduling engine.

Part of Member 2: Core Scheduling Algorithm.
Project: LLM-Aware OS Scheduler for Latency-Sensitive Local AI Inference.

Everything the simulator schedules is a `Task`:

* INFERENCE task  -> one LLM request. Its CPU demand is a list of "bursts":
      bursts_ms[0]   = prefill burst  (ends with the FIRST token / TTFT)
      bursts_ms[1:]  = one burst per decoded token (each ends with a token emission)
  The per-token service times come straight from Member 1's trace.
* BACKGROUND task -> a CPU-bound job (compression, indexing, build, ...) with a
  single long burst and no latency deadline.

All times inside the engine are in MILLISECONDS on one simulated clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

INF = float("inf")


class TaskKind(str, Enum):
    INFERENCE = "inference"
    BACKGROUND = "background"


# Member 1 exposes priority as descriptive metadata ("high" / "normal" / "low").
PRIORITY_RANK: Dict[str, int] = {"high": 2, "normal": 1, "low": 0}


@dataclass
class TokenOutcome:
    """What actually happened to one emitted token under a given scheduler."""
    task_id: str
    token_index: int        # 0 = first token (TTFT), 1.. = decode tokens
    emit_ms: float          # absolute simulated time of emission
    latency_ms: float       # TTFT (since arrival) for index 0, otherwise ITL
    deadline_ms: float      # TTFT budget for index 0, otherwise ITL target
    missed: bool            # latency_ms > deadline_ms
    slack_ms: float         # deadline_ms - latency_ms (negative = violation)


class Task:
    """A schedulable entity. Static fields describe the work, runtime fields are
    mutated by the simulator. Use `clone()` to get a fresh copy for another run."""

    def __init__(
        self,
        task_id: str,
        kind: TaskKind,
        arrival_ms: float,
        bursts_ms: List[float],
        *,
        itl_deadline_ms: float = 0.0,
        ttft_budget_ms: float = 0.0,
        prompt_tokens: int = 0,
        request_type: str = "standard",
        priority_label: str = "normal",
        category: str = "",
        nice: int = 0,
        source: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ):
        if not bursts_ms:
            raise ValueError(f"Task {task_id} needs at least one burst")
        self.task_id = task_id
        self.kind = kind
        self.arrival_ms = float(arrival_ms)
        self.bursts_ms = [max(0.05, float(b)) for b in bursts_ms]
        self.itl_deadline_ms = float(itl_deadline_ms)
        self.ttft_budget_ms = float(ttft_budget_ms)
        self.prompt_tokens = int(prompt_tokens)
        self.request_type = request_type
        self.priority_label = priority_label
        self.category = category
        self.nice = int(nice)
        self.source = source
        self.meta = dict(meta or {})
        self.reset_runtime()

    # ------------------------------------------------------------------ runtime
    def reset_runtime(self) -> None:
        self.burst_idx: int = 0
        self.burst_done_ms: float = 0.0          # progress inside current burst
        self.cpu_received_ms: float = 0.0
        self.last_emit_ms: float = self.arrival_ms   # deadline anchor
        self.first_token_ms: Optional[float] = None
        self.finish_ms: Optional[float] = None
        self.running_core: Optional[int] = None
        self.last_core: Optional[int] = None
        self.run_since_dispatch_ms: float = 0.0
        self.last_run_end_ms: float = self.arrival_ms  # for aging / wait time
        self.dispatch_count: int = 0
        self.preemption_count: int = 0
        self.migration_count: int = 0
        self.outcomes: List[TokenOutcome] = []

    def clone(self) -> "Task":
        return Task(
            self.task_id, self.kind, self.arrival_ms, list(self.bursts_ms),
            itl_deadline_ms=self.itl_deadline_ms, ttft_budget_ms=self.ttft_budget_ms,
            prompt_tokens=self.prompt_tokens, request_type=self.request_type,
            priority_label=self.priority_label, category=self.category,
            nice=self.nice, source=self.source, meta=self.meta,
        )

    # --------------------------------------------------------------- properties
    @property
    def is_inference(self) -> bool:
        return self.kind == TaskKind.INFERENCE

    @property
    def is_done(self) -> bool:
        return self.burst_idx >= len(self.bursts_ms)

    @property
    def phase(self) -> str:
        if self.is_done:
            return "done"
        if not self.is_inference:
            return "cpu_bound"
        return "prefill" if self.burst_idx == 0 else "decode"

    @property
    def current_burst_ms(self) -> float:
        return self.bursts_ms[self.burst_idx]

    @property
    def remaining_burst_ms(self) -> float:
        return self.current_burst_ms - self.burst_done_ms

    @property
    def total_service_ms(self) -> float:
        return sum(self.bursts_ms)

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK.get(self.priority_label, 1)

    def next_deadline_ms(self) -> float:
        """Absolute time by which the NEXT token must be emitted.

        prefill phase : arrival + TTFT budget
        decode phase  : time of previous token + ITL target
        (Same definition Member 1 uses: a violation is ITL > target.)
        Background tasks have no deadline."""
        if not self.is_inference:
            return INF
        if self.burst_idx == 0:
            return self.arrival_ms + self.ttft_budget_ms
        return self.last_emit_ms + self.itl_deadline_ms

    def wait_ms(self, now_ms: float) -> float:
        """Time since this task last held a core (0 while running)."""
        if self.running_core is not None:
            return 0.0
        return max(0.0, now_ms - self.last_run_end_ms)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Task {self.task_id} {self.kind.value} {self.phase} burst={self.burst_idx}>"


class Core:
    """One simulated CPU core."""
    __slots__ = ("core_id", "current", "penalty_ms", "busy_ms", "overhead_ms")

    def __init__(self, core_id: int):
        self.core_id = core_id
        self.current: Optional[Task] = None
        self.penalty_ms = 0.0     # pending context-switch / migration dead time
        self.busy_ms = 0.0        # time spent doing useful task work
        self.overhead_ms = 0.0    # time lost to context switches / migrations
