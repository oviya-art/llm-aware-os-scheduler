"""
Decision-log exporter: the deliverable of Member 2.

For every scheduler run it writes (into `decision_logs/`):

  <prefix>_<scheduler>_decisions.json   full run: config, metrics, decisions, per-token outcomes
  <prefix>_<scheduler>_decisions.csv    one row per scheduling decision (ARRIVE/DISPATCH/PREEMPT/COMPLETE)
  <prefix>_<scheduler>_requests.csv     one row per inference request (scheduled outcome)
  <prefix>_<scheduler>_tokens.csv       one row per emitted token (latency, deadline, slack, missed)
and, for a multi-scheduler comparison:
  <prefix>_comparison.json / .csv       headline metrics per scheduler (for Member 3's dashboard)

Decision-log row schema
-----------------------
t_ms            simulated time of the decision
event           ARRIVE | DISPATCH | PREEMPT | COMPLETE
core            core id (blank for ARRIVE / COMPLETE)
task_id         the task the event is about
task_kind       inference | background
phase           prefill | decode | cpu_bound | done
reason          why (e.g. fill_idle_core, preempt_background, preempt_token_boundary,
                preempt_critical_mid_token, bg_aging_rotation, anti_starvation_token, ...)
other_task_id   DISPATCH: the task that was displaced   PREEMPT: the task that took the core
laxity_ms       predicted token laxity of the dispatched task (TLS only)
norm_laxity     laxity / deadline (TLS only)
key             policy-specific priority key (TLS urgency key, EDF deadline, CFS vruntime...)
pred_service_ms EWMA-predicted CPU time needed for the next token (TLS only)
detail          human-readable explanation
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, List

from metrics import compute_metrics, request_summary
from simulator import SimulationResult

SCHEMA_VERSION = "1.0"
DECISION_FIELDS = ["t_ms", "event", "core", "task_id", "task_kind", "phase", "reason",
                   "other_task_id", "laxity_ms", "norm_laxity", "key", "pred_service_ms", "detail"]
TOKEN_FIELDS = ["request_id", "token_index", "emit_time_ms", "latency_ms", "deadline_ms",
                "slack_ms", "missed"]


def _write_csv(path: str, fields: List[str], rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def token_rows(res: SimulationResult) -> List[Dict[str, Any]]:
    rows = []
    for t in res.inference_tasks:
        for o in t.outcomes:
            rows.append({"request_id": o.task_id, "token_index": o.token_index,
                         "emit_time_ms": round(o.emit_ms, 3), "latency_ms": round(o.latency_ms, 3),
                         "deadline_ms": o.deadline_ms, "slack_ms": round(o.slack_ms, 3),
                         "missed": o.missed})
    return rows


def export_run(res: SimulationResult, out_dir: str, prefix: str = "run",
               source_trace: str = "", extra_config: Dict[str, Any] = None) -> Dict[str, str]:
    """Write all artefacts for one scheduler run; returns {kind: path}."""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"{prefix}_{res.scheduler}")
    metrics = compute_metrics(res)
    decisions = [e.to_dict() for e in res.events]
    tokens = token_rows(res)
    requests = [request_summary(t) for t in res.inference_tasks]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "producer": "Member 2 - llm_scheduler_engine",
        "scheduler": res.scheduler,
        "description": res.description,
        "source_trace": source_trace,
        "simulator_config": res.config.__dict__,
        "extra_config": extra_config or {},
        "metrics": metrics,
        "requests": requests,
        "decisions": decisions,
        "token_outcomes": tokens,
    }
    paths = {
        "json": base + "_decisions.json",
        "decisions_csv": base + "_decisions.csv",
        "requests_csv": base + "_requests.csv",
        "tokens_csv": base + "_tokens.csv",
    }
    with open(paths["json"], "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    _write_csv(paths["decisions_csv"], DECISION_FIELDS, decisions)
    _write_csv(paths["requests_csv"], list(requests[0].keys()) if requests else ["request_id"], requests)
    _write_csv(paths["tokens_csv"], TOKEN_FIELDS, tokens)
    return paths


COMPARISON_FIELDS = [
    "scheduler", "deadline_miss_rate_pct", "itl_violation_rate_pct", "ttft_miss_rate_pct",
    "sla_attained_pct", "mean_ttft_ms", "p95_ttft_ms", "mean_itl_ms", "p95_itl_ms", "p99_itl_ms",
    "max_itl_ms", "mean_slack_ms", "deadline_goodput_tokens_per_s", "inference_slowdown_mean",
    "inference_fairness_jain", "bg_completed", "bg_mean_slowdown", "bg_max_slowdown",
    "makespan_s", "cpu_utilization_pct", "switch_overhead_pct", "context_switches",
    "preemptions", "migrations",
]


def export_comparison(metrics_list: List[Dict[str, Any]], out_dir: str, prefix: str = "run",
                      extra_config: Dict[str, Any] = None) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = {"json": os.path.join(out_dir, f"{prefix}_comparison.json"),
             "csv": os.path.join(out_dir, f"{prefix}_comparison.csv")}
    with open(paths["json"], "w", encoding="utf-8") as fh:
        json.dump({"schema_version": SCHEMA_VERSION, "config": extra_config or {},
                   "schedulers": metrics_list}, fh, indent=2)
    _write_csv(paths["csv"], COMPARISON_FIELDS, metrics_list)
    return paths
