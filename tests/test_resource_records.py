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
