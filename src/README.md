# 源代码目录 (src)

本目录包含项目的所有 Python 源代码。

## 模块说明

*   **`core/`**: 核心领域逻辑库。
    *   设计为纯 Python 逻辑，不依赖 UI 框架。
    *   被 `services/` (后台服务) 和 `gateway/` (UI 应用) 共同引用。
*   **`services/`**: 后台守护进程 (Daemons)。
    *   `sensor_service.py`: 负责 Modbus RTU 采集和阈值判断，周期 1s。
    *   `alarm_service.py`: 负责读取采集结果并执行报警逻辑 (写 RTU)，周期 1s。
*   **`gateway/`**: 图形用户界面 (GUI)。
    *   基于 Tkinter，用于开发调试或现场可视化。
*   **`RTU/`**: 预留。

## 运行环境

*   Python 3.8+
*   依赖库: `pymodbus`, `pyserial` (详见 `../requirements.txt`)

## 附：实时查看 RTU 寄存器

参见 `deploy/monitor_rtu.py`，可通过基础实现（无 pymodbus）读取 40101-40108、40501-40521 等范围：
```bash
python deploy/monitor_rtu.py --mode tcp --host 192.168.0.200 --unit 1 --ranges "40101-40108,40501-40521" --rate 1
```
