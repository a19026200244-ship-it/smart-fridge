#!/usr/bin/env python3
# 冰箱食材识别管理系统 - 开发板端主程序
import json
import os
import sys
import time
import signal
import subprocess
import threading
import re
import traceback

from config import (
    DOOR_PIN, RELAY_PIN, DETECTION_FILE,
    SYNC_INTERVAL, DB_PATH, SCREEN_WIDTH, SCREEN_HEIGHT, FB_DEVICE
)
from gpio_ctrl import DoorSensor, Relay
from database import FridgeDatabase
from event_detector import EventDetector
from display_ui import DisplayUI
from server_sync import ServerSync

running = True
ai_process = None
detection_cache = []
detection_cache_time = 0

# COCO类别到中文名称的映射
COCO_FOOD_MAP = {
    'person': '人',
    'bottle': '瓶装饮品', 'wine glass': '杯子', 'cup': '杯子', 'bowl': '碗',
    'banana': '香蕉', 'apple': '苹果', 'orange': '橙子',
    'sandwich': '三明治', 'hot dog': '热狗', 'pizza': '披萨',
    'donut': '甜甜圈', 'cake': '蛋糕',
    'broccoli': '西兰花', 'carrot': '胡萝卜',
    'potted plant': '盆栽', 'vase': '花瓶',
    'refrigerator': '冰箱', 'oven': '烤箱', 'toaster': '烤面包机',
    'microwave': '微波炉', 'sink': '水槽',
    'book': '书本', 'cell phone': '手机',
    'chair': '椅子', 'couch': '沙发', 'dining table': '餐桌',
    'scissors': '剪刀', 'teddy bear': '泰迪熊',
    'hair dryer': '吹风机', 'toothbrush': '牙刷',
    'mouse': '鼠标', 'keyboard': '键盘', 'laptop': '笔记本电脑',
    'remote': '遥控器', 'clock': '时钟', 'tv': '电视',
}


def signal_handler(sig, frame):
    global running, ai_process
    print("\n正在关闭系统...")
    running = False
    if ai_process:
        ai_process.terminate()


def read_detections(filepath):
    """从JSON文件读取AI检测结果"""
    try:
        if not os.path.exists(filepath):
            return []
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("detections", [])
    except (json.JSONDecodeError, IOError) as e:
        return []


def parse_ai_stdout(line):
    """解析AI程序的stdout输出行
    格式: name @ (x1 y1 x2 y2) confidence
    例如: apple @ (100 200 300 400) 0.850
    """
    match = re.match(r'(.+?)\s*@\s*\((\d+)\s+(\d+)\s+(\d+)\s+(\d+)\)\s+([\d.]+)', line.strip())
    if match:
        name_en = match.group(1).strip()
        name_cn = COCO_FOOD_MAP.get(name_en, name_en)
        return {
            "name": name_cn,
            "box": [int(match.group(2)), int(match.group(3)),
                    int(match.group(4)), int(match.group(5))],
            "confidence": float(match.group(6))
        }
    return None


def ai_stdout_reader(process):
    """后台线程：读取AI进程的stdout并更新检测缓存"""
    global detection_cache, detection_cache_time
    current_batch = []
    last_batch_time = time.time()

    for line in iter(process.stdout.readline, ''):
        if not running:
            break
        line = line.strip()
        det = parse_ai_stdout(line)
        if det:
            current_batch.append(det)

        # 每秒更新一次缓存
        now = time.time()
        if now - last_batch_time >= 1.0:
            detection_cache = current_batch
            detection_cache_time = now
            current_batch = []
            last_batch_time = now


def start_ai_process(model_path=None):
    """启动AI推理子进程"""
    global ai_process

    # 尝试多个可能的AI程序路径
    ai_binaries = [
        "/oem/usr/bin/fridge_ai",
        "/userdata/fridge_ai",
        "/root/fridge_ai",
        "./fridge_ai",
    ]
    ai_bin = None
    for path in ai_binaries:
        if os.path.exists(path):
            ai_bin = path
            break

    if ai_bin and model_path:
        print(f"[Main] 启动AI推理程序: {ai_bin} {model_path}")
        try:
            ai_process = subprocess.Popen(
                [ai_bin, model_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            t = threading.Thread(target=ai_stdout_reader, args=(ai_process,), daemon=True)
            t.start()
            return True
        except Exception as e:
            print(f"[Main] AI进程启动失败: {e}")
            ai_process = None
    return False


def get_cpu_temp():
    """获取CPU温度"""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return float(f.read().strip()) / 1000.0
    except Exception:
        return 0.0


def main():
    global running, detection_cache, detection_cache_time

    print("=" * 50)
    print("冰箱食材识别管理系统 - 开发板端启动中...")
    print("=" * 50)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # ---- 初始化各模块 ----
    try:
        door = DoorSensor(DOOR_PIN)
        print("[Main] 门磁传感器初始化成功 (GPIO{})".format(DOOR_PIN))
    except Exception as e:
        print("[Main] 门磁初始化失败: {} (模拟运行)".format(e))
        door = None

    try:
        relay = Relay(RELAY_PIN)
        print("[Main] 继电器初始化成功 (GPIO{})".format(RELAY_PIN))
    except Exception as e:
        print("[Main] 继电器初始化失败: {} (模拟运行)".format(e))
        relay = None

    db = FridgeDatabase(DB_PATH)
    print("[Main] 数据库初始化成功 ({})".format(DB_PATH))

    detector = EventDetector(stability_frames=3)

    syncer = ServerSync()
    print("[Main] 服务器同步模块初始化")

    display = DisplayUI(SCREEN_WIDTH, SCREEN_HEIGHT, FB_DEVICE)

    # ---- 尝试启动AI推理程序 ----
    ai_model = "/oem/usr/share/yolov5n.rknn"
    if not os.path.exists(ai_model):
        ai_model = "/userdata/yolov5n.rknn"
    if not os.path.exists(ai_model):
        ai_model = "./model/yolov5.rknn"

    ai_started = start_ai_process(ai_model)
    if ai_started:
        print("[Main] AI推理程序已启动，等待检测结果...")
    else:
        print("[Main] 未找到AI推理程序，将从JSON文件读取检测结果")

    last_sync_time = 0
    last_display_time = 0

    print("[Main] 系统就绪，开始主循环...\n")

    while running:
        try:
            now = time.time()

            # 1. 门磁状态
            door_is_closed = True
            if door:
                door_is_closed = door.is_closed

            # 门打开时开灯
            if relay and not door_is_closed:
                relay.turn_on()
            elif relay and door_is_closed:
                relay.turn_off()

            # 2. AI检测结果
            detections = []
            if ai_process is None:
                # 模式1: 从JSON文件读取
                if now - detection_cache_time > 1.0:
                    detections = read_detections(DETECTION_FILE)
                    detection_cache = detections
                    detection_cache_time = now
                else:
                    detections = detection_cache
            else:
                # 模式2: 使用后台线程解析的stdout结果
                detections = detection_cache

            # 3. 事件检测
            events = detector.update_detections(detections, door_is_closed)

            # 4. 处理事件 -> 更新数据库
            for evt in events:
                action = evt["action"]
                food_name = evt["food_name"]
                count = evt.get("count", 1)

                if action == "put_in":
                    db.add_or_update_item(food_name, delta=count)
                    db.add_event("put_in", food_name, count)
                    print(">>> 事件: 放入 {} x{}".format(food_name, count))
                    if relay and door_is_closed:
                        relay.turn_on()
                        time.sleep(0.3)
                        relay.turn_off()

                elif action == "take_out":
                    db.remove_item(food_name, count)
                    db.add_event("take_out", food_name, count)
                    print(">>> 事件: 取出 {} x{}".format(food_name, count))

            # 5. 更新硬件状态
            door_state_str = "closed" if door_is_closed else "open"
            light_state_str = "on" if (relay and relay.is_on) else "off"
            db.update_hardware_status(
                door_state=door_state_str,
                light_state=light_state_str,
                cpu_temp=get_cpu_temp()
            )

            # 6. 定期同步到服务器
            if now - last_sync_time >= SYNC_INTERVAL:
                data = db.get_all_data()
                syncer.sync_all(data)
                last_sync_time = now

            # 7. 定期更新LCD显示
            if now - last_display_time >= 2.0:
                inventory = db.get_inventory()
                recent_events = db.get_events(limit=20)
                img = display.draw_inventory_screen(
                    inventory, recent_events, door_state_str, light_state_str
                )
                display.show(img)
                last_display_time = now

            # 8. 短暂休眠
            time.sleep(0.5)

        except Exception as e:
            print("[Main] 循环异常: {}".format(e))
            traceback.print_exc()
            time.sleep(1)

    # ---- 清理 ----
    print("[Main] 正在清理资源...")
    if ai_process:
        ai_process.terminate()
        try:
            ai_process.wait(timeout=3)
        except Exception:
            ai_process.kill()
    if door:
        door.close()
    if relay:
        relay.turn_off()
        relay.close()
    display.close()
    print("[Main] 系统已停止")


if __name__ == "__main__":
    main()
