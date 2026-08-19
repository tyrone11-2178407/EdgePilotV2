"""Tool schemas for MCP function calling integration."""

from __future__ import annotations

from typing import Any, Dict, List

# Tool schemas following the function calling format for LLMs
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "gather_metrics",
        "description": "Collect a current snapshot of system metrics (CPU, memory, disk, network, battery, processes). Use ONLY for 'right now' status; do not use for multi-hour summaries.",
        "parameters": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Number of top processes by CPU usage to include. Defaults to 10. Ignored if all_processes is True.",
                    "default": 10,
                },
                "all_processes": {
                    "type": "boolean",
                    "description": "If True, include all running processes instead of just top N. Use this when you need complete process information.",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "report_edge_status",
        "description": "Summarize host utilization over a past window using Prometheus. Returns no_data when Prometheus history is unavailable.",
        "parameters": {
            "type": "object",
            "properties": {
                "window": {
                    "type": "string",
                    "description": "Prometheus time window to evaluate (e.g., '1h', '6h'). Defaults to 1h.",
                    "default": "1h",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of top processes or rank entries to include when applicable.",
                    "default": 5,
                },
            },
        },
    },
    {
        "name": "evaluate_capacity",
        "description": "Assess whether the host (or a Prometheus instance) currently has enough headroom for a workload.",
        "parameters": {
            "type": "object",
            "properties": {
                "requirements": {
                    "type": "object",
                    "description": "Workload requirements (e.g., {'cpu_pct': 40, 'mem_bytes': 2147483648}).",
                },
                "duration": {
                    "type": "string",
                    "description": "Intended runtime duration for the workload (Prometheus reference). Defaults to '45m'.",
                    "default": "45m",
                },
                "host": {
                    "type": "string",
                    "description": "Optional Prometheus instance label to check. Leave empty to evaluate all instances.",
                },
            },
            "required": ["requirements"],
        },
    },
    {
        "name": "suggest_capacity_window",
        "description": "Suggest upcoming windows when resource headroom is likely sufficient for a workload.",
        "parameters": {
            "type": "object",
            "properties": {
                "requirements": {
                    "type": "object",
                    "description": "Workload requirements (e.g., {'cpu_pct': 30, 'mem_bytes': 1073741824}).",
                },
                "duration": {
                    "type": "string",
                    "description": "Desired runtime duration (e.g., '1h'). Defaults to '45m'.",
                    "default": "45m",
                },
                "horizon_hours": {
                    "type": "integer",
                    "description": "How far ahead to look when suggesting windows. Defaults to 24 hours.",
                    "default": 24,
                },
                "host": {
                    "type": "string",
                    "description": "Optional Prometheus instance label to focus on.",
                },
            },
            "required": ["requirements"],
        },
    },
    {
        "name": "launch",
        "description": "Launch an application by name, immediately or after a delay. Cross-platform: on Windows we search Start Menu shortcuts and Microsoft Store apps; on macOS we resolve .app bundles and fall back to 'open -a <name>'; on Linux we scan .desktop files in standard locations (including Flatpak/Snap) and fall back to $PATH. Use simple names like 'chrome', 'safari', 'calculator', 'notepad'. If you need to check if an app exists first, use the 'search' tool.",
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Application name to launch (e.g., 'chrome', 'safari', 'calculator', 'discord'). The system searches Windows Start Menu/Store, macOS .app bundles, and Linux .desktop entries; if not found, it may try the name on $PATH.",
                },
                "delay_seconds": {
                    "type": "number",
                    "description": "Number of seconds to wait before launching. Default is 0 (launch immediately). Examples: 30 for '30 seconds', 120 for '2 minutes'.",
                    "default": 0,
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional chat session identifier so the launch can be surfaced on the jobs view.",
                },
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "search",
        "description": "Search for installed applications by name. Cross-platform: searches Windows Start Menu/Microsoft Store, macOS .app bundles, and Linux .desktop entries (including Flatpak/Snap). Returns friendly application names that you can pass to 'launch'.",
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Application name to search for (e.g., 'term', 'chrome', 'office'). Partial matches are supported.",
                },
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "list_apps",
        "description": "List all installed applications, optionally filtered by a search term. Cross-platform: enumerates Windows Start Menu, macOS .app bundles, and Linux .desktop entries. Returns a sorted list of friendly application names.",
        "parameters": {
            "type": "object",
            "properties": {
                "filter_term": {
                    "type": "string",
                    "description": "Optional search term to filter results (e.g., 'term', 'microsoft'). Leave empty to get all apps.",
                    "default": "",
                },
            },
        },
    },
    {
        "name": "run_shell_commands",
        "description": "Execute a shell command on the local machine. This tool records the job so the user can view it in the Jobs tab; do not attempt to fetch or summarize the output afterward.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command to execute (e.g., 'ls -la', 'df -h')."
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory for the command."
                },
                "delay_seconds": {
                    "type": "number",
                    "description": "Number of seconds to wait before running the command. Example: 30 for '30 seconds'.",
                    "default": 0,
                },
                "seconds": {
                    "type": "number",
                    "description": "Alias for delay_seconds. Use when a model extracts 'seconds' from the request.",
                },
                "delay": {
                    "type": "string",
                    "description": "Natural language delay value (e.g., 'in 45 seconds', 'after 2 minutes').",
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional chat session identifier so the job can be associated with a conversation thread.",
                },
            },
            "required": ["command"]
        }
    },
    {
        "name": "run_python_script",
        "description": "Execute a Python script using the scheduler runtime. Provide the script path and optional arguments. The user will view results in the Jobs tab; do not follow up with extra commands to read stdout/stderr.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the Python script."
                },
                "args": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "Optional list of command-line arguments for the script.",
                    "default": []
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory."
                },
                "delay_seconds": {
                    "type": "number",
                    "description": "Number of seconds to wait before running the script. Example: 30 for '30 seconds'.",
                    "default": 0,
                },
                "seconds": {
                    "type": "number",
                    "description": "Alias for delay_seconds. Use when a model extracts 'seconds' from input.",
                },
                "delay": {
                    "type": "string",
                    "description": "Natural language delay value (e.g., 'after 2 minutes').",
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional chat session identifier so the job can be associated with a conversation thread.",
                },
            },
            "required": ["path"]
        }
    },
    {
        "name": "end_task",
        "description": "Terminate running processes matching the identifier. The identifier can be part of the process name, executable path, or command line. Use this to stop applications, kill hung processes, or clean up resources.",
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Process identifier to match. Can be part of the process name (e.g., 'notepad'), executable path (e.g., 'C:\\Program Files\\App\\'), or command line arguments. Matching is case-insensitive.",
                },
                "force": {
                    "type": "boolean",
                    "description": "If True, forcefully kill processes (SIGKILL). If False, gracefully terminate (SIGTERM). Default is False.",
                    "default": False,
                },
            },
            "required": ["identifier"],
        },
    },
{
    "name": "inspect_kubernetes_cluster",
    "description": (
        "Inspect Kubernetes nodes, schedulable capacity, Pod resource "
        "requests, taints, readiness, and available Pod slots. This is "
        "read-only and reports schedulable headroom rather than live "
        "CPU or memory utilization."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
},
{
    "name": "evaluate_kubernetes_workload",
    "description": (
        "Evaluate whether a Kubernetes workload can fit on one or more "
        "nodes using CPU, memory, Pod slots, readiness, schedulability, "
        "taints, and tolerations. This operation is read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "requirements": {
                "type": "object",
                "description": "The workload's Kubernetes resource requirements.",
                "properties": {
                    "cpu_cores": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Required CPU cores.",
                    },
                    "memory_bytes": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Required memory in bytes.",
                    },
                    "pods": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 1,
                        "description": "Required Pod slots.",
                    },
                    "tolerations": {
                        "type": "array",
                        "description": "Optional Kubernetes tolerations.",
                        "items": {
                            "type": "object",
                        },
                    },
                },
                "required": [
                    "cpu_cores",
                    "memory_bytes",
                ],
            },
            "node": {
                "type": "string",
                "description": "Optional exact node name to evaluate.",
            },
        },
        "required": ["requirements"],
    },
},
    {
        "name": "inspect_kubernetes_deployment",
        "description": (
            "Inspect the desired, ready, available, updated, and unavailable "
            "replicas of an exact Kubernetes Deployment. This operation is "
            "read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Exact Kubernetes namespace.",
                },
                "deployment_name": {
                    "type": "string",
                    "description": "Exact Deployment name.",
                },
            },
            "required": [
                "namespace",
                "deployment_name",
            ],
        },
    },
    {
        "name": "scale_workload",
        "description": "Scales a Kubernetes deployment up or down. Requires human approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "The Kubernetes namespace.",
                    "default": "default"
                },
                "deployment_name": {
                    "type": "string",
                    "description": "The name of the deployment to scale."
                },
                "replicas": {
                    "type": "number",
                    "description": "The target number of replicas."
                }
            },
            "required": ["deployment_name", "replicas"]
        }
    },
    {
        "name": "restart_workload",
        "description": "Performs a rolling restart of a Kubernetes deployment. Requires human approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "The Kubernetes namespace.",
                    "default": "default"
                },
                "deployment_name": {
                    "type": "string",
                    "description": "The name of the deployment to restart."
                }
            },
            "required": ["deployment_name"]
        }
    },
    {
        "name": "plan_cluster_rebalance",
        "description": "Survey every node and propose an ordered plan to relieve any node under pressure. Read-only: it proposes, it does not act. Prefers reducing an over-sized reservation (which restarts nothing) over moving a workload (which does). Refuses to propose moving a StatefulSet, a Job, or anything with attached storage, because a restart destroys work in progress. Returns the pressured nodes, the ordered steps with a reason for each, and notes explaining anything considered and rejected. Execute the steps with scale_workload, apply_resource_requests or migrate_workload, each of which requires its own approval.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "migrate_workload",
        "description": "Move a Kubernetes deployment onto a specific node and verify it lands. Refuses if the target node does not exist, is cordoned, or lacks free CPU/memory for the workload. Kubernetes performs the move as a rolling update, so the new pod starts and becomes ready before the old one stops. Returns success only after every replica is ready on the new placement; a stuck rollout is reported as a failure. Requires human approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "The Kubernetes namespace.",
                    "default": "default"
                },
                "deployment_name": {
                    "type": "string",
                    "description": "The name of the deployment to migrate."
                },
                "target_node": {
                    "type": "string",
                    "description": "The target node to migrate the deployment to."
                }
            },
            "required": ["deployment_name", "target_node"]
        }
    },
    {
        "name": "cordon_node",
        "description": "Marks a Kubernetes node as unschedulable. Requires human approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_name": {
                    "type": "string",
                    "description": "The name of the node to cordon."
                }
            },
            "required": ["node_name"]
        }
    },
    {
        "name": "preview_free_disk_space",
        "description": "(SAFE) Scans system temp files and browser caches to report what CAN be deleted and how much space it will free. Call this first before execute_free_disk_space.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "execute_free_disk_space",
        "description": "(HITL REQUIRED) Actually executes the deletion of system temp files. You MUST provide the exact paths to delete, obtained from preview_free_disk_space.",
        "parameters": {
            "type": "object",
            "properties": {
                "paths_to_delete": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of absolute paths to delete. Must be obtained from preview_free_disk_space."
                }
            },
            "required": ["paths_to_delete"]
        }
    },
    {
        "name": "hibernate_background_apps",
        "description": "(HITL REQUIRED) Identifies and suspends/kills heavy background processes (e.g., Slack, Docker Desktop).",
        "parameters": {
            "type": "object",
            "properties": {
                "app_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of application names to hibernate."
                }
            },
            "required": ["app_names"]
        }
    },
    {
        "name": "analyze_network_hogs",
        "description": "Queries active network connections to find apps silently downloading/uploading massive amounts of data in the background.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "query_slurm_jobstats",
        "description": "Returns time-series metrics (CPU, Memory, GPU) and labels (partition, QOS, node) for specific Slurm jobs.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The Slurm job ID."
                }
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "query_slurm_accounting",
        "description": "Returns Slurm job lifecycles (submit, start, end, exit code, requested vs used TRES).",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The Slurm job ID."
                }
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "slurm_queue_snapshot",
        "description": "Returns pending/running job counts by partition, top waiters, and fairshare info.",
        "parameters": {
            "type": "object",
            "properties": {
                "partition": {
                    "type": "string",
                    "description": "The partition name to query (default: all).",
                    "default": "all"
                }
            }
        }
    },
    {
        "name": "query_node_exporter_subset",
        "description": "Returns a curated subset of prometheus metrics to evaluate hardware pressure.",
        "parameters": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "The node name."
                }
            },
            "required": ["node"]
        }
    },
    {
        "name": "query_node_specs",
        "description": "Returns hardware specs (cores, total memory, GPUs) to compare limits vs actual usage.",
        "parameters": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "The node name."
                }
            },
            "required": ["node"]
        }
    },
    {
        "name": "cancel_slurm_job",
        "description": "(HITL REQUIRED) Safely cancel a stalled or hoarding Slurm job to immediately reclaim wasted resources.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The Slurm job ID."
                }
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "update_slurm_job_qos",
        "description": "(HITL REQUIRED) Demote a non-critical resource-heavy Slurm job to a lower priority QoS to relieve queue contention.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The Slurm job ID."
                },
                "new_qos": {
                    "type": "string",
                    "description": "The new QoS level."
                }
            },
            "required": ["job_id", "new_qos"]
        }
    },
    {
        "name": "compare_job_efficiency",
        "description": "Compares Requested vs Actual usage for a Slurm job to calculate waste percentage.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The Slurm job ID"
                }
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "query_cluster_incidents",
        "description": "Scrapes Slurm logs for OOMs, Node Fails, and Preemptions over the last N hours.",
        "parameters": {
            "type": "object",
            "properties": {
                "hours_back": {
                    "type": "integer",
                    "description": "Hours to look back (default 24)"
                }
            }
        }
    },
    {
        "name": "ingest_historical_sample",
        "description": "Parses a CSV of historical jobs to enable offline optimization simulations.",
        "parameters": {
            "type": "object",
            "properties": {
                "csv_file_path": {
                    "type": "string",
                    "description": "Path to the CSV file"
                }
            },
            "required": ["csv_file_path"]
        }
    },
    {
        "name": "analyze_oomkilled_pods",
        "description": "Identify which K8s pods crashed due to memory limits and simulate how much more memory they actually need.",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "The Kubernetes namespace.",
                    "default": "default"
                }
            }
        }
    },
    {
        "name": "drain_k8s_node",
        "description": "(HITL REQUIRED) Safely evict all workloads off a dying Kubernetes node before it crashes.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_name": {
                    "type": "string",
                    "description": "The name of the node to drain."
                }
            },
            "required": ["node_name"]
        }
    },
    {
        "name": "query_ray_workers",
        "description": "Check if GPU workers in a Ray cluster are actually saturated or sitting idle.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "list_skills",
        "description": (
            "List project-local EdgePilot Skills and their descriptions. "
            "Use this when determining whether a specialized workflow is "
            "available for a request."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "load_skill",
        "description": (
            "Load the complete instructions for an exact project-local "
            "EdgePilot Skill. Load a relevant Skill before following its "
            "specialized workflow."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Exact Skill name returned by list_skills, such as "
                        "'kubernetes-control'."
                    ),
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "recommend_rightsizing",
        "description": (
            "Compare what each workload requests against what it actually "
            "consumes, and recommend corrected CPU, memory and GPU sizing. "
            "Works over Slurm job accounting, an exported accounting CSV, or "
            "Kubernetes. Flags over-requested, under-requested, OOM-killed, "
            "idle-GPU and no-requests-set workloads, and reports total "
            "reclaimable resources. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "Which scheduler to read: 'slurm', 'kubernetes', "
                        "'csv' for an exported accounting file, or 'auto' to "
                        "use whichever is reachable."
                    ),
                    "default": "auto"
                },
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to restrict analysis to."
                },
                "csv_path": {
                    "type": "string",
                    "description": (
                        "Path to a sacct-shaped CSV export. Required when "
                        "source is 'csv'."
                    )
                },
                "node_csv_path": {
                    "type": "string",
                    "description": (
                        "Optional path to a node-specification CSV, enabling "
                        "node-class fit analysis."
                    )
                },
                "jobstats_path": {
                    "type": "string",
                    "description": (
                        "Optional path to a Jobstats time-series JSON export, "
                        "which yields more accurate usage than accounting "
                        "summaries alone."
                    )
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days of Slurm accounting to analyze.",
                    "default": 7
                },
                "cpu_target_utilization": {
                    "type": "number",
                    "description": "Target CPU utilization ratio. Defaults to 0.7.",
                    "default": 0.7
                },
                "memory_target_utilization": {
                    "type": "number",
                    "description": "Target memory utilization ratio. Defaults to 0.8.",
                    "default": 0.8
                },
                "gpu_target_utilization": {
                    "type": "number",
                    "description": "Target GPU utilization ratio. Defaults to 0.7.",
                    "default": 0.7
                }
            },
            "required": []
        }
    },
    {
        "name": "analyze_bottlenecks",
        "description": (
            "Identify which resource actually limits each workload - CPU, "
            "memory, GPU, or none - and roll the findings up by partition. "
            "Answers 'where are the bottlenecks in this cluster', as distinct "
            "from recommend_rightsizing's 'is this workload the right size'. "
            "Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "Which scheduler to read: 'slurm', 'kubernetes', "
                        "'csv', or 'auto'."
                    ),
                    "default": "auto"
                },
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to restrict analysis to."
                },
                "csv_path": {
                    "type": "string",
                    "description": "Path to a sacct-shaped CSV export."
                },
                "node_csv_path": {
                    "type": "string",
                    "description": "Optional path to a node-specification CSV."
                },
                "jobstats_path": {
                    "type": "string",
                    "description": (
                        "Optional path to a Jobstats time-series JSON export."
                    )
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days of Slurm accounting to analyze.",
                    "default": 7
                }
            },
            "required": []
        }
    },
    {
        "name": "analyze_workload_families",
        "description": (
            "Group jobs into families of similar workloads using a local "
            "embedding model, then flag the runs that deviate from their own "
            "family - e.g. 'this run used a twentieth of the memory of the "
            "other 37 jobs like it'. Peer-relative counterpart to "
            "recommend_rightsizing: judges a job against its peers rather "
            "than a fixed utilization target, so it needs no arbitrary "
            "threshold. Falls back to grouping by job name when the local "
            "model is unavailable, and says so in the 'degraded' field. "
            "Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "Which scheduler to read: 'slurm', 'kubernetes', "
                        "'csv', or 'auto'."
                    ),
                    "default": "auto"
                },
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to restrict analysis to."
                },
                "csv_path": {
                    "type": "string",
                    "description": "Path to a sacct-shaped CSV export."
                },
                "node_csv_path": {
                    "type": "string",
                    "description": "Optional path to a node-specification CSV."
                },
                "jobstats_path": {
                    "type": "string",
                    "description": (
                        "Optional path to a Jobstats time-series JSON export."
                    )
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days of Slurm accounting to analyze.",
                    "default": 7
                },
                "similarity_threshold": {
                    "type": "number",
                    "description": (
                        "How alike two jobs must be to share a family, "
                        "0 to 1. Higher means tighter families."
                    ),
                    "default": 0.75
                },
                "anomaly_threshold": {
                    "type": "number",
                    "description": (
                        "Robust outlier score above which a run is flagged. "
                        "Defaults to 3.5."
                    ),
                    "default": 3.5
                },
                "min_family_size": {
                    "type": "integer",
                    "description": (
                        "Smallest family that can produce outliers; below "
                        "this a median is not meaningful."
                    ),
                    "default": 4
                }
            },
            "required": []
        }
    },
    {
        "name": "apply_resource_requests",
        "description": (
            "Update the CPU/memory requests and limits of a container in a "
            "Kubernetes deployment. Use the quantity strings returned by "
            "recommend_rightsizing. Requires human approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "The Kubernetes namespace.",
                    "default": "default"
                },
                "deployment_name": {
                    "type": "string",
                    "description": "The deployment to update."
                },
                "container_name": {
                    "type": "string",
                    "description": "The container within the pod template."
                },
                "cpu_request": {
                    "type": "string",
                    "description": "CPU request quantity, e.g. '500m'."
                },
                "memory_request": {
                    "type": "string",
                    "description": "Memory request quantity, e.g. '512Mi'."
                },
                "cpu_limit": {
                    "type": "string",
                    "description": "CPU limit quantity, e.g. '1'."
                },
                "memory_limit": {
                    "type": "string",
                    "description": "Memory limit quantity, e.g. '1Gi'."
                }
            },
            "required": ["deployment_name", "container_name"]
        }
    }
]


def get_tool_schema(tool_name: str) -> Dict[str, Any] | None:
    """Get schema for a specific tool by name."""
    for schema in TOOL_SCHEMAS:
        if schema["name"] == tool_name:
            return schema
    return None


def get_all_tool_schemas() -> List[Dict[str, Any]]:
    """Get all available tool schemas."""
    return TOOL_SCHEMAS.copy()


def format_tools_for_gemini() -> List[Dict[str, Any]]:
    """
    Format tool schemas for Gemini function calling API.

    Gemini expects a different format than the standard function calling schema.
    """
    gemini_tools = []
    for schema in TOOL_SCHEMAS:
        gemini_tool = {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema["parameters"],
        }
        gemini_tools.append(gemini_tool)
    return gemini_tools


def format_tools_for_claude() -> List[Dict[str, Any]]:
    """
    Format tool schemas for Claude function calling API.

    Claude uses a specific tool format.
    """
    claude_tools = []
    for schema in TOOL_SCHEMAS:
        claude_tool = {
            "name": schema["name"],
            "description": schema["description"],
            "input_schema": schema["parameters"],
        }
        claude_tools.append(claude_tool)
    return claude_tools


def format_tools_for_provider(provider: Any) -> List[Dict[str, Any]]:
    """Return tool schemas in the shape *this* provider's API expects.

    Claude needs ``input_schema``; Gemini needs ``parameters``. Passing the
    raw schemas to Claude returns a 400 on every request
    (``tools.0.custom.input_schema: Field required``) — and because Gemini
    accepts the raw shape, the mistake looks like a Claude-specific model
    failure rather than a formatting bug.

    Every caller that hands schemas to a provider should use this rather than
    branching on the provider id itself; duplicating that branch is what let
    the runner and the app disagree.
    """
    try:
        provider_id = (type(provider).describe() or {}).get("id", "")
    except Exception:  # noqa: BLE001 - an odd provider falls back to raw
        provider_id = ""

    if provider_id == "claude":
        return format_tools_for_claude()

    if provider_id == "gemini":
        return format_tools_for_gemini()

    return get_all_tool_schemas()


# ── Which tools change something ────────────────────────────────────────
# The single source of truth for "this call has a side effect", used by the
# HITL approval gate in main.py and by the reliability scorer. It lives here,
# next to the schemas, because two hand-maintained copies drift: the scorer
# once carried its own read-only allowlist, and every tool missing from it
# (``preview_free_disk_space``, ``query_slurm_accounting``, ...) was scored as
# an unsafe action a model never took.
MUTATING_TOOLS = {
    "scale_workload",
    "restart_workload",
    "cordon_node",
    "drain_k8s_node",
    "migrate_workload",
    "apply_resource_requests",
    "run_shell_commands",
    "run_python_script",
    "execute_free_disk_space",
    "hibernate_background_apps",
    "cancel_slurm_job",
    "update_slurm_job_qos",
    "ingest_historical_sample",
    "launch",
    "end_task",
}


def is_mutating(tool_name: str) -> bool:
    """True when calling *tool_name* changes state.

    Unknown names count as mutating. A tool added without being classified
    should fail closed — needing an approval it did not need is recoverable,
    silently acting without one is not.
    """
    if tool_name in MUTATING_TOOLS:
        return True

    known = {schema["name"] for schema in get_all_tool_schemas()}
    return tool_name not in known
