"""Scalability analysis: what does AI cluster management cost, and does it scale?

Implements the 7/29 meeting's third goal — simulate clusters from 1 to
~1,000 nodes and 10 to 10,000 jobs, and measure AI token usage, dollar
cost, and time-to-resolution against a human baseline.

Two modes:

* ``--mock-provider`` — a scripted LLM. No API calls, deterministic, safe
  for CI. Token counts are estimated from context length, so the *shape*
  of the scaling curve is real even though the numbers are not.
* ``--provider gemini`` — real API calls and real token accounting. This
  is the mode that produces reportable figures.

Usage::

    python3 -m evaluations.scalability --mock-provider --sizes 1,10,100
    python3 -m evaluations.scalability --provider gemini --sizes 1,10,100,1000

Read ``evaluations/README.md`` before quoting any number from this.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from providers.base import LLMResponse, ToolCall
from tools.cluster_simulator import SimulatedMetricsProvider

RESULTS_DIR = Path(__file__).parent / "results"

# From the 7/29 meeting: treat each Kubernetes API call as ~5 seconds of
# human operator time. This is a proxy agreed in the meeting, not a
# measurement of real operators. Change it here, not inline.
HUMAN_SECONDS_PER_API_CALL = 5.0

# WARNING: these rates go stale. Re-verify against current provider
# pricing before publishing any cost figure — a wrong rate here silently
# invalidates every dollar number in the output.
PRICING_USD_PER_MTOK: Dict[str, Dict[str, float]] = {
    "gemini-3.1-flash-lite": {"input": 0.10, "output": 0.40},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
    "mock": {"input": 0.0, "output": 0.0},
}

# Rough characters-per-token used only by the mock provider, so its token
# counts grow with context the way a real tokenizer's would.
_CHARS_PER_TOKEN = 4


# ====================================================================== #
# Scripted provider                                                       #
# ====================================================================== #


class MockProvider:
    """A scripted LLM that calls the first tool a step allows.

    Token counts are estimated from context length rather than invented,
    so the mock reproduces the scaling relationship being measured even
    though the absolute values are not real.
    """

    def __init__(self) -> None:
        self._enabled: List[Dict[str, Any]] = []

    @classmethod
    def describe(cls) -> Dict[str, str]:
        return {"id": "mock", "name": "Mock provider"}

    def enable_tools(self, schemas: List[Dict[str, Any]]) -> None:
        self._enabled = schemas or []

    def generate(self, messages) -> LLMResponse:
        context_chars = sum(len(str(m.get("content", ""))) for m in messages)
        prompt_tokens = max(1, context_chars // _CHARS_PER_TOKEN)

        if self._enabled:
            tool = self._enabled[0]
            return LLMResponse(
                text=f"Calling {tool['name']} to inspect the cluster.",
                prompt_tokens=prompt_tokens,
                response_tokens=24,
                tool_calls=[ToolCall(name=tool["name"], arguments={})],
            )

        return LLMResponse(
            text="No anomalies could be confirmed from the available data.",
            prompt_tokens=prompt_tokens,
            response_tokens=40,
        )


# ====================================================================== #
# Simulated tool execution                                                #
# ====================================================================== #


class SimulatedToolExecutor:
    """Routes the workflow's tool calls at the generated cluster.

    Anything not backed by the simulator returns an explicit
    ``not simulated`` error rather than silently succeeding — a fake
    success would corrupt the measurement.
    """

    def __init__(self, cluster: SimulatedMetricsProvider) -> None:
        self.cluster = cluster
        self.call_count = 0

    async def __call__(self, calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []

        for call in calls:
            self.call_count += 1
            name = call["name"]

            try:
                results.append({
                    "success": True,
                    "tool": name,
                    "result": self._dispatch(name, call.get("arguments") or {}),
                })
            except NotImplementedError as exc:
                results.append({"success": False, "tool": name, "error": str(exc)})

        return results

    def _dispatch(self, name: str, arguments: Dict[str, Any]) -> Any:
        if name in {"inspect_kubernetes_cluster", "inspect_cluster_resources"}:
            return self.cluster.gather_metrics()

        if name == "query_pod_resources":
            usage = self.cluster.pod_usage()
            pod = arguments.get("pod_name")

            for (namespace, pod_name), values in usage.items():
                if pod is None or pod_name == pod:
                    return {
                        "pod": pod_name,
                        "namespace": namespace,
                        "memory_bytes": values["memory_bytes"],
                    }

            raise NotImplementedError(f"pod {pod!r} not present in the simulation")

        if name == "query_prometheus":
            # Report the worst offenders, which is what the workflow's
            # detect step is actually looking for.
            usage = self.cluster.pod_usage()
            worst = sorted(
                usage.items(), key=lambda kv: kv[1]["memory_bytes"], reverse=True
            )[:10]
            return {
                "results": [
                    {
                        "metric": {"namespace": ns, "pod": pod},
                        "summary": {"last": values["memory_bytes"]},
                    }
                    for (ns, pod), values in worst
                ]
            }

        if name == "inspect_kubernetes_deployment":
            return {"note": "deployment inspection is not simulated"}

        raise NotImplementedError(f"tool {name!r} is not simulated")


# ====================================================================== #
# One measured run                                                        #
# ====================================================================== #


async def _measure(
    workflow: str,
    cluster: SimulatedMetricsProvider,
    provider: Any,
    model: str,
) -> Dict[str, Any]:
    from core.tool_schemas import get_all_tool_schemas
    from core.workflows import run_workflow

    executor = SimulatedToolExecutor(cluster)
    transcript: List[str] = []
    usage: Dict[str, Any] = {}
    errors: List[str] = []

    async def auto_approve(approval_id: str) -> bool:
        # The harness measures cost, not the approval gate. Real runs stop
        # here for a human; that wait time is deliberately excluded.
        return True

    started = time.perf_counter()

    async for event in run_workflow(
        workflow, provider, get_all_tool_schemas(), executor, auto_approve, "scalability",
    ):
        kind = event["type"]

        if kind == "usage":
            usage = event["data"]
        elif kind == "chunk":
            transcript.append(event["data"].get("text", ""))
        elif kind == "error":
            errors.append(event["data"].get("detail", ""))

    wall_seconds = time.perf_counter() - started

    prompt_tokens = usage.get("prompt_tokens", 0)
    response_tokens = usage.get("response_tokens", 0)
    rates = PRICING_USD_PER_MTOK.get(model, PRICING_USD_PER_MTOK["mock"])
    usd_cost = (
        prompt_tokens * rates["input"] + response_tokens * rates["output"]
    ) / 1_000_000

    text = "".join(transcript)
    planted = cluster.planted_anomalies
    found = sum(1 for a in planted if a["pod"] in text)

    return {
        "nodes": cluster.node_count,
        "pods": cluster.pod_count,
        "workflow": workflow,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "response_tokens": response_tokens,
        "total_tokens": prompt_tokens + response_tokens,
        "llm_turns": usage.get("llm_turns", 0),
        "tool_calls": usage.get("tool_calls", 0),
        "steps_completed": usage.get("steps", 0),
        "wall_seconds": round(wall_seconds, 3),
        "usd_cost": round(usd_cost, 6),
        "human_baseline_seconds": executor.call_count * HUMAN_SECONDS_PER_API_CALL,
        "anomalies_planted": len(planted),
        "anomalies_found": found,
        "errors": "; ".join(errors),
    }


# ====================================================================== #
# Sweep                                                                   #
# ====================================================================== #


def run_sweep(
    sizes: List[int],
    workflow: str = "health_check",
    pods_per_node: int = 10,
    anomaly_rate: float = 0.01,
    seed: int = 7,
    provider_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run *workflow* against a cluster of each size and collect metrics."""

    if provider_name:
        from core.settings import provider_config
        from providers import get_provider

        config = provider_config(provider_name)
        provider_factory = lambda: get_provider(provider_name, config)  # noqa: E731
        model = config.model
    else:
        provider_factory = MockProvider
        model = "mock"

    rows = []

    for size in sizes:
        cluster = SimulatedMetricsProvider(
            node_count=size,
            pods_per_node=pods_per_node,
            anomaly_rate=anomaly_rate,
            seed=seed,
        )
        print(f"  {size:>5} nodes / {cluster.pod_count:>6} pods ...", end="", flush=True)

        row = asyncio.run(_measure(workflow, cluster, provider_factory(), model))
        rows.append(row)

        print(f" {row['total_tokens']:>8} tokens, {row['wall_seconds']:>6.2f}s")

    return rows


def write_csv(rows: List[Dict[str, Any]]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"scalability_{datetime.now():%Y%m%d_%H%M%S}.csv"

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    return path


def print_summary(rows: List[Dict[str, Any]]) -> None:
    mock = rows[0]["model"] == "mock"

    header = (
        f"{'nodes':>6} {'pods':>7} {'tokens':>9} {'cost $':>9} "
        f"{'ai sec':>8} {'human sec':>10} {'speedup':>8} {'found':>9}"
    )
    print("\n" + header)
    print("-" * len(header))

    for row in rows:
        human = row["human_baseline_seconds"]
        ai = row["wall_seconds"]
        found = f"{row['anomalies_found']}/{row['anomalies_planted']}"

        if mock:
            # A scripted LLM returns instantly and does no reasoning, so a
            # speedup ratio against it is meaningless and a detection score
            # is always zero. Showing either as a number invites someone to
            # quote it.
            speedup = "--"
            found = "--"
        else:
            speedup = f"{human / ai:.1f}x" if ai > 0 else "n/a"

        print(
            f"{row['nodes']:>6} {row['pods']:>7} {row['total_tokens']:>9} "
            f"{row['usd_cost']:>9.5f} {ai:>8.2f} {human:>10.1f} "
            f"{speedup:>8} {found:>9}"
        )

    first, last = rows[0], rows[-1]
    node_growth = last["nodes"] / max(first["nodes"], 1)
    token_growth = last["total_tokens"] / max(first["total_tokens"], 1)

    print(
        f"\n{node_growth:.0f}x the nodes cost {token_growth:.1f}x the tokens."
    )
    if token_growth < node_growth:
        print("Token use grows sub-linearly with cluster size.")
    else:
        print(
            "Token use grows at least linearly — check that Prometheus and "
            "cluster results are being summarized before reaching the model."
        )

    calls = {row["tool_calls"] for row in rows}
    if len(calls) == 1:
        print(
            f"Tool calls stayed flat at {calls.pop()} across every size — the "
            f"AI's call count is driven by the workflow, not the cluster."
        )

    if mock:
        print(
            "\nMOCK MODE: token counts are estimated from context length and "
            "the LLM is scripted.\nThe scaling shape is real; the timing, "
            "cost and detection columns are not.\nUse --provider gemini for "
            "figures worth reporting."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sizes", default="1,10,100",
                        help="Comma-separated node counts, e.g. 1,10,100,1000")
    parser.add_argument("--workflow", default="health_check")
    parser.add_argument("--pods-per-node", type=int, default=10)
    parser.add_argument("--anomaly-rate", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--mock-provider", action="store_true",
                        help="Scripted LLM, no API calls. Default when no provider given.")
    parser.add_argument("--provider", default=None,
                        help="Real provider id, e.g. 'gemini'. Makes real API calls.")
    parser.add_argument("--json", action="store_true", help="Emit rows as JSON")

    args = parser.parse_args()
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    provider_name = None if args.mock_provider else args.provider

    print(
        f"Workflow '{args.workflow}' over {len(sizes)} cluster size(s), "
        f"provider={provider_name or 'mock'}\n"
    )

    rows = run_sweep(
        sizes,
        workflow=args.workflow,
        pods_per_node=args.pods_per_node,
        anomaly_rate=args.anomaly_rate,
        seed=args.seed,
        provider_name=provider_name,
    )

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print_summary(rows)

    print(f"\nWrote {write_csv(rows)}")
    print("Read evaluations/README.md before quoting these numbers.")


if __name__ == "__main__":
    main()
