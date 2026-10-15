# Scheduling Engine: Token-Laxity Scheduler (Member 2 of 3)

**Project:** LLM-Aware OS Scheduler for Latency-Sensitive Local AI Inference
**Environment:** Windows 10/11 or Linux, Python 3.10+ (developed/tested on Linux, Python 3.12; engine is pure standard library)

This module is the **scheduling policy**. It takes Member 1's workload trace as input, adds competing
CPU-bound background jobs, simulates scheduling on N cores under several policies, and produces a
**scheduling decision log** plus metrics (consumed by Member 3's dashboard).

| Member | Responsibility |
|---|---|
| 1 | Workload generation, Ollama profiling, trace export (`llm_workload_profiler/`) |
| **2 (this folder)** | **Scheduling algorithms + simulator + decision log** |
| 3 | Benchmark dashboard / comparison (reads `decision_logs/`) |

## 1. Folder layout

```
llm_scheduler_engine/
|-- task_model.py             Task / Core data model
|-- trace_loader.py           Member 1 trace (JSON/CSV) -> schedulable tasks
|-- background_load.py        CPU-bound competitor generator (light / mixed / heavy)
|-- scheduler_base.py         Scheduler interface + core placement helper
|-- baselines.py              FCFS, Round Robin, Priority, CFS-like, EDF
|-- token_laxity_scheduler.py THE NOVEL POLICY (Token-Laxity Scheduler, "TLS")
|-- simulator.py              Discrete-time multi-core simulator
|-- metrics.py                Deadline-miss, TTFT, p95/p99 ITL, SLA-attained, fairness ...
|-- decision_log.py           JSON/CSV decision-log + comparison exporter
|-- main.py                   CLI
|-- evaluate.py               Multi-trace evaluation (averages over many workloads)
|-- os_actuator.py            OPTIONAL: apply urgency to real processes (experimental)
|-- test_scheduler.py         36 unit tests
|-- decision_logs/            Sample output generated from Member 1's sample trace
|-- requirements.txt
```

Put this folder **next to** `llm_workload_profiler/` (the CLI looks for `../llm_workload_profiler/traces/`).

## 2. Quick start (PowerShell)

```powershell
cd "c:\Users\vello\OneDrive\Desktop\OS PROJECT\llm-aware-os-scheduler-main\llm_scheduler_engine"

# compare all 6 schedulers on Member 1's simulation trace (auto-detected)
python main.py

# explicit trace, 4 cores, heavy background load
python main.py --trace ..\llm_workload_profiler\traces\simulation_trace.json --cores 4 --background heavy_load

# real Ollama trace, replicated 6x (staggered 2 s) to emulate several concurrent users
python main.py --trace ..\llm_workload_profiler\traces\realtime_trace.json --replicate 6 --replicate-gap-ms 2000

# only the novel scheduler, override a parameter
python main.py --schedulers token_laxity --tls-set util_bound=0.7

# tests
python -m unittest test_scheduler.py -v
```

Main options: `--schedulers fcfs,rr,priority,cfs,edf,token_laxity|all`, `--cores N`,
`--background none|light_load|mixed_load|heavy_load`, `--bg-fraction`, `--bg-trace file.json`,
`--quantum-ms`, `--headroom`, `--demand-scale`, `--ttft-budget-ms`, `--output-dir`, `--no-export`.
Run `python main.py --help` for all.

## 3. How a trace becomes a scheduling problem

Member 1's trace is a *measured latency profile per request*, not a scheduling problem. The loader
turns each request into **CPU service demand**: `bursts = [prefill_ms, itl_1, ..., itl_N] * demand_scale`.
The simulator then re-computes how long each token takes when requests must compete for cores with each
other and with background jobs.

* **Deadlines:** token k must be emitted within `deadline_target_itl_ms` of token k-1 (same definition as
  Member 1: a violation is ITL > target). The first token has a TTFT budget (not in Member 1's trace; defaults
  interactive 300 ms / standard 800 ms / batch 3000 ms, override with `--ttft-budget-ms`).
* **`demand_scale` (important assumption):** Member 1's synthetic backend produces ITL ~= target x interference
  factor, i.e. the traced latency already contains *simulated* contention, and uncontended latency sits right at
  the target (so even a dedicated core would miss ~half of the tokens). The loader therefore recovers uncontended
  service as `headroom / avg_interference_factor` (default headroom 0.7: with a core to itself a token takes ~70%
  of its SLA target). For **realtime** traces (genuinely measured) the scale is 1.0. Override with `--demand-scale`.
* **Background jobs:** Member 1's trace only *names* a contention scenario, so this module generates the
  competing CPU-bound jobs itself (deterministic, seeded).
* Member 1's README field list differs slightly from the files it actually writes; the loader follows the **real
  files** (`token_records[].itl_ms`, `chunk_records[].inter_chunk_latency_ms`, `deadline_target_itl_ms` vs `deadline_ms`).
  CSV traces work too, but only have summary rows, so per-token demand is synthesised from the average (noted in output).

## 4. The Token-Laxity Scheduler (TLS)

Full derivation is in the docstring of `token_laxity_scheduler.py`. Summary:

1. **Next-token deadline** `d = t_last_token + D` (prefill: `arrival + TTFT budget`).
2. **Token-rate service estimate** (strictly causal): EWMA of the CPU time each past token actually needed,
   `c`, and of its jitter `sigma`. `1/c` is the stream's token-generation rate per unit of CPU time.
3. **Token laxity** `L = (d - now) - c_rem - kappa*sigma`; normalised `lam = L / D` so 30 ms and 100 ms SLAs compare fairly.
4. **Rate-based capacity planner:** a stream needs CPU share `u = (c + kappa*sigma)/D`. Streams join a *protected*
   set (ordered by class, then arrival) while `sum(u) <= rho * cores` (`rho = util_bound`, default 0.80). Others are
   best-effort (idle cores + an anti-starvation guard). This prevents the overload collapse of plain EDF/least-laxity.
5. **Urgency key** (lower = more urgent) on one scale for all tasks; background jobs get an aging pseudo-deadline so
   they are never starved forever.
6. **Preemption controller: when and by how much.** A waiting protected stream preempts only if it is urgent
   (`lam < lambda_soft`), beats the victim's key by a margin, and the victim has run its minimum time. Soft preemption
   happens at **token boundaries** (or against background jobs); **mid-token preemption** only when critical
   (`lam < lambda_crit`). The boost is self-limiting: after the token, laxity jumps back up and the stream yields.

Every DISPATCH in the log carries the laxity, normalised laxity, key, predicted service time and the reason code.

### Baselines
`fcfs` (non-preemptive), `rr` (quantum), `priority` (preemptive static priority from Member 1's `priority_metadata`,
RR within a level), `cfs` (simplified Linux CFS: weighted vruntime, nice weights, wakeup/min granularity),
`edf` (preemptive EDF on the next-token deadline: an *ablation* that knows deadlines but has no rate/laxity/capacity model).

## 5. Results (held-out)

Parameters (`util_bound=0.80`) were chosen on trace seeds 1-5, then evaluated **unchanged on seeds 6-10**.
20 scenarios = 5 Member-1 traces (20 requests, mixed_load, 1.5 req/s) x {4, 6 cores} x {mixed, heavy background}. Means:

| Scheduler | Deadline miss % | TTFT miss % | **SLA-attained %** | p99 ITL (ms) | Mean TTFT (ms) | Background slowdown |
|---|---|---|---|---|---|---|
| fcfs | 2.1 | 68.0 | 24.8 | 83 | 8905 | 8.9 |
| rr | 50.6 | 0.0 | 5.3 | 227 | 144 | 2.2 |
| priority | 29.8 | 6.8 | 39.3 | 240 | 1218 | 1.5 |
| cfs | 52.4 | 0.0 | 5.3 | 228 | 126 | 2.2 |
| edf | 17.2 | 25.8 | 35.3 | 119 | 424 | 11.2 |
| **token_laxity** | **3.2** | 5.5 | **68.0** | 349 | 344 | 10.9 |

* TLS had the best SLA-attained % in 18/20 scenarios (tied best in the other 2) and the lowest deadline-miss rate
  among RR/priority/CFS/EDF in 20/20.
* **FCFS's low "deadline miss" is an artifact:** requests wait in the queue *before* their first token (68% miss their
  first-token budget, mean TTFT ~9 s), which only shows up in TTFT. That is why the headline metric includes the
  first token and why SLA-attained % is reported.
* *SLA-attained* = first token on time AND <= 5% of tokens late.

### Honest limitations
* **Tail latency is worse than EDF** (p99 349 vs 119 ms). Streams outside the protected set are *parked* during
  overload and get one token per `starve_limit_ms` (1.5 s), so they show long stalls. Lowering `util_bound` protects more
  streams per-token but parks more of them; raising it does the opposite (`util_bound=1.0` gave p99 ~200 ms but a much
  higher miss rate). It is a policy trade-off, not a free win.
* **TLS makes background jobs pay** (slowdown ~11x, same as EDF); RR/CFS are far kinder to background work but destroy ITL.
* **At light load all deadline-aware policies tie** (see the sample trace: TLS = EDF = 3.06% miss). The advantage appears under contention.
* **The baselines were not tuned** (e.g. RR quantum fixed at 20 ms); only TLS was tuned (on the training seeds). A tuned
  RR/priority would look better.
* The real Ollama sample (single request, ITL 103 ms vs 100 ms target, TTFT 2.7 s) is infeasible by itself, so every
  scheduler fails its first-token budget there.
* **Simulation, not a kernel scheduler.** Results depend on the modelling assumptions above (service demand taken from
  traces, 1 ms physics tick, 4 ms scheduler tick plus event-triggered passes, context-switch 0.1 ms, migration 0.3 ms).
* ~3% random latency spikes in Member 1's synthetic backend impose a ~3.5% floor on ITL violations for every scheduler.

Reproduce: generate traces with Member 1 for several seeds and run `python evaluate.py --traces my_traces\*.json`.

## 6. Decision-log schema (for Member 3)

Written to `decision_logs/` as `<prefix>_<scheduler>_{decisions,requests,tokens}.csv`, a full
`..._decisions.json`, and `<prefix>_comparison.{csv,json}`.

**Decisions** (`*_decisions.csv`): `t_ms, event, core, task_id, task_kind, phase, reason, other_task_id, laxity_ms,
norm_laxity, key, pred_service_ms, detail`. `event` in {ARRIVE, DISPATCH, PREEMPT, COMPLETE}. TLS reason codes:
`fill_idle_core`, `fill_idle_core_critical`, `preempt_background`, `preempt_background_critical`, `preempt_besteffort`,
`preempt_token_boundary`, `preempt_critical_mid_token`, `bg_aging_rotation`, `bg_starvation_guard`, `anti_starvation_token`.
**Requests** (`*_requests.csv`): per-request scheduled outcome (TTFT, avg/p95/p99/max ITL, violations, slack, slowdown, `sla_attained`) alongside
Member 1's original `trace_ttft_ms` / `trace_avg_itl_ms`. **Tokens**: every emitted token with latency, deadline, slack, `missed`.
**Comparison**: headline metrics per scheduler (see `decision_log.COMPARISON_FIELDS`).

## 7. Optional: real OS priority control (`os_actuator.py`, experimental)

Bridges the decision logic to real processes: Windows priority classes (never REALTIME) or Linux nice values via `psutil`.
`dry_run=True` by default. `LiveGovernor` boosts the inference process (e.g. Ollama) and demotes background PIDs when observed
ITL slack gets small, with release hysteresis. **The Windows path was not tested on Windows**; Linux nice and dry-run paths are tested.
On Linux an unprivileged user cannot lower a nice value again. `SCHED_DEADLINE` / cgroups are not implemented.

## 8. Notes on the "novelty" claim

EDF and least-laxity-first are classical. What this design adds is (a) a laxity derived from the per-stream *token-generation rate*
with a causal EWMA estimator, (b) a rate-based capacity planner that protects a feasible subset of streams under overload, and
(c) a preemption controller whose depth scales with urgency and aligns with token boundaries. Whether that is patentable needs a
prior-art search by a patent professional (related serving-system work on LLM scheduling exists); this README makes no legal claim.
