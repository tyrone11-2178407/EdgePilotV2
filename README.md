# EdgePilot - AI Copilot Console

EdgePilot is an **on-premises AI copilot** that combines a lightweight FastAPI backend with an Electron desktop UI. It features **full MCP (Model Context Protocol) integration**, enabling Gemini and other providers to autonomously monitor your system, evaluate Kubernetes cluster capacity, launch applications with scheduling, and manage processes through natural language.

## Highlights
- **MCP Integration** - Gemini can autonomously call tools for system monitoring, app launching, and process management
- **Secure Tool Execution** - Executes shell commands, Python scripts, and Kubernetes actions with a built-in Human-in-the-Loop approval system to prevent unintended side effects
- **Kubernetes Capacity Evaluation & AIOps** - Query K8s clusters to check node headroom, and actively scale/restart workloads with explicit user consent
- **Real-time Metrics** - CPU, memory, disk (MB/s), and network (MB/s) telemetry streamed directly to the UI
- **Real-Time Token Streaming** - Ultra-low latency chat responses powered by Server-Sent Events (SSE)
- **Semantic Query Cache** - Skips redundant LLM API calls for near-duplicate questions using local embeddings
- **Async Parallel Tool Execution** - Multiple tool calls run concurrently via `asyncio`, cutting multi-tool latency
- **Smart App Launcher** - Launch applications by name with delay support (cross-platform)
- **Desktop UI** - Electron-based chat interface with dark theme and live telemetry dashboards
- **High-Performance Backend** - Utilizes in-memory caching for chats and loggers to eliminate blocking disk I/O reads
- **HPC Cluster Optimization** - Native Slurm integrations to monitor job limits, track node failures, cancel stalled jobs, and analyze actual hardware usage via `sacct`, `sstat`, `squeue`, and `sinfo`
- **Local PC Management** - Analyzes network hogs, dry-runs temporary file cleanups with a preview/execute two-step flow, and suspends heavy background processes to save battery
- **Agentic Offline Simulations** - Ingests historical CSV job data to run resource optimization simulations offline without needing a live cluster connection

## Architecture

EdgePilot uses a modular architecture with clear separation between the UI, backend API, and system tools:

![EdgePilot Architecture](assets/architecture.png)

**Key Components:**
- **Electron UI** - Desktop interface built with HTML/CSS/JS, communicates with backend via REST API
- **FastAPI Backend** - Handles chat management, provider abstraction, and tool execution
- **Provider Layer** - Pluggable LLM adapters (Gemini with tools, Claude, GPT)
- **MCP Integration** - Tool schemas and execution engine for system operations
- **System Tools** - Metrics gathering, app launcher, process management, usage monitoring
- **Scheduler** - Task queue for delayed execution and background jobs
- **Data Layer** - JSON-based persistence for chats, metrics, and tool history

## Demo

https://github.com/tahasinshadat/EdgePilot/blob/main/assets/EdgePilot_Demo_Compressed.mp4

## Installation

For the easiest installation experience, use our cross-platform installers:
- **Windows**: Download `EdgePilot-Installer-Windows-v1.0.1.exe` from [Releases](https://github.com/tahasinshadat/EdgePilot/releases)
- **macOS**: Download `EdgePilot-Installer-macOS-v1.0.1.app` from [Releases](https://github.com/tahasinshadat/EdgePilot/releases) (COMING SOON)

The installers will guide you through setup, configure your API keys, install dependencies, and create a desktop shortcut automatically.

**Full Installation Guide**: See [INSTALL.md](INSTALL.md) for detailed instructions and manual installation.

## Quick Start (Manual Installation)

### 1. Setup & Installation
```bash
# Install dependencies
pip install -r requirements.txt

# Configure your API key
# Edit env/.env and add: GEMINI_API_KEY=your_key_here

# Install Electron UI (one-time, requires Node.js 18+)
cd ui && npm install && cd ..
```

### 2. Run EdgePilot
```bash
# Launch the full application (API + Electron UI)
python main.py

# Or run API only:
python main.py serve --host 127.0.0.1 --port 8000
```

### 3. Test Tools
```bash
# Test all MCP tools integration
pytest test/test_tools.py
```

### 4. Example Prompts
Once running, you can interact with EdgePilot naturally. Here are some examples of what it can do:

**System Management:**
- *"List the files in my Documents folder."*
- *"Kill the notepad process."*
- *"Launch Calculator and wait 5 seconds before launching Chrome."*
- *"Are there any python scripts currently running?"*

**Kubernetes AIOps:**
EdgePilot integrates natively with your local `~/.kube/config`. Try:
- *"Scale my 'nginx' deployment in the default namespace down to 1 replica."*
- *"Perform a rolling restart of the 'frontend' deployment."*
- *"Cordon the 'worker-node-1' node so no new pods get scheduled there."*
- *"Check if any pods are crashing in the kube-system namespace."*
*(Note: All destructive actions like scaling or restarting require explicit Allow/Deny approval via the UI)*

**Local PC Optimization:**
- *"Can you safely preview what junk files can be deleted from my computer to free up space?"*
- *"Execute the disk cleanup and wipe those files."* (Triggers HITL approval pop-up)
- *"Hibernate Docker Desktop and Slack so my battery doesn't die."* (Triggers HITL approval pop-up)
- *"Are there any background apps secretly hogging my network?"*

**HPC / Slurm Cluster Management:**
- *"Give me a snapshot of the current slurm queue."*
- *"Check the jobstats for job ID 3841920. Is it wasting GPUs?"*
- *"Cancel Slurm job 3841920 because it is stalled."* (Triggers HITL approval pop-up)
- *"Demote job 3841920 to a lower QoS."* (Triggers HITL approval pop-up)

**Agentic Simulations:**
- *"Load the scripts/mock_jobs.csv file and tell me if any of the historical jobs ran out of memory."*
- *"Compare the requested limits against the actual usage for job 1001 and tell me its waste percentage."*
- *"Scrape the cluster logs for the last 24 hours and tell me if any nodes failed or jobs were preempted."*

## Usage Alerts


EdgePilot includes a powerful usage monitoring system that sends desktop notifications and email alerts when your system resources exceed defined thresholds.

### Features
- **Desktop Notifications** - Cross-platform notifications (Windows, macOS, Linux) with custom icon
- **Email Alerts** - Optional email notifications with configurable SMTP settings
- **Independent Operation** - Runs as a standalone background process
- **Configurable Thresholds** - Customize CPU, memory, and disk usage thresholds
- **Automatic Startup** - Can be configured to start automatically on system boot

### Quick Setup

1. **Enable Usage Alerts in Settings:**
   - Run EdgePilot: `python main.py`
   - Open Settings tab
   - Toggle "Enable Usage Alerts" ON
   - **Auto-start is automatically enabled** - monitor will start on boot
   - Adjust thresholds (default: CPU 85%, Memory 85%, Disk 90%)
   - Set check interval (default: 30 seconds)

2. **(Optional) Configure Email Alerts:**
   - Toggle "Enable Email Alerts" ON
   - Enter your email address (where you want to receive alerts)
   - Leave Gmail credentials empty to use EdgePilot's default sender
   - Or fill in your own Gmail credentials to send from your account

3. **(Optional) Disable Auto-Start:**
   - Toggle "Start on Boot" OFF in Settings
   - Or use command line: `python scripts/manage_startup.py uninstall`

### How Auto-Start Works

**Windows:**
- Installs a VBScript in your Startup folder
- Checks if usage alerts are enabled in settings
- Automatically starts the monitor on login
- Location: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\EdgePilot_Monitor.vbs`

**macOS:**
- Installs a Launch Agent (`.plist` file)
- Uses `launchd` to run monitor at login
- Automatically manages start/stop based on settings
- Location: `~/Library/LaunchAgents/com.edgepilot.monitor.plist`

**Linux:**
- Desktop notifications work via `libnotify` (notify-send)
- Auto-start requires manual configuration (systemd user service)
- See: `scripts/manage_startup.py` for reference implementation

### Email Configuration

**Using Your Own Email Account (Recommended):**
```
Your Email Address: john@example.com  Required
SMTP Server: smtp.gmail.com
SMTP Port: 587
SMTP Username: john@example.com
SMTP Password: your-app-password
TLS: Enabled
```
For Gmail, you **must** use an [App Password](https://support.google.com/accounts/answer/185833), not your regular password.

**Using EdgePilot's Default Sender (Simplest):**
```
Your Email Address: john@example.com  Required
SMTP Server: (leave empty)
SMTP Username: (leave empty)
SMTP Password: (leave empty)
```
Emails will be sent from EdgePilot's account to your email.

### Manual Control

You can also control the monitor manually:
```bash
# Start monitor manually
python tools/usage_monitor.py start

# Stop monitor
python tools/usage_monitor.py stop

# Check status
python tools/usage_monitor.py status
```

### Notifications

**Desktop Notifications:**
- Appear in the bottom-right (Windows) or top-right (macOS)
- Show EdgePilot logo
- Stay on screen for 30 seconds
- Include current usage percentage and threshold

**Email Alerts:**
- Same content as desktop notifications
- Sent only if email alerts are enabled
- 5-minute cooldown between same alert types (prevents spam)

## Command-Line Interface (CLI)
The CLI lets you ask questions, schedule jobs, and inspect the queue without opening the UI.

```bash
# Start an interactive REPL (works on macOS, Linux, and Windows)
python -m edgepilot_cli start

# One-shot questions
python -m edgepilot_cli ask "Can I run a heavy build right now?"
python -m edgepilot_cli ask "List my last few jobs" --format json

# Queue new work
python -m edgepilot_cli schedule --action run_shell_commands --command "echo hello" --delay 5
python -m edgepilot_cli schedule --action launch --command "Calculator"

# Inspect the scheduler
python -m edgepilot_cli status --limit 10
```

Additional commands:
- `edgepilot_cli activate` – alias for the interactive REPL
- `edgepilot_cli ask --context-file context.json` – include JSON context or change the system prompt

EdgePilot automatically detects your OS and picks the right application launcher or shell runner.

## Local REST API
Run the backend (`python -m main serve --host 127.0.0.1`) and interact with the new endpoints:

- `POST /api/ask` — submit natural-language questions via JSON payloads.  
  Example:
  ```bash
  curl -X POST http://127.0.0.1:8000/api/ask \
    -H "Content-Type: application/json" \
    -d '{"query": "Is it safe to start a GPU job?", "response_format": "json"}'
  ```
- `POST /api/schedule` — queue new shell/python/launch tasks.
  ```bash
  curl -X POST http://127.0.0.1:8000/api/schedule \
    -H "Content-Type: application/json" \
    -d '{"action":"run_shell_commands","command":"echo from API","delay_seconds":0}'
  ```
- `GET /api/tasks` — list recent scheduled tasks or filter by action.

The API binds to localhost by default, rejects malformed payloads with HTTP 400, and never writes request bodies to logs.

### 4. Try It Out!
Open the UI and try these prompts with **Gemini**:

**System Monitoring:**
- "What's my current CPU and memory usage?"
- "Show me the top 5 processes using the most CPU"

**Application Discovery:**
- "What apps do I have installed?"
- "Do I have Discord installed?"
- "List all my games"

**Application Launching:**
- "Launch notepad"
- "Open Chrome in 30 seconds"
- "Start Minecraft in 1 minute"

**Process Control:**
- "Close all Chrome instances"
- "End the notepad process"

### Optional: Enable Prometheus Metrics
EdgePilot can pull historical metrics from Prometheus when `PROM_URL` is set. A helper script downloads Prometheus, prepares a default config, installs node_exporter, and prints the commands to launch both services:

```bash
chmod +x scripts/bootstrap_prometheus.sh
./scripts/bootstrap_prometheus.sh   # follow the start instructions it prints
```

After the script runs it updates `env/.env` with sensible defaults (`PROM_URL=http://localhost:9090`, `PROM_TIMEOUT_SEC=15`) and adds a `node` scrape job. You can launch/stop the metrics stack in the background at any time:
```bash
./scripts/bootstrap_prometheus.sh start   # starts Prometheus + node_exporter (logs in ~/.edgepilot/logs)
./scripts/bootstrap_prometheus.sh status  # check running pids
./scripts/bootstrap_prometheus.sh stop    # stop both services
```

Finally, restart the EdgePilot backend so it picks up the new environment variables. Queries like `report_edge_status(window="6h")` will then read from Prometheus instead of the local fallback.

## Environment Configuration
Edit `env/.env`:
```bash
GEMINI_API_KEY=your_gemini_key        # Required for MCP tools
ANTHROPIC_API_KEY=your_claude_key     # Optional
OPENAI_API_KEY=your_openai_key        # Optional
DEFAULT_PROVIDER=gemini               # Use gemini for tool calling
```

## Project Layout
```
EdgePilot/
├── README.md
├── requirements.txt
├── main.py                  # FastAPI backend + CLI entry point
├── core/                    # Shared logic
│   ├── interface.py         # ask_question / schedule_operation helpers
│   ├── settings.py          # Environment config + provider setup
│   └── semantic_cache.py    # Embedding-based LLM response cache
├── providers/               # LLM provider adapters
│   ├── base.py              # BaseLLM protocol + ToolCall classes
│   ├── gemini.py            # Gemini with function calling
│   ├── claude.py            # Claude adapter
│   └── gpt.py               # GPT placeholder
├── tools/                   # System utilities exposed as tools
│   ├── __init__.py          # Export metrics, scheduler, process helpers
│   ├── metrics.py           # psutil + Prometheus-backed host reporting (TTL-cached)
│   ├── providers.py         # Kubernetes + local metrics provider abstraction
│   ├── scheduler.py         # Task registry + app launcher + shell/python runner
│   └── end_task.py          # Process termination
├── core/                     # Model Context Protocol integration
│   ├── tool_schemas.py
│   └── workflows.py         # Autonomous workflows
│   ├── tool_executor.py
├── ui/                      # Electron desktop application
│   ├── index.html           # UI markup
│   ├── renderer.js          # Frontend logic
│   ├── styles.css           # Dark theme styling
│   ├── main.js              # Electron main process
│   └── package.json         # Node.js dependencies
├── test/                    # Test suite
│   ├── test_cli_api.py      # CLI + API endpoint tests
│   ├── test_tools.py        # MCP tool smoke tests
│   ├── test_providers.py    # Kubernetes provider tests
│   └── test_optimization.py # TTL cache, async executor, semantic cache tests
├── env/.env                 # API keys and configuration
├── scripts/
│   └── bootstrap_prometheus.sh  # Installs Prometheus + node_exporter
└── data/                    # JSON persistence
    ├── chat_history.json
    ├── usage_metrics.json
    └── tool_call_history.json
```

## API Overview
- `GET /api/providers` – enumerate providers and configuration status
- `GET /api/chats` – list chat sessions with summary metadata
- `POST /api/chats` – create a new chat session
- `GET /api/chats/{chat_id}` – fetch full conversation history
- `POST /api/chats/{chat_id}/messages` – send a prompt and get LLM response (with tool calling)
- `POST /api/chats/{chat_id}/messages/stream` – SSE streaming variant with real-time tool progress
- `GET /api/metrics` – retrieve current system metrics snapshot
- `POST /api/ask` – answer natural-language questions with shared assistant logic
- `POST /api/schedule` – queue shell/python/launch tasks
- `GET /api/tasks` – show the scheduler queue/status
- `GET /api/cache/stats` – semantic cache diagnostics
- `POST /api/cache/clear` – flush the semantic query cache

## MCP (Model Context Protocol)

EdgePilot includes full MCP integration with powerful tools using launcher.py for intelligent app launching:

### Available Tools

#### 1. **gather_metrics** - System Monitoring
Collects comprehensive system metrics including CPU, memory, disk, network, battery, and all running processes with executable paths.

```python
# LLM can call this automatically when user asks about system status
gather_metrics(top_n=10, all_processes=False)
```

#### 2. **launch** - Application Launcher with Scheduling
Launch applications by name with optional delay. Uses Windows Start Menu search and Microsoft Store app discovery.

```python
# LLM calls this when user wants to launch an app
launch(app_name="chrome", delay_seconds=0)
launch(app_name="minecraft", delay_seconds=30)  # Launch in 30 seconds
```

**Features:**
- Searches Windows Start Menu shortcuts
- Finds Microsoft Store/UWP apps
- Supports delayed execution with threading
- Simple app names (no paths needed)

#### 3. **search** - Application Discovery
Search for installed applications by name. Returns list of matching apps found in Start Menu and Microsoft Store.

```python
# LLM calls this to check if an app is installed
search(app_name="discord")  # Returns: ["Discord"]
search(app_name="game")     # Returns: ["Game Bar", "Steam", ...]
```

#### 4. **list_apps** - Browse Installed Applications
List all installed applications with optional filtering. Perfect for "what apps do I have?" queries.

```python
# LLM calls this to browse available apps
list_apps(filter_term="")       # Returns all apps
list_apps(filter_term="game")   # Returns only apps with "game" in name
```

#### 5. **end_task** - Process Termination
Terminates processes by name, path, or command line identifier.

```python
# LLM calls this when user wants to close an app
end_task(identifier="chrome", force=False)
end_task(identifier="notepad", force=True)
```

### How It Works
1. User sends a message in natural language
2. Gemini analyzes the request and decides which tools to call
3. Tools are executed automatically (e.g., launching apps, gathering metrics)
4. Results are fed back to Gemini
5. Gemini formulates a human-readable response

**Example 1: System Monitoring**
```
User: "Show me what's using the most CPU"
→ Gemini calls gather_metrics(top_n=3)
→ Receives: {processes: [{name: "chrome.exe", cpu: 15.2%, ...}]}
→ Responds: "Chrome is using the most CPU at 15.2%..."
```

**Example 2: Scheduled App Launch**
```
User: "Launch Minecraft in 30 seconds"
→ Gemini calls launch(app_name="minecraft", delay_seconds=30)
→ Receives: {success: true, message: "Scheduled 'minecraft' to launch in 30 seconds"}
→ Responds: "I've scheduled Minecraft to launch in 30 seconds!"
```

**Example 3: App Discovery**
```
User: "What games do I have?"
→ Gemini calls list_apps(filter_term="game")
→ Receives: {count: 3, apps: ["Game Bar", "Steam", "Minecraft"]}
→ Responds: "You have 3 games installed: Game Bar, Steam, and Minecraft"
```

### Adding Your Own Tools
See `docs/scenario_docs.md` for the complete guide. It's a simple 5-step process:
1. Create tool function in `tools/`
2. Export it in `tools/__init__.py`
3. Add schema to `core/tool_schemas.py`
4. Add executor in `core/tool_executor.py`
5. Restart and test!

## Testing & Utilities
```bash
# Test all MCP tools integration
python test_tools.py

# Test launcher directly (launches notepad, chrome, minecraft)
python tools/launcher.py

# Run modules directly
python -c "from tools import gather_metrics; print(gather_metrics(top_n=5))"
python -c "from tools import search; print(search('chrome'))"
python -c "from tools import list_apps; print(list_apps('game'))"
```

## Extending Providers
1. Add a module under `providers/` implementing the `BaseLLM` protocol
2. Register it in `providers/__init__.py`
3. Add environment variables for API keys/models
4. For tool support, implement `enable_tools()` and parse `tool_calls` in responses

## Key Features Powered by launcher.py

EdgePilot's application launching is powered by `launcher.py`, which provides:

1. **Windows Start Menu Search** - Searches .lnk shortcuts in user and system Start Menu locations
2. **Microsoft Store Apps** - Discovers and launches UWP/Store apps via PowerShell
3. **Delayed Execution** - Background threading for scheduled launches
4. **Intelligent Fallback** - Falls back to Windows `start` command for built-in apps
5. **Simple API** - Just 3 core functions: `launch()`, `search()`, `list_apps()`

The LLM can use simple app names like "chrome", "minecraft", or "notepad" without needing full paths!

## Kubernetes Capacity Evaluation

EdgePilot can connect to a Kubernetes cluster and evaluate node-level capacity. The `tools/providers.py` module provides a `KubernetesMetricsProvider` that queries the K8s API for:

- **Node headroom** — available CPU cores, free memory, and open pod slots per worker node
- **Taints & tolerations** — checks whether a workload's tolerations match a node's scheduling constraints
- **Node health** — verifies `Ready` status and schedulability before recommending placement

The `evaluate_capacity` tool uses this provider to answer questions like *"Can my cluster handle a new 4-core workload?"* directly through natural language.

```python
# Example: evaluate whether a workload fits on the cluster
evaluate_capacity({"cpu_pct": 40, "mem_bytes": 2147483648})
```

## Kubernetes AIOps & Human-in-the-Loop (HITL)

Beyond read-only capacity checks, EdgePilot can actively manage your cluster state using built-in AIOps tooling:
- **`scale_workload`** — scale a deployment up or down.
- **`restart_workload`** — perform a rolling restart of a deployment (useful if metrics indicate a stuck process).
- **`cordon_node`** — mark a specific Kubernetes node as unschedulable.

**Human-in-the-Loop Safety**: Because these tools mutate cluster state, they are marked as **dangerous**. When the LLM decides to use them, execution pauses and emits an `approval_required` event to the Electron UI. The user is presented with a prompt to explicitly **Allow** or **Deny** the action before the backend proceeds.

## Performance

EdgePilot includes several performance optimizations to minimize latency:

- **TTL-Cached Metrics** — `gather_metrics()` results are cached for 5 seconds, preventing redundant psutil process scans during rapid LLM tool-calling loops.
- **In-Memory Chat & Telemetry Caching** — Chat histories and tool execution logs are persisted entirely in memory during active sessions to eliminate $O(N)$ synchronous disk I/O bottlenecks.
- **In-Place DOM Mutation** — Real-time telemetry dashboards mutate text content directly rather than redrawing the DOM, preventing CPU spikes and layout thrashing during 1-second metric polling.
- **Async Parallel Tool Execution** — when the LLM emits multiple tool calls in a single turn, they run concurrently via `asyncio.gather()` instead of sequentially.
- **Semantic Query Cache** — near-duplicate user queries are detected via sentence-transformer embeddings and served from an in-memory cache, bypassing the cloud LLM API entirely.
- **SSE Streaming** — the `/api/chats/{chat_id}/messages/stream` endpoint uses Server-Sent Events to push real-time status updates (tool execution, cache hits) to the UI.

## Documentation
- **`README.md`** (this file) - Quick start and overview
- **`docs/scenario_docs.md`** - Testing scenarios and guides

## Autonomous AI Workflows

EdgePilotV2 now supports robust, multi-step autonomous AI workflows designed to execute complex, procedural tasks such as infrastructure health checks and security audits. 

Workflows are defined cleanly in YAML files inside the `skills/workflows/` directory. Each workflow consists of discrete sequential steps, where the LLM is explicitly provided with the contextual instructions and allowed tools for that specific step. 

### Built-in Workflows:
1. **Infrastructure Health Check** (`health_check.yaml`) - Evaluates node capacity, polls Prometheus for CPU/Memory spikes, and checks for failing pods.
2. **Memory Anomaly Remediation** (`memory_anomaly.yaml`) - Autonomously detects memory leaks via Prometheus, identifies the rogue pod, and asks for approval to restart it.
3. **Security Audit** (`security_audit.yaml`) - Cross-references Kubernetes pod declarations with actual listening network ports (via `ss -tuln`) to detect undocumented open ports, and flags discrepancies.

### Workflow Features:
- **State Accumulation:** The conversational context and tool results are preserved sequentially across steps.
- **Forced Summarization:** Tools are automatically disabled between steps so the LLM is forced to generate a clear, human-readable summary of the prior step's findings.
- **Human-in-the-Loop:** Workflows can mark specific steps with `requires_approval: true`, prompting you to explicitly approve or deny execution (like cordoning nodes) in the UI before proceeding.
