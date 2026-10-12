"""
Manual Real-Time Integration Test Script for Ollama.

Part of Member 1: Real-Time Workload Profiling Subsystem.
Project: LLM-Aware OS Scheduler for Latency-Sensitive Local AI Inference.

Usage:
  python test_ollama_live.py [--model llama3.2] [--prompt "Hello!"]

This script checks whether Ollama is running locally, queries available models,
and executes one real streamed prompt, printing wall-clock latency metrics.
If Ollama is not active, it exits gracefully with clear setup instructions.
"""

import argparse
import sys

from realtime_profiler import OllamaClient, RealtimeProfiler
from windows_monitor import WindowsWorkloadMonitor


def main():
    parser = argparse.ArgumentParser(description="Live integration test for Ollama")
    parser.add_argument("--model", type=str, default="llama3.2", help="Model tag to test")
    parser.add_argument("--prompt", type=str, default="Explain in one sentence what an operating system scheduler does.", help="Prompt to test")
    parser.add_argument("--url", type=str, default="http://localhost:11434", help="Ollama API URL")
    args = parser.parse_args()

    print("====================================================================")
    print("  LIVE OLLAMA REAL-TIME PROFILING INTEGRATION TEST")
    print("====================================================================")
    print(f"Target URL:    {args.url}")
    print(f"Target Model:  {args.model}")
    print(f"Test Prompt:   \"{args.prompt}\"")
    print("--------------------------------------------------------------------")

    client = OllamaClient(base_url=args.url)
    print("\n[Step 1] Checking connection to Ollama daemon...")
    if not client.check_health():
        print(f"[-] Ollama is NOT reachable at {args.url}.")
        print("\nTo enable live testing on your Windows machine:")
        print("  1. Download and install Ollama from https://ollama.com")
        print("  2. Start the Ollama background daemon: `ollama serve`")
        print(f"  3. Pull your preferred model:          `ollama pull {args.model}`")
        print("  4. Re-run this test script.")
        print("\nIntegration test status: SKIPPED (Ollama daemon offline).")
        sys.exit(0)

    print("[+] Ollama daemon is active and responding!")

    print("\n[Step 2] Listing installed models...")
    models = client.list_models()
    print(f"Installed models: {models}")
    if not any(args.model in m for m in models):
        print(f"[-] Warning: '{args.model}' not found in installed models.")
        print(f"    Run `ollama pull {args.model}` before invoking.")

    print("\n[Step 3] Submitting live streaming inference request...")
    profiler = RealtimeProfiler(ollama_client=client, monitor=WindowsWorkloadMonitor())

    def on_token(chunk: str):
        sys.stdout.write(chunk)
        sys.stdout.flush()

    try:
        result = profiler.profile_request(
            prompt=args.prompt,
            model=args.model,
            target_latency_ms=45.0,
            chunk_callback=on_token,
        )
    except Exception as e:
        print(f"\n[-] Inference failed: {e}")
        sys.exit(1)

    print("\n\n--------------------------------------------------------------------")
    print("  LIVE MEASUREMENT SUMMARY")
    print("--------------------------------------------------------------------")
    rec = result.to_scheduler_record()
    print(f"Time To First Output (TTFO): {rec['first_output_latency_ms']} ms")
    print(f"Total Generation Duration:   {rec['generation_duration_ms']} ms")
    print(f"Output Chunks:               {rec['output_chunks']}")
    print(f"Output Characters:           {rec['output_characters']}")
    print(f"Throughput:                  {rec['chunks_per_second']} chunks/sec")
    print(f"Avg Inter-Chunk Latency:     {rec['average_inter_chunk_latency_ms']} ms")
    print(f"p95 Inter-Chunk Latency:     {rec['p95_inter_chunk_latency_ms']} ms")
    print(f"Target Latency (Deadline):   {rec['deadline_ms']} ms")
    print(f"Deadline Missed:             {rec['deadline_missed']}")
    print(f"Predicted Slack (EWMA):      {rec['predicted_slack_ms']} ms")
    print(f"CPU Utilization:             {rec['cpu_utilization_percent']}%")
    print(f"RAM Utilization:             {rec['ram_utilization_percent']}%")
    print(f"GPU Utilization:             {rec['gpu_utilization_percent']}")
    print("--------------------------------------------------------------------")
    print("[+] Live integration test succeeded!")


if __name__ == "__main__":
    main()
