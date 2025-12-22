# 工业网关 RTU 项目 (Gateway RTU)

本项目是一个运行在 Ubuntu 工业网关上的 Python 应用程序，用于全方位采集工业设备数据、评估故障等级，并通过 Modbus RTU/TCP 控制 PLC 或报警设备。

## 主要功能

1.  **振动监测**：
    *   采集 4 个位置（左曲柄、右曲柄、尾轴承、中轴承）的振动数据。
    *   支持 **X/Y/Z 三轴**数据采集（取最大值）。
    *   基于阈值（5/10/20 mm/s）判断 0-3 级故障。
    *   具备滑动窗口去抖动功能（10秒窗口）。
2.  **电参监测**：
    *   采集 A/B/C 三相电流状态。
    *   实时检测缺相故障（电流为 0 视为异常）。
3.  **光电传感器监测**：
    *   采集皮带（Belt）和线路/驴头（Line）的距离/状态数据。
    *   基于阈值（310/320/330 mm）判断故障等级。
4.  **报警联动**：
    *   综合上述所有传感器状态，计算整体报警等级。
    *   将报警状态和故障代码写入现场 RTU/PLC 寄存器。

## 系统架构

*   **采集服务 (`sensor_service`)**：
    *   **频率**：每 1 秒运行一次。
    *   **职责**：通过串口（`/dev/ttyS2`, `/dev/ttyS3`）轮询所有传感器，更新状态文件 `/tmp/sensor_fault_state.json`。
*   **报警服务 (`alarm_service`)**：
    *   **频率**：每 1 秒运行一次。
    *   **职责**：读取状态文件，执行复杂报警逻辑，通过 Modbus 写寄存器。

## 快速开始

请参考 `CHEAT_SHEET.md` 获取常用的网关执行命令。

### 目录结构

*   **`deploy/`**: 部署脚本、诊断工具、Systemd 配置。
*   **`src/`**: 源代码。
    *   **`services/`**: 后台服务入口。
    *   **`core/`**: 核心业务逻辑。

---

## 附：实时查看 RTU 寄存器

已提供纯基础实现的监控脚本（不依赖 pymodbus）：`deploy/monitor_rtu.py`

- TCP 模式（每秒刷新）：
  ```bash
  python deploy/monitor_rtu.py --mode tcp --host 192.168.0.200 --unit 1 --ranges "40101-40108,40501-40521" --rate 1
  ```
- 串口 RTU 模式（需安装 pyserial）：
  ```bash
  python deploy/monitor_rtu.py --mode rtu --serial /dev/ttyS2 --baud 9600 --parity N --stopbits 1 --unit 1 --ranges "40101-40108,40501-40521" --rate 1
  ```
