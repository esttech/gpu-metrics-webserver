"""Apple Silicon GPU + Neural Engine (ANE) parser for macOS.

Apple Silicon SoCs (M1/M2/M3/M4 and variants) expose an integrated GPU and a
dedicated Neural Engine (ANE). Neither has a vendor CLI like nvidia-smi, so this
plugin stitches together a few macOS-native sources:

  * ``system_profiler SPDisplaysDataType``  -> GPU name + core count (no root)
  * ``sysctl``                              -> SoC brand string, unified memory
  * ``powermetrics``                        -> live GPU/ANE power + GPU busy %
                                               (requires root)

Design notes
------------
* Apple Silicon uses *unified memory* shared between CPU, GPU and ANE, so there
  is no dedicated VRAM. We report the total unified memory as ``memory_total_mb``
  and the system-wide used memory (from ``vm_stat``) as ``memory_used_mb`` since
  that is the pool the GPU actually draws from.
* Live metrics come from ``powermetrics``, which needs root. When it is not
  available (not root, or not macOS) we still emit the GPU and Neural Engine
  entries with a name so the device is discoverable; dynamic fields stay ``None``.
* The Neural Engine is surfaced as a separate ``GPUMetrics`` entry (vendor
  ``"apple"``, name containing "Neural Engine"). It has no temperature or memory
  of its own, so only ``power_draw_w`` / ``utilization_gpu_pct`` are populated.
* Everything is best-effort and must never raise, matching the other vendor
  plugins (bare ``except`` -> empty/partial result).
"""

import platform
import re
import subprocess
from typing import List, Optional, Tuple

from .schema import GPUMetrics


def _is_apple_silicon() -> bool:
    """True only on macOS running on an arm64 (Apple Silicon) CPU."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _run(cmd: List[str], timeout: float = 2.0) -> Optional[str]:
    """Run a command, returning stripped stdout, or None on any failure."""
    try:
        output = subprocess.check_output(
            cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout
        )
        return output.strip()
    except Exception:
        return None


def _sysctl(key: str) -> Optional[str]:
    """Read a single sysctl value by key."""
    return _run(["sysctl", "-n", key])


def _soc_name() -> str:
    """Best-effort SoC/GPU name, e.g. 'Apple M3 Pro'."""
    brand = _sysctl("machdep.cpu.brand_string")
    if brand:
        return brand
    return "Apple Silicon"


def _gpu_name_and_cores() -> Tuple[str, Optional[int]]:
    """
    Parse ``system_profiler SPDisplaysDataType`` for the GPU chipset name and
    total GPU core count. Falls back to the SoC brand string.
    """
    soc = _soc_name()
    name = f"{soc} GPU"
    cores: Optional[int] = None

    out = _run(["system_profiler", "SPDisplaysDataType"], timeout=5.0)
    if out:
        chipset = None
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("Chipset Model:"):
                chipset = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Total Number of Cores:"):
                try:
                    cores = int(stripped.split(":", 1)[1].strip())
                except ValueError:
                    pass
        if chipset:
            name = chipset

    return name, cores


def _unified_memory_mb() -> Tuple[Optional[float], Optional[float]]:
    """
    Return (used_mb, total_mb) for the unified memory pool.

    Total comes from ``hw.memsize``. Used is derived from ``vm_stat`` page
    accounting (active + wired + compressed pages).
    """
    total_mb: Optional[float] = None
    total_bytes = _sysctl("hw.memsize")
    if total_bytes:
        try:
            total_mb = float(total_bytes) / (1024 * 1024)
        except ValueError:
            pass

    used_mb: Optional[float] = None
    vm = _run(["vm_stat"])
    if vm:
        page_size = 4096
        m = re.search(r"page size of (\d+) bytes", vm)
        if m:
            page_size = int(m.group(1))

        def _pages(label: str) -> int:
            match = re.search(rf"{re.escape(label)}:\s+(\d+)", vm)
            return int(match.group(1)) if match else 0

        used_pages = (
            _pages("Pages active")
            + _pages("Pages wired down")
            + _pages("Pages occupied by compressor")
        )
        if used_pages:
            used_mb = used_pages * page_size / (1024 * 1024)

    return used_mb, total_mb


def _query_powermetrics() -> dict:
    """
    Sample GPU and ANE power/activity via ``powermetrics`` (requires root).

    Returns a dict with any of: ``gpu_power_w``, ``gpu_busy_pct``,
    ``ane_power_w``. Returns an empty dict if powermetrics is unavailable
    (e.g. not running as root).
    """
    out = _run(
        [
            # Note: on current macOS, powermetrics requires -n/-i *before*
            # --samplers; placing them after causes an argument-parsing error.
            "powermetrics",
            "-n", "1",
            "-i", "200",
            "--samplers", "gpu_power,ane_power",
        ],
        timeout=4.0,
    )
    if not out:
        return {}

    result: dict = {}

    # GPU active residency, e.g. "GPU HW active residency:  42.11%"
    m = re.search(r"GPU HW active residency:\s+([\d.]+)%", out)
    if m:
        result["gpu_busy_pct"] = float(m.group(1))

    # Power lines are reported in milliwatts, e.g. "GPU Power: 1234 mW"
    m = re.search(r"GPU Power:\s+([\d.]+)\s*mW", out)
    if m:
        result["gpu_power_w"] = float(m.group(1)) / 1000.0

    # Apple Neural Engine power, e.g. "ANE Power: 56 mW"
    m = re.search(r"ANE Power:\s+([\d.]+)\s*mW", out)
    if m:
        result["ane_power_w"] = float(m.group(1)) / 1000.0

    return result


def get_gpus() -> List[GPUMetrics]:
    """
    Discover the Apple Silicon integrated GPU and Neural Engine.

    Emits two entries: index 0 for the GPU, index 1 for the Neural Engine.
    On non-Apple-Silicon platforms this returns an empty list so the plugin is
    inert everywhere else.
    """
    if not _is_apple_silicon():
        return []

    gpus: List[GPUMetrics] = []

    try:
        gpu_name, cores = _gpu_name_and_cores()
        mem_used, mem_total = _unified_memory_mb()
        metrics = _query_powermetrics()

        if cores:
            gpu_name = f"{gpu_name} ({cores}-core)"

        # Integrated GPU
        gpus.append(GPUMetrics(
            vendor="apple",
            index=0,
            name=gpu_name,
            temperature_c=None,  # No root-free SoC die temp source on Apple Silicon
            utilization_gpu_pct=metrics.get("gpu_busy_pct"),
            memory_used_mb=mem_used,
            memory_total_mb=mem_total,
            power_draw_w=metrics.get("gpu_power_w"),
        ))

        # Neural Engine (ANE) — only emit if we have, or could have, data for it.
        ane_power = metrics.get("ane_power_w")
        ane_name = f"{_soc_name()} Neural Engine"
        gpus.append(GPUMetrics(
            vendor="apple",
            index=1,
            name=ane_name,
            temperature_c=None,
            utilization_gpu_pct=None,  # ANE residency not exposed as a simple %
            memory_used_mb=None,       # Shares unified memory; not separately metered
            memory_total_mb=None,
            power_draw_w=ane_power,
        ))
    except Exception:
        pass

    return gpus
