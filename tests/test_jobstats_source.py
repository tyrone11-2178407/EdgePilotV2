from pathlib import Path

import pytest

from tools.jobstats_source import (
    attach_jobstats,
    load_jobstats,
    summarize_samples,
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
