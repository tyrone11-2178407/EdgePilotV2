# EdgePilot AI Workflows: Demo Scenarios

This document outlines the three end-to-end AI workflow scenarios built into EdgePilot. These scenarios demonstrate EdgePilot's ability to transition from a simple Q&A assistant to an autonomous diagnostic agent capable of multi-step reasoning, historical metric analysis, and safe remediation via Human-in-the-Loop (HITL) gates.

## Scenario 1: Memory Anomaly Detection & Remediation

**Context:** A specific workload in the cluster is behaving abnormally, consuming significantly more memory than expected, risking node stability.

**Problem:** A pod is consuming 20 GB of memory instead of its expected baseline of 1 GB.

**AI Workflow Sequence:**
1. **Inspect (Read-only):** The user asks EdgePilot to investigate the cluster. EdgePilot queries the cluster state (`inspect_kubernetes_cluster`) and lists all pods and their current resource usage.
2. **Detect (Historical):** EdgePilot queries Prometheus (`query_prometheus`) to check historical memory trends, identifying pods where the actual memory usage over the last few hours heavily exceeds requested limits.
3. **Diagnose (Reasoning):** EdgePilot identifies the offending pod and compares its actual memory against its requested resources (`query_pod_resources`).
4. **Recommend (Reasoning):** The AI generates a remediation plan (e.g., restarting the pod to clear a memory leak or scaling down).
5. **Remediate (HITL Gate):** EdgePilot attempts to execute the restart (`restart_workload`). The system pauses and presents a Human-in-the-Loop approval prompt to the user.
6. **Verify (Read-only):** Upon user approval, the action executes. EdgePilot re-checks the pod's resource usage post-restart to confirm the anomaly is resolved.

---

## Scenario 2: Unexpected Open Network Ports (Security Audit)

**Context:** The NUIT team requires security auditing to ensure that running workloads only expose ports that are explicitly declared in their specifications.

**Problem:** A pod has unexpectedly opened network ports that were not defined in its Kubernetes pod specification, potentially indicating a compromised container.

**AI Workflow Sequence:**
1. **Scan (Read-only):** EdgePilot lists all pods and extracts their declared container port specifications from the Kubernetes API.
2. **Compare (Active execution):** EdgePilot executes `netstat` or `ss` inside the running containers (`run_shell_commands` via `kubectl exec`) to find the actual listening ports.
3. **Flag (Reasoning):** The LLM compares the declared ports against the actual listening ports and identifies discrepancies.
4. **Report (Reasoning):** EdgePilot generates a security report detailing the offending pod, the unexpected ports, and a risk assessment.
5. **Remediate (HITL Gate):** EdgePilot recommends cordoning the node to prevent scheduling or restarting the suspicious pod, waiting for explicit user approval before executing the action (`cordon_node` or `restart_workload`).

---

## Scenario 3: Daily Maintenance Health Check

**Context:** A cluster administrator wants a comprehensive "course of action" routine to run through standard checks daily.

**Problem:** Instead of manually querying multiple components, the admin needs an on-demand, comprehensive cluster health check.

**AI Workflow Sequence:**
1. **Trigger:** The user initiates the check by saying "Run the daily health check."
2. **Node Health (Read-only):** EdgePilot checks all nodes for `Ready` status, resource pressure (memory/disk), and taints (`inspect_kubernetes_cluster`).
3. **Memory Audit (Historical):** EdgePilot queries Prometheus (`query_prometheus`) for any pod that has sustained >80% of its memory limit over the last 24 hours.
4. **CPU Audit (Historical):** EdgePilot queries Prometheus for any pod that has sustained >90% CPU usage over the last 24 hours.
5. **Failed Pods (Read-only):** EdgePilot checks the cluster for pods stuck in `CrashLoopBackOff`, `ImagePullBackOff`, or `Error` states.
6. **Summary Report:** The AI aggregates the findings into a structured daily report, highlighting risk levels and recommending specific remediation actions for any identified issues.
