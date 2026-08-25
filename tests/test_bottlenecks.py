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
        [
            record(
                requested_gpu_count=4,
                used_gpu_utilization=0.01,
                used_cpu_cores=7.5,
            )
        ]
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


def test_gpu_node_used_without_a_gpu_request_is_surfaced():
    gpu_node = NodeSpec(
        name="qgpu0201",
        cpu_cores=52,
        memory_bytes=384 * 1024**3,
        gpu_count=4,
        hardware_class="gpu",
    )

    result = classify_bottlenecks([record(node_spec=gpu_node)])

    assert "gpu_node_used_without_gpu_request" in result["workloads"][0]["notes"]


def test_cluster_summary_ranks_bottlenecks_by_frequency():
    result = classify_bottlenecks(
        [
            record(
                workload_name="a", requested_gpu_count=4, used_gpu_utilization=0.0
            ),
            record(
                workload_name="b", requested_gpu_count=2, used_gpu_utilization=0.0
            ),
            record(
                workload_name="c",
                used_cpu_cores=7.8,
                peak_memory_bytes=2 * 1024**3,
            ),
        ]
    )

    assert result["summary"]["by_bottleneck"]["gpu_idle"] == 2
    assert result["summary"]["top_bottleneck"] == "gpu_idle"


def test_empty_input_produces_empty_summary():
    result = classify_bottlenecks([])

    assert result["workloads"] == []
    assert result["summary"]["top_bottleneck"] is None
