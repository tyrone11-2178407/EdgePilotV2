import logging
import time
from typing import Dict, Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

def _get_client() -> client.AppsV1Api:
    """Load the standard kubeconfig and return the AppsV1 API client."""
    try:
        config.load_kube_config()
        return client.AppsV1Api()
    except Exception as e:
        logger.error(f"Failed to load kubeconfig: {e}")
        raise RuntimeError(f"Could not load Kubernetes configuration: {e}")

def _get_core_client() -> client.CoreV1Api:
    """Load the standard kubeconfig and return the CoreV1 API client."""
    try:
        config.load_kube_config()
        return client.CoreV1Api()
    except Exception as e:
        logger.error(f"Failed to load kubeconfig: {e}")
        raise RuntimeError(f"Could not load Kubernetes configuration: {e}")

def scale_workload(namespace: str, deployment_name: str, replicas: int) -> Dict[str, Any]:
    """Scales a deployment up or down."""
    if replicas < 0:
        return {"success": False, "error": "Replicas must be >= 0"}
    
    api = _get_client()
    try:
        # Patch the deployment spec to set the new replica count
        patch = {"spec": {"replicas": replicas}}
        api.patch_namespaced_deployment_scale(
            name=deployment_name,
            namespace=namespace,
            body=patch
        )
        msg = f"Successfully scaled deployment '{deployment_name}' in namespace '{namespace}' to {replicas} replicas."
        logger.info(msg)
        return {"success": True, "message": msg}
    except ApiException as e:
        err_msg = f"Kubernetes API error scaling deployment: {e.reason} ({e.status})"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}
    except Exception as e:
        err_msg = f"Unexpected error scaling deployment: {e}"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}

def restart_workload(namespace: str, deployment_name: str) -> Dict[str, Any]:
    """Performs a rolling restart of a deployment."""
    api = _get_client()
    try:
        import datetime
        # To trigger a rolling restart, we patch the pod template with a new annotation
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()
                        }
                    }
                }
            }
        }
        api.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=patch
        )
        msg = f"Successfully triggered rolling restart for deployment '{deployment_name}' in namespace '{namespace}'."
        logger.info(msg)
        return {"success": True, "message": msg}
    except ApiException as e:
        err_msg = f"Kubernetes API error restarting deployment: {e.reason} ({e.status})"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}
    except Exception as e:
        err_msg = f"Unexpected error restarting deployment: {e}"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}

def apply_resource_requests(
    namespace: str,
    deployment_name: str,
    container_name: str,
    cpu_request: str | None = None,
    memory_request: str | None = None,
    cpu_limit: str | None = None,
    memory_limit: str | None = None,
) -> Dict[str, Any]:
    """Patch a deployment container's resource requests and/or limits.

    Quantities are Kubernetes strings such as ``500m`` or ``512Mi`` —
    exactly what ``tools.rightsizing`` emits in its recommendations.
    """

    requests: Dict[str, str] = {}
    limits: Dict[str, str] = {}

    if cpu_request:
        requests["cpu"] = cpu_request
    if memory_request:
        requests["memory"] = memory_request
    if cpu_limit:
        limits["cpu"] = cpu_limit
    if memory_limit:
        limits["memory"] = memory_limit

    if not requests and not limits:
        return {
            "success": False,
            "error": (
                "At least one of cpu_request, memory_request, cpu_limit "
                "or memory_limit must be provided."
            ),
        }

    resources: Dict[str, Any] = {}

    if requests:
        resources["requests"] = requests
    if limits:
        resources["limits"] = limits

    api = _get_client()

    try:
        # A strategic merge patch keys the container list by name, so
        # sibling containers are left untouched.
        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {"name": container_name, "resources": resources}
                        ]
                    }
                }
            }
        }
        api.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=patch,
        )
        msg = (
            f"Updated resources for container '{container_name}' in "
            f"deployment '{deployment_name}' (namespace '{namespace}'): "
            f"{resources}."
        )
        logger.info(msg)
        return {"success": True, "message": msg}
    except ApiException as e:
        err_msg = (
            f"Kubernetes API error updating deployment resources: "
            f"{e.reason} ({e.status})"
        )
        logger.error(err_msg)
        return {"success": False, "error": err_msg}
    except Exception as e:
        err_msg = f"Unexpected error updating deployment resources: {e}"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}

def cordon_node(node_name: str) -> Dict[str, Any]:
    """Marks a node as unschedulable (cordoned)."""
    api = _get_core_client()
    try:
        patch = {"spec": {"unschedulable": True}}
        api.patch_node(name=node_name, body=patch)
        msg = f"Successfully cordoned node '{node_name}'."
        logger.info(msg)
        return {"success": True, "message": msg}
    except ApiException as e:
        err_msg = f"Kubernetes API error cordoning node: {e.reason} ({e.status})"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}
    except Exception as e:
        err_msg = f"Unexpected error cordoning node: {e}"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}

def _quantity_to_number(value: str) -> float:
    """Parse a Kubernetes quantity ('500m', '2Gi', '1') into a plain number.

    CPU is returned in cores, memory in bytes. Kubernetes mixes SI and binary
    suffixes in the same field, so this cannot be a simple int().
    """
    if value is None:
        return 0.0

    text = str(value).strip()
    if not text:
        return 0.0

    binary = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}
    decimal = {"k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}

    for suffix, factor in binary.items():
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor

    if text.endswith("m"):          # millicores
        return float(text[:-1]) / 1000.0

    for suffix, factor in decimal.items():
        if text.endswith(suffix):
            return float(text[:-1]) * factor

    return float(text)


def _node_free_capacity(core, node_name: str) -> Dict[str, float]:
    """CPU cores and memory bytes still unreserved on a node.

    Sums what every pod already scheduled there has *requested*, not what it is
    using. Requests are what the scheduler reserves, so requests are what
    decide whether another pod fits.
    """
    node = core.read_node(name=node_name)
    allocatable = node.status.allocatable or {}
    free_cpu = _quantity_to_number(allocatable.get("cpu", 0))
    free_mem = _quantity_to_number(allocatable.get("memory", 0))

    try:
        pods = core.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={node_name}"
        ).items
    except Exception:  # noqa: BLE001 - an unfilterable API still lets us proceed
        pods = []

    for pod in pods or []:
        for container in getattr(pod.spec, "containers", []) or []:
            requests = getattr(container.resources, "requests", None) or {}
            free_cpu -= _quantity_to_number(requests.get("cpu", 0))
            free_mem -= _quantity_to_number(requests.get("memory", 0))

    return {"cpu": free_cpu, "memory": free_mem}


def _deployment_requests(deployment) -> Dict[str, float]:
    """Total CPU and memory one replica of this deployment reserves."""
    cpu = mem = 0.0
    for container in getattr(deployment.spec.template.spec, "containers", []) or []:
        requests = getattr(container.resources, "requests", None) or {}
        cpu += _quantity_to_number(requests.get("cpu", 0))
        mem += _quantity_to_number(requests.get("memory", 0))
    return {"cpu": cpu, "memory": mem}


def _rollout_complete(deployment) -> bool:
    """True when every replica is updated and ready.

    Patching a deployment only tells Kubernetes what you want. The move is not
    done until the new pods are actually running, and it may never finish.
    """
    desired = deployment.spec.replicas or 0
    ready = deployment.status.ready_replicas or 0
    updated = deployment.status.updated_replicas or 0
    return ready >= desired and updated >= desired


def migrate_workload(
    namespace: str,
    deployment_name: str,
    target_node: str,
    wait_seconds: float = 60.0,
) -> Dict[str, Any]:
    """Move a deployment onto ``target_node``, verifying it actually lands.

    Kubernetes performs the move as a rolling update: it starts the new pod,
    waits for it to become ready, then removes the old one. That handover is
    graceful on its own. What this function adds is the checking around it —
    the previous version patched the deployment and immediately reported
    "Successfully migrated" whether or not anything moved.

    Refuses rather than acting when the target cannot take the workload, since
    a pod that cannot be scheduled leaves the deployment stuck half-moved.
    """
    api = _get_client()
    core = _get_core_client()

    # 1. The target must exist and be willing to accept work.
    try:
        node = core.read_node(name=target_node)
    except Exception as e:
        err_msg = f"Target node '{target_node}' could not be read: {e}"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}

    if getattr(node.spec, "unschedulable", False):
        err_msg = (
            f"Target node '{target_node}' is cordoned (unschedulable). "
            f"Moving work onto it would leave the pod Pending."
        )
        logger.error(err_msg)
        return {"success": False, "error": err_msg}

    # 2. The target must have room. This is the check whose absence let the
    #    assistant migrate blindly onto whichever node it happened to pick.
    try:
        deployment = api.read_namespaced_deployment(
            name=deployment_name, namespace=namespace
        )
    except Exception as e:
        err_msg = f"Deployment '{namespace}/{deployment_name}' could not be read: {e}"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}

    needed = _deployment_requests(deployment)
    free = _node_free_capacity(core, target_node)

    if needed["cpu"] > free["cpu"] or needed["memory"] > free["memory"]:
        err_msg = (
            f"Target node '{target_node}' lacks capacity: needs "
            f"{needed['cpu']:.2f} CPU / {needed['memory'] / 2**30:.2f}Gi, "
            f"free {free['cpu']:.2f} CPU / {free['memory'] / 2**30:.2f}Gi."
        )
        logger.error(err_msg)
        return {"success": False, "error": err_msg}

    # 3. Express the target as a preference, not a permanent pin. A hard
    #    nodeSelector survives the node's death and strands the workload with
    #    nowhere to run; affinity states the same intent and keeps a fallback.
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "affinity": {
                        "nodeAffinity": {
                            "preferredDuringSchedulingIgnoredDuringExecution": [
                                {
                                    "weight": 100,
                                    "preference": {
                                        "matchExpressions": [
                                            {
                                                "key": "kubernetes.io/hostname",
                                                "operator": "In",
                                                "values": [target_node],
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }
    }

    try:
        api.patch_namespaced_deployment(
            name=deployment_name, namespace=namespace, body=patch
        )
    except ApiException as e:
        err_msg = f"Kubernetes API error migrating deployment: {e.reason} ({e.status})"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}
    except Exception as e:
        err_msg = f"Unexpected error migrating deployment: {e}"
        logger.error(err_msg)
        return {"success": False, "error": err_msg}

    # 4. Only now find out whether it worked. Reporting success before this
    #    point is reporting that a request was accepted, not that work moved.
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    while True:
        try:
            current = api.read_namespaced_deployment(
                name=deployment_name, namespace=namespace
            )
        except Exception as e:
            err_msg = f"Migration patched, but the rollout could not be read: {e}"
            logger.error(err_msg)
            return {"success": False, "error": err_msg}

        if _rollout_complete(current):
            msg = (
                f"Migrated '{namespace}/{deployment_name}' to node "
                f"'{target_node}'. All {current.spec.replicas} replica(s) "
                f"ready on the new placement."
            )
            logger.info(msg)
            return {"success": True, "message": msg}

        if time.monotonic() >= deadline:
            err_msg = (
                f"Migration of '{namespace}/{deployment_name}' to "
                f"'{target_node}' did not complete within {wait_seconds:.0f}s: "
                f"{current.status.ready_replicas or 0}/"
                f"{current.spec.replicas or 0} replicas ready. The deployment "
                f"may be stuck part-moved — check for Pending pods."
            )
            logger.error(err_msg)
            return {"success": False, "error": err_msg}

        time.sleep(2.0)
