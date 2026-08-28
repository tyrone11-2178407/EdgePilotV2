# MCP Integration

This package implements EdgePilot's tool-calling integration between supported
LLM providers and local tools.

- `tool_schemas.py` defines the tool registry, provider-formatting helpers,
  and state-changing tool classifications.
- `tool_executor.py` validates and executes individual or batched tool calls.
- `__init__.py` exposes the public integration API.

The registry currently contains 40 tools. Fifteen are classified as
state-changing. The FastAPI backend applies explicit human-in-the-loop
approval to 14 high-impact operations.

See `docs/architecture.md` and `docs/ai-workflow.md` for the system and
approval workflows.