#!/bin/bash
# 脚本功能：同时查看 sensor 和 alarm 服务的实时日志
# 使用方法：./watch_logs.sh
# 退出方法：按 Ctrl+C

echo "========================================================"
echo " 正在监视 Gateway 服务日志 (Sensor + Alarm)"
echo " 采集服务: 每 1 秒更新一次"
echo " 报警服务: 每 30 秒更新一次"
echo " 按 Ctrl+C 退出查看 (服务会在后台继续运行)"
echo "========================================================"
echo ""

# -u 指定服务单元
# -f 实时跟随 (Follow)
# -n 20 显示最近 20 行历史
# --no-pager 防止分页
journalctl -u sensor.service -u alarm.service -f -n 20 --no-pager

