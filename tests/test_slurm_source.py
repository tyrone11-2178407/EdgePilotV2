from pathlib import Path
from unittest.mock import patch

import pytest

from tools.slurm_source import (
    expand_node_list,
    load_node_specs,
    parse_slurm_duration,
    parse_slurm_memory,
    parse_tres,
    query_slurm_jobs,
    records_from_csv,
    records_from_rows,
)

FIXTURES = Path(__file__).parent / "fixtures"
JOBS_CSV = FIXTURES / "quest_jobs_sample.csv"
NODES_CSV = FIXTURES / "quest_nodes_sample.csv"


# ====================================================================== #
# Parsing primitives                                                      #
# ====================================================================== #


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
    assert parse_tres("cpu=8,mem=64G,node=1,gres/gpu=4")["gpu"] == pytest.approx(
        4.0
    )


def test_parse_tres_extracts_typed_gpu_gres():
    parsed = parse_tres("cpu=8,mem=64G,gres/gpu:a100=2")

    assert parsed["gpu"] == pytest.approx(2.0)
    assert parsed["gpu_model"] == "a100"


def test_parse_tres_handles_empty_and_malformed_input():
    assert parse_tres("") == {}
    assert parse_tres("garbage") == {}


def test_expand_node_list_handles_ranges_and_plain_names():
    assert expand_node_list("qnode[0103-0106]") == [
        "qnode0103",
        "qnode0104",
        "qnode0105",
        "qnode0106",
    ]
    assert expand_node_list("qnode0101") == ["qnode0101"]
    assert expand_node_list("") == []


# ====================================================================== #
# Records                                                                 #
# ====================================================================== #


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
    # 01:12:00 of CPU time over 02:00:00 elapsed is 0.6 cores.
    assert records_from_csv(str(JOBS_CSV))[0].used_cpu_cores == pytest.approx(0.6)


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


# ====================================================================== #
# Node specs                                                              #
# ====================================================================== #


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

    assert mpi.nodes == ["qnode0103", "qnode0104", "qnode0105", "qnode0106"]


# ====================================================================== #
# sacct invocation                                                        #
# ====================================================================== #


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
def test_query_slurm_jobs_parses_pipe_delimited_output(mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = (
        "2001|sim|COMPLETED|0:0|cpu=2,mem=8G|cpu=2,mem=8G|"
        "1048576K|00:30:00|01:00:00|1|qnode0101|normal|normal|acct_z\n"
    )

    result = query_slurm_jobs()

    assert result["status"] == "ok"
    assert len(result["records"]) == 1
    assert result["records"][0].workload_name == "sim"
    assert result["records"][0].requested_memory_bytes == 8 * 1024**3


@patch("tools.slurm_source.subprocess.run")
def test_query_slurm_jobs_reports_missing_sacct(mock_run):
    mock_run.side_effect = FileNotFoundError()

    result = query_slurm_jobs()

    assert result["status"] == "unavailable"
    assert "sacct" in result["error"]
