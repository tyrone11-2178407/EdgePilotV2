---
name: kubernetes-control
description: Safely inspect and control Kubernetes clusters through EdgePilot. Load this skill for Kubernetes, K8s, cluster capacity, Pod placement, Deployment scaling, rolling restarts, node cordoning, rightsizing, or troubleshooting requests.
---

# Kubernetes Control

Treat Kubernetes control as a staged workflow. Keep observation separate from
mutation and use only EdgePilot's typed Kubernetes tools. Never construct or run
arbitrary kubectl or shell commands.

## Workflow

1. Identify the user's goal and exact Kubernetes target.
2. Observe the relevant live state using read-only tools.
3. Assess whether the evidence supports an action.
4. Explain the proposed mutation, reason, expected effect, and risk.
5. Request human approval.
6. Execute only what was approved. A single request normally means a single
   mutation. A rebalance may need several, in which case list every step in
   one plan and obtain approval for the plan before executing any of it.
7. Re-observe the target and report the verified result.

Do not claim success from the mutation request alone. A successful API response
only means Kubernetes accepted the request.

## Read-only tools

- Use `inspect_kubernetes_cluster` for node health, capacity, taints, requests,
  limits, and Pod slots.
- Use `evaluate_kubernetes_workload` for workload-placement questions.
- Use `inspect_kubernetes_deployment` before and after scaling or restarting.

Read-only inspection does not require approval.

## Control tools

- Use `scale_workload` only with an exact namespace, Deployment, and replica count.
- Use `restart_workload` only with an exact namespace and Deployment.
- Use `cordon_node` only with an exact node observed in the current cluster.
- Use `migrate_workload` to move a Deployment to a node with room. Only
  Deployments may be moved; see "Moving workloads" below.
- Use `apply_resource_requests` to reduce an over-sized reservation, using the
  values returned by `recommend_rightsizing`. Never invent request values.

Every control tool requires human approval. Never replace these tools with
`run_shell_commands` or `run_python_script`.

## Moving workloads

Moving a workload restarts it. Kubernetes starts the new Pod, waits for it to
become ready, then stops the old one, so a service sees no interruption.

A computation is different: it loses everything it has done and starts from the
beginning. That work cannot be recovered.

Only move Deployments. Refuse to move, and say why, when the target is:

- a StatefulSet, or anything holding data of its own
- a Job, CronJob, or any long-running computation
- any workload with a PersistentVolumeClaim attached

Before proposing a move, confirm the receiving node exists, is schedulable, and
has enough request headroom for the workload. Never move a workload onto a node
that merely looks less busy — a Pod that cannot be scheduled leaves the
Deployment stuck part-moved.

## Rebalancing a cluster

When one node is under pressure and others have room, there are two remedies.
Prefer the first.

1. **Reduce an over-sized reservation.** Nothing moves, nothing restarts, no
   work is lost. A workload reserving far more than it uses blocks that
   capacity from everyone else even while idle. Most "full" clusters are full
   of reservations rather than of running work.
2. **Move a workload to a node with room.** Only when reducing reservations is
   insufficient, and only subject to every condition above.

Procedure:

- Inspect every node, not only the one reported as stressed.
- Identify over-sized reservations across the cluster.
- Choose reduction over movement wherever both would work.
- Verify the receiving node has room before proposing any move.
- Present one plan covering every step, with the reason for each.
- Report what actually changed, including any step that failed.

If neither remedy resolves the pressure, say so plainly rather than acting.

## Safety rules

- Never guess names, namespaces, nodes, replica counts, or resource requests.
- Do not assume the `default` namespace unless the user confirms it.
- Do not scale from CPU percentage alone.
- Do not restart a busy workload unless it is unhealthy or the user explicitly
  requests the restart.
- Explain that cordoning stops new scheduling but does not evict existing Pods.
- Stop if cluster inspection fails.
- Ask for clarification when a target is ambiguous.
- Describe capacity from `inspect_kubernetes_cluster` as schedulable headroom
  based on Pod resource requests. Do not describe it as real-time CPU or memory
  availability unless live utilization metrics are provided by Prometheus.
- Namespace is required for a named workload or namespaced operation, but not for a cluster-wide capacity question.
- When the user supplies an exact deployment name and namespace, call
  `inspect_kubernetes_deployment`; do not substitute `inspect_kubernetes_cluster`.
- Cluster-wide inspection cannot prove that a named deployment is absent.
- Report a deployment as not found only when `inspect_kubernetes_deployment`
  returns not found for that exact name and namespace.
- For deployment-health questions, report deployment replicas and pod health;
  omit unrelated cluster-capacity statistics.
- Use binary Kubernetes memory conversion: 1024 MiB equals 1 GiB.
- Label allocatable minus existing requests as "request headroom," not simply
  "available" or "free."
- Phrase a positive result as "fits based on resource requests and the stated
  placement constraints."
- Treat `migrate_workload` as successful only when it reports every replica
  ready on the new placement; any other result is a failed move.
- After `apply_resource_requests`, re-read the Deployment and confirm the
  container's requests match the approved values.
- Never reduce a GPU reservation.
- Never say that pods "will be scheduled"; admission policies, quotas, runtime
  state, and other scheduler constraints may still prevent placement.
