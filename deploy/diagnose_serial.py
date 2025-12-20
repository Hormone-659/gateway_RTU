import sys
import glob
import time
import struct

try:
    import serial
    import serial.rs485
except ImportError:
    print("错误: 未找到 pyserial 模块。")
    print("请使用虚拟环境运行此脚本，例如: /root/venv38/bin/python diagnose_serial.py")
    sys.exit(1)

def calculate_crc(data):
    """计算 Modbus CRC16"""
    crc = 0xFFFF
    for char in data:
        crc ^= char
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return struct.pack('<H', crc)

def scan_ports():
    """扫描系统中可能的串口"""
    patterns = [
        '/dev/ttyUSB*',
        '/dev/ttyACM*',
        '/dev/ttyS*',
        '/dev/ttymxc*',
        '/dev/ttyAMA*',
        '/dev/ttyO*',
        '/dev/ttyWK*',
        '/dev/ttyAP*',
    ]
    ports = []
    for p in patterns:
        found = glob.glob(p)
        ports.extend(found)
    return sorted(ports)

def test_raw_modbus(port, baudrate=9600, parity='N', slave_id=1, reg_addr=58):
    """使用原生 pyserial 发送 Modbus RTU 请求"""

    try:
        # 映射校验位
        p_val = serial.PARITY_NONE
        if parity == 'E': p_val = serial.PARITY_EVEN
        elif parity == 'O': p_val = serial.PARITY_ODD

        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=8,
            parity=p_val,
            stopbits=1,
            timeout=0.2  # 快速超时
        )

        # 尝试开启 RS485 模式 (针对板载串口)
        if sys.platform.startswith("linux") and ("ttyS" in port or "ttymxc" in port):
            try:
                ser.rs485_mode = serial.rs485.RS485Settings()
            except Exception:
                pass

        # 构建 Modbus RTU 请求帧: 读保持寄存器 (0x03)
        # 格式: [ID] [03] [AddrHi] [AddrLo] [CountHi] [CountLo] [CRCLo] [CRCHi]
        # 读取 1 个寄存器
        req = struct.pack('>BBHH', slave_id, 3, reg_addr, 1)
        req += calculate_crc(req)

        # 清空缓冲区
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        # 发送
        ser.write(req)

        # 读取响应
        # 预期响应: [ID] [03] [Bytes] [DataHi] [DataLo] [CRCLo] [CRCHi] = 7 字节
        resp = ser.read(7)
        ser.close()

        if len(resp) == 0:
            return False

        if len(resp) < 5:
            # print(f"  -> [{port}] 收到不完整数据: {resp.hex()}")
            return False

        # 简单校验 ID 和功能码
        resp_id, resp_func = struct.unpack('>BB', resp[:2])
        if resp_id == slave_id and (resp_func == 3 or resp_func == 0x83):
            if resp_func == 0x83:
                print(f"  ⚠️ [{port}] 收到异常响应 (Exception): {resp.hex()}")
                return True # 虽然是异常，但也说明通了

            # 校验 CRC (可选)
            if len(resp) >= 7:
                val_hi, val_lo = struct.unpack('>BB', resp[3:5])
                val = (val_hi << 8) | val_lo
                print(f"  ✅ [成功] 串口: {port} | 波特率: {baudrate} | ID: {slave_id} | 收到值: {val} (Hex: {resp.hex()})")
                return True
        else:
            # print(f"  -> [{port}] 数据不匹配: {resp.hex()}")
            pass

    except Exception as e:
        # print(f"  -> [{port}] 错误: {e}")
        pass

    return False

if __name__ == "__main__":
    print("=== 串口诊断工具 (原生 pyserial 版) ===")
    print("正在扫描可用串口...")
    ports = scan_ports()

    # 过滤逻辑
    filtered_ports = []
    for p in ports:
        if "ttyS" in p:
            try:
                suffix = p.replace("/dev/ttyS", "")
                if suffix.isdigit() and int(suffix) < 10:
                    filtered_ports.append(p)
            except ValueError:
                pass
        else:
            filtered_ports.append(p)

    print(f"待扫描串口: {filtered_ports}")
    print("-" * 30)

    # 扫描配置
    target_baudrates = [9600, 19200]
    target_parities = ['N', 'E']
    target_unit_ids = [1, 2, 3, 4]
    target_address = 58  # 寄存器地址

    found = False
    for port in filtered_ports:
        print(f"正在扫描串口: {port} ...")
        for baud in target_baudrates:
            for parity in target_parities:
                # print(f"  尝试: {baud} {parity} ...")
                for uid in target_unit_ids:
                    if test_raw_modbus(port, baud, parity, uid, target_address):
                        found = True
                        print(f"\n🎉 找到有效配置！")
                        print(f"   串口: {port}")
                        print(f"   波特率: {baud}")
                        print(f"   校验: {parity}")
                        print(f"   站号: {uid}")
                        break
                if found: break
            if found: break
        if found: break

    if not found:
        print("\n❌ 未检测到任何响应。")
        print("请检查: 1.接线(A/B) 2.供电 3.是否开启了 RS485 模式(如果是板载串口)")

