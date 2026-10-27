"""Shared schema for GPU metrics."""

from pydantic import BaseModel
from typing import Optional


class GPUMetrics(BaseModel):
    vendor: str          # "nvidia", "amd", "intel", or "apple"
    index: int
    name: str
    temperature_c: Optional[float] = None
    utilization_gpu_pct: Optional[float] = None
    memory_used_mb: Optional[float] = None
    memory_total_mb: Optional[float] = None
    power_draw_w: Optional[float] = None
