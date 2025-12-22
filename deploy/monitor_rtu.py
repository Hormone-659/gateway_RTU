import time
import struct
import socket
from typing import Optional, List, Tuple

# -----------------------------------------------------------------------------
# 配置部分（可被命令行参数覆盖）
# -----------------------------------------------------------------------------
RTU_HOST = "192.168.0.200"  # RTU IP 地址（TCP 模式）
RTU_PORT = 502               # Modbus TCP 端口
UNIT_ID = 1                  # 从站地址

# 要监控的寄存器范围 (工程地址，1-based)
RANGES: List[Tuple[int, int]] = [
    # 默认改为监控报警服务写入的寄存器区：3501-3520 对应 PLC 工程 43501-43520
    (43501, 43520),
]

REFRESH_RATE = 1.0  # 刷新频率 (秒)

# 串口默认参数（RTU 模式）
SERIAL_PORT = "/dev/ttyS2"
SERIAL_BAUDRATE = 9600
SERIAL_PARITY = "N"  # N/E/O
SERIAL_STOPBITS = 1
SERIAL_TIMEOUT = 0.5

# -----------------------------------------------------------------------------
# 工具函数：工程地址 -> 0-based PDU 地址
# -----------------------------------------------------------------------------

def eng_to_pdu(start_eng: int) -> int:
    """将 4xxxx 工程地址转换为 0-based PDU 地址。

    规则：40001 -> 0, 40101 -> 100, 40501 -> 500。
    实现方式：取后四位减一：pdu = (eng % 10000) - 1
    """
    return (start_eng % 10000) - 1


# -----------------------------------------------------------------------------
# Modbus TCP 简易客户端（不依赖 pymodbus）
# -----------------------------------------------------------------------------
class SimpleModbusTcpClient:
    def __init__(self, host: str, port: int, unit_id: int = 1):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.sock: Optional[socket.socket] = None
        self.transaction_id = 0

    def connect(self):
        if self.sock:
            return
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(3.0)
            self.sock.connect((self.host, self.port))
            print(f"已连接到 {self.host}:{self.port}")
        except Exception as e:
            print(f"连接失败: {e}")
            self.sock = None

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def read_holding_registers(self, start_addr: int, count: int):
        """
        读取保持寄存器 (Function Code 03)
        start_addr: 0-based PDU address (e.g. 40101 -> 100)
        """
        if not self.sock:
            self.connect()
            if not self.sock:
                return None

        self.transaction_id = (self.transaction_id + 1) & 0xFFFF

        # MBAP Header: TID(2) + PID(2) + LEN(2) + UID(1)
        # PDU: FC(1) + StartAddr(2) + Count(2)
        pdu = struct.pack('>BHH', 0x03, start_addr, count)
        mbap = struct.pack('>HHHB', self.transaction_id, 0, len(pdu) + 1, self.unit_id)
        req = mbap + pdu

        try:
            self.sock.sendall(req)

            # 接收响应头 (7 bytes)
            header = self.sock.recv(7)
            if len(header) < 7:
                raise RuntimeError("响应头不完整")

            _, _, length, _ = struct.unpack('>HHHB', header)
            # length = UnitID(1) + PDU
            remaining = length - 1
            data = b''
            while len(data) < remaining:
                chunk = self.sock.recv(remaining - len(data))
                if not chunk:
                    break
                data += chunk

            if len(data) < remaining:
                raise RuntimeError("响应数据不完整")

            # PDU format for Read Holding Registers Response:
            # FC(1) + ByteCount(1) + Data(2 * Count)
            fc = data[0]
            if fc != 0x03:
                if fc == 0x83 and len(data) >= 2:
                    print(f"Modbus 异常: Code {data[1]}")
                else:
                    print(f"未知功能码响应: {fc}")
                return None

            if len(data) < 2:
                print("响应数据过短")
                return None

            byte_count = data[1]
            if len(data) < 2 + byte_count:
                print("数据区长度与声明不符")
                return None

            values = []
            for i in range(0, byte_count, 2):
                # 防御切片越界
                if 2 + i + 2 <= len(data):
                    val = struct.unpack('>H', data[2 + i: 4 + i])[0]
                    values.append(val)
            return values

        except Exception as e:
            print(f"读取错误: {e}")
            self.close()
            return None


# -----------------------------------------------------------------------------
# Modbus RTU 简易客户端（串口，使用 pyserial；不依赖 pymodbus）
# -----------------------------------------------------------------------------
class SimpleModbusRtuClient:
    def __init__(self, port: str, baudrate: int = 9600, parity: str = "N", stopbits: int = 1, timeout: float = 0.5, unit_id: int = 1):
        self.port = port
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout
        self.unit_id = unit_id
        self.ser = None

    @staticmethod
    def _crc16(data: bytes) -> bytes:
        crc = 0xFFFF
        for ch in data:
            crc ^= ch
            for _ in range(8):
                if crc & 0x0001:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        # 返回小端序（低字节在前）
        return struct.pack('<H', crc)

    def connect(self):
        if self.ser:
            return
        try:
            import serial
            import serial.rs485  # noqa: F401
        except Exception as e:
            print(f"缺少 pyserial 库，无法使用 RTU 串口模式: {e}")
            self.ser = None
            return
        try:
            ser = serial.Serial(self.port, self.baudrate, bytesize=8, parity=self.parity, stopbits=self.stopbits, timeout=self.timeout)
            # 可选：启用 RS485 自动方向
            try:
                ser.rs485_mode = serial.rs485.RS485Settings()
            except Exception:
                pass
            self.ser = ser
            print(f"已打开串口 {self.port} ({self.baudrate},{self.parity},{self.stopbits})")
        except Exception as e:
            print(f"无法打开串口: {e}")
            self.ser = None

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def read_holding_registers(self, start_addr: int, count: int):
        if not self.ser:
            self.connect()
            if not self.ser:
                return None

        # RTU 请求帧: [ addr(1), fc(1)=0x03, start(2), count(2), crc(2) ]
        pdu = struct.pack('>BHH', 0x03, start_addr, count)
        req = struct.pack('>B', self.unit_id) + pdu
        req += self._crc16(req)

        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self.ser.write(req)

            # 期望响应: [ addr(1), fc(1)=0x03, byte_count(1), data(2*count), crc(2) ]
            # 先读最小头 5 字节(含 addr,fc,byte_count)，再按 byte_count 读剩余
            # 这里直接一次性尝试读足够多的字节
            min_len = 5
            resp = self.ser.read(min_len)
            if len(resp) < min_len:
                print("串口响应过短(头)")
                return None

            # 解析 byte_count
            # resp[0]=addr, resp[1]=fc, resp[2]=byte_count
            byte_count = resp[2]
            remain = byte_count + 2  # data + crc
            rest = self.ser.read(remain)
            if len(rest) < remain:
                print("串口响应过短(数据)")
                return None
            resp_full = resp + rest

            # CRC 校验
            if len(resp_full) < 5:
                print("串口响应异常长度")
                return None
            body, crc_recv = resp_full[:-2], resp_full[-2:]
            crc_calc = self._crc16(body)
            if crc_calc != crc_recv:
                print("CRC 校验失败")
                return None

            if body[1] != 0x03:
                if body[1] == 0x83 and len(body) >= 3:
                    print(f"Modbus 异常: Code {body[2]}")
                else:
                    print(f"功能码异常: {body[1]}")
                return None

            values = []
            data = body[3: 3 + byte_count]
            for i in range(0, byte_count, 2):
                if i + 2 <= len(data):
                    values.append(struct.unpack('>H', data[i:i+2])[0])
            return values
        except Exception as e:
            print(f"串口读取错误: {e}")
            self.close()
            return None


# -----------------------------------------------------------------------------
# 主程序
# -----------------------------------------------------------------------------

def parse_ranges(arg: str) -> List[Tuple[int, int]]:
    """
    解析形如 "40101-40108,40501-40521" 的范围字符串。
    """
    res: List[Tuple[int, int]] = []
    parts = [p.strip() for p in arg.split(',') if p.strip()]
    for p in parts:
        if '-' in p:
            s, e = p.split('-', 1)
            res.append((int(s), int(e)))
        else:
            v = int(p)
            res.append((v, v))
    return res


def main():
    import argparse

    parser = argparse.ArgumentParser(description="实时监控 RTU/PLC 保持寄存器 (无 pymodbus)")
    parser.add_argument('--mode', choices=['tcp', 'rtu'], default='tcp', help='通信模式: tcp 或 rtu (默认 tcp)')
    parser.add_argument('--unit', type=int, default=UNIT_ID, help='从站地址 Unit ID (默认 1)')

    # TCP 相关
    parser.add_argument('--host', default=RTU_HOST, help='TCP 主机地址')
    parser.add_argument('--port', type=int, default=RTU_PORT, help='TCP 端口')

    # RTU 串口相关
    parser.add_argument('--serial', default=SERIAL_PORT, help='串口设备路径, 如 /dev/ttyS2')
    parser.add_argument('--baud', type=int, default=SERIAL_BAUDRATE, help='串口波特率')
    parser.add_argument('--parity', default=SERIAL_PARITY, choices=['N', 'E', 'O'], help='奇偶校验 (N/E/O)')
    parser.add_argument('--stopbits', type=int, default=SERIAL_STOPBITS, choices=[1, 2], help='停止位 (1/2)')

    parser.add_argument('--ranges', default=None, help='自定义监控范围, 例如 "40101-40108,40501-40521"')
    parser.add_argument('--rate', type=float, default=REFRESH_RATE, help='刷新频率(秒)')

    args = parser.parse_args()

    ranges = parse_ranges(args.ranges) if args.ranges else RANGES

    if args.mode == 'tcp':
        client = SimpleModbusTcpClient(args.host, args.port, args.unit)
    else:
        client = SimpleModbusRtuClient(args.serial, args.baud, args.parity, args.stopbits, SERIAL_TIMEOUT, args.unit)

    print("开始监控 RTU 寄存器 ... (Ctrl+C 退出)")
    print(f"模式: {args.mode.upper()} | Unit={args.unit}")
    if args.mode == 'tcp':
        print(f"目标: {args.host}:{args.port}")
    else:
        print(f"串口: {args.serial} @ {args.baud} {args.parity}{args.stopbits}")
    print("-" * 60)

    try:
        while True:
            print(f"\n--- 时间: {time.strftime('%H:%M:%S')} ---")
            for start_eng, end_eng in ranges:
                start_pdu = eng_to_pdu(start_eng)
                count = end_eng - start_eng + 1

                # 分批读取（若后续扩展超过 125，可在此拆分）
                values = client.read_holding_registers(start_pdu, count)

                if values:
                    print(f"[{start_eng}-{end_eng}]:")
                    for i, val in enumerate(values):
                        addr = start_eng + i
                        end_char = '\n' if (i + 1) % 8 == 0 else '\t'
                        print(f"{addr}: {val:<6}", end=end_char)
                    if count % 8 != 0:
                        print()
                else:
                    print(f"[{start_eng}-{end_eng}]: 读取失败")
            time.sleep(args.rate)
    except KeyboardInterrupt:
        print("\n已停止监控。")
    finally:
        # 两种客户端均实现了 close
        try:
            client.close()  # type: ignore[attr-defined]
        except Exception:
            pass


if __name__ == "__main__":
    main()
