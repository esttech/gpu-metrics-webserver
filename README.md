# nvidia-smi-webserver

A GPU metrics server that exposes temperature, power, utilization, and memory data over HTTP as JSON. Supports NVIDIA, AMD, Intel, and Apple Silicon GPUs (including the Apple Neural Engine).

## Server

### Requirements

- Python 3.8+
- `uvicorn`, `fastapi`, `xmltodict`

### Running

```bash
python3 server.py
```

The server listens on `0.0.0.0:8000` and exposes a single endpoint:

- `GET /gpu` — returns an array of GPU metrics objects

### API Response

```json
[
  {
    "vendor": "nvidia",
    "index": 0,
    "name": "NVIDIA GeForce RTX 4090",
    "temperature_c": 72.0,
    "utilization_gpu_pct": 100.0,
    "memory_used_mb": 20480.0,
    "memory_total_mb": 24564.0,
    "power_draw_w": 350.0
  }
]
```

---

## macOS / Apple Silicon

On Apple Silicon (M1/M2/M3/M4 and variants) the server reports two entries with
`"vendor": "apple"`:

- **Integrated GPU** (`index: 0`) — name, GPU busy %, power draw, and unified
  memory usage.
- **Neural Engine / ANE** (`index: 1`) — power draw.

### Metric sources & caveats

- **GPU name and core count** come from `system_profiler` and work without root.
- **Live metrics** (GPU busy %, GPU power, ANE power) come from `powermetrics`,
  which **requires root**. Run the server with `sudo` to populate these fields;
  otherwise the device is still listed by name with dynamic fields set to `null`.
- **Unified memory**: Apple Silicon shares one memory pool between CPU, GPU, and
  ANE, so there is no dedicated VRAM. `memory_total_mb` is the total unified
  memory and `memory_used_mb` is the system-wide used memory (active + wired +
  compressed), since that is the pool the GPU draws from.
- **Temperature** is not exposed through a root-free API on Apple Silicon, so
  `temperature_c` is `null`.

```bash
# For full metrics on macOS, run as root:
sudo python3 server.py
```

---



Deploy the server as a systemd service on the GPU machine so it starts on boot and auto-restarts on failure.

### Setup

```bash
# Copy files to the GPU machine
sudo mkdir -p /opt/gpu-monitor
sudo cp server.py /opt/gpu-monitor/
sudo cp deploy/gpu-monitor.service /etc/systemd/system/

# Install Python dependencies
sudo pip3 install uvicorn fastapi xmltodict

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable gpu-monitor.service
sudo systemctl start gpu-monitor.service

# Check status
sudo systemctl status gpu-monitor.service
journalctl -u gpu-monitor.service -f
```

### Firewall

Ensure port 8000 is accessible from your development machine:

```bash
sudo ufw allow 8000/tcp
```

---

## VSCode Extension — GPU Thermal Throttle Monitor

A VSCode extension that monitors a remote GPU server and alerts you when thermal throttling is detected.

### How It Works

1. Polls the GPU server at a configurable interval
2. Tracks temperature, power draw, and GPU utilization
3. Uses a state machine to detect throttling:
   - **NORMAL** → **HOT**: Temperature hits threshold while power is high (GPU under full load at thermal limit)
   - **HOT** → **THROTTLING**: Power draw drops while temperature stays high and utilization is still 100% (GPU is clock-throttling to shed heat)
   - Recovery: Temperature drops below threshold minus 3°C hysteresis

### Notifications

- **Warning** (HOT): "GPU temperature has reached 87°C at 350W — near thermal limit"
- **Error** (THROTTLING): "GPU appears to be thermally throttling — power dropped from 350W to 280W while utilization remains at 100%. Performance is degraded."

Notifications have a configurable cooldown (default: 60s) to avoid spam.

### Status Bar

Shows real-time GPU stats in the VS Code status bar:
- 🟢 `✓ GPU: 65°C | 320W | 98%` — Normal
- 🟡 `⚠ GPU: 87°C | 350W | 100%` — Hot
- 🔴 `🔥 GPU: 89°C | 280W | 100%` — Throttling

### Installation

```bash
cd vscode-extension
npm install
npm run compile
```

Then press F5 in VS Code to launch the extension in a development host, or package it:

```bash
npx vsce package
theia --install-extension gpu-thermal-monitor-0.1.0.vsix
```

### Configuration

Open VS Code Settings and search for "GPU Monitor":

| Setting | Default | Description |
|---------|---------|-------------|
| `gpuMonitor.serverUrl` | `http://localhost:8000` | URL of the GPU monitoring server |
| `gpuMonitor.pollIntervalMs` | `2000` | Polling interval in ms |
| `gpuMonitor.temperatureThreshold` | `87` | Temperature (°C) to trigger alerts |
| `gpuMonitor.powerHighThreshold` | `300` | Power (W) considered "high" at full load |
| `gpuMonitor.powerDropPercent` | `15` | % power drop that indicates throttling |
| `gpuMonitor.gpuIndex` | `0` | Which GPU to monitor |
| `gpuMonitor.notificationCooldownMs` | `60000` | Minimum time between repeated alerts |

### Commands

- `GPU Monitor: Start Monitoring` — begin polling
- `GPU Monitor: Stop Monitoring` — stop polling
- `GPU Monitor: Show GPU Status` — display current state summary

---

## License

See [LICENSE](LICENSE).
