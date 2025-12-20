# 工业网关 RTU 项目 (Gateway RTU)

本项目是一个运行在 Ubuntu 工业网关上的 Python 应用程序，用于采集振动传感器数据、评估故障等级，并通过 Modbus RTU/TCP 控制 PLC 或报警设备。

## 项目结构

*   **`deploy/`**: 部署脚本和配置文件（Systemd 服务、安装脚本、诊断工具）。
*   **`src/`**: 源代码目录。
    *   **`core/`**: 核心业务逻辑（无 UI 依赖），包括 Modbus 通讯、报警引擎、阈值判断。
    *   **`services/`**: 后台服务（Systemd 运行），包括采集服务 (`sensor_service`) 和报警服务 (`alarm_service`)。
    *   **`gateway/`**: UI 相关代码（Tkinter），用于本地调试或带屏幕的工控机。
    *   **`RTU/`**: 预留目录。

## 快速开始

请参考 `deploy/README.md` 获取详细的部署和运行指南。

### 常用命令

*   **部署**: `sudo ./deploy/install.sh`
*   **查看日志**: `./deploy/watch_logs.sh`
*   **重启服务**: `systemctl restart sensor.service alarm.service`

