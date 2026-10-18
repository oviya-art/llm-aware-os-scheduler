"""Dashboard server: serves the static pages, runs simulation workloads,
and provides live Ollama chat with application-level request scheduling.

    python server.py            ->  http://localhost:8000
    GET  /api/health
    GET  /api/run               (simulation workloads)
    GET  /api/ollama/models     (installed Ollama models)
    POST /api/live/submit       (queue a live inference request)
    GET  /api/live/stream/<id>  (Server-Sent Events streaming chunks)
    GET  /api/live/queue        (queue inspection)
    GET  /api/live/metrics      (real-workload evaluation metrics)
"""
import json
import os
import queue
import sys
import tempfile
import time

from flask import Flask, Response, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from benchmark.common import SCENARIOS, build_workload   # noqa: E402
from benchmark.replay import build_replay                # noqa: E402
from live_scheduler import LiveSchedulerController, OllamaService, POLICIES  # noqa: E402

DASH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")
app = Flask(__name__, static_folder=DASH, static_url_path="")

# Instantiate the live application scheduler controller
live_controller = LiveSchedulerController()


def _num(name, default, lo, hi, cast=float):
    try:
        v = cast(request.args.get(name, default))
    except ValueError:
        raise ValueError(f"{name} must be a number")
    if not lo <= v <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return v


# ============================================================================
# Existing Static & Simulation Routes (Preserved Exactly)
# ============================================================================

@app.route("/")
def index():
    return send_from_directory(DASH, "index.html")


@app.route("/api/health")
def health():
    return jsonify(
        ok=True,
        scenarios=list(SCENARIOS),
        ollama_connected=live_controller.ollama.check_health(),
        ollama_url=live_controller.ollama.base_url,
        active_policy=live_controller.active_policy,
        supported_policies=list(POLICIES.keys()),
    )


@app.route("/api/run")
def run():
    key = request.args.get("scenario")
    if key:                                   # reproduce a benchmark scenario exactly (any seed)
        if key not in SCENARIOS:
            return jsonify(error="unknown scenario"), 400
        try:
            seed = _num("seed", 1, 1, 9999, int)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        sc = SCENARIOS[key]
        with tempfile.TemporaryDirectory() as d:
            w = build_workload(sc, seed, d, tag=f"{key}_s{seed}")
            rp = build_replay(w, sc.cores)
        return jsonify(rp)
    try:
        cores = _num("cores", 4, 1, 16, int)
        n = _num("requests", 10, 2, 30, int)
        rps = _num("rps", 1.2, 0.2, 6.0)
        seed = _num("seed", 7, 1, 999, int)
        bg = request.args.get("background", "mixed_load")
        if bg not in ("none", "light_load", "mixed_load", "heavy_load"):
            raise ValueError("background must be none, light_load, mixed_load or heavy_load")
    except ValueError as e:
        return jsonify(error=str(e)), 400
    m1 = {"none": "light_load", "light_load": "light_load", "mixed_load": "mixed_load", "heavy_load": "heavy_load"}[bg]
    base = SCENARIOS["mixed"]
    with tempfile.TemporaryDirectory() as d:
        w = build_workload(base, seed, d, cores=cores, requests=n, arrival_rps=rps, background=bg,
                           m1_scenario=m1, tag=f"custom_s{seed}")
        rp = build_replay(w, cores)
    rp["meta"].update(title="Custom run", scenario="custom", background=bg, blurb="Custom workload")
    return jsonify(rp)


# ============================================================================
# Phase 2 & 4: Live Ollama Chat & Workload Scheduling API Routes
# ============================================================================

@app.route("/api/ollama/models")
def ollama_models():
    """Returns list of installed models discovered via local Ollama API."""
    connected = live_controller.ollama.check_health()
    models = live_controller.ollama.list_models() if connected else []
    return jsonify({
        "connected": connected,
        "url": live_controller.ollama.base_url,
        "models": models,
    })


@app.route("/api/ollama/status")
def ollama_status():
    """Checks reachability of the local Ollama instance."""
    connected = live_controller.ollama.check_health()
    return jsonify({
        "connected": connected,
        "url": live_controller.ollama.base_url,
    })


@app.route("/api/live/submit", methods=["POST"])
def live_submit():
    """Enqueues a real chat inference request to the application scheduler."""
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "")
    model = data.get("model", "")
    policy = data.get("policy")
    priority = data.get("priority", "normal")
    deadline_ms = data.get("deadline_ms", 5000.0)
    ttft_budget_ms = data.get("ttft_budget_ms", 1000.0)
    target_icl_ms = data.get("target_icl_ms", 80.0)
    conversation_id = data.get("conversation_id")

    if not prompt or not str(prompt).strip():
        return jsonify(error="prompt cannot be empty"), 400
    if not model or not str(model).strip():
        return jsonify(error="model cannot be empty"), 400

    try:
        req = live_controller.submit_request(
            prompt=str(prompt),
            model=str(model),
            policy=str(policy) if policy else None,
            priority=str(priority),
            deadline_ms=float(deadline_ms),
            ttft_budget_ms=float(ttft_budget_ms),
            target_icl_ms=float(target_icl_ms),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
    except ValueError as ve:
        return jsonify(error=str(ve)), 400

    q_status = live_controller.get_queue_status()
    # Find position
    pos = next((i["queue_position"] for i in q_status["queue"] if i["request_id"] == req.request_id), 1)

    return jsonify({
        "ok": True,
        "request_id": req.request_id,
        "conversation_id": req.conversation_id,
        "queue_position": pos,
        "status": req.status,
    })


@app.route("/api/live/stream/<request_id>")
def live_stream(request_id):
    """Streams live tokens and real-time latency metrics via Server-Sent Events."""
    req_obj = live_controller.get_request(request_id)
    if not req_obj:
        return jsonify(error="Request not found"), 404

    def event_stream():
        # First event: initial status
        yield f"data: {json.dumps({'type': 'status', 'status': req_obj.status})}\n\n"
        while True:
            try:
                item = req_obj.chunk_queue.get(timeout=25.0)
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") in ("done", "error", "cancelled"):
                    break
            except queue.Empty:
                if req_obj.status in ("completed", "failed", "cancelled"):
                    break
                # Keep-alive SSE comment
                yield ": keepalive\n\n"

    response = Response(event_stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@app.route("/api/live/status/<request_id>")
def live_status(request_id):
    """Returns the current processing status and metrics for a specific request."""
    req_obj = live_controller.get_request(request_id)
    if not req_obj:
        return jsonify(error="Request not found"), 404
    q_status = live_controller.get_queue_status()
    pos = next((i["queue_position"] for i in q_status["queue"] if i["request_id"] == req_obj.request_id), 0)
    return jsonify(req_obj.to_summary(queue_position=pos))


@app.route("/api/live/queue")
def live_queue():
    """Provides a live snapshot of queued and actively executing requests."""
    return jsonify(live_controller.get_queue_status())


@app.route("/api/live/policy", methods=["POST"])
def live_policy():
    """Updates the active scheduler dispatch policy."""
    data = request.get_json(silent=True) or {}
    new_policy = data.get("policy")
    if not new_policy or new_policy not in POLICIES:
        return jsonify(error=f"Invalid policy '{new_policy}'. Supported: {list(POLICIES.keys())}"), 400
    live_controller.set_policy(new_policy)
    return jsonify(ok=True, active_policy=new_policy)


@app.route("/api/live/timeline")
def live_timeline():
    """Provides dynamic timeline items and scheduler events for Live Arena."""
    return jsonify(live_controller.get_timeline())


@app.route("/api/live/batch", methods=["POST"])
def live_batch():
    """Submits a burst of benchmark prompts to quickly demonstrate queue reordering."""
    data = request.get_json(silent=True) or {}
    prompts = data.get("prompts")
    model = data.get("model")
    policy = data.get("policy")
    try:
        results = live_controller.submit_batch(prompts=prompts, model=model, policy=policy)
        return jsonify({
            "ok": True,
            "submitted_count": len(results),
            "requests": results,
            "active_policy": live_controller.active_policy,
        })
    except Exception as e:
        return jsonify(error=str(e)), 400


@app.route("/api/live/metrics")
def live_metrics():
    """Returns aggregate performance and deadline attainment evaluation metrics."""
    return jsonify(live_controller.get_metrics_summary())


@app.route("/api/live/export")
def live_export():
    """Exports live evaluation data in CSV or JSON format."""
    fmt = request.args.get("format", "csv").lower()
    if fmt == "json":
        json_data = live_controller.export_metrics_json()
        return Response(
            json_data,
            mimetype="application/json",
            headers={"Content-Disposition": "attachment;filename=live_chat_eval_metrics.json"}
        )
    csv_data = live_controller.export_metrics_csv()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=live_chat_eval_metrics.csv"}
    )


@app.route("/api/live/cancel/<request_id>", methods=["POST"])
def live_cancel(request_id):
    """Cancels a request if it is still in the queue."""
    cancelled = live_controller.cancel_request(request_id)
    return jsonify(ok=cancelled)


@app.route("/api/live/clear", methods=["POST"])
def live_clear():
    """Clears conversation history for the specified conversation ID."""
    data = request.get_json(silent=True) or {}
    cid = data.get("conversation_id")
    if cid:
        live_controller.clear_conversation(cid)
    return jsonify(ok=True)


@app.route("/api/live/history/<conversation_id>")
def live_history(conversation_id):
    """Retrieves message history for a conversation."""
    hist = live_controller.get_conversation_history(conversation_id)
    return jsonify(history=hist)


if __name__ == "__main__":
    print("Dashboard on http://localhost:8000")
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
