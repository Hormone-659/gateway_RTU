# Gateway UI 模块（`src/gateway`）

本目录包含工业网关的图形用户界面（GUI）相关代码，主要基于 Tkinter 实现。这些模块通常运行在带有显示器的工控机或调试用的 PC 上。

## 子目录说明

- `sensor/`
  - **传感器监测**：负责振动传感器的数据采集、波形显示和初步阈值判断。
  - 核心文件：`vibration_monitor_1.py`。

- `alarm/`
  - **报警演示与控制**：负责综合各传感器状态，进行报警逻辑判断，并将结果写入 RTU/PLC。
  - 核心文件：`alarm_play/alarm_rtu_ui.py`。

## 运行方式

通常需要分别启动传感器监测和报警服务：

1.  启动传感器监测：
    ```bash
    python -m gateway.sensor.vibration_monitor_1
    ```
2.  启动报警逻辑演示/控制：
    ```bash
    python -m gateway.alarm.alarm_play.alarm_rtu_ui
    ```

