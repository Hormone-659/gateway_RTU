"""Vibration sensor data model and unit conversion utilities.

This module defines small data structures that represent vibration sensor
channels and helper functions to convert raw Modbus register values into
engineering units (e.g. mm/s).

It is intentionally free of any UI framework so it can be reused from both
Tkinter-based UIs and background services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class VibrationSample:
    """Single vibration sample for one axis at one location.

    Attributes
    ----------
    raw_value: int
        The raw value read from a Modbus register.
    value: float
        Converted value in engineering units (mm/s or mm/s², depending on
        the channel semantics).
    unit: str
        Text description of the unit (e.g. "mm/s").
    """

    raw_value: int
    value: float
    unit: str


@dataclass
class LocationAxesSample:
    """Vibration values for a 3-axis sensor at a given mechanical location."""

    vx: VibrationSample
    vy: VibrationSample
    vz: VibrationSample


def default_speed_scale() -> float:
    """Return the default scaling factor from raw register to mm/s.

    Many industrial vibration sensors encode speed as an integer that needs
    to be divided by 100 or 1000. This helper keeps the conversion in one
    place so future calibration only changes here.
    """

    return 0.1  # raw / 10 -> mm/s (User specified /10)


def raw_to_speed(raw: int, scale: Optional[float] = None) -> float:
    """Convert a raw Modbus register value to vibration speed (mm/s).

    The type annotation uses Optional[...] instead of the Python 3.10+
    ``float | None`` syntax to stay compatible with Python 3.8.
    """

    if scale is None:
        scale = default_speed_scale()
    return raw * scale


def build_location_axes_sample(raw_xyz: Dict[str, int], unit: str = "mm/s") -> LocationAxesSample:
    """Helper to build a 3-axis sample from raw register values.

    Parameters
    ----------
    raw_xyz:
        Mapping with keys "x", "y", "z" and integer raw values.
    unit:
        Engineering unit text for the converted value.
    """

    vx = VibrationSample(raw_xyz.get("x", 0), raw_to_speed(raw_xyz.get("x", 0)), unit)
    vy = VibrationSample(raw_xyz.get("y", 0), raw_to_speed(raw_xyz.get("y", 0)), unit)
    vz = VibrationSample(raw_xyz.get("z", 0), raw_to_speed(raw_xyz.get("z", 0)), unit)
    return LocationAxesSample(vx=vx, vy=vy, vz=vz)


__all__ = [
    "VibrationSample",
    "LocationAxesSample",
    "raw_to_speed",
    "build_location_axes_sample",
]
