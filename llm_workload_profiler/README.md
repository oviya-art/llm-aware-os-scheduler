# LLM Workload Simulation & Real-Time Latency Profiling Module

**Subsystem:** Member 1 of 3  
**Project:** LLM-Aware OS Scheduler for Latency-Sensitive Local AI Inference  
**Environment:** Windows 10/11 (PowerShell / Command Prompt), Python 3.10+ (Tested on Python 3.13)

---

## 1. Project Objective

Local LLM inference workloads exhibit two distinct phases: prompt prefill (compute-bound) and autoregressive token decoding (memory-bandwidth- and latency-bound). When background processes saturate the CPU or memory bus, inter-token generation latency spikes, violating user-facing service-level agreements (SLAs).

This module (**Member 1**) provides dual profiling modes:
1. **Simulation Mode:** Generates synthetic inference workloads and simulates prefill latency (TTFT) and decode latency (ITL) under configurable background CPU contention scenarios (`light_load`, `heavy_load`, `mixed_load`).
2. **Real-Time Mode:** Connects to a locally running **Ollama** instance via HTTP streaming (`http://localhost:11434`), records actual wall-clock Time To First Output (TTFO), measures Inter-Chunk Latency (ICL) across arriving tokens, captures Windows host telemetry (CPU, RAM, Ollama process, GPU), and evaluates deadline risks using our Exponential Weighted Moving Average (EWMA) model.

Both modes export standardized trace datasets that **Member 2's scheduling algorithms** (e.g., EDF, Least Slack First, preemption policies) consume directly.

> [!NOTE]
> **Architectural & Team Boundaries:**
> - **Member 1 (This Subsystem):** Workload simulation, real-time Ollama profiling, latency measurement, deadline evaluation, and trace generation.
> - **Member 2:** Scheduling algorithm design (FCFS, EDF, Least Slack First, dynamic priorities).
> - **Member 3:** Benchmarking dashboard and comparative evaluation.
>
> *Operating System Boundary Notice:* This module measures and reports latency and system telemetry. It does not modify Windows kernel scheduling policies or GPU drivers directly.

---

## 2. Directory Structure

```
llm_workload_profiler/
│── realtime_profiler.py    # Real-time Ollama streaming profiler & latency measurements
│── windows_monitor.py      # Windows host resource telemetry (CPU, RAM, Ollama process, GPU)
│── workload_generator.py   # Synthetic inference request generator (short/medium/long)
│── latency_profiler.py     # Simulation latency engine with pluggable backend interface
│── deadline_model.py       # Deadline evaluation & causal EWMA risk predictor
│── mixed_workload.py       # Background CPU contention scenarios (light, heavy, mixed)
│── trace_exporter.py       # Unified CSV & JSON trace exporter for Member 2
│── main.py                 # CLI entry point supporting --mode simulation and --mode realtime
│── test_profiler.py        # Automated test suite (15 unit tests, including mocked Ollama)
│── test_ollama_live.py     # Manual live integration test against local Ollama daemon
│── requirements.txt        # Dependencies (Python standard library + psutil)
│── README.md               # Complete setup, usage, schema, and troubleshooting guide
└── traces/                 # Exported simulation & real-time traces (CSV / JSON)
```

---

## 3. Windows Setup & Installation

### Step 1: Install Python Dependencies
Open PowerShell or Command Prompt:

```powershell
cd "c:\Users\vello\OneDrive\Desktop\OS PROJECT\llm_workload_profiler"
pip install -r requirements.txt
```
*(Note: Core features run strictly on Python standard libraries; `psutil` is used for host resource telemetry).*

### Step 2: Install and Start Ollama on Windows (For Real-Time Mode)
1. Download Ollama for Windows from [https://ollama.com/download/windows](https://ollama.com/download/windows) and run the installer.
2. Verify Ollama is running or start it in PowerShell:
   ```powershell
   ollama serve
   ```
3. Pull the default lightweight model (`llama3.2` or any model of your choice):
   ```powershell
   ollama pull llama3.2
   ```
4. Verify local availability:
   ```powershell
   ollama list
   ```

*(Note: Simulation mode does NOT require Ollama and can be run immediately without downloading models).*

---

## 4. How to Run

### A. Run Real-Time Profiling Mode (with Ollama)

Submit a prompt to the local Ollama instance and observe streaming generation with real-time metrics:

```powershell
python main.py --mode realtime --model llama3.2 --target-latency 45.0 --prompt "Explain the role of an OS scheduler in local AI inference."
```

If Ollama is not currently active, the CLI prints a helpful diagnostic message and setup guidance.

### B. Run Synthetic Simulation Mode

Run synthetic workloads across bursty background load scenarios without external dependencies:

```powershell
# Default simulation (8 requests, mixed_load scenario, seed 42)
python main.py --mode simulation

# Custom configuration
python main.py --mode simulation --requests 10 --scenario heavy_load --arrival-rate 2.5 --seed 123
```

### C. Run Automated Tests

Run the full automated test suite (all tests mock Ollama, so they pass with or without an active Ollama daemon):

```powershell
python -m unittest test_profiler.py
```

### D. Run Manual Live Integration Test

When Ollama is running locally, test end-to-end communication:

```powershell
python test_ollama_live.py --model llama3.2
```

---

## 5. Trace Schema Documentation (For Member 2 Integration)

All traces exported into `traces/*.json` and `traces/*.csv` follow a standardized contract designed for Member 2's scheduler.

### Field Definitions

| Field Name | Type | Units | Description |
|---|---|---|---|
| `request_id` | `str` | — | Unique request identifier (e.g., `real_123456` or `req_0001`) |
| `mode` | `str` | — | `"realtime"` or `"simulation"` |
| `model` | `str` | — | Target model invoked (e.g. `llama3.2`) |
| `request_start_time` | `str` | ISO 8601 | UTC timestamp of request submission |
| `first_output_latency_ms` | `float` | ms | Time To First Output (TTFO) / Prefill latency |
| `average_inter_chunk_latency_ms`| `float` | ms | Mean latency between consecutive streamed chunks |
| `p95_inter_chunk_latency_ms` | `float` | ms | 95th percentile inter-chunk latency |
| `p99_inter_chunk_latency_ms` | `float` | ms | 99th percentile inter-chunk latency |
| `generation_duration_ms` | `float` | ms | Total wall-clock generation duration |
| `output_chunks` | `int` | count | Total streaming chunks received |
| `output_characters` | `int` | count | Total text characters generated |
| `chunks_per_second` | `float` | chunks/s | Stream throughput |
| `deadline_ms` | `float` | ms | Configurable target maximum inter-chunk latency SLA |
| `deadline_missed` | `bool` | — | `True` if any observed chunk exceeded `deadline_ms` |
| `observed_violation_count` | `int` | count | Number of chunks that violated the deadline |
| `min_observed_slack_ms` | `float` | ms | Worst-case slack: `deadline_ms - max(observed_latency)` |
| `average_observed_slack_ms` | `float` | ms | Average slack: `deadline_ms - average_latency` |
| `predicted_slack_ms` | `float` | ms | Forecasted slack using causal EWMA |
| `predicted_risk_level` | `str` | — | Categorical risk: `"low"`, `"medium"`, or `"high"` |
| `cpu_utilization_percent` | `float` | % | Host CPU utilization snapshot (or `None` if unavailable) |
| `ram_utilization_percent` | `float` | % | Host RAM utilization snapshot (or `None` if unavailable) |
| `gpu_utilization_percent` | `float` | % | NVIDIA GPU utilization via `nvidia-smi` (or `None`) |
| `is_simulated` | `bool` | — | `False` in realtime mode, `True` in simulation mode |
| `measurement_type` | `str` | — | `"actual_wall_clock_chunks"` or `"synthetic_tokens"` |

---

## 6. Real Demonstration Output

### Example: Live Ollama Benchmark (`llama3.2` on Windows)

```text
==============================================================================
  REAL-TIME MEASUREMENT SUMMARY TABLE
==============================================================================
Request ID:                     real_306837
Measurement Mode:               REALTIME (is_simulated = False)
Model Invoked:                  llama3.2
Time To First Output (TTFO):    2673.26 ms
Generation Duration:            17264.06 ms
Total Stream Chunks:            142 chunks
Total Output Characters:        729 chars
Throughput:                     8.23 chunks/sec
Average Inter-Chunk Latency:    103.48 ms
p95 Inter-Chunk Latency:        123.57 ms
p99 Inter-Chunk Latency:        126.83 ms
------------------------------------------------------------------------------
Target Latency (Deadline):      100.00 ms
Deadline Missed:                True (76 chunks exceeded target)
Worst-Case Observed Slack:      -70.87 ms
Predicted Slack (EWMA):         -3.13 ms
Predicted Risk Level:           HIGH
------------------------------------------------------------------------------
Host CPU Utilization:           41.7%
Host RAM Utilization:           88.3%
Host GPU Utilization:           N/A (No NVIDIA GPU detected - Graceful Fallback)

Saved Real-Time Traces:
  JSON Trace: traces\realtime_trace.json
  CSV Trace:  traces\realtime_trace.csv
```

---

## 7. Critical Technical Notes & Limitations

1. **Tokens vs. Streaming Chunks:**
   In real-time HTTP streaming, Ollama returns streamed response chunks as text pieces. While in many autoregressive configurations each chunk corresponds to an individual token, BPE decoders or network buffering may occasionally group multiple tokens into a single chunk.
   *Therefore, real-time measurements represent actual wall-clock **Inter-Chunk Latency (ICL)**, which is clearly distinguished from theoretical Inter-Token Latency (ITL).*
2. **GPU Telemetry:**
   GPU monitoring queries `nvidia-smi`. On machines with integrated Intel/AMD graphics, or where NVIDIA drivers are absent, GPU utilization gracefully reports `None` without crashing.
3. **OS Priority Control:**
   This module measures performance and provides priority metadata to Member 2. It does not manipulate Windows kernel priority classes directly.
4. **Member Responsibilities:**
   - **Member 1 (This Subsystem):** Workload generation, latency measurement, real-time profiling, deadline modeling, trace export.
   - **Member 2:** Scheduling algorithm design (FCFS vs. EDF vs. Least Slack First).
   - **Member 3:** Benchmarking dashboard and comparative visualization.

