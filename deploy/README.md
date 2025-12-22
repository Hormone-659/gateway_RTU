# 部署工具包 (Deploy)

本目录包含用于部署、管理和诊断网关服务的脚本。

## 核心脚本

*   **`install.sh`**: **一键部署脚本**。负责安装依赖、配置 Systemd 服务、设置权限。
*   **`watch_logs.sh`**: **日志监视器**。同时查看 sensor 和 alarm 服务的实时日志。
*   **`enable_autostart.sh`**: **自启配置**。强制开启服务开机自启并进行验证。
*   **`debug_run.sh`**: **手动调试**。停止后台服务并手动运行采集程序，用于排查启动失败问题。

## 诊断工具 (Python)

*   **`diagnose_serial.py`**: 扫描系统串口，自动探测连接的 Modbus 设备参数（波特率、站号）。
*   **`diagnose_address.py`**: 扫描指定串口的 Modbus 寄存器地址，用于确认数据位置。
*   **`diagnose_photo.py`**: 专门用于扫描光电传感器的寄存器数值。
*   **`monitor_rtu.py`**: 实时查看 RTU 寄存器（无 pymodbus，基础实现）。
    - TCP：
      ```bash
      /root/venv38/bin/python monitor_rtu.py --mode tcp --host 192.168.0.200 --unit 1 --ranges "40101-40108,40501-40521" --rate 1
      ```
    - 串口 RTU（需安装 pyserial）：
      ```bash
      /root/venv38/bin/python monitor_rtu.py --mode rtu --serial /dev/ttyS2 --baud 9600 --parity N --stopbits 1 --unit 1 --ranges "40101-40108,40501-40521" --rate 1
      ```

## 服务配置

*   **`sensor.service`**: 采集服务的 Systemd 单元文件。
*   **`alarm.service`**: 报警服务的 Systemd 单元文件。
