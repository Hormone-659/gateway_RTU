# 部署指南

本目录包含将工业网关服务部署到 Ubuntu 系统的脚本和配置文件。

## 文件清单与作用

*   **`install.sh`**：**[核心]** 自动化安装脚本。在网关上执行此文件即可完成所有部署工作。
*   **`sensor.service`**：Systemd 服务配置文件，用于管理采集服务（`sensor_service.py`）的后台运行与开机自启。
*   **`alarm.service`**：Systemd 服务配置文件，用于管理报警服务（`alarm_service.py`）的后台运行与开机自启。
*   **`deploy_remote.ps1`**：Windows 端辅助脚本，用于通过 SSH 将文件上传到网关。
*   **`watch_logs.sh`**：实时监视两个服务的日志输出。
*   **`debug_run.sh`**：停止后台服务并手动运行采集程序，用于调试。
*   **`diagnose_serial.py`**：串口扫描与诊断工具。
*   **`diagnose_address.py`**：Modbus 地址扫描工具。
*   **`enable_autostart.sh`**：强制开启并验证开机自启。

## 前置条件

1.  **Ubuntu 系统**：目标机器需运行 Ubuntu。
2.  **Python 3.8**：必须已安装 Python 3.8。
    *   **当前配置路径**：`/root/venv38/bin/python` (虚拟环境)。
    *   如果实际路径不同，请修改 `install.sh` 和 `.service` 文件中的路径。
3.  **硬件连接**：
    *   Modbus RTU 传感器连接到串口（默认 `/dev/ttyS0`）。
    *   如果使用 USB 转串口，可能需要修改 `src/services/sensor_service.py` 中的端口号为 `/dev/ttyUSB0` 等。

## 部署步骤

1.  将整个项目文件夹复制到目标机器。
2.  进入 `deploy` 目录：
    ```bash
    cd deploy
    ```
3.  赋予安装脚本执行权限：
    ```bash
    chmod +x install.sh
    ```
4.  运行安装脚本（需要 root 权限）：
    ```bash
    sudo ./install.sh
    ```

## 远程部署（通过网线/网络）

如果你在 Windows 开发机上，可以使用 `deploy_remote.ps1` 脚本一键上传文件。

### 1. 网络准备
1.  用网线将开发机与工业网关连接（或连接到同一交换机）。
2.  确保开发机 IP 与网关在同一网段。
    *   例如：网关 IP 为 `192.168.1.100`，则将电脑 IP 设为 `192.168.1.101`。
3.  确认能 Ping 通网关：`ping 192.168.1.100`。

### 2. 上传文件
1.  编辑 `deploy/deploy_remote.ps1`，修改 `$GatewayIP` 和 `$GatewayUser` 为实际值。
2.  在 PowerShell 中运行该脚本：
    ```powershell
    cd deploy
    .\deploy_remote.ps1
    ```
3.  根据提示输入网关密码。

### 3. 执行安装
脚本执行成功后，会提示后续步骤：
1.  SSH 登录网关：`ssh root@192.168.1.100`
2.  进入上传目录：`cd /tmp/gateway_deploy/deploy`
3.  执行安装：`sudo ./install.sh`

---

## 离线部署（网关无外网）

如果网关无法连接互联网，`install.sh` 中的 `pip install` 步骤会失败。请按以下步骤进行离线安装：

### 1. 在开发机下载依赖
在有网络的 Windows 电脑上，运行 `deploy/download_deps.ps1`：
```powershell
cd deploy
.\download_deps.ps1
```
这将在 `deploy/deps` 目录下下载所需的 `.whl` 文件。

### 2. 上传依赖包
将生成的 `deps` 文件夹上传到网关的 `/opt/gateway_rtu/` 目录。
可以使用 `scp` 或 `deploy_remote.ps1`（需稍作修改以包含 deps 目录）。

### 3. 在网关离线安装
SSH 登录网关，执行：
```bash
cd /opt/gateway_rtu
/root/venv38/bin/python -m pip install --no-index --find-links=./deploy/deps -r requirements.txt
```

### 4. 重启服务
依赖安装完成后，重启服务：
```bash
systemctl restart sensor.service
systemctl restart alarm.service
```

## 服务管理

部署完成后，服务将自动启动并开机自启。

*   **查看状态**：
    ```bash
    systemctl status sensor.service
    systemctl status alarm.service
    ```
*   **查看日志**：
    ```bash
    journalctl -u sensor.service -f
    journalctl -u alarm.service -f
    ```
*   **停止服务**：
    ```bash
    systemctl stop sensor.service
    systemctl stop alarm.service
    ```
*   **重启服务**：
    ```bash
    systemctl restart sensor.service
    systemctl restart alarm.service
    ```

## 配置文件修改

如果需要修改串口号或 Modbus 配置：
1.  修改 `/opt/gateway_RTU/src/services/sensor_service.py`。
2.  重启服务：`systemctl restart sensor.service`。

## 故障排查

### 1. 服务启动失败 (code=exited, status=1/FAILURE)
*   **检查日志**：`journalctl -u sensor.service -n 50`
*   **常见原因**：
    *   Python 路径错误：检查 `install.sh` 和 `.service` 文件中的路径。
    *   依赖缺失：运行 `pip install -r requirements.txt`。
    *   代码错误：查看日志中的 Traceback。

### 2. Modbus 通信错误 (Modbus RTU read_holding_registers failed)
*   **现象**：服务运行正常（Active: active），但日志中不断刷出此错误。
*   **原因**：代码无法从串口读取数据。
*   **检查步骤**：
    1.  **串口号**：默认使用 `/dev/ttyS0`。如果是 USB 转串口，通常是 `/dev/ttyUSB0`。
        *   查看可用串口：`ls /dev/tty*` 或 `dmesg | grep tty`。
        *   修改端口：编辑 `src/services/sensor_service.py`，找到 `port="/dev/ttyS0"` 并修改。
    2.  **权限**：确保运行服务的用户（root）有权访问串口。
    3.  **接线**：检查 RS485 A/B 线是否接反，传感器是否供电。
    4.  **波特率/地址**：默认 9600, 8N1，从站地址 1/2/3/4。

### 3. 报警服务无反应
*   **检查**：`sensor.service` 是否正常生成状态文件 `/tmp/sensor_fault_state.json`。
*   **日志**：`journalctl -u alarm.service -f`。
