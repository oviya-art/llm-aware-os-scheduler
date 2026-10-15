"""
Discrete-time multi-core CPU simulator.

Part of Member 2: Core Scheduling Algorithm.

Replays a workload (Member 1's inference requests + background CPU-bound jobs)
on `num_cores` identical cores under a pluggable `Scheduler`, in fixed ticks
(default 1 ms). Each tick:
    1. admit tasks whose arrival time has come
    2. ask the scheduler which tasks should run  (-> DISPATCH / PREEMPT events)
    3. advance running tasks; a finished burst emits a token (inference) or
       completes the job (background); token latency is judged against its deadline

Costs modelled: context-switch dead time on a core, plus an extra penalty when a
task migrates to a different core. The same workload (cloned) is used for every
scheduler so comparisons are apples-to-apples. Fully deterministic.

NOTE: this is a *simulation*. It never touches real OS priorities; see
`os_actuator.py` for the optional real-OS hook.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from scheduler_base import Assignment, Scheduler
from task_model import Core, Task, TaskKind, TokenOutcome

EPS = 1e-9


@dataclass
class SimConfig:
    num_cores: int = 4
    tick_ms: float = 1.0                 # physics resolution
    decision_interval_ms: float = 4.0    # scheduler timer tick (Linux HZ=250); also runs on every event
    context_switch_ms: float = 0.1
    migration_ms: float = 0.3
    max_sim_ms: float = 3_600_000.0      # hard stop (1 hour of simulated time)


@dataclass
class DecisionEvent:
    """One row of the scheduling decision log."""
    t_ms: float
    event: str                 # ARRIVE | DISPATCH | PREEMPT | COMPLETE
    core: Optional[int]
    task_id: str
    task_kind: str
    phase: str = ""
    reason: str = ""
    other_task_id: str = ""    # DISPATCH: task displaced   PREEMPT: task that took the core
    laxity_ms: Optional[float] = None
    norm_laxity: Optional[float] = None
    key: Optional[float] = None
    pred_service_ms: Optional[float] = None
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["t_ms"] = round(self.t_ms, 3)
        return d


@dataclass
class SimulationResult:
    scheduler: str
    description: str
    config: SimConfig
    tasks: List[Task]
    events: List[DecisionEvent]
    cores: List[Core]
    makespan_ms: float
    context_switches: int
    preemptions: int
    migrations: int
    truncated: bool = False

    @property
    def inference_tasks(self) -> List[Task]:
        return [t for t in self.tasks if t.kind == TaskKind.INFERENCE]

    @property
    def background_tasks(self) -> List[Task]:
        return [t for t in self.tasks if t.kind == TaskKind.BACKGROUND]


class Simulator:
    def __init__(self, tasks: List[Task], scheduler: Scheduler, config: Optional[SimConfig] = None):
        self.cfg = config or SimConfig()
        self.scheduler = scheduler
        self.tasks = sorted((t.clone() for t in tasks), key=lambda t: (t.arrival_ms, t.task_id))
        self.events: List[DecisionEvent] = []
        self.cores = [Core(i) for i in range(self.cfg.num_cores)]
        self.context_switches = 0
        self.preemptions = 0
        self.migrations = 0
        self._dirty = True               # an event (arrival/token/completion) forces a scheduling pass

    # ----------------------------------------------------------------- logging
    def _log(self, now: float, event: str, core: Optional[int], task: Task, **kw) -> None:
        self.events.append(DecisionEvent(
            t_ms=now, event=event, core=core, task_id=task.task_id,
            task_kind=task.kind.value, phase=task.phase if not task.is_done else "done", **kw))

    # ------------------------------------------------------------------ apply
    def _apply(self, mapping: Dict[int, Assignment], now: float, runnable_ids: set) -> None:
        new_core_of: Dict[str, int] = {}
        for cid, a in mapping.items():
            tid = a.task.task_id
            if tid in new_core_of:
                raise RuntimeError(f"{self.scheduler.name}: task {tid} assigned to two cores")
            if tid not in runnable_ids:
                raise RuntimeError(f"{self.scheduler.name}: task {tid} is not runnable")
            if not (0 <= cid < len(self.cores)):
                raise RuntimeError(f"{self.scheduler.name}: invalid core {cid}")
            new_core_of[tid] = cid

        for core in self.cores:
            a = mapping.get(core.core_id)
            new = a.task if a else None
            old = core.current
            if old is new:
                continue

            if old is not None and old.task_id not in new_core_of:       # descheduled -> preemption
                old.running_core = None
                old.last_run_end_ms = now
                old.preemption_count += 1
                self.preemptions += 1
                self._log(now, "PREEMPT", core.core_id, old,
                          reason=a.reason if a else "idle", other_task_id=new.task_id if new else "",
                          detail=a.detail if a else "")

            if new is not None:
                migrated = new.last_core is not None and new.last_core != core.core_id
                penalty = self.cfg.context_switch_ms + (self.cfg.migration_ms if migrated else 0.0)
                core.penalty_ms = penalty
                new.running_core = core.core_id
                new.last_core = core.core_id
                new.run_since_dispatch_ms = 0.0
                new.dispatch_count += 1
                self.context_switches += 1
                if migrated:
                    new.migration_count += 1
                    self.migrations += 1
                info = a.info
                self._log(now, "DISPATCH", core.core_id, new, reason=a.reason,
                          other_task_id=a.displaced_id, laxity_ms=info.get("laxity_ms"),
                          norm_laxity=info.get("norm_laxity"), key=info.get("key"),
                          pred_service_ms=info.get("pred_service_ms"),
                          detail=a.detail + (" [migrated]" if migrated else ""))
            core.current = new

    # ---------------------------------------------------------------- advance
    def _emit(self, task: Task, t_emit: float) -> None:
        """A burst finished: emit a token (inference) or complete (background)."""
        sched = self.scheduler
        if task.is_inference:
            idx = task.burst_idx
            service = task.bursts_ms[idx]
            if idx == 0:
                latency = t_emit - task.arrival_ms
                deadline = task.ttft_budget_ms
                task.first_token_ms = t_emit
            else:
                latency = t_emit - task.last_emit_ms
                deadline = task.itl_deadline_ms
            task.outcomes.append(TokenOutcome(
                task.task_id, idx, t_emit, latency, deadline, latency > deadline, deadline - latency))
            task.last_emit_ms = t_emit
            sched.on_token_emit(task, t_emit, latency, service, idx)
            self._dirty = True               # token boundary = natural preemption point
        task.burst_idx += 1
        task.burst_done_ms = 0.0

    def _advance(self, now: float) -> List[Task]:
        dt = self.cfg.tick_ms
        finished: List[Task] = []
        for core in self.cores:
            task = core.current
            if task is None:
                continue
            avail = dt
            used = 0.0
            while avail > EPS and not task.is_done:
                if core.penalty_ms > EPS:                      # dead time first
                    p = min(core.penalty_ms, avail)
                    core.penalty_ms -= p
                    core.overhead_ms += p
                    avail -= p
                    used += p
                    continue
                need = task.remaining_burst_ms
                w = min(avail, need)
                task.burst_done_ms += w
                task.cpu_received_ms += w
                task.run_since_dispatch_ms += w
                core.busy_ms += w
                avail -= w
                used += w
                self.scheduler.on_run(task, w, now + used)
                if task.burst_done_ms >= task.current_burst_ms - EPS:
                    self._emit(task, now + used)
            if task.is_done:
                task.finish_ms = now + used
                task.running_core = None
                task.last_run_end_ms = task.finish_ms
                core.current = None
                core.penalty_ms = 0.0
                finished.append(task)
                self._dirty = True
        return finished

    # -------------------------------------------------------------------- run
    def run(self) -> SimulationResult:
        sched = self.scheduler
        sched.reset()
        cfg = self.cfg
        pending: Deque[Task] = deque(self.tasks)
        runnable: List[Task] = []
        now = 0.0
        truncated = False
        makespan = 0.0
        last_decide = -1e18

        while pending or runnable:
            if not runnable and pending and pending[0].arrival_ms > now:
                now = pending[0].arrival_ms                    # skip idle time
            while pending and pending[0].arrival_ms <= now + EPS:
                t = pending.popleft()
                runnable.append(t)
                sched.on_arrival(t, now)
                self._dirty = True
                self._log(now, "ARRIVE", None, t,
                          detail=f"prompt={t.prompt_tokens} bursts={len(t.bursts_ms)} "
                                 f"total_service={t.total_service_ms:.1f}ms")
            if not runnable:
                continue

            if self._dirty or now - last_decide >= cfg.decision_interval_ms - EPS:
                mapping = sched.decide(now, runnable, self.cores)
                self._apply(mapping, now, {t.task_id for t in runnable})
                last_decide = now
                self._dirty = False
            for t in self._advance(now):
                sched.on_complete(t, t.finish_ms)
                self._log(t.finish_ms, "COMPLETE", None, t,
                          detail=f"cpu={t.cpu_received_ms:.1f}ms preemptions={t.preemption_count}")
                makespan = max(makespan, t.finish_ms)
            runnable = [t for t in runnable if not t.is_done]
            now += cfg.tick_ms
            if now > cfg.max_sim_ms:
                truncated = True
                break

        return SimulationResult(
            scheduler=sched.name, description=sched.description, config=cfg, tasks=self.tasks,
            events=self.events, cores=self.cores, makespan_ms=makespan,
            context_switches=self.context_switches, preemptions=self.preemptions,
            migrations=self.migrations, truncated=truncated)
