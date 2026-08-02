import os
import requests
import logging
from typing import Any, Dict, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Assumes user runs `kubectl port-forward svc/prometheus-kube-prometheus-prometheus 9090:9090 -n monitoring`
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
USE_MOCK = os.getenv("PROMETHEUS_MOCK", "false").lower() == "true"

def query_prometheus(query: str, time_range: str = "1h", step: str = "1m") -> Dict[str, Any]:
    """
    Query Prometheus for historical metric data over a time range.

    Args:
        query: The PromQL expression.
        time_range: The time range to query (e.g. "1h", "1d", "30m").
        step: The resolution step (e.g. "1m", "5m").

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
            "results": [
                {
                    "metric": {"pod": "mock-pod-1", "namespace": "default"},
                    "values": values
                }
            ]
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
            "results": results
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

    # Also fetch limits from the K8s API directly to compare
    limits = {"cpu": "Unknown", "memory": "Unknown"}
    try:
        from kubernetes import client, config
        config.load_kube_config()
        core_api = client.CoreV1Api()
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)

        cpu_limit = 0
        mem_limit = 0
        for container in pod.spec.containers:
            if container.resources and container.resources.limits:
                if 'cpu' in container.resources.limits:
                    # Simplify to string as it might have 'm' (millicores)
                    limits["cpu"] = container.resources.limits['cpu']
                if 'memory' in container.resources.limits:
                    limits["memory"] = container.resources.limits['memory']
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
