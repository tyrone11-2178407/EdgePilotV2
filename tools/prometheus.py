import os
import requests
import logging
from typing import Any, Dict, List, Optional, Sequence
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Assumes user runs `kubectl port-forward svc/prometheus-kube-prometheus-prometheus 9090:9090 -n monitoring`
#
# PROM_URL is checked first because scripts/bootstrap_prometheus.sh writes
# that name into env/.env, and tools/metrics.py's PrometheusClient already
# reads it. PROMETHEUS_URL is kept as a fallback so existing setups using
# it keep working.
PROMETHEUS_URL = (
    os.getenv("PROM_URL")
    or os.getenv("PROMETHEUS_URL")
    or "http://localhost:9090"
)
USE_MOCK = os.getenv("PROMETHEUS_MOCK", "false").lower() == "true"

# A workflow step feeds these results straight into the model's context, so
# an unbounded series is both a cost and a comprehension problem.
DEFAULT_MAX_POINTS = 20


def _summarize_series(values: Sequence[Sequence[Any]]) -> Dict[str, Any]:
    """Reduce a Prometheus value series to the statistics that matter."""

    numbers: List[float] = []

    for point in values:
        try:
            numbers.append(float(point[1]))
        except (IndexError, TypeError, ValueError):
            continue

    if not numbers:
        return {"points": 0}

    return {
        "points": len(numbers),
        "min": min(numbers),
        "max": max(numbers),
        "mean": sum(numbers) / len(numbers),
        "last": numbers[-1],
    }


def _downsample(values: Sequence[Any], max_points: int) -> List[Any]:
    """Evenly thin a series, always keeping the first and last points."""

    if max_points <= 0 or len(values) <= max_points:
        return list(values)

    step = (len(values) - 1) / (max_points - 1)
    picked = [values[round(i * step)] for i in range(max_points)]
    picked[-1] = values[-1]

    return picked

def _finalize(
    results: List[Dict[str, Any]],
    summarize: bool,
    max_points: int,
) -> List[Dict[str, Any]]:
    """Attach per-series statistics and cap the raw point count."""

    if not summarize:
        return results

    finalized = []

    for series in results:
        values = series.get("values") or []
        finalized.append({
            **series,
            "summary": _summarize_series(values),
            "values": _downsample(values, max_points),
        })

    return finalized


def query_prometheus(
    query: str,
    time_range: str = "1h",
    step: str = "1m",
    summarize: bool = True,
    max_points: int = DEFAULT_MAX_POINTS,
) -> Dict[str, Any]:
    """
    Query Prometheus for historical metric data over a time range.

    Args:
        query: The PromQL expression.
        time_range: The time range to query (e.g. "1h", "1d", "30m").
        step: The resolution step (e.g. "1m", "5m").
        summarize: Attach min/max/mean/last per series and thin the raw
            points to *max_points*. On by default because these results go
            straight into the model's context, where a long raw series is
            expensive and harder to reason over than its statistics.
        max_points: Cap on raw points retained per series when summarizing.

    Returns:
        A dictionary with the success status and the time-series data.
    """
    if USE_MOCK:
        # Generate synthetic data for the mock demo
        import time
        now = int(time.time())
        # Generate 12 data points (1 hour with 5m steps)
        values = [[now - (i * 300), str(1000000000 + i * 50000000)] for i in range(12, 0, -1)]
        return {
            "success": True,
            "query": query,
            "results": _finalize(
                [{"metric": {"pod": "mock-pod-1", "namespace": "default"},
                  "values": values}],
                summarize,
                max_points,
            ),
        }

    try:
        # Convert time_range string (e.g. '1h') to a rough seconds equivalent for start time
        import re
        match = re.match(r"^(\d+)([smhd])$", time_range)
        if not match:
            return {"success": False, "error": f"Invalid time_range format: {time_range}. Use format like '1h' or '30m'."}

        val = int(match.group(1))
        unit = match.group(2)
        multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        duration_seconds = val * multiplier

        end_time = datetime.now(timezone.utc).timestamp()
        start_time = end_time - duration_seconds

        params = {
            "query": query,
            "start": start_time,
            "end": end_time,
            "step": step
        }

        response = requests.get(f"{PROMETHEUS_URL}/api/v1/query_range", params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("status") != "success":
            return {"success": False, "error": f"Prometheus query failed: {data.get('error')}"}

        results = data.get("data", {}).get("result", [])
        return {
            "success": True,
            "query": query,
            "results": _finalize(results, summarize, max_points),
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to query Prometheus: {e}")
        return {"success": False, "error": f"Failed to connect to Prometheus at {PROMETHEUS_URL}. Is it running? (Error: {e})"}
    except Exception as e:
        logger.error(f"Unexpected error querying Prometheus: {e}")
        return {"success": False, "error": str(e)}


def query_pod_resources(namespace: str, pod_name: str, window: str = "1h") -> Dict[str, Any]:
    """
    Get historical CPU and memory usage for a specific pod.

    Args:
        namespace: The Kubernetes namespace.
        pod_name: The name of the pod.
        window: The time window to look back (e.g. "1h").

    Returns:
        Historical resource usage.
    """
    # CPU usage in cores
    cpu_query = f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}", pod="{pod_name}", container!=""}}[5m])) by (pod)'
    # Memory usage in bytes
    mem_query = f'sum(container_memory_working_set_bytes{{namespace="{namespace}", pod="{pod_name}", container!=""}}) by (pod)'

    cpu_res = query_prometheus(cpu_query, time_range=window, step="5m")
    mem_res = query_prometheus(mem_query, time_range=window, step="5m")

    if not cpu_res["success"]:
        return cpu_res
    if not mem_res["success"]:
        return mem_res

    # Also fetch limits from the K8s API directly to compare. Summed across
    # containers: the usage queries above aggregate the whole pod, so a
    # per-container limit would not be comparable against them.
    limits: Dict[str, Any] = {
        "cpu_cores": None,
        "memory_bytes": None,
        "container_count": 0,
        "source": "unavailable",
    }

    try:
        from kubernetes import client, config
        from kubernetes.config.config_exception import ConfigException

        from .kubernetes_capacity import (
            _parse_cpu_quantity,
            _parse_memory_quantity,
        )

        try:
            config.load_incluster_config()
        except ConfigException:
            config.load_kube_config()

        core_api = client.CoreV1Api()
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)

        cpu_total = 0.0
        memory_total = 0
        containers = pod.spec.containers or []

        for container in containers:
            resources = getattr(container, "resources", None)
            container_limits = getattr(resources, "limits", None) or {}

            if "cpu" in container_limits:
                cpu_total += _parse_cpu_quantity(container_limits["cpu"])
            if "memory" in container_limits:
                memory_total += _parse_memory_quantity(container_limits["memory"])

        limits = {
            "cpu_cores": cpu_total,
            "memory_bytes": memory_total,
            "container_count": len(containers),
            "source": "kubernetes",
        }
    except Exception as e:
        logger.warning(f"Could not fetch pod limits from k8s API: {e}")

    return {
        "success": True,
        "pod": pod_name,
        "namespace": namespace,
        "limits": limits,
        "cpu_usage_history": cpu_res["results"],
        "memory_usage_history": mem_res["results"]
    }
