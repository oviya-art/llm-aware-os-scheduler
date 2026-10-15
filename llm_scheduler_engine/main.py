"""
CLI for the scheduling engine (Member 2).

Takes a Member 1 workload trace, adds background CPU-bound load, simulates one or
more schedulers on N cores, prints a comparison table and exports decision logs.

Examples (run from this folder):

  # compare every scheduler on Member 1's simulation trace
  python main.py --trace ../llm_workload_profiler/traces/simulation_trace.json

  # only the novel scheduler, 4 cores, heavy background load
  python main.py --schedulers token_laxity --cores 4 --background heavy_load

  # use a real Ollama trace, replicated 6x to emulate several concurrent users
  python main.py --trace ../llm_workload_profiler/traces/realtime_trace.json --replicate 6

  # override token-laxity parameters
  python main.py --tls-set util_bound=0.7 --tls-set lambda_soft=0.5
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List

from background_load import SCENARIOS, generate_background, load_background_json
from baselines import (CFSLikeScheduler, EDFScheduler, FCFSScheduler, PriorityScheduler,
                       RoundRobinScheduler)
from decision_log import export_comparison, export_run
from metrics import compute_metrics
from scheduler_base import Scheduler
from simulator import SimConfig, Simulator
from token_laxity_scheduler import TLSConfig, TokenLaxityScheduler
from trace_loader import LoadOptions, load_trace

ALL_SCHEDULERS = ["fcfs", "rr", "priority", "cfs", "edf", "token_laxity"]
DEFAULT_TRACES = [
    "../llm_workload_profiler/traces/simulation_trace.json",
    "../llm_workload_profiler/traces/realtime_trace.json",
]


def parse_tls_overrides(items: List[str]) -> Dict[str, float]:
    valid = TLSConfig.__dataclass_fields__
    out: Dict[str, float] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--tls-set expects key=value, got '{item}'")
        k, v = item.split("=", 1)
        if k not in valid:
            raise SystemExit(f"Unknown TLS parameter '{k}'. Valid: {', '.join(valid)}")
        if k == "class_bonus":
            raise SystemExit("class_bonus cannot be set from the CLI")
        out[k] = float(v)
    return out


def build_scheduler(name: str, args, tls_overrides: Dict[str, float]) -> Scheduler:
    if name == "fcfs":
        return FCFSScheduler()
    if name == "rr":
        return RoundRobinScheduler(args.quantum_ms)
    if name == "priority":
        return PriorityScheduler(args.quantum_ms)
    if name == "cfs":
        return CFSLikeScheduler()
    if name == "edf":
        return EDFScheduler()
    if name == "token_laxity":
        return TokenLaxityScheduler(**tls_overrides)
    raise SystemExit(f"Unknown scheduler '{name}'. Choose from {ALL_SCHEDULERS}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="LLM-aware scheduling engine (Member 2)",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__)
    g = p.add_argument_group("workload")
    g.add_argument("--trace", help="Member 1 trace (.json or .csv). Default: auto-detect in ../llm_workload_profiler/traces")
    g.add_argument("--max-requests", type=int, help="use only the first N requests of the trace")
    g.add_argument("--replicate", type=int, default=1, help="clone the trace N times (staggered) to emulate more users")
    g.add_argument("--replicate-gap-ms", type=float, default=500.0)
    g.add_argument("--headroom", type=float, default=0.7,
                   help="uncontended token service as a fraction of the SLA target (simulation traces; default 0.7)")
    g.add_argument("--demand-scale", type=float, help="explicit multiplier on traced latencies (overrides --headroom)")
    g.add_argument("--ttft-budget-ms", type=float, help="override first-token budget for every request")
    b = p.add_argument_group("background CPU-bound load")
    b.add_argument("--background", default="mixed_load", choices=list(SCENARIOS),
                   help="competing CPU-bound jobs (default mixed_load)")
    b.add_argument("--bg-fraction", type=float, help="override the scenario's average load (fraction of all cores)")
    b.add_argument("--bg-trace", help="custom background jobs JSON (overrides --background)")
    b.add_argument("--seed", type=int, default=42, help="seed for background generation")
    s = p.add_argument_group("system / schedulers")
    s.add_argument("--schedulers", default="all",
                   help=f"comma list from {ALL_SCHEDULERS} or 'all'")
    s.add_argument("--cores", type=int, default=4)
    s.add_argument("--quantum-ms", type=float, default=20.0, help="RR / priority time slice")
    s.add_argument("--tick-ms", type=float, default=1.0)
    s.add_argument("--decision-interval-ms", type=float, default=4.0, help="scheduler timer tick (also runs on events)")
    s.add_argument("--context-switch-ms", type=float, default=0.1)
    s.add_argument("--migration-ms", type=float, default=0.3)
    s.add_argument("--tls-set", action="append", metavar="KEY=VALUE", help="override a TokenLaxity parameter")
    o = p.add_argument_group("output")
    o.add_argument("--output-dir", default="decision_logs")
    o.add_argument("--prefix", default="run")
    o.add_argument("--no-export", action="store_true", help="print only, write no files")
    return p


def find_trace(arg: str) -> str:
    if arg:
        return arg
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in DEFAULT_TRACES:
        path = os.path.normpath(os.path.join(here, rel))
        if os.path.exists(path):
            return path
    raise SystemExit("No trace found. Run Member 1 first (python main.py --mode simulation in "
                     "llm_workload_profiler) or pass --trace <file>.")


def print_table(rows: List[dict]) -> None:
    cols = [("scheduler", "scheduler", 13, "s"), ("deadline_miss_rate_pct", "DLmiss%", 8, ".2f"),
            ("itl_violation_rate_pct", "ITLviol%", 8, ".2f"), ("ttft_miss_rate_pct", "TTFTmiss%", 9, ".2f"),
            ("sla_attained_pct", "SLA-ok%", 8, ".1f"), ("p99_itl_ms", "p99ITL", 8, ".1f"),
            ("mean_ttft_ms", "meanTTFT", 9, ".0f"), ("bg_mean_slowdown", "bgSlow", 7, ".2f"),
            ("cpu_utilization_pct", "util%", 7, ".1f"), ("preemptions", "preempt", 8, "d")]
    print(" ".join(f"{h:>{w}}" if k != "scheduler" else f"{h:<{w}}" for k, h, w, _ in cols))
    print("-" * (sum(w for _, _, w, _ in cols) + len(cols) - 1))
    for r in rows:
        print(" ".join((f"{r[k]:<{w}}" if k == "scheduler" else f"{r[k]:>{w}{f}}") for k, _, w, f in cols))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    names = ALL_SCHEDULERS if args.schedulers == "all" else [s.strip() for s in args.schedulers.split(",")]
    tls_overrides = parse_tls_overrides(args.tls_set)

    path = find_trace(args.trace)
    opts = LoadOptions(headroom=args.headroom, demand_scale=args.demand_scale,
                       ttft_budget_override_ms=args.ttft_budget_ms, max_requests=args.max_requests,
                       replicate=args.replicate, replicate_gap_ms=args.replicate_gap_ms)
    loaded = load_trace(path, opts)

    if args.bg_trace:
        bg = load_background_json(args.bg_trace)
        bg_label = f"custom:{args.bg_trace}"
    else:
        bg = generate_background(args.background, args.cores, loaded.horizon_ms, args.seed, args.bg_fraction)
        bg_label = args.background
    tasks = loaded.tasks + bg

    cfg = SimConfig(num_cores=args.cores, tick_ms=args.tick_ms, decision_interval_ms=args.decision_interval_ms,
                    context_switch_ms=args.context_switch_ms, migration_ms=args.migration_ms)

    print("=" * 78)
    print("  LLM-AWARE SCHEDULING ENGINE (Member 2)")
    print("=" * 78)
    print(f"Trace:        {path}  [{loaded.mode}]")
    print(f"Requests:     {len([t for t in tasks if t.is_inference])} inference, {len(bg)} background ({bg_label})")
    print(f"Machine:      {args.cores} cores, tick {args.tick_ms} ms, decision interval {args.decision_interval_ms} ms")
    print(f"Demand scale: {loaded.demand_scale:.3f}  (traced latency -> CPU service demand)")
    for n in loaded.notes[:5]:
        print(f"  note: {n}")
    print()

    run_config = {"trace": path, "trace_mode": loaded.mode, "demand_scale": loaded.demand_scale,
                  "background": bg_label, "bg_tasks": len(bg), "seed": args.seed,
                  "tls_overrides": tls_overrides, "quantum_ms": args.quantum_ms}
    all_metrics = []
    for name in names:
        res = Simulator(tasks, build_scheduler(name, args, tls_overrides), cfg).run()
        m = compute_metrics(res)
        all_metrics.append(m)
        if not args.no_export:
            export_run(res, args.output_dir, args.prefix, path, run_config)
        if res.truncated:
            print(f"WARNING: {name} hit the simulation time limit", file=sys.stderr)

    print_table(all_metrics)
    print("\nDLmiss% = % of ALL deadlines missed (first token + every decode token).")
    print("SLA-ok% = requests with first token on time AND <=5% of tokens late.")
    if not args.no_export:
        paths = export_comparison(all_metrics, args.output_dir, args.prefix, run_config)
        print(f"\nDecision logs written to: {os.path.abspath(args.output_dir)}")
        print(f"Comparison summary:       {paths['csv']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
