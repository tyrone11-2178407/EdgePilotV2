import pytest

from evaluations.scalability import (
    HUMAN_SECONDS_PER_API_CALL,
    PRICING_USD_PER_MTOK,
    MockProvider,
    SimulatedToolExecutor,
    run_sweep,
)
from tools.cluster_simulator import SimulatedMetricsProvider


def test_sweep_produces_one_row_per_size():
    rows = run_sweep([1, 2], workflow="health_check", pods_per_node=2)

    assert len(rows) == 2
    assert [r["nodes"] for r in rows] == [1, 2]
    assert rows[0]["pods"] == 2


def test_sweep_records_the_metrics_goal_three_asks_for():
    rows = run_sweep([2], workflow="health_check", pods_per_node=2)
    row = rows[0]

    for field in (
        "prompt_tokens", "response_tokens", "total_tokens",
        "wall_seconds", "usd_cost", "human_baseline_seconds",
        "tool_calls", "llm_turns",
    ):
        assert field in row, f"missing {field}"

    assert row["total_tokens"] > 0
    assert row["llm_turns"] > 0


def test_human_baseline_uses_the_agreed_proxy():
    rows = run_sweep([1], workflow="health_check", pods_per_node=1)
    row = rows[0]

    expected = row["tool_calls"] * HUMAN_SECONDS_PER_API_CALL
    assert row["human_baseline_seconds"] == pytest.approx(expected)


def test_tokens_grow_with_cluster_size():
    """The whole point of the sweep: bigger cluster, more context."""
    rows = run_sweep([1, 20], workflow="health_check", pods_per_node=5)

    assert rows[1]["total_tokens"] > rows[0]["total_tokens"]


def test_mock_run_is_free():
    rows = run_sweep([1], workflow="health_check", pods_per_node=1)

    assert rows[0]["model"] == "mock"
    assert rows[0]["usd_cost"] == 0.0


def test_repeated_sweeps_agree():
    """Deterministic, or the harness measures its own noise."""
    a = run_sweep([3], workflow="health_check", pods_per_node=3, seed=99)
    b = run_sweep([3], workflow="health_check", pods_per_node=3, seed=99)

    assert a[0]["total_tokens"] == b[0]["total_tokens"]
    assert a[0]["tool_calls"] == b[0]["tool_calls"]


def test_pricing_table_covers_the_configured_models():
    from core.settings import PROVIDER_ENV_SETTINGS

    for provider, settings in PROVIDER_ENV_SETTINGS.items():
        model = settings["default_model"]
        assert model in PRICING_USD_PER_MTOK, (
            f"no price for {provider}'s default model {model!r} — cost "
            f"figures for it would silently be zero"
        )


def test_unsimulated_tool_fails_loudly():
    """A fake success would corrupt the measurement."""
    import asyncio

    cluster = SimulatedMetricsProvider(node_count=1, pods_per_node=1)
    executor = SimulatedToolExecutor(cluster)

    results = asyncio.run(executor([{"name": "scale_workload", "arguments": {}}]))

    assert results[0]["success"] is False
    assert "not simulated" in results[0]["error"]


def test_executor_serves_cluster_state():
    import asyncio

    cluster = SimulatedMetricsProvider(node_count=3, pods_per_node=2)
    executor = SimulatedToolExecutor(cluster)

    results = asyncio.run(
        executor([{"name": "inspect_kubernetes_cluster", "arguments": {}}])
    )

    assert results[0]["success"] is True
    assert results[0]["result"]["node_count"] == 3
    assert executor.call_count == 1


def test_mock_provider_tokens_scale_with_context():
    provider = MockProvider()
    provider.enable_tools([])

    short = provider.generate([{"role": "user", "content": "hi"}])
    long = provider.generate([{"role": "user", "content": "x" * 10_000}])

    assert long.prompt_tokens > short.prompt_tokens
