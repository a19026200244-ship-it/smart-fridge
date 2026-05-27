#!/bin/sh
# 冰箱食材识别系统 - 停止脚本

PID_FILE="/var/run/fridge_ai.pid"

echo "停止冰箱食材识别系统..."

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "停止进程 PID=$PID..."
        kill "$PID"
        sleep 1
        if kill -0 "$PID" 2>/dev/null; then
            echo "强制终止..."
            kill -9 "$PID"
        fi
        echo "已停止"
    else
        echo "进程不存在"
    fi
    rm -f "$PID_FILE"
else
    echo "未找到PID文件, 尝试killall..."
    killall fridge_ai 2>/dev/null
fi

echo "完成"
