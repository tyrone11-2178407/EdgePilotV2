"""Shared Kubernetes client loader.

Every module that touches the K8s API was calling ``load_kube_config()``
independently.  This module provides a single helper so configuration
is loaded once and clients are constructed consistently.
"""

from __future__ import annotations

import logging
from typing import Optional

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

logger = logging.getLogger(__name__)

_loaded = False


def _ensure_config() -> None:
    """Load kubeconfig once per process."""
    global _loaded
    if _loaded:
        return
    try:
        config.load_incluster_config()
    except ConfigException:
        try:
            config.load_kube_config()
        except Exception as exc:
            logger.error("Failed to load kubeconfig: %s", exc)
            raise RuntimeError(f"Could not load Kubernetes configuration: {exc}") from exc
    _loaded = True


def get_apps_v1() -> client.AppsV1Api:
    """Return an AppsV1Api client."""
    _ensure_config()
    return client.AppsV1Api()


def get_core_v1() -> client.CoreV1Api:
    """Return a CoreV1Api client."""
    _ensure_config()
    return client.CoreV1Api()
