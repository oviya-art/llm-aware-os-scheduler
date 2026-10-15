"""
Multi-trace evaluation: average every scheduler over many traces / machine sizes /
background loads, so results do not hinge on one lucky workload.

Example:
  # 1) make traces with Member 1 (in ../llm_workload_profiler), one per seed:
  #      python main.py --mode simulation --requests 20 --scenario mixed_load --arrival-rate 1.5 --seed 6
  #      copy traces/simulation_trace.json to my_traces/t6.json  (repeat for other seeds)
  # 2) evaluate:
  python evaluate.py --traces my_traces/*.json --cores 4,6 --backgrounds mixed_load,heavy_load

Prints a table of means and in how many scenarios each scheduler had the best
SLA-attained %. A full run (10 traces x 2 core counts x 2 loads x 6 schedulers) takes
a few minutes in pure Python.
"""

import argparse
import glob
import statistics as st
import sys
from typing import List

from background_load import generate_background
from main import ALL_SCHEDULERS, build_scheduler
from metrics import compute_metrics
from simulator import SimConfig, Simulator
from trace_loader import LoadOptions, load_trace

KEYS = [("deadline_miss_rate_pct", "DLmiss%"), ("ttft_miss_rate_pct", "TTFTmiss%"),
        ("sla_attained_pct", "SLA-ok%"), ("p99_itl_ms", "p99ITL"),
        ("mean_ttft_ms", "meanTTFT"), ("bg_mean_slowdown", "bgSlow")]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--traces", nargs="+", required=True, help="trace files (globs allowed)")
    p.add_argument("--cores", default="4,6")
    p.add_argument("--backgrounds", default="mixed_load,heavy_load")
    p.add_argument("--schedulers", default="all")
    p.add_argument("--quantum-ms", type=float, default=20.0)
    p.add_argument("--headroom", type=float, default=0.7)
    a = p.parse_args(argv)

    files: List[str] = sorted(f for pat in a.traces for f in glob.glob(pat))
    if not files:
        print("No trace files matched", file=sys.stderr)
        return 1
    names = ALL_SCHEDULERS if a.schedulers == "all" else a.schedulers.split(",")
    rows = []                                        # (scenario, scheduler, metrics)
    for fi, path in enumerate(files):
        lt = load_trace(path, LoadOptions(headroom=a.headroom))
        for cores in [int(c) for c in a.cores.split(",")]:
            for bg in a.backgrounds.split(","):
                tasks = lt.tasks + generate_background(bg, cores, lt.horizon_ms, seed=fi + 1)
                for n in names:
                    res = Simulator(tasks, build_scheduler(n, a, {}), SimConfig(num_cores=cores)).run()
                    rows.append(((path, cores, bg), n, compute_metrics(res)))
        print(f"  finished {path}", file=sys.stderr)

    print(f"\n{len({r[0] for r in rows})} scenarios ({len(files)} traces)")
    print(f"{'scheduler':13s}" + "".join(f"{h:>11s}" for _, h in KEYS))
    for n in names:
        ms = [m for _, s, m in rows if s == n]
        print(f"{n:13s}" + "".join(f"{st.mean(m[k] for m in ms):11.2f}" for k, _ in KEYS))
    scen = sorted({r[0] for r in rows})
    wins = {n: 0 for n in names}
    for sc in scen:
        best = max(m["sla_attained_pct"] for k, s, m in rows if k == sc)
        for k, s, m in rows:
            if k == sc and m["sla_attained_pct"] == best:
                wins[s] += 1
    print("\nScenarios where each scheduler reached the best SLA-attained % (ties count for all):")
    print("  " + ", ".join(f"{n}={w}" for n, w in wins.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
