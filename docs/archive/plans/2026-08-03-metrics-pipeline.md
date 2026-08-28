# Metrics Pipeline Implementation Plan

> **Status:** not started. Branch from `ai-workflow-fixes` (which carries `tools/prometheus.py`
> and PR #5). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the metrics the AI workflows already ask for actually exist, so Dr. Kim's
"pod using 20 GB instead of 1 GB" scenario runs on real data instead of `PROMETHEUS_MOCK=true`.

**Architecture:** Deploy `kube-prometheus-stack` into a reproducible local `kind` cluster.
That single Helm chart brings Prometheus, Grafana, kube-state-metrics and kubelet/cAdvisor
scraping — closing three separate meeting items at once. Then add a metrics module that
joins *requested* resources (kube-state-metrics) against *actual* usage (cAdvisor) in one
PromQL query, so anomaly detection is a number rather than the model eyeballing JSON.

**Tech Stack:** kind, Helm, kube-prometheus-stack, PromQL, Python 3.13, pytest with
mocked HTTP.

---

## Context

The 7/29 meeting's foundational complaint was Manish's: *"we just get like one number"* for
CPU and memory, and *"fix the foundational data pipeline before layering AI on top."* Dr. Kim
agreed and named the fix — Prometheus and Grafana on the Kubernetes master node, pulled via
new MCP functions.

The MCP functions were built. **The metrics they query were never collected.**

`tools/prometheus.py` queries `container_cpu_usage_seconds_total` and
`container_memory_working_set_bytes`. Those come from **cAdvisor**.
`scripts/bootstrap_prometheus.sh` scrapes exactly two targets:

```yaml
- job_name: 'prometheus'   # localhost:9090
- job_name: 'node'         # localhost:9100
```

node_exporter emits `node_*`, never `container_*`. So `query_pod_resources` returns empty
against a real Prometheus, and the `memory_anomaly` workflow — the centrepiece demo —
only works with `PROMETHEUS_MOCK=true`.

The fix is already implied by the code. `tools/prometheus.py:9` reads:

```python
# Assumes user runs `kubectl port-forward svc/prometheus-kube-prometheus-prometheus 9090:9090 -n monitoring`
```

That service name is the **kube-prometheus-stack** Helm release. Aarav designed against it;
nobody installed it. Doing so delivers, in one chart:

| Meeting item | Provided by |
|---|---|
| Prometheus for historical metrics | chart |
| **Grafana** — currently zero references in any branch | chart |
| kube-state-metrics — requests/limits per container | chart |
| cAdvisor scraping via kubelet — actual usage | chart |
| Richer metrics than "one number" | the two above, joined |

**None of this needs Quest access.** It is entirely local simulated Kubernetes, which is
exactly what the meeting specified (*"everything is simulated on a single machine"*).

`scripts/bootstrap_prometheus.sh` is **not** replaced. It installs a host-level Prometheus
into `~/.edgepilot` for non-Kubernetes metrics and still serves that purpose. This plan adds
the in-cluster stack alongside it.

### Verification honesty

The author of this plan has no Kubernetes tooling installed (`kind`, `kubectl`, `helm`,
`docker` all absent), so the cluster steps are written from the chart's documented behaviour
and are **unverified end to end**. Task 3 exists precisely to prove the pipeline works
rather than assume it. Every Python change is covered by tests that mock the HTTP layer and
run with no cluster.

---

## File Structure

| File | Responsibility |
|---|---|
| `deploy/kind-cluster.yaml` *(create)* | Reproducible 3-node local cluster so all four of you run the same thing |
| `scripts/bootstrap_k8s_metrics.sh` *(create)* | Create the cluster, install kube-prometheus-stack, port-forward, write env vars |
| `scripts/verify_metrics_pipeline.py` *(create)* | Assert the exact PromQL the workflows use returns data. The acceptance gate |
| `deploy/demo-workloads.yaml` *(create)* | A well-behaved pod and a deliberately memory-hungry one — real data for the flagship scenario |
| `tools/cluster_metrics.py` *(create)* | Join requested vs actual per workload in one query. Pure fetch + parse, no analysis |
| `tools/prometheus.py` *(modify)* | Exclude the pause container; make mock mode shaped like real responses |
| `core/tool_schemas.py`, `core/tool_executor.py`, `tools/__init__.py` *(modify)* | Register the new tool |
| `skills/workflows/memory_anomaly.yaml` *(modify)* | Use the deterministic detector instead of eyeballing raw series |
| `test/test_cluster_metrics.py` *(create)* | Query construction and parsing, HTTP mocked |

The split that matters: **`tools/cluster_metrics.py` does fetching and parsing only.** No
thresholds, no verdicts. That keeps it testable without a cluster and leaves judgement to the
workflow, or later to the rightsizing engine on `main`.

---

## Task 1: Reproducible cluster definition

Right now the cluster lives on one person's laptop with no definition in the repo, so nobody
else can reproduce a result.

**Files:**
- Create: `deploy/kind-cluster.yaml`

- [ ] **Step 1: Write the cluster config**

Three nodes so per-node metrics are actually distinguishable — a single-node cluster hides
scheduling and node-pressure behaviour entirely.

```yaml
# Local simulated cluster for EdgePilot metrics work.
#   kind create cluster --name edgepilot --config deploy/kind-cluster.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: edgepilot
nodes:
  - role: control-plane
    kubeadmConfigPatches:
      # Expose the kubelet's cAdvisor metrics endpoint to in-cluster scrapers.
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "edgepilot.role=control-plane"
    extraPortMappings:
      - containerPort: 30090   # Prometheus NodePort
        hostPort: 30090
        protocol: TCP
      - containerPort: 30030   # Grafana NodePort
        hostPort: 30030
        protocol: TCP
  - role: worker
  - role: worker
```

- [ ] **Step 2: Verify it parses**

```bash
python3 -c "import yaml; c=yaml.safe_load(open('deploy/kind-cluster.yaml')); print(c['name'], len(c['nodes']), 'nodes')"
```

Expected: `edgepilot 3 nodes`

- [ ] **Step 3: Commit**

```bash
git add deploy/kind-cluster.yaml && git commit -m "feat: add reproducible kind cluster definition"
```

---

## Task 2: Install the metrics stack

**Files:**
- Create: `scripts/bootstrap_k8s_metrics.sh`

- [ ] **Step 1: Write the script**

NodePort rather than `kubectl port-forward` because port-forward dies with its terminal, and
a demo that needs a babysat foreground process will fail in front of stakeholders.

```bash
#!/usr/bin/env bash
#
# Install Prometheus + Grafana + kube-state-metrics + cAdvisor scraping into a
# local kind cluster, via the kube-prometheus-stack chart.
#
# This is the in-cluster stack. scripts/bootstrap_prometheus.sh is separate and
# still installs a host-level Prometheus for non-Kubernetes metrics.
#
#   ./scripts/bootstrap_k8s_metrics.sh install
#   ./scripts/bootstrap_k8s_metrics.sh status
#   ./scripts/bootstrap_k8s_metrics.sh uninstall

set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-edgepilot}"
NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
RELEASE="${PROM_RELEASE:-prometheus}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/env/.env"
ACTION="${1:-install}"

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required tool: $1" >&2
    echo "  kind:    https://kind.sigs.k8s.io/docs/user/quick-start/#installation" >&2
    echo "  kubectl: https://kubernetes.io/docs/tasks/tools/" >&2
    echo "  helm:    https://helm.sh/docs/intro/install/" >&2
    exit 1
  }
}

install_stack() {
  require kind; require kubectl; require helm

  if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
    echo "Creating kind cluster '${CLUSTER_NAME}' ..."
    kind create cluster --name "$CLUSTER_NAME" --config "${REPO_ROOT}/deploy/kind-cluster.yaml"
  else
    echo "kind cluster '${CLUSTER_NAME}' already exists."
  fi

  kubectl config use-context "kind-${CLUSTER_NAME}"

  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
  helm repo update >/dev/null

  echo "Installing kube-prometheus-stack into namespace '${NAMESPACE}' ..."
  helm upgrade --install "$RELEASE" prometheus-community/kube-prometheus-stack \
    --namespace "$NAMESPACE" --create-namespace \
    --set prometheus.service.type=NodePort \
    --set prometheus.service.nodePort=30090 \
    --set grafana.service.type=NodePort \
    --set grafana.service.nodePort=30030 \
    --set grafana.adminPassword=edgepilot \
    --set prometheus.prometheusSpec.retention=7d \
    --set prometheus.prometheusSpec.scrapeInterval=15s \
    --wait --timeout 10m

  echo "Waiting for Prometheus to become ready ..."
  kubectl -n "$NAMESPACE" rollout status statefulset \
    "prometheus-${RELEASE}-kube-prometheus-prometheus" --timeout=5m

  mkdir -p "$(dirname "$ENV_FILE")"
  touch "$ENV_FILE"

  if grep -q '^PROM_URL=' "$ENV_FILE"; then
    echo "PROM_URL already set in ${ENV_FILE}; leaving it alone."
  else
    echo 'PROM_URL=http://localhost:30090' >> "$ENV_FILE"
    echo "Wrote PROM_URL to ${ENV_FILE}"
  fi

  cat <<EOF

Done.
  Prometheus  http://localhost:30090
  Grafana     http://localhost:30030   (admin / edgepilot)

Next: python3 scripts/verify_metrics_pipeline.py
EOF
}

status_stack() {
  require kubectl
  kubectl -n "$NAMESPACE" get pods -o wide || true
  echo
  echo "Scrape targets that matter:"
  curl -fsS "http://localhost:30090/api/v1/targets?state=active" 2>/dev/null \
    | python3 -c "import json,sys; [print('  ', t['labels'].get('job'), t['health']) for t in json.load(sys.stdin)['data']['activeTargets']]" \
    || echo "  Prometheus not reachable on :30090"
}

uninstall_stack() {
  require helm; require kind
  helm uninstall "$RELEASE" -n "$NAMESPACE" || true
  kind delete cluster --name "$CLUSTER_NAME" || true
}

case "$ACTION" in
  install) install_stack ;;
  status) status_stack ;;
  uninstall) uninstall_stack ;;
  *) echo "Usage: $0 {install|status|uninstall}" >&2; exit 1 ;;
esac
```

- [ ] **Step 2: Make it executable and syntax-check it**

```bash
chmod +x scripts/bootstrap_k8s_metrics.sh && bash -n scripts/bootstrap_k8s_metrics.sh && echo "syntax OK"
```

Expected: `syntax OK`

- [ ] **Step 3: Confirm it refuses to run without tooling**

On a machine without kind/kubectl/helm:

```bash
./scripts/bootstrap_k8s_metrics.sh install
```

Expected: `Missing required tool: kind` plus the three install links. Must not proceed.

- [ ] **Step 4: Run it for real (needs Docker)**

```bash
./scripts/bootstrap_k8s_metrics.sh install
```

Expected: cluster created, chart installed, then the Prometheus and Grafana URLs. Takes
5–10 minutes on first run.

- [ ] **Step 5: Commit**

```bash
git add scripts/bootstrap_k8s_metrics.sh && git commit -m "feat: install in-cluster Prometheus, Grafana and kube-state-metrics"
```

---

## Task 3: Prove the pipeline actually feeds the workflows

The acceptance gate. Without this, "Prometheus is installed" is not the same as "the queries
the workflows run return data" — and today those are different things.

**Files:**
- Create: `scripts/verify_metrics_pipeline.py`

- [ ] **Step 1: Write the verifier**

```python
#!/usr/bin/env python3
"""Assert the metrics EdgePilot's workflows depend on actually exist.

Runs the exact PromQL used by tools/prometheus.py and tools/cluster_metrics.py
against a live Prometheus and reports which return data. Before the in-cluster
stack is installed every container_* check fails, which is the bug this catches.

    python3 scripts/verify_metrics_pipeline.py
"""

from __future__ import annotations

import os
import sys

import requests

PROM_URL = os.getenv("PROM_URL", "http://localhost:30090")

# (label, query, why it matters)
CHECKS = [
    ("node metrics (node_exporter)",
     "up{job=~'.*node.*'}",
     "host-level CPU/memory"),
    ("kube-state-metrics: requests",
     "kube_pod_container_resource_requests{resource='memory'}",
     "what pods asked for — required to spot over-consumption"),
    ("kube-state-metrics: limits",
     "kube_pod_container_resource_limits{resource='memory'}",
     "limit headroom and OOM risk"),
    ("cAdvisor: memory usage",
     "container_memory_working_set_bytes{container!='',container!='POD'}",
     "query_pod_resources and the memory_anomaly workflow"),
    ("cAdvisor: cpu usage",
     "rate(container_cpu_usage_seconds_total{container!='',container!='POD'}[5m])",
     "query_pod_resources CPU history"),
    ("pod state",
     "kube_pod_status_phase",
     "CrashLoopBackOff detection in health_check"),
]


def probe(query: str) -> tuple[bool, str]:
    try:
        response = requests.get(
            f"{PROM_URL}/api/v1/query", params={"query": query}, timeout=10
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        return False, f"unreachable: {exc}"

    payload = response.json()

    if payload.get("status") != "success":
        return False, f"query error: {payload.get('error')}"

    count = len(payload.get("data", {}).get("result", []))

    return count > 0, f"{count} series"


def main() -> int:
    print(f"Prometheus: {PROM_URL}\n")

    failures = []

    for label, query, why in CHECKS:
        ok, detail = probe(query)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:<32} {detail}")

        if not ok:
            failures.append((label, why))

    if failures:
        print("\nMissing metrics:")
        for label, why in failures:
            print(f"  - {label}: needed for {why}")
        print(
            "\nInstall the in-cluster stack:\n"
            "  ./scripts/bootstrap_k8s_metrics.sh install"
        )
        return 1

    print("\nAll metrics present. The workflows have real data to run against.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it before installing anything**

```bash
python3 scripts/verify_metrics_pipeline.py
```

Expected: exit 1, with every `cAdvisor` and `kube-state-metrics` row FAIL. **This failure is
the bug** — it is what makes `memory_anomaly` mock-only today.

- [ ] **Step 3: Run it after Task 2**

```bash
./scripts/bootstrap_k8s_metrics.sh install && python3 scripts/verify_metrics_pipeline.py
```

Expected: exit 0, all six PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/verify_metrics_pipeline.py && git commit -m "feat: verify the metrics workflows depend on actually exist"
```

---

## Task 4: Demo workloads for the flagship scenario

The `memory_anomaly` workflow needs a pod genuinely over-consuming memory. Without one it has
nothing true to find, and the demo has to be faked.

**Files:**
- Create: `deploy/demo-workloads.yaml`

- [ ] **Step 1: Write the manifests**

```yaml
# Workloads for the memory-anomaly demo.
#   kubectl apply -f deploy/demo-workloads.yaml
#
# well-behaved-app sits comfortably inside its request.
# memory-hog requests 128Mi and allocates ~1Gi — roughly 8x over, the shape of
# Dr. Kim's "expected 1 GB, using 20 GB" example at a size that fits in kind.
---
apiVersion: v1
kind: Namespace
metadata:
  name: edgepilot-demo
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: well-behaved-app
  namespace: edgepilot-demo
spec:
  replicas: 2
  selector:
    matchLabels: {app: well-behaved-app}
  template:
    metadata:
      labels: {app: well-behaved-app}
    spec:
      containers:
        - name: app
          image: polinux/stress
          command: ["stress"]
          # ~64Mi against a 256Mi request: healthy, should not be flagged.
          args: ["--vm", "1", "--vm-bytes", "64M", "--vm-hang", "0"]
          resources:
            requests: {memory: "256Mi", cpu: "100m"}
            limits: {memory: "512Mi", cpu: "500m"}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: memory-hog
  namespace: edgepilot-demo
spec:
  replicas: 1
  selector:
    matchLabels: {app: memory-hog}
  template:
    metadata:
      labels: {app: memory-hog}
    spec:
      containers:
        - name: app
          image: polinux/stress
          command: ["stress"]
          # ~1Gi against a 128Mi request. The limit is deliberately set well
          # above the request so the pod is NOT OOMKilled — we want a live
          # over-consuming pod to detect, not a crash loop.
          args: ["--vm", "1", "--vm-bytes", "1024M", "--vm-hang", "0"]
          resources:
            requests: {memory: "128Mi", cpu: "100m"}
            limits: {memory: "2Gi", cpu: "500m"}
```

- [ ] **Step 2: Verify the manifests parse**

```bash
python3 -c "
import yaml
docs=[d for d in yaml.safe_load_all(open('deploy/demo-workloads.yaml')) if d]
print(len(docs), 'objects:', [d['kind'] for d in docs])
"
```

Expected: `3 objects: ['Namespace', 'Deployment', 'Deployment']`

- [ ] **Step 3: Apply and confirm the anomaly is real**

```bash
kubectl apply -f deploy/demo-workloads.yaml
```

Wait ~2 minutes for scrapes, then confirm the ratio is visible in Prometheus:

```bash
curl -fsS 'http://localhost:30090/api/v1/query' --data-urlencode 'query=sum by (pod) (container_memory_working_set_bytes{namespace="edgepilot-demo",container!="",container!="POD"}) / on (pod) group_left sum by (pod) (kube_pod_container_resource_requests{namespace="edgepilot-demo",resource="memory"})' | python3 -m json.tool | head -30
```

Expected: `memory-hog` at a ratio around 8, `well-behaved-app` around 0.25.

- [ ] **Step 4: Commit**

```bash
git add deploy/demo-workloads.yaml && git commit -m "feat: add demo workloads reproducing the memory anomaly scenario"
```

---

## Task 5: The richer-metrics module

Manish's ask, concretely: usage joined to requests and limits, per workload, over time —
instead of `cpu.percent` as one number.

**Files:**
- Create: `tools/cluster_metrics.py`
- Test: `test/test_cluster_metrics.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_cluster_metrics.py`:

```python
from unittest.mock import patch

import pytest

from tools.cluster_metrics import (
    build_usage_vs_request_query,
    parse_ratio_response,
    workload_resource_history,
)


def test_query_excludes_the_pause_container():
    """The pause container reports memory but is not the workload."""
    query = build_usage_vs_request_query("edgepilot-demo", "memory")

    assert 'container!=""' in query
    assert 'container!="POD"' in query
    assert 'namespace="edgepilot-demo"' in query


def test_query_joins_usage_against_requests():
    query = build_usage_vs_request_query("demo", "memory")

    assert "container_memory_working_set_bytes" in query
    assert "kube_pod_container_resource_requests" in query
    assert "group_left" in query


def test_cpu_query_uses_a_rate():
    """CPU is a counter — a bare value is meaningless."""
    query = build_usage_vs_request_query("demo", "cpu")

    assert "rate(container_cpu_usage_seconds_total" in query


def test_unknown_resource_is_rejected():
    with pytest.raises(ValueError, match="resource must be"):
        build_usage_vs_request_query("demo", "disk")


def test_parse_ratio_response_extracts_pod_ratios():
    payload = {
        "status": "success",
        "data": {"result": [
            {"metric": {"pod": "memory-hog-abc", "namespace": "demo"},
             "value": [1234567890, "8.25"]},
            {"metric": {"pod": "well-behaved-xyz", "namespace": "demo"},
             "value": [1234567890, "0.25"]},
        ]},
    }

    parsed = parse_ratio_response(payload)

    assert parsed[0]["pod"] == "memory-hog-abc"
    assert parsed[0]["ratio"] == pytest.approx(8.25)
    # Sorted worst-first so the model sees the offender without scanning.
    assert parsed[0]["ratio"] > parsed[1]["ratio"]


def test_parse_ratio_response_skips_unparseable_rows():
    payload = {"status": "success", "data": {"result": [
        {"metric": {"pod": "a"}, "value": [0, "NaN"]},
        {"metric": {"pod": "b"}, "value": [0, "2.0"]},
    ]}}

    parsed = parse_ratio_response(payload)

    assert [row["pod"] for row in parsed] == ["b"]


def test_parse_ratio_response_handles_a_failed_query():
    assert parse_ratio_response({"status": "error", "error": "boom"}) == []


@patch("tools.cluster_metrics.requests.get")
def test_history_reports_worst_offenders_first(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.raise_for_status.return_value = None
    mock_get.return_value.json.return_value = {
        "status": "success",
        "data": {"result": [
            {"metric": {"pod": "quiet", "namespace": "demo"}, "value": [0, "0.2"]},
            {"metric": {"pod": "hog", "namespace": "demo"}, "value": [0, "8.0"]},
        ]},
    }

    result = workload_resource_history("demo", resource="memory")

    assert result["success"] is True
    assert result["worst"][0]["pod"] == "hog"
    assert result["namespace"] == "demo"


@patch("tools.cluster_metrics.requests.get")
def test_history_degrades_when_prometheus_is_down(mock_get):
    import requests as _requests

    mock_get.side_effect = _requests.exceptions.ConnectionError("refused")

    result = workload_resource_history("demo")

    assert result["success"] is False
    assert "Prometheus" in result["error"]
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest test/test_cluster_metrics.py -q
```

Expected: `ModuleNotFoundError: No module named 'tools.cluster_metrics'`

- [ ] **Step 3: Implement `tools/cluster_metrics.py`**

```python
"""Requested versus actual resources per workload, from Prometheus.

Manish's recurring complaint in the 7/29 meeting was that EdgePilot exposes
"one number" for CPU and memory. This joins what each pod *asked for*
(kube-state-metrics) against what it *actually uses* (cAdvisor) in a single
PromQL expression, so "using 8x its request" is a computed value rather than
something the model has to infer from two raw series.

Fetch and parse only — no thresholds and no verdicts. Judgement belongs to the
workflow, or to the rightsizing engine on `main`.

Requires the in-cluster stack: ./scripts/bootstrap_k8s_metrics.sh install
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

PROM_URL = (
    os.getenv("PROM_URL")
    or os.getenv("PROMETHEUS_URL")
    or "http://localhost:30090"
)

# cAdvisor reports the pause container too. It holds the pod's network
# namespace, uses a trivial amount of memory, and is not the workload — leaving
# it in makes every pod look like it has an extra idle container.
_REAL_CONTAINERS = 'container!="",container!="POD"'

_USAGE_METRIC = {
    "memory": 'container_memory_working_set_bytes{{namespace="{ns}",{filt}}}',
    # CPU is a counter, so it must be rated before it means anything.
    "cpu": 'rate(container_cpu_usage_seconds_total{{namespace="{ns}",{filt}}}[5m])',
}


def build_usage_vs_request_query(namespace: str, resource: str = "memory") -> str:
    """PromQL for actual-over-requested, per pod, as a single ratio."""

    if resource not in _USAGE_METRIC:
        raise ValueError(
            f"resource must be one of {sorted(_USAGE_METRIC)}, got {resource!r}"
        )

    usage = _USAGE_METRIC[resource].format(ns=namespace, filt=_REAL_CONTAINERS)

    return (
        f"sum by (namespace, pod) ({usage})"
        f" / on (namespace, pod) group_left "
        f'sum by (namespace, pod) (kube_pod_container_resource_requests'
        f'{{namespace="{namespace}",resource="{resource}"}})'
    )


def parse_ratio_response(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract pod ratios, worst first. Unparseable rows are dropped."""

    if payload.get("status") != "success":
        logger.warning("Prometheus query failed: %s", payload.get("error"))
        return []

    rows: List[Dict[str, Any]] = []

    for series in payload.get("data", {}).get("result", []):
        metric = series.get("metric", {})

        try:
            ratio = float(series["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue

        # A division by a missing request yields NaN or inf; neither is a
        # usable signal and both would sort to the top.
        if ratio != ratio or ratio in (float("inf"), float("-inf")):
            continue

        rows.append({
            "pod": metric.get("pod", ""),
            "namespace": metric.get("namespace", ""),
            "ratio": ratio,
        })

    rows.sort(key=lambda row: row["ratio"], reverse=True)

    return rows


def workload_resource_history(
    namespace: str,
    resource: str = "memory",
    top_n: int = 10,
) -> Dict[str, Any]:
    """Return the pods consuming the most relative to what they requested.

    A ratio above 1.0 means the pod uses more than it asked for. Well below
    1.0 means it is over-provisioned.
    """

    query = build_usage_vs_request_query(namespace, resource)

    try:
        response = requests.get(
            f"{PROM_URL}/api/v1/query", params={"query": query}, timeout=15
        )
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.RequestException as exc:
        return {
            "success": False,
            "error": (
                f"Could not reach Prometheus at {PROM_URL}: {exc}. "
                f"Run ./scripts/bootstrap_k8s_metrics.sh install"
            ),
        }

    rows = parse_ratio_response(payload)

    return {
        "success": True,
        "namespace": namespace,
        "resource": resource,
        "worst": rows[:top_n],
        "pods_measured": len(rows),
        "note": "ratio = actual usage / requested. Above 1.0 exceeds the request.",
    }
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m pytest test/test_cluster_metrics.py -q
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/cluster_metrics.py test/test_cluster_metrics.py && git commit -m "feat: join requested against actual resources per workload"
```

---

## Task 6: Expose it to the AI and use it in the workflow

**Files:**
- Modify: `tools/__init__.py`, `core/tool_schemas.py`, `core/tool_executor.py`
- Modify: `skills/workflows/memory_anomaly.yaml`
- Test: `test/test_workflows.py`

- [ ] **Step 1: Export the function**

Append to `tools/__init__.py`:

```python
from .cluster_metrics import workload_resource_history
```

- [ ] **Step 2: Add the schema**

Append to `TOOL_SCHEMAS` in `core/tool_schemas.py`, before the closing `]`:

```python
    {
        "name": "workload_resource_history",
        "description": (
            "Compare what each pod in a namespace actually consumes against "
            "what it requested, returned as a ratio and sorted worst first. A "
            "ratio above 1.0 means the pod uses more than it asked for. Use "
            "this to find memory or CPU anomalies instead of reading raw "
            "Prometheus series. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to examine.",
                },
                "resource": {
                    "type": "string",
                    "description": "Either 'memory' or 'cpu'. Defaults to memory.",
                    "default": "memory",
                },
                "top_n": {
                    "type": "integer",
                    "description": "How many of the worst offenders to return.",
                    "default": 10,
                },
            },
            "required": ["namespace"],
        },
    },
```

- [ ] **Step 3: Register the executor**

In `core/tool_executor.py`, add `workload_resource_history` to the `from tools import (...)`
block, add the dispatch entry beside the other read-only tools:

```python
            "workload_resource_history": self._execute_workload_resource_history,
```

and add the unwrapper:

```python
    def _execute_workload_resource_history(
        self, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        namespace = args.get("namespace")

        if not namespace:
            raise ValueError("namespace is required")

        return workload_resource_history(
            namespace,
            resource=args.get("resource", "memory"),
            top_n=int(args.get("top_n", 10) or 10),
        )
```

- [ ] **Step 4: Point the workflow at it**

In `skills/workflows/memory_anomaly.yaml`, replace the `detect_anomalies` step:

```yaml
  - name: "detect_anomalies"
    instruction: "Call workload_resource_history for the 'edgepilot-demo' namespace to get each pod's actual memory usage as a ratio of what it requested. Report any pod with a ratio above 1.0, quoting the ratio."
    tools: ["workload_resource_history"]
```

The ratio is now computed in PromQL rather than inferred by the model from two raw series —
cheaper, and reproducible run to run.

- [ ] **Step 5: Verify registration and that the workflow guards still hold**

```bash
python3 -c "
from core.tool_executor import ToolExecutor
from core.tool_schemas import get_all_tool_schemas
tools = set(ToolExecutor().tools)
names = {s['name'] for s in get_all_tool_schemas()}
assert 'workload_resource_history' in tools and 'workload_resource_history' in names
print('registered:', len(tools), 'tools /', len(names), 'schemas')
"
python3 -m pytest test/ -q
```

Expected: registration line, then all tests pass — including
`test_every_workflow_references_a_real_tool`, which would fail on a typo in the YAML.

- [ ] **Step 6: Commit**

```bash
git add tools/__init__.py core/tool_schemas.py core/tool_executor.py skills/workflows/memory_anomaly.yaml && git commit -m "feat: expose workload resource history and use it for anomaly detection"
```

---

## Task 7: Fix the pause container leak in the existing client

`tools/prometheus.py`'s `query_pod_resources` filters `container!=""` but not
`container!="POD"`, so the pause container inflates every pod's memory figure.

**Files:**
- Modify: `tools/prometheus.py`
- Test: `test/test_prometheus.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_prometheus.py`:

```python
def test_pod_queries_exclude_the_pause_container(monkeypatch):
    """cAdvisor reports the pause container; it is not the workload."""
    captured = []

    def fake_query(query, time_range="1h", step="5m", **kwargs):
        captured.append(query)
        return {"success": True, "query": query, "results": []}

    monkeypatch.setattr("tools.prometheus.query_prometheus", fake_query)

    query_pod_resources("demo", "some-pod")

    assert captured, "no queries were issued"
    for query in captured:
        assert 'container!="POD"' in query, f"pause container not excluded: {query}"
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest test/test_prometheus.py::test_pod_queries_exclude_the_pause_container -q
```

Expected: FAIL — `pause container not excluded`

- [ ] **Step 3: Fix both queries**

In `tools/prometheus.py`'s `query_pod_resources`, change:

```python
    cpu_query = (
        f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}", '
        f'pod="{pod_name}", container!="", container!="POD"}}[5m])) by (pod)'
    )
    mem_query = (
        f'sum(container_memory_working_set_bytes{{namespace="{namespace}", '
        f'pod="{pod_name}", container!="", container!="POD"}}) by (pod)'
    )
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m pytest test/test_prometheus.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tools/prometheus.py test/test_prometheus.py && git commit -m "fix: exclude the pause container from pod resource queries"
```

---

## Verification

**Without a cluster** — everything Python, and what CI runs:

```bash
python3 -m pytest test/ -q
```

Expected: all pass, including the new `test_cluster_metrics.py`.

**With a cluster** — the real acceptance path, start to finish:

```bash
./scripts/bootstrap_k8s_metrics.sh install
```

```bash
kubectl apply -f deploy/demo-workloads.yaml
```

Wait about two minutes for scrapes, then:

```bash
python3 scripts/verify_metrics_pipeline.py
```

Expected: all six checks PASS. Before this plan, the four `container_*` and
`kube_*` checks fail — that is the bug.

Then confirm the detector sees the planted anomaly:

```bash
python3 -c "
from tools.cluster_metrics import workload_resource_history
import json
print(json.dumps(workload_resource_history('edgepilot-demo'), indent=2))
"
```

Expected: `memory-hog` first with a ratio around 8, `well-behaved-app` well below 1.0.

**End to end through the app** — the actual demo:

```bash
python3 -m uvicorn main:app --reload --port 8000
```

Run the `memory_anomaly` workflow and confirm it names `memory-hog`, quotes the real ratio,
and stops for approval before restarting anything. Crucially, run it with
`PROMETHEUS_MOCK` unset — the point of this plan is that the scenario no longer needs mock
mode.

**Grafana**, which closes the meeting item nothing had touched:

```bash
open http://localhost:30030
```

Log in as `admin` / `edgepilot`. The chart ships Kubernetes dashboards; confirm
`memory-hog` is visible in "Kubernetes / Compute Resources / Namespace (Pods)". This is the
screen to show stakeholders alongside the AI's answer.

---

## What this does not cover

- **Cloud Scale / kubectl-ai (Goal 1).** Needs its own plan. EdgePilot's `MCP/` is a
  function-calling registry, not the Model Context Protocol — there is no `mcp` package and
  no JSON-RPC anywhere — so consuming kubectl-ai's MCP server means building a real client
  first. Also still worth confirming with Dr. Kim exactly which tool he means.
- **Quest data.** Deliberately nothing here depends on it. When access lands, the Slurm
  readers on `main` are the entry point, not this pipeline.
- **Grafana dashboards of our own.** The chart's built-in Kubernetes dashboards are enough
  to demo; a bespoke EdgePilot dashboard is polish.
- **The `ai-workflow` / `main` divergence.** `ai-workflow` renames `tools/providers.py` and
  moves `MCP/` to `core/`, while `main` has four modules importing `from .providers import`.
  That needs agreeing between the three of you, not resolving inside this work.
- **Retention beyond 7 days.** Set in Task 2 via `prometheus.prometheusSpec.retention`.
  Raise it if a demo needs longer history, at the cost of disk.
