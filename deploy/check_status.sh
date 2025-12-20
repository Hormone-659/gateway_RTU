#!/bin/bash
# 快速检查服务状态和日志的脚本

echo "========================================"
echo " 1. 服务运行状态"
echo "========================================"
systemctl status sensor.service --no-pager
echo ""
systemctl status alarm.service --no-pager

echo ""
echo "========================================"
echo " 2. 最近 20 行日志"
echo "========================================"
echo "--- Sensor Service Logs ---"
journalctl -u sensor.service -n 20 --no-pager
echo ""
echo "--- Alarm Service Logs ---"
journalctl -u alarm.service -n 20 --no-pager

