"""System and Prometheus-backed metric helpers for EdgePilot.

Provides four primary helpers:
- gather_metrics: local snapshot via psutil
- report_edge_status: short fact summary (prefers Prometheus, falls back to local)
- evaluate_capacity: check if a workload can run now
- suggest_capacity_window: suggest when to run based on current/off-peak
"""

from __future__ import annotations
from .kubernetes_capacity import LocalMetricsProvider, MetricsProvider

import json
import math
import os
import platform
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx
import psutil

PROM_URL = os.getenv("PROM_URL")
PROM_TIMEOUT = float(os.getenv("PROM_TIMEOUT_SEC", "15"))
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"

# ============================================================================
# Prometheus helpers
# ============================================================================


class PrometheusUnavailable(RuntimeError):
    """Raised when Prometheus metrics cannot be fetched."""


class PrometheusClient:
    """Lightweight helper for synchronous Prometheus queries."""

    def __init__(self, base_url: Optional[str] = None, *, timeout: float = PROM_TIMEOUT) -> None:
        self.base_url = base_url
        self.timeout = timeout

    @property
    def available(self) -> bool:
        if self.base_url:
            return True
        env_url = os.getenv("PROM_URL")
        if env_url:
            self.base_url = env_url
            return True
        return False

    def _require_url(self) -> str:
        if not self.base_url:
            env_url = os.getenv("PROM_URL")
            if env_url:
                self.base_url = env_url
        if not self.base_url:
            raise PrometheusUnavailable("PROM_URL environment variable is not configured.")
        return self.base_url

    def query(self, promql: str) -> List[Dict[str, Any]]:
        url = f"{self._require_url().rstrip('/')}/api/v1/query"
        try:
            response = httpx.get(url, params={"query": promql}, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise PrometheusUnavailable(f"Prometheus request failed: {exc}") from exc
        if data.get("status") != "success":
            raise PrometheusUnavailable(f"Prometheus returned non-success status: {data}")
        return data["data"]["result"]

    def fetch_scalar_map(self, query: str, *, label: str = "instance") -> Dict[str, float]:
        result = self.query(query)
        output: Dict[str, float] = {}
        for row in result:
            metric = row.get("metric", {})
            key = metric.get(label)
            if not key:
                continue
            try:
                value = float(row["value"][1])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value):
                output[key] = value
        return output


PROM = PrometheusClient(PROM_URL)
LOCAL = LocalMetricsProvider()


def _avg_vector(result: Iterable[Dict[str, Any]]) -> float:
    values: List[float] = []
    for row in result:
        raw_value = row.get("value", [None, None])[1]
        try:
            candidate = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(candidate):
            values.append(candidate)
    return (sum(values) / len(values)) if values else float("nan")




# ============================================================================
# Local metrics helpers
# ============================================================================


def _battery_info() -> Dict[str, float | bool | None]:
    battery = psutil.sensors_battery()
    if not battery:
        return {"available": False}
    return {
        "available": True,
        "percent": battery.percent,
        "secs_left": battery.secsleft,
        "power_plugged": battery.power_plugged,
    }



def _process_snapshot(limit: Optional[int] = 10) -> List[Dict[str, float | int | str]]:
    procs = []
    for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent", "memory_info"]):
        with proc.oneshot():
            info = proc.info
            rss = info["memory_info"].rss if info.get("memory_info") else 0
            cpu_pct = info.get("cpu_percent") or 0.0
            procs.append(
                {
                    "pid": info["pid"],
                    "name": info.get("name") or "unknown",
                    "cpu_percent": float(cpu_pct),
                    "rss_bytes": rss,
                }
            )
    procs.sort(key=lambda item: item["cpu_percent"], reverse=True)
    if limit is None or limit <= 0:
        return procs
    return procs[:limit]


_metrics_cache = {"ts": 0, "data": {}, "raw": {}}

def gather_metrics(top_n: int = 10, all_processes: bool = False) -> Dict[str, Any]:
    """Collect local host metrics via psutil with a 1-second TTL cache."""
    global _metrics_cache
    now = time.time()
    
    # Return cached data if it's less than 1 second old and args match
    if now - _metrics_cache.get("ts", 0) < 1.0 and _metrics_cache.get("data"):
        if _metrics_cache.get("args") == (top_n, all_processes):
            return _metrics_cache["data"]
        
    # Use interval=None to instantly return the average CPU usage 
    # since the last time this function was called, avoiding micro-spikes and blocking.
    cpu_percent = psutil.cpu_percent(interval=None)
    virtual_mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk_io = psutil.disk_io_counters()
    disk_usage = psutil.disk_usage("/")
    net = psutil.net_io_counters()

    # Calculate per-second rates
    prev_ts = _metrics_cache.get("ts", 0)
    prev_raw = _metrics_cache.get("raw", {})
    
    dt = now - prev_ts if prev_ts else 1.0
    if dt <= 0:
        dt = 1.0

    cur_disk_read = disk_io.read_bytes if disk_io else 0
    cur_disk_write = disk_io.write_bytes if disk_io else 0
    cur_net_sent = net.bytes_sent if net else 0
    cur_net_recv = net.bytes_recv if net else 0

    disk_read_rate = max(0, (cur_disk_read - prev_raw.get("disk_read", cur_disk_read)) / dt)
    disk_write_rate = max(0, (cur_disk_write - prev_raw.get("disk_write", cur_disk_write)) / dt)
    net_sent_rate = max(0, (cur_net_sent - prev_raw.get("net_sent", cur_net_sent)) / dt)
    net_recv_rate = max(0, (cur_net_recv - prev_raw.get("net_recv", cur_net_recv)) / dt)

    _metrics_cache["raw"] = {
        "disk_read": cur_disk_read,
        "disk_write": cur_disk_write,
        "net_sent": cur_net_sent,
        "net_recv": cur_net_recv
    }

    metrics = {
        "ts": time.time(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python_version": platform.python_version(),
        },
        "cpu": {
            "percent": cpu_percent,
            "cores_logical": psutil.cpu_count(),
            "cores_physical": psutil.cpu_count(logical=False),
        },
        "memory": {
            "total": virtual_mem.total,
            "used": virtual_mem.used,
            "available": virtual_mem.available,
            "percent": virtual_mem.percent,
            "swap_total": swap.total,
            "swap_used": swap.used,
        },
        "disk": {
            "read_bytes": disk_read_rate,
            "write_bytes": disk_write_rate,
        },
        "filesystem": {
            "mount": "/",
            "total": disk_usage.total,
            "used": disk_usage.used,
            "free": disk_usage.free,
            "percent": disk_usage.percent,
        },
        "network": {
            "bytes_sent": net_sent_rate,
            "bytes_recv": net_recv_rate,
        },
        "battery": _battery_info(),
        "gpu": {"available": False},  # psutil has no GPU support; placeholder
        "top_processes": _process_snapshot(None if all_processes else top_n),
    }
    _metrics_cache["ts"] = now
    _metrics_cache["data"] = metrics
    _metrics_cache["args"] = (top_n, all_processes)
    return metrics


# ============================================================================
# Public APIs
# ============================================================================


def report_edge_status(window: str = "1h", top_k: int = 5, *, client: PrometheusClient | None = None) -> Dict[str, Any]:
    """Return edge status facts using Prometheus if available, otherwise local snapshot."""
    client = client or PROM
    facts: List[str] = []
    source = "prometheus"

    if client.available:
        try:
            cpu_busy = client.fetch_scalar_map(
                '100 - (100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m]))))'
            )
            # macOS node_exporter doesn't expose MemAvailable; fall back to free_bytes
            mem_free = client.fetch_scalar_map("node_memory_MemAvailable_bytes")
            if not mem_free:
                mem_free = client.fetch_scalar_map("node_memory_free_bytes")
            mem_total = client.fetch_scalar_map("node_memory_total_bytes")
            disk_free = client.fetch_scalar_map(
                'max by (instance) (node_filesystem_free_bytes{fstype!~"tmpfs|overlay"})'
            )
            # Include top processes only if a process exporter is present
            top_procs = client.query('topk({k}, rate(process_cpu_seconds_total[5m]))'.format(k=top_k))
            instances = sorted(set(cpu_busy) | set(mem_free) | set(disk_free))
            for inst in instances[:top_k]:
                cpu = cpu_busy.get(inst)
                mem = mem_free.get(inst)
                mem_t = mem_total.get(inst)
                disk = disk_free.get(inst)
                parts = []
                if cpu is not None:
                    parts.append(f"CPU {cpu:.1f}%")
                if mem is not None:
                    parts.append(f"MemFree {int(mem)} bytes")
                if mem_t is not None:
                    parts.append(f"MemTotal {int(mem_t)} bytes")
                if disk is not None:
                    parts.append(f"DiskFree {int(disk)} bytes")
                if parts:
                    facts.append(f"{inst}: " + ", ".join(parts))
            # Append a simple top-k process snapshot if available
            for row in top_procs[:top_k]:
                metric = row.get("metric", {})
                proc = metric.get("process") or metric.get("name") or metric.get("cmdline") or "proc"
                inst_label = metric.get("instance") or ""
                try:
                    cpu_pct = float(row["value"][1]) * 100
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(cpu_pct):
                    label = f"{proc} ({cpu_pct:.1f}% CPU)"
                    if inst_label:
                        label = f"{inst_label}: {label}"
                    facts.append(label)
        except PrometheusUnavailable:
            facts = []

    if not facts:
        return {"status": "no_data", "window": window, "top_k": top_k, "facts": [], "source": "none"}

    return {"status": "ok", "window": window, "top_k": top_k, "facts": facts, "source": source}


def evaluate_capacity(
    requirements: Dict[str, float],
    *,
    duration: str = "45m",
    host: Optional[str] = None,
    client: PrometheusClient | None = None,
    local_provider: MetricsProvider | None = None,
) -> Dict[str, Any]:
    """Determine whether the workload can run now."""
    client = client or PROM
    local_provider = local_provider or LOCAL

    results: List[Dict[str, Any]] = []
    source = "prometheus" if client.available else "local"

    need_cpu = float(requirements.get("cpu_pct", 0) or 0)
    need_mem = float(requirements.get("mem_bytes", 0) or 0)
    need_disk = float(requirements.get("disk_free_bytes", 0) or 0)

    if client.available:
        try:
            cpu_headroom = client.fetch_scalar_map(
                '100 - (100 * (1 - avg by (instance) '
                '(rate(node_cpu_seconds_total{mode="idle"}[5m]))))'
            )
            mem_free = client.fetch_scalar_map(
                "node_memory_MemAvailable_bytes"
            )
            disk_free = client.fetch_scalar_map(
                'max by (instance) '
                '(node_filesystem_free_bytes{fstype!~"tmpfs|overlay"})'
            )

            instances = (
                [host]
                if host
                else sorted(
                    set(cpu_headroom)
                    | set(mem_free)
                    | set(disk_free)
                )
            )

            for instance in instances:
                reasons: List[str] = []

                cpu_now = cpu_headroom.get(instance, 0.0)
                mem_now = mem_free.get(instance, 0.0)
                disk_now = disk_free.get(instance, 0.0)

                can_run = True

                if cpu_now < need_cpu:
                    can_run = False
                    reasons.append(
                        f"CPU headroom {cpu_now:.1f}% "
                        f"< need {need_cpu:.1f}%"
                    )

                if mem_now < need_mem:
                    can_run = False
                    reasons.append(
                        f"Mem free {int(mem_now)} "
                        f"< need {int(need_mem)}"
                    )

                if disk_now < need_disk:
                    can_run = False
                    reasons.append(
                        f"Disk free {int(disk_now)} "
                        f"< need {int(need_disk)}"
                    )

                results.append(
                    {
                        "instance": instance or host or "unknown",
                        "can_run_now": can_run,
                        "reasons": reasons,
                        "headroom_now": {
                            "cpu_pct": cpu_now,
                            "mem_bytes": mem_now,
                            "disk_free_bytes": disk_now,
                        },
                        "requested": {
                            "duration": duration,
                            "cpu_pct": need_cpu,
                            "mem_bytes": need_mem,
                            "disk_free_bytes": need_disk,
                        },
                    }
                )

        except PrometheusUnavailable:
            results = []
            source = "local"

    if not results:
        snapshot = local_provider.gather_metrics()
        hostname = host or socket.gethostname()

        cpu_headroom = max(
            0.0,
            100.0 - snapshot["cpu"]["percent"],
        )
        mem_available = snapshot["memory"]["available"]
        disk_free = snapshot["filesystem"]["free"]

        reasons: List[str] = []
        can_run = True

        if cpu_headroom < need_cpu:
            can_run = False
            reasons.append(
                f"CPU headroom {cpu_headroom:.1f}% "
                f"< need {need_cpu:.1f}%"
            )

        if mem_available < need_mem:
            can_run = False
            reasons.append(
                f"Mem free {int(mem_available)} "
                f"< need {int(need_mem)}"
            )

        if disk_free < need_disk:
            can_run = False
            reasons.append(
                f"Disk free {int(disk_free)} "
                f"< need {int(need_disk)}"
            )

        results.append(
            {
                "instance": hostname,
                "can_run_now": can_run,
                "reasons": reasons,
                "headroom_now": {
                    "cpu_pct": cpu_headroom,
                    "mem_bytes": mem_available,
                    "disk_free_bytes": disk_free,
                },
                "requested": {
                    "duration": duration,
                    "cpu_pct": need_cpu,
                    "mem_bytes": need_mem,
                    "disk_free_bytes": need_disk,
                },
            }
        )

        source = "local"

    return {
        "status": "ok",
        "results": results,
        "source": source,
    }

def _offpeak_local_iso() -> str:
    """Return the next local off-peak time (1:00 AM)."""
    now = datetime.now()
    offpeak = now.replace(hour=1, minute=0, second=0, microsecond=0)

    if offpeak <= now:
        offpeak += timedelta(days=1)

    return offpeak.isoformat()

def suggest_capacity_window(
    requirements: Dict[str, float],
    *,
    duration: str = "45m",
    horizon_hours: int = 24,
    host: Optional[str] = None,
    client: PrometheusClient | None = None,
    local_provider: MetricsProvider | None = None,
) -> Dict[str, Any]:
    """Suggest execution windows based on recent utilization."""
    client = client or PROM
    local_provider = local_provider or LOCAL

    source = "prometheus" if client.available else "local"

    need_cpu = float(requirements.get("cpu_pct", 0) or 0)
    need_mem = float(requirements.get("mem_bytes", 0) or 0)
    need_disk = float(
        requirements.get("disk_free_bytes", 0) or 0
    )

    results: List[Dict[str, Any]] = []

    if client.available:
        try:
            cpu_busy = client.fetch_scalar_map(
                "quantile_over_time("
                "0.95, "
                "(100 * (1 - avg by (instance) "
                '(rate(node_cpu_seconds_total{mode="idle"}[5m]))))'
                "[1h:1m]"
                ")"
            )

            mem_p05 = client.fetch_scalar_map(
                "quantile_over_time("
                "0.05, "
                "node_memory_MemAvailable_bytes[1h]"
                ")"
            )

            disk_p05 = client.fetch_scalar_map(
                "quantile_over_time("
                "0.05, "
                'node_filesystem_free_bytes{fstype!~"tmpfs|overlay"}'
                "[1h]"
                ")"
            )

            cpu_headroom = {
                name: max(0.0, 100.0 - value)
                for name, value in cpu_busy.items()
            }

            instances = (
                [host]
                if host
                else sorted(
                    set(cpu_headroom)
                    | set(mem_p05)
                    | set(disk_p05)
                )
            )

            for instance in instances:
                windows: List[Dict[str, Any]] = []

                cpu_now = cpu_headroom.get(instance, 0.0)
                mem_now = mem_p05.get(instance, 0.0)
                disk_now = disk_p05.get(instance, 0.0)

                if (
                    cpu_now >= need_cpu
                    and mem_now >= need_mem
                    and disk_now >= need_disk
                ):
                    windows.append(
                        {
                            "start": "now",
                            "duration": duration,
                            "reason": "p95 headroom sufficient",
                        }
                    )

                windows.append(
                    {
                        "start": _offpeak_local_iso(),
                        "duration": duration,
                        "reason": (
                            "typical off-peak "
                            "(1–4am local)"
                        ),
                    }
                )

                results.append(
                    {
                        "instance": (
                            instance or host or "unknown"
                        ),
                        "windows": windows,
                    }
                )

        except PrometheusUnavailable:
            results = []
            source = "local"

    if not results:
        snapshot = local_provider.gather_metrics()
        hostname = host or socket.gethostname()

        cpu_headroom = max(
            0.0,
            100.0 - snapshot["cpu"]["percent"],
        )
        mem_available = snapshot["memory"]["available"]
        disk_free = snapshot["filesystem"]["free"]

        windows: List[Dict[str, Any]] = []

        if (
            cpu_headroom >= need_cpu
            and mem_available >= need_mem
            and disk_free >= need_disk
        ):
            windows.append(
                {
                    "start": "now",
                    "duration": duration,
                    "reason": "current headroom sufficient",
                }
            )

        windows.append(
            {
                "start": _offpeak_local_iso(),
                "duration": duration,
                "reason": (
                    "local fallback off-peak estimate"
                ),
            }
        )

        results.append(
            {
                "instance": hostname,
                "windows": windows,
            }
        )

        source = "local"

    return {
        "status": "ok",
        "results": results,
        "source": source,
        "horizon_hours": horizon_hours,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EdgePilot metrics snapshot")
    parser.add_argument("--top-n", type=int, default=10, help="Number of processes to include")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    data = gather_metrics(top_n=args.top_n)
    indent = 2 if args.pretty else None
    print(json.dumps(data, indent=indent))
