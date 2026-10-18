"""
Member 3 performance metrics, computed from a Member 2 ``SimulationResult``.

Headline metrics requested by the project brief
-----------------------------------------------
* deadline-miss rate      token level  (TTFT + every ITL) and request level (SLA violated)
* average token latency   mean ITL, plus ITL normalised by each request's own target
* throughput              decode tokens / s over the *inference window*, and deadline goodput
* fairness                Jain's index  J = (sum x)^2 / (n * sum x^2)  over competing tasks

Why more than Member 2's ``compute_metrics``?
  * Member 2 normalises throughput by the *makespan*, which is dominated by the last
    background job, so it hides inference throughput. We use the inference window.
  * A token-level miss rate rewards a scheduler that lets a request wait 20 s for its
    FIRST token and then streams perfectly (that is 1 miss out of hundreds). The
    request-level miss rate and "stall" metrics expose that failure mode.
  * Member 2's fairness is Jain over raw slowdowns; Jain's index is meant for
    allocations where bigger = better, so we apply it to normalised progress 1/slowdown
    and to per-request on-time-token ratios (quality-of-service fairness).
"""
from __future__ import annotations

import math
import statistics as st
from typing import Any, Dict, List

from metrics import compute_metrics, percentile          # Member 2
from simulator import SimulationResult                   # Member 2
from task_model import TaskKind


def jain(xs: List[float]) -> float:
    """Jain's fairness index in (0, 1]; 1 = perfectly equal."""
    xs = [x for x in xs if x is not None and not math.isnan(x)]
    if not xs:
        return 1.0
    sq = sum(x * x for x in xs)
    return (sum(xs) ** 2) / (len(xs) * sq) if sq > 0 else 1.0


def evaluate(res: SimulationResult) -> Dict[str, Any]:
    base = compute_metrics(res)                              # Member 2's numbers, reused verbatim
    inf = res.inference_tasks
    bg = res.background_tasks

    dec = [o for t in inf for o in t.outcomes if o.token_index > 0]
    first = [o for t in inf for o in t.outcomes if o.token_index == 0]
    late = [o for o in dec + first if o.missed]

    # ---- request level
    req_ok, req_quality, progress, norm_itl = [], [], [], []
    for t in inf:
        d = [o for o in t.outcomes if o.token_index > 0]
        f = [o for o in t.outcomes if o.token_index == 0]
        ok_tokens = sum(not o.missed for o in d)
        ttft_ok = bool(f and not f[0].missed)
        viol = len(d) - ok_tokens
        req_ok.append(ttft_ok and (not d or viol / len(d) <= 0.05))
        req_quality.append((ok_tokens + (1 if ttft_ok else 0)) / (len(d) + 1))
        if t.finish_ms is not None:
            progress.append(t.total_service_ms / max(t.finish_ms - t.arrival_ms, 1e-9))
        norm_itl += [o.latency_ms / o.deadline_ms for o in d]

    # ---- throughput over the inference window
    if inf:
        t0 = min(t.arrival_ms for t in inf)
        t1 = max((t.finish_ms or 0.0) for t in inf)
        window_s = max((t1 - t0) / 1000.0, 1e-9)
    else:
        window_s = 1e-9
    good = (len(dec) - sum(o.missed for o in dec)) + (len(first) - sum(o.missed for o in first))

    # ---- background (cost of protection) and starvation
    bg_prog = [t.total_service_ms / max(t.finish_ms - t.arrival_ms, 1e-9)
               for t in bg if t.finish_ms is not None]
    stalls = [o.latency_ms for o in dec]

    ext = {
        # headline
        "token_miss_rate": base["deadline_miss_rate_pct"],
        "request_miss_rate": round(100.0 * (1 - sum(req_ok) / max(1, len(req_ok))), 2),
        "avg_token_latency_ms": base["mean_itl_ms"],
        "avg_norm_itl": round(st.mean(norm_itl), 4) if norm_itl else 0.0,
        "p95_itl_ms": base["p95_itl_ms"],
        "p99_itl_ms": base["p99_itl_ms"],
        "max_itl_ms": base["max_itl_ms"],
        "mean_ttft_ms": base["mean_ttft_ms"],
        "p95_ttft_ms": base["p95_ttft_ms"],
        "ttft_miss_rate": base["ttft_miss_rate_pct"],
        "itl_violation_rate": base["itl_violation_rate_pct"],
        "sla_attained": base["sla_attained_pct"],
        "throughput_tok_s": round(len(dec) / window_s, 3),
        "goodput_tok_s": round(good / window_s, 3),
        "mean_lateness_ms": round(st.mean(o.latency_ms - o.deadline_ms for o in late), 3) if late else 0.0,
        "total_lateness_s": round(sum(o.latency_ms - o.deadline_ms for o in late) / 1000.0, 3),
        # fairness
        "jain_progress": round(jain(progress), 4),
        "jain_quality": round(jain(req_quality), 4),
        "jain_all_progress": round(jain(progress + bg_prog), 4),
        # cost / system
        "bg_mean_slowdown": base["bg_mean_slowdown"],
        "bg_max_slowdown": base["bg_max_slowdown"],
        "inference_slowdown": base["inference_slowdown_mean"],
        "cpu_util": base["cpu_utilization_pct"],
        "switch_overhead_pct": base["switch_overhead_pct"],
        "context_switches": base["context_switches"],
        "preemptions": base["preemptions"],
        "migrations": base["migrations"],
        "makespan_s": base["makespan_s"],
        "decode_tokens": len(dec),
        "requests": len(inf),
        "bg_tasks": len(bg),
        "truncated": bool(res.truncated),
    }
    return ext


# direction: True = higher is better
METRIC_META: Dict[str, Dict[str, Any]] = {
    "token_miss_rate":     {"label": "Token deadline-miss rate", "unit": "%", "higher_better": False, "headline": True},
    "request_miss_rate":   {"label": "Request SLA-violation rate", "unit": "%", "higher_better": False, "headline": True},
    "avg_token_latency_ms": {"label": "Average token latency (ITL)", "unit": "ms", "higher_better": False, "headline": True},
    "p99_itl_ms":          {"label": "p99 inter-token latency", "unit": "ms", "higher_better": False, "headline": True},
    "mean_ttft_ms":        {"label": "Mean time-to-first-token", "unit": "ms", "higher_better": False, "headline": False},
    "throughput_tok_s":    {"label": "Decode throughput", "unit": "tok/s", "higher_better": True, "headline": True},
    "goodput_tok_s":       {"label": "On-time goodput", "unit": "tok/s", "higher_better": True, "headline": False},
    "jain_progress":       {"label": "Jain fairness (progress)", "unit": "", "higher_better": True, "headline": True},
    "jain_quality":        {"label": "Jain fairness (QoS)", "unit": "", "higher_better": True, "headline": False},
    "bg_mean_slowdown":    {"label": "Background slowdown", "unit": "x", "higher_better": False, "headline": False},
    "mean_lateness_ms":    {"label": "Mean lateness when late", "unit": "ms", "higher_better": False, "headline": False},
    "preemptions":         {"label": "Preemptions", "unit": "", "higher_better": False, "headline": False},
}
