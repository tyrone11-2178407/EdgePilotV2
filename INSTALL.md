# EdgePilot Installation Guide

There are two ways to install EdgePilot:

## Option 1: Automated Installer (Recommended for End Users)

Perfect for users who want a simple, guided installation process.

### Download Pre-built Installer

**Windows:**
1. Download `EdgePilot-Installer.exe` from [Releases](https://github.com/tahasinshadat/EdgePilot/releases)
2. Double-click to run
3. Follow the on-screen instructions

**macOS:**
1. Download `EdgePilot-Installer.app.zip` from [Releases](https://github.com/tahasinshadat/EdgePilot/releases)
2. Extract the zip file
3. Right-click on `EdgePilot Installer.app` and select "Open"
4. Follow the on-screen instructions

### What the Installer Does

The installer will:
- ✅ Download the latest version of EdgePilot
- ✅ Ask for your API keys (Gemini required, Claude optional)
- ✅ Install all Python and Node.js dependencies
- ✅ Configure your environment automatically
- ✅ Create a desktop shortcut/application
- ✅ Set up auto-start for usage alerts (optional)

### Prerequisites

Before running the installer, make sure you have:
- **Python 3.8+** - [Download](https://python.org/)
- **Node.js 18+** - [Download](https://nodejs.org/)
- (Optional) **Git** - [Download](https://git-scm.com/)

## Option 2: Manual Installation (For Developers)

Perfect for developers who want full control over the installation.

### 1. Clone Repository

```bash
git clone https://github.com/tahasinshadat/EdgePilot.git
cd EdgePilot
```

### 2. Install Dependencies

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Node.js dependencies
cd ui && npm install && cd ..
```

### 3. Configure Environment

Create `env/.env` file:

```bash
GEMINI_API_KEY=your_gemini_key_here
ANTHROPIC_API_KEY=your_claude_key_here  # Optional
DEFAULT_PROVIDER=gemini

# Optional: Prometheus metrics
PROM_URL=http://localhost:9090
PROM_TIMEOUT_SEC=15

# Default SMTP for usage alerts
DEFAULT_SMTP_SERVER=smtp.gmail.com
DEFAULT_SMTP_PORT=587
DEFAULT_SMTP_USERNAME=edgepilot.app@gmail.com
DEFAULT_SMTP_PASSWORD="kdrw lbey jhzm wfma"
DEFAULT_SMTP_USE_TLS=true
```

**Get API Keys:**
- **Gemini** (Required): https://aistudio.google.com/app/apikey
- **Claude** (Optional): https://console.anthropic.com/

### 4. Create Settings File

Create `data/settings.json`:

```json
{
  "usage_alerts_enabled": false,
  "alert_thresholds": {
    "cpu_percent": 85.0,
    "memory_percent": 85.0,
    "disk_percent": 90.0
  },
  "check_interval_seconds": 30,
  "email_alerts_enabled": false,
  "email_address": "",
  "smtp_username": "",
  "smtp_password": ""
}
```

### 5. Run EdgePilot

```bash
# Launch with UI
python main.py

# Or run API server only
python main.py serve --host 127.0.0.1 --port 8000
```

## Building the Installer (For Contributors)

If you want to build the installer yourself:

### Windows

```bash
pyinstaller --onefile --windowed --icon=assets/logo.ico --name=EdgePilot-Installer-Windows-v1.0.1 installer/install_windows.py
```

Output: `dist/EdgePilot-Installer-Windows-v1.0.1.exe`

### macOS

```bash
pyinstaller --onefile --windowed --icon=assets/logo.icns --name=EdgePilot-Installer-macOS-v1.0.1 installer/install_macos.py
```

Output: `dist/EdgePilot-Installer-macOS-v1.0.1.app`

See [installer/README.md](installer/README.md) for more details.

## Troubleshooting

### Common Issues

**"Module not found" errors:**
```bash
pip install -r requirements.txt
cd ui && npm install
```

**"Gemini API key not found":**
- Make sure `env/.env` exists with `GEMINI_API_KEY=...`

**Electron not launching:**
```bash
cd ui
npm install
```

**Port already in use:**
```bash
# Use a different port
python main.py serve --port 8080
```

### Platform-Specific Issues

**Windows: "python not recognized"**
- Install Python from https://python.org/
- Make sure "Add Python to PATH" was checked

**macOS: "node: command not found"**
- Install Node.js from https://nodejs.org/
- Or use Homebrew: `brew install node`

**macOS: Permission denied**
```bash
chmod +x scripts/*.sh
```

## Uninstalling

### If Installed via Installer

**Windows:**
1. Delete the EdgePilot folder (default: `C:\Users\YourName\EdgePilot`)
2. Delete desktop shortcut
3. (Optional) Remove from Startup folder: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\EdgePilot_Monitor.vbs`

**macOS:**
1. Delete the EdgePilot folder (default: `~/Applications/EdgePilot`)
2. Delete `~/Applications/EdgePilot.app`
3. (Optional) Remove launch agent: `~/Library/LaunchAgents/com.edgepilot.monitor.plist`

### If Installed Manually

Simply delete the cloned repository folder.

## Next Steps

After installation:

1. **Configure Usage Alerts** (Optional)
   - Open Settings tab in UI
   - Enable usage alerts
   - Set thresholds for CPU, memory, disk usage
   - Configure email notifications

2. **Run Tests**
   ```bash
   pytest
   ```

3. **Read the Documentation**
   - [README.md](README.md) - Full feature guide
   - [MCP/README.md](MCP/README.md) - MCP integration guide

4. **Start Using EdgePilot!**
   - Ask Gemini about system status
   - Launch applications with natural language
   - Monitor resource usage
   - Schedule tasks

## Support

If you encounter issues:
- Check [README.md](README.md) for feature documentation
- Report bugs at https://github.com/tahasinshadat/EdgePilot/issues
- Make sure all prerequisites are installed

## License

MIT License - See LICENSE file for details
