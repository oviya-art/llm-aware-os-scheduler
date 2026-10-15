"""
Scheduler interface shared by every policy (baselines and the novel one).

The simulator calls `decide()` once per tick. A policy answers with the set of
tasks that should be on the CPUs *right now*; the simulator diffs this against the
current state to emit DISPATCH / PREEMPT events and charge context-switch costs.

Lifecycle hooks give policies the information a real kernel scheduler would have:
    on_arrival     a task became runnable
    on_run         a task just consumed CPU time (runtime accounting)
    on_token_emit  an inference task emitted a token (observed latency + the CPU
                   service that token needed) - only PAST information is exposed
    on_complete    a task finished
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List

from task_model import Core, Task


@dataclass
class Assignment:
    """'Task X should run (on some core)', with the reasoning for the decision log."""
    task: Task
    reason: str = "keep"
    detail: str = ""
    displaced_id: str = ""                        # task this decision pushed off a core
    info: Dict[str, float] = field(default_factory=dict)   # laxity_ms, norm_laxity, key, pred_service_ms ...


class Scheduler(ABC):
    name: str = "base"
    description: str = ""

    def reset(self) -> None:
        """Clear all internal state (called before every simulation run)."""

    def on_arrival(self, task: Task, now_ms: float) -> None: ...
    def on_run(self, task: Task, ran_ms: float, now_ms: float) -> None: ...
    def on_token_emit(self, task: Task, now_ms: float, latency_ms: float,
                      service_ms: float, token_index: int) -> None: ...
    def on_complete(self, task: Task, now_ms: float) -> None: ...

    @abstractmethod
    def decide(self, now_ms: float, runnable: List[Task], cores: List[Core]) -> Dict[int, Assignment]:
        """Return {core_id: Assignment}. At most one task per core, each task at most once."""


def place(selected: List[Assignment], cores: List[Core]) -> Dict[int, Assignment]:
    """Map the chosen tasks onto cores while avoiding needless migrations.

    1. A selected task that is already running stays on its current core.
    2. Remaining tasks prefer the core they last ran on (cache affinity).
    3. Anything left takes the first free core.
    `selected` must be in priority order and contain at most len(cores) entries.
    """
    if len(selected) > len(cores):
        raise ValueError(f"policy selected {len(selected)} tasks for {len(cores)} cores")

    mapping: Dict[int, Assignment] = {}
    by_task = {a.task.task_id: a for a in selected}

    for c in cores:                                   # 1. stay put
        if c.current is not None and c.current.task_id in by_task:
            mapping[c.core_id] = by_task.pop(c.current.task_id)

    leftovers = list(by_task.values())                # insertion order = priority order
    free = [c.core_id for c in cores if c.core_id not in mapping]

    still: List[Assignment] = []
    for a in leftovers:                               # 2. affinity
        lc = a.task.last_core
        if lc is not None and lc in free:
            mapping[lc] = a
            free.remove(lc)
        else:
            still.append(a)
    for a in still:                                   # 3. anything free
        mapping[free.pop(0)] = a
    return mapping
