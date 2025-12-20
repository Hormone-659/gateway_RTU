import sys
import struct
import time

try:
    import serial
    import serial.rs485
except ImportError:
    print("错误: 未找到 pyserial 模块。")
    sys.exit(1)

def calculate_crc(data):
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

def send_modbus_request(ser, slave_id, func_code, start_addr, count):
    """发送 Modbus 请求并返回响应"""
    req = struct.pack('>BBHH', slave_id, func_code, start_addr, count)
    req += calculate_crc(req)

    ser.reset_input_buffer()
    ser.reset_output_buffer()
    ser.write(req)

    # 读取头部
    time.sleep(0.1)
    resp = ser.read(1024) # 读所有缓冲区
    return resp

def test_address(port='/dev/ttyS2'):
    print(f"正在测试串口: {port} (9600, N, 1)...")

    try:
        ser = serial.Serial(port, 9600, bytesize=8, parity='N', stopbits=1, timeout=0.5)
        if sys.platform.startswith("linux"):
            try:
                ser.rs485_mode = serial.rs485.RS485Settings()
            except:
                pass
    except Exception as e:
        print(f"无法打开串口: {e}")
        return

    slave_id = 1

    # 测试方案: 尝试读取地址 0 到 5
    print("\n--- 测试保持寄存器 (Function 03) ---")
    for addr in range(10):
        resp = send_modbus_request(ser, slave_id, 3, addr, 1)
        if len(resp) >= 5:
            resp_id, resp_func = struct.unpack('>BB', resp[:2])
            if resp_func == 3:
                # 成功
                val_hi, val_lo = struct.unpack('>BB', resp[3:5])
                val = (val_hi << 8) | val_lo
                print(f"✅ 地址 {addr}: 读取成功! 值={val}")
            elif resp_func == 0x83:
                # 异常
                err_code = resp[2]
                print(f"❌ 地址 {addr}: 异常 (代码 {err_code:02X}) - 通常是地址无效")
        else:
            print(f"❓ 地址 {addr}: 无响应或数据不完整")

    print("\n--- 测试输入寄存器 (Function 04) ---")
    for addr in range(10):
        resp = send_modbus_request(ser, slave_id, 4, addr, 1)
        if len(resp) >= 5:
            resp_id, resp_func = struct.unpack('>BB', resp[:2])
            if resp_func == 4:
                # 成功
                val_hi, val_lo = struct.unpack('>BB', resp[3:5])
                val = (val_hi << 8) | val_lo
                print(f"✅ 地址 {addr}: 读取成功! 值={val}")
            elif resp_func == 0x84:
                # 异常
                err_code = resp[2]
                print(f"❌ 地址 {addr}: 异常 (代码 {err_code:02X})")
        else:
            # print(f"❓ 地址 {addr}: 无响应")
            pass

    ser.close()

if __name__ == "__main__":
    test_address()

