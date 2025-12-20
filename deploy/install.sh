#!/bin/bash

# 检查是否以 root 运行
if [ "$EUID" -ne 0 ]; then
  echo "请以 root 权限运行此脚本"
  exit 1
fi

# --- 配置 ---
PYTHON_BIN="/root/venv38/bin/python"
# ------------

# 获取脚本当前所在目录 (deploy 目录)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
# 项目根目录 (deploy 的上一级)
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== 工业网关部署脚本 ==="
echo "脚本位置: $SCRIPT_DIR"
echo "项目根目录: $PROJECT_ROOT"
echo "Python 路径: $PYTHON_BIN"

# 1. 检查 Python 是否存在
if [ ! -f "$PYTHON_BIN" ]; then
    echo "错误: 未找到 Python 解释器: $PYTHON_BIN"
    echo "请检查虚拟环境路径是否正确。"
    exit 1
fi

# 2. 检查 src 目录是否存在
if [ ! -d "$PROJECT_ROOT/src" ]; then
    echo "错误: 在 $PROJECT_ROOT 下未找到 src 目录。"
    echo "请确保你是在 deploy 目录下运行此脚本，且项目结构完整。"
    exit 1
fi

# 3. 动态修正 service 文件中的路径
echo "正在配置服务文件..."
SERVICE_FILES=("sensor.service" "alarm.service")

for SERVICE in "${SERVICE_FILES[@]}"; do
    FILE_PATH="$SCRIPT_DIR/$SERVICE"
    if [ -f "$FILE_PATH" ]; then
        echo "处理 $SERVICE ..."
        # 替换 WorkingDirectory
        sed -i "s|WorkingDirectory=.*|WorkingDirectory=$PROJECT_ROOT/src|g" "$FILE_PATH"
        # 替换 PYTHONPATH
        sed -i "s|Environment=PYTHONPATH=.*|Environment=PYTHONPATH=$PROJECT_ROOT/src|g" "$FILE_PATH"
        # 替换 ExecStart 中的 Python 路径
        sed -i "s|ExecStart=.* -m|ExecStart=$PYTHON_BIN -m|g" "$FILE_PATH"
    else
        echo "警告: 未找到 $FILE_PATH"
    fi
done

# 4. 安装依赖
echo "正在安装依赖..."
"$PYTHON_BIN" -m pip install -r "$PROJECT_ROOT/requirements.txt"

# 5. 部署 Systemd 服务
echo "部署 Systemd 服务..."
for SERVICE in "${SERVICE_FILES[@]}"; do
    if [ -f "$SCRIPT_DIR/$SERVICE" ]; then
        cp "$SCRIPT_DIR/$SERVICE" /etc/systemd/system/
        echo "已复制 $SERVICE 到 /etc/systemd/system/"
    fi
done

# 6. 重新加载并启动
echo "重新加载 Systemd..."
systemctl daemon-reload

# 禁用可能冲突的 serial-getty
# 如果我们使用 ttyS2，必须确保没有 getty 占用它
TARGET_PORT="ttyS2"
if systemctl list-units --all | grep -q "serial-getty@$TARGET_PORT.service"; then
    echo "禁用 serial-getty@$TARGET_PORT 以释放串口..."
    systemctl stop "serial-getty@$TARGET_PORT.service"
    systemctl disable "serial-getty@$TARGET_PORT.service"
    systemctl mask "serial-getty@$TARGET_PORT.service"
fi

echo "启用并重启服务..."
systemctl enable sensor.service
systemctl enable alarm.service
systemctl restart sensor.service
systemctl restart alarm.service

echo "=== 部署完成 ==="
echo "检查状态命令: systemctl status sensor.service alarm.service"


# 重新加载 systemd 配置
systemctl daemon-reload

# 启用并启动服务
echo "启用并启动服务..."
systemctl enable sensor.service
systemctl enable alarm.service
systemctl restart sensor.service
systemctl restart alarm.service

echo "部署完成！"
echo "可以使用 'systemctl status sensor.service' 和 'systemctl status alarm.service' 查看状态。"

