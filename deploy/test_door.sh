#!/bin/sh
# 模拟开关门测试
echo "=== 模拟开门 ==="
echo 32 > /sys/class/gpio/unexport 2>/dev/null
sleep 1
echo 32 > /sys/class/gpio/export
echo out > /sys/class/gpio/gpio32/direction
echo 0 > /sys/class/gpio/gpio32/value
echo "门已打开..."
sleep 8

echo "=== 模拟关门 ==="
echo 1 > /sys/class/gpio/gpio32/value
echo "门已关闭..."
sleep 5

echo "=== 恢复 ==="
echo in > /sys/class/gpio/gpio32/direction
echo "=== 数据库 ==="
cat /root/smartfridge/fridge_data.json 2>/dev/null
echo ""
echo "=== AI日志 ==="
tail -5 /root/smartfridge/logs/ai.log 2>/dev/null
echo ""
echo "=== 管理日志 ==="
tail -10 /root/smartfridge/logs/mgr.log 2>/dev/null
