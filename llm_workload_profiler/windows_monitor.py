"""
Windows Workload Monitor Module.

Part of Member 1: Real-Time Workload Profiling & Latency Measurement Subsystem.
Project: LLM-Aware OS Scheduler for Latency-Sensitive Local AI Inference.

Monitors system resources on Windows:
- Overall CPU utilization (%)
- Memory (RAM) utilization (%) and available MB
- Ollama process identification and per-process CPU/RAM usage
- GPU utilization check (via nvidia-smi if available, with graceful fallback to None)

IMPORTANT ARCHITECTURAL & OPERATING SYSTEM NOTE:
This module observes and collects resource utilization telemetries.
It does NOT alter Windows scheduling policies, thread priorities, or kernel quantums.
"""

from dataclasses import dataclass, asdict
import os
import shutil
import subprocess
from typing import Dict, List, Optional


@dataclass
class SystemMetrics:
    """Snapshot of system resource utilization."""
    cpu_percent: Optional[float]
    ram_percent: Optional[float]
    ram_available_mb: Optional[float]
    ollama_cpu_percent: Optional[float]
    ollama_ram_mb: Optional[float]
    ollama_process_count: int
    gpu_utilization_percent: Optional[float]
    gpu_memory_used_mb: Optional[float]
    monitoring_backend: str

    def to_dict(self) -> dict:
        return asdict(self)


class WindowsWorkloadMonitor:
    """Monitors Windows CPU, RAM, GPU, and Ollama background processes."""

    def __init__(self):
        self._has_psutil = False
        try:
            import psutil
            self._psutil = psutil
            self._has_psutil = True
        except ImportError:
            self._psutil = None

        # Check for nvidia-smi availability
        self._nvidia_smi_path = shutil.which("nvidia-smi")

    @property
    def has_psutil(self) -> bool:
        return self._has_psutil

    def get_ollama_processes(self) -> List[any]:
        """Finds any running Ollama processes on Windows."""
        if not self._has_psutil:
            return []

        ollama_procs = []
        try:
            for p in self._psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    name = p.info.get('name') or ''
                    if 'ollama' in name.lower():
                        ollama_procs.append(p)
                except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                    continue
        except Exception:
            pass
        return ollama_procs

    def get_gpu_metrics(self) -> Dict[str, Optional[float]]:
        """Queries nvidia-smi if installed. Returns None gracefully if unsupported/unavailable."""
        metrics = {
            "gpu_utilization_percent": None,
            "gpu_memory_used_mb": None,
        }
        if not self._nvidia_smi_path:
            return metrics

        try:
            cmd = [
                self._nvidia_smi_path,
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits"
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False
            )
            if result.returncode == 0 and result.stdout:
                line = result.stdout.strip().split("\n")[0]
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    metrics["gpu_utilization_percent"] = float(parts[0])
                    metrics["gpu_memory_used_mb"] = float(parts[1])
        except Exception:
            # Graceful fallback: hardware query failed or timed out
            pass

        return metrics

    def capture_snapshot(self) -> SystemMetrics:
        """Captures a point-in-time system resource snapshot."""
        if not self._has_psutil:
            gpu_res = self.get_gpu_metrics()
            return SystemMetrics(
                cpu_percent=None,
                ram_percent=None,
                ram_available_mb=None,
                ollama_cpu_percent=None,
                ollama_ram_mb=None,
                ollama_process_count=0,
                gpu_utilization_percent=gpu_res["gpu_utilization_percent"],
                gpu_memory_used_mb=gpu_res["gpu_memory_used_mb"],
                monitoring_backend="fallback (psutil not installed)",
            )

        try:
            cpu_pct = self._psutil.cpu_percent(interval=None)
            mem = self._psutil.virtual_memory()
            ram_pct = mem.percent
            ram_avail_mb = round(mem.available / (1024 * 1024), 1)

            # Query Ollama processes
            ollama_procs = self.get_ollama_processes()
            ollama_cpu = 0.0
            ollama_ram = 0.0
            for proc in ollama_procs:
                try:
                    ollama_cpu += proc.cpu_percent(interval=None)
                    ollama_ram += proc.memory_info().rss / (1024 * 1024)
                except Exception:
                    continue

            ollama_cpu_val = round(ollama_cpu, 1) if ollama_procs else None
            ollama_ram_val = round(ollama_ram, 1) if ollama_procs else None

            gpu_res = self.get_gpu_metrics()

            return SystemMetrics(
                cpu_percent=round(cpu_pct, 1),
                ram_percent=round(ram_pct, 1),
                ram_available_mb=ram_avail_mb,
                ollama_cpu_percent=ollama_cpu_val,
                ollama_ram_mb=ollama_ram_val,
                ollama_process_count=len(ollama_procs),
                gpu_utilization_percent=gpu_res["gpu_utilization_percent"],
                gpu_memory_used_mb=gpu_res["gpu_memory_used_mb"],
                monitoring_backend="psutil (Windows)",
            )
        except Exception as e:
            return SystemMetrics(
                cpu_percent=None,
                ram_percent=None,
                ram_available_mb=None,
                ollama_cpu_percent=None,
                ollama_ram_mb=None,
                ollama_process_count=0,
                gpu_utilization_percent=None,
                gpu_memory_used_mb=None,
                monitoring_backend=f"error: {str(e)}",
            )


if __name__ == "__main__":
    monitor = WindowsWorkloadMonitor()
    snap = monitor.capture_snapshot()
    print("--- Windows Workload Monitor Test ---")
    print(f"Backend:          {snap.monitoring_backend}")
    print(f"CPU Utilization:  {snap.cpu_percent}%")
    print(f"RAM Utilization:  {snap.ram_percent}% (Available: {snap.ram_available_mb} MB)")
    print(f"Ollama Processes: {snap.ollama_process_count}")
    print(f"Ollama CPU:       {snap.ollama_cpu_percent}%")
    print(f"Ollama RAM:       {snap.ollama_ram_mb} MB")
    print(f"GPU Utilization:  {snap.gpu_utilization_percent}")
    print(f"GPU Memory:       {snap.gpu_memory_used_mb} MB")
