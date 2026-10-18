"""Turn a Member 2 decision log into a compact "replay" payload for the dashboard.

Segments (per-core Gantt bars) are reconstructed from the DISPATCH / PREEMPT /
COMPLETE events of the decision log; token outcomes come from the simulator.
"""
from __future__ import annotations

from typing import Any, Dict, List

from benchmark.common import SCHEDULER_LABELS, SCHEDULERS, Workload, simulate
from benchmark.metrics_ext import evaluate

MAX_EVENTS = 2600


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def build_run(workload: Workload, scheduler: str, cores: int) -> Dict[str, Any]:
    res = simulate(workload.tasks, scheduler, cores)
    index = {t.task_id: i for i, t in enumerate(sorted(
        res.tasks, key=lambda t: (t.kind.value != "inference", t.arrival_ms, t.task_id)))}
    reasons: List[str] = []
    rid = {}

    def reason_id(r: str) -> int:
        if r not in rid:
            rid[r] = len(reasons)
            reasons.append(r)
        return rid[r]

    # ---- segments
    open_seg: Dict[str, List[Any]] = {}
    segments: List[List[Any]] = []

    def close(tid: str, t: float):
        s = open_seg.pop(tid, None)
        if s and t > s[2]:
            segments.append([s[0], index[tid], _r(s[2], 2), _r(t, 2), s[3]])

    events: List[List[Any]] = []
    for e in res.events:
        if e.event == "DISPATCH":
            close(e.task_id, e.t_ms)
            open_seg[e.task_id] = [e.core, e.task_id, e.t_ms, reason_id(e.reason)]
        elif e.event == "PREEMPT":
            close(e.task_id, e.t_ms)
        elif e.event == "COMPLETE":
            close(e.task_id, e.t_ms)
        code = {"ARRIVE": 0, "DISPATCH": 1, "PREEMPT": 2, "COMPLETE": 3}[e.event]
        events.append([_r(e.t_ms, 2), code, e.core, index[e.task_id], reason_id(e.reason) if e.reason else -1,
                       index.get(e.other_task_id, -1) if e.other_task_id else -1, _r(e.norm_laxity, 3)])
    for tid in list(open_seg):
        close(tid, res.makespan_ms)

    if len(events) > MAX_EVENTS:                                   # keep the feed light: even subsample
        keep = [ev for ev in events if ev[1] in (0, 3)]
        rest = [ev for ev in events if ev[1] in (1, 2)]
        budget = max(200, MAX_EVENTS - len(keep))
        step = len(rest) / budget
        rest = [rest[int(i * step)] for i in range(budget)]
        events = sorted(keep + rest, key=lambda ev: ev[0])

    tokens = []
    for t in res.inference_tasks:
        for o in t.outcomes:
            tokens.append([index[t.task_id], _r(o.emit_ms, 1), _r(o.latency_ms, 2), _r(o.deadline_ms, 1),
                           1 if o.missed else 0, o.token_index])
    tokens.sort(key=lambda r: r[1])

    return {
        "label": SCHEDULER_LABELS.get(scheduler, scheduler),
        "makespan_ms": _r(res.makespan_ms, 1),
        "segments": segments, "events": events, "reasons": reasons, "tokens": tokens,
        "metrics": evaluate(res),
        "decision_count": len(res.events),
    }


def build_replay(workload: Workload, cores: int, schedulers: List[str] = None) -> Dict[str, Any]:
    schedulers = schedulers or SCHEDULERS
    tasks = sorted(workload.tasks, key=lambda t: (not t.is_inference, t.arrival_ms, t.task_id))
    reqs, bgs = [], []
    for t in tasks:
        if t.is_inference:
            reqs.append({"id": t.task_id, "type": t.request_type, "prio": t.priority_label,
                         "arrival_ms": _r(t.arrival_ms, 1), "itl_ms": t.itl_deadline_ms,
                         "ttft_ms": t.ttft_budget_ms, "prompt": t.prompt_tokens,
                         "tokens": len(t.bursts_ms) - 1, "service_ms": _r(t.total_service_ms, 1)})
        else:
            bgs.append({"id": t.task_id, "arrival_ms": _r(t.arrival_ms, 1), "burst_ms": _r(t.total_service_ms, 1)})
    sc = workload.scenario
    return {
        "meta": {"scenario": sc.key, "title": sc.title, "blurb": sc.blurb, "seed": workload.seed, "cores": cores,
                 "background": sc.background, "offered_load": round(workload.offered_load, 3),
                 "trace": workload.trace_path.split("/")[-1]},
        "requests": reqs, "background": bgs,
        "runs": {s: build_run(workload, s, cores) for s in schedulers},
    }
