"""
Background CPU-bound workload generator.

Part of Member 2: Core Scheduling Algorithm.

Member 1's traces only name a contention *scenario* (light/heavy/mixed); they do not
contain the competing processes themselves. The scheduler needs real competitors to
decide between, so this module creates CPU-bound jobs (compression, indexing,
builds ...) that share the cores with the inference requests.

Scenario names mirror Member 1's `mixed_workload.LoadScenario`:

    none        no background jobs
    light_load  ~10% of the machine, short jobs
    mixed_load  ~30% on average but BURSTY (sinusoidally modulated arrival rate)
    heavy_load  ~50% of the machine, long jobs

Jobs arrive as a Poisson process whose rate is chosen so that the *average* CPU
demand equals `fraction * num_cores`. Burst lengths are log-normal. Everything is
seeded and deterministic.
"""

from __future__ import annotations

import json
import math
import random
from typing import Dict, List

from task_model import Task, TaskKind

SCENARIOS: Dict[str, dict] = {
    "none":       {"fraction": 0.00, "median_burst_ms": 0.0,    "sigma": 0.0, "bursty": False},
    "light_load": {"fraction": 0.10, "median_burst_ms": 400.0,  "sigma": 0.5, "bursty": False},
    "mixed_load": {"fraction": 0.30, "median_burst_ms": 1500.0, "sigma": 0.6, "bursty": True},
    "heavy_load": {"fraction": 0.50, "median_burst_ms": 4000.0, "sigma": 0.5, "bursty": False},
}


def generate_background(
    scenario: str,
    num_cores: int,
    horizon_ms: float,
    seed: int = 42,
    fraction_override: float = None,
) -> List[Task]:
    """Create CPU-bound background tasks covering [0, horizon_ms]."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown background scenario '{scenario}'. Choose from {list(SCENARIOS)}")
    cfg = SCENARIOS[scenario]
    fraction = cfg["fraction"] if fraction_override is None else fraction_override
    if fraction <= 0 or horizon_ms <= 0:
        return []

    rng = random.Random(seed * 7919 + 13)
    mu = math.log(cfg["median_burst_ms"])
    sigma = cfg["sigma"]
    mean_burst = math.exp(mu + sigma * sigma / 2.0)
    base_rate_per_ms = (fraction * num_cores) / mean_burst        # jobs per ms

    period_ms = max(4000.0, horizon_ms / 3.0)
    peak = 1.8 if cfg["bursty"] else 1.0                          # thinning upper bound

    tasks: List[Task] = []
    t = 0.0
    idx = 0
    while True:
        t += rng.expovariate(base_rate_per_ms * peak)
        if t >= horizon_ms:
            break
        if cfg["bursty"]:
            rate_now = 1.0 + 0.8 * math.sin(2 * math.pi * t / period_ms)
            if rng.random() > rate_now / peak:                     # thinning
                continue
        burst = max(50.0, rng.lognormvariate(mu, sigma))
        idx += 1
        tasks.append(Task(
            f"bg_{idx:03d}", TaskKind.BACKGROUND, t, [burst],
            request_type="background", priority_label="normal",
            category=scenario, nice=0, source="generated",
        ))

    if not tasks:                                                  # always give a non-empty scenario one job
        tasks.append(Task("bg_001", TaskKind.BACKGROUND, 0.0, [cfg["median_burst_ms"]],
                          request_type="background", category=scenario, source="generated"))
    return tasks


def load_background_json(path: str) -> List[Task]:
    """Load custom background jobs.

    Format: {"background_tasks": [{"id": "bg1", "arrival_s": 0.5, "burst_ms": 3000, "nice": 0}, ...]}
    """
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    items = payload["background_tasks"] if isinstance(payload, dict) else payload
    tasks = []
    for i, it in enumerate(items, 1):
        tasks.append(Task(
            str(it.get("id", f"bg_{i:03d}")), TaskKind.BACKGROUND,
            float(it.get("arrival_s", 0.0)) * 1000.0, [float(it["burst_ms"])],
            request_type="background", priority_label=str(it.get("priority", "normal")),
            nice=int(it.get("nice", 0)), category="custom", source=path,
        ))
    return tasks
