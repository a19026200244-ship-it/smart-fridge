#!/bin/bash
# 编译 fridge_mgr (管理程序)
export LUCKFOX_SDK_PATH=/home/taki/luckfox-pico
TC="${LUCKFOX_SDK_PATH}/tools/linux/toolchain/arm-rockchip830-linux-uclibcgnueabihf/bin/arm-rockchip830-linux-uclibcgnueabihf-gcc"

echo "Compiler: $TC"
$TC --version | head -1

cd /mnt/c/claude_code_demo/board/smartfridge/
$TC -Os -o fridge_mgr fridge_mgr.c sqlite3.c -lpthread -ldl -lm

if [ -f fridge_mgr ]; then
    echo "BUILD SUCCESS!"
    ls -la fridge_mgr
    file fridge_mgr
else
    echo "BUILD FAILED!"
fi
