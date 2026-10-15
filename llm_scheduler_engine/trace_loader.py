"""
Trace loader: converts Member 1's exported traces into schedulable Tasks.

Supports (auto-detected):
  * simulation JSON  (schema 1.0)  - per-token `token_records[].itl_ms`
  * realtime   JSON  (schema 2.0)  - per-chunk `chunk_records[].inter_chunk_latency_ms`
  * simulation / realtime CSV      - summary rows only; per-token demand is
                                     synthesised from the average latency (flagged in notes)

How a trace becomes a scheduling problem
----------------------------------------
Member 1's trace says "this request took X ms for the first token and Y_k ms for
token k". The scheduler engine reinterprets that as CPU SERVICE DEMAND:

    bursts_ms = [ prefill_ms, itl_1, itl_2, ..., itl_N ]  * demand_scale

and then re-simulates how long each token takes when the request must COMPETE for
cores with other requests and background CPU-bound jobs.

demand_scale
------------
Member 1's synthetic backend produces ITL ~= target * interference_factor, i.e. the
traced latency already contains Member 1's *simulated* contention. Re-using it
unchanged would double-count contention and would leave zero headroom (a request
would miss ~50% of its own deadlines even on a dedicated core). So for simulation
traces we recover the uncontended service time:

    demand_scale = headroom / avg_interference_factor        (headroom default 0.7)

meaning "with a core to itself, a token takes ~70% of its SLA target".
For realtime traces (genuinely measured on hardware) the default is 1.0.
Override either with `demand_scale=`.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from task_model import Task, TaskKind

DEFAULT_TTFT_BUDGETS_MS = {"interactive": 300.0, "standard": 800.0, "batch": 3000.0}


@dataclass
class LoadOptions:
    headroom: float = 0.7
    demand_scale: Optional[float] = None
    ttft_budgets_ms: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TTFT_BUDGETS_MS))
    ttft_budget_override_ms: Optional[float] = None
    max_requests: Optional[int] = None
    replicate: int = 1                 # clone the loaded requests N times (multi-tenant load)
    replicate_gap_ms: float = 500.0    # arrival offset between replicas


@dataclass
class LoadedTrace:
    tasks: List[Task]
    source_path: str
    mode: str                          # "simulation" | "realtime"
    experiment_config: Dict[str, Any]
    demand_scale: float
    notes: List[str] = field(default_factory=list)

    @property
    def horizon_ms(self) -> float:
        """Rough end of the workload (last arrival + its isolated service time)."""
        return max((t.arrival_ms + t.total_service_ms for t in self.tasks), default=0.0)


# ------------------------------------------------------------------ helpers
def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_realtime_record(rec: Dict[str, Any]) -> bool:
    return "first_output_latency_ms" in rec or "chunk_records" in rec


def _parse_iso_ms(text: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(text).timestamp() * 1000.0
    except (TypeError, ValueError):
        return None


def _read_records(path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, list):
            return {}, payload
        return payload.get("experiment_config", {}) or {}, payload.get("requests", [])
    if ext == ".csv":
        with open(path, "r", newline="", encoding="utf-8") as fh:
            return {}, list(csv.DictReader(fh))
    raise ValueError(f"Unsupported trace format '{ext}' (expected .json or .csv)")


# ------------------------------------------------------------------ record -> task
def _build_simulation_task(rec: Dict[str, Any], scale: float, opts: LoadOptions,
                           notes: List[str]) -> Task:
    rid = str(rec["request_id"])
    req_type = str(rec.get("request_type", "standard"))
    deadline = _f(rec.get("deadline_target_itl_ms"), 50.0)
    prefill = _f(rec.get("ttft_ms"), 50.0)

    token_records = rec.get("token_records")
    if token_records:
        itls = [float(t["itl_ms"]) for t in token_records]
    else:
        n = int(_f(rec.get("tokens_generated"), _f(rec.get("requested_output_tokens"), 0)) or 0)
        avg = _f(rec.get("observed_avg_itl_ms"), deadline)
        itls = [avg] * n
        notes.append(f"{rid}: no per-token records; synthesised {n} tokens at avg ITL {avg:.1f} ms")

    budget = opts.ttft_budget_override_ms or opts.ttft_budgets_ms.get(req_type, 800.0)
    return Task(
        rid, TaskKind.INFERENCE, (_f(rec.get("arrival_time_s"), 0.0) or 0.0) * 1000.0,
        [prefill * scale] + [i * scale for i in itls],
        itl_deadline_ms=deadline, ttft_budget_ms=budget,
        prompt_tokens=int(_f(rec.get("prompt_tokens"), 0) or 0),
        request_type=req_type, priority_label=str(rec.get("priority_metadata", "normal")),
        category=str(rec.get("category", "")), source="simulation",
        meta={"trace_ttft_ms": prefill, "trace_avg_itl_ms": _f(rec.get("observed_avg_itl_ms"))},
    )


def _build_realtime_task(rec: Dict[str, Any], t0_ms: float, scale: float, opts: LoadOptions,
                         notes: List[str]) -> Task:
    rid = str(rec["request_id"])
    deadline = _f(rec.get("deadline_ms"), 100.0)

    chunks = rec.get("chunk_records")
    if chunks:
        lat = [float(c["inter_chunk_latency_ms"]) for c in chunks]
        prefill = _f(rec.get("first_output_latency_ms"), lat[0])
        itls = lat[1:]                      # chunk 1 *is* the time-to-first-output
    else:
        n_chunks = int(_f(rec.get("output_chunks"), 1) or 1)
        prefill = _f(rec.get("first_output_latency_ms"), 100.0)
        avg = _f(rec.get("average_inter_chunk_latency_ms"), deadline)
        itls = [avg] * max(0, n_chunks - 1)
        notes.append(f"{rid}: no chunk records; synthesised {len(itls)} chunks at avg {avg:.1f} ms")

    ts = _f(rec.get("request_start_timestamp_s"))
    if ts is not None:
        start_ms = ts * 1000.0
    else:
        start_ms = _parse_iso_ms(rec.get("request_start_time", "")) or 0.0

    budget = opts.ttft_budget_override_ms or opts.ttft_budgets_ms["standard"]
    return Task(
        rid, TaskKind.INFERENCE, start_ms - t0_ms,
        [prefill * scale] + [i * scale for i in itls],
        itl_deadline_ms=deadline, ttft_budget_ms=budget, prompt_tokens=0,
        request_type="standard", priority_label="normal", category="realtime",
        source="realtime", meta={"model": rec.get("model"), "trace_ttft_ms": prefill},
    )


def _replicate(tasks: List[Task], n: int, gap_ms: float) -> List[Task]:
    if n <= 1:
        return tasks
    out: List[Task] = []
    for k in range(n):
        for t in tasks:
            c = t.clone()
            if k > 0:
                c.task_id = f"{t.task_id}#{k + 1}"
                c.arrival_ms = t.arrival_ms + k * gap_ms
                c.reset_runtime()
            out.append(c)
    return out


# ------------------------------------------------------------------ public API
def load_trace(path: str, options: Optional[LoadOptions] = None) -> LoadedTrace:
    opts = options or LoadOptions()
    if not os.path.exists(path):
        raise FileNotFoundError(f"Trace not found: {path}")

    config, records = _read_records(path)
    if not records:
        raise ValueError(f"Trace {path} contains no requests")
    if opts.max_requests:
        records = records[: opts.max_requests]

    notes: List[str] = []
    realtime = _is_realtime_record(records[0])
    mode = "realtime" if realtime else "simulation"

    if opts.demand_scale is not None:
        scale = opts.demand_scale
    elif realtime:
        scale = 1.0
    else:
        factor = _f(config.get("avg_interference_factor"), None)
        if factor is None:
            factor = 1.0
            notes.append("avg_interference_factor not found (CSV trace?) - assuming 1.0")
        scale = opts.headroom / factor

    tasks: List[Task] = []
    if realtime:
        stamps = [_f(r.get("request_start_timestamp_s")) for r in records]
        if all(s is not None for s in stamps):
            t0 = min(stamps) * 1000.0
        else:
            iso = [_parse_iso_ms(r.get("request_start_time", "")) for r in records]
            t0 = min((i for i in iso if i is not None), default=0.0)
        for rec in records:
            tasks.append(_build_realtime_task(rec, t0, scale, opts, notes))
    else:
        for rec in records:
            tasks.append(_build_simulation_task(rec, scale, opts, notes))

    ids = [t.task_id for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate request_id values in trace")

    tasks = _replicate(tasks, opts.replicate, opts.replicate_gap_ms)
    tasks.sort(key=lambda t: (t.arrival_ms, t.task_id))
    return LoadedTrace(tasks, path, mode, config, scale, notes)
