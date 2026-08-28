# EdgePilot — The AI Workflow

How one request actually travels through the system.

## The flow

```mermaid
sequenceDiagram
    actor User
    participant App as EdgePilot
    participant AI as Model
    participant Cluster

    User->>App: "Scale api to 5 replicas"
    App->>AI: request + cluster state + Skill rules + 40 tool definitions

    Note over AI: Reads the rules.<br/>Checks before acting.
    AI->>App: call inspect_kubernetes_deployment
    App->>Cluster: read
    Cluster-->>App: 3 replicas, capacity free
    App->>AI: here is what it returned

    AI->>App: "Proposal: 3 → 5 replicas.<br/>Reason, effect, risk.<br/>May I proceed?"
    App->>User: Approve or deny?

    alt Approved
        User->>App: Approve
        App->>AI: approved
        AI->>App: call scale_workload(api, 5)
        App->>Cluster: apply change
        Cluster-->>App: done
        App->>User: "Scaled api to 5 replicas"
    else Denied
        User->>App: Deny
        App->>AI: denied
        AI->>User: stops, explains
    end
```

## What each step costs

Every arrow from EdgePilot to the model is a **separate billed request** that
re-sends the whole context. A task that takes 4 round-trips costs roughly four
times a task that takes 1.

That is the single biggest driver of cost, and it is why the Skill matters so
much to the numbers — see [LLM experiments](llm-experiments.md).

## The rules the model follows

The Skill (`skills/kubernetes-control/SKILL.md`) tells the model to:

1. Inspect before acting — never assume the current state
2. Never guess a name; ask if a target is ambiguous
3. Refuse if the named thing does not exist
4. Explain the change, the reason, the expected effect and the risk
5. Request human approval before any control action

Rules 2 and 3 are safety. Rule 5 is what makes the approval gate work.

## Safety, in three layers

| Layer | What it stops |
|---|---|
| **The Skill** | The model proposing something reckless in the first place |
| **The approval gate** | The 14 high-impact operations configured in the backend running without human approval |
| **Read-only by default** | 25 of 40 tools cannot change anything at all |

The gate is not advisory. For those 14 operations, the backend blocks the call
until a person answers. The registry classifies 15 tools as state-changing in
total. The `ingest_historical_sample` tool is currently explicitly exempt from
the approval gate.

## What this workflow can and cannot do today

**Works now:** inspect a cluster, scale, restart, cordon a node, migrate a
workload, read metrics, propose and apply resource changes — all on Kubernetes.

**Not built:** creating arbitrary pods or directly scheduling individual pods.