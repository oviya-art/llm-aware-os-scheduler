"""
Live Application-Level LLM Workload Scheduler & Ollama Controller.

Part of Member 3: Evaluation, Benchmarking & Live Interactive Inference.
Project: LLM-Aware OS Scheduler for Latency-Sensitive Local AI Inference.

Features:
- Thread-safe request queue with deterministic dispatching.
- Single-active-request inference constraint for local commodity LLM execution.
- Four dispatch policies:
    * FCFS: First-Come, First-Served (FIFO arrival order).
    * Priority: Multi-level priority (high > normal > low) with FIFO tie-breaker.
    * EDF: Earliest Deadline First (absolute deadline = arrival + deadline_budget).
    * Token Laxity: Application queue-level adaptation using remaining time to deadline
      minus causal estimated service time, with priority class bonus.
- Real-time streaming integration with Ollama (/api/chat or /api/generate).
- High-resolution monotonic timers (time.perf_counter) measuring:
    * Queue wait time
    * Time To First Output (TTFO)
    * Inter-chunk latencies (mean, p95, p99, count)
    * Total generation duration
- Non-blocking SSE (Server-Sent Events) streaming per client.
- Separate session/conversation chat histories.
- Comprehensive metrics logging and JSON/CSV export.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import io
import json
import math
import os
import queue
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
VALID_POLICIES = ["fcfs", "priority", "edf", "token_laxity"]
VALID_PRIORITIES = ["high", "normal", "low"]
PRIORITY_RANKS = {"high": 0, "normal": 1, "low": 2}
CLASS_BONUSES = {"high": 0.25, "normal": 0.10, "low": 0.0}


# ============================================================================
# 1. Ollama Service Wrapper
# ============================================================================

class OllamaService:
    """Safe, non-privileged client for local Ollama daemon."""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL):
        self.base_url = base_url.rstrip("/")

    def check_health(self, timeout_s: float = 2.0) -> bool:
        """Verifies if Ollama is running and responding."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=timeout_s) as response:
                return response.status == 200
        except Exception:
            return False

    def list_models(self, timeout_s: float = 3.0) -> List[str]:
        """Returns the list of locally pulled/installed model tags."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=timeout_s) as response:
                if response.status != 200:
                    return []
                data = json.loads(response.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
                return sorted(models)
        except Exception:
            return []

    def stream_chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        chunk_callback: Callable[[str, float], None],
        timeout_s: float = 180.0,
    ) -> Dict[str, Any]:
        """Streams chat completion from Ollama /api/chat.

        Args:
            model: Installed model tag.
            messages: List of {'role': 'user'|'assistant', 'content': '...'}.
            chunk_callback: Callback invoked per text delta with timestamp.
            timeout_s: Connection & read timeout.

        Returns:
            Dict containing raw Ollama final stats (eval_count, prompt_eval_count, etc.).
        """
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        final_stats: Dict[str, Any] = {}

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                for line in resp:
                    line_str = line.decode("utf-8").strip()
                    if not line_str:
                        continue
                    arrival_time = time.perf_counter()
                    try:
                        event = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue

                    # Ollama /api/chat emits message: {"role": "assistant", "content": "..."}
                    msg = event.get("message", {})
                    text_delta = msg.get("content", "")
                    if text_delta:
                        chunk_callback(text_delta, arrival_time)

                    if event.get("done", False):
                        final_stats = {
                            "total_duration": event.get("total_duration"),
                            "load_duration": event.get("load_duration"),
                            "prompt_eval_count": event.get("prompt_eval_count"),
                            "prompt_eval_duration": event.get("prompt_eval_duration"),
                            "eval_count": event.get("eval_count"),
                            "eval_duration": event.get("eval_duration"),
                        }
                        break
        except urllib.error.URLError as exc:
            raise ConnectionError(
                f"Ollama connection error at {self.base_url}: {exc}. "
                "Ensure Ollama is running (`ollama serve`)."
            ) from exc

        return final_stats


# ============================================================================
# 2. Live Request Data Model
# ============================================================================

@dataclass
class LiveRequest:
    """Represents an incoming chat inference request in the application scheduler."""
    request_id: str
    conversation_id: str
    prompt: str
    model: str
    policy: str = "fcfs"
    priority: str = "normal"
    deadline_ms: float = 5000.0          # Target completion SLA budget from arrival
    ttft_budget_ms: float = 1000.0       # Target first-chunk SLA budget
    target_icl_ms: float = 80.0          # Target inter-chunk latency budget
    arrival_time: float = field(default_factory=time.time)
    arrival_perf: float = field(default_factory=time.perf_counter)
    arrival_index: int = 0
    dispatch_index: Optional[int] = None

    # Lifecycle state: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
    status: str = "queued"
    dispatch_perf: Optional[float] = None
    first_chunk_perf: Optional[float] = None
    completion_perf: Optional[float] = None
    error_message: Optional[str] = None

    # Thread-safe queue for SSE streaming to browser client
    chunk_queue: queue.Queue = field(default_factory=queue.Queue)

    # Measured live metrics
    output_text: str = ""
    chunk_count: int = 0
    character_count: int = 0
    icl_samples_ms: List[float] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_summary(self, queue_position: int = -1) -> Dict[str, Any]:
        """Safe public representation of request status."""
        now_perf = time.perf_counter()
        wait_ms = (
            (self.dispatch_perf - self.arrival_perf) * 1000.0
            if self.dispatch_perf is not None
            else (now_perf - self.arrival_perf) * 1000.0
        )
        return {
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "model": self.model,
            "policy": self.policy,
            "priority": self.priority,
            "deadline_ms": self.deadline_ms,
            "target_icl_ms": self.target_icl_ms,
            "arrival_time": self.arrival_time,
            "arrival_index": self.arrival_index,
            "dispatch_index": self.dispatch_index,
            "status": self.status,
            "queue_position": queue_position,
            "wait_ms": round(wait_ms, 1),
            "output_chars": self.character_count,
            "output_chunks": self.chunk_count,
            "prompt_preview": self.prompt[:60] + ("..." if len(self.prompt) > 60 else ""),
            "metrics": self.metrics,
            "error": self.error_message,
            "source": "live_ollama",
        }


# ============================================================================
# 3. Queue Scheduling Policies
# ============================================================================

def percentile(values: List[float], p: float) -> float:
    """Computes percentile on a list of floats."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_v[int(k)]
    return sorted_v[f] * (c - k) + sorted_v[c] * (k - f)


class QueuePolicy:
    """Base interface for request queue scheduling policies."""
    name: str = "base"

    def rank(self, requests: List[LiveRequest], now_perf: float, context: Any = None) -> List[LiveRequest]:
        raise NotImplementedError


class FCFSPolicy(QueuePolicy):
    """First-Come, First-Served: strictly preserves arrival order."""
    name = "fcfs"

    def rank(self, requests: List[LiveRequest], now_perf: float, context: Any = None) -> List[LiveRequest]:
        return sorted(requests, key=lambda r: (r.arrival_perf, r.arrival_index))


class PriorityPolicy(QueuePolicy):
    """Static Priority with deterministic FIFO tie-breaking within each level."""
    name = "priority"

    def rank(self, requests: List[LiveRequest], now_perf: float, context: Any = None) -> List[LiveRequest]:
        return sorted(requests, key=lambda r: (
            PRIORITY_RANKS.get(r.priority, 1),
            r.arrival_perf,
            r.arrival_index,
        ))


class EDFPolicy(QueuePolicy):
    """Earliest Deadline First: orders by absolute deadline (arrival + deadline_ms)."""
    name = "edf"

    def rank(self, requests: List[LiveRequest], now_perf: float, context: Any = None) -> List[LiveRequest]:
        def key(r: LiveRequest):
            abs_deadline = r.arrival_perf + (r.deadline_ms / 1000.0)
            return (abs_deadline, r.arrival_perf, r.arrival_index)
        return sorted(requests, key=key)


class TokenLaxityPolicy(QueuePolicy):
    """Queue-level adaptation of the Token-Laxity Scheduler.

    Score calculation (causal estimate):
        Time to absolute deadline: delta_d = (arrival + deadline_ms/1000) - now
        Estimated duration: S_est = base_time + prompt_len * char_factor
        Queue laxity: L = delta_d - S_est
        Normalized laxity: lambda_val = L / (deadline_ms / 1000)
        Urgency key: key = lambda_val - class_bonus(priority)
    Lower key = more urgent (dispatched first).
    """
    name = "token_laxity"

    def __init__(self, avg_ms_per_char: float = 8.0, base_overhead_s: float = 0.5):
        self.avg_ms_per_char = avg_ms_per_char
        self.base_overhead_s = base_overhead_s

    def rank(self, requests: List[LiveRequest], now_perf: float, context: Any = None) -> List[LiveRequest]:
        # Context may provide learned EWMA service rate per character if available
        char_factor_s = (self.avg_ms_per_char / 1000.0)
        if context and hasattr(context, "ewma_sec_per_char") and context.ewma_sec_per_char > 0:
            char_factor_s = context.ewma_sec_per_char

        def key(r: LiveRequest):
            abs_deadline = r.arrival_perf + (r.deadline_ms / 1000.0)
            delta_d = abs_deadline - now_perf
            s_est = self.base_overhead_s + (len(r.prompt) * char_factor_s)
            laxity = delta_d - s_est
            budget_s = max(r.deadline_ms / 1000.0, 0.05)
            norm_laxity = laxity / budget_s
            bonus = CLASS_BONUSES.get(r.priority, 0.10)
            urgency_score = norm_laxity - bonus
            return (urgency_score, r.arrival_perf, r.arrival_index)

        return sorted(requests, key=key)


POLICIES: Dict[str, QueuePolicy] = {
    "fcfs": FCFSPolicy(),
    "priority": PriorityPolicy(),
    "edf": EDFPolicy(),
    "token_laxity": TokenLaxityPolicy(),
}


# ============================================================================
# 4. Thread-Safe Live Scheduler Controller
# ============================================================================

class LiveSchedulerController:
    """Manages the request queue, active inference, and evaluation history."""

    def __init__(self, ollama_service: Optional[OllamaService] = None, active_policy: str = "fcfs"):
        self.ollama = ollama_service or OllamaService()
        self.active_policy = active_policy if active_policy in POLICIES else "fcfs"
        self._lock = threading.RLock()
        self._wake_condition = threading.Condition(self._lock)
        self._stop = False

        self._counter = 0
        self._dispatch_counter = 0
        self.queue: List[LiveRequest] = []
        self.active_request: Optional[LiveRequest] = None
        self.registry: Dict[str, LiveRequest] = {}
        self.conversations: Dict[str, List[Dict[str, str]]] = {}
        self.completed_history: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.max_events = 250

        # Online EWMA tracker for character duration estimation (for Token Laxity)
        self.ewma_sec_per_char: float = 0.008

        # Worker thread
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="LiveSchedulerWorker")
        self.worker_thread.start()

    def _record_event(self, event_type: str, details: Dict[str, Any]):
        """Records a timestamped scheduling event for timeline and feed."""
        now_ts = time.time()
        ev = {
            "timestamp": now_ts,
            "time_str": time.strftime("%H:%M:%S", time.localtime(now_ts)),
            "type": event_type,
            "source": "live_ollama",
            **details,
        }
        self.events.append(ev)
        if len(self.events) > self.max_events:
            self.events.pop(0)

    def set_policy(self, policy_name: str) -> bool:
        """Changes the active default dispatch policy."""
        with self._lock:
            if policy_name in POLICIES:
                old_policy = self.active_policy
                self.active_policy = policy_name
                self._record_event("policy_changed", {
                    "policy": policy_name,
                    "old_policy": old_policy,
                })
                return True
            return False

    def submit_request(
        self,
        prompt: str,
        model: str,
        policy: Optional[str] = None,
        priority: str = "normal",
        deadline_ms: float = 5000.0,
        ttft_budget_ms: float = 1000.0,
        target_icl_ms: float = 80.0,
        conversation_id: Optional[str] = None,
    ) -> LiveRequest:
        """Validates and enqueues an incoming chat request."""
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("Prompt cannot be empty")
        if not model or not re.match(r"^[a-zA-Z0-9_\-\.:]+$", model):
            raise ValueError(f"Invalid model name format: {model}")

        policy_to_use = policy if policy in POLICIES else self.active_policy
        prio_to_use = priority if priority in VALID_PRIORITIES else "normal"
        d_ms = max(50.0, min(120000.0, float(deadline_ms)))
        ttft_b = max(50.0, min(60000.0, float(ttft_budget_ms)))
        icl_b = max(10.0, min(5000.0, float(target_icl_ms)))
        cid = conversation_id.strip() if conversation_id and conversation_id.strip() else f"conv_{int(time.time()*1000)}"

        with self._lock:
            self._counter += 1
            rid = f"req_{int(time.time()*1000)}_{self._counter}"
            req = LiveRequest(
                request_id=rid,
                conversation_id=cid,
                prompt=clean_prompt,
                model=model,
                policy=policy_to_use,
                priority=prio_to_use,
                deadline_ms=d_ms,
                ttft_budget_ms=ttft_b,
                target_icl_ms=icl_b,
                arrival_time=time.time(),
                arrival_perf=time.perf_counter(),
                arrival_index=self._counter,
                status="queued",
            )
            self.queue.append(req)
            self.registry[rid] = req
            self._record_event("enqueued", {
                "request_id": req.request_id,
                "arrival_index": req.arrival_index,
                "policy": req.policy,
                "priority": req.priority,
                "deadline_ms": req.deadline_ms,
                "prompt_preview": clean_prompt[:60] + ("..." if len(clean_prompt) > 60 else ""),
            })
            self._wake_condition.notify_all()
            return req

    def submit_batch(
        self,
        prompts: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        policy: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Submits a burst of benchmark test requests to exercise queue reordering."""
        default_batch = [
            {
                "prompt": "Explain the role of an OS scheduler in 2 sentences.",
                "priority": "low",
                "deadline_ms": 16000.0,
                "ttft_budget_ms": 1500.0,
            },
            {
                "prompt": "List the 3 main process scheduling states (names only).",
                "priority": "high",
                "deadline_ms": 4000.0,
                "ttft_budget_ms": 800.0,
            },
            {
                "prompt": "Define CPU quantum in one short sentence.",
                "priority": "normal",
                "deadline_ms": 3000.0,
                "ttft_budget_ms": 900.0,
            },
            {
                "prompt": "What is starvation in operating system scheduling?",
                "priority": "normal",
                "deadline_ms": 8000.0,
                "ttft_budget_ms": 1000.0,
            },
        ]

        target_model = model or "llama3.2:latest"
        available = self.ollama.list_models()
        if available and target_model not in available:
            target_model = available[0]

        items_to_submit = prompts if prompts else default_batch
        policy_to_use = policy if policy in POLICIES else self.active_policy

        results = []
        with self._lock:
            cid = f"batch_{int(time.time()*1000)}"
            for item in items_to_submit:
                req = self.submit_request(
                    prompt=item.get("prompt", "Hello"),
                    model=target_model,
                    policy=policy_to_use,
                    priority=item.get("priority", "normal"),
                    deadline_ms=float(item.get("deadline_ms", 5000.0)),
                    ttft_budget_ms=float(item.get("ttft_budget_ms", 1000.0)),
                    conversation_id=cid,
                )
                results.append({
                    "request_id": req.request_id,
                    "arrival_index": req.arrival_index,
                    "priority": req.priority,
                    "deadline_ms": req.deadline_ms,
                    "prompt_preview": req.prompt[:50],
                })
        return results

    def cancel_request(self, request_id: str) -> bool:
        """Cancels a queued request if it has not dispatched yet."""
        with self._lock:
            for i, r in enumerate(self.queue):
                if r.request_id == request_id:
                    self.queue.pop(i)
                    r.status = "cancelled"
                    r.error_message = "Cancelled by user"
                    r.chunk_queue.put({"type": "cancelled", "message": "Request cancelled before execution"})
                    self._record_event("cancelled", {"request_id": request_id})
                    return True
            return False

    def get_request(self, request_id: str) -> Optional[LiveRequest]:
        """Retrieves a request by its ID."""
        with self._lock:
            return self.registry.get(request_id)

    def get_queue_status(self) -> Dict[str, Any]:
        """Provides a safe snapshot of current queue state."""
        with self._lock:
            now_perf = time.perf_counter()
            policy_inst = POLICIES[self.active_policy]
            sorted_queue = policy_inst.rank(self.queue, now_perf, context=self)
            items = [r.to_summary(queue_position=idx + 1) for idx, r in enumerate(sorted_queue)]
            active = self.active_request.to_summary(queue_position=0) if self.active_request else None
            return {
                "active_policy": self.active_policy,
                "supported_policies": list(POLICIES.keys()),
                "queued_count": len(self.queue),
                "is_busy": active is not None,
                "active_request": active,
                "queue": items,
            }

    def get_timeline(self) -> Dict[str, Any]:
        """Returns requests and chronological events for the Live Arena timeline."""
        with self._lock:
            now_perf = time.perf_counter()
            timeline_items: List[Dict[str, Any]] = []

            for h in self.completed_history[-50:]:
                timeline_items.append(dict(h))

            if self.active_request:
                act = self.active_request
                active_wait = (act.dispatch_perf - act.arrival_perf) * 1000.0 if act.dispatch_perf else 0.0
                active_dur = (now_perf - act.dispatch_perf) * 1000.0 if act.dispatch_perf else 0.0
                act_entry = {
                    "request_id": act.request_id,
                    "conversation_id": act.conversation_id,
                    "model": act.model,
                    "policy": act.policy,
                    "priority": act.priority,
                    "deadline_ms": act.deadline_ms,
                    "target_icl_ms": act.target_icl_ms,
                    "status": "running",
                    "arrival_index": act.arrival_index,
                    "dispatch_index": act.dispatch_index,
                    "queue_wait_ms": round(active_wait, 1),
                    "ttfo_ms": round((act.first_chunk_perf - act.dispatch_perf) * 1000.0, 1) if act.first_chunk_perf else None,
                    "generation_duration_ms": round(active_dur, 1),
                    "chunks": act.chunk_count,
                    "characters": act.character_count,
                    "deadline_missed": (active_wait + active_dur) > act.deadline_ms,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(act.arrival_time)),
                    "prompt_preview": act.prompt[:60] + ("..." if len(act.prompt) > 60 else ""),
                    "source": "live_ollama",
                }
                timeline_items.append(act_entry)

            policy_inst = POLICIES.get(self.active_policy, POLICIES["fcfs"])
            sorted_q = policy_inst.rank(self.queue, now_perf, context=self)
            for idx, q_req in enumerate(sorted_q):
                wait_so_far = (now_perf - q_req.arrival_perf) * 1000.0
                q_entry = {
                    "request_id": q_req.request_id,
                    "conversation_id": q_req.conversation_id,
                    "model": q_req.model,
                    "policy": q_req.policy,
                    "priority": q_req.priority,
                    "deadline_ms": q_req.deadline_ms,
                    "target_icl_ms": q_req.target_icl_ms,
                    "status": "queued",
                    "queue_position": idx + 1,
                    "arrival_index": q_req.arrival_index,
                    "dispatch_index": None,
                    "queue_wait_ms": round(wait_so_far, 1),
                    "ttfo_ms": None,
                    "generation_duration_ms": 0.0,
                    "chunks": 0,
                    "characters": 0,
                    "deadline_missed": wait_so_far > q_req.deadline_ms,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(q_req.arrival_time)),
                    "prompt_preview": q_req.prompt[:60] + ("..." if len(q_req.prompt) > 60 else ""),
                    "source": "live_ollama",
                }
                timeline_items.append(q_entry)

            completed_count = len([r for r in self.completed_history if r.get("status") == "completed"])
            failed_count = len([r for r in self.completed_history if r.get("status") == "failed"])

            return {
                "source": "live_ollama",
                "active_policy": self.active_policy,
                "supported_policies": list(POLICIES.keys()),
                "total_submitted": self._counter,
                "queued_count": len(self.queue),
                "is_busy": self.active_request is not None,
                "active_request": self.active_request.to_summary(queue_position=0) if self.active_request else None,
                "completed_count": completed_count,
                "failed_count": failed_count,
                "timeline": timeline_items,
                "events": list(reversed(self.events[-60:])),
            }

    def clear_conversation(self, conversation_id: str) -> bool:
        """Clears memory for a specific conversation without affecting other sessions."""
        with self._lock:
            if conversation_id in self.conversations:
                del self.conversations[conversation_id]
                return True
            return False

    def get_conversation_history(self, conversation_id: str) -> List[Dict[str, str]]:
        """Returns message list for a conversation."""
        with self._lock:
            return list(self.conversations.get(conversation_id, []))

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Computes comprehensive live evaluation stats with means, medians, and percentiles."""
        with self._lock:
            runs = self.completed_history
            total = len(runs)
            if not total:
                return {
                    "source": "live_ollama",
                    "total_requests": self._counter,
                    "completed_requests": 0,
                    "failed_requests": 0,
                    "queued_requests": len(self.queue),
                    "running_requests": 1 if self.active_request else 0,
                    "deadline_attainment_pct": 0.0,
                    "deadline_miss_rate_pct": 0.0,
                    "mean_wait_ms": 0.0,
                    "median_wait_ms": 0.0,
                    "mean_ttfo_ms": 0.0,
                    "median_ttfo_ms": 0.0,
                    "mean_icl_ms": 0.0,
                    "mean_duration_ms": 0.0,
                    "median_duration_ms": 0.0,
                    "p95_wait_ms": 0.0,
                    "p99_wait_ms": 0.0,
                    "p95_ttfo_ms": 0.0,
                    "p99_ttfo_ms": 0.0,
                    "p95_icl_ms": 0.0,
                    "p99_icl_ms": 0.0,
                    "sample_counts": {
                        "total_submitted": self._counter,
                        "completed": 0,
                        "failed": 0,
                        "wait": 0,
                        "ttfo": 0,
                        "duration": 0,
                        "icl": 0,
                    },
                    "runs_by_policy": {},
                    "history": [],
                }

            completed = [r for r in runs if r["status"] == "completed"]
            failed = [r for r in runs if r["status"] == "failed"]
            on_time = [r for r in completed if not r.get("deadline_missed", False)]

            wait_times = [float(r["queue_wait_ms"]) for r in runs]
            ttfos = [float(r["ttfo_ms"]) for r in completed if r.get("ttfo_ms") is not None]
            icls = [float(r["average_icl_ms"]) for r in completed if r.get("average_icl_ms") is not None]
            durations = [float(r["generation_duration_ms"]) for r in completed if r.get("generation_duration_ms") is not None]

            att_pct = round(100.0 * len(on_time) / max(1, len(completed)), 1)
            miss_pct = round(100.0 - att_pct, 1)

            by_policy: Dict[str, Dict[str, Any]] = {}
            for p in POLICIES:
                p_runs = [r for r in completed if r.get("policy") == p]
                if p_runs:
                    p_on_time = [r for r in p_runs if not r.get("deadline_missed", False)]
                    p_waits = [float(r["queue_wait_ms"]) for r in p_runs]
                    p_ttfos = [float(r["ttfo_ms"]) for r in p_runs if r.get("ttfo_ms") is not None]
                    p_durs = [float(r["generation_duration_ms"]) for r in p_runs if r.get("generation_duration_ms") is not None]
                    p_p95_icls = [float(r["p95_icl_ms"]) for r in p_runs if r.get("p95_icl_ms") is not None]

                    by_policy[p] = {
                        "sample_size": len(p_runs),
                        "deadline_met_pct": round(100.0 * len(p_on_time) / len(p_runs), 1),
                        "deadline_miss_pct": round(100.0 - (100.0 * len(p_on_time) / len(p_runs)), 1),
                        "mean_wait_ms": round(sum(p_waits) / len(p_waits), 1),
                        "median_wait_ms": round(percentile(p_waits, 0.50), 1),
                        "mean_ttfo_ms": round(sum(p_ttfos) / max(1, len(p_ttfos)), 1),
                        "median_ttfo_ms": round(percentile(p_ttfos, 0.50), 1) if p_ttfos else 0.0,
                        "mean_duration_ms": round(sum(p_durs) / max(1, len(p_durs)), 1),
                        "p95_icl_ms": round(percentile(p_p95_icls, 0.95), 1) if p_p95_icls else 0.0,
                    }

            return {
                "source": "live_ollama",
                "total_requests": self._counter,
                "completed_requests": len(completed),
                "failed_requests": len(failed),
                "queued_requests": len(self.queue),
                "running_requests": 1 if self.active_request else 0,
                "deadline_attainment_pct": att_pct,
                "deadline_miss_rate_pct": miss_pct,
                "mean_wait_ms": round(sum(wait_times) / max(1, len(wait_times)), 1),
                "median_wait_ms": round(percentile(wait_times, 0.50), 1),
                "mean_ttfo_ms": round(sum(ttfos) / max(1, len(ttfos)), 1),
                "median_ttfo_ms": round(percentile(ttfos, 0.50), 1) if ttfos else 0.0,
                "mean_icl_ms": round(sum(icls) / max(1, len(icls)), 1),
                "mean_duration_ms": round(sum(durations) / max(1, len(durations)), 1),
                "median_duration_ms": round(percentile(durations, 0.50), 1) if durations else 0.0,
                "p95_wait_ms": round(percentile(wait_times, 0.95), 1),
                "p99_wait_ms": round(percentile(wait_times, 0.99), 1),
                "p95_ttfo_ms": round(percentile(ttfos, 0.95), 1) if ttfos else 0.0,
                "p99_ttfo_ms": round(percentile(ttfos, 0.99), 1) if ttfos else 0.0,
                "p95_icl_ms": round(percentile(icls, 0.95), 1) if icls else 0.0,
                "p99_icl_ms": round(percentile(icls, 0.99), 1) if icls else 0.0,
                "sample_counts": {
                    "total_submitted": self._counter,
                    "completed": len(completed),
                    "failed": len(failed),
                    "wait": len(wait_times),
                    "ttfo": len(ttfos),
                    "duration": len(durations),
                    "icl": len(icls),
                },
                "runs_by_policy": by_policy,
                "history": list(reversed(runs[-50:])),
            }

    def export_metrics_csv(self) -> str:
        """Exports live evaluation runs as a CSV string."""
        with self._lock:
            output = io.StringIO()
            cols = [
                "request_id", "arrival_index", "dispatch_index", "conversation_id", "model", "policy", "priority",
                "deadline_ms", "target_icl_ms", "status", "queue_wait_ms",
                "ttfo_ms", "average_icl_ms", "p95_icl_ms", "generation_duration_ms",
                "chunks", "characters", "deadline_missed", "source", "timestamp"
            ]
            writer = csv.DictWriter(output, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for r in self.completed_history:
                row = dict(r)
                row["source"] = "live_ollama"
                writer.writerow(row)
            return output.getvalue()

    def export_metrics_json(self) -> str:
        """Exports live evaluation data with full metadata as a JSON string."""
        with self._lock:
            data = {
                "source": "live_ollama",
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "metrics": self.get_metrics_summary(),
                "timeline": self.get_timeline(),
                "history": self.completed_history,
            }
            return json.dumps(data, indent=2)

    # ------------------------------------------------------------------------
    # Internal Worker Loop
    # ------------------------------------------------------------------------
    def _worker_loop(self):
        """Sequential single-inference worker."""
        while not self._stop:
            target_req: Optional[LiveRequest] = None
            with self._lock:
                while not self.queue and not self._stop:
                    self._wake_condition.wait(timeout=0.5)
                if self._stop:
                    break

                # Select next request according to policy
                policy_key = self.active_policy
                policy_inst = POLICIES.get(policy_key, POLICIES["fcfs"])
                now_perf = time.perf_counter()
                ranked = policy_inst.rank(self.queue, now_perf, context=self)
                target_req = ranked[0]
                self._dispatch_counter += 1
                target_req.dispatch_index = self._dispatch_counter
                self.queue.remove(target_req)
                target_req.status = "running"
                target_req.dispatch_perf = now_perf
                self.active_request = target_req

                wait_ms = (now_perf - target_req.arrival_perf) * 1000.0
                self._record_event("dispatched", {
                    "request_id": target_req.request_id,
                    "dispatch_index": target_req.dispatch_index,
                    "arrival_index": target_req.arrival_index,
                    "policy": target_req.policy,
                    "priority": target_req.priority,
                    "wait_ms": round(wait_ms, 1),
                })

            # Execute inference outside the lock to keep API responsive
            if target_req:
                self._execute_single_request(target_req)

            with self._lock:
                self.active_request = None
                self._wake_condition.notify_all()

    def _execute_single_request(self, req: LiveRequest):
        """Streams Ollama inference and captures real live latency metrics."""
        wait_ms = (req.dispatch_perf - req.arrival_perf) * 1000.0

        # Build message history for this conversation
        with self._lock:
            history = list(self.conversations.get(req.conversation_id, []))
        messages = history + [{"role": "user", "content": req.prompt}]

        chunks_received: List[Tuple[str, float]] = []
        full_text_acc: List[str] = []
        t_dispatch = time.perf_counter()

        def on_chunk(text_piece: str, arrival_perf: float):
            chunks_received.append((text_piece, arrival_perf))
            full_text_acc.append(text_piece)
            is_first = (len(chunks_received) == 1)
            ttfo_now = (arrival_perf - t_dispatch) * 1000.0 if is_first else None

            if is_first:
                self._record_event("first_token", {
                    "request_id": req.request_id,
                    "ttfo_ms": round(ttfo_now, 1) if ttfo_now is not None else 0.0,
                })

            # Calculate inter-chunk latency for this piece
            icl_now = None
            if len(chunks_received) > 1:
                icl_now = (arrival_perf - chunks_received[-2][1]) * 1000.0

            req.chunk_queue.put({
                "type": "chunk",
                "request_id": req.request_id,
                "text": text_piece,
                "first_chunk": is_first,
                "ttfo_ms": round(ttfo_now, 1) if ttfo_now is not None else None,
                "inter_chunk_latency_ms": round(icl_now, 1) if icl_now is not None else None,
            })

        try:
            raw_stats = self.ollama.stream_chat(
                model=req.model,
                messages=messages,
                chunk_callback=on_chunk,
            )
            t_finish = time.perf_counter()
            total_duration_ms = (t_finish - t_dispatch) * 1000.0
            full_text = "".join(full_text_acc)
            req.output_text = full_text
            req.character_count = len(full_text)
            req.chunk_count = len(chunks_received)
            req.completion_perf = t_finish

            # Calculate per-chunk latency samples (excluding chunk 0 which is TTFO)
            icls: List[float] = []
            ttfo_ms = None
            if chunks_received:
                ttfo_ms = (chunks_received[0][1] - t_dispatch) * 1000.0
                for i in range(1, len(chunks_received)):
                    diff_ms = (chunks_received[i][1] - chunks_received[i - 1][1]) * 1000.0
                    icls.append(diff_ms)

            req.icl_samples_ms = icls
            avg_icl = (sum(icls) / len(icls)) if icls else 0.0
            p95_icl = percentile(icls, 0.95)
            p99_icl = percentile(icls, 0.99)

            # Evaluate deadlines
            # 1. Overall request SLA: queue_wait + duration <= deadline_ms
            total_turnaround_ms = wait_ms + total_duration_ms
            missed_sla = total_turnaround_ms > req.deadline_ms
            # 2. First-token budget check
            missed_ttft = (ttfo_ms > req.ttft_budget_ms) if ttfo_ms is not None else False
            # 3. Inter-chunk violations count
            icl_violations = sum(1 for c in icls if c > req.target_icl_ms)

            metrics = {
                "queue_wait_ms": round(wait_ms, 1),
                "ttfo_ms": round(ttfo_ms, 1) if ttfo_ms is not None else 0.0,
                "ttft_budget_ms": req.ttft_budget_ms,
                "ttft_missed": missed_ttft,
                "average_icl_ms": round(avg_icl, 1),
                "p95_icl_ms": round(p95_icl, 1),
                "p99_icl_ms": round(p99_icl, 1),
                "target_icl_ms": req.target_icl_ms,
                "icl_violation_count": icl_violations,
                "total_chunks": len(chunks_received),
                "total_characters": len(full_text),
                "generation_duration_ms": round(total_duration_ms, 1),
                "turnaround_duration_ms": round(total_turnaround_ms, 1),
                "deadline_ms": req.deadline_ms,
                "deadline_missed": missed_sla,
                "ollama_eval_count": raw_stats.get("eval_count"),
                "ollama_eval_duration_ms": (raw_stats.get("eval_duration") or 0) / 1e6,
            }
            req.metrics = metrics
            req.status = "completed"

            # Update conversation history with assistant response
            with self._lock:
                conv = self.conversations.setdefault(req.conversation_id, [])
                conv.append({"role": "user", "content": req.prompt})
                conv.append({"role": "assistant", "content": full_text})
                # Update EWMA throughput estimator for Token Laxity
                if req.character_count > 0 and total_duration_ms > 0:
                    sec_per_char = (total_duration_ms / 1000.0) / req.character_count
                    self.ewma_sec_per_char = 0.3 * sec_per_char + 0.7 * self.ewma_sec_per_char

                self._record_history_entry(req, metrics)
                self._record_event("completed", {
                    "request_id": req.request_id,
                    "arrival_index": req.arrival_index,
                    "dispatch_index": req.dispatch_index,
                    "duration_ms": round(total_duration_ms, 1),
                    "turnaround_ms": round(total_turnaround_ms, 1),
                    "deadline_missed": missed_sla,
                })

            req.chunk_queue.put({
                "type": "done",
                "request_id": req.request_id,
                "metrics": metrics,
            })

        except Exception as exc:
            req.status = "failed"
            req.error_message = str(exc)
            metrics = {
                "queue_wait_ms": round(wait_ms, 1),
                "error": str(exc),
            }
            req.metrics = metrics
            with self._lock:
                self._record_history_entry(req, metrics)
                self._record_event("failed", {
                    "request_id": req.request_id,
                    "error": str(exc),
                })
            req.chunk_queue.put({
                "type": "error",
                "request_id": req.request_id,
                "message": str(exc),
                "metrics": metrics,
            })

    def _record_history_entry(self, req: LiveRequest, metrics: Dict[str, Any]):
        """Saves a run summary for live evaluation stats."""
        entry = {
            "request_id": req.request_id,
            "arrival_index": req.arrival_index,
            "dispatch_index": req.dispatch_index,
            "conversation_id": req.conversation_id,
            "model": req.model,
            "policy": req.policy,
            "priority": req.priority,
            "deadline_ms": req.deadline_ms,
            "target_icl_ms": req.target_icl_ms,
            "status": req.status,
            "queue_wait_ms": metrics.get("queue_wait_ms", 0.0),
            "ttfo_ms": metrics.get("ttfo_ms", 0.0),
            "average_icl_ms": metrics.get("average_icl_ms", 0.0),
            "p95_icl_ms": metrics.get("p95_icl_ms", 0.0),
            "generation_duration_ms": metrics.get("generation_duration_ms", 0.0),
            "chunks": req.chunk_count,
            "characters": req.character_count,
            "deadline_missed": metrics.get("deadline_missed", False),
            "prompt_preview": req.prompt[:60] + ("..." if len(req.prompt) > 60 else ""),
            "source": "live_ollama",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(req.arrival_time)),
        }
        self.completed_history.append(entry)
        if len(self.completed_history) > 200:
            self.completed_history.pop(0)

    def shutdown(self):
        """Stops the worker thread cleanly."""
        with self._lock:
            self._stop = True
            self._wake_condition.notify_all()
        self.worker_thread.join(timeout=2.0)
