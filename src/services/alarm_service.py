"""Background alarm service: read fault levels and write RTU registers.

This module periodically reads the JSON state file written by sensor_service,
converts fault levels into overall alarm levels and RTU register maps using
core.alarm.alarm_engine, and writes those registers to the RTU/PLC.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from core.alarm.alarm_engine import AlarmEngine, FaultLevels
from services.rtu_comm import RtuWriter

DEFAULT_STATE_PATH = Path("/tmp/sensor_fault_state.json")


@dataclass
class _LocationSnapshot:
    value: float
    level: int


@dataclass
class _StateSnapshot:
    crank_left: _LocationSnapshot
    crank_right: _LocationSnapshot
    tail_bearing: _LocationSnapshot
    mid_bearing: _LocationSnapshot
    belt: _LocationSnapshot
    line: _LocationSnapshot
    elec_a: bool
    elec_b: bool
    elec_c: bool


class AlarmService:
    def __init__(
        self,
        state_path: Path = DEFAULT_STATE_PATH,
        interval: float = 1.0,
    ) -> None:
        self._state_path = state_path
        self._interval = interval
        self._stop = threading.Event()
        self._engine = AlarmEngine()
        self._rtu = RtuWriter()

    def _load_state(self) -> Optional[_StateSnapshot]:
        try:
            with self._state_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            print(f"[alarm_service] Failed to read state: {exc}", file=sys.stderr)
            return None

        try:
            cl = data["crank_left"]
            cr = data["crank_right"]
            tb = data["tail_bearing"]
            mb = data["mid_bearing"]

            # Read photo sensors, default to 0 if missing (backward compatibility)
            belt_data = data.get("belt", {"value": 0.0, "level": 0})
            line_data = data.get("line", {"value": 0.0, "level": 0})

            # Read electrical status, default to True if missing (backward compatibility)
            ea = data.get("elec_a", True)
            eb = data.get("elec_b", True)
            ec = data.get("elec_c", True)
        except KeyError:
            return None

        return _StateSnapshot(
            crank_left=_LocationSnapshot(cl["value"], cl["level"]),
            crank_right=_LocationSnapshot(cr["value"], cr["level"]),
            tail_bearing=_LocationSnapshot(tb["value"], tb["level"]),
            mid_bearing=_LocationSnapshot(mb["value"], mb["level"]),
            belt=_LocationSnapshot(belt_data["value"], belt_data["level"]),
            line=_LocationSnapshot(line_data["value"], line_data["level"]),
            elec_a=ea,
            elec_b=eb,
            elec_c=ec,
        )

    def _process_once(self) -> None:
        snapshot = self._load_state()
        if not snapshot:
            return

        faults = FaultLevels(
            crank_left=snapshot.crank_left.level,
            crank_right=snapshot.crank_right.level,
            tail_bearing=snapshot.tail_bearing.level,
            mid_bearing=snapshot.mid_bearing.level,
            belt=snapshot.belt.level,
            line=snapshot.line.level,
            elec_a=snapshot.elec_a,
            elec_b=snapshot.elec_b,
            elec_c=snapshot.elec_c,
        )

        # Read current 101 value from RTU
        current_rtu_101 = None
        try:
            # Read 1 register starting at 101
            values = self._rtu.read_holding_registers(101, 1)
            if values:
                current_rtu_101 = values[0]
        except Exception as e:
            print(f"[alarm_service] Warning: Failed to read RTU 101: {e}", file=sys.stderr)

        alarm_level, rtu_registers = self._engine.evaluate(faults, current_rtu_101=current_rtu_101)

        # === 关键修改：注入 101 寄存器逻辑 ===
        # 如果是 3 级报警，强制将 101 寄存器设为 82
        # 这样 rtu_registers 字典里就会同时包含 3501~3520 和 101
        if alarm_level == 3:
            rtu_registers[101] = 82

        # Write to RTU and log internally.
        # 注意：请确保 services/rtu_comm.py 中的 RtuWriter.write_registers 方法
        # 内部调用了 client.write_registers_map(registers) 而不是旧的 write_multiple_registers
        self._rtu.write_registers(rtu_registers, alarm_level)

    def run_forever(self) -> None:
        print(f"[alarm_service] Starting alarm evaluation loop (interval={self._interval}s)...", file=sys.stderr)
        while not self._stop.is_set():
            try:
                self._process_once()
                # 打印心跳日志，确保用户能看到服务在运行
                # 注意：如果 _process_once 成功写入 RTU，rtu_comm 也会打印日志
                # 这里打印是为了覆盖读取失败或无数据的情况
                print(f"[alarm_service] Cycle completed at {time.time():.2f}. Next update in {self._interval}s.", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"[alarm_service] Error: {exc}", file=sys.stderr)
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()


def _install_signal_handlers(service: AlarmService) -> None:
    def handler(signum, frame) -> None:  # type: ignore[override]
        print(f"[alarm_service] Received signal {signum}, stopping...")
        service.stop()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main() -> None:
    # Update cycle to 1 second as requested
    service = AlarmService(interval=1.0)
    _install_signal_handlers(service)
    service.run_forever()


if __name__ == "__main__":
    main()
