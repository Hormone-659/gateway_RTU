"""RTU 写入演示 UI。

用途：
- 在界面中调节各传感器等级、电参、载荷位移，
  然后一键把当前报警状态按照 RTU_address_fuc.csv 的映射写入 RTU（Modbus TCP）。
- 与 `alarm_demo_ui.py` 的区别：
  - `alarm_demo_ui.py` 只演示规则，不真正写 RTU；
  - 本文件 `alarm_rtu_ui.py` 专门负责“UI 改值 -> 真写 RTU”。

运行方式：
    cd E:\gateway_RTU\src\gateway\alarm
    python alarm_rtu_ui.py

前置条件：
- RTU 设备 IP 为 192.168.0.200，端口 502，从站地址 unit=1（如不同可在本文件中修改或在界面中调整）。
"""

from __future__ import annotations

import os
import socket
import struct
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from src.gateway.alarm.alarm_play.alarm_logic import SensorState, build_rtu_registers, evaluate_alarms

# 新增：从 sensor 侧获取故障等级
try:
    from src.gateway.sensor.fault_state_bridge import (
        get_latest_levels_for_alarm,
        map_to_state_fields,
    )
except Exception:  # pragma: no cover - 如果 sensor 模块不可用，则自动禁用自动模式
    get_latest_levels_for_alarm = None
    map_to_state_fields = None


RTU_IP_DEFAULT = "192.168.0.200"
RTU_PORT_DEFAULT = 502
RTU_UNIT_ID_DEFAULT = 1  # Modbus 从站地址，可按现场实际修改


class AlarmRTUWriteUI:
    """可实际写入 RTU 的报警模拟界面。"""

    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.master.title("报警 -> RTU 写入模拟器")

        # 记录 vibration_monitor_1.py 的子进程句柄（如果成功启动）
        self._sensor_proc = None  # type: Optional[subprocess.Popen]

        # 写 RTU 操作的最近日志（最多保留3条，包含当前和前两条）
        self._rtu_log_history = []  # type: list[str]

        # 尝试自动启动 vibration_monitor_1.py
        self._start_vibration_monitor_subprocess()

        # 当前 TCP 连接状态（原始 Modbus TCP）
        self._sock: Optional[socket.socket] = None
        self._unit_id: Optional[int] = None
        self._transaction_id: int = 1

        # 连接参数变量
        self.ip_var = tk.StringVar(value=RTU_IP_DEFAULT)
        self.port_var = tk.StringVar(value=str(RTU_PORT_DEFAULT))
        self.unit_var = tk.StringVar(value=str(RTU_UNIT_ID_DEFAULT))

        # 传感器等级
        self.belt_level = tk.IntVar(value=0)
        self.mid_bearing_level = tk.IntVar(value=0)
        self.tail_bearing_level = tk.IntVar(value=0)
        self.horsehead_level = tk.IntVar(value=0)
        self.crank_left_level = tk.IntVar(value=0)
        self.crank_right_level = tk.IntVar(value=0)
        self.line_level = tk.IntVar(value=0)

        # 电参 / 载荷位移
        self.elec_phase_a_ok = tk.BooleanVar(value=True)
        self.elec_phase_b_ok = tk.BooleanVar(value=True)
        self.elec_phase_c_ok = tk.BooleanVar(value=True)
        self.loadpos_ok = tk.BooleanVar(value=True)

        # 单寄存器调试变量
        self.manual_addr = tk.StringVar(value="3501")
        self.manual_value = tk.StringVar(value="1")

        # 新增：
        # - 是否启用从传感器自动更新等级
        # - 是否在检测到等级变化后自动写入 RTU
        self.auto_from_sensor = tk.BooleanVar(value=False)
        self.auto_write_rtu = tk.BooleanVar(value=False)

        # 记录上一次写入 RTU 时的 SensorState（用于变化检测）
        # 在 Python 3.8 下避免使用 X | Y 语法，简单标注为 SensorState，并允许 None 运行时赋值
        self._last_written_state = None  # type: Optional[SensorState]

        self._build_ui()

    # ------------------------ UI 构建 ------------------------

    def _build_ui(self) -> None:
        root = self.master
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)

        frame = ttk.Frame(root, padding=10)
        frame.grid(row=0, column=0, sticky="nsew")

        row = 0
        # 一、RTU 连接参数
        ttk.Label(frame, text="一、RTU 连接参数：", font=("SimHei", 11, "bold")).grid(
            row=row, column=0, columnspan=4, sticky="w"
        )
        row += 1

        ttk.Label(frame, text="IP 地址：").grid(row=row, column=0, sticky="e")
        ttk.Entry(frame, textvariable=self.ip_var, width=18).grid(row=row, column=1, sticky="w")

        ttk.Label(frame, text="端口：").grid(row=row, column=2, sticky="e")
        ttk.Entry(frame, textvariable=self.port_var, width=8).grid(row=row, column=3, sticky="w")
        row += 1

        ttk.Label(frame, text="单元 ID：").grid(row=row, column=0, sticky="e")
        ttk.Entry(frame, textvariable=self.unit_var, width=8).grid(row=row, column=1, sticky="w")

        btn_connect = ttk.Button(frame, text="连接 RTU", command=self.on_connect)
        btn_connect.grid(row=row, column=2, sticky="ew")

        btn_disconnect = ttk.Button(frame, text="断开 RTU", command=self.on_disconnect)
        btn_disconnect.grid(row=row, column=3, sticky="ew")
        self._btn_connect = btn_connect
        self._btn_disconnect = btn_disconnect
        row += 1

        ttk.Separator(frame, orient="horizontal").grid(
            row=row, column=0, columnspan=4, sticky="ew", pady=6
        )
        row += 1

        # 二、传感器等级设置
        ttk.Label(frame, text="二、传感器等级设置 (0-3)：", font=("SimHei", 11, "bold")).grid(
            row=row, column=0, columnspan=4, sticky="w"
        )
        row += 1

        # 新增一行：自动从传感器更新 & 自动写入 RTU
        if get_latest_levels_for_alarm is not None and map_to_state_fields is not None:
            ttk.Checkbutton(
                frame,
                text="从传感器自动更新等级",
                variable=self.auto_from_sensor,
            ).grid(row=row, column=0, columnspan=2, sticky="w")
            ttk.Checkbutton(
                frame,
                text="检测等级变化后自动写入 RTU",
                variable=self.auto_write_rtu,
            ).grid(row=row, column=2, columnspan=2, sticky="w")
            row += 1
        # 然后再布置各个部位的 Spinbox
        self._add_level(frame, "皮带光电", self.belt_level, row)
        row += 1
        self._add_level(frame, "中部轴承", self.mid_bearing_level, row)
        row += 1
        self._add_level(frame, "尾部轴承", self.tail_bearing_level, row)
        row += 1
        self._add_level(frame, "驴头", self.horsehead_level, row)
        row += 1
        self._add_level(frame, "左曲柄", self.crank_left_level, row)
        row += 1
        self._add_level(frame, "右曲柄", self.crank_right_level, row)
        row += 1

        ttk.Separator(frame, orient="horizontal").grid(
            row=row, column=0, columnspan=4, sticky="ew", pady=6
        )
        row += 1

        # 三、电参 / 载荷位移
        ttk.Label(frame, text="三、电参 / 载荷位移：", font=("SimHei", 11, "bold")).grid(
            row=row, column=0, columnspan=4, sticky="w"
        )
        row += 1

        ttk.Checkbutton(
            frame, text="电参 A 相正常", variable=self.elec_phase_a_ok
        ).grid(row=row, column=0, sticky="w")
        row += 1
        ttk.Checkbutton(
            frame, text="电参 B 相正常", variable=self.elec_phase_b_ok
        ).grid(row=row, column=0, sticky="w")
        row += 1
        ttk.Checkbutton(
            frame, text="电参 C 相正常", variable=self.elec_phase_c_ok
        ).grid(row=row, column=0, sticky="w")
        row += 1
        ttk.Checkbutton(
            frame, text="载荷异常", variable=self.loadpos_ok, onvalue=False, offvalue=True
        ).grid(row=row, column=0, sticky="w")
        row += 1

        ttk.Separator(frame, orient="horizontal").grid(
            row=row, column=0, columnspan=4, sticky="ew", pady=6
        )
        row += 1

        # 四、报警整体写入 RTU
        ttk.Label(frame, text="四、根据报警逻辑写入 RTU：", font=("SimHei", 11, "bold")).grid(
            row=row, column=0, columnspan=4, sticky="w"
        )
        row += 1

        btn_write = ttk.Button(frame, text="按报警逻辑写入 RTU", command=self.on_write_rtu)
        btn_write.grid(row=row, column=0, sticky="ew")

        btn_reset = ttk.Button(frame, text="恢复默认(3501-3520清零)", command=self.on_reset_defaults)
        btn_reset.grid(row=row, column=1, sticky="ew")

        btn_quit = ttk.Button(frame, text="退出", command=self.master.destroy)
        btn_quit.grid(row=row, column=2, sticky="ew")
        row += 1

        # 五、单寄存器调试区（读 / 写）
        ttk.Separator(frame, orient="horizontal").grid(
            row=row, column=0, columnspan=4, sticky="ew", pady=6
        )
        row += 1

        ttk.Label(frame, text="五、单寄存器读写（调试用）：", font=("SimHei", 11, "bold")).grid(
            row=row, column=0, columnspan=4, sticky="w"
        )
        row += 1

        ttk.Label(frame, text="寄存器地址（保持寄存器，例如 3501）：").grid(row=row, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.manual_addr, width=12).grid(row=row, column=1, sticky="w")

        ttk.Label(frame, text="写入数值（可选）：").grid(row=row, column=2, sticky="e")
        ttk.Entry(frame, textvariable=self.manual_value, width=10).grid(row=row, column=3, sticky="w")
        row += 1

        btn_manual_read = ttk.Button(frame, text="读取单寄存器", command=self.on_manual_read)
        btn_manual_read.grid(row=row, column=0, sticky="w")

        btn_manual_write = ttk.Button(frame, text="写入单寄存器", command=self.on_manual_write)
        btn_manual_write.grid(row=row, column=1, sticky="w")
        row += 1

        # 结果展示区
        self.text_result = tk.Text(frame, width=80, height=18)
        self.text_result.grid(row=row, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
        frame.rowconfigure(row, weight=1)

        # 在 UI 构建结束前，如果支持自动模式，则启动周期刷新
        if get_latest_levels_for_alarm is not None and map_to_state_fields is not None:
            self.master.after(1000, self._auto_refresh_from_sensor)

    def _add_level(self, parent: tk.Widget, label: str, var: tk.IntVar, row: int) -> None:
        """添加一行等级调节控件。"""

        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        spin = ttk.Spinbox(parent, from_=0, to=3, textvariable=var, width=5)
        spin.grid(row=row, column=1, sticky="w")

    # ------------------------ 通用连接辅助（原始 Modbus TCP） ------------------------

    def _parse_unit_id(self) -> Optional[int]:
         unit_str = self.unit_var.get().strip() or str(RTU_UNIT_ID_DEFAULT)
         try:
             return int(unit_str)
         except ValueError:
             messagebox.showerror("错误", f"单元 ID 必须是整数: {unit_str}")
             return None

    def _log_text(self, msg: str) -> None:
        self.text_result.insert(tk.END, msg + "\n")
        self.text_result.see(tk.END)

    # ------------------------ 连接 / 断开 ------------------------

    def on_connect(self) -> None:
        """建立到 RTU 的 Modbus TCP 连接（原始帧）。"""

        if self._sock is not None:
            messagebox.showinfo("提示", "已经处于连接状态。")
            return

        ip = self.ip_var.get().strip() or RTU_IP_DEFAULT
        try:
            port = int(self.port_var.get().strip() or RTU_PORT_DEFAULT)
        except ValueError:
            messagebox.showerror("错误", f"端口必须是整数: {self.port_var.get()}")
            return

        unit_id = self._parse_unit_id()
        if unit_id is None:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        try:
            sock.connect((ip, port))
        except OSError as exc:
            sock.close()
            messagebox.showerror("错误", f"无法连接到 RTU ({ip}:{port})，异常: {exc}")
            return

        self._sock = sock
        self._unit_id = unit_id
        self._transaction_id = 1
        self._btn_connect.configure(state="disabled")
        self._btn_disconnect.configure(state="normal")
        messagebox.showinfo("连接成功", f"已连接到 RTU ({ip}:{port}), 单元ID={unit_id}")

    def on_disconnect(self) -> None:
        """断开与 RTU 的 TCP 连接。"""

        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._unit_id = None
        self._btn_connect.configure(state="normal")
        self._btn_disconnect.configure(state="normal")
        messagebox.showinfo("已断开", "已断开与 RTU 的连接。")

    def _ensure_connected(self) -> bool:
        if self._sock is None or self._unit_id is None:
            messagebox.showwarning("未连接", "请先点击“连接 RTU”。")
            return False
        return True

    # ------------------------ 原始 Modbus TCP 帧工具 ------------------------

    def _next_tid(self) -> int:
        tid = self._transaction_id
        self._transaction_id = 1 if tid >= 0xFFFF else tid + 1
        return tid

    def _send_modbus_request(self, pdu: bytes) -> Optional[bytes]:
        """发送 Modbus TCP 请求并返回 PDU 响应（去掉 MBAP+unit）。"""

        if not self._ensure_connected():
            return None

        assert self._sock is not None
        assert self._unit_id is not None

        tid = self._next_tid()
        protocol_id = 0
        length = len(pdu) + 1  # unit id + pdu
        mbap = struct.pack(">HHHB", tid, protocol_id, length, self._unit_id)
        req = mbap + pdu

        try:
            self._sock.sendall(req)
        except OSError as exc:
            messagebox.showerror("错误", f"发送报文失败: {exc}")
            return None

        # 先收 7 字节 MBAP
        try:
            header = self._recv_exact(7)
        except OSError as exc:
            messagebox.showerror("错误", f"接收响应头失败: {exc}")
            return None
        if header is None:
            return None

        r_tid, r_pid, r_len, r_unit = struct.unpack(">HHHB", header)
        if r_tid != tid:
            self._log_text(f"警告：事务ID不匹配，本地={tid}, 响应={r_tid}")
        if r_pid != 0:
            self._log_text(f"警告：协议ID非0: {r_pid}")
        if r_unit != self._unit_id:
            self._log_text(f"警告：Unit ID 不匹配，本地={self._unit_id}, 响应={r_unit}")

        pdu_len = r_len - 1
        try:
            pdu_resp = self._recv_exact(pdu_len)
        except OSError as exc:
            messagebox.showerror("错误", f"接收响应PDU失败: {exc}")
            return None
        return pdu_resp

    def _recv_exact(self, size: int) -> Optional[bytes]:
        if self._sock is None:
            return None
        data = b""
        while len(data) < size:
            chunk = self._sock.recv(size - len(data))
            if not chunk:
                self._log_text("连接被对端关闭")
                return None
            data += chunk
        return data

    def _read_holding_single(self, address: int) -> Optional[int]:
        """使用功能码03读取单个保持寄存器。

        说明：
        - 界面/业务中约定使用 "工程地址"（即 RTU_address_fuc.csv 中的 3501 表示 PLC 43501）。
        - 现场 RTU 实际采用 0 基地址：PLC 43501 对应 Modbus PDU 地址 3500。
        - 因此，这里的 address 为 3501 时，需要先减 1，转换为 PDU 地址 3500 再发送。
        """

        # 工程地址(如 3501) -> PDU 地址(如 3500)
        pdu_addr = address - 1

        # PDU: func(1) + addr(2) + count(2)
        pdu = struct.pack(">BHH", 0x03, pdu_addr, 1)
        resp = self._send_modbus_request(pdu)
        if not resp or len(resp) < 4:
            return None
        fc = resp[0]
        if fc & 0x80:
            exc_code = resp[1] if len(resp) > 1 else None
            self._log_text(f"读取寄存器异常响应: 功能码=0x{fc:02X}, 异常码={exc_code}")
            return None
        if fc != 0x03:
            self._log_text(f"读取寄存器响应功能码不匹配: 期望0x03, 实际0x{fc:02X}")
            return None
        byte_count = resp[1]
        if byte_count != 2 or len(resp) < 4:
            self._log_text(f"读取寄存器响应字节数异常: {byte_count}")
            return None
        value = (resp[2] << 8) | resp[3]
        return value

    def _write_holding_single(self, address: int, value: int) -> bool:
        """使用功能码06写单个保持寄存器。

        说明：
        - 界面/业务中约定使用 "工程地址"（即 RTU_address_fuc.csv 中的 3501 表示 PLC 43501）。
        - 现场 RTU 实际采用 0 基地址：PLC 43501 对应 Modbus PDU 地址 3500。
        - 因此，这里的 address 为 3501 时，需要先减 1，转换为 PDU 地址 3500 再发送。
        """

        # 工程地址(如 3501) -> PDU 地址(如 3500)
        pdu_addr = address - 1

        # PDU: func + addr + value
        pdu = struct.pack(">BHH", 0x06, pdu_addr, value)
        resp = self._send_modbus_request(pdu)
        if not resp or len(resp) < 5:
            return False
        fc = resp[0]
        if fc & 0x80:
            exc_code = resp[1] if len(resp) > 1 else None
            self._log_text(f"写寄存器异常响应: 功能码=0x{fc:02X}, 异常码={exc_code}")
            return False
        if fc != 0x06:
            self._log_text(f"写寄存器响应功能码不匹配: 期望0x06, 实际0x{fc:02X}")
            return False
        r_addr, r_val = struct.unpack(">HH", resp[1:5])
        if r_addr != pdu_addr or r_val != value:
            self._log_text(f"写寄存器响应数据不匹配: addr={r_addr}, val={r_val}")
            return False
        return True

    # ------------------------ 状态构造 ------------------------

    def _build_state(self) -> SensorState:
        """从界面当前值构造一个 SensorState。"""

        return SensorState(
            belt_level=self.belt_level.get(),
            mid_bearing_level=self.mid_bearing_level.get(),
            tail_bearing_level=self.tail_bearing_level.get(),
            horsehead_level=self.horsehead_level.get(),
            crank_left_level=self.crank_left_level.get(),
            crank_right_level=self.crank_right_level.get(),
            line_level=self.line_level.get(),
            elec_phase_a_ok=self.elec_phase_a_ok.get(),
            elec_phase_b_ok=self.elec_phase_b_ok.get(),
            elec_phase_c_ok=self.elec_phase_c_ok.get(),
            loadpos_ok=self.loadpos_ok.get(),
        )

    # ------------------------ 报警整体写入 RTU ------------------------

    def on_write_rtu(self) -> None:
        """根据当前界面参数计算寄存器，并通过原始 Modbus TCP 写入 RTU。"""

        if not self._ensure_connected():
            return

        try:
            state = self._build_state()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("错误", f"读取界面参数失败: {exc}")
            return

        self._write_rtu_for_state(state)

    def _write_rtu_for_state(self, state: SensorState) -> None:
        """给定一个 SensorState，根据报警逻辑写入 RTU，并记录本次写入的状态。"""

        try:
            file_map = evaluate_alarms(state)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("错误", f"计算报警文件映射失败: {exc}")
            return

        try:
            registers = build_rtu_registers(state)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("错误", f"生成 RTU 寄存器数据失败: {exc}")
            return

        self._write_to_rtu(registers, file_map)
        # 写入成功后，记录当前状态为“上一次写入状态”
        self._last_written_state = state

    # ------------------------ Modbus 写入（报警整体） ------------------------

    def _write_to_rtu(self, registers, file_map) -> None:
        """使用原始 Modbus TCP 将 registers 写入 RTU，并在文本框顶部展示报警等级和关键 txt 文件。"""

        # ---------------- 文本框中先展示报警等级和关键 txt 文件 ----------------
        # 整体报警等级寄存器为 3502（参见 RTU_address_fuc.csv），这里直接读取 3502
        overall_level = registers.get(3502, 0)

        def _get_flag(path_key: str) -> str:
            return file_map.get(path_key, "0")

        from pathlib import Path

        base = Path(__file__).parent / "alarm_level"
        sensor3_flag = _get_flag(str(base / "level_3" / "sensor_3.txt"))
        belt_all3_flag = _get_flag(str(base / "level_3" / "belt_all_3.txt"))
        line3_flag = _get_flag(str(base / "level_3" / "line_3.txt"))
        level1_flag = _get_flag(str(base / "level_1" / "level_1.txt"))
        level2_flag = _get_flag(str(base / "level_2" / "level_2.txt"))
        level3_flag = _get_flag(str(base / "level_3" / "level_3.txt"))

        # 在文本框中先清空，再写入当前报警汇总信息
        self.text_result.delete("1.0", tk.END)
        self.text_result.insert(
            tk.END,
            "当前报警汇总：\n"
            f"  整体报警等级（0=无报警,1/2/3 级）：{overall_level}\n"
            f"  level_1.txt：{level1_flag}\n"
            f"  level_2.txt：{level2_flag}\n"
            f"  level_3.txt：{level3_flag}\n"
            f"  sensor_3.txt：{sensor3_flag}\n"
            f"  belt_all_3.txt：{belt_all3_flag}\n"
            f"  line_3.txt：{line3_flag}\n\n",
        )

        # 写入寄存器明细
        self.text_result.insert(
            tk.END,
            f"准备向 RTU {self.ip_var.get().strip() or RTU_IP_DEFAULT}:{self.port_var.get().strip() or RTU_PORT_DEFAULT} 写入以下保持寄存器 (地址 -> 值)：\n\n",
        )
        for addr in sorted(registers):
            self.text_result.insert(tk.END, f"地址 {addr} -> {registers[addr]}\n")

        try:
            # 简单实现：按字典逐个写保持寄存器（地址即 Modbus 地址）
            for addr, val in sorted(registers.items()):
                ok = self._write_holding_single(addr, val)
                if not ok:
                    raise RuntimeError(f"写寄存器失败: 地址={addr}, 值={val}")

            # 组织一条本次写 RTU 的概要日志
            summary = f"写 RTU 成功：目标 {self.ip_var.get().strip() or RTU_IP_DEFAULT}:{self.port_var.get().strip() or RTU_PORT_DEFAULT}，寄存器数量={len(registers)}，整体报警等级={overall_level}"
            self._append_rtu_history(summary)

        except Exception as exc:  # noqa: BLE001
            err_msg = f"写 RTU 失败：{exc}"
            self._append_rtu_history(err_msg)
            messagebox.showerror("错误", err_msg)

    def _append_rtu_history(self, msg: str) -> None:
        """追加一条写 RTU 的历史记录，只保留最近三条，并在文本框底部展示。"""

        # 维护最近三条
        self._rtu_log_history.append(msg)
        if len(self._rtu_log_history) > 3:
            self._rtu_log_history = self._rtu_log_history[-3:]

        # 在现有文本下方追加一个分隔和历史三条记录
        self.text_result.insert(tk.END, "\n---- 最近三次写 RTU 记录 ----\n")
        for item in self._rtu_log_history:
            self.text_result.insert(tk.END, item + "\n")
        self.text_result.see(tk.END)

    # ------------------------ 恢复默认：3501-3520 清零 ------------------------

    def on_reset_defaults(self) -> None:
        """将地址 3501~3520 对应的寄存器全部写 0。

        现在约定：3501 就是 Modbus PDU 地址（对应 PLC 43501），不再减 1。
        使用功能码 06 逐个清零，适合作为“恢复默认”操作。
        """

        if not self._ensure_connected():
            return

        start_addr = 3501
        end_addr = 3520

        self._log_text(
            f"开始恢复默认：地址 {start_addr}-{end_addr} 全部写 0"
        )

        try:
            for addr in range(start_addr, end_addr + 1):
                ok = self._write_holding_single(addr, 0)
                if not ok:
                    raise RuntimeError(
                        f"写寄存器失败: 地址={addr}, 值=0"
                    )

            self._log_text("恢复默认完成：3501-3520 已全部写为 0")
            messagebox.showinfo("完成", "3501-3520 已全部恢复为 0。")

        except Exception as exc:  # noqa: BLE001
            self._log_text(f"恢复默认失败: {exc}")
            messagebox.showerror("错误", f"恢复默认失败: {exc}")

    # ------------------------ 单寄存器读写（调试） ------------------------

    def on_manual_read(self) -> None:
        """读取界面中指定地址的单个保持寄存器值。"""

        addr_str = self.manual_addr.get().strip()
        try:
            addr = int(addr_str)
        except ValueError:
            messagebox.showerror("错误", f"寄存器地址必须是整数：{addr_str}")
            return

        if not self._ensure_connected():
            return

        self._log_text(f"读取单寄存器：地址={addr}")
        value = self._read_holding_single(addr)
        if value is None:
            messagebox.showerror("错误", "单寄存器读取失败，请查看日志。")
            return

        self._log_text(f"读取成功：地址 {addr} 的当前值为 {value}")
        messagebox.showinfo("读取结果", f"地址 {addr} 的当前值为: {value}")

    def on_manual_write(self) -> None:
        """根据界面输入的寄存器地址和值，单独写入一个保持寄存器（调试用）。"""

        addr_str = self.manual_addr.get().strip()
        val_str = self.manual_value.get().strip()
        try:
            addr = int(addr_str)
        except ValueError:
            messagebox.showerror("错误", f"寄存器地址必须是整数：{addr_str}")
            return

        try:
            value = int(val_str)
        except ValueError:
            messagebox.showerror("错误", f"写入数值必须是整数：{val_str}")
            return

        self._log_text(f"写入单寄存器：地址={addr}，值={value}")
        ok = self._write_holding_single(addr, value)
        if not ok:
            messagebox.showerror("错误", "单寄存器写入失败，请查看日志。")
            return

        self._log_text("写入成功，请在 RTU 侧核对该寄存器的数值。")
        messagebox.showinfo("完成", "单寄存器写入成功。")

    # 新增：周期性从 sensor 读取等级并更新界面
    def _auto_refresh_from_sensor(self) -> None:
        """若启用自动模式，则从 sensor.fault_state_bridge 读取最新等级并更新界面变量；
        如启用了自动写入 RTU 且检测到等级变化，则自动根据报警逻辑写入 RTU。
        """

        try:
            if self.auto_from_sensor.get() and get_latest_levels_for_alarm and map_to_state_fields:
                snap = get_latest_levels_for_alarm()
                field_levels = map_to_state_fields(snap)
                # 将映射到的字段填入对应 IntVar
                val = field_levels.get("crank_left_level")
                if val is not None:
                    self.crank_left_level.set(int(val))
                val = field_levels.get("crank_right_level")
                if val is not None:
                    self.crank_right_level.set(int(val))
                val = field_levels.get("tail_bearing_level")
                if val is not None:
                    self.tail_bearing_level.set(int(val))
                val = field_levels.get("mid_bearing_level")
                if val is not None:
                    self.mid_bearing_level.set(int(val))

                # 如果开启了“自动写入 RTU”，并且 RTU 已连接，则检查是否需要自动写入
                if self.auto_write_rtu.get() and self._ensure_connected():
                    current_state = self._build_state()
                    if self._state_changed(self._last_written_state, current_state):
                        # 这里不弹窗，直接静默写入，但在日志区打印一行提示
                        self._log_text("[auto] 检测到传感器故障等级变化，自动写入 RTU。")
                        self._write_rtu_for_state(current_state)
        except Exception:
            # 压制异常避免影响 UI 运行，如需要可在此处打印详细日志
            pass

        # 继续下一次调度
        self.master.after(1000, self._auto_refresh_from_sensor)

    # 新增：比较两个 SensorState 是否在关键字段上有变化
    def _state_changed(self, old, new: SensorState) -> bool:
        """判断关键字段是否发生变化。

        参数：
            old: 上一次写入的 SensorState 或 None
            new: 当前界面构造的 SensorState
        """
        if old is None:
            return True
        # 目前只关心与振动相关的四个字段，其它字段可以按需扩展
        watched_fields = [
            "mid_bearing_level",
            "tail_bearing_level",
            "crank_left_level",
            "crank_right_level",
        ]
        for f in watched_fields:
            if getattr(old, f) != getattr(new, f):
                return True
        return False

    def _start_vibration_monitor_subprocess(self) -> None:
        """自动以子进程方式启动 vibration_monitor_1.py（如果能找到脚本的话）。"""

        try:
            # 计算 vibration_monitor_1.py 的路径：
            # 当前文件位于 .../src/gateway/alarm/alarm_play/alarm_rtu_ui.py
            # 目标文件位于 .../src/gateway/sensor/vibration_monitor_1.py
            base_dir = os.path.dirname(os.path.dirname(__file__))  # .../src/gateway/alarm
            src_root = os.path.dirname(base_dir)                   # .../src/gateway
            sensor_dir = os.path.join(src_root, "sensor")         # .../src/gateway/sensor
            vm_path = os.path.join(sensor_dir, "vibration_monitor_1.py")

            if not os.path.exists(vm_path):
                # 找不到脚本就直接返回，不影响报警 UI 使用
                return

            # 使用当前 Python 解释器启动一个新的进程运行 vibration_monitor_1.py
            # 工作目录设置为项目 src 根目录，便于相对导入
            project_src_root = os.path.dirname(src_root)  # .../src
            self._sensor_proc = subprocess.Popen(
                [sys.executable, vm_path],
                cwd=project_src_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0,
            )
        except Exception:
            # 启动失败时静默忽略，不影响报警 UI 的其它功能
            self._sensor_proc = None

    # 在应用退出时，尝试结束 vibration_monitor_1 子进程
    def _on_close(self) -> None:
        try:
            if self._sensor_proc is not None:
                # 尝试温和结束子进程
                self._sensor_proc.terminate()
        except Exception:
            pass
        finally:
            self.master.destroy()


def main() -> None:
    root = tk.Tk()
    app = AlarmRTUWriteUI(root)
    # 重新绑定关闭事件，确保退出时清理子进程
    root.protocol("WM_DELETE_WINDOW", app._on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
