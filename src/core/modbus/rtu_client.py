"""Core Modbus RTU client abstraction.

This module provides a small, UI-independent wrapper around pymodbus (or any
other Modbus backend) for RTU over serial. It only exposes the operations
currently needed by your project: read_holding_registers, write_single_register
and write_multiple_registers.

The implementation is intentionally simple and conservative so it can be used
both by UI code (vibration_monitor_*.py) and by background services
(sensor_service / alarm_service).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional, Dict, Union

try:
    # We try to import pymodbus, but we don't enforce it at import time.
    # If it's missing, the user will get a clear error when trying to connect.
    from pymodbus.client.sync import ModbusSerialClient  # type: ignore
except Exception:  # pragma: no cover - optional dependency at runtime
    ModbusSerialClient = None  # type: ignore


@dataclass
class RtuConfig:
    port: str
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    timeout: float = 1.0
    unit_id: int = 1


class ModbusRtuClient:
    """Thin wrapper for Modbus RTU operations used in this project.

    This class does **not** start any GUI and can safely be used from
    background services and UI alike.
    """

    def __init__(self, config: RtuConfig) -> None:
        if ModbusSerialClient is None:
            raise RuntimeError(
                "pymodbus is required for ModbusRtuClient but is not installed. "
                "Install it with 'pip install pymodbus'."
            )
        self._config = config
        self._client: Optional[ModbusSerialClient] = None

    def connect(self) -> None:
        """Open the serial connection if not already open."""
        if self._client is None:
            self._client = ModbusSerialClient(
                method="rtu",
                port=self._config.port,
                baudrate=self._config.baudrate,
                bytesize=self._config.bytesize,
                parity=self._config.parity,
                stopbits=self._config.stopbits,
                timeout=self._config.timeout,
            )
        if not self._client.connect():  # type: ignore[union-attr]
            raise IOError(f"Failed to open serial port {self._config.port}")

        # 尝试为板载串口开启 RS485 模式 (Linux only)
        # 必须在 connect() 之后设置，因为 connect() 会创建 socket (serial.Serial) 对象
        if sys.platform.startswith("linux") and "ttyS" in self._config.port:
            try:
                import serial
                import serial.rs485
                # pymodbus 2.x 中，self._client.socket 就是 serial.Serial 实例
                if hasattr(self._client, "socket") and isinstance(self._client.socket, serial.Serial):
                    rs485_conf = serial.rs485.RS485Settings()
                    self._client.socket.rs485_mode = rs485_conf
            except Exception:
                pass

    def close(self) -> None:
        """Close the underlying serial connection."""
        if self._client is not None:
            try:
                self._client.close()  # type: ignore[union-attr]
            finally:
                self._client = None

    @property
    def unit_id(self) -> int:
        return self._config.unit_id

    @unit_id.setter
    def unit_id(self, value: int) -> None:
        self._config.unit_id = value

    def read_holding_registers(self, address: int, count: int = 1) -> List[int]:
        """Read one or more holding registers."""
        if self._client is None:
            self.connect()

        result = self._client.read_holding_registers(  # type: ignore[union-attr]
            address=address,
            count=count,
            unit=self._config.unit_id,
        )
        if not hasattr(result, "registers") or result.registers is None:
            raise IOError("Modbus RTU read_holding_registers failed or returned no data")
        return list(result.registers)

    def read_input_registers(self, address: int, count: int = 1) -> List[int]:
        """Read one or more input registers (Function 0x04)."""
        if self._client is None:
            self.connect()

        result = self._client.read_input_registers(  # type: ignore[union-attr]
            address=address,
            count=count,
            unit=self._config.unit_id,
        )
        if not hasattr(result, "registers") or result.registers is None:
            raise IOError("Modbus RTU read_input_registers failed or returned no data")
        return list(result.registers)

    def write_single_register(self, address: int, value: int) -> None:
        """Write a single holding register using Modbus function 0x06."""
        if self._client is None:
            self.connect()

        result = self._client.write_register(  # type: ignore[union-attr]
            address=address,
            value=value,
            unit=self._config.unit_id,
        )
        if result.isError():  # type: ignore[union-attr]
            raise IOError(f"Modbus RTU write_single_register failed at {address}")

    def write_multiple_registers(self, address: int, values: List[int]) -> None:
        """Write multiple consecutive holding registers using Modbus 0x10."""
        if self._client is None:
            self.connect()

        result = self._client.write_registers(  # type: ignore[union-attr]
            address=address,
            values=values,
            unit=self._config.unit_id,
        )
        if result.isError():  # type: ignore[union-attr]
            raise IOError(
                f"Modbus RTU write_multiple_registers failed at {address} (len={len(values)})"
            )

    # ========= 统一入口：自动选择 0x06 或 0x10 =========
    def write_registers_auto(self, address: int, values: Union[List[int], int]) -> None:
        """Unified API: if values is int or len==1 -> 0x06, else -> 0x10."""
        if isinstance(values, int):
            self.write_single_register(address, values)
            return
        if len(values) == 1:
            self.write_single_register(address, values[0])
        elif len(values) > 1:
            self.write_multiple_registers(address, values)

    # ========= 按映射自动分段写（把分散地址合并为连续块）=========
    def write_registers_map(self, reg_map: Dict[int, int]) -> None:
        """Write scattered addresses by merging consecutive runs into 0x10 blocks."""
        if not reg_map:
            return
        items = sorted(reg_map.items())  # [(addr, val), ...] sorted by addr

        start: Optional[int] = None
        block: List[int] = []
        prev: Optional[int] = None

        for addr, val in items:
            if start is None:
                start = addr
                prev = addr
                block = [val]
                continue
            assert prev is not None
            if addr == prev + 1:
                block.append(val)
                prev = addr
            else:
                # Write the previous block
                self.write_registers_auto(start, block if len(block) > 1 else block[0])
                # Start new block
                start = addr
                prev = addr
                block = [val]

        if start is not None and block:
            self.write_registers_auto(start, block if len(block) > 1 else block[0])


__all__ = ["RtuConfig", "ModbusRtuClient"]
