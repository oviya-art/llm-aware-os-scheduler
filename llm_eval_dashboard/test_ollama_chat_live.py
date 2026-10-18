"""
Optional live integration test against an active local Ollama daemon.

Usage:
    python test_ollama_chat_live.py [--model llama3.2:latest]

Skips gracefully if Ollama is not running locally.
"""

import argparse
import sys
import time

from live_scheduler import LiveSchedulerController, OllamaService


def main():
    parser = argparse.ArgumentParser(description="Live Ollama chat integration test")
    parser.add_argument("--model", default="llama3.2:latest", help="Ollama model to test")
    parser.add_argument("--url", default="http://localhost:11434", help="Ollama base URL")
    args = parser.parse_args()

    service = OllamaService(base_url=args.url)
    if not service.check_health():
        print(f"[SKIP] Ollama is not running at {args.url}. Live integration test skipped.")
        return 0

    models = service.list_models()
    print(f"[OK] Ollama is active! Available models: {models}")
    target_model = args.model
    if target_model not in models and models:
        target_model = models[0]
        print(f"[INFO] '{args.model}' not found; using '{target_model}' instead.")

    print(f"\n[TEST] Initializing LiveSchedulerController with model '{target_model}'...")
    controller = LiveSchedulerController(ollama_service=service, active_policy="token_laxity")

    try:
        print("[TEST] Submitting live request with policy=token_laxity, priority=high, deadline=15000ms...")
        req = controller.submit_request(
            prompt="Respond in 4 words.",
            model=target_model,
            policy="token_laxity",
            priority="high",
            deadline_ms=15000.0,
        )

        print(f"[TEST] Request enqueued: ID={req.request_id}, status={req.status}")
        print("[STREAM] Receiving tokens: ", end="", flush=True)

        full_text = ""
        while True:
            event = req.chunk_queue.get(timeout=60.0)
            if event["type"] == "chunk":
                print(event["text"], end="", flush=True)
                full_text += event["text"]
            elif event["type"] == "done":
                print("\n[STREAM] Done!")
                break
            elif event["type"] == "error":
                print(f"\n[ERROR] Request failed: {event.get('message')}")
                return 1

        print("\n--- Live Measured Metrics ---")
        m = req.metrics
        print(f"Queue Wait Time:        {m['queue_wait_ms']} ms")
        print(f"Time to First Output:   {m['ttfo_ms']} ms")
        print(f"Average Inter-Chunk:    {m['average_icl_ms']} ms")
        print(f"p95 Inter-Chunk:        {m['p95_icl_ms']} ms")
        print(f"Total Duration:         {m['generation_duration_ms']} ms")
        print(f"Chunks Count:           {m['total_chunks']}")
        print(f"Characters Count:       {m['total_characters']}")
        print(f"Deadline Met:           {not m['deadline_missed']}")
        print("\n[PASS] Live Ollama chat integration test passed successfully!")
        return 0
    finally:
        controller.shutdown()


if __name__ == "__main__":
    sys.exit(main())
