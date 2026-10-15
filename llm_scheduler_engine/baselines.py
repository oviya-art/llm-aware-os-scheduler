"""
Baseline schedulers used to benchmark the token-laxity policy.

None of these know about token-latency deadlines (except EDF, which is an ablation
that knows deadlines but has no service-rate / laxity model):

  fcfs      First-Come-First-Served, non-preemptive (head-of-line blocking).
  rr        Round Robin with a fixed time quantum.
  priority  Preemptive static priority (high > normal > low from Member 1's
            `priority_metadata`), Round Robin inside a level - the classic
            POSIX SCHED_RR / Windows-style priority-class behaviour.
  cfs       Simplified Linux CFS: weighted virtual runtime, wakeup/min granularity.
  edf       Preemptive global Earliest-Deadline-First on the next-token deadline.
            Ablation baseline: deadline-aware but ignores how much CPU a token needs.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List

from scheduler_base import Assignment, Scheduler, place
from task_model import Core, INF, Task


class FCFSScheduler(Scheduler):
    name = "fcfs"
    description = "First-Come-First-Served, non-preemptive"

    def decide(self, now_ms: float, runnable: List[Task], cores: List[Core]) -> Dict[int, Assignment]:
        running_ids = {c.current.task_id for c in cores if c.current is not None}
        selected = [Assignment(c.current, "keep") for c in cores if c.current is not None]
        free = len(cores) - len(selected)
        if free > 0:
            waiting = sorted((t for t in runnable if t.task_id not in running_ids),
                             key=lambda t: (t.arrival_ms, t.task_id))
            for t in waiting[:free]:
                selected.append(Assignment(t, "fcfs_head_of_queue", info={"key": t.arrival_ms}))
        return place(selected, cores)


class RoundRobinScheduler(Scheduler):
    name = "rr"
    description = "Round Robin, fixed quantum"

    def __init__(self, quantum_ms: float = 20.0):
        self.quantum_ms = quantum_ms
        self.reset()

    def reset(self) -> None:
        self.queue: Deque[Task] = deque()
        self.slice_ms: Dict[str, float] = {}

    def on_arrival(self, task, now_ms):
        self.queue.append(task)
        self.slice_ms[task.task_id] = 0.0

    def on_run(self, task, ran_ms, now_ms):
        self.slice_ms[task.task_id] = self.slice_ms.get(task.task_id, 0.0) + ran_ms

    def on_complete(self, task, now_ms):
        self.slice_ms.pop(task.task_id, None)

    def decide(self, now_ms, runnable, cores):
        keep: List[Task] = []
        for c in cores:
            t = c.current
            if t is None:
                continue
            if self.slice_ms.get(t.task_id, 0.0) >= self.quantum_ms and self.queue:
                self.queue.append(t)                 # quantum expired -> back of the line
                self.slice_ms[t.task_id] = 0.0
            else:
                keep.append(t)
        selected = [Assignment(t, "keep") for t in keep]
        free = len(cores) - len(keep)
        while free > 0 and self.queue:
            t = self.queue.popleft()
            self.slice_ms[t.task_id] = 0.0
            selected.append(Assignment(t, "rr_next_in_queue"))
            free -= 1
        return place(selected, cores)


class PriorityScheduler(Scheduler):
    name = "priority"
    description = "Preemptive static priority + RR within a level"

    def __init__(self, quantum_ms: float = 20.0):
        self.quantum_ms = quantum_ms
        self.reset()

    def reset(self) -> None:
        self.order: Dict[str, int] = {}
        self.slice_ms: Dict[str, float] = {}
        self.counter = 0

    def on_arrival(self, task, now_ms):
        self.order[task.task_id] = self.counter
        self.counter += 1
        self.slice_ms[task.task_id] = 0.0

    def on_run(self, task, ran_ms, now_ms):
        self.slice_ms[task.task_id] = self.slice_ms.get(task.task_id, 0.0) + ran_ms

    def on_complete(self, task, now_ms):
        self.slice_ms.pop(task.task_id, None)
        self.order.pop(task.task_id, None)

    def decide(self, now_ms, runnable, cores):
        expired = set()
        for c in cores:
            t = c.current
            if t is not None and self.slice_ms.get(t.task_id, 0.0) >= self.quantum_ms:
                self.order[t.task_id] = self.counter          # rotate within its level
                self.counter += 1
                self.slice_ms[t.task_id] = 0.0
                expired.add(t.task_id)

        def key(t: Task):
            sticky = 0 if (t.running_core is not None and t.task_id not in expired) else 1
            return (-t.priority_rank, sticky, self.order[t.task_id])

        ranked = sorted(runnable, key=key)[: len(cores)]
        return place([Assignment(t, "priority_level_%d" % t.priority_rank,
                                 info={"key": float(-t.priority_rank)}) for t in ranked], cores)


class CFSLikeScheduler(Scheduler):
    name = "cfs"
    description = "Simplified Linux CFS (weighted virtual runtime)"

    def __init__(self, min_granularity_ms: float = 3.0, wakeup_granularity_ms: float = 4.0):
        self.min_gran = min_granularity_ms
        self.wakeup_gran = wakeup_granularity_ms
        self.reset()

    def reset(self) -> None:
        self.vr: Dict[str, float] = {}
        self.min_vr = 0.0

    @staticmethod
    def weight(task: Task) -> float:
        return 1024.0 / (1.25 ** task.nice)              # ~Linux prio_to_weight: nice 0 -> 1024

    def on_arrival(self, task, now_ms):
        self.vr[task.task_id] = self.min_vr               # no sleeper credit

    def on_run(self, task, ran_ms, now_ms):
        self.vr[task.task_id] += ran_ms * 1024.0 / self.weight(task)

    def on_complete(self, task, now_ms):
        self.vr.pop(task.task_id, None)

    def decide(self, now_ms, runnable, cores):
        if runnable:
            self.min_vr = max(self.min_vr, min(self.vr[t.task_id] for t in runnable))
        running = [c.current for c in cores if c.current is not None]
        selected = [Assignment(t, "keep") for t in running]
        waiting = sorted((t for t in runnable if t.running_core is None),
                         key=lambda t: self.vr[t.task_id])
        fresh = set()
        free = len(cores) - len(running)
        for t in waiting[:free]:
            selected.append(Assignment(t, "cfs_idle_core", info={"key": self.vr[t.task_id]}))
            fresh.add(t.task_id)
        for w in waiting[free:]:
            best = None
            for i, a in enumerate(selected):             # most-served running task that is preemptible
                r = a.task
                if r.task_id in fresh or r.run_since_dispatch_ms < self.min_gran:
                    continue
                if best is None or self.vr[r.task_id] > self.vr[selected[best].task.task_id]:
                    best = i
            if best is None:
                break
            r = selected[best].task
            if self.vr[r.task_id] - self.vr[w.task_id] > self.wakeup_gran:
                selected[best] = Assignment(
                    w, "cfs_lower_vruntime", displaced_id=r.task_id,
                    detail=f"vruntime {self.vr[w.task_id]:.1f} << running {self.vr[r.task_id]:.1f}",
                    info={"key": self.vr[w.task_id]})
                fresh.add(w.task_id)
            else:
                break
        return place(selected, cores)


class EDFScheduler(Scheduler):
    name = "edf"
    description = "Preemptive global EDF on next-token deadline (no laxity model)"

    def decide(self, now_ms, runnable, cores):
        ranked = sorted(runnable, key=lambda t: (t.next_deadline_ms(), t.arrival_ms, t.task_id))
        ranked = ranked[: len(cores)]
        return place([Assignment(t, "edf_earliest_deadline",
                                 info={"key": t.next_deadline_ms() if t.is_inference else -1.0})
                      for t in ranked], cores)
