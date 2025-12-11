import struct
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Dict, Tuple

# 为 matplotlib 中文显示做全局字体设置
try:
    import matplotlib

    matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
    matplotlib.rcParams["axes.unicode_minus"] = False
except Exception:
    matplotlib = None

try:
    import serial
except ImportError:
    serial = None

from threshold_analyzer import ThresholdConfig, MultiChannelThresholdAnalyzer
from fault_state_bridge import update_vibration_levels


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

    @staticmethod
    def _crc16(data: bytes) -> int:
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
        with self._lock:
            if not self._ser.is_open:
                self._ser.open()

            crc = self._crc16(bytes([self.unit_id]) + pdu)
            frame = bytes([self.unit_id]) + pdu + struct.pack('<H', crc)
            self._ser.reset_input_buffer()
            self._ser.write(frame)
            self._ser.flush()

            header = self._ser.read(3)
            if len(header) < 3:
                raise TimeoutError("Modbus 响应超时或长度不足")

            addr, func, byte_count = header
            if addr != self.unit_id:
                raise IOError(f"从站地址不匹配，期望 {self.unit_id}，实际 {addr}")
            if func & 0x80:
                exc_code = self._ser.read(1)
                raise IOError(f"Modbus 异常响应，功能码: {func:#x}, 异常码: {exc_code.hex() if exc_code else '??'}")

            data_and_crc = self._ser.read(byte_count + 2)
            if len(data_and_crc) < byte_count + 2:
                raise TimeoutError("Modbus 响应数据长度不足")

            data = data_and_crc[:-2]
            crc_recv = struct.unpack('<H', data_and_crc[-2:])[0]
            crc_calc = self._crc16(header + data)
            if crc_calc != crc_recv:
                raise IOError("Modbus CRC 校验失败")

            return bytes([func, byte_count]) + data

    def read_holding_registers(self, address: int, count: int) -> List[int]:
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


class VibrationMonitor2App(tk.Tk):
    """第二种振动传感器：四个位置、三轴速度 + 三轴加速度的实时监视 UI。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("第二种振动传感器实时监视 (三轴速度+加速度)")
        self.geometry("1600x1000")

        # Python 3.8 下不使用 X | Y 的联合类型写法，直接标注为具体类型并允许 None
        self._client: ModbusRtuClient = None
        self._polling = False
        self._poll_thread: threading.Thread = None

        # 四个位置，对应从站 1~4，编号含义与原来一致
        self._unit_ids = [1, 2, 3, 4]
        self._unit_labels = {
            1: "曲柄销子左",
            2: "曲柄销子右",
            3: "尾轴承",
            4: "中轴承",
        }
        self._unit_enabled_vars = {uid: tk.BooleanVar(value=True) for uid in self._unit_ids}

        # 速度和加速度通道：每个从站共 6 通道（Vx,Vy,Vz,Ax,Ay,Az）
        self._history_len = 200
        self._history_x = list(range(self._history_len))
        # 结构：{unit_id: {"Vx": [...], "Vy": [...], "Vz": [...], "Ax": [...], "Ay": [...], "Az": [...]}}
        self._history: Dict[int, Dict[str, List[float]]] = {
            uid: {ch: [0.0] * self._history_len for ch in ["Vx", "Vy", "Vz", "Ax", "Ay", "Az"]}
            for uid in self._unit_ids
        }

        # 阈值分析配置：这里只根据速度三轴判断等级
        # 按你的需求，将三级阈值设置为：1000 / 2000 / 3000
        self._th_cfg = ThresholdConfig(level1=1000.0, level2=2000.0, level3=3000.0)
        self._th_analyzers = {
            uid: MultiChannelThresholdAnalyzer(self._th_cfg, ["Vx", "Vy", "Vz"])
            for uid in self._unit_ids
        }
        self._th_period_sec = 10
        self._th_countdown_sec = float(self._th_period_sec)

        # 速度与加速度的寄存器地址映射（工程地址，十进制）
        # 根据你给的说明：1,2,3 -> Vx,Vy,Vz；19,14,9 -> Ax,Ay,Az
        # 这里假设这些地址属于同一个从站设备寄存器空间
        self._addr_speed = {"Vx": 1, "Vy": 2, "Vz": 3}
        self._addr_acc = {"Ax": 19, "Ay": 14, "Az": 9}

        self._build_ui()

    # ---------------- UI -----------------
    def _build_ui(self) -> None:
        frm_conn = ttk.LabelFrame(self, text="串口参数")
        frm_conn.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(frm_conn, text="串口号：").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.var_port = tk.StringVar(value="COM3")
        port_values = [f"COM{i}" for i in range(1, 11)]
        self.combo_port = ttk.Combobox(frm_conn, textvariable=self.var_port, values=port_values,
                                       width=8, state="readonly")
        self.combo_port.grid(row=0, column=1, sticky=tk.W, padx=5)
        self.combo_port.current(2)

        ttk.Label(frm_conn, text="采样周期 (ms)：").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        self.var_interval = tk.IntVar(value=200)
        ttk.Entry(frm_conn, textvariable=self.var_interval, width=8).grid(row=0, column=3, sticky=tk.W, padx=5)

        ttk.Button(frm_conn, text="连接并开始采集", command=self.start_polling).grid(row=0, column=4, padx=10)
        ttk.Button(frm_conn, text="停止采集", command=self.stop_polling).grid(row=0, column=5, padx=5)

        # 从站启用
        frm_units = ttk.LabelFrame(self, text="启用的从站")
        frm_units.pack(fill=tk.X, padx=10, pady=5)
        col = 0
        for uid in self._unit_ids:
            desc = self._unit_labels.get(uid, "")
            text = f"从站 {uid}" if not desc else f"从站 {uid}（{desc}）"
            ttk.Checkbutton(frm_units, text=text, variable=self._unit_enabled_vars[uid],
                            onvalue=True, offvalue=False).grid(row=0, column=col, padx=5, pady=2, sticky=tk.W)
            col += 1

        ttk.Label(frm_conn, text="速度寄存器: 1/2/3 -> Vx/Vy/Vz, 加速度寄存器: 19/14/9 -> Ax/Ay/Az").grid(
            row=1, column=0, columnspan=6, sticky=tk.W, padx=5
        )

        # 实时数值表格（速度+加速度+当前阈值等级）
        frm_data = ttk.LabelFrame(self, text="各从站实时数据（速度+加速度）")
        frm_data.pack(fill=tk.X, padx=10, pady=5)

        headers = ["从站ID", "位置", "通道", "寄存器", "十六进制", "十进制", "当前阈值等级"]
        for j, h in enumerate(headers):
            ttk.Label(frm_data, text=h).grid(row=0, column=j, padx=5, pady=3)

        # 为每个从站准备显示变量
        # 结构：{unit_id: {channel: (hex_var, dec_var)}}
        self._value_vars: Dict[int, Dict[str, Tuple[tk.StringVar, tk.StringVar]]] = {}
        self._level_vars: Dict[int, tk.StringVar] = {}

        row = 1
        channels = [
            ("Vx", "速度 X (mm/s)", self._addr_speed["Vx"]),
            ("Vy", "速度 Y (mm/s)", self._addr_speed["Vy"]),
            ("Vz", "速度 Z (mm/s)", self._addr_speed["Vz"]),
            ("Ax", "加速度 X (m/s^2)", self._addr_acc["Ax"]),
            ("Ay", "加速度 Y (m/s^2)", self._addr_acc["Ay"]),
            ("Az", "加速度 Z (m/s^2)", self._addr_acc["Az"]),
        ]

        for uid in self._unit_ids:
            self._value_vars[uid] = {}
            level_var = tk.StringVar(value="0")
            self._level_vars[uid] = level_var
            for idx, (ch_key, ch_name, addr) in enumerate(channels):
                ttk.Label(frm_data, text=str(uid)).grid(row=row, column=0, padx=5, sticky=tk.W)
                ttk.Label(frm_data, text=self._unit_labels.get(uid, "")).grid(row=row, column=1, padx=5, sticky=tk.W)
                ttk.Label(frm_data, text=ch_name).grid(row=row, column=2, padx=5, sticky=tk.W)
                ttk.Label(frm_data, text=f"{addr} (0x{addr:02X})").grid(row=row, column=3, padx=5, sticky=tk.W)

                hex_var = tk.StringVar(value="-")
                dec_var = tk.StringVar(value="-")
                ttk.Label(frm_data, textvariable=hex_var, width=10).grid(row=row, column=4, padx=5)
                ttk.Label(frm_data, textvariable=dec_var, width=10).grid(row=row, column=5, padx=5)
                self._value_vars[uid][ch_key] = (hex_var, dec_var)

                # 在该从站最后一行显示阈值等级
                if idx == len(channels) - 1:
                    ttk.Label(frm_data, textvariable=level_var, width=8).grid(row=row, column=6, padx=5)
                row += 1

        # 波形图区域：每个从站一行，分两列：速度三轴、加速度三轴
        frm_plot = ttk.LabelFrame(self, text="波形图（速度 & 加速度）")
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

        # 4 个从站 × 2 图（速度/加速度）= 8 个子图
        self._fig = Figure(figsize=(10, 8), dpi=100)
        self._fig.subplots_adjust(hspace=0.6, wspace=0.4)

        self._axes_speed: Dict[int, object] = {}
        self._axes_acc: Dict[int, object] = {}
        self._lines_speed: Dict[int, Tuple[object, object, object]] = {}
        self._lines_acc: Dict[int, Tuple[object, object, object]] = {}

        # 子图排列：4 行 2 列
        subplot_index = 1
        for uid in self._unit_ids:
            # 速度
            ax_v = self._fig.add_subplot(4, 2, subplot_index)
            subplot_index += 1
            desc = self._unit_labels.get(uid, "")
            title_v = f"从站 {uid} 速度 Vx/Vy/Vz" + (f"（{desc}）" if desc else "")
            ax_v.set_title(title_v)
            ax_v.set_xlabel("样本点")
            ax_v.set_ylabel("速度 (mm/s)")
            hist = self._history[uid]
            (line_vx,) = ax_v.plot(self._history_x, hist["Vx"], label="Vx")
            (line_vy,) = ax_v.plot(self._history_x, hist["Vy"], label="Vy")
            (line_vz,) = ax_v.plot(self._history_x, hist["Vz"], label="Vz")
            ax_v.legend(loc="upper right", fontsize=8)
            self._axes_speed[uid] = ax_v
            self._lines_speed[uid] = (line_vx, line_vy, line_vz)

            # 加速度
            ax_a = self._fig.add_subplot(4, 2, subplot_index)
            subplot_index += 1
            title_a = f"从站 {uid} 加速度 Ax/Ay/Az" + (f"（{desc}）" if desc else "")
            ax_a.set_title(title_a)
            ax_a.set_xlabel("样本点")
            ax_a.set_ylabel("加速度 (m/s^2)")
            (line_ax,) = ax_a.plot(self._history_x, hist["Ax"], label="Ax")
            (line_ay,) = ax_a.plot(self._history_x, hist["Ay"], label="Ay")
            (line_az,) = ax_a.plot(self._history_x, hist["Az"], label="Az")
            ax_a.legend(loc="upper right", fontsize=8)
            self._axes_acc[uid] = ax_a
            self._lines_acc[uid] = (line_ax, line_ay, line_az)

        self._canvas = FigureCanvasTkAgg(self._fig, master=frm_plot)
        self._canvas.draw()
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        frm_log = ttk.LabelFrame(self, text="状态")
        frm_log.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.var_status = tk.StringVar(value="未连接")
        ttk.Label(frm_log, textvariable=self.var_status).pack(anchor=tk.W, padx=5, pady=3)

        self.var_countdown = tk.StringVar(value=f"阈值等级刷新倒计时: {int(self._th_countdown_sec)} 秒")
        ttk.Label(frm_log, textvariable=self.var_countdown).pack(anchor=tk.W, padx=5, pady=3)

    # ---------------- 采集控制 -----------------
    def start_polling(self) -> None:
        if self._polling:
            return
        port = self.var_port.get().strip()
        if not port:
            messagebox.showerror("错误", "请选择串口号，例如 COM3")
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
        except Exception as e:
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
                result: Dict[int, Dict[str, int]] = {}
                for uid in self._unit_ids:
                    if not self._unit_enabled_vars[uid].get():
                        continue
                    self._client.unit_id = uid
                    # 这里速度和加速度的地址不连续，因此分别读取
                    regs_unit: Dict[str, int] = {}
                    # 速度三轴
                    for ch, addr in self._addr_speed.items():
                        val = self._client.read_holding_registers(address=addr, count=1)[0]
                        regs_unit[ch] = val
                    # 加速度三轴
                    for ch, addr in self._addr_acc.items():
                        val = self._client.read_holding_registers(address=addr, count=1)[0]
                        regs_unit[ch] = val
                    result[uid] = regs_unit
                if result:
                    self.after(0, self._update_all, result)
                    interval_ms = max(self.var_interval.get(), 50)
                    delta_sec = interval_ms / 1000.0
                    self.after(0, self._update_threshold_countdown, delta_sec)
            except Exception as e:
                self.after(0, self._on_error, str(e))
                break

            interval_ms = max(self.var_interval.get(), 50)
            elapsed = (time.time() - t0) * 1000
            sleep_ms = max(interval_ms - elapsed, 10)
            time.sleep(sleep_ms / 1000.0)

        self.after(0, self.stop_polling)

    def _update_all(self, data: Dict[int, Dict[str, int]]) -> None:
        # 更新数值、历史和波形
        for uid, ch_vals in data.items():
            # 数值表
            if uid in self._value_vars:
                for ch, val in ch_vals.items():
                    if ch in self._value_vars[uid]:
                        hex_var, dec_var = self._value_vars[uid][ch]
                        hex_var.set(f"0x{val:04X}")
                        dec_var.set(str(val))

            # 历史和波形
            hist = self._history.get(uid)
            if hist is not None:
                for ch in ["Vx", "Vy", "Vz", "Ax", "Ay", "Az"]:
                    v = float(ch_vals.get(ch, 0))
                    hist[ch] = (hist[ch][1:] + [v])[-self._history_len:]

                if uid in self._lines_speed:
                    line_vx, line_vy, line_vz = self._lines_speed[uid]
                    line_vx.set_ydata(hist["Vx"])
                    line_vy.set_ydata(hist["Vy"])
                    line_vz.set_ydata(hist["Vz"])
                if uid in self._lines_acc:
                    line_ax, line_ay, line_az = self._lines_acc[uid]
                    line_ax.set_ydata(hist["Ax"])
                    line_ay.set_ydata(hist["Ay"])
                    line_az.set_ydata(hist["Az"])

            # 阈值分析：这里只根据速度三轴 Vx/Vy/Vz 判断等级
            if uid in self._th_analyzers:
                vx = float(ch_vals.get("Vx", 0))
                vy = float(ch_vals.get("Vy", 0))
                vz = float(ch_vals.get("Vz", 0))
                # 每次调用 update 都基于当前数据重新评估等级，
                # 当数据恢复到较小值时，MultiChannelThresholdAnalyzer 会返回较低等级。
                th_res = self._th_analyzers[uid].update({"Vx": vx, "Vy": vy, "Vz": vz})
                if uid in self._level_vars:
                    self._level_vars[uid].set(str(th_res.max_level))

        # 统一计算完所有从站的等级后，将对应部位的故障等级写入共享模块
        # 从站ID -> 物理部位的映射：
        #   1: 曲柄销子左 (vib_crank_left)
        #   2: 曲柄销子右 (vib_crank_right)
        #   3: 尾轴承       (vib_tail_bearing)
        #   4: 中轴承       (vib_mid_bearing)
        vib_level_map: Dict[str, int] = {}
        for uid in self._unit_ids:
            if uid not in self._th_analyzers:
                continue
            analyzer = self._th_analyzers[uid]
            # MultiChannelThresholdAnalyzer 通常会把最近一次 update 的结果缓存，
            # 这里简化起见，直接用界面上显示的 level_var 值作为当前等级来源。
            lvl_str = self._level_vars.get(uid).get() if uid in self._level_vars else "0"
            try:
                lvl = int(lvl_str)
            except ValueError:
                lvl = 0

            if uid == 1:
                vib_level_map["vib_crank_left"] = lvl
            elif uid == 2:
                vib_level_map["vib_crank_right"] = lvl
            elif uid == 3:
                vib_level_map["vib_tail_bearing"] = lvl
            elif uid == 4:
                vib_level_map["vib_mid_bearing"] = lvl

        if vib_level_map:
            update_vibration_levels(vib_level_map)

        # 自适应 Y 轴范围
        all_speed = []
        all_acc = []
        for uid in self._history:
            h = self._history[uid]
            all_speed.extend(h["Vx"] + h["Vy"] + h["Vz"])
            all_acc.extend(h["Ax"] + h["Ay"] + h["Az"])
        if all_speed:
            ymin = min(all_speed)
            ymax = max(all_speed)
            if ymin == ymax:
                ymin -= 1
                ymax += 1
            for ax in self._axes_speed.values():
                ax.set_ylim(ymin, ymax)
        if all_acc:
            ymin = min(all_acc)
            ymax = max(all_acc)
            if ymin == ymax:
                ymin -= 1
                ymax += 1
            for ax in self._axes_acc.values():
                ax.set_ylim(ymin, ymax)

        if hasattr(self, "_canvas"):
            self._canvas.draw_idle()

    def _on_error(self, msg: str) -> None:
        self.var_status.set(f"错误: {msg}")
        messagebox.showerror("采集错误", msg)

    def _update_threshold_countdown(self, delta_sec: float) -> None:
        """每隔固定时间重置阈值分析窗口，但不锁死等级。

        这里保留原有的 10 秒周期重置：
        - 在 10 秒内，等级会随着数据变化即时更新（包括从高恢复为低时会降级）；
        - 每 10 秒重新创建一批分析器，相当于“丢弃旧历史，只看最近一段时间的数据”。
        """
        self._th_countdown_sec -= delta_sec
        if self._th_countdown_sec <= 0:
            self._th_countdown_sec = float(self._th_period_sec)
            self._th_analyzers = {
                uid: MultiChannelThresholdAnalyzer(self._th_cfg, ["Vx", "Vy", "Vz"])
                for uid in self._unit_ids
            }
        remain = int(self._th_countdown_sec)
        if hasattr(self, "var_countdown"):
            self.var_countdown.set(f"阈值等级刷新倒计时: {remain} 秒")


def main() -> None:
    app = VibrationMonitor2App()
    app.mainloop()


if __name__ == "__main__":
    main()
