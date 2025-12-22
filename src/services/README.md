# services 目录说明

`services` 目录存放后台长期运行的服务脚本，这些脚本不依赖 UI，由 Systemd 托管。

## 1. sensor_service.py (采集服务)

负责底层硬件的数据采集和初步处理。

*   **运行频率**: 1 秒/次
*   **硬件接口**:
    *   `/dev/ttyS2` (Modbus RTU): 连接振动传感器和电参模块。
    *   `/dev/ttyS3` (Modbus RTU): 连接光电传感器。
*   **采集内容**:
    *   **振动**: 4 个位置 (Unit 1-4)，读取 X/Y/Z 三轴速度，取最大值。
    *   **电参**: 读取 Unit 1 的寄存器 102-104 (A/B/C 相电流)。
    *   **光电**: 读取 Unit 6 (皮带) 和 Unit 5 (驴头) 的寄存器 0 (距离)。
*   **输出**:
    *   实时更新 `/tmp/sensor_fault_state.json` 文件。
    *   日志输出当前各传感器数值。

## 2. alarm_service.py (报警服务)

负责业务逻辑判断和对外控制。

*   **运行频率**: 1 秒/次
*   **输入**: 读取 `/tmp/sensor_fault_state.json`。
*   **处理逻辑**:
    *   调用 `core.alarm.alarm_engine`。
    *   结合振动等级、电参状态、光电状态，计算综合报警等级 (0-3)。
*   **输出**:
    *   通过 Modbus TCP/RTU 将报警寄存器 (3501-3520) 写入现场 RTU/PLC。
    *   生成报警日志文件 (txt)。

## 3. rtu_comm.py

*   提供报警服务写 RTU 的底层通信抽象。
*   采用**原始 Modbus TCP 帧**实现（MBAP + PDU），当前实现功能码 0x06（写单个保持寄存器），在发送前将工程地址减 1 转成 0 基地址。
