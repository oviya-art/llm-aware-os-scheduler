"""
OPTIONAL real-OS actuator (experimental).

The simulator never touches real process priorities. This module is the bridge for
the "bonus" goal: apply the scheduler's urgency decisions to REAL processes using the
knobs the host OS actually offers.

  Windows : process priority classes via psutil (IDLE, BELOW_NORMAL, NORMAL,
            ABOVE_NORMAL, HIGH). REALTIME is deliberately never used.
  Linux   : nice values via psutil (os.setpriority). Raising priority (negative nice)
            needs root / CAP_SYS_NICE. SCHED_DEADLINE (sched_setattr) is NOT
            implemented - it needs root and a kernel-specific syscall.

SAFETY
  * dry_run=True by default: only RECORDS what it would do.
  * Original priorities are remembered and `restore_all()` puts them back
    (on Linux an unprivileged user cannot lower a nice value again, so demoting a
    process there is one-way unless you have privileges).
  * It never targets PIDs you did not explicitly hand it.

STATUS: Windows path is written against psutil's documented constants but was NOT
tested on a Windows machine. The Linux path and dry-run path are covered by tests.

Typical use (a governor loop driven by Member 1's live ITL measurements):

    act = OSActuator(dry_run=True)
    gov = LiveGovernor(act, inference_pid=ollama_pid, background_pids=[hog_pid])
    gov.step(observed_itl_ms=130.0, deadline_ms=100.0)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

try:
    import psutil
except ImportError:                      # psutil is optional for the simulator
    psutil = None


class Urgency(str, Enum):
    COMFORTABLE = "comfortable"
    URGENT = "urgent"
    CRITICAL = "critical"


def classify_urgency(norm_laxity: float, lambda_soft: float = 0.60, lambda_crit: float = 0.10) -> Urgency:
    """Same thresholds the Token-Laxity Scheduler uses for its preemption gate."""
    if norm_laxity < lambda_crit:
        return Urgency.CRITICAL
    if norm_laxity < lambda_soft:
        return Urgency.URGENT
    return Urgency.COMFORTABLE


def _windows_levels() -> Dict[str, Optional[int]]:
    if psutil is None or not sys.platform.startswith("win"):
        return {"low": None, "normal": None, "high": None}
    return {"low": psutil.BELOW_NORMAL_PRIORITY_CLASS,
            "normal": psutil.NORMAL_PRIORITY_CLASS,
            "high": psutil.ABOVE_NORMAL_PRIORITY_CLASS}


# Linux nice values for the three abstract levels.
_LINUX_NICE = {"low": 10, "normal": 0, "high": -5}


@dataclass
class ActuationRecord:
    pid: int
    level: str
    applied: bool
    dry_run: bool
    note: str = ""


class OSActuator:
    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.history: List[ActuationRecord] = []
        self._original: Dict[int, int] = {}

    def set_level(self, pid: int, level: str) -> ActuationRecord:
        """level in {'low','normal','high'}."""
        if level not in ("low", "normal", "high"):
            raise ValueError("level must be low/normal/high")
        if self.dry_run or psutil is None:
            rec = ActuationRecord(pid, level, False, self.dry_run,
                                  "dry run" if self.dry_run else "psutil not installed")
            self.history.append(rec)
            return rec
        try:
            proc = psutil.Process(pid)
            if pid not in self._original:
                self._original[pid] = proc.nice()
            if sys.platform.startswith("win"):
                target = _windows_levels()[level]
            else:
                target = _LINUX_NICE[level]
            proc.nice(target)
            rec = ActuationRecord(pid, level, True, False, f"set to {target}")
        except Exception as exc:         # AccessDenied, NoSuchProcess ...
            rec = ActuationRecord(pid, level, False, False, f"failed: {type(exc).__name__}: {exc}")
        self.history.append(rec)
        return rec

    def restore_all(self) -> None:
        if self.dry_run or psutil is None:
            return
        for pid, original in list(self._original.items()):
            try:
                psutil.Process(pid).nice(original)
            except Exception:
                pass
        self._original.clear()


@dataclass
class LiveGovernor:
    """Applies urgency to REAL processes from OBSERVED inter-token latency.

    slack_ratio = (deadline - observed_itl) / deadline
        < critical_slack  (default 0.0)  -> CRITICAL  (deadline already missed)
        < urgent_slack    (default 0.15) -> URGENT    (within 15% of the deadline)
        otherwise                        -> COMFORTABLE
    Boost the inference process (and demote background ones) on URGENT/CRITICAL;
    release only after `release_steps` consecutive COMFORTABLE observations
    (hysteresis, so priorities do not flap).
    """
    actuator: OSActuator
    inference_pid: int
    background_pids: List[int] = field(default_factory=list)
    release_steps: int = 5
    urgent_slack: float = 0.15
    critical_slack: float = 0.0
    _comfortable_streak: int = 0
    _boosted: bool = False

    def classify(self, observed_itl_ms: float, deadline_ms: float) -> Urgency:
        slack_ratio = (deadline_ms - observed_itl_ms) / max(deadline_ms, 1.0)
        if slack_ratio < self.critical_slack:
            return Urgency.CRITICAL
        if slack_ratio < self.urgent_slack:
            return Urgency.URGENT
        return Urgency.COMFORTABLE

    def step(self, observed_itl_ms: float, deadline_ms: float) -> Urgency:
        urgency = self.classify(observed_itl_ms, deadline_ms)
        if urgency != Urgency.COMFORTABLE:
            self._comfortable_streak = 0
            if not self._boosted:
                self.actuator.set_level(self.inference_pid, "high")
                for pid in self.background_pids:
                    self.actuator.set_level(pid, "low")
                self._boosted = True
        else:
            self._comfortable_streak += 1
            if self._boosted and self._comfortable_streak >= self.release_steps:
                self.actuator.set_level(self.inference_pid, "normal")
                for pid in self.background_pids:
                    self.actuator.set_level(pid, "normal")
                self._boosted = False
        return urgency
