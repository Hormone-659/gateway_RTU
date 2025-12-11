import struct
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import List

# 为 matplotlib 中文显示做全局字体设置（在导入 matplotlib 之前配置）
try:  # 可选：如果系统没有 matplotlib 或字体，这段不会影响主功能
    import matplotlib

    # 使用常见中文字体，例如 SimHei（黑体）；如果系统没有，会回退为默认字体
    matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
    matplotlib.rcParams["axes.unicode_minus"] = False  # 正常显示负号
except Exception:
    matplotlib = None

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None

# 从同一目录导入阈值分析器，适合直接运行此脚本
from threshold_analyzer import ThresholdConfig, MultiChannelThresholdAnalyzer



class ModbusRtuClient:
    """极简 Modbus RTU 客户端，只实现读保持寄存器 (FC=3)。"""

    def __init__(self, port: str, baudrate: int = 9600, bytesize: int = 8,
                 parity: str = 'N', stopbits: int = 1, timeout: float = 0.5, unit_id: int = 1):
        if serial is None:
            raise RuntimeError("pyserial 未安装，请先安装: pip install pyserial")

        self._ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
        )
        self._lock = threading.Lock()
        self.unit_id = unit_id

    def close(self) -> None:
        with self._lock:
            try:
                self._ser.close()
            except Exception:
                pass

    # ---------------- Modbus RTU 基础 -----------------
    @staticmethod
    def _crc16(data: bytes) -> int:
        """Modbus RTU CRC16 (little-endian)。"""
        crc = 0xFFFF
        for ch in data:
            crc ^= ch
            for _ in range(8):
                if crc & 0x0001:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return crc & 0xFFFF

    def _send_and_recv(self, pdu: bytes) -> bytes:
        """发送 Modbus RTU 帧并返回响应 PDU（去掉地址与 CRC）。"""
        with self._lock:
            if not self._ser.is_open:
                self._ser.open()

            crc = self._crc16(bytes([self.unit_id]) + pdu)
            frame = bytes([self.unit_id]) + pdu + struct.pack('<H', crc)
            self._ser.reset_input_buffer()
            self._ser.write(frame)
            self._ser.flush()

            # 简单读取：先读前 3 个字节判断长度
            header = self._ser.read(3)
            if len(header) < 3:
                raise TimeoutError("Modbus 响应超时或长度不足")

            addr, func, byte_count = header
            if addr != self.unit_id:
                raise IOError(f"从站地址不匹配，期望 {self.unit_id}，实际 {addr}")
            if func & 0x80:
                # 异常响应
                exc_code = self._ser.read(1)
                raise IOError(f"Modbus 异常响应，功能码: {func:#x}, 异常码: {exc_code.hex() if exc_code else '??'}")

            # 读取数据 + CRC
            data_and_crc = self._ser.read(byte_count + 2)
            if len(data_and_crc) < byte_count + 2:
                raise TimeoutError("Modbus 响应数据长度不足")

            data = data_and_crc[:-2]
            crc_recv = struct.unpack('<H', data_and_crc[-2:])[0]
            crc_calc = self._crc16(header + data)
            if crc_calc != crc_recv:
                raise IOError("Modbus CRC 校验失败")

            return bytes([func, byte_count]) + data

    def read_holding_registers(self, address: int, count: int) -> List[int]:  # 使用 List[int] 以兼容旧版本
        """读取保持寄存器 (FC=3)。address 使用 Modbus PDU 地址（0 基）。"""
        if not (0 <= address <= 0xFFFF and 1 <= count <= 125):
            raise ValueError("地址或数量非法")

        pdu = struct.pack('>BHH', 0x03, address, count)
        resp = self._send_and_recv(pdu)
        func, byte_count = resp[0], resp[1]
        if func != 0x03:
            raise IOError(f"意外的功能码: {func:#x}")
        if byte_count != count * 2:
            raise IOError(f"返回字节数不匹配，应为 {count * 2}，实际 {byte_count}")

        regs = []
        data = resp[2:2 + byte_count]
        for i in range(0, byte_count, 2):
            regs.append(struct.unpack('>H', data[i:i + 2])[0])
        return regs


class VibrationMonitorApp(tk.Tk):
    """实时监视多从站振动传感器数据，读取地址 0x3A~0x3C (58~60)，并显示波形与阈值等级。"""

    def __init__(self):
        super().__init__()
        self.title("多从站振动传感器实时监视 (Modbus RTU)")
        # 将窗口进一步放大，宽度和高度都增加
        self.geometry("1400x1100")

        self._client = None
        self._polling = False
        self._poll_thread = None

        # 支持的从站 ID 列表
        self._unit_ids = [1, 2, 3, 4]
        # 从站物理位置说明
        self._unit_labels = {
            1: "曲柄销子左",
            2: "曲柄销子右",
            3: "尾轴承",
            4: "中轴承",
        }
        # 每个从站是否启用的布尔变量
        self._unit_enabled_vars = {uid: tk.BooleanVar(value=True) for uid in self._unit_ids}

        # 历史数据缓冲
        self._history_len = 200
        self._history_x = list(range(self._history_len))
        # 结构：{unit_id: {"ch1": [...], "ch2": [...], "ch3": [...]}}
        self._history = {
            uid: {
                "ch1": [0] * self._history_len,
                "ch2": [0] * self._history_len,
                "ch3": [0] * self._history_len,
            }
            for uid in self._unit_ids
        }

        # 阈值分析配置（根据现场需要调整阈值）
        self._th_cfg = ThresholdConfig(level1=10.0, level2=20.0, level3=30.0)
        # 为每个从站准备一个多通道阈值分析器（VX/VY/VZ 分别对应 58/59/60）
        self._th_analyzers = {
            uid: MultiChannelThresholdAnalyzer(self._th_cfg, ["VX", "VY", "VZ"]) for uid in self._unit_ids
        }
        # 阈值刷新周期（秒）及倒计时（秒）
        self._th_period_sec = 10
        self._th_countdown_sec = self._th_period_sec

        self._build_ui()

    # ---------------- UI 构建 -----------------
    def _build_ui(self) -> None:
        frm_conn = ttk.LabelFrame(self, text="串口参数")
        frm_conn.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(frm_conn, text="串口号：").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        # 使用下拉框提供 COM1~COM10 选择，默认 COM3
        self.var_port = tk.StringVar(value="COM3")
        port_values = [f"COM{i}" for i in range(1, 11)]
        self.combo_port = ttk.Combobox(frm_conn, textvariable=self.var_port, values=port_values, width=8, state="readonly")
        self.combo_port.grid(row=0, column=1, sticky=tk.W, padx=5)
        self.combo_port.current(2)  # 索引从 0 开始，2 对应 COM3

        ttk.Label(frm_conn, text="采样周期 (ms)：").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        self.var_interval = tk.IntVar(value=200)
        ttk.Entry(frm_conn, textvariable=self.var_interval, width=8).grid(row=0, column=3, sticky=tk.W, padx=5)

        ttk.Button(frm_conn, text="连接并开始采集", command=self.start_polling).grid(row=0, column=4, padx=10)
        ttk.Button(frm_conn, text="停止采集", command=self.stop_polling).grid(row=0, column=5, padx=5)

        # 从站启用选择
        frm_units = ttk.LabelFrame(self, text="启用的从站")
        frm_units.pack(fill=tk.X, padx=10, pady=5)
        col = 0
        for uid in self._unit_ids:
            desc = self._unit_labels.get(uid, "")
            text = f"从站 {uid}" if not desc else f"从站 {uid}（{desc}）"
            chk = ttk.Checkbutton(
                frm_units,
                text=text,
                variable=self._unit_enabled_vars[uid],
                onvalue=True,
                offvalue=False,
            )
            chk.grid(row=0, column=col, padx=5, pady=2, sticky=tk.W)
            col += 1

        ttk.Label(frm_conn, text="说明：波特率9600, 8N1, 读取保持寄存器 58~60 (0x3A~0x3C)").grid(
            row=1, column=0, columnspan=6, sticky=tk.W, padx=5
        )

        # 多从站实时数据表格
        frm_data = ttk.LabelFrame(self, text="各从站实时数据")
        frm_data.pack(fill=tk.X, padx=10, pady=5)

        # 新增一列 "当前阈值等级"
        headers = ["从站ID", "位置", "寄存器", "十六进制", "十进制", "当前阈值等级"]
        for j, h in enumerate(headers):
            ttk.Label(frm_data, text=h).grid(row=0, column=j, padx=5, pady=5)

        # 起始工程地址 58，对应设备文档中的 0x3A
        self._start_addr = 58
        # 为每个从站、每个寄存器准备显示变量：{unit_id: [(hex_var, dec_var), ...]}
        self._value_vars = {}
        # 为每个从站准备一个显示当前最高阈值等级的变量
        self._level_vars: dict[int, tk.StringVar] = {}
        row_idx = 1
        for uid in self._unit_ids:
            self._value_vars[uid] = []
            level_var = tk.StringVar(value="0")
            self._level_vars[uid] = level_var
            for offset in range(3):
                addr = self._start_addr + offset
                # 从站ID
                ttk.Label(frm_data, text=str(uid)).grid(row=row_idx, column=0, padx=5, sticky=tk.W)
                # 位置说明
                pos = self._unit_labels.get(uid, "")
                ttk.Label(frm_data, text=pos).grid(row=row_idx, column=1, padx=5, sticky=tk.W)
                # 寄存器
                ttk.Label(frm_data, text=f"{addr} (0x{addr:02X})").grid(row=row_idx, column=2, padx=5, sticky=tk.W)
                # 实际值
                hex_var = tk.StringVar(value="-")
                dec_var = tk.StringVar(value="-")
                ttk.Label(frm_data, textvariable=hex_var, width=12).grid(row=row_idx, column=3, padx=5)
                ttk.Label(frm_data, textvariable=dec_var, width=12).grid(row=row_idx, column=4, padx=5)
                self._value_vars[uid].append((hex_var, dec_var))
                # 只在该从站最后一行显示阈值等级（避免重复）
                if offset == 2:
                    ttk.Label(frm_data, textvariable=level_var, width=8).grid(row=row_idx, column=5, padx=5)
                row_idx += 1

        # 波形图区域：为每个从站一个子图
        frm_plot = ttk.LabelFrame(self, text="波形图 (各从站 VX/VY/VZ)")
        frm_plot.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            self.var_status = tk.StringVar(value="未安装 matplotlib，仅显示数值，无波形图。请 pip install matplotlib")
            ttk.Label(frm_plot, textvariable=self.var_status).pack(anchor=tk.W, padx=5, pady=3)
            frm_log = ttk.LabelFrame(self, text="状态")
            frm_log.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
            ttk.Label(frm_log, textvariable=self.var_status).pack(anchor=tk.W, padx=5, pady=3)
            return

        # 2x2 子图布局，增大间距避免重叠
        self._fig = Figure(figsize=(8, 5), dpi=100)
        self._fig.subplots_adjust(hspace=0.4, wspace=0.3)  # 调整子图间距
        self._axes = {}
        self._lines = {}  # {unit_id: (line_ch1, line_ch2, line_ch3)}
        positions = {1: 221, 2: 222, 3: 223, 4: 224}
        for uid in self._unit_ids:
            ax = self._fig.add_subplot(positions[uid])
            desc = self._unit_labels.get(uid, "")
            if desc:
                ax.set_title(f"从站 {uid}（{desc}） VX/VY/VZ 波形")
            else:
                ax.set_title(f"从站 {uid} VX/VY/VZ 波形")
            ax.set_xlabel("样本点")
            ax.set_ylabel("寄存器值")
            hist = self._history[uid]
            (line1,) = ax.plot(self._history_x, hist["ch1"], label="58(VX)")
            (line2,) = ax.plot(self._history_x, hist["ch2"], label="59(VY)")
            (line3,) = ax.plot(self._history_x, hist["ch3"], label="60(VZ)")
            ax.legend(loc="upper right", fontsize=8)
            self._axes[uid] = ax
            self._lines[uid] = (line1, line2, line3)

        self._canvas = FigureCanvasTkAgg(self._fig, master=frm_plot)
        self._canvas.draw()
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        frm_log = ttk.LabelFrame(self, text="状态")
        frm_log.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.var_status = tk.StringVar(value="未连接")
        ttk.Label(frm_log, textvariable=self.var_status).pack(anchor=tk.W, padx=5, pady=3)

        # 新增：阈值刷新倒计时显示
        self.var_countdown = tk.StringVar(value=f"阈值等级刷新倒计时: {self._th_countdown_sec} 秒")
        ttk.Label(frm_log, textvariable=self.var_countdown).pack(anchor=tk.W, padx=5, pady=3)

    # ---------------- 采集控制 -----------------
    def start_polling(self) -> None:
        if self._polling:
            return
        port = self.var_port.get().strip()
        if not port:
            messagebox.showerror("错误", "请填写串口号，例如 COM3")
            return
        try:
            self._client = ModbusRtuClient(
                port=port,
                baudrate=9600,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=0.5,
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("串口错误", f"无法打开串口 {port}: {e}")
            return

        self._polling = True
        self.var_status.set(f"已连接 {port}, 正在采集从站 {self._unit_ids}...")
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._polling = False
        if self._client is not None:
            self._client.close()
            self._client = None
        self.var_status.set("已停止采集")

    def _poll_loop(self) -> None:
        while self._polling:
            t0 = time.time()
            try:
                if self._client is None:
                    break
                # 轮询勾选启用的从站
                result = {}
                for uid in self._unit_ids:
                    if not self._unit_enabled_vars[uid].get():
                        continue
                    self._client.unit_id = uid
                    regs = self._client.read_holding_registers(address=self._start_addr, count=3)
                    result[uid] = regs
                if result:
                    self.after(0, self._update_all, result)

                    # 计算本轮采集耗时近似对应的毫秒数，并更新倒计时
                    interval_ms = max(self.var_interval.get(), 50)
                    # 将采样周期视为实际间隔，换算为秒
                    delta_sec = interval_ms / 1000.0
                    # 在主线程中更新倒计时和定期重置阈值分析器
                    self.after(0, self._update_threshold_countdown, delta_sec)
            except Exception as e:  # noqa: BLE001
                self.after(0, self._on_error, str(e))
                break

            interval_ms = max(self.var_interval.get(), 50)
            elapsed = (time.time() - t0) * 1000
            sleep_ms = max(interval_ms - elapsed, 10)
            time.sleep(sleep_ms / 1000.0)

        self.after(0, self.stop_polling)

    def _update_all(self, data: dict) -> None:
        """根据多从站数据更新数值、波形以及阈值等级。"""
        for uid, regs in data.items():
            # 更新表格数值
            if uid in self._value_vars:
                for i, val in enumerate(regs):
                    hex_var, dec_var = self._value_vars[uid][i]
                    hex_var.set(f"0x{val:04X}")
                    dec_var.set(str(val))

            # 更新历史缓冲和波形
            if uid in self._history and uid in self._lines:
                hist = self._history[uid]
                if len(regs) >= 3:
                    v1, v2, v3 = regs[0], regs[1], regs[2]
                else:
                    v1 = v2 = v3 = 0
                hist["ch1"] = (hist["ch1"][1:] + [v1])[-self._history_len:]
                hist["ch2"] = (hist["ch2"][1:] + [v2])[-self._history_len:]
                hist["ch3"] = (hist["ch3"][1:] + [v3])[-self._history_len:]

                line1, line2, line3 = self._lines[uid]
                line1.set_ydata(hist["ch1"])
                line2.set_ydata(hist["ch2"])
                line3.set_ydata(hist["ch3"])

            # 阈值分析：将 58/59/60 分别视为 VX/VY/VZ
            if len(regs) >= 3 and uid in self._th_analyzers:
                vx, vy, vz = float(regs[0]), float(regs[1]), float(regs[2])
                th_res = self._th_analyzers[uid].update({"VX": vx, "VY": vy, "VZ": vz})
                # 更新该从站当前最高阈值等级显示
                if uid in self._level_vars:
                    self._level_vars[uid].set(str(th_res.max_level))

        # 统一调整每个子图的 Y 轴范围，并重绘
        all_vals = []
        for uid in self._history:
            hist = self._history[uid]
            all_vals.extend(hist["ch1"] + hist["ch2"] + hist["ch3"])
        if all_vals:
            ymin = min(all_vals)
            ymax = max(all_vals)
            if ymin == ymax:
                ymin -= 1
                ymax += 1
            for ax in self._axes.values():
                ax.set_ylim(ymin, ymax)
        if hasattr(self, "_canvas"):
            self._canvas.draw_idle()

    def _on_error(self, msg: str) -> None:
        self.var_status.set(f"错误: {msg}")
        messagebox.showerror("采集错误", msg)

    def _update_threshold_countdown(self, delta_sec: float) -> None:
        """在主线程中更新阈值刷新倒计时，并在到期时重置分析器。"""
        # 减少倒计时时间
        self._th_countdown_sec -= delta_sec
        if self._th_countdown_sec <= 0:
            # 到达周期：重置倒计时
            self._th_countdown_sec = float(self._th_period_sec)
            # 重新创建所有从站的阈值分析器，相当于每10秒重新开始分析窗口
            self._th_analyzers = {
                uid: MultiChannelThresholdAnalyzer(self._th_cfg, ["VX", "VY", "VZ"]) for uid in self._unit_ids
            }
        # 更新UI上的倒计时显示（向下取整显示）
        remain = int(self._th_countdown_sec)
        if hasattr(self, "var_countdown"):
            self.var_countdown.set(f"阈值等级刷新倒计时: {remain} 秒")


def main() -> None:
    app = VibrationMonitorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
