"""Live Pod usage from the Kubernetes metrics-server.

Reads ``metrics.k8s.io/v1beta1`` — the only source of *actual* consumption
for Kubernetes, since ``tools/providers.py`` reads only what Pods request.
metrics-server is an optional add-on, so callers should catch
:class:`MetricsServerUnavailable` and degrade to a requests-only view.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from .metrics_providers import _parse_cpu_quantity, _parse_memory_quantity

METRICS_GROUP = "metrics.k8s.io"
METRICS_VERSION = "v1beta1"


class MetricsServerUnavailable(RuntimeError):
    """Raised when the metrics.k8s.io API cannot be reached."""


def _unavailable(exc: ApiException) -> MetricsServerUnavailable:
    return MetricsServerUnavailable(
        f"metrics.k8s.io is unavailable "
        f"(status={exc.status}, reason={exc.reason}). "
        f"Install metrics-server to enable usage-based rightsizing."
    )


def fetch_pod_usage(
    namespace: str | None = None,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Return live Pod usage keyed by ``(namespace, pod_name)``."""

    api = client.CustomObjectsApi()

    try:
        if namespace:
            payload = api.list_namespaced_custom_object(
                METRICS_GROUP, METRICS_VERSION, namespace, "pods"
            )
        else:
            payload = api.list_cluster_custom_object(
                METRICS_GROUP, METRICS_VERSION, "pods"
            )
    except ApiException as exc:
        raise _unavailable(exc) from exc

    usage: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for item in payload.get("items", []):
        metadata = item.get("metadata", {})
        key = (metadata.get("namespace", ""), metadata.get("name", ""))

        cpu_cores = 0.0
        memory_bytes = 0

        for container in item.get("containers", []):
            container_usage = container.get("usage", {})
            cpu_cores += _parse_cpu_quantity(container_usage.get("cpu", "0"))
            memory_bytes += _parse_memory_quantity(
                container_usage.get("memory", "0")
            )

        usage[key] = {"cpu_cores": cpu_cores, "memory_bytes": memory_bytes}

    return usage


def fetch_node_usage() -> Dict[str, Dict[str, Any]]:
    """Return live node usage keyed by node name."""

    api = client.CustomObjectsApi()

    try:
        payload = api.list_cluster_custom_object(
            METRICS_GROUP, METRICS_VERSION, "nodes"
        )
    except ApiException as exc:
        raise _unavailable(exc) from exc

    usage: Dict[str, Dict[str, Any]] = {}

    for item in payload.get("items", []):
        name = item.get("metadata", {}).get("name", "")
        node_usage = item.get("usage", {})

        usage[name] = {
            "cpu_cores": _parse_cpu_quantity(node_usage.get("cpu", "0")),
            "memory_bytes": _parse_memory_quantity(
                node_usage.get("memory", "0")
            ),
        }

    return usage
