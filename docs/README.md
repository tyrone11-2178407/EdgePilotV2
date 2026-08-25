# EdgePilot Project Evidence Index

This page provides a single entry point for reviewing EdgePilot's architecture, AI workflow, experiments, results, and current limitations.

## System and Workflow

- [System Architecture](architecture.md) — components, data flows, model providers, tool registry, approval gate, and infrastructure connections.
- [AI Workflow](ai-workflow.md) — request lifecycle from user prompt through model reasoning, tool execution, approval, and final response.
- [Kubernetes Skill Walkthrough](kubernetes-skill-walkthrough.md) — expected behavior, observed provider behavior, independent ground truth, and current limitations of the Kubernetes skill.
- [Kubernetes Control Skill](../skills/kubernetes-control/SKILL.md) — operational rules for safe inspection, planning, approval, execution, and verification.

## Experiments and Results

- [LLM Token and Cost Experiments](llm-experiments.md) — measured token overhead, model round trips, latency, caching impact, cost, and planned three-condition comparison.
- [AI Workflow Evaluation](AI_Workflow_Observations.md) — simple and complex Kubernetes workflow results, failure modes, manual interventions, and conclusions.
- [Kubernetes Evaluation Methodology](../evaluations/kubernetes/methodology.md) — experimental controls, evidence-capture procedure, scoring process, and reproducibility requirements.
- [Kubernetes Reliability Report](../evaluations/kubernetes/report.md) — identical-prompt consistency results and observed numerical-reporting limitations.
- [Evaluation Rubric](../evaluations/kubernetes/rubric.md) — criteria used to score tool selection, execution, safety, and answer accuracy.
- [Excluded Results](../evaluations/kubernetes/results/exclusions.md) — invalid or incomplete runs and the reasons they were excluded.

## Demonstration Materials

- [Architecture Diagram](../assets/architecture.png) — rendered overview of the EdgePilot system architecture.
- [Final Demonstration Video](../assets/EdgePilot_Final_Demo.mp4) — current end-to-end project demonstration.
- [Compressed Demonstration Video](../assets/EdgePilot_Demo_Compressed.mp4) — smaller demonstration file for easier review and sharing.
- [Installation Guide](../INSTALL.md) — environment setup, dependencies, and application startup instructions.
- [Main Project README](../README.md) — project overview, capabilities, example prompts, APIs, and repository structure.

## Historical Plans

- [Archive Plans](archive/plans/) — historical planning documents and checklists used during EdgePilot's development.

## Current Status and Limitations

- Kubernetes inspection and control workflows are implemented, including capacity inspection, scaling, restart, cordon, migration, and resource-request changes.
- High-impact operations remain protected by human approval. Fully autonomous mutation should be tested only in an isolated or simulated environment.
- The current Kubernetes reliability study covers one model, five runs, and a one-node test cluster. Its results should not be generalized to production clusters.
- Tool selection and execution succeeded in all five reliability runs, but only one run passed the strict numerical-accuracy rubric. Memory-unit conversion and arithmetic require additional hardening.
- The no-skill and skill-assisted token conditions have been measured. The fully agentic condition remains a prediction until the controlled comparison is completed.
- The planned 10, 100, and 1,000-node scaling study has not yet been completed.
