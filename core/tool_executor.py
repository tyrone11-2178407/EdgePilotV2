"""Tool executor for MCP function calling.

Provides both synchronous and asynchronous execution paths.
The async path (execute_async / execute_batch) allows multiple independent
tool calls to run concurrently using asyncio.gather(), cutting total
latency when the LLM emits several tool calls in a single turn.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from tools import (
    end_task,
    evaluate_capacity,
    gather_metrics,
    launch,
    list_apps,
    report_edge_status,
    run_python_script as launcher_run_python,
    run_shell_commands as launcher_run_shell,
    search,
    suggest_capacity_window,
)
from tools.kubernetes_actions import scale_workload, restart_workload, cordon_node
from tools.kubernetes import (
    evaluate_kubernetes_workload,
    inspect_kubernetes_cluster,
    inspect_kubernetes_deployment,
)
from tools.skills import list_skills, load_skill
from tools.prometheus import query_prometheus, query_pod_resources
from tools import (
    preview_free_disk_space,
    execute_free_disk_space,
    hibernate_background_apps,
    analyze_network_hogs,
    query_slurm_jobstats,
    query_slurm_accounting,
    slurm_queue_snapshot,
    query_node_exporter_subset,
    query_node_specs,
    cancel_slurm_job,
    update_slurm_job_qos,
    compare_job_efficiency,
    query_cluster_incidents,
    ingest_historical_sample,
    analyze_oomkilled_pods,
    drain_k8s_node,
    query_ray_workers,
)


class ToolExecutor:
    """Execute tool calls from LLM responses."""

    def __init__(self):
        """Initialize the tool executor with available tools."""
        self.tools = {
            "gather_metrics": self._execute_gather_metrics,
            "report_edge_status": self._execute_report_edge_status,
            "evaluate_capacity": self._execute_evaluate_capacity,
            "suggest_capacity_window": self._execute_suggest_capacity_window,
            "launch": self._execute_launch,
            "search": self._execute_search,
            "list_apps": self._execute_list_apps,
            "end_task": self._execute_end_task,
            "run_shell_commands": self._execute_run_shell,
            "run_python_script": self._execute_run_python,
            "preview_free_disk_space": self._execute_preview_free_disk_space,
            "execute_free_disk_space": self._execute_execute_free_disk_space,
            "hibernate_background_apps": self._execute_hibernate_background_apps,
            "analyze_network_hogs": self._execute_analyze_network_hogs,
            "query_slurm_jobstats": self._execute_query_slurm_jobstats,
            "query_slurm_accounting": self._execute_query_slurm_accounting,
            "slurm_queue_snapshot": self._execute_slurm_queue_snapshot,
            "query_node_exporter_subset": self._execute_query_node_exporter_subset,
            "query_node_specs": self._execute_query_node_specs,
            "cancel_slurm_job": self._execute_cancel_slurm_job,
            "update_slurm_job_qos": self._execute_update_slurm_job_qos,
            "compare_job_efficiency": self._execute_compare_job_efficiency,
            "query_cluster_incidents": self._execute_query_cluster_incidents,
            "ingest_historical_sample": self._execute_ingest_historical_sample,
            "analyze_oomkilled_pods": self._execute_analyze_oomkilled_pods,
            "drain_k8s_node": self._execute_drain_k8s_node,
            "query_ray_workers": self._execute_query_ray_workers,
            "inspect_kubernetes_cluster": self._execute_inspect_kubernetes_cluster,
            "evaluate_kubernetes_workload": self._execute_evaluate_kubernetes_workload,
            "inspect_kubernetes_deployment": self._execute_inspect_kubernetes_deployment,
            "scale_workload": self._execute_scale_workload,
            "restart_workload": self._execute_restart_workload,
            "cordon_node": self._execute_cordon_node,
            "list_skills": self._execute_list_skills,
            "load_skill": self._execute_load_skill,
            "query_prometheus": self._execute_query_prometheus,
            "query_pod_resources": self._execute_query_pod_resources,
        }

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool call synchronously.

        This is the original blocking path, kept for backward compatibility
        with the CLI and existing REST endpoints.
        """
        if tool_name not in self.tools:
            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}",
                "available_tools": list(self.tools.keys()),
            }

        try:
            result = self.tools[tool_name](arguments)
            return {
                "success": True,
                "tool": tool_name,
                "arguments": arguments,
                "result": result,
            }
        except Exception as error:
            return {
                "success": False,
                "tool": tool_name,
                "arguments": arguments,
                "error": str(error),
                "error_type": type(error).__name__,
            }

    # ------------------------------------------------------------------ #
    # Async execution paths                                               #
    # ------------------------------------------------------------------ #

    async def execute_async(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run a single tool asynchronously in a thread-pool.

        Tool functions are synchronous (psutil, subprocess, etc.), so we
        delegate them to the default executor to avoid blocking the event
        loop while still benefiting from concurrency.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute, tool_name, arguments
        )

    async def execute_batch(
        self,
        calls: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Run multiple independent tool calls concurrently.

        Parameters
        ----------
        calls:
            A list of dicts, each containing 'name' and 'arguments'.

        Returns
        -------
        A list of result dicts in the same order as the input calls.
        """
        tasks = [
            self.execute_async(call["name"], call.get("arguments", {}))
            for call in calls
        ]
        return await asyncio.gather(*tasks)

    def _execute_gather_metrics(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute gather_metrics tool."""
        top_n = args.get("top_n", 10)
        all_processes = args.get("all_processes", False)
        return gather_metrics(top_n=top_n, all_processes=all_processes)

    def _execute_report_edge_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        window = args.get("window", "1h")
        top_k = args.get("top_k", 5)
        return report_edge_status(window=window, top_k=top_k)

    def _execute_evaluate_capacity(self, args: Dict[str, Any]) -> Dict[str, Any]:
        requirements = args.get("requirements") or {}
        if not isinstance(requirements, dict):
            raise ValueError("requirements must be an object")
        duration = args.get("duration", "45m")
        host = args.get("host")
        return evaluate_capacity(requirements, duration=duration, host=host)

    def _execute_suggest_capacity_window(self, args: Dict[str, Any]) -> Dict[str, Any]:
        requirements = args.get("requirements") or {}
        if not isinstance(requirements, dict):
            raise ValueError("requirements must be an object")
        duration = args.get("duration", "45m")
        horizon_hours = int(args.get("horizon_hours", 24) or 24)
        host = args.get("host")
        return suggest_capacity_window(
            requirements,
            duration=duration,
            horizon_hours=horizon_hours,
            host=host,
        )

    def _execute_inspect_kubernetes_cluster(
            self,
            args: Dict[str, Any],
    ) -> Dict[str, Any]:
        return inspect_kubernetes_cluster()

    def _execute_evaluate_kubernetes_workload(
            self,
            args: Dict[str, Any],
    ) -> Dict[str, Any]:
        requirements = args.get("requirements")

        if not isinstance(requirements, dict):
            raise ValueError("requirements must be an object")

        node = args.get("node")

        return evaluate_kubernetes_workload(
            requirements=requirements,
            node=node,
        )

    def _execute_inspect_kubernetes_deployment(
            self,
            args: Dict[str, Any],
    ) -> Dict[str, Any]:
        namespace = args.get("namespace")
        deployment_name = args.get("deployment_name")

        if not namespace:
            raise ValueError("namespace is required")

        if not deployment_name:
            raise ValueError("deployment_name is required")

        return inspect_kubernetes_deployment(
            namespace=namespace,
            deployment_name=deployment_name,
        )

    def _execute_scale_workload(self, args: Dict[str, Any]) -> Dict[str, Any]:
        namespace = args.get("namespace", "default")
        deployment_name = args.get("deployment_name")
        replicas = args.get("replicas")
        if not deployment_name or replicas is None:
            raise ValueError("deployment_name and replicas are required")
        return scale_workload(namespace, deployment_name, int(replicas))

    def _execute_restart_workload(self, args: Dict[str, Any]) -> Dict[str, Any]:
        namespace = args.get("namespace", "default")
        deployment_name = args.get("deployment_name")
        if not deployment_name:
            raise ValueError("deployment_name is required")
        return restart_workload(namespace, deployment_name)

    def _execute_cordon_node(self, args: Dict[str, Any]) -> Dict[str, Any]:
        node_name = args.get("node_name")
        if not node_name:
            raise ValueError("node_name is required")
        return cordon_node(node_name)

    def _execute_launch(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute launch tool to start an application."""
        app_name = args.get("app_name")
        if not app_name:
            raise ValueError("app_name parameter is required")

        delay_seconds = args.get("delay_seconds", 0)
        chat_id = args.get("chat_id")

        # Use launcher.py's launch function
        success = launch(app_name, delay_seconds, chat_id=chat_id)

        if success:
            if delay_seconds > 0:
                message = f"Scheduled '{app_name}' to launch in {delay_seconds} seconds"
            else:
                message = f"Launched '{app_name}'"

            payload = {
                "success": True,
                "message": message,
                "app_name": app_name,
                "delay_seconds": delay_seconds,
            }
            return payload
        else:
            return {
                "success": False,
                "error": f"Could not find or launch '{app_name}'",
                "app_name": app_name,
            }

    def _execute_end_task(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute end_task tool."""
        identifier = args.get("identifier")
        if not identifier:
            raise ValueError("identifier parameter is required")

        force = args.get("force", False)
        exact_path = args.get("exact_path", False)

        return end_task(identifier=identifier, force=force, exact_path=exact_path)

    def _execute_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute search tool to find installed applications."""
        app_name = args.get("app_name")
        if not app_name:
            raise ValueError("app_name parameter is required")

        results = search(app_name)

        return {
            "query": app_name,
            "found": len(results),
            "apps": results,
        }

    def _execute_list_apps(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute list_apps tool to list all installed applications."""
        filter_term = args.get("filter_term", "")

        results = list_apps(filter_term)

        return {
            "filter_term": filter_term,
            "count": len(results),
            "apps": results,
        }

    def _execute_run_shell(self, args: Dict[str, Any]) -> Dict[str, Any]:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command parameter is required")
        cwd = args.get("cwd")
        chat_id = args.get("chat_id")

        # Prevent the model from trying to "cat" scheduler temp outputs; direct to Jobs tab instead.
        blocked_output_peek = (
            "/tmp/run_python_script_" in command and "output" in command
        )
        if blocked_output_peek:
            return {
                "status": "blocked",
                "message": "Job output is available in the Jobs tab. No need to read temp files.",
                "command": command,
            }

        raw = launcher_run_shell(
            command,
            cwd=cwd,
            delay_seconds=args.get("delay_seconds"),
            seconds=args.get("seconds"),
            delay=args.get("delay"),
            chat_id=chat_id,
        )
        return {
            "status": raw.get("status"),
            "task_id": raw.get("task_id") or raw.get("run_id"),
            "run_id": raw.get("run_id") or raw.get("task_id"),
            "action": "run_shell_commands",
            "command": raw.get("command"),
            "delay_seconds": raw.get("delay_seconds"),
            "message": "Shell command recorded. Check the Jobs tab for progress and output.",
        }

    def _execute_run_python(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path parameter is required")
        script_args = args.get("args")
        if script_args is not None and not isinstance(script_args, list):
            raise ValueError("args must be a list when provided")
        cwd = args.get("cwd")
        chat_id = args.get("chat_id")
        raw = launcher_run_python(
            path,
            args=script_args,
            cwd=cwd,
            delay_seconds=args.get("delay_seconds"),
            seconds=args.get("seconds"),
            delay=args.get("delay"),
            chat_id=chat_id,
        )
        return {
            "status": raw.get("status"),
            "task_id": raw.get("task_id") or raw.get("run_id"),
            "run_id": raw.get("run_id") or raw.get("task_id"),
            "action": "run_python_script",
            "path": raw.get("path"),
            "delay_seconds": raw.get("delay_seconds"),
            "message": "Python job recorded. Check the Jobs tab for progress and output.",
        }

    def _execute_preview_free_disk_space(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return preview_free_disk_space()

    def _execute_execute_free_disk_space(self, args: Dict[str, Any]) -> Dict[str, Any]:
        paths = args.get("paths_to_delete", [])
        return execute_free_disk_space(paths)

    def _execute_hibernate_background_apps(self, args: Dict[str, Any]) -> Dict[str, Any]:
        app_names = args.get("app_names")
        if not app_names:
            raise ValueError("app_names parameter is required")
        return hibernate_background_apps(app_names)

    def _execute_analyze_network_hogs(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return analyze_network_hogs()

    def _execute_query_slurm_jobstats(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return query_slurm_jobstats(args.get("job_id", ""))

    def _execute_query_slurm_accounting(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return query_slurm_accounting(args.get("job_id", ""))

    def _execute_slurm_queue_snapshot(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return slurm_queue_snapshot(args.get("partition", "all"))

    def _execute_query_node_exporter_subset(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return query_node_exporter_subset(args.get("node", ""))

    def _execute_query_node_specs(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return query_node_specs(args.get("node", ""))

    def _execute_cancel_slurm_job(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return cancel_slurm_job(args.get("job_id", ""))

    def _execute_update_slurm_job_qos(self, args: Dict[str, Any]) -> Dict[str, Any]:
        job_id = args.get("job_id")
        new_qos = args.get("new_qos")
        return update_slurm_job_qos(job_id, new_qos)

    def _execute_compare_job_efficiency(self, args: Dict[str, Any]) -> Dict[str, Any]:
        job_id = args.get("job_id")
        return compare_job_efficiency(job_id)

    def _execute_query_cluster_incidents(self, args: Dict[str, Any]) -> Dict[str, Any]:
        hours_back = args.get("hours_back", 24)
        return query_cluster_incidents(hours_back)

    def _execute_ingest_historical_sample(self, args: Dict[str, Any]) -> Dict[str, Any]:
        csv_file_path = args.get("csv_file_path")
        return ingest_historical_sample(csv_file_path)

    def _execute_analyze_oomkilled_pods(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return analyze_oomkilled_pods(args.get("namespace", "default"))

    def _execute_drain_k8s_node(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return drain_k8s_node(args.get("node_name", ""))

    def _execute_query_ray_workers(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return query_ray_workers()

    def _execute_list_skills(
            self,
            args: Dict[str, Any],
    ) -> Dict[str, Any]:
        return list_skills()

    def _execute_load_skill(
            self,
            args: Dict[str, Any],
    ) -> Dict[str, Any]:
        name = args.get("name")

        if not name:
            raise ValueError("name is required")

        return load_skill(name)

    def _execute_query_prometheus(
            self,
            args: Dict[str, Any],
    ) -> Dict[str, Any]:
        query = args.get("query")
        if not query:
            raise ValueError("query is required")
        return query_prometheus(
            query=query,
            time_range=args.get("time_range", "1h"),
            step=args.get("step", "1m")
        )

    def _execute_query_pod_resources(
            self,
            args: Dict[str, Any],
    ) -> Dict[str, Any]:
        namespace = args.get("namespace")
        pod_name = args.get("pod_name")
        if not namespace or not pod_name:
            raise ValueError("namespace and pod_name are required")
        return query_pod_resources(
            namespace=namespace,
            pod_name=pod_name,
            window=args.get("window", "1h")
        )



def parse_tool_calls_from_text(text: str) -> list[Dict[str, Any]]:
    """
    Parse tool calls from LLM response text.

    This is a fallback for models that don't support structured function calling.
    It looks for JSON blocks or special markers in the text.

    Parameters
    ----------
    text:
        The LLM response text to parse.

    Returns
    -------
    List of tool call dictionaries with 'name' and 'arguments' keys.
    """
    tool_calls = []

    # Look for JSON code blocks that might contain tool calls
    lines = text.split("\n")
    in_json_block = False
    json_lines = []

    for line in lines:
        if line.strip() == "```json" or line.strip() == "```":
            if in_json_block:
                # End of block, try to parse
                if json_lines:
                    try:
                        data = json.loads("\n".join(json_lines))
                        if isinstance(data, dict) and "tool" in data:
                            tool_calls.append({
                                "name": data["tool"],
                                "arguments": data.get("arguments", {}),
                            })
                        json_lines = []
                    except json.JSONDecodeError:
                        pass
                in_json_block = False
            else:
                in_json_block = True
                json_lines = []
        elif in_json_block:
            json_lines.append(line)

    return tool_calls


# Global tool executor instance
_executor = ToolExecutor()


def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a tool synchronously using the global executor."""
    return _executor.execute(tool_name, arguments)


async def execute_tool_async(
    tool_name: str, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    """Execute a tool asynchronously using the global executor."""
    return await _executor.execute_async(tool_name, arguments)


async def execute_tools_batch(
    calls: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Execute multiple tools concurrently using the global executor."""
    return await _executor.execute_batch(calls)
