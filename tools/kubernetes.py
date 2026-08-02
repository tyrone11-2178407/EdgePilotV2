from typing import Any, Dict

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from .kubernetes_capacity import (
    KubernetesMetricsProvider,
    evaluate_kubernetes_capacity,
)


def inspect_kubernetes_cluster() -> Dict[str, Any]:
    provider = KubernetesMetricsProvider()
    return provider.gather_metrics()


def evaluate_kubernetes_workload(
    requirements: Dict[str, Any],
    node: str | None = None,
) -> Dict[str, Any]:
    if not isinstance(requirements, dict):
        raise ValueError("requirements must be an object")

    provider = KubernetesMetricsProvider()

    return evaluate_kubernetes_capacity(
        provider,
        requirements,
        node=node,
    )


def inspect_kubernetes_deployment(
    namespace: str,
    deployment_name: str,
) -> Dict[str, Any]:
    if not namespace:
        raise ValueError("namespace is required")

    if not deployment_name:
        raise ValueError("deployment_name is required")

    provider = KubernetesMetricsProvider()
    apps = client.AppsV1Api(provider.core.api_client)

    try:
        deployment = apps.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
        )
    except ApiException as exc:
        raise RuntimeError(
            f"Unable to inspect {namespace}/{deployment_name}: "
            f"status={exc.status}, reason={exc.reason}"
        ) from exc

    return {
        "namespace": namespace,
        "deployment_name": deployment_name,
        "desired_replicas": int(
            deployment.spec.replicas or 0
        ),
        "ready_replicas": int(
            deployment.status.ready_replicas or 0
        ),
        "available_replicas": int(
            deployment.status.available_replicas or 0
        ),
        "updated_replicas": int(
            deployment.status.updated_replicas or 0
        ),
        "unavailable_replicas": int(
            deployment.status.unavailable_replicas or 0
        ),
        "observed_generation": (
            deployment.status.observed_generation
        ),
    }