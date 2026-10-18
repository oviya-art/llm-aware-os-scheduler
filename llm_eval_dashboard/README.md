# Member 3: Evaluation, benchmarking and dashboard

Wires Member 1's workload generator into Member 2's scheduling engine and five baselines, runs controlled
experiments, scores them, tests the differences statistically and shows everything in a single-page web app (five screens, no build step).

```
llm_eval_dashboard/
  benchmark/common.py        integration layer: scenarios, Member 1 -> trace -> Member 2 -> simulate
  benchmark/metrics_ext.py   deadline-miss (token + request), avg latency, throughput, Jain fairness, cost
  benchmark/stats_tests.py   paired Wilcoxon, bootstrap CI, Holm, Friedman, Cohen's d
  benchmark/run_benchmark.py experiment driver (parallel), ablation, sensitivity sweep, replay export
  benchmark/replay.py        decision log -> per-core timeline + token series for the live arena
  benchmark/make_report.py   figures (PNG) + results/REPORT.md from real numbers
  server.py                  serves the dashboard and runs custom workloads (/api/run)
  dashboard/                 SPA: index.html + css/ js/ data/ (vanilla JS, hash routes)
  results/                   raw_runs.csv/json, summary.json, comparisons.csv, REPORT.md, figures/
  traces/                    every Member 1 trace the experiments used
  test_benchmark.py          10 tests
```

## Run

```
pip install -r requirements.txt
python -m benchmark.run_benchmark --seeds 12       # ~3 min on 2 cores; --quick skips ablation
python -m benchmark.make_report                    # figures + REPORT.md
python server.py                                   # http://localhost:8000
```
The five prepared scenarios also work by just opening `dashboard/index.html`; only "New workload" (custom runs)
and replaying seeds other than 1 need `server.py`.

## Screens
| Screen | What it does |
|---|---|
| Dashboard | hero with a live TLS vs CFS preview, scorecard, leaderboard, SLA heat matrix; scenario chips |
| Live arena | two schedulers replayed side by side: core timeline, per-token latency, request lanes, decision feed; new workloads |
| Analytics | tabs: Latency, Fairness, Significance (Holm-corrected matrix), Design (ablation, sweep) |
| Experiments | searchable, filterable, sortable table of all 360 runs, detail modal, CSV export, "My runs" history |
| Settings | theme, motion, arena defaults, server status, reset saved data |

Shortcuts: `Ctrl/Cmd+K` quick jump, `Space` play/pause and arrow keys in the arena.

## Notes
* Member 1 and Member 2 both ship a `main.py`; the harness never imports either, so the folders share `sys.path` safely.
* Metrics beyond Member 2's: request-level SLA violation (token-level rate rewards queueing), throughput over the
  inference window, Jain on normalised progress (1/slowdown) and on per-request QoS.
* All results are from simulation; see Threats to validity in `results/REPORT.md`.
