# 传感器监测模块（`src/gateway/sensor`）

本目录包含振动传感器的采集、监测 UI 以及阈值分析逻辑。

## 文件说明

- `vibration_monitor_1.py`
  - **主监测程序**：提供基于 Tkinter 的图形界面。
  - 功能：
    - 通过串口（Modbus RTU）读取振动传感器数据。
    - 实时显示波形图（使用 Matplotlib）。
    - 显示当前振动速度值。
    - 调用 `threshold_analyzer` 进行阈值判断。
    - 通过 `fault_state_bridge` 将故障等级共享给报警模块。

- `vibration_monitor_ui.py`
  - 振动监测 UI 的另一个版本（或早期版本），功能与 `vibration_monitor_1.py` 类似。

- `threshold_analyzer.py`
  - **阈值分析逻辑**：
    - 定义 `ThresholdConfig` 类，存储各级报警阈值。
    - 实现滑动窗口分析，判断数据是否持续超过阈值。

- `fault_state_bridge.py`
  - **状态共享桥接**：
    - 提供一个简单的内存共享机制（基于文件或全局变量）。
    - 允许 `vibration_monitor` 将计算出的故障等级传递给 `alarm` 模块（如 `alarm_rtu_ui.py`）。
    - 实现了 `update_vibration_levels` 和 `get_latest_levels_for_alarm` 等接口。

## 依赖

- `pyserial`: 用于串口通信。
- `matplotlib`: 用于绘制波形图。
- `tkinter`: 用于图形界面。

