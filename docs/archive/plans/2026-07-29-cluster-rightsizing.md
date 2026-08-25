# Cluster Rightsizing and Bottleneck Analysis — Implementation Plan

> **Status:** implemented. This document is kept as the design record — it explains why the
> analysis engines are scheduler-agnostic and where Quest data plugs in. See the
> "When Quest data arrives" section at the end for the integration checklist.

**Goal:** Answer the project's stated research question — *"In a cluster setting, how might we identify resource bottlenecks and optimize them?"* — with tools that ingest Quest job data, identify each workload's binding constraint, and recommend corrected CPU / memory / GPU sizing.

**Architecture:** Three normalized data layers (`ResourceRecord`, `UsageSample`, `NodeSpec`) mirroring the three layers requested in the Quest proposal, and two **pure** analysis engines over them — rightsizing and bottleneck classification. All scheduler-specific I/O lives in swappable source modules. The engines import no scheduler client and perform no I/O, so the entire analysis runs and is tested against fixtures before Quest access lands.

**Tech Stack:** Python 3.13, stdlib `csv` / `json` / `dataclasses` / `statistics` / `subprocess`, `kubernetes` 35.x client, FastAPI + SSE, pytest with `unittest.mock` / `SimpleNamespace` fakes.

---

## Context

The repo bills itself as "LLMs for Real-Time Transparency into Computing Cluster Performance", but the LLM's cluster abilities are backwards: it can **mutate** a cluster (`scale_workload`, `restart_workload`, `cordon_node`) yet cannot **read** one. There is no tool to list workloads, inspect requests, or observe consumption, so any sizing advice it gives is ungrounded guesswork.

The target is **Northwestern Quest**, a Slurm HPC cluster, under an approved-pending data-use proposal (PI: Yongho Kim, ANL; contact: Kristian Hammond, Northwestern FORGE). Access is not yet live. Phase 1 will deliver **1,000–2,000 anonymized jobs** over a few representative weeks, deliberately sampled to include GPU-heavy *and GPU-underutilized* jobs, CPU-heavy and memory-intensive jobs, multi-node jobs, and failed / OOM / timeout jobs — in **three linked layers**:

| Proposal layer | Contents | Our type |
|---|---|---|
| 1. Job-level time-series (Jobstats/Slurm) | per-interval CPU utilization, memory used vs. allocated, GPU utilization, per-GPU memory | `UsageSample` |
| 2. Accounting records (`sacct`) | submit/start/end, exit code, requested vs. used TRES, partition, QOS, nodes, completion reason | `ResourceRecord` |
| 3. Node specs | CPU type + core count, total memory, GPU type/count + GPU memory, storage, interconnect, hardware class | `NodeSpec` |

That structure drives the design. **No data exists yet**, so the reviewable, demonstrable part of this work must run against fixtures on a laptop. Every engine here does.

Three facts about the data shape the analysis:

- **GPU is first-class.** The proposal samples for GPU-underutilized jobs by name. On an HPC cluster an idle allocated GPU is the largest single waste category — a job holding 4 A100s at 6% utilization wastes far more than any memory over-request. CPU-and-memory-only rightsizing would miss the headline finding.
- **Time-series enables real statistics.** Per-interval samples distinguish a steady 4-core job from one that used 16 cores for five minutes and 1 core for three hours. Both look identical in `sacct` alone. We derive mean / p95 / peak from samples when present, and fall back to scalar accounting fields when not.
- **Node specs enable bottleneck attribution.** Knowing a node's memory-per-core ratio is what separates "this job is memory-bound" from "this job landed on the wrong node class".

Kubernetes stays in scope because the repo already carries most of it. `tools/providers.py` contains a complete `KubernetesMetricsProvider` — correct init-container `max()` accounting plus `pod.spec.overhead`, per-node and cluster-wide `requested_percent`, taint/toleration matching — with **no MCP schema, no executor entry, and no export from `tools/__init__.py`**. It is dead code today. It also gives the team a live cluster to develop against while Quest access is pending.

`main` does not pass its own tests. Two collection errors:

```
ImportError: cannot import name 'evaluate_kubernetes_capacity' from 'tools.providers'
ImportError: cannot import name 'os_profile' from 'core.interface'
```

`test/test_providers.py` (604 lines, the repo's best-mocked suite) tests a function never committed, and `_untolerated_taints` at `tools/providers.py:547` builds `unmatched` then **falls off the end without returning it**. `edgepilot_cli.py:11` imports two functions absent from `core/interface.py`. Fixes exist on `origin/improve-answer-quality` and are reproduced below.

**Outcome:** a demo today over three-layer fixtures showing which jobs waste GPUs, which are memory-bound, and what each should request instead. When Quest data arrives, writing a column mapping is the only new work.

### Design constraints

- **Thresholds are parameters, never hardcoded verdicts.** `origin/improve-answer-quality` rewrites the system prompt to say *"Do not use arbitrary universal thresholds to determine whether a workload is safe."* Every classification takes a target-utilization argument with a documented default, and every finding carries its observed ratio as evidence. This is a deliberate improvement on `origin/v2-optimizations`, whose `compare_job_efficiency` hardcodes *"waste > 50% → downsize by at least 50%"*.
- **Memory is sized from observed peak, never the mean.** Memory is non-compressible; sizing to the average causes OOM kills.
- **Never recommend shrinking memory for an OOM-killed job.** Its observed peak is truncated at the moment the kernel intervened — the sample is evidence of under-sizing, not right-sizing.
- **Unknown usage is reported, never estimated.** Missing measurements produce a `usage_unavailable` finding and `recommendation=None`.
- **The data is anonymized and stays that way.** Job records carry anonymized user/account labels; nothing in these tools logs, caches, or emits record-level identifiers to an LLM provider beyond what the user asked about. See "Privacy handling" below.
- **Only `apply_resource_requests` is added to `DANGEROUS_TOOLS`.** Slurm output is advisory — job sizing is set at submission — so there is no Slurm mutation path.

### Prior art to harvest, not merge

`origin/v2-optimizations` has `tools/cluster_schedulers.py` with `query_slurm_accounting`, `compare_job_efficiency`, `query_cluster_incidents`, `ingest_historical_sample`. **Take the ideas, not the file** — that branch forks `tools/metrics.py`'s TTL cache into a shape that breaks `test/test_optimization.py`, and drops the `run_shell`/`run_python` executor aliases.

Two defects there not to repeat:
1. `scripts/mock_jobs.csv` is **malformed**: `ReqTRES` values like `cpu=4,mem=32G,node=1` contain unquoted commas, so an 8-column header receives 14 fields per row and `DictReader` silently mis-parses. Our fixtures quote them.
2. `compare_job_efficiency` reads `MaxRSS` from `sstat`, which only works for *running* jobs, and only considers memory. We read completed-job accounting from `sacct` and cover CPU, memory, and GPU.

---

## File Structure

| File | Responsibility |
|---|---|
| `tools/resource_records.py` *(create)* | `ResourceRecord`, `UsageSample`, `NodeSpec`. The contract between sources and engines. No I/O, no scheduler imports. |
| `tools/rightsizing.py` *(create)* | **Pure** sizing: arithmetic, classification, aggregation, report shape. |
| `tools/bottlenecks.py` *(create)* | **Pure** constraint classification: which resource actually limits each workload, and cluster-level rollup. |
| `tools/slurm_source.py` *(create)* | Slurm TRES/duration/memory parsing, `sacct` invocation, CSV replay, node-spec join. |
| `tools/jobstats_source.py` *(create)* | Per-interval time-series → `UsageSample` → derived statistics. |
| `tools/cluster_usage.py` *(create)* | metrics-server client only. |
| `tools/kubernetes_source.py` *(create)* | Pod specs + live usage → records, grouped by owning controller. |
| `tools/providers.py` *(modify)* | Repair `_untolerated_taints`; add `evaluate_kubernetes_capacity`. |
| `tools/kubernetes_actions.py` *(modify)* | Add `apply_resource_requests`. |
| `test/fixtures/quest_jobs_sample.csv` *(create)* | Layer 2 — `sacct`-shaped, correctly quoted. |
| `test/fixtures/quest_jobstats_sample.json` *(create)* | Layer 1 — per-interval time-series. |
| `test/fixtures/quest_nodes_sample.csv` *(create)* | Layer 3 — node specs. |
| `tools/__init__.py`, `MCP/tool_schemas.py`, `MCP/tool_executor.py` *(modify)* | Standard five-point tool registration. |
| `core/interface.py`, `core/settings.py` *(modify)* | Repair missing functions; teach the prompt cluster vocabulary. |

The split that matters: **`rightsizing.py` and `bottlenecks.py` must never import `kubernetes` or shell out to `sacct`.** That is what makes them testable with no cluster and lets a Quest adapter drop in without touching analysis.

### Privacy handling

The data is anonymized at source, but three rules apply in code, and each has a test:

- `ResourceRecord.record_id` and account labels are **never** written to `data/` or into `core/semantic_cache.py`. Rightsizing tools are already excluded from caching by being added to `_STATE_CHANGING_TOOLS`.
- `tools/*_source.py` modules log **counts and aggregates only** — never a job ID, user, or account at `INFO` or above.
- Report payloads returned to the LLM are per-workload aggregates. Individual job IDs appear only in the `records` layer, which the MCP tools do not return.

---

## Task 1: Repair `tools/providers.py`

Prerequisite — the K8s test suite cannot import today.

**Files:**
- Modify: `tools/providers.py:547-567`
- Test: `test/test_providers.py` (already written, currently failing to collect)

- [ ] **Step 1: Confirm the failure**

```bash
python3 -m pytest test/test_providers.py --collect-only -q
```

Expected: `ImportError: cannot import name 'evaluate_kubernetes_capacity' from 'tools.providers'`

- [ ] **Step 2: Add the missing `return`**

`_untolerated_taints` currently ends at `unmatched.append(taint)`. Make the tail read:

```python
        if not tolerated:
            unmatched.append(taint)

    return unmatched
```

- [ ] **Step 3: Append `evaluate_kubernetes_capacity`**

Reason strings are load-bearing — `test/test_providers.py` asserts them exactly (`"Pod slots available 0 < required 1"`, `"Node is not Ready"`, `"Node is unschedulable"`, `"Untolerated node taint: dedicated=gpu:NoSchedule"`, plus `startswith("CPU available")` / `startswith("Memory available")`).

```python
def evaluate_kubernetes_capacity(
    provider: MetricsProvider,
    request: Dict[str, Any],
    node: str | None = None,
) -> Dict[str, Any]:
    """Determine which cluster nodes can admit a workload request.

    ``request`` accepts ``cpu_cores``, ``memory_bytes``, ``pods`` and an
    optional ``tolerations`` list shaped like a Pod spec's tolerations.
    """

    capacities = provider.get_capacity(host=node)

    need_cpu = float(request.get("cpu_cores", 0) or 0)
    need_memory = int(request.get("memory_bytes", 0) or 0)
    need_pods = int(request.get("pods", 0) or 0)
    tolerations = request.get("tolerations") or []

    results: list[Dict[str, Any]] = []

    for capacity in capacities:
        reasons: list[str] = []
        status = capacity.get("status", {})

        if not status.get("ready", False):
            reasons.append("Node is not Ready")

        if not status.get("schedulable", False):
            reasons.append("Node is unschedulable")

        headroom = capacity.get("headroom", {})
        available_cpu = float(headroom.get("cpu_cores", 0) or 0)
        available_memory = int(headroom.get("memory_bytes", 0) or 0)
        available_pods = int(headroom.get("pods", 0) or 0)

        if available_cpu < need_cpu:
            reasons.append(
                f"CPU available {available_cpu:.3f} cores "
                f"< required {need_cpu:.3f} cores"
            )

        if available_memory < need_memory:
            reasons.append(
                f"Memory available {available_memory} bytes "
                f"< required {need_memory} bytes"
            )

        if available_pods < need_pods:
            reasons.append(
                f"Pod slots available {available_pods} "
                f"< required {need_pods}"
            )

        for taint in _untolerated_taints(
            capacity.get("taints", []),
            tolerations,
        ):
            reasons.append(
                f"Untolerated node taint: "
                f"{taint['key']}={taint['value']}:{taint['effect']}"
            )

        results.append(
            {
                "instance": capacity.get("instance"),
                "can_run_now": not reasons,
                "reasons": reasons,
            }
        )

    return {"status": "ok", "results": results, "source": "kubernetes"}
```

- [ ] **Step 4: Run the suite**

```bash
python3 -m pytest test/test_providers.py -q
```

Expected: all pass (~30 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/providers.py && git commit -m "fix: return untolerated taints and add cluster capacity evaluation"
```

---

## Task 2: The three-layer data model

**Files:**
- Create: `tools/resource_records.py`
- Test: `test/test_resource_records.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_resource_records.py`:

```python
import pytest

from tools.resource_records import NodeSpec, ResourceRecord, UsageSample


def test_group_key_aggregates_repeated_runs():
    record = ResourceRecord(
        source="slurm",
        workload_name="train",
        group="ml",
        requested_cpu_cores=8,
        requested_memory_bytes=1024,
    )

    assert record.group_key == ("slurm", "ml", "train")


def test_effective_gpus_used_scales_utilization_by_count():
    record = ResourceRecord(
        source="slurm",
        workload_name="train",
        requested_cpu_cores=8,
        requested_memory_bytes=1024,
        requested_gpu_count=4,
        used_gpu_utilization=0.10,
    )

    assert record.effective_gpus_used == pytest.approx(0.4)


def test_effective_gpus_used_is_none_without_measurement():
    record = ResourceRecord(
        source="slurm",
        workload_name="train",
        requested_cpu_cores=8,
        requested_memory_bytes=1024,
        requested_gpu_count=4,
    )

    assert record.effective_gpus_used is None


def test_node_spec_reports_memory_per_core():
    node = NodeSpec(
        name="qnode0101",
        cpu_cores=64,
        memory_bytes=256 * 1024**3,
        hardware_class="normal",
    )

    assert node.memory_bytes_per_core == pytest.approx(4 * 1024**3)


def test_node_spec_memory_per_core_handles_zero_cores():
    node = NodeSpec(name="broken", cpu_cores=0, memory_bytes=1024)

    assert node.memory_bytes_per_core is None


def test_usage_sample_defaults_are_none_not_zero():
    sample = UsageSample(offset_seconds=0)

    assert sample.cpu_cores is None
    assert sample.gpu_utilization is None
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest test/test_resource_records.py -q
```

Expected: `ModuleNotFoundError: No module named 'tools.resource_records'`

- [ ] **Step 3: Create `tools/resource_records.py`**

```python
"""The contract between resource sources and the analysis engines.

Three types mirror the three linked data layers requested in the Quest
data-use proposal:

* :class:`UsageSample`  — layer 1, per-interval Jobstats time-series
* :class:`ResourceRecord` — layer 2, sacct accounting for one unit of work
* :class:`NodeSpec`     — layer 3, hardware specs for the nodes work ran on

Sources normalize into these; the engines consume only these. Nothing here
imports a scheduler client or performs I/O.

``None`` consistently means *not measured*, never zero. The engines report
a missing measurement rather than estimating it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UsageSample:
    """One interval of observed usage for a single unit of work.

    ``offset_seconds`` is measured from job start, which keeps samples
    usable after timestamps are anonymized.
    """

    offset_seconds: float
    cpu_cores: Optional[float] = None
    memory_bytes: Optional[int] = None
    gpu_utilization: Optional[float] = None      # fraction 0.0–1.0
    gpu_memory_bytes: Optional[int] = None
    node: Optional[str] = None


@dataclass
class NodeSpec:
    """Hardware specification for a compute node."""

    name: str
    cpu_cores: int = 0
    memory_bytes: int = 0
    gpu_count: int = 0
    gpu_model: Optional[str] = None
    gpu_memory_bytes: int = 0
    hardware_class: Optional[str] = None
    partition: Optional[str] = None

    @property
    def memory_bytes_per_core(self) -> Optional[float]:
        """Memory-to-core ratio, the signal for node-class fit."""

        if not self.cpu_cores:
            return None

        return self.memory_bytes / self.cpu_cores


@dataclass
class ResourceRecord:
    """One unit of scheduled work, normalized across schedulers.

    Attributes
    ----------
    workload_name:
        Stable name shared by repeated runs — a Slurm ``JobName`` or a
        Kubernetes Deployment name. Records sharing
        ``(source, group, workload_name)`` are aggregated together.
    group:
        Namespace, account, or partition. Anonymized upstream.
    instance_count:
        Nodes for a Slurm job, Pods for a Kubernetes workload.
    used_cpu_cores / peak_memory_bytes / used_gpu_utilization:
        ``None`` means *not measured*.
    samples:
        Layer-1 time-series, when available. Sources that provide samples
        should also populate the scalar summaries derived from them.
    """

    source: str
    workload_name: str
    requested_cpu_cores: float
    requested_memory_bytes: int

    group: Optional[str] = None
    record_id: Optional[str] = None
    instance_count: int = 1

    requested_gpu_count: float = 0.0

    used_cpu_cores: Optional[float] = None
    peak_cpu_cores: Optional[float] = None
    peak_memory_bytes: Optional[int] = None
    used_gpu_utilization: Optional[float] = None
    peak_gpu_memory_bytes: Optional[int] = None

    state: str = "UNKNOWN"
    failed_oom: bool = False
    elapsed_seconds: Optional[float] = None

    partition: Optional[str] = None
    qos: Optional[str] = None
    nodes: List[str] = field(default_factory=list)
    node_spec: Optional[NodeSpec] = None
    samples: List[UsageSample] = field(default_factory=list)

    containers: List[str] = field(default_factory=list)
    labels: Dict[str, Any] = field(default_factory=dict)

    @property
    def group_key(self) -> tuple[str, str, str]:
        """Key used to aggregate repeated runs of the same workload."""

        return (self.source, self.group or "", self.workload_name)

    @property
    def effective_gpus_used(self) -> Optional[float]:
        """GPU-equivalents actually consumed.

        Four GPUs at 10% mean utilization is 0.4 effective GPUs — the
        quantity a corrected GPU request should be sized from.
        """

        if self.used_gpu_utilization is None or not self.requested_gpu_count:
            return None

        return self.used_gpu_utilization * self.requested_gpu_count
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m pytest test/test_resource_records.py -q
```

Expected: 6 passed.

---

## Task 3: Sizing arithmetic

**Files:**
- Create: `tools/rightsizing.py`
- Test: `test/test_rightsizing.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_rightsizing.py`:

```python
import pytest

from tools.rightsizing import (
    _recommended_cpu_cores,
    _recommended_gpu_count,
    _recommended_memory_bytes,
    to_cpu_quantity,
    to_memory_quantity,
)


def test_recommended_cpu_adds_headroom_for_target_utilization():
    # Using 0.35 cores at a 70% target implies a 0.5 core request.
    assert _recommended_cpu_cores(0.35, 0.70) == pytest.approx(0.5)


def test_recommended_cpu_respects_floor():
    assert _recommended_cpu_cores(0.0001, 0.70) == pytest.approx(0.01)


def test_recommended_cpu_rejects_nonpositive_target():
    with pytest.raises(ValueError, match="target_utilization"):
        _recommended_cpu_cores(1.0, 0.0)


def test_recommended_memory_rounds_up_to_whole_mebibytes():
    # 100 MiB peak at an 80% target is 125 MiB.
    assert _recommended_memory_bytes(100 * 1024**2, 0.80) == 125 * 1024**2


def test_recommended_memory_respects_floor():
    assert _recommended_memory_bytes(0, 0.80) == 16 * 1024**2


def test_recommended_gpu_count_rounds_up_to_whole_gpus():
    # 0.4 effective GPUs at a 70% target needs 1 GPU.
    assert _recommended_gpu_count(0.4, 0.70) == 1
    # 2.8 effective GPUs at a 70% target needs 4.
    assert _recommended_gpu_count(2.8, 0.70) == 4


def test_recommended_gpu_count_is_zero_when_gpus_are_unused():
    assert _recommended_gpu_count(0.0, 0.70) == 0


def test_quantity_formatting_produces_kubernetes_strings():
    assert to_cpu_quantity(0.5) == "500m"
    assert to_memory_quantity(125 * 1024**2) == "125Mi"
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest test/test_rightsizing.py -q
```

Expected: `ModuleNotFoundError: No module named 'tools.rightsizing'`

- [ ] **Step 3: Create `tools/rightsizing.py`**

```python
"""Rightsizing analysis over normalized resource records.

Compares what work *requests* against what it *actually consumes* and
recommends corrected CPU, memory and GPU sizing. Deliberately pure: it
imports no scheduler client and performs no I/O, so it runs identically
over Slurm accounting and live Kubernetes, and is fully testable with no
cluster.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional

from .resource_records import ResourceRecord

DEFAULT_CPU_TARGET_UTILIZATION = 0.70
DEFAULT_MEMORY_TARGET_UTILIZATION = 0.80
DEFAULT_GPU_TARGET_UTILIZATION = 0.70

# Floors keep recommendations schedulable — work sized from a near-idle
# sample should still get a usable slice, not effectively zero.
_MIN_CPU_CORES = 0.01             # 10m
_MIN_MEMORY_BYTES = 16 * 1024**2  # 16Mi

_MEBIBYTE = 1024**2


def _require_target(target_utilization: float) -> None:
    if target_utilization <= 0:
        raise ValueError("target_utilization must be greater than 0")


def _recommended_cpu_cores(
    observed_cores: float,
    target_utilization: float,
) -> float:
    """Size a CPU request so observed usage lands at *target_utilization*."""

    _require_target(target_utilization)

    return max(round(observed_cores / target_utilization, 2), _MIN_CPU_CORES)


def _recommended_memory_bytes(
    observed_bytes: float,
    target_utilization: float,
) -> int:
    """Size a memory request so *peak* usage lands at *target_utilization*.

    Callers pass observed peak, not mean — memory is not compressible, so
    sizing to the average invites OOM kills.
    """

    _require_target(target_utilization)

    sized = observed_bytes / target_utilization
    whole_mebibytes = math.ceil(sized / _MEBIBYTE)

    return max(whole_mebibytes * _MEBIBYTE, _MIN_MEMORY_BYTES)


def _recommended_gpu_count(
    effective_gpus_used: float,
    target_utilization: float,
) -> int:
    """Size a GPU request from GPU-equivalents actually consumed.

    Unlike CPU and memory there is no floor: a result of ``0`` is the
    meaningful signal that the work never used its GPUs and belongs on a
    CPU-only partition.
    """

    _require_target(target_utilization)

    return math.ceil(effective_gpus_used / target_utilization)


def to_cpu_quantity(cores: float) -> str:
    """Format CPU cores as a Kubernetes quantity string (e.g. ``500m``)."""

    return f"{int(round(cores * 1000))}m"


def to_memory_quantity(byte_count: int) -> str:
    """Format bytes as a Kubernetes quantity string (e.g. ``125Mi``)."""

    return f"{int(round(byte_count / _MEBIBYTE))}Mi"
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m pytest test/test_rightsizing.py -q
```

Expected: 8 passed.

---

## Task 4: The rightsizing engine

**Files:**
- Modify: `tools/rightsizing.py`
- Test: `test/test_rightsizing.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_rightsizing.py`:

```python
from tools.resource_records import ResourceRecord
from tools.rightsizing import analyze_records


def record(**overrides):
    defaults = dict(
        source="slurm",
        workload_name="train",
        group="chem",
        requested_cpu_cores=4.0,
        requested_memory_bytes=32 * 1024**3,
        used_cpu_cores=0.4,
        peak_memory_bytes=2 * 1024**3,
        state="COMPLETED",
    )
    defaults.update(overrides)
    return ResourceRecord(**defaults)


def test_flags_over_requested_cpu_and_memory():
    workload = analyze_records([record()])["workloads"][0]

    assert workload["name"] == "train"
    assert "cpu_over_requested" in workload["findings"]
    assert "memory_over_requested" in workload["findings"]
    assert workload["utilization"]["cpu_ratio"] == pytest.approx(0.1)
    assert workload["recommendation"]["cpu_request"] == "570m"


def test_flags_under_requested_memory():
    report = analyze_records(
        [record(requested_memory_bytes=1024**3, peak_memory_bytes=2 * 1024**3)]
    )

    assert "memory_under_requested" in report["workloads"][0]["findings"]


def test_flags_missing_requests():
    findings = analyze_records(
        [record(requested_cpu_cores=0.0, requested_memory_bytes=0)]
    )["workloads"][0]["findings"]

    assert "no_cpu_requests_set" in findings
    assert "no_memory_requests_set" in findings


def test_declines_to_recommend_without_usage():
    workload = analyze_records(
        [record(used_cpu_cores=None, peak_memory_bytes=None)]
    )["workloads"][0]

    assert "usage_unavailable" in workload["findings"]
    assert workload["recommendation"] is None


def test_oom_is_flagged_and_blocks_memory_downsizing():
    workload = analyze_records(
        [record(failed_oom=True, state="OUT_OF_MEMORY")]
    )["workloads"][0]

    assert "oom_killed" in workload["findings"]
    assert "memory_over_requested" not in workload["findings"]
    assert (
        workload["recommendation"]["memory_request_bytes"]
        >= workload["requests"]["memory_bytes"]
    )


def test_idle_gpu_is_flagged_and_recommended_to_zero():
    workload = analyze_records(
        [record(requested_gpu_count=4, used_gpu_utilization=0.0)]
    )["workloads"][0]

    assert "gpu_idle" in workload["findings"]
    assert workload["recommendation"]["gpu_count"] == 0


def test_underutilized_gpu_is_flagged_and_downsized():
    workload = analyze_records(
        [record(requested_gpu_count=4, used_gpu_utilization=0.10)]
    )["workloads"][0]

    assert "gpu_over_requested" in workload["findings"]
    assert workload["recommendation"]["gpu_count"] == 1


def test_well_used_gpu_is_not_flagged():
    findings = analyze_records(
        [record(requested_gpu_count=2, used_gpu_utilization=0.85)]
    )["workloads"][0]["findings"]

    assert "gpu_over_requested" not in findings
    assert "gpu_idle" not in findings


def test_repeated_runs_aggregate_using_peak_memory():
    workload = analyze_records(
        [
            record(record_id="1", peak_memory_bytes=2 * 1024**3),
            record(record_id="2", peak_memory_bytes=6 * 1024**3),
        ]
    )["workloads"][0]

    assert workload["sample_count"] == 2
    assert workload["usage"]["peak_memory_bytes"] == 6 * 1024**3


def test_distinct_workloads_are_not_merged():
    report = analyze_records(
        [record(workload_name="train"), record(workload_name="infer")]
    )

    assert sorted(w["name"] for w in report["workloads"]) == ["infer", "train"]


def test_summary_totals_reclaimable_resources():
    summary = analyze_records(
        [record(instance_count=2, requested_gpu_count=4, used_gpu_utilization=0.05)]
    )["summary"]

    assert summary["workload_count"] == 1
    assert summary["requested_cpu_cores"] == pytest.approx(8.0)
    assert summary["reclaimable_cpu_cores"] > 0
    assert summary["reclaimable_gpu_count"] > 0


def test_empty_input_produces_empty_report():
    report = analyze_records([])

    assert report["workloads"] == []
    assert report["summary"]["workload_count"] == 0
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest test/test_rightsizing.py -q
```

Expected: `ImportError: cannot import name 'analyze_records'`

- [ ] **Step 3: Append the engine to `tools/rightsizing.py`**

```python
# Below this mean utilization a GPU allocation is treated as idle rather
# than merely oversized — the distinction matters because an idle GPU job
# should move to a CPU-only partition, not just request fewer GPUs.
GPU_IDLE_UTILIZATION = 0.05


def _ratio(used: Optional[float], requested: float) -> Optional[float]:
    """Return used/requested, or None when either side is unknown."""

    if used is None or not requested:
        return None

    return used / requested


def _mean_of(values: List[float]) -> Optional[float]:
    """Mean of the measured values, or None when nothing was measured."""

    return sum(values) / len(values) if values else None


def analyze_records(
    records: Iterable[ResourceRecord],
    *,
    cpu_target_utilization: float = DEFAULT_CPU_TARGET_UTILIZATION,
    memory_target_utilization: float = DEFAULT_MEMORY_TARGET_UTILIZATION,
    gpu_target_utilization: float = DEFAULT_GPU_TARGET_UTILIZATION,
) -> Dict[str, Any]:
    """Compare requested against actual resources for every workload.

    Records sharing a ``group_key`` are aggregated: requests, CPU usage and
    GPU utilization are averaged across runs, memory takes the observed
    **peak**, and an OOM in any run marks the whole workload.
    """

    for target in (
        cpu_target_utilization,
        memory_target_utilization,
        gpu_target_utilization,
    ):
        _require_target(target)

    grouped: Dict[tuple[str, str, str], List[ResourceRecord]] = {}

    for entry in records:
        grouped.setdefault(entry.group_key, []).append(entry)

    workloads: List[Dict[str, Any]] = []

    totals = {
        "requested_cpu_cores": 0.0,
        "requested_memory_bytes": 0,
        "requested_gpu_count": 0.0,
        "used_cpu_cores": 0.0,
        "reclaimable_cpu_cores": 0.0,
        "reclaimable_memory_bytes": 0,
        "reclaimable_gpu_count": 0.0,
    }
    any_usage = False

    for (source, group, name), entries in sorted(grouped.items()):
        sample_count = len(entries)
        instance_count = max(entry.instance_count for entry in entries)

        requested_cpu = sum(
            entry.requested_cpu_cores for entry in entries
        ) / sample_count
        requested_memory = int(
            sum(entry.requested_memory_bytes for entry in entries)
            / sample_count
        )
        requested_gpus = sum(
            entry.requested_gpu_count for entry in entries
        ) / sample_count

        used_cpu = _mean_of(
            [e.used_cpu_cores for e in entries if e.used_cpu_cores is not None]
        )
        memory_samples = [
            e.peak_memory_bytes
            for e in entries
            if e.peak_memory_bytes is not None
        ]
        peak_memory = max(memory_samples) if memory_samples else None
        gpu_utilization = _mean_of(
            [
                e.used_gpu_utilization
                for e in entries
                if e.used_gpu_utilization is not None
            ]
        )

        effective_gpus = (
            gpu_utilization * requested_gpus
            if gpu_utilization is not None and requested_gpus
            else None
        )

        failed_oom = any(entry.failed_oom for entry in entries)
        states = sorted({entry.state for entry in entries})

        has_usage = any(
            value is not None
            for value in (used_cpu, peak_memory, gpu_utilization)
        )
        any_usage = any_usage or has_usage

        cpu_ratio = _ratio(used_cpu, requested_cpu)
        memory_ratio = _ratio(peak_memory, requested_memory)

        findings: List[str] = []

        if not requested_cpu:
            findings.append("no_cpu_requests_set")

        if not requested_memory:
            findings.append("no_memory_requests_set")

        if failed_oom:
            findings.append("oom_killed")

        if not has_usage:
            findings.append("usage_unavailable")
            recommendation = None
        else:
            if cpu_ratio is not None:
                if cpu_ratio < cpu_target_utilization:
                    findings.append("cpu_over_requested")
                elif cpu_ratio > 1.0:
                    findings.append("cpu_under_requested")

            if memory_ratio is not None:
                # An OOM-killed workload is under-sized by definition no
                # matter what the sampled peak says — the sample is
                # truncated at the moment the kernel intervened.
                if failed_oom or memory_ratio > 1.0:
                    findings.append("memory_under_requested")
                elif memory_ratio < memory_target_utilization:
                    findings.append("memory_over_requested")

            if gpu_utilization is not None and requested_gpus:
                if gpu_utilization <= GPU_IDLE_UTILIZATION:
                    findings.append("gpu_idle")
                elif gpu_utilization < gpu_target_utilization:
                    findings.append("gpu_over_requested")

            recommended_cpu = _recommended_cpu_cores(
                used_cpu or 0.0, cpu_target_utilization
            )
            recommended_memory = _recommended_memory_bytes(
                peak_memory or 0, memory_target_utilization
            )

            if failed_oom:
                recommended_memory = max(recommended_memory, requested_memory)

            recommended_gpus = (
                _recommended_gpu_count(effective_gpus, gpu_target_utilization)
                if effective_gpus is not None
                else int(requested_gpus)
            )

            recommendation = {
                "cpu_request": to_cpu_quantity(recommended_cpu),
                "memory_request": to_memory_quantity(recommended_memory),
                "cpu_request_cores": recommended_cpu,
                "memory_request_bytes": recommended_memory,
                "gpu_count": recommended_gpus,
                "rationale": _rationale(
                    used_cpu=used_cpu,
                    peak_memory=peak_memory,
                    gpu_utilization=gpu_utilization,
                    requested_cpu=requested_cpu,
                    requested_memory=requested_memory,
                    requested_gpus=requested_gpus,
                    sample_count=sample_count,
                    cpu_target=cpu_target_utilization,
                    memory_target=memory_target_utilization,
                    failed_oom=failed_oom,
                ),
            }

            totals["reclaimable_cpu_cores"] += max(
                (requested_cpu - recommended_cpu) * instance_count, 0.0
            )
            totals["reclaimable_memory_bytes"] += max(
                (requested_memory - recommended_memory) * instance_count, 0
            )
            totals["reclaimable_gpu_count"] += max(
                (requested_gpus - recommended_gpus) * instance_count, 0.0
            )

        totals["requested_cpu_cores"] += requested_cpu * instance_count
        totals["requested_memory_bytes"] += requested_memory * instance_count
        totals["requested_gpu_count"] += requested_gpus * instance_count

        if used_cpu is not None:
            totals["used_cpu_cores"] += used_cpu * instance_count

        workloads.append(
            {
                "source": source,
                "group": group or None,
                "name": name,
                "sample_count": sample_count,
                "instance_count": instance_count,
                "states": states,
                "requests": {
                    "cpu_cores": requested_cpu,
                    "memory_bytes": requested_memory,
                    "gpu_count": requested_gpus,
                },
                "usage": {
                    "cpu_cores": used_cpu,
                    "peak_memory_bytes": peak_memory,
                    "gpu_utilization": gpu_utilization,
                    "effective_gpus_used": effective_gpus,
                },
                "utilization": {
                    "cpu_ratio": cpu_ratio,
                    "memory_ratio": memory_ratio,
                    "gpu_ratio": gpu_utilization,
                },
                "findings": findings,
                "recommendation": recommendation,
            }
        )

    return {
        "status": "ok",
        "usage_available": any_usage,
        "targets": {
            "cpu_utilization": cpu_target_utilization,
            "memory_utilization": memory_target_utilization,
            "gpu_utilization": gpu_target_utilization,
        },
        "summary": {"workload_count": len(workloads), **totals},
        "workloads": workloads,
    }


def _rationale(
    *,
    used_cpu: Optional[float],
    peak_memory: Optional[int],
    gpu_utilization: Optional[float],
    requested_cpu: float,
    requested_memory: int,
    requested_gpus: float,
    sample_count: int,
    cpu_target: float,
    memory_target: float,
    failed_oom: bool,
) -> str:
    """Build the human-readable evidence string for a recommendation."""

    parts = [
        f"Observed {used_cpu or 0:.3f} cores and "
        f"{(peak_memory or 0) / _MEBIBYTE:.0f}Mi peak memory across "
        f"{sample_count} run(s), against requests of "
        f"{requested_cpu:.3f} cores and "
        f"{requested_memory / _MEBIBYTE:.0f}Mi."
    ]

    if gpu_utilization is not None and requested_gpus:
        parts.append(
            f"GPU utilization averaged {gpu_utilization:.0%} across "
            f"{requested_gpus:.0f} allocated GPU(s)."
        )

    parts.append(
        f"Sized for {cpu_target:.0%} CPU and {memory_target:.0%} memory "
        f"utilization."
    )

    if failed_oom:
        parts.append(
            "Memory held at the current request because this workload was "
            "OOM-killed."
        )

    return " ".join(parts)
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m pytest test/test_rightsizing.py -q
```

Expected: 20 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/resource_records.py tools/rightsizing.py test/test_resource_records.py test/test_rightsizing.py && git commit -m "feat: add scheduler-agnostic rightsizing engine with GPU support"
```

---

## Task 5: Bottleneck classification

This is the half of the research question that rightsizing does not answer. Rightsizing says *"this job asks for too much"*; bottleneck analysis says *"this is the resource that actually limits it"* — which is what tells a user whether to change their request, their node class, or their code.

**Files:**
- Create: `tools/bottlenecks.py`
- Test: `test/test_bottlenecks.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_bottlenecks.py`:

```python
import pytest

from tools.bottlenecks import classify_bottlenecks
from tools.resource_records import NodeSpec, ResourceRecord


def record(**overrides):
    defaults = dict(
        source="slurm",
        workload_name="job",
        group="chem",
        requested_cpu_cores=8.0,
        requested_memory_bytes=32 * 1024**3,
        used_cpu_cores=1.0,
        peak_memory_bytes=4 * 1024**3,
        state="COMPLETED",
        partition="normal",
    )
    defaults.update(overrides)
    return ResourceRecord(**defaults)


def test_idle_gpu_dominates_every_other_constraint():
    # Even with high CPU use, an idle GPU is the headline finding.
    result = classify_bottlenecks(
        [record(requested_gpu_count=4, used_gpu_utilization=0.01, used_cpu_cores=7.5)]
    )

    assert result["workloads"][0]["bottleneck"] == "gpu_idle"


def test_memory_bound_when_memory_saturated_and_cpu_is_not():
    result = classify_bottlenecks(
        [record(used_cpu_cores=0.5, peak_memory_bytes=31 * 1024**3)]
    )

    assert result["workloads"][0]["bottleneck"] == "memory_bound"


def test_cpu_bound_when_cpu_saturated_and_memory_is_not():
    result = classify_bottlenecks(
        [record(used_cpu_cores=7.8, peak_memory_bytes=2 * 1024**3)]
    )

    assert result["workloads"][0]["bottleneck"] == "cpu_bound"


def test_oom_killed_is_memory_bound_regardless_of_sampled_peak():
    result = classify_bottlenecks(
        [record(failed_oom=True, peak_memory_bytes=1 * 1024**3)]
    )

    assert result["workloads"][0]["bottleneck"] == "memory_bound"


def test_balanced_low_utilization_is_oversized():
    result = classify_bottlenecks([record()])

    assert result["workloads"][0]["bottleneck"] == "oversized"


def test_well_matched_job_has_no_bottleneck():
    # Both ratios 0.625 — above the slack mark, below saturation.
    result = classify_bottlenecks(
        [record(used_cpu_cores=5.0, peak_memory_bytes=20 * 1024**3)]
    )

    assert result["workloads"][0]["bottleneck"] == "well_matched"


def test_saturation_on_both_axes_is_reported_as_such():
    result = classify_bottlenecks(
        [record(used_cpu_cores=6.5, peak_memory_bytes=27 * 1024**3)]
    )

    assert result["workloads"][0]["bottleneck"] == "cpu_and_memory_bound"


def test_unmeasured_job_is_reported_as_unknown():
    result = classify_bottlenecks(
        [record(used_cpu_cores=None, peak_memory_bytes=None)]
    )

    assert result["workloads"][0]["bottleneck"] == "unknown"


def test_node_class_mismatch_is_surfaced_when_specs_are_known():
    fat_node = NodeSpec(
        name="qnode0101",
        cpu_cores=64,
        memory_bytes=768 * 1024**3,
        hardware_class="himem",
    )

    result = classify_bottlenecks(
        [
            record(
                used_cpu_cores=7.0,
                peak_memory_bytes=2 * 1024**3,
                node_spec=fat_node,
            )
        ]
    )

    assert "node_class_oversized" in result["workloads"][0]["notes"]


def test_cluster_summary_ranks_bottlenecks_by_frequency():
    result = classify_bottlenecks(
        [
            record(workload_name="a", requested_gpu_count=4, used_gpu_utilization=0.0),
            record(workload_name="b", requested_gpu_count=2, used_gpu_utilization=0.0),
            record(workload_name="c", used_cpu_cores=7.8, peak_memory_bytes=2 * 1024**3),
        ]
    )

    assert result["summary"]["by_bottleneck"]["gpu_idle"] == 2
    assert result["summary"]["top_bottleneck"] == "gpu_idle"


def test_empty_input_produces_empty_summary():
    result = classify_bottlenecks([])

    assert result["workloads"] == []
    assert result["summary"]["top_bottleneck"] is None
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest test/test_bottlenecks.py -q
```

Expected: `ModuleNotFoundError: No module named 'tools.bottlenecks'`

- [ ] **Step 3: Create `tools/bottlenecks.py`**

```python
"""Bottleneck classification over normalized resource records.

Answers "which resource actually limits this work?", as distinct from
rightsizing's "is this work the right size?". A job can be simultaneously
over-requested on CPU and bottlenecked on memory; the two analyses are
reported separately because they imply different remedies — change the
request, change the node class, or change the code.

Pure: no I/O, no scheduler imports.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from .resource_records import ResourceRecord

# A resource is "saturated" above this fraction of its allocation, and
# "slack" below the low-water mark. Both are parameters on the public
# entry point; these are only the documented defaults.
DEFAULT_SATURATION = 0.80
DEFAULT_SLACK = 0.50
DEFAULT_GPU_IDLE = 0.05

# A node whose memory-per-core ratio far exceeds what the work used is a
# placement problem, not a sizing problem.
NODE_CLASS_OVERSIZE_FACTOR = 4.0


def _ratio(used: Optional[float], requested: float) -> Optional[float]:
    if used is None or not requested:
        return None

    return used / requested


def _classify_one(
    entry: ResourceRecord,
    *,
    saturation: float,
    slack: float,
    gpu_idle: float,
) -> Dict[str, Any]:
    """Return the bottleneck verdict and supporting notes for one record."""

    cpu_ratio = _ratio(entry.used_cpu_cores, entry.requested_cpu_cores)
    memory_ratio = _ratio(entry.peak_memory_bytes, entry.requested_memory_bytes)
    gpu_ratio = entry.used_gpu_utilization

    notes: List[str] = []

    # An allocated-but-idle GPU outranks every other finding: it is both
    # the largest waste and the easiest to act on.
    if entry.requested_gpu_count and gpu_ratio is not None:
        if gpu_ratio <= gpu_idle:
            return {
                "bottleneck": "gpu_idle",
                "notes": notes,
                "ratios": {
                    "cpu": cpu_ratio,
                    "memory": memory_ratio,
                    "gpu": gpu_ratio,
                },
            }

    # An OOM kill is definitive evidence of a memory constraint, and
    # outranks the sampled peak, which is truncated at the kill.
    if entry.failed_oom:
        bottleneck = "memory_bound"
    elif cpu_ratio is None and memory_ratio is None and gpu_ratio is None:
        bottleneck = "unknown"
    else:
        cpu_saturated = cpu_ratio is not None and cpu_ratio >= saturation
        memory_saturated = (
            memory_ratio is not None and memory_ratio >= saturation
        )
        gpu_saturated = gpu_ratio is not None and gpu_ratio >= saturation

        cpu_slack = cpu_ratio is not None and cpu_ratio < slack
        memory_slack = memory_ratio is not None and memory_ratio < slack

        if memory_saturated and not cpu_saturated:
            bottleneck = "memory_bound"
        elif cpu_saturated and not memory_saturated:
            bottleneck = "cpu_bound"
        elif gpu_saturated:
            bottleneck = "gpu_bound"
        elif cpu_saturated and memory_saturated:
            bottleneck = "cpu_and_memory_bound"
        elif cpu_slack and memory_slack:
            bottleneck = "oversized"
        else:
            bottleneck = "well_matched"

    # Placement check — only meaningful when layer-3 node specs are joined.
    node_spec = entry.node_spec

    if node_spec is not None and entry.peak_memory_bytes is not None:
        memory_per_core = node_spec.memory_bytes_per_core

        if memory_per_core and entry.requested_cpu_cores:
            used_per_core = (
                entry.peak_memory_bytes / entry.requested_cpu_cores
            )

            if used_per_core * NODE_CLASS_OVERSIZE_FACTOR < memory_per_core:
                notes.append("node_class_oversized")

    if node_spec is not None and not entry.requested_gpu_count:
        if node_spec.gpu_count:
            notes.append("gpu_node_used_without_gpu_request")

    return {
        "bottleneck": bottleneck,
        "notes": notes,
        "ratios": {
            "cpu": cpu_ratio,
            "memory": memory_ratio,
            "gpu": gpu_ratio,
        },
    }


def classify_bottlenecks(
    records: Iterable[ResourceRecord],
    *,
    saturation: float = DEFAULT_SATURATION,
    slack: float = DEFAULT_SLACK,
    gpu_idle: float = DEFAULT_GPU_IDLE,
) -> Dict[str, Any]:
    """Classify the binding constraint for each record and roll up totals."""

    workloads: List[Dict[str, Any]] = []
    counts: Counter[str] = Counter()
    by_partition: Dict[str, Counter] = {}

    for entry in records:
        verdict = _classify_one(
            entry,
            saturation=saturation,
            slack=slack,
            gpu_idle=gpu_idle,
        )

        counts[verdict["bottleneck"]] += 1

        partition = entry.partition or "unknown"
        by_partition.setdefault(partition, Counter())[
            verdict["bottleneck"]
        ] += 1

        workloads.append(
            {
                "source": entry.source,
                "group": entry.group,
                "name": entry.workload_name,
                "partition": entry.partition,
                "state": entry.state,
                **verdict,
            }
        )

    top = counts.most_common(1)

    return {
        "status": "ok",
        "thresholds": {
            "saturation": saturation,
            "slack": slack,
            "gpu_idle": gpu_idle,
        },
        "summary": {
            "record_count": len(workloads),
            "by_bottleneck": dict(counts),
            "by_partition": {
                name: dict(counter) for name, counter in by_partition.items()
            },
            "top_bottleneck": top[0][0] if top else None,
        },
        "workloads": workloads,
    }
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m pytest test/test_bottlenecks.py -q
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/bottlenecks.py test/test_bottlenecks.py && git commit -m "feat: classify workload resource bottlenecks"
```

---

## Task 6: Slurm parsing primitives

**Files:**
- Create: `tools/slurm_source.py`
- Test: `test/test_slurm_source.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_slurm_source.py`:

```python
import pytest

from tools.slurm_source import (
    parse_slurm_duration,
    parse_slurm_memory,
    parse_tres,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00:00:30", 30.0),
        ("02:03:04", 7384.0),
        ("1-02:03:04", 93784.0),
        ("03:04.500", 184.5),
        ("", 0.0),
        ("INVALID", 0.0),
    ],
)
def test_parse_slurm_duration(raw, expected):
    assert parse_slurm_duration(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2048K", 2048 * 1024),
        ("512M", 512 * 1024**2),
        ("1.5G", int(1.5 * 1024**3)),
        ("1T", 1024**4),
        ("4096", 4096),
        ("", 0),
    ],
)
def test_parse_slurm_memory(raw, expected):
    assert parse_slurm_memory(raw) == expected


def test_parse_tres_extracts_cpu_memory_and_nodes():
    parsed = parse_tres("cpu=4,mem=32G,node=1,billing=4")

    assert parsed["cpu"] == pytest.approx(4.0)
    assert parsed["mem"] == 32 * 1024**3
    assert parsed["node"] == pytest.approx(1.0)


def test_parse_tres_extracts_gpu_from_gres():
    parsed = parse_tres("cpu=8,mem=64G,node=1,gres/gpu=4")

    assert parsed["gpu"] == pytest.approx(4.0)


def test_parse_tres_extracts_typed_gpu_gres():
    parsed = parse_tres("cpu=8,mem=64G,gres/gpu:a100=2")

    assert parsed["gpu"] == pytest.approx(2.0)
    assert parsed["gpu_model"] == "a100"


def test_parse_tres_handles_empty_and_malformed_input():
    assert parse_tres("") == {}
    assert parse_tres("garbage") == {}
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest test/test_slurm_source.py -q
```

Expected: `ModuleNotFoundError: No module named 'tools.slurm_source'`

- [ ] **Step 3: Create `tools/slurm_source.py`**

```python
"""Slurm accounting as normalized resource records.

Reads completed-job accounting from ``sacct`` — or from a CSV export with
the same column names, which is how Northwestern Quest data is expected to
arrive. Both paths converge on :func:`records_from_rows`, so CSV replay is
a genuine rehearsal of the live path rather than a separate code path.

Logging here is deliberately aggregate-only: no job ID, user or account is
written above DEBUG, because the dataset is anonymized under a data-use
agreement.
"""

from __future__ import annotations

import csv
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .resource_records import NodeSpec, ResourceRecord

logger = logging.getLogger(__name__)

# Slurm reports memory with binary suffixes and no trailing "B".
_MEMORY_SUFFIXES = {
    "K": 1024,
    "M": 1024**2,
    "G": 1024**3,
    "T": 1024**4,
    "P": 1024**5,
}

_OOM_STATES = {"OUT_OF_MEMORY", "OOM"}

SACCT_FIELDS = (
    "JobID,JobName,State,ExitCode,ReqTRES,AllocTRES,MaxRSS,TotalCPU,"
    "Elapsed,NNodes,NodeList,Partition,QOS,Account"
)


def parse_slurm_memory(raw: str) -> int:
    """Convert a Slurm memory quantity (``2048K``, ``1.5G``) to bytes."""

    text = (raw or "").strip()

    if not text:
        return 0

    multiplier = 1
    if text[-1].upper() in _MEMORY_SUFFIXES:
        multiplier = _MEMORY_SUFFIXES[text[-1].upper()]
        text = text[:-1]

    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


def parse_slurm_duration(raw: str) -> float:
    """Convert a Slurm duration to seconds.

    Handles ``DD-HH:MM:SS``, ``HH:MM:SS`` and ``MM:SS.mmm``.
    """

    text = (raw or "").strip()

    if not text:
        return 0.0

    days = 0.0
    if "-" in text:
        day_part, _, text = text.partition("-")
        try:
            days = float(day_part)
        except ValueError:
            return 0.0

    try:
        values = [float(part) for part in text.split(":")]
    except ValueError:
        return 0.0

    if len(values) == 3:
        hours, minutes, seconds = values
    elif len(values) == 2:
        hours = 0.0
        minutes, seconds = values
    elif len(values) == 1:
        hours = minutes = 0.0
        seconds = values[0]
    else:
        return 0.0

    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_tres(raw: str) -> Dict[str, Any]:
    """Parse a Slurm TRES string such as ``cpu=4,mem=32G,gres/gpu:a100=2``.

    Memory is returned in bytes under ``mem``; GPU count is normalized to
    ``gpu`` regardless of whether the GRES was typed, with any GPU model
    surfaced separately as ``gpu_model``.
    """

    parsed: Dict[str, Any] = {}

    for token in (raw or "").split(","):
        key, separator, value = token.partition("=")

        if not separator:
            continue

        key = key.strip()
        value = value.strip()

        if key == "mem":
            parsed["mem"] = parse_slurm_memory(value)
            continue

        if key.startswith("gres/gpu"):
            _, _, model = key.partition(":")

            if model:
                parsed["gpu_model"] = model

            key = "gpu"

        try:
            parsed[key] = float(re.sub(r"[^0-9.\-]", "", value) or 0)
        except ValueError:
            continue

    return parsed
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m pytest test/test_slurm_source.py -q
```

Expected: 16 passed.

---

## Task 7: Slurm records, CSV replay, and the node-spec join

**Files:**
- Modify: `tools/slurm_source.py`
- Create: `test/fixtures/quest_jobs_sample.csv`, `test/fixtures/quest_nodes_sample.csv`
- Test: `test/test_slurm_source.py`

- [ ] **Step 1: Create the layer-2 fixture**

`test/fixtures/quest_jobs_sample.csv`. Every TRES field is **quoted** — its internal commas are what corrupts `origin/v2-optimizations`'s `mock_jobs.csv`. Columns are exactly `SACCT_FIELDS`. The mix mirrors the proposal's Phase 1 sampling: GPU-underutilized, GPU-heavy, CPU-heavy, memory-intensive, multi-node, and OOM.

```csv
JobID,JobName,State,ExitCode,ReqTRES,AllocTRES,MaxRSS,TotalCPU,Elapsed,NNodes,NodeList,Partition,QOS,Account
1001,md_sim,COMPLETED,0:0,"cpu=4,mem=32G,node=1","cpu=4,mem=32G,node=1",2100000K,01:12:00,02:00:00,1,qnode0101,normal,normal,acct_a
1002,md_sim,COMPLETED,0:0,"cpu=4,mem=32G,node=1","cpu=4,mem=32G,node=1",2400000K,01:20:00,02:10:00,1,qnode0101,normal,normal,acct_a
1003,train_net,OUT_OF_MEMORY,0:125,"cpu=8,mem=16G,node=1,gres/gpu=1","cpu=8,mem=16G,node=1,gres/gpu=1",16700000K,00:40:00,00:45:00,1,qgpu0201,gpu,normal,acct_b
1004,train_net,COMPLETED,0:0,"cpu=8,mem=64G,node=1,gres/gpu=1","cpu=8,mem=64G,node=1,gres/gpu=1",41900000K,05:30:00,03:00:00,1,qgpu0201,gpu,normal,acct_b
1005,preprocess,COMPLETED,0:0,"cpu=16,mem=128G,node=1","cpu=16,mem=128G,node=1",8300000K,00:50:00,03:00:00,1,qnode0102,normal,normal,acct_a
1006,hyperparam,COMPLETED,0:0,"cpu=16,mem=64G,node=1,gres/gpu=4","cpu=16,mem=64G,node=1,gres/gpu=4",12000000K,02:00:00,08:00:00,1,qgpu0202,gpu,normal,acct_b
1007,mpi_solve,COMPLETED,0:0,"cpu=128,mem=512G,node=4","cpu=128,mem=512G,node=4",98000000K,3-12:00:00,00:50:00,4,"qnode[0103-0106]",normal,normal,acct_c
1008,fit_model,TIMEOUT,0:0,"cpu=8,mem=32G,node=1","cpu=8,mem=32G,node=1",30500000K,15:40:00,02:00:00,1,qnode0102,normal,normal,acct_c
```

- [ ] **Step 2: Create the layer-3 fixture**

`test/fixtures/quest_nodes_sample.csv`:

```csv
NodeName,CPUs,RealMemory,Gres,GpuMemory,HardwareClass,Partition
qnode0101,64,257698037760,,0,standard,normal
qnode0102,64,257698037760,,0,standard,normal
qnode0103,64,549755813888,,0,himem,normal
qnode0104,64,549755813888,,0,himem,normal
qnode0105,64,549755813888,,0,himem,normal
qnode0106,64,549755813888,,0,himem,normal
qgpu0201,52,412316860416,gpu:a100:4,85899345920,gpu,gpu
qgpu0202,52,412316860416,gpu:a100:4,85899345920,gpu,gpu
```

- [ ] **Step 3: Write the failing tests**

Append to `test/test_slurm_source.py`:

```python
from pathlib import Path
from unittest.mock import patch

from tools.slurm_source import (
    load_node_specs,
    query_slurm_jobs,
    records_from_csv,
    records_from_rows,
)

FIXTURES = Path(__file__).parent / "fixtures"
JOBS_CSV = FIXTURES / "quest_jobs_sample.csv"
NODES_CSV = FIXTURES / "quest_nodes_sample.csv"


def test_records_from_csv_reads_quoted_tres_columns():
    records = records_from_csv(str(JOBS_CSV))

    assert len(records) == 8

    first = records[0]
    assert first.workload_name == "md_sim"
    assert first.group == "acct_a"
    assert first.partition == "normal"
    assert first.requested_cpu_cores == pytest.approx(4.0)
    assert first.requested_memory_bytes == 32 * 1024**3
    assert first.peak_memory_bytes == 2100000 * 1024


def test_cpu_usage_is_derived_from_totalcpu_over_elapsed():
    records = records_from_csv(str(JOBS_CSV))

    # 01:12:00 of CPU time over 02:00:00 elapsed is 0.6 cores.
    assert records[0].used_cpu_cores == pytest.approx(0.6)


def test_gpu_request_is_parsed_from_gres():
    records = records_from_csv(str(JOBS_CSV))
    hyperparam = next(r for r in records if r.workload_name == "hyperparam")

    assert hyperparam.requested_gpu_count == pytest.approx(4.0)


def test_out_of_memory_state_sets_the_oom_flag():
    records = records_from_csv(str(JOBS_CSV))
    oom = [entry for entry in records if entry.state == "OUT_OF_MEMORY"]

    assert len(oom) == 1
    assert oom[0].failed_oom is True
    assert oom[0].workload_name == "train_net"


def test_multi_node_job_records_its_node_count():
    records = records_from_csv(str(JOBS_CSV))
    mpi = next(r for r in records if r.workload_name == "mpi_solve")

    assert mpi.instance_count == 4


def test_zero_elapsed_does_not_divide_by_zero():
    rows = [
        {
            "JobID": "1",
            "JobName": "instant",
            "State": "COMPLETED",
            "ReqTRES": "cpu=1,mem=1G",
            "MaxRSS": "1024K",
            "TotalCPU": "00:00:00",
            "Elapsed": "00:00:00",
            "NNodes": "1",
        }
    ]

    assert records_from_rows(rows)[0].used_cpu_cores is None


def test_step_rows_fold_maxrss_into_the_parent_job():
    rows = [
        {
            "JobID": "1001",
            "JobName": "job",
            "State": "COMPLETED",
            "ReqTRES": "cpu=1,mem=1G",
            "MaxRSS": "",
            "TotalCPU": "00:01:00",
            "Elapsed": "00:02:00",
            "NNodes": "1",
        },
        {
            "JobID": "1001.batch",
            "JobName": "batch",
            "State": "COMPLETED",
            "ReqTRES": "",
            "MaxRSS": "500K",
            "TotalCPU": "00:01:00",
            "Elapsed": "00:02:00",
            "NNodes": "1",
        },
    ]

    records = records_from_rows(rows)

    assert len(records) == 1
    assert records[0].peak_memory_bytes == 500 * 1024


def test_load_node_specs_parses_gres_and_memory():
    nodes = load_node_specs(str(NODES_CSV))

    assert nodes["qgpu0201"].gpu_count == 4
    assert nodes["qgpu0201"].gpu_model == "a100"
    assert nodes["qnode0103"].hardware_class == "himem"
    assert nodes["qnode0101"].cpu_cores == 64


def test_node_specs_are_joined_onto_records():
    nodes = load_node_specs(str(NODES_CSV))
    records = records_from_csv(str(JOBS_CSV), node_specs=nodes)

    train = next(r for r in records if r.workload_name == "train_net")

    assert train.node_spec is not None
    assert train.node_spec.hardware_class == "gpu"


def test_node_list_ranges_are_expanded():
    nodes = load_node_specs(str(NODES_CSV))
    records = records_from_csv(str(JOBS_CSV), node_specs=nodes)

    mpi = next(r for r in records if r.workload_name == "mpi_solve")

    assert mpi.nodes == [
        "qnode0103",
        "qnode0104",
        "qnode0105",
        "qnode0106",
    ]


@patch("tools.slurm_source.subprocess.run")
def test_query_slurm_jobs_invokes_sacct_with_parsable_output(mock_run):
    mock_run.return_value.stdout = ""
    mock_run.return_value.returncode = 0

    query_slurm_jobs(days_back=3)

    command = mock_run.call_args[0][0]

    assert command[0] == "sacct"
    assert "--parsable2" in command
    assert "--noconvert" in command
    assert "now-3days" in command


@patch("tools.slurm_source.subprocess.run")
def test_query_slurm_jobs_reports_missing_sacct(mock_run):
    mock_run.side_effect = FileNotFoundError()

    result = query_slurm_jobs()

    assert result["status"] == "unavailable"
    assert "sacct" in result["error"]
```

- [ ] **Step 4: Run to verify failure**

```bash
python3 -m pytest test/test_slurm_source.py -q
```

Expected: `ImportError: cannot import name 'records_from_csv'`

- [ ] **Step 5: Append to `tools/slurm_source.py`**

The step-row handling is the subtle part: `sacct` emits a parent job row plus `.batch` / `.extern` step rows, and **`MaxRSS` is only populated on the steps**. `origin/v2-optimizations` sidesteps this by calling `sstat`, which only works while a job is running. We fold step memory back into the parent.

```python
def expand_node_list(raw: str) -> List[str]:
    """Expand a Slurm hostlist such as ``qnode[0103-0106]``.

    Handles the bracketed range and comma forms Slurm emits in NodeList.
    """

    text = (raw or "").strip().strip('"')

    if not text or text in {"None assigned", "(null)"}:
        return []

    match = re.match(r"^([^\[]+)\[([^\]]+)\]$", text)

    if not match:
        return [part for part in text.split(",") if part]

    prefix, body = match.groups()
    names: List[str] = []

    for part in body.split(","):
        if "-" in part:
            start, _, end = part.partition("-")
            width = len(start)

            for value in range(int(start), int(end) + 1):
                names.append(f"{prefix}{value:0{width}d}")
        else:
            names.append(f"{prefix}{part}")

    return names


def load_node_specs(csv_path: str) -> Dict[str, NodeSpec]:
    """Load layer-3 node specifications keyed by node name."""

    path = Path(csv_path)

    if not path.exists():
        raise FileNotFoundError(f"Node spec file not found: {csv_path}")

    specs: Dict[str, NodeSpec] = {}

    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("NodeName") or "").strip()

            if not name:
                continue

            gpu_count = 0
            gpu_model: Optional[str] = None
            gres = (row.get("Gres") or "").strip()

            if gres.startswith("gpu"):
                parts = gres.split(":")

                if len(parts) == 3:
                    gpu_model = parts[1]
                    gpu_count = int(float(parts[2]))
                elif len(parts) == 2:
                    gpu_count = int(float(parts[1]))

            def as_int(value: Optional[str]) -> int:
                try:
                    return int(float(value or 0))
                except ValueError:
                    return 0

            specs[name] = NodeSpec(
                name=name,
                cpu_cores=as_int(row.get("CPUs")),
                memory_bytes=as_int(row.get("RealMemory")),
                gpu_count=gpu_count,
                gpu_model=gpu_model,
                gpu_memory_bytes=as_int(row.get("GpuMemory")),
                hardware_class=(row.get("HardwareClass") or "").strip() or None,
                partition=(row.get("Partition") or "").strip() or None,
            )

    logger.info("Loaded %d node specifications", len(specs))

    return specs


def _is_step_row(job_id: str) -> bool:
    """Return True for ``sacct`` step rows such as ``1001.batch``."""

    return "." in job_id


def records_from_rows(
    rows: Iterable[Dict[str, str]],
    node_specs: Optional[Dict[str, NodeSpec]] = None,
) -> List[ResourceRecord]:
    """Normalize ``sacct``-shaped rows into resource records.

    Accepts rows from either ``sacct --parsable2`` or a CSV export with the
    same column names. Step rows (``1001.batch``) are not emitted as
    records; their ``MaxRSS`` folds into the parent job, because that is
    the only place Slurm reports peak memory for a completed job.
    """

    parents: Dict[str, Dict[str, str]] = {}
    step_peak_memory: Dict[str, int] = {}
    order: List[str] = []

    for row in rows:
        job_id = (row.get("JobID") or "").strip()

        if not job_id:
            continue

        memory = parse_slurm_memory(row.get("MaxRSS", ""))
        parent = job_id.split(".", 1)[0]

        if memory:
            step_peak_memory[parent] = max(
                step_peak_memory.get(parent, 0), memory
            )

        if _is_step_row(job_id):
            continue

        if job_id not in parents:
            order.append(job_id)

        parents[job_id] = row

    records: List[ResourceRecord] = []

    for job_id in order:
        row = parents[job_id]

        requested = parse_tres(row.get("ReqTRES", ""))
        allocated = parse_tres(row.get("AllocTRES", ""))

        requested_cpu = float(requested.get("cpu", allocated.get("cpu", 0.0)))
        requested_memory = int(requested.get("mem", allocated.get("mem", 0)))
        requested_gpus = float(requested.get("gpu", allocated.get("gpu", 0.0)))

        elapsed = parse_slurm_duration(row.get("Elapsed", ""))
        total_cpu = parse_slurm_duration(row.get("TotalCPU", ""))

        # Average cores actually consumed — the quantity `seff` reports as
        # CPU efficiency, before dividing by the allocation.
        used_cpu = total_cpu / elapsed if elapsed > 0 else None

        state = (row.get("State") or "UNKNOWN").strip().split(" ")[0]
        nodes = expand_node_list(row.get("NodeList", ""))

        try:
            node_count = int(float(row.get("NNodes") or 1))
        except ValueError:
            node_count = 1

        node_spec = None
        if node_specs and nodes:
            node_spec = node_specs.get(nodes[0])

        records.append(
            ResourceRecord(
                source="slurm",
                record_id=job_id,
                workload_name=(row.get("JobName") or job_id).strip(),
                group=(row.get("Account") or "").strip() or None,
                requested_cpu_cores=requested_cpu,
                requested_memory_bytes=requested_memory,
                requested_gpu_count=requested_gpus,
                instance_count=node_count,
                used_cpu_cores=used_cpu,
                peak_memory_bytes=step_peak_memory.get(job_id),
                state=state,
                failed_oom=state in _OOM_STATES,
                elapsed_seconds=elapsed,
                partition=(row.get("Partition") or "").strip() or None,
                qos=(row.get("QOS") or "").strip() or None,
                nodes=nodes,
                node_spec=node_spec,
                labels={
                    "exit_code": (row.get("ExitCode") or "").strip(),
                    "gpu_model": requested.get("gpu_model"),
                },
            )
        )

    logger.info("Normalized %d Slurm job records", len(records))

    return records


def records_from_csv(
    csv_path: str,
    node_specs: Optional[Dict[str, NodeSpec]] = None,
) -> List[ResourceRecord]:
    """Load resource records from a ``sacct``-shaped CSV export.

    This is the path Quest data is expected to arrive on. TRES columns
    contain commas, so the export must be properly quoted.
    """

    path = Path(csv_path)

    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with path.open(newline="", encoding="utf-8") as handle:
        return records_from_rows(list(csv.DictReader(handle)), node_specs)


def query_slurm_jobs(
    days_back: int = 7,
    account: Optional[str] = None,
    partition: Optional[str] = None,
) -> Dict[str, Any]:
    """Return recent completed jobs from ``sacct`` as resource records.

    Returns ``{"status": "unavailable"}`` rather than raising when Slurm is
    absent, so a non-cluster machine degrades cleanly.
    """

    command = [
        "sacct",
        "-S",
        f"now-{days_back}days",
        "--format",
        SACCT_FIELDS,
        "--parsable2",
        "--noheader",
        # Keep Slurm from rounding memory units, so parse_slurm_memory is
        # exact rather than reading a pre-rounded "16G".
        "--noconvert",
    ]

    if account:
        command.extend(["-A", account])

    if partition:
        command.extend(["-r", partition])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError:
        return {
            "status": "unavailable",
            "error": (
                "sacct is not available on this machine — Slurm accounting "
                "can only be queried from a cluster login node."
            ),
            "records": [],
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "unavailable",
            "error": "sacct timed out after 120 seconds.",
            "records": [],
        }

    if completed.returncode != 0:
        return {
            "status": "unavailable",
            "error": f"sacct failed: {completed.stderr.strip()}",
            "records": [],
        }

    columns = SACCT_FIELDS.split(",")
    rows = [
        dict(zip(columns, line.split("|")))
        for line in completed.stdout.splitlines()
        if line.strip()
    ]

    return {"status": "ok", "records": records_from_rows(rows)}
```

- [ ] **Step 6: Run to verify pass**

```bash
python3 -m pytest test/test_slurm_source.py -q
```

Expected: 28 passed.

- [ ] **Step 7: Verify the end-to-end fixture path**

```bash
python3 -c "
from tools.slurm_source import load_node_specs, records_from_csv
from tools.rightsizing import analyze_records
from tools.bottlenecks import classify_bottlenecks
nodes = load_node_specs('test/fixtures/quest_nodes_sample.csv')
records = records_from_csv('test/fixtures/quest_jobs_sample.csv', node_specs=nodes)
print('bottlenecks:', classify_bottlenecks(records)['summary']['by_bottleneck'])
for w in analyze_records(records)['workloads']:
    print(f\"{w['name']:12} {w['findings']}\")
"
```

Expected: `md_sim` and `preprocess` flagged `cpu_over_requested` + `memory_over_requested`; `train_net` flagged `oom_killed`; the bottleneck summary dominated by `oversized`.

- [ ] **Step 8: Commit**

```bash
git add tools/slurm_source.py test/test_slurm_source.py test/fixtures/ && git commit -m "feat: read Slurm accounting, CSV exports and node specs as resource records"
```

---

## Task 8: Jobstats time-series

Layer 1. This is what makes the analysis better than `sacct` alone — per-interval samples reveal whether a job used its allocation steadily or in a brief spike.

**Files:**
- Create: `tools/jobstats_source.py`
- Create: `test/fixtures/quest_jobstats_sample.json`
- Test: `test/test_jobstats_source.py`

- [ ] **Step 1: Create the fixture**

`test/fixtures/quest_jobstats_sample.json`. The exact Quest export schema is not yet known, so this models the shape Jobstats produces — per-job, per-interval, per-node series — and the loader is written so a column remap is the only change needed later:

```json
{
  "jobs": [
    {
      "job_id": "1006",
      "interval_seconds": 300,
      "nodes": ["qgpu0202"],
      "samples": [
        {"offset": 0,    "cpu_cores": 2.1, "memory_bytes": 8589934592,  "gpu_utilization": 4,  "gpu_memory_bytes": 2147483648},
        {"offset": 300,  "cpu_cores": 2.4, "memory_bytes": 10737418240, "gpu_utilization": 6,  "gpu_memory_bytes": 2147483648},
        {"offset": 600,  "cpu_cores": 2.2, "memory_bytes": 12884901888, "gpu_utilization": 3,  "gpu_memory_bytes": 2147483648},
        {"offset": 900,  "cpu_cores": 15.8,"memory_bytes": 12884901888, "gpu_utilization": 5,  "gpu_memory_bytes": 2147483648}
      ]
    },
    {
      "job_id": "1004",
      "interval_seconds": 300,
      "nodes": ["qgpu0201"],
      "samples": [
        {"offset": 0,   "cpu_cores": 7.2, "memory_bytes": 34359738368, "gpu_utilization": 91, "gpu_memory_bytes": 42949672960},
        {"offset": 300, "cpu_cores": 7.6, "memory_bytes": 42949672960, "gpu_utilization": 94, "gpu_memory_bytes": 45097156608}
      ]
    }
  ]
}
```

Job `1006` (`hyperparam`) holds 4 GPUs at ~4% utilization with one brief CPU spike — the case `sacct` alone cannot distinguish from steady use. Job `1004` is genuinely GPU-bound.

- [ ] **Step 2: Write the failing tests**

Create `test/test_jobstats_source.py`:

```python
from pathlib import Path

import pytest

from tools.jobstats_source import (
    load_jobstats,
    summarize_samples,
    attach_jobstats,
)
from tools.resource_records import ResourceRecord, UsageSample

FIXTURE = Path(__file__).parent / "fixtures" / "quest_jobstats_sample.json"


def test_load_jobstats_returns_samples_keyed_by_job_id():
    series = load_jobstats(str(FIXTURE))

    assert set(series) == {"1006", "1004"}
    assert len(series["1006"]) == 4
    assert isinstance(series["1006"][0], UsageSample)


def test_gpu_utilization_percentages_are_normalized_to_fractions():
    series = load_jobstats(str(FIXTURE))

    # The fixture reports 4 (percent); we store 0.04.
    assert series["1006"][0].gpu_utilization == pytest.approx(0.04)


def test_summarize_samples_reports_mean_peak_and_p95():
    # 19 steady samples plus one spike. Nearest-rank p95 over n=20 selects
    # index 18, so the lone spike at index 19 must not reach it — with
    # fewer than ~20 samples p95 and peak coincide, which is why this
    # fixture is sized the way it is.
    samples = [
        UsageSample(offset_seconds=float(i), cpu_cores=1.0) for i in range(19)
    ] + [UsageSample(offset_seconds=19.0, cpu_cores=9.0)]

    summary = summarize_samples(samples)

    assert summary["cpu_mean"] == pytest.approx(1.4)
    assert summary["cpu_peak"] == pytest.approx(9.0)
    # p95 must not be dragged to the peak by a single spike.
    assert summary["cpu_p95"] == pytest.approx(1.0)


def test_summarize_empty_samples_returns_none_fields():
    summary = summarize_samples([])

    assert summary["cpu_mean"] is None
    assert summary["memory_peak"] is None


def test_attach_jobstats_overrides_scalar_summaries():
    record = ResourceRecord(
        source="slurm",
        record_id="1006",
        workload_name="hyperparam",
        requested_cpu_cores=16,
        requested_memory_bytes=64 * 1024**3,
        requested_gpu_count=4,
        used_cpu_cores=0.5,
    )

    attach_jobstats([record], load_jobstats(str(FIXTURE)))

    assert record.samples
    # Mean of 2.1, 2.4, 2.2, 15.8
    assert record.used_cpu_cores == pytest.approx(5.625)
    assert record.peak_cpu_cores == pytest.approx(15.8)
    assert record.used_gpu_utilization == pytest.approx(0.045)
    assert record.peak_memory_bytes == 12884901888


def test_attach_jobstats_leaves_unmatched_records_untouched():
    record = ResourceRecord(
        source="slurm",
        record_id="9999",
        workload_name="orphan",
        requested_cpu_cores=1,
        requested_memory_bytes=1024,
        used_cpu_cores=0.5,
    )

    attach_jobstats([record], load_jobstats(str(FIXTURE)))

    assert record.samples == []
    assert record.used_cpu_cores == pytest.approx(0.5)
```

- [ ] **Step 3: Run to verify failure**

```bash
python3 -m pytest test/test_jobstats_source.py -q
```

Expected: `ModuleNotFoundError: No module named 'tools.jobstats_source'`

- [ ] **Step 4: Create `tools/jobstats_source.py`**

```python
"""Per-interval job time-series (layer 1 of the Quest dataset).

Jobstats-style samples reveal usage *shape*, which accounting summaries
cannot: a job averaging 4 cores may have used 16 for five minutes and 1 for
three hours. Attaching samples to a record replaces its scalar summaries
with statistics derived from the series.

The exact Quest export schema is not yet fixed. :data:`FIELD_ALIASES` is
the single place to remap column names when it is.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .resource_records import ResourceRecord, UsageSample

logger = logging.getLogger(__name__)

# Remap here when the Quest export schema is confirmed.
FIELD_ALIASES = {
    "offset": ("offset", "offset_seconds", "t", "timestamp"),
    "cpu_cores": ("cpu_cores", "cpus", "cpu"),
    "memory_bytes": ("memory_bytes", "mem", "rss"),
    "gpu_utilization": ("gpu_utilization", "gpu_util", "gpu"),
    "gpu_memory_bytes": ("gpu_memory_bytes", "gpu_mem"),
}


def _first_present(row: Dict[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]

    return None


def _as_fraction(value: Any) -> Optional[float]:
    """Normalize a GPU utilization reading to a 0.0–1.0 fraction.

    Jobstats reports percentages; values above 1 are divided by 100.
    """

    if value is None:
        return None

    number = float(value)

    return number / 100.0 if number > 1.0 else number


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile — no interpolation, no numpy dependency."""

    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * len(ordered)) - 1))

    return ordered[index]


def load_jobstats(path: str) -> Dict[str, List[UsageSample]]:
    """Load per-job time-series keyed by job ID."""

    source = Path(path)

    if not source.exists():
        raise FileNotFoundError(f"Jobstats file not found: {path}")

    payload = json.loads(source.read_text(encoding="utf-8"))
    series: Dict[str, List[UsageSample]] = {}

    for job in payload.get("jobs", []):
        job_id = str(job.get("job_id", "")).strip()

        if not job_id:
            continue

        nodes = job.get("nodes") or []
        samples: List[UsageSample] = []

        for row in job.get("samples", []):
            memory = _first_present(row, FIELD_ALIASES["memory_bytes"])
            gpu_memory = _first_present(row, FIELD_ALIASES["gpu_memory_bytes"])
            cpu = _first_present(row, FIELD_ALIASES["cpu_cores"])

            samples.append(
                UsageSample(
                    offset_seconds=float(
                        _first_present(row, FIELD_ALIASES["offset"]) or 0
                    ),
                    cpu_cores=float(cpu) if cpu is not None else None,
                    memory_bytes=int(memory) if memory is not None else None,
                    gpu_utilization=_as_fraction(
                        _first_present(row, FIELD_ALIASES["gpu_utilization"])
                    ),
                    gpu_memory_bytes=(
                        int(gpu_memory) if gpu_memory is not None else None
                    ),
                    node=nodes[0] if nodes else None,
                )
            )

        series[job_id] = samples

    logger.info("Loaded time-series for %d jobs", len(series))

    return series


def summarize_samples(samples: Iterable[UsageSample]) -> Dict[str, Any]:
    """Derive mean / p95 / peak statistics from a usage series."""

    samples = list(samples)

    def stats(values: List[float]) -> tuple:
        if not values:
            return (None, None, None)

        return (mean(values), _percentile(values, 0.95), max(values))

    cpu_mean, cpu_p95, cpu_peak = stats(
        [s.cpu_cores for s in samples if s.cpu_cores is not None]
    )
    memory_mean, memory_p95, memory_peak = stats(
        [s.memory_bytes for s in samples if s.memory_bytes is not None]
    )
    gpu_mean, gpu_p95, gpu_peak = stats(
        [s.gpu_utilization for s in samples if s.gpu_utilization is not None]
    )
    _, _, gpu_memory_peak = stats(
        [s.gpu_memory_bytes for s in samples if s.gpu_memory_bytes is not None]
    )

    return {
        "sample_count": len(samples),
        "cpu_mean": cpu_mean,
        "cpu_p95": cpu_p95,
        "cpu_peak": cpu_peak,
        "memory_mean": memory_mean,
        "memory_p95": memory_p95,
        "memory_peak": memory_peak,
        "gpu_mean": gpu_mean,
        "gpu_p95": gpu_p95,
        "gpu_peak": gpu_peak,
        "gpu_memory_peak": gpu_memory_peak,
    }


def attach_jobstats(
    records: Iterable[ResourceRecord],
    series: Dict[str, List[UsageSample]],
) -> List[ResourceRecord]:
    """Attach time-series to records and refresh their scalar summaries.

    Measured series are strictly better evidence than accounting summaries,
    so they replace ``used_cpu_cores``, ``peak_memory_bytes`` and the GPU
    fields. Records with no matching series are left untouched.
    """

    matched = 0

    for record in records:
        samples = series.get(str(record.record_id or ""))

        if not samples:
            continue

        matched += 1
        record.samples = samples

        summary = summarize_samples(samples)

        if summary["cpu_mean"] is not None:
            record.used_cpu_cores = summary["cpu_mean"]
            record.peak_cpu_cores = summary["cpu_peak"]

        if summary["memory_peak"] is not None:
            record.peak_memory_bytes = int(summary["memory_peak"])

        if summary["gpu_mean"] is not None:
            record.used_gpu_utilization = summary["gpu_mean"]

        if summary["gpu_memory_peak"] is not None:
            record.peak_gpu_memory_bytes = int(summary["gpu_memory_peak"])

    logger.info("Attached time-series to %d records", matched)

    return list(records)
```

- [ ] **Step 5: Run to verify pass**

```bash
python3 -m pytest test/test_jobstats_source.py -q
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add tools/jobstats_source.py test/test_jobstats_source.py test/fixtures/quest_jobstats_sample.json && git commit -m "feat: ingest per-interval job time-series"
```

---

## Task 9: Kubernetes source

Gives the team a live cluster to develop against while Quest access is pending.

**Files:**
- Create: `tools/cluster_usage.py`, `tools/kubernetes_source.py`
- Test: `test/test_cluster_usage.py`, `test/test_kubernetes_source.py`

- [ ] **Step 1: Write the failing tests for the metrics-server client**

Create `test/test_cluster_usage.py`:

```python
from unittest.mock import patch

import pytest
from kubernetes.client.exceptions import ApiException

from tools.cluster_usage import MetricsServerUnavailable, fetch_pod_usage

POD_PAYLOAD = {
    "items": [
        {
            "metadata": {"name": "web-abc123-xyz", "namespace": "prod"},
            "containers": [
                {"name": "app", "usage": {"cpu": "250m", "memory": "512Mi"}},
                {"name": "sidecar", "usage": {"cpu": "50m", "memory": "64Mi"}},
            ],
        }
    ]
}


@patch("tools.cluster_usage.client.CustomObjectsApi")
def test_fetch_pod_usage_sums_containers(mock_api):
    mock_api.return_value.list_cluster_custom_object.return_value = POD_PAYLOAD

    entry = fetch_pod_usage()[("prod", "web-abc123-xyz")]

    assert entry["cpu_cores"] == pytest.approx(0.3)
    assert entry["memory_bytes"] == 576 * 1024**2


@patch("tools.cluster_usage.client.CustomObjectsApi")
def test_fetch_pod_usage_scopes_to_namespace(mock_api):
    mock_api.return_value.list_namespaced_custom_object.return_value = POD_PAYLOAD

    fetch_pod_usage(namespace="prod")

    mock_api.return_value.list_namespaced_custom_object.assert_called_once_with(
        "metrics.k8s.io", "v1beta1", "prod", "pods"
    )


@patch("tools.cluster_usage.client.CustomObjectsApi")
def test_fetch_pod_usage_raises_when_api_missing(mock_api):
    mock_api.return_value.list_cluster_custom_object.side_effect = ApiException(
        status=404, reason="Not Found"
    )

    with pytest.raises(MetricsServerUnavailable, match="metrics.k8s.io"):
        fetch_pod_usage()
```

- [ ] **Step 2: Create `tools/cluster_usage.py`**

Reuses the quantity parsers already at `tools/providers.py:86` and `:100` — do not write new ones. metrics-server reports CPU in nanocores (`n`) and memory in `Ki`, both already handled.

```python
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

from .providers import _parse_cpu_quantity, _parse_memory_quantity

METRICS_GROUP = "metrics.k8s.io"
METRICS_VERSION = "v1beta1"


class MetricsServerUnavailable(RuntimeError):
    """Raised when the metrics.k8s.io API cannot be reached."""


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
        raise MetricsServerUnavailable(
            f"metrics.k8s.io is unavailable "
            f"(status={exc.status}, reason={exc.reason}). "
            f"Install metrics-server to enable usage-based rightsizing."
        ) from exc

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
```

- [ ] **Step 3: Write the failing tests for the Kubernetes source**

Create `test/test_kubernetes_source.py`:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tools.cluster_usage import MetricsServerUnavailable
from tools.kubernetes_source import _workload_identity, records_from_cluster


def owner(kind, name):
    return SimpleNamespace(kind=kind, name=name)


def make_container(*, name="app", cpu_request="1", memory_request="1Gi"):
    requests = {}
    if cpu_request:
        requests["cpu"] = cpu_request
    if memory_request:
        requests["memory"] = memory_request

    return SimpleNamespace(
        name=name,
        resources=SimpleNamespace(requests=requests, limits={}),
    )


def make_pod(
    *,
    name="web-7d9f8b6c5d-x4k2p",
    namespace="prod",
    containers=None,
    owners=None,
    container_statuses=None,
    phase="Running",
    node_name="node-a",
):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            owner_references=owners or [owner("ReplicaSet", "web-7d9f8b6c5d")],
        ),
        spec=SimpleNamespace(
            node_name=node_name,
            containers=containers or [make_container()],
            init_containers=[],
            overhead={},
        ),
        status=SimpleNamespace(
            phase=phase, container_statuses=container_statuses or []
        ),
    )


def oom_status(name="app", restart_count=3):
    return SimpleNamespace(
        name=name,
        restart_count=restart_count,
        last_state=SimpleNamespace(
            terminated=SimpleNamespace(reason="OOMKilled")
        ),
    )


def test_replicaset_owner_resolves_to_deployment():
    assert _workload_identity(make_pod()) == ("prod", "Deployment", "web")


def test_statefulset_owner_used_directly():
    pod = make_pod(namespace="db", owners=[owner("StatefulSet", "postgres")])

    assert _workload_identity(pod) == ("db", "StatefulSet", "postgres")


def test_bare_pod_is_its_own_workload():
    pod = make_pod(name="debug-shell", owners=None)

    assert _workload_identity(pod) == ("prod", "Pod", "debug-shell")


def test_replicaset_name_without_hash_is_left_intact():
    pod = make_pod(owners=[owner("ReplicaSet", "legacy")])

    assert _workload_identity(pod) == ("prod", "Deployment", "legacy")


def test_records_join_requests_with_live_usage():
    provider = MagicMock()
    provider.list_pods.return_value = [make_pod()]

    records = records_from_cluster(
        provider=provider,
        usage_fetcher=lambda namespace=None: {
            ("prod", "web-7d9f8b6c5d-x4k2p"): {
                "cpu_cores": 0.1,
                "memory_bytes": 200 * 1024**2,
            }
        },
    )

    record = records[0]
    assert record.source == "kubernetes"
    assert record.workload_name == "web"
    assert record.requested_cpu_cores == pytest.approx(1.0)
    assert record.used_cpu_cores == pytest.approx(0.1)


def test_pods_of_one_workload_collapse_to_a_single_record():
    provider = MagicMock()
    provider.list_pods.return_value = [
        make_pod(name="web-7d9f8b6c5d-aaaaa"),
        make_pod(name="web-7d9f8b6c5d-bbbbb"),
    ]

    records = records_from_cluster(
        provider=provider,
        usage_fetcher=lambda namespace=None: {
            ("prod", "web-7d9f8b6c5d-aaaaa"): {
                "cpu_cores": 0.1,
                "memory_bytes": 100 * 1024**2,
            },
            ("prod", "web-7d9f8b6c5d-bbbbb"): {
                "cpu_cores": 0.3,
                "memory_bytes": 400 * 1024**2,
            },
        },
    )

    assert len(records) == 1
    assert records[0].instance_count == 2
    assert records[0].used_cpu_cores == pytest.approx(0.2)
    assert records[0].peak_memory_bytes == 400 * 1024**2


def test_oomkilled_container_sets_the_flag():
    provider = MagicMock()
    provider.list_pods.return_value = [
        make_pod(container_statuses=[oom_status()])
    ]

    records = records_from_cluster(
        provider=provider, usage_fetcher=lambda namespace=None: {}
    )

    assert records[0].failed_oom is True


def test_completed_and_unscheduled_pods_are_skipped():
    provider = MagicMock()
    provider.list_pods.return_value = [
        make_pod(name="done", phase="Succeeded"),
        make_pod(name="pending", node_name=None),
    ]

    records = records_from_cluster(
        provider=provider, usage_fetcher=lambda namespace=None: {}
    )

    assert records == []


def test_missing_metrics_server_leaves_usage_unknown():
    provider = MagicMock()
    provider.list_pods.return_value = [make_pod()]

    def usage_fetcher(namespace=None):
        raise MetricsServerUnavailable("metrics.k8s.io is unavailable")

    records = records_from_cluster(
        provider=provider, usage_fetcher=usage_fetcher
    )

    assert records[0].used_cpu_cores is None
    assert records[0].peak_memory_bytes is None
```

- [ ] **Step 4: Create `tools/kubernetes_source.py`**

```python
"""Kubernetes workloads as normalized resource records.

Joins what Pods request (``tools.providers``) with what they actually
consume (``tools.cluster_usage``), grouped by the controller that owns
them — Pods are ephemeral, so recommendations must target the Deployment.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .cluster_usage import MetricsServerUnavailable, fetch_pod_usage
from .providers import KubernetesMetricsProvider, _pod_resource_totals
from .resource_records import ResourceRecord

# Kubernetes builds pod-template-hash from a vowel-free base32 alphabet so
# the generated suffix can never spell a word.
_HASH_ALPHABET = set("0123456789bcdfghjklmnpqrstvwxz")


def _looks_like_pod_template_hash(segment: str) -> bool:
    """Return True when *segment* looks like a generated ReplicaSet hash."""

    return 5 <= len(segment) <= 10 and all(
        character in _HASH_ALPHABET for character in segment
    )


def _strip_pod_template_hash(name: str) -> str:
    """Recover a Deployment name from its ReplicaSet name."""

    head, separator, tail = name.rpartition("-")

    if separator and _looks_like_pod_template_hash(tail):
        return head

    return name


def _workload_identity(pod) -> tuple[str, str, str]:
    """Return ``(namespace, kind, name)`` for the workload owning *pod*."""

    namespace = pod.metadata.namespace or "default"

    for owner in pod.metadata.owner_references or []:
        if owner.kind == "ReplicaSet":
            return (
                namespace,
                "Deployment",
                _strip_pod_template_hash(owner.name),
            )

        return (namespace, owner.kind, owner.name)

    return (namespace, "Pod", pod.metadata.name)


def _was_oom_killed(pod) -> bool:
    """Return True when any container's last termination was an OOM kill."""

    for status in pod.status.container_statuses or []:
        last_state = getattr(status, "last_state", None)
        terminated = getattr(last_state, "terminated", None)

        if terminated is not None and (
            getattr(terminated, "reason", None) == "OOMKilled"
        ):
            return True

    return False


def records_from_cluster(
    *,
    namespace: Optional[str] = None,
    provider: Any = None,
    usage_fetcher: Optional[Callable[..., Dict]] = None,
) -> List[ResourceRecord]:
    """Return one record per Kubernetes workload.

    CPU usage is averaged across a workload's Pods; memory takes the peak.
    When metrics-server is unavailable, usage fields stay ``None`` so the
    engines report the gap instead of estimating it.
    """

    provider = provider or KubernetesMetricsProvider()
    usage_fetcher = usage_fetcher or fetch_pod_usage

    try:
        usage = usage_fetcher(namespace=namespace)
    except MetricsServerUnavailable:
        usage = {}

    grouped: Dict[tuple[str, str, str], Dict[str, Any]] = {}

    for pod in provider.list_pods():
        # Mirror the accounting rules in tools/providers.py: unscheduled
        # and completed Pods hold no resources.
        if not pod.spec.node_name:
            continue

        if pod.status.phase in {"Succeeded", "Failed"}:
            continue

        if namespace and pod.metadata.namespace != namespace:
            continue

        key = _workload_identity(pod)
        bucket = grouped.setdefault(
            key,
            {
                "pod_count": 0,
                "cpu_requests_cores": 0.0,
                "memory_requests_bytes": 0,
                "cpu_samples": [],
                "memory_samples": [],
                "containers": [],
                "failed_oom": False,
            },
        )

        totals = _pod_resource_totals(pod)

        bucket["pod_count"] += 1
        bucket["cpu_requests_cores"] += totals["cpu_requests_cores"]
        bucket["memory_requests_bytes"] += totals["memory_requests_bytes"]
        bucket["failed_oom"] = bucket["failed_oom"] or _was_oom_killed(pod)

        for container in pod.spec.containers or []:
            if container.name not in bucket["containers"]:
                bucket["containers"].append(container.name)

        pod_usage = usage.get((pod.metadata.namespace, pod.metadata.name))

        if pod_usage:
            bucket["cpu_samples"].append(pod_usage["cpu_cores"])
            bucket["memory_samples"].append(pod_usage["memory_bytes"])

    records: List[ResourceRecord] = []

    for (pod_namespace, kind, name), bucket in sorted(grouped.items()):
        pod_count = bucket["pod_count"]
        cpu_samples = bucket["cpu_samples"]
        memory_samples = bucket["memory_samples"]

        records.append(
            ResourceRecord(
                source="kubernetes",
                record_id=f"{pod_namespace}/{name}",
                workload_name=name,
                group=pod_namespace,
                requested_cpu_cores=bucket["cpu_requests_cores"] / pod_count,
                requested_memory_bytes=int(
                    bucket["memory_requests_bytes"] / pod_count
                ),
                instance_count=pod_count,
                used_cpu_cores=(
                    sum(cpu_samples) / len(cpu_samples)
                    if cpu_samples
                    else None
                ),
                peak_memory_bytes=(
                    max(memory_samples) if memory_samples else None
                ),
                state="Running",
                failed_oom=bucket["failed_oom"],
                containers=bucket["containers"],
                labels={"kind": kind},
            )
        )

    return records
```

- [ ] **Step 5: Run to verify pass**

```bash
python3 -m pytest test/test_cluster_usage.py test/test_kubernetes_source.py -q
```

Expected: 12 passed.

- [ ] **Step 6: Commit**

```bash
git add tools/cluster_usage.py tools/kubernetes_source.py test/test_cluster_usage.py test/test_kubernetes_source.py && git commit -m "feat: read Kubernetes workloads and live usage as resource records"
```

---

## Task 10: `apply_resource_requests`

The only new mutating tool. Slurm has no equivalent — job sizing is set at submission, so Slurm output is advisory.

**Files:**
- Modify: `tools/kubernetes_actions.py`, `main.py:332`, `core/semantic_cache.py:21`

- [ ] **Step 1: Append the action to `tools/kubernetes_actions.py`**

```python
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
```

- [ ] **Step 2: Gate it behind human approval**

`main.py:332`:

```python
DANGEROUS_TOOLS = {
    "scale_workload",
    "restart_workload",
    "cordon_node",
    "apply_resource_requests",
}
```

- [ ] **Step 3: Mark the new tools non-cacheable**

In `core/semantic_cache.py`, add `"apply_resource_requests"`, `"recommend_rightsizing"`, `"analyze_bottlenecks"` and `"inspect_cluster_resources"` to `_STATE_CHANGING_TOOLS` (line 21), and drop the now-inaccurate `# future Kubernetes action` comments on the three existing K8s entries. Caching a rightsizing report would serve stale cluster state, and — per the privacy rules above — keeps job-derived data out of the cache entirely.

- [ ] **Step 4: Verify**

```bash
python3 -c "
from tools.kubernetes_actions import apply_resource_requests
from main import DANGEROUS_TOOLS
assert 'apply_resource_requests' in DANGEROUS_TOOLS
print('gated ok')
"
```

Expected: `gated ok`

---

## Task 11: MCP wiring

**Files:**
- Modify: `tools/rightsizing.py`, `tools/__init__.py`, `MCP/tool_schemas.py`, `MCP/tool_executor.py`

- [ ] **Step 1: Add the tool entry points to `tools/rightsizing.py`**

These are the callable surfaces the executor binds to. Imports are lazy so a machine without the `kubernetes` client can still use the Slurm and CSV paths.

```python
def _load_records(
    *,
    source: str,
    namespace: Optional[str],
    csv_path: Optional[str],
    node_csv_path: Optional[str],
    jobstats_path: Optional[str],
    days_back: int,
) -> tuple[List[ResourceRecord], str, List[str]]:
    """Resolve records from whichever source is selected or reachable."""

    errors: List[str] = []

    if source == "csv" or (source == "auto" and csv_path):
        from .slurm_source import load_node_specs, records_from_csv

        if not csv_path:
            raise ValueError("csv_path is required when source='csv'")

        node_specs = load_node_specs(node_csv_path) if node_csv_path else None
        records = records_from_csv(csv_path, node_specs=node_specs)

        if jobstats_path:
            from .jobstats_source import attach_jobstats, load_jobstats

            attach_jobstats(records, load_jobstats(jobstats_path))

        return records, "csv", errors

    if source in {"slurm", "auto"}:
        from .slurm_source import query_slurm_jobs

        result = query_slurm_jobs(days_back=days_back)

        if result["status"] == "ok":
            return result["records"], "slurm", errors

        errors.append(result["error"])

        if source == "slurm":
            return [], "slurm", errors

    try:
        from .kubernetes_source import records_from_cluster

        return records_from_cluster(namespace=namespace), "kubernetes", errors
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller
        errors.append(str(exc))

        if source != "auto":
            raise

    return [], "none", errors


def recommend_rightsizing(
    *,
    source: str = "auto",
    namespace: str | None = None,
    csv_path: str | None = None,
    node_csv_path: str | None = None,
    jobstats_path: str | None = None,
    days_back: int = 7,
    cpu_target_utilization: float = DEFAULT_CPU_TARGET_UTILIZATION,
    memory_target_utilization: float = DEFAULT_MEMORY_TARGET_UTILIZATION,
    gpu_target_utilization: float = DEFAULT_GPU_TARGET_UTILIZATION,
) -> Dict[str, Any]:
    """Analyze rightsizing for whichever scheduler is selected or reachable."""

    records, resolved, errors = _load_records(
        source=source,
        namespace=namespace,
        csv_path=csv_path,
        node_csv_path=node_csv_path,
        jobstats_path=jobstats_path,
        days_back=days_back,
    )

    if resolved == "none":
        return {
            "status": "unavailable",
            "errors": errors,
            "workloads": [],
            "summary": {"workload_count": 0},
        }

    report = analyze_records(
        records,
        cpu_target_utilization=cpu_target_utilization,
        memory_target_utilization=memory_target_utilization,
        gpu_target_utilization=gpu_target_utilization,
    )
    report["source"] = resolved
    report["errors"] = errors

    return report


def analyze_bottlenecks(
    *,
    source: str = "auto",
    namespace: str | None = None,
    csv_path: str | None = None,
    node_csv_path: str | None = None,
    jobstats_path: str | None = None,
    days_back: int = 7,
) -> Dict[str, Any]:
    """Identify the binding resource constraint for each workload."""

    from .bottlenecks import classify_bottlenecks

    records, resolved, errors = _load_records(
        source=source,
        namespace=namespace,
        csv_path=csv_path,
        node_csv_path=node_csv_path,
        jobstats_path=jobstats_path,
        days_back=days_back,
    )

    if resolved == "none":
        return {
            "status": "unavailable",
            "errors": errors,
            "workloads": [],
            "summary": {"record_count": 0, "top_bottleneck": None},
        }

    report = classify_bottlenecks(records)
    report["source"] = resolved
    report["errors"] = errors

    return report


def inspect_cluster_resources(
    *,
    node: str | None = None,
    provider: Any = None,
) -> Dict[str, Any]:
    """Return the Kubernetes node and cluster-level resource snapshot."""

    from .providers import KubernetesMetricsProvider

    provider = provider or KubernetesMetricsProvider()
    snapshot = provider.gather_metrics()

    if node is not None:
        snapshot = {
            **snapshot,
            "nodes": [
                entry for entry in snapshot["nodes"] if entry["name"] == node
            ],
        }

    return snapshot
```

- [ ] **Step 2: Export them**

Append to `tools/__init__.py`:

```python
from .rightsizing import (
    analyze_bottlenecks,
    inspect_cluster_resources,
    recommend_rightsizing,
)
```

- [ ] **Step 3: Add the schemas**

Append to `TOOL_SCHEMAS` in `MCP/tool_schemas.py`, before the closing `]`. The four source-selection properties are shared by the two analysis tools:

```python
    {
        "name": "recommend_rightsizing",
        "description": (
            "Compare what each workload requests against what it actually "
            "consumes, and recommend corrected CPU, memory and GPU sizing. "
            "Works over Slurm job accounting, an exported accounting CSV, or "
            "Kubernetes. Flags over-requested, under-requested, OOM-killed, "
            "idle-GPU and no-requests-set workloads, and reports total "
            "reclaimable resources. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "Which scheduler to read: 'slurm', 'kubernetes', "
                        "'csv' for an exported accounting file, or 'auto' to "
                        "use whichever is reachable."
                    ),
                    "default": "auto",
                },
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to restrict analysis to.",
                },
                "csv_path": {
                    "type": "string",
                    "description": (
                        "Path to a sacct-shaped CSV export. Required when "
                        "source is 'csv'."
                    ),
                },
                "node_csv_path": {
                    "type": "string",
                    "description": (
                        "Optional path to a node-specification CSV, enabling "
                        "node-class fit analysis."
                    ),
                },
                "jobstats_path": {
                    "type": "string",
                    "description": (
                        "Optional path to a Jobstats time-series JSON export, "
                        "which yields more accurate usage than accounting "
                        "summaries alone."
                    ),
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days of Slurm accounting to analyze.",
                    "default": 7,
                },
                "cpu_target_utilization": {
                    "type": "number",
                    "description": "Target CPU utilization ratio. Defaults to 0.7.",
                    "default": 0.7,
                },
                "memory_target_utilization": {
                    "type": "number",
                    "description": "Target memory utilization ratio. Defaults to 0.8.",
                    "default": 0.8,
                },
                "gpu_target_utilization": {
                    "type": "number",
                    "description": "Target GPU utilization ratio. Defaults to 0.7.",
                    "default": 0.7,
                },
            },
            "required": [],
        },
    },
    {
        "name": "analyze_bottlenecks",
        "description": (
            "Identify which resource actually limits each workload — CPU, "
            "memory, GPU, or none — and roll the findings up by partition. "
            "Answers 'where are the bottlenecks in this cluster', as distinct "
            "from recommend_rightsizing's 'is this workload the right size'. "
            "Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "Which scheduler to read: 'slurm', 'kubernetes', "
                        "'csv', or 'auto'."
                    ),
                    "default": "auto",
                },
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to restrict analysis to.",
                },
                "csv_path": {
                    "type": "string",
                    "description": "Path to a sacct-shaped CSV export.",
                },
                "node_csv_path": {
                    "type": "string",
                    "description": "Optional path to a node-specification CSV.",
                },
                "jobstats_path": {
                    "type": "string",
                    "description": "Optional path to a Jobstats time-series JSON export.",
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days of Slurm accounting to analyze.",
                    "default": 7,
                },
            },
            "required": [],
        },
    },
    {
        "name": "inspect_cluster_resources",
        "description": (
            "Read the Kubernetes cluster's node and cluster-wide resource "
            "picture: allocatable, requested and available CPU/memory per "
            "node, pod slots, node readiness and taints. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "Optional node name. Omit for the whole cluster.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "apply_resource_requests",
        "description": (
            "Update the CPU/memory requests and limits of a container in a "
            "Kubernetes deployment. Use the quantity strings returned by "
            "recommend_rightsizing. Requires human approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "The Kubernetes namespace.",
                    "default": "default",
                },
                "deployment_name": {
                    "type": "string",
                    "description": "The deployment to update.",
                },
                "container_name": {
                    "type": "string",
                    "description": "The container within the pod template.",
                },
                "cpu_request": {
                    "type": "string",
                    "description": "CPU request quantity, e.g. '500m'.",
                },
                "memory_request": {
                    "type": "string",
                    "description": "Memory request quantity, e.g. '512Mi'.",
                },
                "cpu_limit": {
                    "type": "string",
                    "description": "CPU limit quantity, e.g. '1'.",
                },
                "memory_limit": {
                    "type": "string",
                    "description": "Memory limit quantity, e.g. '1Gi'.",
                },
            },
            "required": ["deployment_name", "container_name"],
        },
    },
```

- [ ] **Step 4: Register in the executor**

In `MCP/tool_executor.py`, extend the `from tools import (...)` block (lines 15-26) with:

```python
    analyze_bottlenecks,
    inspect_cluster_resources,
    recommend_rightsizing,
```

and replace the K8s import at line 27:

```python
from tools.kubernetes_actions import (
    apply_resource_requests,
    cordon_node,
    restart_workload,
    scale_workload,
)
```

Add to `self.tools` in `__init__`, after `"cordon_node"`:

```python
            "recommend_rightsizing": self._execute_recommend_rightsizing,
            "analyze_bottlenecks": self._execute_analyze_bottlenecks,
            "inspect_cluster_resources": self._execute_inspect_cluster_resources,
            "apply_resource_requests": self._execute_apply_resource_requests,
```

Add the unwrappers next to `_execute_cordon_node`:

```python
    def _source_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the source-selection arguments shared by both analyses."""
        return {
            "source": args.get("source", "auto"),
            "namespace": args.get("namespace"),
            "csv_path": args.get("csv_path"),
            "node_csv_path": args.get("node_csv_path"),
            "jobstats_path": args.get("jobstats_path"),
            "days_back": int(args.get("days_back", 7) or 7),
        }

    def _execute_recommend_rightsizing(
        self, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        return recommend_rightsizing(
            **self._source_args(args),
            cpu_target_utilization=float(
                args.get("cpu_target_utilization", 0.7) or 0.7
            ),
            memory_target_utilization=float(
                args.get("memory_target_utilization", 0.8) or 0.8
            ),
            gpu_target_utilization=float(
                args.get("gpu_target_utilization", 0.7) or 0.7
            ),
        )

    def _execute_analyze_bottlenecks(
        self, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        return analyze_bottlenecks(**self._source_args(args))

    def _execute_inspect_cluster_resources(
        self, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        return inspect_cluster_resources(node=args.get("node"))

    def _execute_apply_resource_requests(
        self, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        deployment_name = args.get("deployment_name")
        container_name = args.get("container_name")

        if not deployment_name or not container_name:
            raise ValueError(
                "deployment_name and container_name are required"
            )

        return apply_resource_requests(
            args.get("namespace", "default"),
            deployment_name,
            container_name,
            cpu_request=args.get("cpu_request"),
            memory_request=args.get("memory_request"),
            cpu_limit=args.get("cpu_limit"),
            memory_limit=args.get("memory_limit"),
        )
```

- [ ] **Step 5: Verify registration and schemas**

```bash
python3 -c "
from MCP.tool_executor import ToolExecutor
from MCP.tool_schemas import get_all_tool_schemas, format_tools_for_claude
tools = sorted(ToolExecutor().tools)
new = {'recommend_rightsizing','analyze_bottlenecks','inspect_cluster_resources','apply_resource_requests'}
print(len(tools), sorted(new & set(tools)))
names = {s['name'] for s in get_all_tool_schemas()}
assert new <= names, new - names
assert all('input_schema' in s for s in format_tools_for_claude())
print('schemas ok', len(names))
"
```

Expected: `19 ['analyze_bottlenecks', 'apply_resource_requests', 'inspect_cluster_resources', 'recommend_rightsizing']` then `schemas ok 17`

- [ ] **Step 6: Verify the full fixture path through the executor**

```bash
python3 -c "
from MCP.tool_executor import execute_tool
args = {
  'source':'csv',
  'csv_path':'test/fixtures/quest_jobs_sample.csv',
  'node_csv_path':'test/fixtures/quest_nodes_sample.csv',
  'jobstats_path':'test/fixtures/quest_jobstats_sample.json',
}
r = execute_tool('recommend_rightsizing', args)
print(r['success'], r['result']['source'], r['result']['summary']['workload_count'])
b = execute_tool('analyze_bottlenecks', args)
print(b['success'], b['result']['summary']['top_bottleneck'])
"
```

Expected: `True csv 7` and a `top_bottleneck` — with Jobstats attached, `hyperparam` should classify as `gpu_idle`.

---

## Task 12: Repair `core/interface.py`, teach the prompt, verify, and commit

**Files:**
- Modify: `core/interface.py`, `core/settings.py:13-33`, `requirements.txt`

- [ ] **Step 1: Confirm the CLI is broken**

```bash
python3 -m pytest test/test_cli_api.py --collect-only -q
```

Expected: `ImportError: cannot import name 'os_profile' from 'core.interface'`

- [ ] **Step 2: Add the two missing functions**

`edgepilot_cli.py:11` imports `os_profile` and `summarize_tasks`, neither of which exists. Add to `core/interface.py` (ensure `import platform` is at the top):

```python
def os_profile() -> str:
    """Return a concise description of the current operating system."""
    return platform.platform()


def summarize_tasks(
    action: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Return recent scheduled tasks, optionally filtered by action."""
    tasks = _recent_tasks(limit=max(1, limit))

    if action:
        tasks = [
            task
            for task in tasks
            if task.get("action") == action
        ]

    return [
        {
            "task_id": task.get("task_id"),
            "action": task.get("action"),
            "target": task.get("target"),
            "status": task.get("status"),
        }
        for task in tasks[:limit]
    ]
```

- [ ] **Step 3: Add cluster vocabulary to `SYSTEM_PROMPT`**

`core/settings.py` has zero cluster vocabulary — the LLM does not know these tools exist. Insert between `TOOL USAGE GUIDELINES` and `DECISION MAKING`:

```python
    "CLUSTER GUIDELINES (Slurm/HPC and Kubernetes):\n"
    "- When users ask about wasted resources, over-provisioning, rightsizing, idle GPUs, or OOM kills → call recommend_rightsizing()\n"
    "- When users ask where the bottlenecks are, why jobs are slow, or what limits a workload → call analyze_bottlenecks()\n"
    "- When users ask about node headroom or 'where can this run' on Kubernetes → call inspect_cluster_resources()\n"
    "- NEVER recommend a replica count, job size, or resource request without first reading real data from one of those tools\n"
    "- Report the observed utilization ratio alongside every recommendation so the user can judge it\n"
    "- If a workload is flagged usage_unavailable, say the measurement is missing — do not estimate usage\n"
    "- Never advise shrinking the memory of a workload flagged oom_killed; it was killed for using too little, not too much\n"
    "- A workload flagged gpu_idle held GPUs it never used — the remedy is a CPU-only partition, not merely fewer GPUs\n"
    "- Slurm job sizing is set at submission, so Slurm recommendations are advisory: tell the user what to change in their submit script\n"
    "- Job data is anonymized under a data-use agreement: report aggregates and workload names, never speculate about individual users\n"
    "- scale_workload, restart_workload, cordon_node and apply_resource_requests change the cluster and require explicit user approval; propose them, never assume consent\n\n"
```

The existing prompt opens with "USE THEM AUTOMATICALLY without asking permission" — the last bullet is the necessary carve-out for the mutating tools.

- [ ] **Step 4: De-duplicate `requirements.txt`**

`kubernetes>=28.1.0` appears on both line 12 and line 13. Delete one.

- [ ] **Step 5: Run the whole suite**

```bash
python3 -m pytest test/ -q
```

Expected: zero collection errors, everything passing. Before this work, `main` produced 2 collection errors and collected only 15 tests.

- [ ] **Step 6: Confirm graceful degradation with no cluster**

```bash
python3 -c "
from MCP.tool_executor import execute_tool
r = execute_tool('recommend_rightsizing', {})
print(r['success'], r.get('error') or r['result'].get('status'))
"
```

Expected: on a machine with neither Slurm nor Kubernetes, `True unavailable`. It must **not** raise.

- [ ] **Step 7: Squash to a single commit**

Per the repo's commit convention — one commit, no co-author trailers:

```bash
git reset --soft $(git merge-base HEAD main) && git commit -m "feat: add cluster rightsizing and bottleneck analysis for Slurm and Kubernetes"
```

---

## Verification

**Unit tests** — the primary gate, and the only one that runs with no cluster:

```bash
python3 -m pytest test/ -q
```

**The demo that works today.** This is the point of the source-agnostic split — a full three-layer analysis with zero infrastructure:

```bash
python3 -c "
from tools.slurm_source import load_node_specs, records_from_csv
from tools.jobstats_source import attach_jobstats, load_jobstats
from tools.rightsizing import analyze_records
from tools.bottlenecks import classify_bottlenecks

nodes = load_node_specs('test/fixtures/quest_nodes_sample.csv')
records = records_from_csv('test/fixtures/quest_jobs_sample.csv', node_specs=nodes)
attach_jobstats(records, load_jobstats('test/fixtures/quest_jobstats_sample.json'))

sizing = analyze_records(records)
print('reclaimable GPUs:', sizing['summary']['reclaimable_gpu_count'])
for w in sizing['workloads']:
    rec = w['recommendation']
    print(f\"{w['name']:12} {w['findings']}\")
    if rec:
        print(f\"             -> cpu {rec['cpu_request']}, mem {rec['memory_request']}, gpu {rec['gpu_count']}\")

print()
print('bottlenecks:', classify_bottlenecks(records)['summary']['by_bottleneck'])
"
```

Expected: `hyperparam` flagged `gpu_idle` with a GPU recommendation of 1 or 0 against its 4 allocated (this only appears once Jobstats is attached — `sacct` alone cannot see GPU utilization); `train_net` flagged `oom_killed` with memory held at its request; `md_sim` and `preprocess` flagged over-requested on CPU and memory.

**End-to-end through the app:**

```bash
python3 -m uvicorn main:app --reload --port 8000
```

In the Electron UI (`cd ui && npm start`), ask *"analyze test/fixtures/quest_jobs_sample.csv for wasted resources"* and confirm a `tool` event fires for `recommend_rightsizing` and the answer cites observed utilization rather than estimates. Then ask it to shrink `train_net`'s memory — it must refuse, because that job was OOM-killed.

**Against Slurm** (once Quest access lands, from a login node):

```bash
python3 -c "
from tools.slurm_source import query_slurm_jobs
result = query_slurm_jobs(days_back=1)
print(result['status'], len(result.get('records', [])))
"
```

Cross-check one job against Slurm's own tool: `seff <jobid>` reports CPU and memory efficiency, and `used_cpu_cores / requested_cpu_cores` should match its CPU Efficiency percentage. That comparison is the acceptance test for the Quest ingest — run it on ~20 jobs spanning the Phase 1 sampling categories before trusting any aggregate.

**Against Kubernetes** (any cluster; `kind` plus metrics-server is cheapest). metrics-server on kind needs `--kubelet-insecure-tls`:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

Wait ~60s for scrapes, then deploy a deliberately over-requested workload:

```bash
kubectl create deployment idle-nginx --image=nginx --replicas=2
```

```bash
kubectl set resources deployment idle-nginx --requests=cpu=1,memory=1Gi
```

```bash
python3 -c "
from tools.rightsizing import recommend_rightsizing
for w in recommend_rightsizing(source='kubernetes', namespace='default')['workloads']:
    print(w['name'], w['findings'], w['recommendation'] and w['recommendation']['cpu_request'])
"
```

Expected: `idle-nginx` flagged `cpu_over_requested` with a recommendation far below `1000m`. Verify the approval gate by asking the UI to apply it, approving, then:

```bash
kubectl get deployment idle-nginx -o jsonpath='{.spec.template.spec.containers[0].resources}'
```

Finally delete metrics-server (`kubectl delete -n kube-system deployment metrics-server`) and re-run: workloads must come back flagged `usage_unavailable` with `recommendation: null` — never a fabricated number.

---

## When Quest data arrives

The plug points, in order:

1. **Confirm the export schema.** Layer 2 columns are assumed to match `SACCT_FIELDS` in `tools/slurm_source.py`. If Quest exports different headers, remap there. Layer 1 remaps in `FIELD_ALIASES` in `tools/jobstats_source.py`. Layer 3 remaps in `load_node_specs`.
2. **Verify TRES quoting.** Confirm the export quotes `ReqTRES`/`AllocTRES`, or the commas inside them will corrupt the parse — the exact defect present in `origin/v2-optimizations`'s fixture. `test_records_from_csv_reads_quoted_tres_columns` is the regression guard.
3. **Check `--noconvert` semantics.** If the export was generated without it, memory values are pre-rounded and `parse_slurm_memory` will be accurate only to the rounded unit. Note it in the report rather than silently trusting it.
4. **Validate against `seff`** on a sample spanning each Phase 1 category before trusting aggregates.
5. **Scale check.** 1,000–2,000 jobs is comfortably in-memory; no database is needed. Revisit only if Phase 2 multiplies the row count.

---

## Addendum: peer-relative analysis (`tools/workload_families.py`)

Added after the first implementation, and it addresses the weakest point of everything above:
the fixed thresholds. "Below 70% utilization is wasteful" cannot distinguish a simulation that
genuinely needs 5% of its cores from one over-requested twentyfold.

Peer comparison needs no such constant. Jobs are grouped into **workload families** using the
local `all-MiniLM-L6-v2` sentence-embedding model already declared in `requirements.txt`, then
each run is scored against its own family with a **modified z-score** (median absolute
deviation, Iglewicz & Hoaglin, cutoff 3.5). The median and MAD are used rather than mean and
standard deviation because a handful of extreme jobs — exactly what is being hunted — would
inflate a standard deviation enough to conceal themselves.

Two design rules carry weight:

- **The descriptor excludes the resource request.** Families are formed from workload *identity*
  (name, account, partition) and then judged on what they asked for. Folding the request into
  the descriptor would make the comparison circular: over-requested jobs would cluster together
  and look normal relative to each other. `test_descriptor_excludes_the_resource_request` guards this.
- **Records are sorted before greedy clustering**, so the same jobs always yield the same
  families regardless of input order (`test_result_is_independent_of_input_order`).

The model is **optional**. Missing package, or a model that cannot load on an offline compute
node, both degrade to grouping by normalized job name, with `degraded: true` and a reason in the
report. Measured on a 56-job simulation with three families and three planted problems:

| | Families | Outliers |
|---|---|---|
| Local model | 3 (correct) | 4 — exactly the planted problems |
| Name fallback | 6 (fragmented) | 8 — extra flags from split peer groups |

The fallback splits `md_sim` / `md_simulation` / `md_sim_run` into three families and
`train_net` / `train_network` into two. Smaller fragments have less representative medians, so
ordinary runs start scoring as anomalies. The report says when it is in that state so the
degradation is never silent.

Still no hosted LLM in the analysis path: the embedding model runs locally, and results are
deterministic for a given input.

## Out of scope

Deliberately excluded, worth noting as follow-ups:

- **Queue and scheduling analysis.** `squeue`/`sinfo` pending-vs-running counts, wait times, and fairshare — a distinct question ("why is my job waiting?") from rightsizing and bottlenecks. Listed under the proposal's Phase 2.
- **node_exporter host metrics.** Also Phase 2. `tools/metrics.py` already has a `PrometheusClient` that could serve this, but `scripts/bootstrap_prometheus.sh` sets up a single local node_exporter with no service discovery.
- **I/O and interconnect bottlenecks.** `classify_bottlenecks` covers CPU, memory and GPU. Storage and network constraints need the Phase 2 `node_disk_*` / `node_network_*` series; until then an I/O-bound job will misclassify as `oversized`.
- **Per-GPU breakdown.** `UsageSample` carries one `gpu_utilization` figure. The proposal requests per-GPU data where available, which would let a 4-GPU job showing one busy GPU be distinguished from four evenly-idle ones — a meaningfully different remedy.
- **Structured UI rendering.** The `tool` SSE event at `main.py:809` carries only `{name, success}`, and `renderMessages` in `ui/renderer.js:249` supports four regex markdown rules with no table support — so analysis output reaches the user only as LLM prose. A rightsizing table would need both a richer SSE payload and new render code.
- **Per-call approval granularity.** `main.py:761` gates the whole turn on any one dangerous call.
- **`report_edge_status` / `suggest_capacity_window` defects.** Both accept parameters they ignore (`window`, `horizon_hours`), the off-peak suggestion is a hardcoded 1 AM constant, and the shared CPU PromQL reduces to `100 * idle_rate` — labelled `cpu_busy` in one place and inverted again in another.
