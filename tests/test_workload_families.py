import numpy as np
import pytest

from tools.resource_records import ResourceRecord
from tools.workload_families import (
    analyze_workload_families,
    build_descriptor,
    load_embedder,
    metric_value,
    modified_z_scores,
    normalized_name,
)


def record(**overrides):
    defaults = dict(
        source="slurm",
        workload_name="md_sim",
        group="acct_a",
        partition="normal",
        requested_cpu_cores=8.0,
        requested_memory_bytes=32 * 1024**3,
        used_cpu_cores=4.0,
        peak_memory_bytes=16 * 1024**3,
        state="COMPLETED",
    )
    defaults.update(overrides)
    return ResourceRecord(**defaults)


# A deterministic stand-in for the local model: vectors are built from
# tokens present in the descriptor, so names sharing a stem land close
# together and unrelated names stay apart. Gives the control a seeded RNG
# cannot.
_TOKENS = ("train", "sim", "preprocess", "gpu", "normal")


def fake_embedder(texts):
    vectors = []

    for text in texts:
        vector = np.zeros(len(_TOKENS) + 1, dtype=np.float32)

        for position, token in enumerate(_TOKENS):
            if token in text:
                vector[position] = 1.0

        # Keeps a descriptor matching no token from becoming a zero vector.
        vector[-1] = 0.1
        vectors.append(vector / np.linalg.norm(vector))

    return np.vstack(vectors)


# ====================================================================== #
# Descriptors                                                             #
# ====================================================================== #


def test_normalized_name_strips_run_suffixes():
    assert normalized_name("md_sim_01") == "md_sim"
    assert normalized_name("train-3") == "train"
    assert normalized_name("solver") == "solver"


def test_normalized_name_keeps_all_digit_names_intact():
    assert normalized_name("12345") == "12345"


def test_descriptor_excludes_the_resource_request():
    # Families must be formed from identity, not from the request being
    # judged — otherwise over-requested jobs cluster together and look
    # normal relative to each other.
    small = record(requested_cpu_cores=1, requested_memory_bytes=1024)
    large = record(requested_cpu_cores=256, requested_memory_bytes=1024**4)

    assert build_descriptor(small) == build_descriptor(large)


def test_descriptor_includes_identity_signals():
    descriptor = build_descriptor(record())

    assert "md_sim" in descriptor
    assert "acct_a" in descriptor
    assert "normal" in descriptor


# ====================================================================== #
# Outlier scoring                                                         #
# ====================================================================== #


def test_modified_z_scores_flags_the_extreme_value():
    scores = modified_z_scores([10.0, 10.0, 10.0, 10.0, 10.0, 90.0])

    assert abs(scores[-1]) > 3.5
    assert all(abs(score) < 3.5 for score in scores[:-1])


def test_modified_z_scores_handles_identical_values():
    assert modified_z_scores([5.0] * 6) == [0.0] * 6


def test_modified_z_scores_survives_zero_mad():
    # Median absolute deviation collapses to zero when most values match;
    # the mean-deviation fallback must still separate the outlier.
    scores = modified_z_scores([4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 100.0])

    assert abs(scores[-1]) > 3.5


def test_modified_z_scores_of_short_series_is_zero():
    assert modified_z_scores([1.0]) == [0.0]


def test_metric_value_returns_none_when_unmeasured():
    assert metric_value(record(used_cpu_cores=None), "cpu_utilization") is None
    assert metric_value(record(), "gpu_utilization") is None


def test_metric_value_rejects_unknown_metric():
    with pytest.raises(ValueError, match="Unknown metric"):
        metric_value(record(), "disk_io")


# ====================================================================== #
# Family construction                                                     #
# ====================================================================== #


def test_embeddings_group_semantically_related_names():
    records = [
        record(workload_name="train_net", record_id="1"),
        record(workload_name="train_network", record_id="2"),
        record(workload_name="preprocess", record_id="3"),
    ]

    report = analyze_workload_families(records, embedder=fake_embedder)

    assert report["method"] == "embedding"
    assert report["degraded"] is False

    sizes = sorted(family["size"] for family in report["families"])
    assert sizes == [1, 2]


def test_name_fallback_keeps_differently_named_jobs_apart():
    records = [
        record(workload_name="train_net", record_id="1"),
        record(workload_name="train_network", record_id="2"),
    ]

    report = analyze_workload_families(records, use_embeddings=False)

    assert report["method"] == "name"
    assert report["degraded"] is True
    assert report["degraded_reason"] == "embeddings disabled by caller"
    assert report["summary"]["family_count"] == 2


def test_name_fallback_groups_numbered_runs_of_one_workload():
    records = [
        record(workload_name="md_sim_1", record_id="1"),
        record(workload_name="md_sim_2", record_id="2"),
        record(workload_name="md_sim_3", record_id="3"),
    ]

    report = analyze_workload_families(records, use_embeddings=False)

    assert report["summary"]["family_count"] == 1
    assert report["families"][0]["label"] == "md_sim"
    assert report["families"][0]["size"] == 3


def test_missing_local_model_degrades_instead_of_raising():
    # embedder=None with use_embeddings=True exercises the real loader;
    # sentence-transformers is absent in CI, so this must not raise.
    report = analyze_workload_families([record(record_id="1")])

    assert report["status"] == "ok"
    assert report["method"] in {"embedding", "name"}


def test_result_is_independent_of_input_order():
    records = [
        record(workload_name="train_net", record_id=str(i)) for i in range(3)
    ] + [record(workload_name="preprocess", record_id="9")]

    forward = analyze_workload_families(records, embedder=fake_embedder)
    reversed_ = analyze_workload_families(
        list(reversed(records)), embedder=fake_embedder
    )

    assert forward["families"] == reversed_["families"]


def test_empty_input_produces_empty_report():
    report = analyze_workload_families([])

    assert report["families"] == []
    assert report["summary"]["family_count"] == 0


def test_malformed_embedder_output_is_rejected():
    def bad_embedder(texts):
        return np.zeros((len(texts) + 1, 4), dtype=np.float32)

    with pytest.raises(ValueError, match="expected a 2-D array"):
        analyze_workload_families(
            [record(record_id="1")], embedder=bad_embedder
        )


# ====================================================================== #
# Anomalies                                                               #
# ====================================================================== #


def test_outlier_within_a_family_is_flagged():
    # Five jobs at ~50% memory utilization, one at 3%.
    records = [
        record(
            workload_name="md_sim",
            record_id=str(i),
            peak_memory_bytes=16 * 1024**3,
        )
        for i in range(5)
    ] + [
        record(
            workload_name="md_sim",
            record_id="outlier",
            peak_memory_bytes=1024**3,
        )
    ]

    report = analyze_workload_families(records, use_embeddings=False)
    flagged = [
        a for a in report["anomalies"] if a["metric"] == "memory_utilization"
    ]

    assert len(flagged) == 1
    assert flagged[0]["record_id"] == "outlier"
    assert flagged[0]["direction"] == "low"
    assert flagged[0]["family"] == "md_sim"


def test_uniform_family_produces_no_anomalies():
    records = [
        record(workload_name="md_sim", record_id=str(i)) for i in range(6)
    ]

    report = analyze_workload_families(records, use_embeddings=False)

    assert report["anomalies"] == []


def test_small_families_are_not_scored_for_anomalies():
    # Two jobs cannot establish a meaningful median to deviate from.
    records = [
        record(workload_name="md_sim", record_id="1", peak_memory_bytes=1024**3),
        record(
            workload_name="md_sim",
            record_id="2",
            peak_memory_bytes=200 * 1024**3,
        ),
    ]

    report = analyze_workload_families(records, use_embeddings=False)

    assert report["anomalies"] == []


def test_family_medians_are_reported_for_context():
    records = [
        record(workload_name="md_sim", record_id=str(i)) for i in range(4)
    ]

    report = analyze_workload_families(records, use_embeddings=False)
    median = report["families"][0]["median"]

    assert median["cpu_utilization"] == pytest.approx(0.5)
    assert median["memory_utilization"] == pytest.approx(0.5)
    assert median["gpu_utilization"] is None


def test_load_embedder_returns_none_or_callable():
    embedder = load_embedder()

    assert embedder is None or callable(embedder)
