import pytest

from tools.resource_records import ResourceRecord
from tools.rightsizing import (
    _recommended_cpu_cores,
    _recommended_gpu_count,
    _recommended_memory_bytes,
    analyze_records,
    to_cpu_quantity,
    to_memory_quantity,
)


# ====================================================================== #
# Sizing arithmetic                                                       #
# ====================================================================== #


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


# ====================================================================== #
# The analysis engine                                                     #
# ====================================================================== #


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
        [
            record(
                instance_count=2,
                requested_gpu_count=4,
                used_gpu_utilization=0.05,
            )
        ]
    )["summary"]

    assert summary["workload_count"] == 1
    assert summary["requested_cpu_cores"] == pytest.approx(8.0)
    assert summary["reclaimable_cpu_cores"] > 0
    assert summary["reclaimable_gpu_count"] > 0


def test_busy_gpu_is_never_recommended_upward():
    # One GPU at 92% would size to 2 under a bare 70% target. Adding GPUs
    # cannot relieve a busy GPU without data-parallel code changes, so the
    # recommendation must stay at the current allocation.
    workload = analyze_records(
        [record(requested_gpu_count=1, used_gpu_utilization=0.92)]
    )["workloads"][0]

    assert workload["recommendation"]["gpu_count"] == 1


def test_workload_without_findings_is_left_alone():
    # 0.78 CPU and 0.78 memory ratios: above target, so neither
    # over- nor under-requested. Nothing should be recommended to change.
    workload = analyze_records(
        [
            record(
                requested_cpu_cores=8.0,
                used_cpu_cores=6.24,
                requested_memory_bytes=32 * 1024**3,
                peak_memory_bytes=int(0.9 * 32 * 1024**3),
            )
        ]
    )["workloads"][0]

    assert workload["findings"] == []
    assert workload["recommendation"]["cpu_request_cores"] == pytest.approx(8.0)
    assert workload["recommendation"]["memory_request_bytes"] == 32 * 1024**3


def test_under_requested_cpu_may_be_recommended_upward():
    workload = analyze_records(
        [record(requested_cpu_cores=2.0, used_cpu_cores=3.0)]
    )["workloads"][0]

    assert "cpu_under_requested" in workload["findings"]
    assert workload["recommendation"]["cpu_request_cores"] > 2.0


def test_workload_with_no_requests_still_gets_advice():
    workload = analyze_records(
        [record(requested_cpu_cores=0.0, used_cpu_cores=1.4)]
    )["workloads"][0]

    assert "no_cpu_requests_set" in workload["findings"]
    assert workload["recommendation"]["cpu_request_cores"] == pytest.approx(2.0)


def test_empty_input_produces_empty_report():
    report = analyze_records([])

    assert report["workloads"] == []
    assert report["summary"]["workload_count"] == 0
