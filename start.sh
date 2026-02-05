#!/bin/bash
# Telegram Matrix Bot - 启动脚本

cd "$(dirname "$0")"

echo "🚀 启动 Telegram Matrix Bot..."

# 检查Python版本
python_version=$(python3 -c 'import sys; print(sys.version_info.major)') 2>/dev/null
if [ "$python_version" != "3" ]; then
    echo "❌ 需要 Python 3"
    exit 1
fi

# 检查依赖
echo "📦 检查依赖..."
pip3 install -q -r requirements.txt 2>/dev/null

# 启动服务
echo "🌐 启动 Web 服务在 http://0.0.0.0:5000 ..."
python3 server.py
