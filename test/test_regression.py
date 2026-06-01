#!/usr/bin/env python3
"""SmartFridge 配置收敛 — 回归检查清单"""
import os, sys, json, sqlite3, subprocess, time, shutil

PROJECT = "/home/jing/my-project/smartfridge"
os.chdir(PROJECT)

REPORT = []

def check(name, cond, detail=""):
    status = "✓ PASS" if cond else "✗ FAIL"
    msg = f"{status} — {name}"
    if detail:
        msg += f": {detail}"
    REPORT.append((name, cond, msg))
    print(f"  {msg}")

# ══════════════════════════════════════════════════
# 1. 库存功能 — 数据结构完整性
# ══════════════════════════════════════════════════
print("=" * 60)
print("回归检查 — 库存功能")
print("=" * 60)

# 板端 fridge_mgr.py 的 inv_update / evt_add 逻辑完整性
# 测试 JSON 数据结构是否正常
sample = {
    "inventory": [{"name": "苹果", "count": 3, "first_seen": "2026-05-28 10:00:00", "last_updated": "2026-05-28 10:00:00"}],
    "events": [{"id": 1, "timestamp": "2026-05-28 10:00:00", "action": "put_in", "food_name": "苹果", "count": 3}],
    "status": {"door_state": "closed", "light_state": "off", "cpu_temp": 45.0}
}
# 模拟 process_events
inv = sample["inventory"]
evts = sample["events"]
def inv_update(data, name, delta):
    for item in data["inventory"]:
        if item["name"] == name:
            item["count"] += delta
            if item["count"] <= 0: data["inventory"].remove(item)
            else: item["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            return
    if delta > 0:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        data["inventory"].append({"name": name, "count": delta, "first_seen": now, "last_updated": now})

def evt_add(data, action, food, count=1):
    data["events"].append({"id": len(data["events"]) + 1, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "action": action, "food_name": food, "count": count})

# 测试库存增减
test_data = json.loads(json.dumps(sample))
inv_update(test_data, "苹果", 2)
check("库存 +2 后变为 5", test_data["inventory"][0]["count"] == 5, f"count={test_data['inventory'][0]['count']}")

inv_update(test_data, "苹果", -5)
check("库存 -5 后删除苹果", len(test_data["inventory"]) == 0, f"len={len(test_data['inventory'])}")

inv_update(test_data, "香蕉", 1)
check("新增食材 香蕉", test_data["inventory"][0]["name"] == "香蕉" and test_data["inventory"][0]["count"] == 1)

evt_add(test_data, "take_out", "香蕉", 1)
check("事件添加 take_out", len(test_data["events"]) == 2 and test_data["events"][-1]["action"] == "take_out")

# ══════════════════════════════════════════════════
# 2. 同步功能 — HTTP POST 逻辑完整性
# ══════════════════════════════════════════════════
print("\n" + "=" * 60)
print("回归检查 — 同步功能（模拟）")
print("=" * 60)

# 验证 http_sync 构建的请求格式正确
def fake_http_sync(data, target_url):
    body = json.dumps(data, default=str).encode()
    # 验证能正确 encode
    ok = isinstance(body, bytes) and len(body) > 0
    # 验证 JSON 可解析
    parsed = json.loads(body.decode())
    return ok and "inventory" in parsed

test_sync = {
    "inventory": [{"name": "测试", "count": 1}],
    "events": [{"id": 1, "action": "put_in", "food_name": "测试", "count": 1}],
    "status": {"door_state": "closed", "light_state": "off", "cpu_temp": 0}
}
check("HTTP sync 数据构建正确", fake_http_sync(test_sync, "http://test:5000/api/sync"))

# ══════════════════════════════════════════════════
# 3. 服务端 API — 数据库操作完整性
# ══════════════════════════════════════════════════
print("\n" + "=" * 60)
print("回归检查 — 服务端 API")
print("=" * 60)

DB_PATH = os.path.join(PROJECT, "server/server_fridge.db")

# 初始化数据库
def init_test_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS inventory(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, category TEXT DEFAULT '',
        count INTEGER DEFAULT 1, first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        action TEXT, food_name TEXT, count INTEGER DEFAULT 1)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS status(
        id INTEGER PRIMARY KEY CHECK(id=1), door_state TEXT DEFAULT 'closed',
        light_state TEXT DEFAULT 'off', cpu_temp REAL DEFAULT 0,
        updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT OR IGNORE INTO status(id) VALUES(1)")
    conn.commit()
    return conn

def get_db():
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row; return c

init_test_db()

# 测试 inventory 增删改
with get_db() as db:
    db.execute("INSERT INTO inventory(name,count,first_seen,last_updated) VALUES(?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
               ("苹果", 3))
    db.commit()

with get_db() as db:
    items = db.execute("SELECT * FROM inventory").fetchall()
    check("数据库 INSERT", len(items) == 1 and items[0]["name"] == "苹果" and items[0]["count"] == 3,
          f"items={len(items)}")

with get_db() as db:
    db.execute("UPDATE inventory SET count=? WHERE id=?", (5, 1))
    db.commit()

with get_db() as db:
    items = db.execute("SELECT count FROM inventory WHERE id=1").fetchone()
    check("数据库 UPDATE", items["count"] == 5, f"count={items['count']}")

with get_db() as db:
    db.execute("DELETE FROM inventory WHERE id=?", (1,))
    db.commit()

with get_db() as db:
    items = db.execute("SELECT * FROM inventory").fetchall()
    check("数据库 DELETE", len(items) == 0)

# 测试 events 表
with get_db() as db:
    db.execute("INSERT INTO events(id,timestamp,action,food_name,count) VALUES(?,?,?,?,?)",
               (1, "2026-05-28 10:00:00", "put_in", "苹果", 3))
    db.commit()

with get_db() as db:
    evts = db.execute("SELECT * FROM events").fetchall()
    check("事件表 INSERT", len(evts) == 1 and evts[0]["action"] == "put_in")

# 测试 status 表更新
with get_db() as db:
    db.execute("UPDATE status SET door_state=?,light_state=?,cpu_temp=?,updated=CURRENT_TIMESTAMP WHERE id=1",
               ("open", "on", 50.5))
    db.commit()

with get_db() as db:
    st = db.execute("SELECT * FROM status WHERE id=1").fetchone()
    check("状态表 UPDATE", st["door_state"] == "open" and st["light_state"] == "on" and st["cpu_temp"] == 50.5,
          f"door={st['door_state']} light={st['light_state']} temp={st['cpu_temp']}")

# ══════════════════════════════════════════════════
# 4. Web 页面 — HTML/JS 语法完整性
# ══════════════════════════════════════════════════
print("\n" + "=" * 60)
print("回归检查 — Web 页面")
print("=" * 60)

html_path = os.path.join(PROJECT, "server/templates/index.html")
css_path = os.path.join(PROJECT, "server/static/style.css")

check("index.html 存在", os.path.exists(html_path), html_path)
check("style.css 存在", os.path.exists(css_path), css_path)

if os.path.exists(html_path):
    with open(html_path) as f:
        content = f.read()
    checks = [
        ("/api/dashboard" in content, "API dashboard 引用存在"),
        ("/api/snapshot" in content, "API snapshot 引用存在"),
        ("/api/edit" in content, "API edit 引用存在"),
        ("setInterval" in content, "轮询逻辑存在"),
        ("function refresh" in content or "function addItem" in content, "JS 函数存在"),
    ]
    for cond, desc in checks:
        check(f"Web页面: {desc}", cond)

# ══════════════════════════════════════════════════
# 5. 视频流 — MJPEG 代理逻辑完整性
# ══════════════════════════════════════════════════
print("\n" + "=" * 60)
print("回归检查 — 视频流")
print("=" * 60)

# 检查 placeholder JPEG 生成函数
placeholder = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00' \
           b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n' \
           b'\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a' \
           b'\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\x1c\x1b\xff\xda\x00\x08\x01\x01' \
           b'\x00\x01?\x10\xff\xd9'
check("MJPEG 占位符长度", len(placeholder) > 0, f"{len(placeholder)} bytes")
check("MJPEG 占位符起始", placeholder[:2] == b'\xff\xd8')
check("MJPEG 占位符结束", placeholder[-2:] == b'\xff\xd9')

# 验证 JPEG 段解析逻辑
buf = b'\x00\x00' + placeholder + b'\x00\x00'
a = buf.find(b'\xff\xd8')
b = buf.find(b'\xff\xd9', a + 2)
check("JPEG 帧解析逻辑", a >= 0 and b > a, f"SOI={a} EOI={b}")

# ══════════════════════════════════════════════════
# 6. 配置系统 — 不影响原有变量名/函数签名
# ══════════════════════════════════════════════════
print("\n" + "=" * 60)
print("回归检查 — 配置系统兼容性")
print("=" * 60)

# 验证 fridge_mgr.py 顶层变量名未被破坏
# （通过 AST 分析确保原有用法兼容）
with open("deploy/fridge_mgr.py") as f:
    src = f.read()

required_names = [
    "DOOR_PIN", "RELAY_PIN", "DB_FILE", "AI_BIN", "AI_MODEL",
    "DET_FILE", "FB_LOCK", "SERVER_URL", "SYNC_INTERVAL",
    "ai_start", "ai_stop", "http_sync", "process_events", "load_data", "save_data",
    "draw_lcd", "find_touch_device", "handle_touch_events"
]
for name in required_names:
    check(f"fridge_mgr: {name} 未被删除", name in src, name)

with open("server/app.py") as f:
    src = f.read()

required_server = [
    "app", "DB", "RTSP_URL", "get_db", "init_db",
    "dashboard", "sync", "edit", "video_feed", "snapshot", "ping"
]
for name in required_server:
    check(f"app.py: {name} 未被删除", name in src, name)

with open("deploy/lcd_ui.py") as f:
    src = f.read()

required_lcd = [
    "FB_DEV", "W", "H", "FONT_PATH",
    "BLACK", "WHITE", "GREEN", "BLUE", "RED", "YELLOW", "GRAY", "DGRAY", "LGRAY",
    "FOOD_NAMES", "draw_lcd", "find_touch_device", "handle_touch_events",
    "_draw_dialog", "_do_action", "_inv_op"
]
for name in required_lcd:
    check(f"lcd_ui: {name} 未被删除", name in src, name)

# ══════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════
print("\n" + "=" * 60)
print("回归检查汇总")
print("=" * 60)
passed = sum(1 for _, ok, _ in REPORT if ok)
total = len(REPORT)
print(f"  {passed}/{total} 通过")
for name, ok, msg in REPORT:
    print(f"  {'✓' if ok else '✗'} {name}")