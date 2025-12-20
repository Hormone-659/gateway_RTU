"""Alarm engine wrapper around existing alarm_logic.

This module centralises how sensor fault levels are converted into overall
alarm levels and RTU register maps, without any dependency on Tkinter or
UI code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

try:
    from gateway.alarm.alarm_play.alarm_logic import (  # type: ignore
        SensorState,
        evaluate_alarms,
        build_rtu_registers,
    )
except Exception:  # pragma: no cover
    SensorState = None  # type: ignore
    evaluate_alarms = None  # type: ignore
    build_rtu_registers = None  # type: ignore


@dataclass
class FaultLevels:
    """Per-location fault levels coming from sensor analysis.

    Levels are integers 0~3 for each mechanical location.
    """

    crank_left: int = 0
    crank_right: int = 0
    tail_bearing: int = 0
    mid_bearing: int = 0


class AlarmEngine:
    """Bridge between fault levels and existing alarm_logic functions."""

    def __init__(self) -> None:
        if SensorState is None or evaluate_alarms is None or build_rtu_registers is None:
            raise RuntimeError(
                "gateway.alarm.alarm_play.alarm_logic is not available. "
                "Ensure it is importable in the deployment environment."
            )

    def evaluate(self, faults: FaultLevels) -> Tuple[int, Dict[int, int]]:
        """Compute overall alarm level and RTU register map.

        Returns
        -------
        alarm_level:
            Overall alarm level 0~3.
        rtu_registers:
            Mapping from RTU register address to value to be written.
        """

        # Map FaultLevels into SensorState fields. The exact mapping should
        # match what alarm_logic currently expects.
        state = SensorState(
            crank_left_level=faults.crank_left,
            crank_right_level=faults.crank_right,
            tail_bearing_level=faults.tail_bearing,
            mid_bearing_level=faults.mid_bearing,
            # ...existing code for other fields with safe defaults...
        )

        # evaluate_alarms returns a file map dict, not an int. We can ignore it here
        # as we are interested in the RTU registers.
        _ = evaluate_alarms(state)

        rtu_registers = build_rtu_registers(state)

        # Extract overall alarm level from register 3502 (as defined in alarm_logic)
        overall_level = rtu_registers.get(3502, 0)

        return int(overall_level), {int(k): int(v) for k, v in rtu_registers.items()}


__all__ = ["FaultLevels", "AlarmEngine"]
