# 冰箱食材识别管理系统 - 开发板端配置
import os

# 网络配置
BOARD_IP = "192.168.2.77"
SERVER_URL = "http://192.168.2.100:5000"  # 服务器地址，需根据实际修改
SYNC_INTERVAL = 2  # 数据同步间隔(秒)

# GPIO引脚配置 (Luckfox Pico Ultra)
DOOR_PIN = 32    # 门磁传感器 (吸合=高电平=门关)
RELAY_PIN = 40   # 继电器 (高电平=闭合=灯带亮)

# AI推理结果文件
DETECTION_FILE = "/tmp/fridge_detections.json"

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), "fridge.db")

# 显示配置
SCREEN_WIDTH = 480
SCREEN_HEIGHT = 480
FB_DEVICE = "/dev/fb0"

# 食材分类映射 (COCO类别中与冰箱食材相关的)
FOOD_CLASSES = {
    47: "苹果", 46: "香蕉", 50: "西兰花", 49: "胡萝卜",
    51: "橙子", 44: "瓶子(饮料)", 73: "碗", 55: "杯子",
    54: "三明治", 53: "披萨", 57: "蛋糕", 52: "甜甜圈",
    56: "热狗", 59: "苹果", 48: "橙子", 58: "胡萝卜",
}

# COCO class ID to Chinese food name
COCO_FOOD_NAMES = {
    0: "人",
    44: "瓶装饮品",    # bottle
    46: "香蕉",        # banana
    47: "苹果",        # apple
    48: "三明治",      # sandwich (merged)
    49: "橙子",        # orange
    50: "西兰花",      # broccoli
    51: "胡萝卜",      # carrot
    52: "热狗",        # hot dog
    53: "披萨",        # pizza
    54: "甜甜圈",      # donut
    55: "蛋糕",        # cake
    56: "椅子",
    57: "沙发",
    58: "盆栽",
    59: "床",
    60: "餐桌",
    61: "马桶",
    62: "电视",
    63: "笔记本电脑",
    64: "鼠标",
    65: "遥控器",
    66: "键盘",
    67: "手机",
    68: "微波炉",
    69: "烤箱",
    70: "烤面包机",
    71: "水槽",
    72: "冰箱",
    73: "书",
    74: "时钟",
    75: "花瓶",
    76: "剪刀",
    77: "泰迪熊",
    78: "吹风机",
    79: "牙刷",
}
