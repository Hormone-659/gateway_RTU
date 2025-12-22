"""Abstract RTU/PLC write interface used by alarm_service.

This module hides the details of how RTU registers are written (Modbus TCP
or RTU). It provides a small, UI-independent wrapper that alarm_service can
use to send holding register writes to the RTU/PLC.

The implementation below reuses the same Modbus TCP protocol details as in
`alarm_rtu_ui.py`:

- 使用原始 Modbus TCP 帧（MBAP + PDU）。
- 目前仅实现功能码 0x06（写单个保持寄存器）。
- 业务层使用 "工程地址"（如 3501），此处自动减 1 转为 0 基 PDU 地址。
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class RtuTcpConfig:
    host: str = "192.168.0.200"
    port: int = 502
    unit_id: int = 1  # Modbus 从站地址
    timeout: float = 3.0


class RtuWriter:
    """Modbus TCP RTU writer abstraction.

    This class maintains its own TCP connection and provides a simple
    `write_registers` method that accepts a mapping {address: value} where
    address is the engineering address (e.g. 3501). It converts this into
    Modbus PDU addresses (address-1) and sends function code 0x06 for each
    register.
    """

    def __init__(self, config: Optional[RtuTcpConfig] = None) -> None:
        self._cfg = config or RtuTcpConfig()
        self._sock: Optional[socket.socket] = None
        self._transaction_id: int = 1
        self._last_logs: List[str] = []

    # ------------------------ public API ------------------------

    def write_registers(self, registers: Dict[int, int], alarm_level: int) -> None:
        """Write multiple holding registers to the RTU/PLC using Modbus TCP.

        Parameters
        ----------
        registers:
            Mapping from engineering address (e.g. 3501) to integer value.
        alarm_level:
            Overall alarm level, used only for logging.
        """

        if not registers:
            return

        self._ensure_connected()

        summary = f"[rtu_comm] Write RTU, alarm_level={alarm_level}, count={len(registers)}"
        self._append_log(summary)

        try:
            for addr, value in sorted(registers.items()):
                self._write_single_holding(addr, value)
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"[rtu_comm] ERROR while writing RTU: {exc}")
            # 遇到异常时尝试关闭连接，下一次写入时会重新连接
            self._close_socket()

    def read_holding_registers(self, address: int, count: int = 1) -> List[int]:
        """Read holding registers from the RTU/PLC using Modbus TCP.

        Parameters
        ----------
        address:
            Starting engineering address (e.g. 101).
        count:
            Number of registers to read.

        Returns
        -------
        List of integer values read from the registers.
        """
        self._ensure_connected()

        # Engineering address -> PDU address
        pdu_addr = address - 1

        # PDU: func(0x03) + start_addr + count
        pdu = struct.pack(">BHH", 0x03, pdu_addr, count)

        try:
            resp = self._send_modbus_request(pdu)
        except Exception as exc:
            self._append_log(f"[rtu_comm] ERROR while reading RTU: {exc}")
            self._close_socket()
            return []

        if len(resp) < 2:
            raise RuntimeError(f"Invalid response length for read_holding: {len(resp)}")

        fc = resp[0]
        if fc & 0x80:
            exc_code = resp[1] if len(resp) > 1 else None
            raise RuntimeError(
                f"RTU exception response: func=0x{fc:02X}, exc_code={exc_code}"
            )
        if fc != 0x03:
            raise RuntimeError(f"Unexpected function code in response: 0x{fc:02X}")

        byte_count = resp[1]
        if len(resp) != 2 + byte_count:
             raise RuntimeError(f"Response length mismatch: expected {2 + byte_count}, got {len(resp)}")

        values = []
        for i in range(count):
            val = struct.unpack(">H", resp[2 + i*2 : 4 + i*2])[0]
            values.append(val)

        return values

    def get_recent_logs(self) -> List[str]:
        return list(self._last_logs)

    # ------------------------ internal helpers ------------------------

    def _append_log(self, msg: str) -> None:
        print(msg)
        self._last_logs.append(msg)
        if len(self._last_logs) > 3:
            self._last_logs = self._last_logs[-3:]

    def _ensure_connected(self) -> None:
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._cfg.timeout)
        try:
            sock.connect((self._cfg.host, self._cfg.port))
        except OSError as exc:
            sock.close()
            raise RuntimeError(
                f"Failed to connect to RTU {self._cfg.host}:{self._cfg.port}: {exc}"
            ) from exc
        self._sock = sock
        self._append_log(
            f"[rtu_comm] Connected to RTU {self._cfg.host}:{self._cfg.port}, unit_id={self._cfg.unit_id}"
        )

    def _close_socket(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            finally:
                self._sock = None

    def _next_tid(self) -> int:
        tid = self._transaction_id
        self._transaction_id = 1 if tid >= 0xFFFF else tid + 1
        return tid

    def _send_modbus_request(self, pdu: bytes) -> bytes:
        """发送 Modbus TCP 请求并返回完整 PDU 响应（不含 MBAP+unit）。"""

        self._ensure_connected()
        assert self._sock is not None

        tid = self._next_tid()
        protocol_id = 0
        length = len(pdu) + 1  # unit id + pdu
        mbap = struct.pack(">HHHB", tid, protocol_id, length, self._cfg.unit_id)
        req = mbap + pdu

        try:
            self._sock.sendall(req)
        except OSError as exc:
            raise RuntimeError(f"Failed to send Modbus request: {exc}") from exc

        # 先收 7 字节 MBAP
        header = self._recv_exact(7)
        r_tid, r_pid, r_len, r_unit = struct.unpack(">HHHB", header)
        if r_tid != tid:
            self._append_log(f"[rtu_comm] WARN: transaction id mismatch local={tid}, resp={r_tid}")
        if r_pid != 0:
            self._append_log(f"[rtu_comm] WARN: protocol id non-zero: {r_pid}")
        if r_unit != self._cfg.unit_id:
            self._append_log(
                f"[rtu_comm] WARN: unit id mismatch local={self._cfg.unit_id}, resp={r_unit}"
            )

        pdu_len = r_len - 1
        pdu_resp = self._recv_exact(pdu_len)
        return pdu_resp

    def _recv_exact(self, size: int) -> bytes:
        if self._sock is None:
            raise RuntimeError("Socket is not connected")
        data = b""
        while len(data) < size:
            chunk = self._sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError("Connection closed by peer while receiving")
            data += chunk
        return data

    def _write_single_holding(self, address: int, value: int) -> None:
        """使用功能码06写单个保持寄存器。

        说明：
        - 上层传入的是工程地址（如 3501），本函数内部自动减 1 转为 0 基 PDU 地址。
        - 若 RTU 返回异常响应或回读数据不匹配，则抛出异常。
        """

        # 工程地址(如 3501) -> PDU 地址(如 3500)
        pdu_addr = address - 1

        # PDU: func + addr + value
        pdu = struct.pack(">BHH", 0x06, pdu_addr, value)
        resp = self._send_modbus_request(pdu)
        if len(resp) < 5:
            raise RuntimeError(f"Invalid response length for write_single: {len(resp)}")

        fc = resp[0]
        if fc & 0x80:
            exc_code = resp[1] if len(resp) > 1 else None
            raise RuntimeError(
                f"RTU exception response: func=0x{fc:02X}, exc_code={exc_code}"
            )
        if fc != 0x06:
            raise RuntimeError(f"Unexpected function code in response: 0x{fc:02X}")

        r_addr, r_val = struct.unpack(">HH", resp[1:5])
        if r_addr != pdu_addr or r_val != value:
            raise RuntimeError(
                f"Mismatch in write response: addr={r_addr} (expected {pdu_addr}), val={r_val} (expected {value})"
            )


__all__ = ["RtuTcpConfig", "RtuWriter"]
