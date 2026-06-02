#!/usr/bin/env python3
"""
SmartFridge v6 - 门开AI启动/门关AI停止 + MT触屏 + 高对比度LCD UI

架构说明:
  板端主控程序，负责门磁检测、灯光控制、AI进程管理、库存事件和与服务端同步。

状态机:
  IDLE (门关)
    ├─ door_open → 切换到 DETECTING: 开灯、启动AI、记录baseline
    └─ 触屏处理 (inventory编辑、事件删除)
  DETECTING (门开)
    ├─ 持续轮询检测结果，每SYNC_INTERVAL秒同步状态到服务端
    └─ door_close → 切换到 PROCESSING: 记录after、对比baseline、生成事件
  PROCESSING (门关事件)
    ├─ process_events(): 比对before/after，计算净变化，更新库存
    └─ 回到 IDLE

事件检测策略 (ADR-6):
  开门时记录baseline → 关门时记录after → diff = after - baseline
  计数增加 → put_in，计数减少 → take_out
  局限: 开门期间多次操作被合并为净变化；手部/遮挡可能导致误判
"""
import os, sys, json, time, signal, subprocess
from periphery import GPIO
from lcd_ui import draw_lcd, find_touch_device, handle_touch_events
from partial_qty import (
    build_count_map,
    compare_liquid_levels,
    parse_detection_details_from_file,
)
from event_stabilizer import FrameStabilizer, CooldownController

# ── 配置加载 ──────────────────────────────────────────────────────────────
# 支持无配置文件时使用硬编码默认值（向后兼容）
# 配置路径: 相对于本文件 ../config/board.json
# 读取层级: _get("hardware", "door_pin") 读取 hardware.door_pin
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../config/board.json")
_cfg = {}
try:
    with open(_CONFIG_PATH) as f:
        _cfg = json.load(f)
except Exception:
    pass

def _get(*keys, default):
    v = _cfg
    for k in keys:
        if isinstance(v, dict): v = v.get(k)
        else: return default
    return v if v is not None else default

# ── 事件增强配置 ──────────────────────────────────────────────────────────
EVT_AUTO_THRESH   = _get("event", "auto_confirm_threshold", default=2)
EVT_REVIEW_THRESH = _get("event", "needs_review_threshold", default=1)
EVT_ENABLE_REVIEW = _get("event", "enable_review", default=True)

# ── 分层识别配置 ─────────────────────────────────────────────────────────
CAT_MAP      = _get("category_map", default={})
DISPLAY_NAME = _get("display_name_map", default={})
CAT_ICONS    = _get("category_icons", default={})
PKG_MAP      = _get("package_type_map", default={})  # NEW: 包装类型映射
LIQUID_CFG   = _get("liquid_level", default={})

def _get_category(raw_name):
    """
    根据 COCO 原名返回 {category, category_l2, item_key}。
    如果无法识别，返回 {category: "未知", category_l2: "未知", item_key: raw_name}。
    保证永远不会返回 None。
    """
    info = CAT_MAP.get(raw_name, {})
    return {
        "category":  info.get("c1") or "未知",
        "category_l2": info.get("c2") or "未知",
        "item_key":  raw_name,
        "icon":      info.get("icon") or CAT_ICONS.get(info.get("c1"), "❓"),
    }

def _get_package_type(raw_name):
    """
    根据 COCO 原名返回 {qty_type, label, approx_levels}。
    qty_type: 'count' (离散) | 'packed' (包装/盒装) | 'approx' (近似数量)
    找不到时默认返回 count（离散物品）。
    """
    info = PKG_MAP.get(raw_name, {})
    return {
        "qty_type": info.get("qty_type") or "count",
        "label":    info.get("label") or "个",
        "approx_levels": info.get("approx_levels") or [],
    }

def _display_name(raw_name):
    """COCO 原名 → 中文显示名，找不到时返回原名"""
    return DISPLAY_NAME.get(raw_name, raw_name)

DOOR_PIN      = _get("hardware", "door_pin", default=32)      # 门磁GPIO: 0=开门 1=关门
RELAY_PIN     = _get("hardware", "relay_pin", default=40)     # 继电器GPIO: True=通电=灯亮
DB_FILE       = _get("paths", "data_file", default="/root/smartfridge/fridge_data.json")  # 本地JSON数据库
AI_BIN        = _get("paths", "ai_binary", default="/root/smartfridge/bin/fridge_ai")       # AI推理二进制路径
AI_MODEL      = _get("paths", "ai_model", default="/root/smartfridge/model/yolov5.rknn")   # RKNN模型文件路径
DET_FILE      = _get("paths", "det_file", default="/tmp/fridge_detections.json")            # AI检测结果文件 (fridge_ai写入)
FB_LOCK       = _get("paths", "fb_lock", default="/tmp/fb_lock")                            # LCD互斥锁文件，防止AI覆盖UI
SERVER_URL    = _get("sync", "server_url", default="http://192.168.2.73:5000")             # Flask服务端同步地址
SYNC_INTERVAL = _get("sync", "interval_seconds", default=2)                                # 状态同步间隔（秒）

# ── 多帧稳定 + 门关后冷却期配置 ─────────────────────────────────────────
# 解决赛题明确点出的两个真实事件识别问题：
#   1) 单帧误检/手部晃过被识别为物品进出
#   2) 门关瞬间手还在、物品没放稳导致的误判
STAB_FRAMES        = _get("stabilization", "stability_frames",      default=3)   # 物品需连续 K 帧被检出才采纳
COOLDOWN_SECONDS   = _get("stabilization", "cooldown_seconds",     default=3.0) # 门关后冷却期秒数
COOLDOWN_STABLE_FRAMES = _get("stabilization", "cooldown_stable_frames", default=3) # 冷却期内画面连续 N 帧一致即提前结束

# 打印配置加载状态（便于调试部署问题）
_config_loaded = bool(_cfg)
if _config_loaded:
    print(f"[Config] Loaded from {_CONFIG_PATH}")
    print(f"  hardware: door={DOOR_PIN} relay={RELAY_PIN}")
    print(f"  paths: data={DB_FILE}")
    print(f"  sync: server={SERVER_URL} interval={SYNC_INTERVAL}s")
else:
    print(f"[Config] No config file found ({_CONFIG_PATH}), using defaults")

running, ai_proc = True, None  # running: 主循环标志, ai_proc: AI子进程句柄
TOUCH_DEV = None                # 触摸设备路径，如 /dev/input/event0

# ── 信号处理 ─────────────────────────────────────────────────────────────
# SIGINT/SIGTERM 优雅退出，避免 AI 进程和 GPIO 未清理
def sig_handler(sig, frame):
    global running; running = False

# ── 数据管理 ─────────────────────────────────────────────────────────────
def load_data():
    """从本地 JSON 文件加载库存和事件数据，文件损坏时返回空结构"""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE) as f: return json.load(f)
        except: pass
    return {"inventory":[], "events":[], "status":{"door_state":"closed","light_state":"off","cpu_temp":0}}

def save_data(d):
    """原子写入: 先写 .tmp 再 rename，防止写坏导致数据丢失"""
    with open(DB_FILE+".tmp","w") as f: json.dump(d, f, default=str)
    os.replace(DB_FILE+".tmp", DB_FILE)

def inv_update(data, name, delta, qty_type=None, qty_estimate=None):
    """
    更新库存中指定食材的数量。
    delta > 0: 增加数量；delta < 0: 减少数量
    数量归零时自动删除该食材。
    qty_type: 'count' | 'packed' | 'approx'，新增/首次时写入
    qty_estimate: 近似等级的字符串索引（如 "左侧贴近食物"），仅 packed/approx 用
    """
    for item in data["inventory"]:
        if item["name"] == name:
            item["count"] += delta
            if item["count"] <= 0:
                data["inventory"].remove(item)
            else:
                item["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
                if qty_type:   item["qty_type"]   = qty_type
                if qty_estimate is not None: item["qty_estimate"] = qty_estimate
            return
    if delta > 0:  # 只在新增时（有delta>0）才加物品，负数不减
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {"name": name, "count": delta, "first_seen": now, "last_updated": now}
        if qty_type: entry["qty_type"] = qty_type
        if qty_estimate is not None: entry["qty_estimate"] = qty_estimate
        data["inventory"].append(entry)

def evt_add(data, action, food, count=1, review_status=None, confidence=None,
             category=None, category_l2=None, item_key=None,
             qty_type=None, qty_estimate=None,
             before_qty_estimate=None, after_qty_estimate=None, reason=None):
    """
    添加事件记录。
    action: 'put_in' | 'take_out' | 'manual_add' | 'manual_del'
    count: 本次操作涉及的数量（正整数）
    review_status: None | 'needs_review' | 'confirmed' | 'rejected'
    confidence: 0.0~1.0，表示事件判定置信度
    category/category_l2: 粗分类（分层识别），向后兼容旧数据可传 None
    qty_type: 'count' | 'packed' | 'approx' | 'liquid_level'
    qty_estimate: 近似等级描述字符串，仅 packed/approx 时有效
    """
    entry = {
        "id": len(data["events"])+1,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "food_name": food,
        "count": count
    }
    if review_status:    entry["review_status"] = review_status
    if confidence is not None: entry["confidence"] = round(confidence, 3)
    if category:        entry["category"] = category
    if category_l2:     entry["category_l2"] = category_l2
    if item_key:        entry["item_key"] = item_key
    if qty_type:        entry["qty_type"] = qty_type
    if qty_estimate is not None: entry["qty_estimate"] = qty_estimate
    if before_qty_estimate is not None: entry["before_qty_estimate"] = before_qty_estimate
    if after_qty_estimate is not None: entry["after_qty_estimate"] = after_qty_estimate
    if reason:          entry["reason"] = reason
    data["events"].append(entry)

# ── 网络同步 ──────────────────────────────────────────────────────────────
def http_sync(data):
    """将本地数据全量同步到 Flask 服务端（每SYNC_INTERVAL秒调用一次）
    失败时只打印错误不抛异常，防止网络抖动导致主循环中断"""
    try:
        import urllib.request
        body = json.dumps(data, default=str).encode()
        req = urllib.request.Request(SERVER_URL+"/api/sync", data=body,
            headers={"Content-Type":"application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception as e:
        print(f"[Sync] FAIL: {e}"); return False

# ── AI 检测结果解析 ───────────────────────────────────────────────────────
def parse_detections():
    """
    读取 fridge_ai 写入的检测结果 JSON，按食材名称聚合计数。
    AI输出格式: {"detections": [{"name": "苹果", "confidence": 0.95}, ...]}
    返回: {"苹果": 3, "香蕉": 2}  (key=食材名, value=检测帧中出现次数)
    注意: person类别被过滤（不把人的手识别为食材）
    """
    try:
        return build_count_map(parse_detection_details())
    except: return {}

def parse_detection_details():
    """
    读取 fridge_ai 检测详情。
    新格式支持 name/confidence/bbox/frame_path/qty_estimate；旧格式只有 name 时也兼容。
    """
    return parse_detection_details_from_file(DET_FILE)

# ── AI 进程管理 ───────────────────────────────────────────────────────────
def ai_start():
    """启动 fridge_ai 子进程，记录日志到 /root/smartfridge/logs/ai.log
    注意: 已在 main() 中设置 LD_LIBRARY_PATH，fridge_ai 依赖板端动态库"""
    global ai_proc
    if ai_proc and ai_proc.poll() is None: return  # 防止重复启动
    env = os.environ.copy(); env["LD_LIBRARY_PATH"] = "/oem/usr/lib:/usr/lib"
    log = open("/root/smartfridge/logs/ai.log","w")
    ai_proc = subprocess.Popen([AI_BIN, AI_MODEL], stdout=log, stderr=log, env=env)
    print(f"[Mgr] AI started PID={ai_proc.pid}")

def ai_stop():
    """
    停止 fridge_ai 子进程（先优雅终止，超时再强制kill）。
    同时删除 FB_LOCK 确保 Python UI 可正常接管屏幕。
    """
    global ai_proc
    if ai_proc:
        ai_proc.terminate()
        try: ai_proc.wait(timeout=3)
        except: ai_proc.kill()
        ai_proc = None
    os.system("killall fridge_ai 2>/dev/null")  # 双保险，确保进程退出
    if os.path.exists(FB_LOCK): os.remove(FB_LOCK)
    print("[Mgr] AI stopped")

def lcd_lock():
    """创建 FB_LOCK 文件，通知 fridge_ai 不要覆盖 LCD（Python正在写屏）"""
    if not os.path.exists(FB_LOCK): open(FB_LOCK,"w").close()

def lcd_unlock():
    """删除 FB_LOCK 文件，通知 fridge_ai 可以写 LCD（AI接管摄像头画面）"""
    if os.path.exists(FB_LOCK): os.remove(FB_LOCK)

# ── 事件处理 ─────────────────────────────────────────────────────────────
def process_events(data, before, after, before_details=None, after_details=None):
    """
    增强版事件检测：带置信度、审核状态和包装类型感知。

    判定规则:
      total_diff = abs(after_cnt - before_cnt)  同一食材的净变化量
      total_diff >= auto_confirm_threshold (默认2) → 自动确认 → 直接更新库存
      needs_review_threshold (默认1) <= total_diff < auto_confirm_threshold → 待审核
      total_diff < needs_review_threshold (默认1) → 忽略（噪声）

    包装类型感知:
      qty_type='count'  → 离散物品，diff 直接作为 count
      qty_type='packed' → 包装/盒装，diff 的绝对值 > 0 即记为"变化"，具体数量进 qty_estimate
      qty_type='approx' → 近似数量，任何非零 diff 都视为"状态变化"，不记录精确 count

    review_status 语义:
      'confirmed'   - 自动确认，库存已更新
      'needs_review' - 待人工审核，库存未变动，待确认后由人工处理
      None           - 旧数据兼容（无审核状态即自动确认）
    """
    all_names = set(before) | set(after)

    for name in all_names:
        before_cnt = before.get(name, 0)
        after_cnt  = after.get(name, 0)
        diff = after_cnt - before_cnt

        if diff == 0:
            continue

        total_diff = abs(diff)
        action = "put_in" if diff > 0 else "take_out"

        # 判定审核状态
        if total_diff >= EVT_AUTO_THRESH:
            status = "confirmed"
        elif total_diff >= EVT_REVIEW_THRESH:
            status = "needs_review"
        else:
            print(f"  -> IGNORE {name} x{total_diff} (below noise threshold)")
            continue

        cat  = _get_category(name)
        pkg  = _get_package_type(name)  # {qty_type, label, approx_levels}

        # 包装类型感知：确定事件 count 和 qty_estimate
        if status == "confirmed":
            if pkg["qty_type"] == "approx":
                # 近似物品：不记录精确 count，记变化方向 + qty_estimate=action
                event_count = 1
                qty_estimate = action
            elif pkg["qty_type"] == "packed":
                event_count = abs(diff) if diff != 0 else 1
                qty_estimate = action
            else:
                # 离散物品：使用精确 diff
                event_count = abs(diff)
                qty_estimate = None
        else:
            # needs_review：所有类型都不改库存，只记录事件
            event_count = abs(diff)
            # count 类型不需要 qty_estimate；packed/approx 才记录近似等级
            qty_estimate = None if pkg["qty_type"] == "count" else "需人工确认"

        print(f"  -> {action.upper():5} {_display_name(name)} "
              f"[qty_type={pkg['qty_type']}] "
              f"[{status}] [{cat['category']}]")

        if status == "confirmed":
            inv_update(data, _display_name(name), diff if pkg["qty_type"] == "count" else 0,
                        qty_type=pkg["qty_type"], qty_estimate=qty_estimate)
            evt_add(data, action, _display_name(name), event_count,
                    review_status="confirmed", confidence=1.0,
                    category=cat["category"], category_l2=cat["category_l2"], item_key=cat["item_key"],
                    qty_type=pkg["qty_type"], qty_estimate=qty_estimate)
        else:
            evt_add(data, action, _display_name(name), event_count,
                    review_status="needs_review", confidence=0.5,
                    category=cat["category"], category_l2=cat["category_l2"], item_key=cat["item_key"],
                    qty_type=pkg["qty_type"], qty_estimate=qty_estimate)

    # 数量没变时，再比较透明瓶等 liquid_level 物品的状态变化。
    # 这一步只处理 count 不变的物品，避免和整件 put_in/take_out 冲突。
    if before_details is not None and after_details is not None:
        partial_events = compare_liquid_levels(
            before_details,
            after_details,
            PKG_MAP,
            DISPLAY_NAME,
            CAT_MAP,
            LIQUID_CFG,
        )
        for event in partial_events:
            name = event.get("item_key")
            if before.get(name, 0) != after.get(name, 0):
                continue

            print(f"  -> PARTIAL {_display_name(name)} "
                  f"[{event.get('before_qty_estimate')} -> {event.get('after_qty_estimate')}] "
                  f"[{event.get('review_status')}]")

            if event.get("review_status") == "confirmed":
                inv_update(data, event["food_name"], 0,
                           qty_type="liquid_level",
                           qty_estimate=event.get("after_qty_estimate"))

            evt_add(data, event["action"], event["food_name"], event.get("count", 1),
                    review_status=event.get("review_status"),
                    confidence=event.get("confidence"),
                    category=event.get("category"),
                    category_l2=event.get("category_l2"),
                    item_key=event.get("item_key"),
                    qty_type=event.get("qty_type"),
                    qty_estimate=event.get("qty_estimate"),
                    before_qty_estimate=event.get("before_qty_estimate"),
                    after_qty_estimate=event.get("after_qty_estimate"),
                    reason=event.get("reason"))

def cpu_temp():
    """读取 CPU 温度（摄尔修斯），异常时返回 0.0"""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f: return float(f.read().strip())/1000.0
    except: return 0.0

def main():
    """
    主循环: 状态机驱动 (IDLE → DETECTING → PROCESSING → IDLE)

    状态转换:
      IDLE (门关):
        ├─ door_open → DETECTING (开灯、启动AI、记录baseline)
        └─ 触屏处理 (仅IDLE时响应触摸，保护操作一致性)
      DETECTING (门开):
        └─ door_close → PROCESSING (记录after、对比baseline、生成事件)
      PROCESSING:
        └─ → IDLE (自动回到空闲)

    门磁逻辑:
      GPIO输入: 0=磁铁靠近=关门, 1=磁铁远离=开门
      door.read() 返回 True(关门)/False(开门)，所以 door_open = not door.read()
    """
    global running, ai_proc, TOUCH_DEV
    signal.signal(signal.SIGINT, sig_handler); signal.signal(signal.SIGTERM, sig_handler)

    print("===== SmartFridge v6.0 =====")

    # ── 硬件初始化 ──────────────────────────────────────────────────────────
    door = GPIO(DOOR_PIN, "in")          # 门磁输入（Pull-up模式）
    relay = GPIO(RELAY_PIN, "out"); relay.write(False)  # 初始关闭继电器（灯灭）
    print("[Init] GPIO OK")

    TOUCH_DEV = find_touch_device()     # 自动探测触摸设备（GT911/Goodix）
    print(f"[Init] Touch: {TOUCH_DEV or 'N/A'}")

    data = load_data()                   # 恢复上次退出时的库存和事件数据
    print(f"[Init] Data: {len(data['inventory'])} items, {len(data['events'])} events")

    # 关闭板端默认显示服务，释放 framebuffer
    os.system("/oem/usr/bin/RkLunch-stop.sh 2>/dev/null"); time.sleep(0.5)

    # 状态机: IDLE (门关) / DETECTING (门开) / COOLING (门关后冷却)
    # COOLING 阶段: 门关后不立刻判定，等冷却期结束或画面稳定
    #              期间若门重新打开则取消本轮，回到 DETECTING
    state = "IDLE"
    door_was_open, light_on = False, False   # 边沿检测 + 灯状态(保留用于兼容)
    baseline = {}                            # 多帧稳定后的开门画面 {"苹果": 3, "香蕉": 2}
    baseline_details = []                    # 开门时检测详情，用于液位/部分取出判断
    after, after_details = {}, []            # 多帧稳定后的关门画面
    last_sync, last_ui = 0, 0                # 时间戳记录，用于控制同步/LCD刷新频率

    # 多帧稳定器：复用于 baseline 和 after 累积
    stab = FrameStabilizer(stability_frames=STAB_FRAMES)

    # 冷却期控制器：门关后等画面稳定或冷却期满
    cooldown = CooldownController(
        cooldown_seconds=COOLDOWN_SECONDS,
        stable_frames_required=COOLDOWN_STABLE_FRAMES,
        door_is_open_callback=lambda: not door.read(),     # True=门开
        take_snapshot_callback=lambda: build_count_map(parse_detection_details()),
    )

    # 初始: 门关, 显示 UI, 无 AI
    draw_lcd(data, TOUCH_DEV)
    print("[Main] IDLE (door closed, AI off, multi-frame + cooldown ready)\n")

    while running:
        try:
            door_open = not door.read()   # GPIO 0=开门, 1=关门 → Python True=关门, False=开门
        except:
            print("[Warn] GPIO error"); time.sleep(5); continue

        # ── 触屏 (仅 IDLE 模式处理) ────────────────────────────────────────
        if state == "IDLE" and not door_open and TOUCH_DEV:
            if handle_touch_events(data, TOUCH_DEV):
                save_data(data); draw_lcd(data, TOUCH_DEV)

        # ── 状态转换 1: IDLE → DETECTING (门从关变为开) ─────────────────────
        if door_open and state == "IDLE":
            print("\n>>> Door OPENED! AI starting...")
            state = "DETECTING"
            door_was_open = True
            relay.write(True); light_on = True
            ai_start()
            stab.reset()
            baseline, baseline_details = {}, []

        # ── DETECTING: 多帧稳定累积 baseline ───────────────────────────────
        if state == "DETECTING":
            # 每帧用 FrameStabilizer 累积稳定的检测结果
            dets = parse_detection_details()
            current = stab.update(dets)
            baseline = current
            baseline_details = dets
            data["status"].update({"door_state":"open","light_state":"on","cpu_temp":cpu_temp()})
            save_data(data)
            if time.time() - last_sync >= SYNC_INTERVAL:
                http_sync(data); last_sync = time.time()
            time.sleep(0.2)

            # 边沿: DETECTING → COOLING (门刚关)
            if not door_open:
                print(">>> Door CLOSED! Entering cooldown...")
                state = "COOLING"
                door_was_open = False
                relay.write(False); light_on = False
                ai_stop()                                # 立即停 AI，节省功耗
                # 暂存最后一帧的稳定 baseline
                baseline = stab.snapshot()
                # 进入冷却期
                cooldown.on_door_close()
                after, after_details = {}, []
                print(f"[Cooldown] start, baseline_stable={baseline}")
            continue

        # ── COOLING: 多帧稳定累积 after + 监控门重开 ─────────────────────
        if state == "COOLING":
            # 冷却期内门重新打开 → 取消本轮 after, 回到 DETECTING
            if door_open:
                print("[Cooldown] door REOPENED, cancel this round")
                state = "DETECTING"
                door_was_open = True
                relay.write(True); light_on = True
                ai_start()
                stab.reset()
                after, after_details = {}, []
                time.sleep(0.1)
                continue

            # 冷却期内持续读帧累积稳定的 after
            dets = parse_detection_details()
            current = stab.update(dets)
            after = current
            after_details = dets

            # tick 一下: 监控画面稳定度 / 冷却期是否已到
            cooldown.tick()
            if cooldown.is_ready():
                # 冷却完成,触发 process_events
                print(f"[Cooldown] READY after {cooldown.elapsed:.1f}s, "
                      f"baseline={baseline} after={after}")
                process_events(data, baseline, after, baseline_details, after_details)
                save_data(data)
                draw_lcd(data, TOUCH_DEV)
                http_sync(data); last_sync = time.time()
                baseline, after = {}, {}
                baseline_details, after_details = [], []
                stab.reset()
                cooldown.reset()
                state = "IDLE"
                print("[Main] IDLE\n")
            elif cooldown.is_canceled():
                # 这里其实到不了(被上面 door_open 提前捕获),但保留
                print("[Cooldown] canceled, back to IDLE")
                baseline, after = {}, {}
                baseline_details, after_details = [], []
                stab.reset()
                cooldown.reset()
                state = "IDLE"
            time.sleep(0.2)
            continue

        # ── IDLE: 门关着 ────────────────────────────────────────────────────
        if state == "IDLE" and not door_open:
            data["status"].update({"door_state":"closed","light_state":"off","cpu_temp":cpu_temp()})
            save_data(data)
            # LCD每1.5秒刷新（避免过于频繁导致闪烁）
            if time.time() - last_ui >= 1.5:
                draw_lcd(data, TOUCH_DEV); last_ui = time.time()
            # 同步到服务端
            if time.time() - last_sync >= SYNC_INTERVAL:
                http_sync(data); last_sync = time.time()
            time.sleep(0.15)  # 避免空转CPU

    # ── 退出清理 ───────────────────────────────────────────────────────────
    relay.write(False); ai_stop()     # 确保灯灭、AI停止
    door.close(); relay.close()        # 释放GPIO
    print("===== Stopped =====")

if __name__ == "__main__":
    main()
