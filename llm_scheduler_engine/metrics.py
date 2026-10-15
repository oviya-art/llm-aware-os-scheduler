"""
Metrics computed from a SimulationResult.

Headline metric = `deadline_miss_rate_pct`: share of ALL token deadlines missed,
counting the first token (TTFT budget) AND every decode token (ITL target).
Counting TTFT matters: a non-preemptive scheduler can show a perfect ITL record
simply because requests wait in a queue *before* their first token.

Definitions (same as Member 1 for ITL: a violation is ITL > target):
  itl_violation_rate_pct  decode-token ITL misses / decode tokens
  ttft_miss_rate_pct      requests whose first token came after the TTFT budget
  sla_attained_pct        requests with TTFT met AND <= sla_itl_tolerance of tokens late
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List

from simulator import SimulationResult
from task_model import Task


def percentile(data: List[float], p: float) -> float:
    """Linear-interpolated percentile (same method as Member 1's calculate_percentile)."""
    if not data:
        return 0.0
    s = sorted(data)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _jain(xs: List[float]) -> float:
    if not xs:
        return 1.0
    sq = sum(x * x for x in xs)
    return (sum(xs) ** 2) / (len(xs) * sq) if sq > 0 else 1.0


def request_summary(t: Task, sla_itl_tolerance: float = 0.05) -> Dict[str, Any]:
    """Per-request outcome under the evaluated scheduler."""
    first = [o for o in t.outcomes if o.token_index == 0]
    dec = [o for o in t.outcomes if o.token_index > 0]
    itls = [o.latency_ms for o in dec]
    viol = sum(o.missed for o in dec)
    ttft = first[0].latency_ms if first else None
    ttft_missed = bool(first and first[0].missed)
    completion = (t.finish_ms - t.arrival_ms) if t.finish_ms is not None else None
    iso = t.total_service_ms
    return {
        "request_id": t.task_id,
        "category": t.category,
        "request_type": t.request_type,
        "priority_metadata": t.priority_label,
        "arrival_time_s": round(t.arrival_ms / 1000.0, 4),
        "deadline_target_itl_ms": t.itl_deadline_ms,
        "ttft_budget_ms": t.ttft_budget_ms,
        "ttft_ms": None if ttft is None else round(ttft, 3),
        "ttft_missed": ttft_missed,
        "tokens_generated": len(dec),
        "avg_itl_ms": round(_mean(itls), 3),
        "p95_itl_ms": round(percentile(itls, 95), 3),
        "p99_itl_ms": round(percentile(itls, 99), 3),
        "max_itl_ms": round(max(itls), 3) if itls else 0.0,
        "itl_violation_count": viol,
        "itl_violation_rate_pct": round(100.0 * viol / len(dec), 2) if dec else 0.0,
        "min_slack_ms": round(min((o.slack_ms for o in dec), default=0.0), 3),
        "avg_slack_ms": round(_mean([o.slack_ms for o in dec]), 3),
        "completion_ms": None if completion is None else round(completion, 3),
        "isolated_service_ms": round(iso, 3),
        "slowdown": None if completion is None else round(completion / iso, 3),
        "sla_attained": (not ttft_missed) and (not dec or viol / len(dec) <= sla_itl_tolerance),
        "preemptions": t.preemption_count,
        "migrations": t.migration_count,
        "trace_ttft_ms": t.meta.get("trace_ttft_ms"),
        "trace_avg_itl_ms": t.meta.get("trace_avg_itl_ms"),
    }


def compute_metrics(res: SimulationResult, sla_itl_tolerance: float = 0.05) -> Dict[str, Any]:
    inf = res.inference_tasks
    bg = res.background_tasks
    reqs = [request_summary(t, sla_itl_tolerance) for t in inf]

    dec = [o for t in inf for o in t.outcomes if o.token_index > 0]
    first = [o for t in inf for o in t.outcomes if o.token_index == 0]
    itls = [o.latency_ms for o in dec]
    itl_viol = sum(o.missed for o in dec)
    ttft_miss = sum(o.missed for o in first)
    total_deadlines = len(dec) + len(first)

    by_type: Dict[str, Dict[str, Any]] = {}
    groups: Dict[str, List[Task]] = defaultdict(list)
    for t in inf:
        groups[t.request_type].append(t)
    for typ, ts in sorted(groups.items()):
        d = [o for t in ts for o in t.outcomes if o.token_index > 0]
        f = [o for t in ts for o in t.outcomes if o.token_index == 0]
        l = [o.latency_ms for o in d]
        by_type[typ] = {
            "requests": len(ts),
            "decode_tokens": len(d),
            "itl_violation_rate_pct": round(100.0 * sum(o.missed for o in d) / len(d), 2) if d else 0.0,
            "ttft_miss_rate_pct": round(100.0 * sum(o.missed for o in f) / len(f), 2) if f else 0.0,
            "deadline_miss_rate_pct": round(100.0 * (sum(o.missed for o in d) + sum(o.missed for o in f))
                                            / max(1, len(d) + len(f)), 2),
            "p95_itl_ms": round(percentile(l, 95), 2),
            "p99_itl_ms": round(percentile(l, 99), 2),
            "mean_ttft_ms": round(_mean([o.latency_ms for o in f]), 2),
        }

    done_bg = [t for t in bg if t.finish_ms is not None]
    bg_slow = [(t.finish_ms - t.arrival_ms) / t.total_service_ms for t in done_bg]
    makespan = max(res.makespan_ms, 1e-9)
    busy = sum(c.busy_ms for c in res.cores)
    overhead = sum(c.overhead_ms for c in res.cores)
    capacity = makespan * len(res.cores)
    slowdowns = [r["slowdown"] for r in reqs if r["slowdown"] is not None]
    good_tokens = (len(dec) - itl_viol) + (len(first) - ttft_miss)

    return {
        "scheduler": res.scheduler,
        "description": res.description,
        "num_cores": len(res.cores),
        "num_inference_requests": len(inf),
        "num_background_tasks": len(bg),
        "truncated": res.truncated,
        # ---- headline (inference QoS)
        "deadline_miss_rate_pct": round(100.0 * (itl_viol + ttft_miss) / max(1, total_deadlines), 2),
        "itl_violation_rate_pct": round(100.0 * itl_viol / max(1, len(dec)), 2),
        "itl_violations": itl_viol,
        "decode_tokens": len(dec),
        "ttft_miss_rate_pct": round(100.0 * ttft_miss / max(1, len(first)), 2),
        "sla_attained_pct": round(100.0 * sum(r["sla_attained"] for r in reqs) / max(1, len(reqs)), 2),
        "mean_ttft_ms": round(_mean([o.latency_ms for o in first]), 2),
        "p95_ttft_ms": round(percentile([o.latency_ms for o in first], 95), 2),
        "mean_itl_ms": round(_mean(itls), 3),
        "p95_itl_ms": round(percentile(itls, 95), 3),
        "p99_itl_ms": round(percentile(itls, 99), 3),
        "max_itl_ms": round(max(itls), 3) if itls else 0.0,
        "mean_slack_ms": round(_mean([o.slack_ms for o in dec]), 3),
        "deadline_goodput_tokens_per_s": round(good_tokens / (makespan / 1000.0), 2),
        "inference_slowdown_mean": round(_mean(slowdowns), 3),
        "inference_fairness_jain": round(_jain(slowdowns), 4),
        "by_request_type": by_type,
        # ---- background (cost of protecting inference)
        "bg_completed": len(done_bg),
        "bg_mean_slowdown": round(_mean(bg_slow), 3),
        "bg_max_slowdown": round(max(bg_slow), 3) if bg_slow else 0.0,
        "bg_cpu_ms_done": round(sum(t.cpu_received_ms for t in bg), 1),
        # ---- system
        "makespan_s": round(makespan / 1000.0, 3),
        "cpu_utilization_pct": round(100.0 * busy / capacity, 2),
        "switch_overhead_pct": round(100.0 * overhead / capacity, 3),
        "context_switches": res.context_switches,
        "preemptions": res.preemptions,
        "migrations": res.migrations,
    }
