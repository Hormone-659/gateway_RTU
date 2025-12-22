# 网关常用命令速查表 (Cheat Sheet)

本文档汇总了在工业网关 SSH 终端中常用的操作命令，方便直接复制粘贴执行。

## 1. 部署与更新

每次修改代码后，都需要运行部署脚本来更新环境和服务。

```bash
cd /opt/gateway_rtu
sudo ./deploy/install.sh
```

## 2. 日志查看 (最常用)

**实时监视所有日志**（采集数据 + 报警写入）：
```bash
cd /opt/gateway_rtu/deploy
./watch_logs.sh
```
*(按 `Ctrl+C` 退出)*

**单独查看采集服务日志** (每秒刷新，显示振动/电参/光电数值)：
```bash
journalctl -u sensor.service -f
```

**单独查看报警服务日志** (每秒刷新，显示 RTU 写入操作)：
```bash
journalctl -u alarm.service -f
```

## 3. 服务管理

**重启所有服务** (配置修改或代码更新后)：
```bash
systemctl restart sensor.service alarm.service
```

**停止服务**：
```bash
systemctl stop sensor.service alarm.service
```

**查看服务运行状态**：
```bash
systemctl status sensor.service alarm.service
```

## 4. 故障诊断工具

**扫描串口设备** (检查传感器是否连接，波特率是否正确)：
```bash
cd /opt/gateway_rtu/deploy
/root/venv38/bin/python diagnose_serial.py
```

**扫描 Modbus 地址** (检查传感器站号)：
```bash
cd /opt/gateway_rtu/deploy
/root/venv38/bin/python diagnose_address.py
```

**扫描光电传感器** (检查光电数值)：
```bash
cd /opt/gateway_rtu/deploy
/root/venv38/bin/python diagnose_photo.py
```

**实时查看 RTU 寄存器** (基础实现，无 pymodbus)：
- TCP 模式：
  ```bash
  cd /opt/gateway_rtu/deploy
  /root/venv38/bin/python monitor_rtu.py --mode tcp --host 192.168.0.200 --unit 1 --ranges "43501-43520" --rate 1
  ```
- RTU 串口模式：
  ```bash
  cd /opt/gateway_rtu/deploy
  /root/venv38/bin/python monitor_rtu.py --mode rtu --serial /dev/ttyS2 --baud 9600 --parity N --stopbits 1 --unit 1 --ranges "43501-43520" --rate 1
  ```

**手动运行采集程序** (绕过 Systemd，用于调试启动报错)：
```bash
cd /opt/gateway_rtu/deploy
sudo ./debug_run.sh
```

## 5. 关键配置文件位置

如果需要修改端口、地址或阈值，请编辑以下文件：

*   **采集逻辑 (端口/地址/阈值)**:
    `src/services/sensor_service.py`
    *   修改串口号: 搜索 `/dev/ttyS2` 或 `/dev/ttyS3`
    *   修改阈值: 搜索 `SimpleThresholdConfig`
    *   修改站号: 搜索 `unit_ids` 或 `_read_photo_sensor` 调用处

*   **报警逻辑 (RTU 寄存器映射)**:
    `src/gateway/alarm/alarm_play/alarm_logic.py`
    *   修改写入规则: 搜索 `build_rtu_registers`

## 6. 开机自启设置

**强制启用开机自启并验证**：
```bash
cd /opt/gateway_rtu/deploy
sudo ./enable_autostart.sh
```
