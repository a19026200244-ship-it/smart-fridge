#!/usr/bin/env python3
"""SmartFridge v6 - 门开AI启动/门关AI停止 + MT触屏 + 高对比度LCD UI"""
import os, sys, json, time, signal, subprocess
from periphery import GPIO
from lcd_ui import draw_lcd, find_touch_device, handle_touch_events

DOOR_PIN, RELAY_PIN = 32, 40
DB_FILE = "/root/smartfridge/fridge_data.json"
AI_BIN  = "/root/smartfridge/bin/fridge_ai"
AI_MODEL = "/root/smartfridge/model/yolov5.rknn"
DET_FILE = "/tmp/fridge_detections.json"
FB_LOCK  = "/tmp/fb_lock"
SERVER_URL = "http://192.168.2.73:5000"
SYNC_INTERVAL = 2

running, ai_proc = True, None
TOUCH_DEV = None

def sig_handler(sig, frame):
    global running; running = False

def load_data():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE) as f: return json.load(f)
        except: pass
    return {"inventory":[], "events":[], "status":{"door_state":"closed","light_state":"off","cpu_temp":0}}

def save_data(d):
    with open(DB_FILE+".tmp","w") as f: json.dump(d, f, default=str)
    os.replace(DB_FILE+".tmp", DB_FILE)

def inv_update(data, name, delta):
    for item in data["inventory"]:
        if item["name"] == name:
            item["count"] += delta
            if item["count"] <= 0: data["inventory"].remove(item)
            else: item["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            return
    if delta > 0:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        data["inventory"].append({"name":name,"count":delta,"first_seen":now,"last_updated":now})

def evt_add(data, action, food, count=1):
    data["events"].append({"id":len(data["events"])+1, "timestamp":time.strftime("%Y-%m-%d %H:%M:%S"),
                           "action":action, "food_name":food, "count":count})

def http_sync(data):
    try:
        import urllib.request
        body = json.dumps(data, default=str).encode()
        req = urllib.request.Request(SERVER_URL+"/api/sync", data=body,
            headers={"Content-Type":"application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception as e:
        print(f"[Sync] FAIL: {e}"); return False

def parse_detections():
    try:
        if not os.path.exists(DET_FILE): return {}
        with open(DET_FILE) as f: dets = json.load(f).get("detections", [])
        r = {}
        for d in dets:
            n = d.get("name","unknown")
            if n == "person": continue
            r[n] = r.get(n, 0) + 1
        return r
    except: return {}

def ai_start():
    global ai_proc
    if ai_proc and ai_proc.poll() is None: return
    env = os.environ.copy(); env["LD_LIBRARY_PATH"] = "/oem/usr/lib:/usr/lib"
    log = open("/root/smartfridge/logs/ai.log","w")
    ai_proc = subprocess.Popen([AI_BIN, AI_MODEL], stdout=log, stderr=log, env=env)
    print(f"[Mgr] AI started PID={ai_proc.pid}")

def ai_stop():
    global ai_proc
    if ai_proc:
        ai_proc.terminate()
        try: ai_proc.wait(timeout=3)
        except: ai_proc.kill()
        ai_proc = None
    os.system("killall fridge_ai 2>/dev/null")
    if os.path.exists(FB_LOCK): os.remove(FB_LOCK)
    print("[Mgr] AI stopped")

def lcd_lock():
    if not os.path.exists(FB_LOCK): open(FB_LOCK,"w").close()

def lcd_unlock():
    if os.path.exists(FB_LOCK): os.remove(FB_LOCK)

def process_events(data, before, after):
    for name, cnt in after.items():
        prev = before.get(name, 0)
        if cnt > prev:
            d = cnt - prev; print(f"  -> PUT  {name} x{d}")
            inv_update(data, name, d); evt_add(data, "put_in", name, d)
    for name, cnt in before.items():
        cur = after.get(name, 0)
        if cnt > cur:
            d = cnt - cur; print(f"  -> TAKE {name} x{d}")
            inv_update(data, name, -d); evt_add(data, "take_out", name, d)

def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f: return float(f.read().strip())/1000.0
    except: return 0.0

def main():
    global running, ai_proc, TOUCH_DEV
    signal.signal(signal.SIGINT, sig_handler); signal.signal(signal.SIGTERM, sig_handler)

    print("===== SmartFridge v6.0 =====")

    door = GPIO(DOOR_PIN, "in")
    relay = GPIO(RELAY_PIN, "out"); relay.write(False)
    print("[Init] GPIO OK")

    TOUCH_DEV = find_touch_device()
    print(f"[Init] Touch: {TOUCH_DEV or 'N/A'}")

    data = load_data()
    print(f"[Init] Data: {len(data['inventory'])} items, {len(data['events'])} events")

    os.system("/oem/usr/bin/RkLunch-stop.sh 2>/dev/null"); time.sleep(0.5)

    door_was_open, light_on = False, False
    baseline = {}
    last_sync, last_ui = 0, 0

    # 初始: 门关, 显示 UI, 无 AI
    draw_lcd(data, TOUCH_DEV)
    print("[Main] IDLE (door closed, AI off)\n")

    while running:
        try:
            door_open = not door.read()
        except:
            print("[Warn] GPIO error"); time.sleep(5); continue

        # ── 触屏 (仅 IDLE) ──
        if not door_open and TOUCH_DEV:
            if handle_touch_events(data, TOUCH_DEV):
                save_data(data); draw_lcd(data, TOUCH_DEV)

        # === DOOR OPENED ===
        if door_open and not door_was_open:
            print("\n>>> Door OPENED! AI starting...")
            door_was_open = True
            relay.write(True); light_on = True
            ai_start()
            baseline = parse_detections()
            print(f"[Baseline] {len(baseline)} items")
            time.sleep(0.1)

        # === DOOR OPEN: DETECTING ===
        if door_open:
            data["status"].update({"door_state":"open","light_state":"on","cpu_temp":cpu_temp()})
            save_data(data)
            if time.time() - last_sync >= SYNC_INTERVAL:
                http_sync(data); last_sync = time.time()
            time.sleep(0.2)
            continue

        # === DOOR CLOSED -> PROCESS ===
        if not door_open and door_was_open:
            print(">>> Door CLOSED! Processing...")
            door_was_open = False
            relay.write(False); light_on = False
            after = parse_detections()
            ai_stop()
            print(f"[Result] Before:{len(baseline)} After:{len(after)}")
            process_events(data, baseline, after)
            save_data(data)
            draw_lcd(data, TOUCH_DEV)
            http_sync(data); last_sync = time.time()
            print("[Main] IDLE\n")

        # === IDLE ===
        if not door_open:
            data["status"].update({"door_state":"closed","light_state":"off","cpu_temp":cpu_temp()})
            save_data(data)
            if time.time() - last_ui >= 1.5:
                draw_lcd(data, TOUCH_DEV); last_ui = time.time()
            if time.time() - last_sync >= SYNC_INTERVAL:
                http_sync(data); last_sync = time.time()
            time.sleep(0.15)

    # 清理
    relay.write(False); ai_stop()
    door.close(); relay.close()
    print("===== Stopped =====")

if __name__ == "__main__":
    main()
