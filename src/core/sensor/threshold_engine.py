"""Threshold engine wrapper around existing threshold_analyzer logic.

This module provides a simple, UI-independent API that other parts of the
system (UIs, background services) can use to compute 0/1/2/3 fault levels
from vibration speed values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

# The existing implementation lives under gateway.sensor.threshold_analyzer.
# We import and adapt it instead of reimplementing the logic.
try:
    from gateway.sensor.threshold_analyzer import (  # type: ignore
        ThresholdConfig,
        MultiChannelThresholdAnalyzer,
    )
except Exception:  # pragma: no cover - allows import in environments without full package setup
    ThresholdConfig = None  # type: ignore
    MultiChannelThresholdAnalyzer = None  # type: ignore


@dataclass
class SimpleThresholdConfig:
    level1: float
    level2: float
    level3: float


class SpeedThresholdEngine:
    """Convenience wrapper for per-channel threshold evaluation.

    The engine is intentionally stateless per call so that services can call
    it with the latest value and immediately get a fault level 0/1/2/3.
    If the project later requires more advanced logic (e.g. hysteresis,
    time windows), that should be delegated to the underlying
    MultiChannelThresholdAnalyzer implementation.
    """

    def __init__(self, cfg: SimpleThresholdConfig) -> None:
        if ThresholdConfig is None or MultiChannelThresholdAnalyzer is None:
            raise RuntimeError(
                "gateway.sensor.threshold_analyzer is not available. "
                "Ensure it is importable in the deployment environment."
            )
        self._cfg = cfg
        # 使用较小的窗口 (window_size=10) 以便报警能更快解除
        # 默认是 50，导致报警消除需要 50 秒
        self._analyzer = MultiChannelThresholdAnalyzer(
            ThresholdConfig(
                level1=cfg.level1,
                level2=cfg.level2,
                level3=cfg.level3,
                window_size=10,      # 10个样本 (约10秒)
                min_spike_count=3    # 只要有3个点超标就触发
            ),
            channels=["x", "y", "z"]
        )

    def evaluate_single(self, value: float) -> int:
        """Return fault level (0~3) for a single vibration speed value (legacy support)."""
        # Treat single value as 'x' axis for backward compatibility or single-axis sensors
        res = self._analyzer.update({"x": value, "y": 0.0, "z": 0.0})
        return res.max_level

    def evaluate_xyz(self, vx: float, vy: float, vz: float) -> int:
        """Return max fault level (0~3) for 3-axis vibration speed values."""
        res = self._analyzer.update({"x": vx, "y": vy, "z": vz})
        return res.max_level

    def evaluate_multi(self, values: Dict[str, float]) -> Dict[str, int]:
        """Evaluate multiple named channels at once.

        Parameters
        ----------
        values:
            Mapping from channel name to vibration speed value.
        """

        levels = self._analyzer.update(values)  # type: ignore[call-arg]
        return {name: int(level) for name, level in levels.items()}  # type: ignore[union-attr]


__all__ = ["SimpleThresholdConfig", "SpeedThresholdEngine"]
