# LLM-Aware OS Scheduler for Latency-Sensitive Local AI Inference

**Project Repository:** [oviya-art/llm-aware-os-scheduler](https://github.com/oviya-art/llm-aware-os-scheduler)  
**Target Environment:** Windows 10/11 / Linux, Python 3.10+ (Tested on Python 3.13)

---

## 1. Project Overview & Motivation

Running Large Language Models (LLMs) locally on commodity client devices (laptops and desktops) presents unique scheduling challenges for standard operating systems:

- **Bimodal Execution Phases:**
  - **Prefill Phase (Prompt Evaluation):** Compute-bound and highly parallelized; determines **Time To First Token (TTFT)**.
  - **Decode Phase (Token Generation):** Memory-bandwidth- and latency-bound; emits tokens sequentially with strict **Inter-Token Latency (ITL)** requirements (typically 30–50 ms/token) to maintain smooth, interactive user experiences.
- **The Problem with Standard OS Schedulers:**
  - Traditional operating system schedulers (such as Linux CFS or standard Windows priority schedulers) are completely agnostic to token generation deadlines and phase transitions.
  - When competing background processes (such as builds, browsers, or indexing) saturate the CPU or memory bus, inter-token generation latency spikes drastically, causing severe deadline violations and perceived stuttering.
- **The Solution:**
  - An end-to-end, LLM-aware scheduling architecture that profiles token generation latencies in real time, models deadline risks, and schedules inference threads alongside competing background jobs using a novel **Token-Laxity Scheduler (TLS)**.

---

## 2. High-Level Subsystem Architecture

The project is structured into three collaborative modules:

```
                          +-------------------------------------------------+
                          |        Member 1: llm_workload_profiler          |
                          |  - Real-time Ollama streaming profiler          |
                          |  - Synthetic workload simulation engine         |
                          |  - Windows host telemetry (CPU, RAM, GPU)       |
                          |  - Causal EWMA deadline risk evaluation         |
                          +-----------------------+-------------------------+
                                                  |
                                                  | Standardized Traces
                                                  | (traces/*.json, *.csv)
                                                  v
                          +-------------------------------------------------+
                          |        Member 2: llm_scheduler_engine          |
                          |  - Discrete-time multi-core CPU simulator       |
                          |  - Baselines: FCFS, RR, Priority, CFS, EDF      |
                          |  - Novel Policy: Token-Laxity Scheduler (TLS)   |
                          |  - Optional Real-OS Actuator (psutil)           |
                          +-----------------------+-------------------------+
                                                  |
                                                  | Decision Logs & Metrics
                                                  | (decision_logs/*.json, *.csv)
                                                  v
                          +-------------------------------------------------+
                          |        Member 3: Dashboard & Evaluation         |
                          |  - Comparative charts & SLA attainment visualizer|
                          |  - Latency distribution & starvation analysis    |
                          +-------------------------------------------------+
```

---

## 3. Subsystem 1: `llm_workload_profiler`

### Role & Purpose
The **LLM Workload Profiler** is responsible for generating realistic inference workloads, capturing live or simulated latency metrics, assessing deadline risks, and exporting standardized trace files.

### Key Capabilities
1. **Real-Time Ollama Profiling:**
   - Connects directly to local Ollama instances (`http://localhost:11434/api/generate`) via HTTP streaming.
   - Measures exact wall-clock Time To First Output (TTFO) and per-token Inter-Chunk Latency (ICL).
   - Monitors Windows host telemetry (host CPU %, RAM %, Ollama process PID, GPU utilization).
2. **Synthetic Workload Simulation:**
   - Generates reproducible, parameterizable synthetic prompt/decode sequences across request categories (`short`, `medium`, `long`).
   - Models CPU and memory contention across three background scenarios: `light_load`, `mixed_load`, and `heavy_load`.
3. **Causal EWMA Deadline Risk Model:**
   - Evaluates remaining slack against target ITL deadlines using strictly causal Exponential Weighted Moving Average (EWMA) smoothing.
   - Categorizes risk into `low`, `medium`, and `high` flags before deadlines are breached.
4. **Standardized Trace Export:**
   - Outputs comprehensive trace records containing request IDs, prompt tokens, generated tokens, arrival times, TTFT, per-token ITL, and system contention metrics to `traces/`.

### Directory Layout
```
llm_workload_profiler/
├── main.py                 # CLI entry point (--mode realtime | simulation)
├── realtime_profiler.py    # Live Ollama streaming client & latency profiler
├── windows_monitor.py      # Host CPU, memory, process & GPU telemetry
├── workload_generator.py   # Synthetic request generator
├── latency_profiler.py     # Simulation latency engine with pluggable backends
├── deadline_model.py       # Causal EWMA deadline risk evaluation
├── mixed_workload.py       # Background contention scenarios (light, mixed, heavy)
├── trace_exporter.py       # Standardized JSON / CSV trace serializer
├── test_profiler.py        # 15 automated unit tests (with mocked Ollama)
├── test_ollama_live.py     # Manual live integration test against Ollama
├── requirements.txt        # Dependencies (psutil>=5.9.0)
├── README.md               # Detailed profiler documentation
└── traces/                 # Exported simulation & real-time trace files
```

### Quick Start Commands
```powershell
cd "llm_workload_profiler"

# Run automated unit tests
python -m unittest test_profiler.py

# Run synthetic workload simulation (produces traces/simulation_trace.json)
python main.py --mode simulation --requests 8 --scenario mixed_load

# Run real-time profiling against a local Ollama instance
python main.py --mode realtime --model llama3.2 --target-latency 45.0 --prompt "Explain OS scheduling for AI."
```

---

## 4. Subsystem 2: `llm_scheduler_engine`

### Role & Purpose
The **LLM Scheduler Engine** models multi-core CPU scheduling under contention. It consumes workload traces produced by Member 1, simulates execution on $N$ cores alongside CPU-bound background jobs, applies standard and novel scheduling policies, and exports fine-grained decision logs.

### Key Capabilities
1. **Discrete-Time Multi-Core Simulator (`simulator.py`):**
   - Accurately models CPU time, configurable time quantum, tick resolution, context-switch overheads, and cross-core migration penalties.
2. **Standard Baseline Schedulers (`baselines.py`):**
   - **FCFS:** Non-preemptive First-Come First-Served.
   - **Round Robin (RR):** Preemptive time-slicing across all active tasks.
   - **Static Priority:** Preemptive multi-level priority (interactive > standard > batch > background).
   - **CFS-like:** Approximates Linux Completely Fair Scheduler using virtual runtime ($vruntime$) and nice weights.
   - **EDF:** Earliest Deadline First based on next-token deadlines (ablation baseline).
3. **The Novel Policy: Token-Laxity Scheduler (`token_laxity_scheduler.py`):**
   - **Next-Token Deadline:** $d = t_{\text{last\_token}} + D$ (or arrival + TTFT budget for prefill).
   - **Causal Service Estimation:** Strictly causal EWMA estimate of token execution time $c$ and execution jitter $\sigma$.
   - **Normalized Token Laxity:** $\lambda = \frac{(d - \text{now}) - c_{\text{rem}} - \kappa\sigma}{D}$, normalizing laxity across varying SLA deadlines.
   - **Capacity Planner:** Tracks stream utilization $u = \frac{c + \kappa\sigma}{D}$. Admits streams into a protected high-priority set while total utilization $\le \rho \times \text{cores}$ ($\rho = 0.80$), preventing overload collapse.
   - **Dual-Threshold Preemption Controller:** Soft preemption at token boundaries for urgent tasks ($\lambda < \lambda_{\text{soft}}$); mid-token preemption only when critically urgent ($\lambda < \lambda_{\text{crit}}$).
   - **Starvation Guard:** Aging pseudo-deadlines ensure background jobs are never starved indefinitely.
4. **Comprehensive Metrics Engine (`metrics.py`):**
   - Computes Deadline Miss %, ITL Violation %, TTFT Miss %, SLA Attainment % (on-time TTFT and $\le 5\%$ late tokens), 95th/99th percentile ITL, makespan, background slowdown, and Jain's fairness index.
5. **Experimental Real-OS Actuator (`os_actuator.py`):**
   - Maps scheduler urgency decisions to real OS process priority classes on Windows (via `psutil`) or nice levels on Linux.

### Directory Layout
```
llm_scheduler_engine/
├── main.py                   # CLI entry point to simulate and compare schedulers
├── token_laxity_scheduler.py # Novel Token-Laxity Scheduler (TLS) implementation
├── baselines.py              # Baseline algorithms: FCFS, RR, Priority, CFS, EDF
├── simulator.py              # Discrete-time multi-core CPU simulator
├── task_model.py             # Task, core, and burst data models
├── trace_loader.py           # Loads Member 1 traces & converts to CPU demand bursts
├── background_load.py        # Deterministic CPU-bound background job generator
├── metrics.py                # SLA, ITL, TTFT, slowdown, and fairness calculations
├── decision_log.py           # Detailed JSON/CSV decision logger
├── evaluate.py               # Batch multi-scenario evaluation script
├── os_actuator.py            # Optional real-OS process priority governor
├── test_scheduler.py         # 36 unit tests covering all policies and components
├── requirements.txt          # Dependencies (psutil>=5.9)
├── README.md                 # Detailed scheduler engine documentation
└── decision_logs/            # Exported decision logs and comparative benchmarks
```

### Quick Start Commands
```powershell
cd "llm_scheduler_engine"

# Run all 36 unit tests
python -m unittest test_scheduler.py

# Run simulation comparing all 6 schedulers on the default trace
python main.py

# Run simulation with 4 cores and heavy background load
python main.py --cores 4 --background heavy_load --schedulers all

# Run only the Token-Laxity Scheduler with custom parameter overrides
python main.py --schedulers token_laxity --tls-set util_bound=0.85 --no-export
```

---

## 5. End-to-End Workflow: Connecting Profiler to Engine

The two subsystems work in tandem through standard data contracts:

```powershell
# Step 1: Generate or record a trace in llm_workload_profiler
cd "c:\Users\vello\OneDrive\Desktop\OS PROJECT\llm_workload_profiler"
python main.py --mode simulation --requests 20 --scenario mixed_load

# Step 2: Feed the generated trace into llm_scheduler_engine
cd "c:\Users\vello\OneDrive\Desktop\OS PROJECT\llm_scheduler_engine"
python main.py --trace ..\llm_workload_profiler\traces\simulation_trace.json --cores 4 --background mixed_load

# Step 3: Inspect results in llm_scheduler_engine/decision_logs/
# Files generated: run_comparison.csv, run_<scheduler>_decisions.csv, run_<scheduler>_tokens.csv
```

---

## 6. Summary Comparison of Subsystems

| Feature / Responsibility | `llm_workload_profiler` (Member 1) | `llm_scheduler_engine` (Member 2) |
|---|---|---|
| **Primary Goal** | Workload generation & latency measurement | Scheduling policies & multi-core simulation |
| **Inference Source** | Local Ollama daemon or synthetic model | Workload traces (`.json` / `.csv`) |
| **Contention Model** | Injected synthetic or host background contention | Generated CPU competitors in simulated cores |
| **Key Metrics** | TTFO, Inter-Chunk Latency (ICL), EWMA slack | Deadline Miss %, SLA Attained %, p99 ITL, Slowdown |
| **Scheduling Policies** | N/A (Measures latencies) | FCFS, RR, Priority, CFS, EDF, Token-Laxity (TLS) |
| **OS Interaction** | Reads host CPU/RAM/Ollama telemetry | Simulates cores; optional priority actuator (`psutil`) |
| **Primary Output** | `traces/simulation_trace.json`, `realtime_trace.json` | `decision_logs/*_decisions.csv`, `*_comparison.csv` |
| **Test Suite** | 15 unit tests (`test_profiler.py`) | 36 unit tests (`test_scheduler.py`) |
