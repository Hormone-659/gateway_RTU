#!/bin/bash
# 手动调试脚本：停止 Systemd 服务并直接运行 Python 代码
# 用于排查服务反复重启或 Systemd 配置问题

echo "=== 停止后台服务 ==="
systemctl stop sensor.service
systemctl stop alarm.service

echo "=== 检查串口占用 ==="
# 检查是否有 getty (终端登录) 占用 ttyS2
if systemctl is-active --quiet serial-getty@ttyS2.service; then
    echo "警告: serial-getty@ttyS2 正在运行，可能会干扰串口通讯！"
    echo "尝试停止它..."
    systemctl stop serial-getty@ttyS2.service
    systemctl mask serial-getty@ttyS2.service
fi

# 检查是否有其他进程占用
fuser /dev/ttyS2

echo "=== 手动运行采集服务 ==="
echo "按 Ctrl+C 停止"
echo "------------------------------------------------"

cd /opt/gateway_rtu
# 设置 PYTHONPATH 并运行
export PYTHONPATH=/opt/gateway_rtu/src
/root/venv38/bin/python -m services.sensor_service

