"""
Member 3 experiment driver.

    python -m benchmark.run_benchmark --seeds 12            # full study (~10-15 min on 2 cores)
    python -m benchmark.run_benchmark --seeds 3 --quick     # smoke test

For every (scenario, seed): Member 1 builds a trace, Member 2's engine replays it under
all six schedulers on identical task sets, Member 3 scores the runs and runs paired
statistics. Outputs land in results/ (JSON + CSV) and dashboard/data/ (JS for the UI).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmark.common import (BASELINES, NOVEL, ROOT, SCENARIOS, SCHEDULER_LABELS, SCHEDULERS,   # noqa: E402
                              TLS_VARIANTS, build_workload, simulate)
from benchmark.metrics_ext import METRIC_META, evaluate                                      # noqa: E402
from benchmark.replay import build_replay                                                    # noqa: E402
from benchmark.stats_tests import friedman, holm, mean_ci, paired_compare                    # noqa: E402

RESULTS = os.path.join(ROOT, "results")
TRACES = os.path.join(ROOT, "traces")
DATA = os.path.join(ROOT, "dashboard", "data")
SWEEP_UTIL = [0.4, 0.6, 0.8, 1.0, 1.3]


def _job(args) -> List[Dict[str, Any]]:
    kind, key, seed, names = args
    sc = SCENARIOS[key]
    w = build_workload(sc, seed, TRACES)
    rows = []
    for n in names:
        if kind == "sweep":
            name, ov = "token_laxity", {"util_bound": n}
            label = f"util_bound={n}"
        else:
            name, ov, label = n, {}, n
        t0 = time.time()
        res = simulate(w.tasks, name if n not in TLS_VARIANTS else n, sc.cores, **ov)
        m = evaluate(res)
        rows.append({"kind": kind, "scenario": key, "seed": seed, "scheduler": label,
                     "offered_load": round(w.offered_load, 3), "sim_seconds": round(time.time() - t0, 2), **m})
    return rows


def run_all(seeds: int, scenarios: List[str], ablation_seeds: int, workers: int) -> List[Dict[str, Any]]:
    jobs = []
    for key in scenarios:
        for s in range(1, seeds + 1):
            jobs.append(("main", key, s, SCHEDULERS))
    for key in [k for k in ("mixed", "heavy", "stress") if k in scenarios]:
        for s in range(1, ablation_seeds + 1):
            jobs.append(("ablation", key, s, [v for v in TLS_VARIANTS if v != "tls_full"]))
    if "heavy" in scenarios:
        for s in range(1, ablation_seeds + 1):
            jobs.append(("sweep", "heavy", s, SWEEP_UTIL))
    rows: List[Dict[str, Any]] = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_job, j): j for j in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            rows += f.result()
            j = futs[f]
            print(f"  [{i:3d}/{len(jobs)}] {j[0]:8s} {j[1]:7s} seed {j[2]:2d}   {time.time() - t0:6.0f}s", flush=True)
    return rows


# ------------------------------------------------------------------------------ analysis
def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    main = [r for r in rows if r["kind"] == "main"]
    scen_keys = [k for k in SCENARIOS if any(r["scenario"] == k for r in main)]
    metrics = list(METRIC_META)
    by: Dict[Any, Dict[int, Dict[str, Any]]] = {}
    for r in main:
        by.setdefault((r["scenario"], r["scheduler"]), {})[r["seed"]] = r

    summary: Dict[str, Any] = {}
    for sk in scen_keys:
        summary[sk] = {}
        for s in SCHEDULERS:
            runs = by.get((sk, s), {})
            summary[sk][s] = {m: mean_ci([r[m] for r in runs.values()]) for m in metrics}

    comparisons: List[Dict[str, Any]] = []
    for sk in scen_keys + ["ALL"]:
        for m in metrics:
            hb = METRIC_META[m]["higher_better"]
            block = []
            for b in BASELINES:
                if sk == "ALL":
                    keys = [(k, sd) for k in scen_keys for sd in sorted(by.get((k, NOVEL), {}))
                            if sd in by.get((k, b), {})]
                    a = [by[(k, NOVEL)][sd][m] for k, sd in keys]
                    c = [by[(k, b)][sd][m] for k, sd in keys]
                else:
                    seeds = sorted(set(by.get((sk, NOVEL), {})) & set(by.get((sk, b), {})))
                    a = [by[(sk, NOVEL)][sd][m] for sd in seeds]
                    c = [by[(sk, b)][sd][m] for sd in seeds]
                if not a:
                    continue
                cmp_ = paired_compare(a, c, hb)
                cmp_.update({"scenario": sk, "metric": m, "baseline": b})
                block.append(cmp_)
            adj = holm([c["p_wilcoxon"] for c in block])
            for c, p in zip(block, adj):
                c["p_holm"] = p
                c["significant"] = bool(p < 0.05)
            comparisons += block

    fried = {}
    for sk in scen_keys:
        fried[sk] = {}
        for m in ("token_miss_rate", "request_miss_rate", "p99_itl_ms", "jain_progress"):
            seeds = sorted(set.intersection(*[set(by[(sk, s)]) for s in SCHEDULERS]))
            fried[sk][m] = friedman({s: [by[(sk, s)][sd][m] for sd in seeds] for s in SCHEDULERS})

    # win-rate matrix on request-level miss rate + token-level miss rate
    wins = {}
    for sk in scen_keys:
        seeds = sorted(set.intersection(*[set(by[(sk, s)]) for s in SCHEDULERS]))
        wins[sk] = {s: 0 for s in SCHEDULERS}
        for sd in seeds:
            best = min(by[(sk, s)][sd]["request_miss_rate"] * 1000 + by[(sk, s)][sd]["token_miss_rate"] for s in SCHEDULERS)
            for s in SCHEDULERS:
                if abs(by[(sk, s)][sd]["request_miss_rate"] * 1000 + by[(sk, s)][sd]["token_miss_rate"] - best) < 1e-9:
                    wins[sk][s] += 1

    # ablation + sensitivity
    abl: Dict[str, Any] = {}
    for sk in ("mixed", "heavy", "stress"):
        full = by.get((sk, NOVEL), {})
        variants = {v: {r["seed"]: r for r in rows if r["kind"] == "ablation" and r["scenario"] == sk and r["scheduler"] == v}
                    for v in TLS_VARIANTS if v != "tls_full"}
        if not variants or not next(iter(variants.values())):
            continue
        abl[sk] = {"tls_full": {m: mean_ci([r[m] for sd, r in full.items() if sd in next(iter(variants.values()))])
                                for m in metrics}}
        for v, rr in variants.items():
            abl[sk][v] = {m: mean_ci([r[m] for r in rr.values()]) for m in metrics}
    sweep: Dict[str, Any] = {}
    for u in SWEEP_UTIL:
        rr = [r for r in rows if r["kind"] == "sweep" and r["scheduler"] == f"util_bound={u}"]
        if rr:
            sweep[str(u)] = {m: mean_ci([r[m] for r in rr]) for m in metrics}

    scen_meta = {}
    for sk in scen_keys:
        sc = SCENARIOS[sk]
        loads = [r["offered_load"] for r in main if r["scenario"] == sk and r["scheduler"] == NOVEL]
        scen_meta[sk] = {"title": sc.title, "blurb": sc.blurb, "cores": sc.cores, "requests": sc.requests,
                         "arrival_rps": sc.arrival_rps, "background": sc.background,
                         "bg_fraction": sc.bg_fraction, "m1_scenario": sc.m1_scenario,
                         "offered_load": mean_ci(loads)["mean"], "seeds": len(loads)}
    return {"scenarios": scen_meta, "schedulers": SCHEDULERS, "labels": SCHEDULER_LABELS, "metric_meta": METRIC_META,
            "summary": summary, "comparisons": comparisons, "friedman": fried, "wins": wins,
            "ablation": abl, "sensitivity": sweep}


def write_outputs(rows, agg, replays):
    os.makedirs(RESULTS, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(RESULTS, "raw_runs.json"), "w") as fh:
        json.dump(rows, fh)
    cols = ["kind", "scenario", "seed", "scheduler"] + [k for k in rows[0] if k not in ("kind", "scenario", "seed", "scheduler")]
    with open(os.path.join(RESULTS, "raw_runs.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(RESULTS, "summary.json"), "w") as fh:
        json.dump(agg, fh, indent=1)
    with open(os.path.join(RESULTS, "comparisons.csv"), "w", newline="") as fh:
        keys = ["scenario", "metric", "baseline", "n", "mean_a", "mean_b", "mean_diff", "rel_improvement_pct",
                "wins", "ties", "losses", "cohens_dz", "p_wilcoxon", "p_holm", "significant"]
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(agg["comparisons"])
    with open(os.path.join(DATA, "bench.js"), "w") as fh:
        fh.write("window.BENCH = " + json.dumps(agg, separators=(",", ":"), allow_nan=False, default=_nan) + ";\n")
    with open(os.path.join(DATA, "runs.js"), "w") as fh:
        fh.write("window.RUNS = " + json.dumps(rows, separators=(",", ":"), default=_nan) + ";\n")
    for key, rp in replays.items():
        with open(os.path.join(DATA, f"replay_{key}.js"), "w") as fh:
            fh.write("window.REPLAY = window.REPLAY || {};\nwindow.REPLAY[%s] = " % json.dumps(key)
                     + json.dumps(rp, separators=(",", ":")) + ";\n")
        with open(os.path.join(RESULTS, f"replay_{key}.json"), "w") as fh:
            json.dump(rp, fh, separators=(",", ":"))


def _nan(o):
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    raise TypeError(type(o))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--ablation-seeds", type=int, default=6)
    ap.add_argument("--scenarios", default="all")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--showcase-seed", type=int, default=1)
    ap.add_argument("--quick", action="store_true", help="skip ablation/sweep")
    a = ap.parse_args(argv)
    scen = list(SCENARIOS) if a.scenarios == "all" else a.scenarios.split(",")
    abl = 0 if a.quick else a.ablation_seeds
    print(f"Running {len(scen)} scenarios x {a.seeds} seeds x {len(SCHEDULERS)} schedulers "
          f"(+ ablation/sweep on {abl} seeds), {a.workers} workers")
    rows = run_all(a.seeds, scen, abl, a.workers)
    agg = aggregate(rows)
    replays = {}
    for key in scen:
        sc = SCENARIOS[key]
        w = build_workload(sc, a.showcase_seed, TRACES, tag=f"showcase_{key}")
        replays[key] = build_replay(w, sc.cores)
        print(f"  replay {key}: " + ", ".join(f"{s}={len(r['segments'])}seg" for s, r in replays[key]['runs'].items()))
    write_outputs(rows, agg, replays)
    print("done ->", RESULTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
