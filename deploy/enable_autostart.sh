#!/bin/bash
# 脚本功能：设置并验证服务开机自启
# 使用方法：sudo ./enable_autostart.sh

echo "=== 配置开机自启动 ==="

# 1. 强制启用服务
echo "正在启用 sensor.service ..."
systemctl enable sensor.service

echo "正在启用 alarm.service ..."
systemctl enable alarm.service

echo ""
echo "=== 验证自启动状态 ==="

# 2. 检查状态
SENSOR_STATUS=$(systemctl is-enabled sensor.service)
ALARM_STATUS=$(systemctl is-enabled alarm.service)

echo "Sensor Service 自启状态: $SENSOR_STATUS"
if [ "$SENSOR_STATUS" == "enabled" ]; then
    echo "✅ Sensor 服务已设置为开机自启"
else
    echo "❌ Sensor 服务未正确设置"
fi

echo "Alarm Service 自启状态:  $ALARM_STATUS"
if [ "$ALARM_STATUS" == "enabled" ]; then
    echo "✅ Alarm 服务已设置为开机自启"
else
    echo "❌ Alarm 服务未正确设置"
fi

echo ""
echo "提示：如果状态显示为 'enabled'，则下次重启网关后服务会自动运行。"

