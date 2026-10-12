"""
Main CLI Program for LLM Workload Profiling & Latency Measurement.

Part of Member 1: Workload Simulation & Latency Profiling Module.
Project: LLM-Aware OS Scheduler for Latency-Sensitive Local AI Inference.

Modes Supported:
1. `simulation`: Generates synthetic inference workloads and simulates prefill (TTFT)
   and token generation (ITL) under background CPU load scenarios (light, heavy, mixed).
2. `realtime`: Connects to a locally running Ollama instance (default: http://localhost:11434),
   streams responses in real time, measures wall-clock TTFO and inter-chunk latencies,
   captures Windows CPU/RAM/Ollama metrics, and exports scheduler traces.
"""

import argparse
import sys
from typing import List

from workload_generator import WorkloadGenerator
from latency_profiler import LatencyProfiler, SimulatedInferenceBackend
from deadline_model import DeadlineRiskModel, RiskLevel
from mixed_workload import MixedWorkloadRunner, LoadScenario
from trace_exporter import TraceExporter
from realtime_profiler import RealtimeProfiler, OllamaClient
from windows_monitor import WindowsWorkloadMonitor


def format_header(title: str) -> str:
    line = "=" * 78
    return f"\n{line}\n  {title}\n{line}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LLM Workload Profiler & Latency Measurement (Member 1)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mode", "-m",
        type=str,
        default="simulation",
        choices=["simulation", "realtime"],
        help="Profiling mode: 'simulation' (synthetic requests) or 'realtime' (live Ollama)",
    )

    # --- Real-Time Mode Arguments ---
    parser.add_argument(
        "--prompt", "-p",
        type=str,
        default="Explain the difference between operating system scheduling and LLM token decoding in two concise paragraphs.",
        help="[Realtime Mode] Prompt text to submit to Ollama",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="llama3.2",
        help="[Realtime Mode] Ollama model tag to invoke (e.g., llama3.2, mistral, phi3)",
    )
    parser.add_argument(
        "--ollama-url",
        type=str,
        default="http://localhost:11434",
        help="[Realtime Mode] Ollama base URL",
    )
    parser.add_argument(
        "--target-latency",
        type=float,
        default=50.0,
        help="[Realtime Mode] Target maximum inter-chunk latency SLA in milliseconds",
    )

    # --- Simulation Mode Arguments ---
    parser.add_argument(
        "--requests", "-n",
        type=int,
        default=8,
        help="[Simulation Mode] Total number of inference requests to generate",
    )
    parser.add_argument(
        "--arrival-rate", "-r",
        type=float,
        default=2.0,
        help="[Simulation Mode] Arrival rate in requests per second (Poisson process)",
    )
    parser.add_argument(
        "--scenario", "-s",
        type=str,
        default="mixed_load",
        choices=["light_load", "heavy_load", "mixed_load"],
        help="[Simulation Mode] Background CPU interference scenario",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="[Simulation Mode] Random seed for reproducible workloads",
    )

    # --- Output Arguments ---
    parser.add_argument(
        "--output-dir", "-o",
        type=str,
        default="traces",
        help="Directory to save generated CSV and JSON traces",
    )
    parser.add_argument(
        "--trace-name",
        type=str,
        default=None,
        help="Prefix filename for exported trace files (default: auto based on mode)",
    )
    return parser.parse_args()


def run_simulation(args: argparse.Namespace):
    """Executes synthetic workload simulation mode."""
    trace_name = args.trace_name or "simulation_trace"
    print(format_header("SIMULATION MODE: WORKLOAD & LATENCY PROFILER"))
    print(f"Configuration:")
    print(f"  Requests:       {args.requests}")
    print(f"  Arrival Rate:   {args.arrival_rate:.2f} req/s")
    print(f"  Load Scenario:  {args.scenario}")
    print(f"  Random Seed:    {args.seed}")
    print(f"  Trace Output:   {args.output_dir}/")

    scenario_enum = LoadScenario(args.scenario)
    runner = MixedWorkloadRunner(seed=args.seed)

    print("\n[1/5] Generating synthetic inference workload...")
    print("[2/5] Running simulation under specified background load...")
    print("[3/5] Profiling per-token latency (TTFT, ITL percentiles)...")
    print("[4/5] Evaluating deadline misses and calculating predicted risk...")

    result = runner.run_scenario(
        scenario=scenario_enum,
        num_requests=args.requests,
        arrival_rate_rps=args.arrival_rate,
    )

    print("\n[5/5] Exporting traces for downstream scheduler (Member 2)...")
    exporter = TraceExporter(output_dir=args.output_dir)
    exp_config = {
        "mode": "simulation",
        "scenario": args.scenario,
        "seed": args.seed,
        "requests_count": args.requests,
        "arrival_rate_rps": args.arrival_rate,
        "avg_interference_factor": result.interference_factor,
        "is_simulated": True,
    }
    json_path = exporter.export_json(
        trace_name,
        result.requests,
        result.profiles,
        result.evaluations,
        experiment_config=exp_config,
    )
    csv_path = exporter.export_csv(
        trace_name,
        result.requests,
        result.profiles,
        result.evaluations,
    )

    print(format_header("SIMULATION PROFILE SUMMARY TABLE"))
    header_line = (
        f"{'Req ID':<9} {'Type':<12} {'Prompt':<7} {'Out':<5} "
        f"{'TTFT(ms)':<9} {'Target':<7} {'Avg ITL':<8} {'p95 ITL':<8} "
        f"{'Violations':<11} {'Pred Slack':<11} {'Risk':<6}"
    )
    print(header_line)
    print("-" * 105)

    high_risk_count = 0
    medium_risk_count = 0
    low_risk_count = 0
    total_violations = 0
    total_tokens = 0

    for req, prof, ev in zip(result.requests, result.profiles, result.evaluations):
        viol_str = f"{ev.observed_violation_count}/{ev.total_tokens_evaluated}"
        pred_slack_str = f"{ev.predicted_slack_ms:+.1f} ms"

        if ev.predicted_risk_level == RiskLevel.HIGH:
            high_risk_count += 1
        elif ev.predicted_risk_level == RiskLevel.MEDIUM:
            medium_risk_count += 1
        else:
            low_risk_count += 1

        total_violations += ev.observed_violation_count
        total_tokens += ev.total_tokens_evaluated

        print(
            f"{req.request_id:<9} "
            f"{req.request_type.value:<12} "
            f"{req.prompt_tokens:<7} "
            f"{req.output_tokens:<5} "
            f"{prof.ttft_ms:<9.1f} "
            f"{ev.deadline_target_ms:<7.1f} "
            f"{prof.avg_itl_ms:<8.1f} "
            f"{prof.p95_itl_ms:<8.1f} "
            f"{viol_str:<11} "
            f"{pred_slack_str:<11} "
            f"{ev.predicted_risk_level.value.upper():<6}"
        )

    print("-" * 105)
    overall_violation_rate = (total_violations / total_tokens * 100) if total_tokens else 0.0

    print("\nAggregate Observations:")
    print(f"  - Mode: SIMULATION (synthetic measurements, is_simulated=True)")
    print(f"  - Total Generated Tokens: {total_tokens}")
    print(f"  - Observed Deadline Violations: {total_violations} tokens ({overall_violation_rate:.1f}%)")
    print(f"  - Risk Classifications: {high_risk_count} HIGH, {medium_risk_count} MEDIUM, {low_risk_count} LOW")
    print(f"  - Trace Artifact (JSON): {json_path}")
    print(f"  - Trace Artifact (CSV):  {csv_path}")
    print("\n[Ready for Member 2 Integration] Trace files contain required schema for scheduling evaluation.")


def run_realtime(args: argparse.Namespace):
    """Executes live Ollama real-time workload profiling."""
    trace_name = args.trace_name or "realtime_trace"
    print(format_header("REAL-TIME PROFILING MODE: LIVE OLLAMA STREAMING"))
    print(f"Configuration:")
    print(f"  Ollama URL:     {args.ollama_url}")
    print(f"  Target Model:   {args.model}")
    print(f"  Target Latency: {args.target_latency:.1f} ms (Deadline)")
    print(f"  Prompt:         \"{args.prompt[:65]}...\"" if len(args.prompt) > 65 else f"  Prompt:         \"{args.prompt}\"")
    print(f"  Trace Output:   {args.output_dir}/")

    client = OllamaClient(base_url=args.ollama_url)
    
    # 1. Health check with helpful instructions if unavailable
    print("\n[1/5] Checking Ollama service availability...")
    if not client.check_health():
        print(f"\n[ERROR] Could not connect to Ollama at {args.ollama_url}.")
        print("\nTroubleshooting Steps for Windows:")
        print("  1. Make sure Ollama for Windows is installed (https://ollama.com).")
        print("  2. Open PowerShell or Command Prompt and start the server:")
        print("       ollama serve")
        print(f"  3. In another terminal, ensure the model is pulled:")
        print(f"       ollama pull {args.model}")
        print("  4. Re-run this command once Ollama is responding.")
        print("\n(Note: You can still run '--mode simulation' at any time without Ollama!)")
        sys.exit(1)

    # 2. Check if the specified model is present
    installed_models = client.list_models()
    print(f"[2/5] Connected to Ollama! Available models: {installed_models or 'None found'}")
    if installed_models and not any(args.model in m for m in installed_models):
        print(f"\n[WARNING] Model '{args.model}' not found in installed models: {installed_models}")
        print(f"To download it manually, run in a separate terminal: ollama pull {args.model}")
        print("Proceeding anyway in case model tag has a custom alias...")

    # 3. Stream inference and record wall-clock timings
    monitor = WindowsWorkloadMonitor()
    profiler = RealtimeProfiler(ollama_client=client, monitor=monitor)

    print(f"\n[3/5] Streaming inference request to '{args.model}'...")
    print("------ Stream Output Start ------")
    
    def on_chunk(text: str):
        sys.stdout.write(text)
        sys.stdout.flush()

    try:
        profile_res = profiler.profile_request(
            prompt=args.prompt,
            model=args.model,
            target_latency_ms=args.target_latency,
            chunk_callback=on_chunk,
        )
    except ConnectionError as ce:
        print(f"\n[ERROR] {ce}")
        sys.exit(1)

    print("\n------- Stream Output End -------")

    # 4. Export traces
    print("\n[4/5] Exporting real-time workload trace for Member 2 scheduler...")
    exporter = TraceExporter(output_dir=args.output_dir)
    exp_config = {
        "mode": "realtime",
        "model": args.model,
        "ollama_url": args.ollama_url,
        "is_simulated": False,
        "measurement_type": "actual_wall_clock_chunks",
    }
    json_path = exporter.export_realtime_json(trace_name, [profile_res], experiment_config=exp_config)
    csv_path = exporter.export_realtime_csv(trace_name, [profile_res])

    # 5. Display concise measurement summary
    print(format_header("REAL-TIME MEASUREMENT SUMMARY TABLE"))
    rec = profile_res.to_scheduler_record()
    print(f"Request ID:                     {rec['request_id']}")
    print(f"Measurement Mode:               {rec['mode'].upper()} (is_simulated = False)")
    print(f"Model Invoked:                  {rec['model']}")
    print(f"Time To First Output (TTFO):    {rec['first_output_latency_ms']:.2f} ms")
    print(f"Generation Duration:            {rec['generation_duration_ms']:.2f} ms")
    print(f"Total Stream Chunks:            {rec['output_chunks']} chunks")
    print(f"Total Output Characters:        {rec['output_characters']} chars")
    print(f"Throughput:                     {rec['chunks_per_second']:.2f} chunks/sec")
    print(f"Average Inter-Chunk Latency:    {rec['average_inter_chunk_latency_ms']:.2f} ms")
    print(f"p95 Inter-Chunk Latency:        {rec['p95_inter_chunk_latency_ms']:.2f} ms")
    print(f"p99 Inter-Chunk Latency:        {rec['p99_inter_chunk_latency_ms']:.2f} ms")
    print("-" * 78)
    print(f"Target Latency (Deadline):      {rec['deadline_ms']:.2f} ms")
    print(f"Deadline Missed:                {rec['deadline_missed']} ({rec['observed_violation_count']} chunks exceeded target)")
    print(f"Worst-Case Observed Slack:      {rec['min_observed_slack_ms']:.2f} ms")
    print(f"Predicted Slack (EWMA):         {rec['predicted_slack_ms']:.2f} ms")
    print(f"Predicted Risk Level:           {rec['predicted_risk_level'].upper()}")
    print("-" * 78)
    print(f"Host CPU Utilization:           {rec['cpu_utilization_percent']}%" if rec['cpu_utilization_percent'] is not None else "Host CPU Utilization:           Unavailable")
    print(f"Host RAM Utilization:           {rec['ram_utilization_percent']}%" if rec['ram_utilization_percent'] is not None else "Host RAM Utilization:           Unavailable")
    print(f"Host GPU Utilization:           {rec['gpu_utilization_percent']}%" if rec['gpu_utilization_percent'] is not None else "Host GPU Utilization:           N/A (No NVIDIA GPU detected)")
    print(f"\nSaved Real-Time Traces:")
    print(f"  JSON Trace: {json_path}")
    print(f"  CSV Trace:  {csv_path}")
    print("\n[Scheduler Ready] Real measurements exported in standardized schema for Member 2.")


def main():
    args = parse_args()
    if args.mode == "realtime":
        run_realtime(args)
    else:
        run_simulation(args)


if __name__ == "__main__":
    main()
