"""Background sensor service: Modbus RTU acquisition + threshold evaluation.

This module is intended to run *without* any UI. It periodically reads
vibration sensor data over Modbus RTU, evaluates fault levels using the
threshold engine, and writes the latest fault state to a simple JSON file
that other processes (e.g. alarm_service or a UI) can consume.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Tuple

from core.modbus.rtu_client import ModbusRtuClient, RtuConfig
from core.sensor.threshold_engine import SimpleThresholdConfig, SpeedThresholdEngine
from core.sensor.vibration_model import raw_to_speed


# Where to store the latest fault levels for cross-process sharing.
DEFAULT_STATE_PATH = Path("/tmp/sensor_fault_state.json")


@dataclass
class LocationFaultState:
    value: float
    level: int


@dataclass
class SensorFaultState:
    timestamp: float
    crank_left: LocationFaultState
    crank_right: LocationFaultState
    tail_bearing: LocationFaultState
    mid_bearing: LocationFaultState


class SensorService:
    """Periodic Modbus reader + threshold evaluator."""

    def __init__(
        self,
        port: str,
        unit_ids: Dict[str, int],
        state_path: Path = DEFAULT_STATE_PATH,
        interval: float = 1.0,
    ) -> None:
        self._client = ModbusRtuClient(RtuConfig(port=port))

        # Create independent threshold engines for each location
        # 调整阈值：之前是 1000/2000/3000 太大了，单位是 mm/s
        # 假设正常运行 < 5mm/s，故障可能在 10~20mm/s
        cfg = SimpleThresholdConfig(level1=5.0, level2=10.0, level3=20.0)
        self._engines = {
            "crank_left": SpeedThresholdEngine(cfg),
            "crank_right": SpeedThresholdEngine(cfg),
            "tail_bearing": SpeedThresholdEngine(cfg),
            "mid_bearing": SpeedThresholdEngine(cfg),
        }

        self._unit_ids = unit_ids
        self._state_path = state_path
        self._interval = interval
        self._stop = threading.Event()

    def _read_speed_xyz(self, unit_id: int, start_address: int) -> Tuple[float, float, float]:
        """Read 3-axis speed values (mm/s) from a given unit starting at address."""
        self._client.unit_id = unit_id
        # Read 3 registers: X, Y, Z
        regs = self._client.read_holding_registers(start_address, 3)
        vx = raw_to_speed(regs[0])
        vy = raw_to_speed(regs[1])
        vz = raw_to_speed(regs[2])
        return vx, vy, vz

    def _safe_read_xyz(self, unit_id: int, start_address: int) -> Tuple[float, float, float]:
        """Wrapper around _read_speed_xyz that catches errors and returns 0s."""
        try:
            return self._read_speed_xyz(unit_id, start_address)
        except Exception:
            # Log could be added here, but might be too noisy if sensor is permanently offline
            return 0.0, 0.0, 0.0

    def _acquire_once(self) -> SensorFaultState:
        """Read all configured locations once and compute fault levels."""

        # For now we assume one unit per mechanical location. The mapping from
        # logical location name to (unit_id, register_address) is passed in
        # via unit_ids configuration.
        # You can extend this to support multiple channels per location.
        mapping: Dict[str, int] = {
            "crank_left": self._unit_ids.get("crank_left", 1),
            "crank_right": self._unit_ids.get("crank_right", 2),
            "tail_bearing": self._unit_ids.get("tail_bearing", 3),
            "mid_bearing": self._unit_ids.get("mid_bearing", 4),
        }

        # User requested to read addresses 1, 2, 3 for X, Y, Z
        reg_addr = 1

        # Read and evaluate Crank Left
        cl_vx, cl_vy, cl_vz = self._safe_read_xyz(mapping["crank_left"], reg_addr)
        cl_lvl = self._engines["crank_left"].evaluate_xyz(cl_vx, cl_vy, cl_vz)
        cl_max = max(cl_vx, cl_vy, cl_vz)

        # Read and evaluate Crank Right
        cr_vx, cr_vy, cr_vz = self._safe_read_xyz(mapping["crank_right"], reg_addr)
        cr_lvl = self._engines["crank_right"].evaluate_xyz(cr_vx, cr_vy, cr_vz)
        cr_max = max(cr_vx, cr_vy, cr_vz)

        # Read and evaluate Tail Bearing
        tb_vx, tb_vy, tb_vz = self._safe_read_xyz(mapping["tail_bearing"], reg_addr)
        tb_lvl = self._engines["tail_bearing"].evaluate_xyz(tb_vx, tb_vy, tb_vz)
        tb_max = max(tb_vx, tb_vy, tb_vz)

        # Read and evaluate Mid Bearing
        mb_vx, mb_vy, mb_vz = self._safe_read_xyz(mapping["mid_bearing"], reg_addr)
        mb_lvl = self._engines["mid_bearing"].evaluate_xyz(mb_vx, mb_vy, mb_vz)
        mb_max = max(mb_vx, mb_vy, mb_vz)

        ts = time.time()
        return SensorFaultState(
            timestamp=ts,
            crank_left=LocationFaultState(cl_max, cl_lvl),
            crank_right=LocationFaultState(cr_max, cr_lvl),
            tail_bearing=LocationFaultState(tb_max, tb_lvl),
            mid_bearing=LocationFaultState(mb_max, mb_lvl),
        )

    def _write_state(self, state: SensorFaultState) -> None:
        data = asdict(state)
        tmp_path = self._state_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        tmp_path.replace(self._state_path)

    def run_forever(self) -> None:
        """Blocking main loop."""
        print(f"[sensor_service] Starting acquisition loop on {self._client._config.port} (interval={self._interval}s)...", file=sys.stderr)

        while not self._stop.is_set():
            try:
                state = self._acquire_once()
                self._write_state(state)
                # 打印心跳日志，方便调试
                # 找出当前所有传感器中的最大值，方便观察
                max_val = max(
                    state.crank_left.value,
                    state.crank_right.value,
                    state.tail_bearing.value,
                    state.mid_bearing.value
                )
                print(f"[sensor_service] Data updated. Max Vib: {max_val:.2f} mm/s", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"[sensor_service] Error: {exc}", file=sys.stderr)
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()


def _install_signal_handlers(service: SensorService) -> None:
    def handler(signum, frame) -> None:  # type: ignore[override]
        print(f"[sensor_service] Received signal {signum}, stopping...")
        service.stop()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main() -> None:
    # Basic CLI entrypoint. In a real deployment you may want to read these
    # from a config file or environment variables.
    unit_ids = {
        "crank_left": 1,
        "crank_right": 2,
        "tail_bearing": 3,
        "mid_bearing": 4,
    }
    # 用户反馈使用板载串口。
    # 工业网关常见配置：
    # - /dev/ttyS0: 通常是 RS232 调试口
    # - /dev/ttyS1: 通常是 RS485 接口 1
    # - /dev/ttyS2: 通常是 RS485 接口 2
    # 诊断结果确认使用 /dev/ttyS2
    service = SensorService(port="/dev/ttyS2", unit_ids=unit_ids)
    _install_signal_handlers(service)
    service.run_forever()


if __name__ == "__main__":
    main()
