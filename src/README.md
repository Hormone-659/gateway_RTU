# 源代码目录 (src)

本目录包含项目的所有 Python 源代码。

## 模块说明

*   **`core/`**: 核心领域逻辑库。
    *   设计为纯 Python 逻辑，不依赖 UI 框架。
    *   被 `services/` (后台服务) 和 `gateway/` (UI 应用) 共同引用。
*   **`services/`**: 后台守护进程 (Daemons)。
    *   `sensor_service.py`: 负责 Modbus RTU 采集和阈值判断。
    *   `alarm_service.py`: 负责读取采集结果并执行报警逻辑 (写 RTU)。
*   **`gateway/`**: 图形用户界面 (GUI)。
    *   基于 Tkinter，用于开发调试或现场可视化。
*   **`RTU/`**: 预留。

## 运行环境

*   Python 3.8+
*   依赖库: `pymodbus`, `pyserial` (详见 `../requirements.txt`)

