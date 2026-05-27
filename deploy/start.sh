#!/bin/sh
# 冰箱食材识别系统 - 启动脚本
DIR="/root/smartfridge"
MGR="$DIR/fridge_mgr.py"
PID_FILE="/var/run/fridge_mgr.pid"
LOG="$DIR/logs/mgr.log"

echo "========================================="
echo "  冰箱食材识别与管理系统 v2.0"
echo "========================================="

if [ ! -f "$MGR" ]; then echo "ERROR: $MGR not found"; exit 1; fi

# 停止旧实例
if [ -f "$PID_FILE" ]; then
    OLD=$(cat "$PID_FILE")
    kill -0 "$OLD" 2>/dev/null && kill "$OLD" 2>/dev/null && sleep 1
    rm -f "$PID_FILE"
fi
killall fridge_ai 2>/dev/null
killall python3 2>/dev/null

echo "Starting..."
cd "$DIR"
PYTHONUNBUFFERED=1 nohup python3 "$MGR" > "$LOG" 2>&1 &
PID=$!
echo $PID > "$PID_FILE"

sleep 2
if kill -0 "$PID" 2>/dev/null; then
    echo "========================================="
    echo "  System RUNNING (PID=$PID)"
    echo "  tail -f $LOG"
    echo "========================================="
else
    echo "ERROR: Start failed"
    cat "$LOG"
    exit 1
fi
