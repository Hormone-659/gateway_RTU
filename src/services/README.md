# services 目录说明

`services` 目录存放后台长期运行的服务脚本，这些脚本不依赖 Tkinter UI，主要用于在工业网关上以 systemd 服务的方式部署。

## 文件说明

- `sensor_service.py`
  - 功能：
    - 通过 Modbus RTU（485 串口）周期性采集振动传感器数据（X/Y/Z 三轴）。
    - 使用 `core/sensor/threshold_engine.py` 对采集到的三轴速度值进行阈值判断，取最大值得到 0/1/2/3 级故障等级。
    - 将“当前振动速度 + 故障等级”写入一个 JSON 状态文件（默认 `/tmp/sensor_fault_state.json`），供其他进程读取。
    - **运行频率**：每 1 秒采集一次。
  - 主要类：
    - `SensorService`：提供 `run_forever()` 主循环，可以直接作为 systemd 服务入口运行。

- `alarm_service.py`
  - 功能：
    - 周期性读取 `sensor_service` 写入的 JSON 状态文件；
    - 利用 `core/alarm/alarm_engine.py` 将各部位故障等级转换为整体报警等级和 RTU 寄存器表；
    - 通过 `services/rtu_comm.py` 抽象出来的接口，将寄存器写入 RTU/PLC，并在日志中记录写入信息。
    - **运行频率**：每 30 秒执行一次评估和写入。
  - 主要类：
    - `AlarmService`：提供 `run_forever()` 主循环，可以作为单独后台进程运行。

- `rtu_comm.py`
  - 功能：
    - 抽象 RTU/PLC 写寄存器操作，屏蔽具体 Modbus TCP/RTU 实现细节。
    - 当前实现为占位版本：
      - `RtuWriter.write_registers(registers, alarm_level)`：打印一条“写 RTU”日志，并在内存中保留最近 3 条记录。
    - 后续可以在这里接入你现有的 RTU 通讯实现（例如 `alarm_rtu_ui.py` 中的 Modbus 写入逻辑），实现真正的 PLC/RTU 写寄存器。

这些服务脚本设计为与 UI 解耦，可以在 Ubuntu 工业网关上使用 systemd 长期运行，实现“无界面自动采集+报警+写 RTU”的完整流程。

