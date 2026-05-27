#!/bin/bash
# 冰箱食材AI程序编译脚本
# 前提条件:
#   1. Luckfox SDK 已安装 (export LUCKFOX_SDK_PATH=/path/to/luckfox-pico)
#      或 glibc交叉编译器已安装 (export GLIBC_COMPILER=/opt/arm-linux-gnueabihf/bin/arm-linux-gnueabihf-)
#   2. luckfox_pico_rknn_example 已克隆到 ~/luckfox_pico_rknn_example

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"

echo "===== 冰箱食材AI程序编译 ====="

# 检查环境
if [ -z "$LUCKFOX_SDK_PATH" ] && [ -z "$GLIBC_COMPILER" ]; then
    echo ""
    echo "请设置以下环境变量之一:"
    echo "  export LUCKFOX_SDK_PATH=/home/taki/luckfox-pico"
    echo "  或"
    echo "  export GLIBC_COMPILER=/opt/arm-linux-gnueabihf/bin/arm-linux-gnueabihf-"
    exit 1
fi

# 检查Luckfox示例目录
LUCKFOX_EXAMPLE="${HOME}/luckfox_pico_rknn_example"
if [ ! -d "$LUCKFOX_EXAMPLE" ]; then
    echo "请先克隆 luckfox_pico_rknn_example:"
    echo "  cd ~ && git clone https://github.com/LuckfoxTECH/luckfox_pico_rknn_example.git"
    exit 1
fi

# 清理旧构建
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# CMake配置
cmake "$SCRIPT_DIR" \
    -DLUCKFOX_EXAMPLE_DIR="$LUCKFOX_EXAMPLE" \
    -DCMAKE_BUILD_TYPE=Release

# 编译
make -j$(nproc)

echo ""
echo "===== 编译完成 ====="
echo "输出文件: ${BUILD_DIR}/fridge_ai"
echo ""
echo "部署到开发板:"
echo "  scp ${BUILD_DIR}/fridge_ai root@192.168.2.77:/oem/usr/bin/"
echo "  scp your_model.rknn root@192.168.2.77:/oem/usr/share/yolov5n.rknn"
echo ""
echo "在开发板上运行:"
echo "  ssh root@192.168.2.77"
echo "  fridge_ai /oem/usr/share/yolov5n.rknn"
