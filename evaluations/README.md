# Scalability analysis

Implements Goal 3 from the 7/29 meeting: *"simulate varying cluster sizes
(1–1,000 nodes) and workloads, measuring AI token usage and time-to-resolution
vs. a human baseline."*

## Running it

No cluster, no API key, no cost:

```bash
python3 -m evaluations.scalability --mock-provider --sizes 1,10,100,1000
```

Real measurements, with a provider configured in `env/.env`:

```bash
python3 -m evaluations.scalability --provider gemini --sizes 1,10,100,1000 --workflow memory_anomaly
```

Results are written to `evaluations/results/scalability_<timestamp>.csv`.

Useful flags: `--workflow` (default `health_check`), `--pods-per-node`
(default 10), `--anomaly-rate` (default 0.01), `--seed` (default 7),
`--json`.

## What it measures

| Column | Meaning |
|---|---|
| `prompt_tokens`, `response_tokens` | From the workflow's `usage` event |
| `llm_turns`, `tool_calls` | Model turns and tool invocations per run |
| `wall_seconds` | Wall-clock time for the whole workflow |
| `usd_cost` | Tokens × the rate table in `scalability.py` |
| `human_baseline_seconds` | `tool_calls × 5s`, the meeting's proxy |
| `anomalies_found` / `anomalies_planted` | Did the run name the pods that were deliberately broken |

## Read this before quoting a number

**The human baseline is a proxy, not a measurement.** The meeting agreed to
treat one Kubernetes API call as ~5 seconds of operator time. Nobody timed a
real administrator. It is a placeholder for comparison, and any claim of an
"Nx speedup" rests on it entirely. The constant is
`HUMAN_SECONDS_PER_API_CALL` — change it in one place, and say what you used.

**Mock mode does not measure the AI.** With `--mock-provider` the LLM is
scripted and returns instantly, so timing, cost and detection columns are
meaningless — the summary prints `--` for them rather than a number someone
might quote. What *is* real in mock mode is the shape of the token curve,
because token counts are estimated from actual context length. Use mock mode
to test the harness; use a real provider to produce figures.

**Prices go stale.** `PRICING_USD_PER_MTOK` is a hardcoded table. Verify it
against current provider pricing before publishing any dollar figure.

**The simulated cluster models resource state, not behaviour.** Nodes, pods,
requests, limits and memory consumption are generated. Scheduling, real
failures, network effects and the actual Kubernetes control plane are not.
A workflow that succeeds here has not been shown to work on a real cluster.

**Detection scoring is a substring check.** A run counts as finding an
anomaly if the offending pod name appears in its output. That over-counts a
model that lists many pods and under-counts one that describes the problem
without naming the pod. Treat it as a smoke signal, not an accuracy metric.

## What the first runs show

From `--mock-provider --sizes 1,10,100,1000` on `health_check`:

- Token use grows **sub-linearly**: 1,000× the nodes costs ~175× the tokens.
- **Tool calls stay flat** at 4 regardless of cluster size — the call count
  is set by the workflow definition, not by how big the cluster is. So the
  human baseline under this proxy is constant while AI cost climbs, which
  makes token growth the thing worth optimising.
- At 1,000 nodes a single `health_check` run costs on the order of 1.3M
  prompt tokens, almost entirely from `inspect_kubernetes_cluster` returning
  every node into the model's context.

That last point is the actionable finding, and it answers Dr. Kim's question
directly ("is it millions of tokens, so it costs a hundred bucks a day?").
Summarising cluster inspection the way `query_prometheus` now summarises
series would cut it sharply. Confirm against a real provider before relying
on the exact figures.
