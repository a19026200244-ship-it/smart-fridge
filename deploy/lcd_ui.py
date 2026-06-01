#!/usr/bin/env python3
"""LCD UI v4 - 极致可读性 | MT协议触屏 | 纯色高对比度"""
import os, json, time, glob, struct, select
from PIL import Image, ImageDraw, ImageFont

# ── 配置文件加载 (支持向后兼容) ──
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../config/board.json")
_ui_cfg = {}
try:
    with open(_CONFIG_PATH) as f:
        _ui_cfg = json.load(f).get("ui", {})
except Exception:
    pass

def _cfg(key, default):
    v = _ui_cfg.get(key)
    if v is None: return default
    if isinstance(default, tuple) and isinstance(v, list): return tuple(v)
    return v

FB_DEV   = _cfg("fb_device", "/dev/fb0")
W, H     = _cfg("screen_size", (480, 480))
FONT_PATH = _cfg("font_path", "/oem/usr/share/simsun_en.ttf")

# 纯色配色 - 最高对比度
BLACK   = _cfg("color_black",   (0, 0, 0))
WHITE   = _cfg("color_white",   (255, 255, 255))
GREEN   = _cfg("color_green",   (0, 255, 150))
BLUE    = _cfg("color_blue",    (60, 160, 255))
RED     = _cfg("color_red",     (255, 60, 80))
YELLOW  = _cfg("color_yellow",  (255, 210, 40))
GRAY    = _cfg("color_gray",    (140, 140, 150))
DGRAY   = _cfg("color_dgray",   (40, 40, 50))
LGRAY   = _cfg("color_lgray",   (25, 25, 35))

_touch_fd = None
_touch_zones = []
_touch_x = 0
_touch_y = 0
_touch_active = False
_touch_dialog = None

FOOD_NAMES = _ui_cfg.get("food_names", [
    "苹果","香蕉","橙子","西兰花","胡萝卜","三明治","披萨","蛋糕",
    "瓶装饮品","热狗","甜甜圈","杯子","牛奶","鸡蛋","面包","奶酪"
])

# 分层识别: display_name → COCO key → {category, icon}
_DISPLAY_TO_COCO = {}
_COCO_TO_ICON = {}
try:
    with open(_CONFIG_PATH) as f:
        _full_cfg = json.load(f)
    _dm = _full_cfg.get("display_name_map", {})
    _cm = _full_cfg.get("category_map", {})
    _ci = _full_cfg.get("category_icons", {})
    for coco, cn in _dm.items():
        _DISPLAY_TO_COCO[cn] = coco
    for coco, info in _cm.items():
        _COCO_TO_ICON[coco] = info.get("icon") or _ci.get(info.get("c1"), "❓")
except Exception:
    pass

def _item_icon(name):
    coco = _DISPLAY_TO_COCO.get(name, "")
    return _COCO_TO_ICON.get(coco, "📦")

def find_touch_device():
    for dev in glob.glob("/dev/input/event*"):
        try:
            with open("/sys/class/input/"+os.path.basename(dev)+"/device/name") as f:
                n = f.read().strip().lower()
            if "goodix" in n or "touch" in n or "gt9" in n: return dev
        except: pass
    return None

def get_font(size):
    try: return ImageFont.truetype(FONT_PATH, size)
    except: return ImageFont.load_default()

def rr(d, xy, r, fill):
    x1,y1,x2,y2 = xy; r = min(r, (x2-x1)//2, (y2-y1)//2)
    if r <= 0: d.rectangle(xy, fill=fill); return
    d.pieslice([x1,y1,x1+r*2,y1+r*2], 180, 270, fill=fill)
    d.pieslice([x2-r*2,y1,x2,y1+r*2], 270, 360, fill=fill)
    d.pieslice([x1,y2-r*2,x1+r*2,y2], 90, 180, fill=fill)
    d.pieslice([x2-r*2,y2-r*2,x2,y2], 0, 90, fill=fill)
    d.rectangle([x1+r,y1,x2-r,y2], fill=fill)
    d.rectangle([x1,y1+r,x2,y2-r], fill=fill)

def draw_lcd(data, touch_dev=None):
    global _touch_zones
    _touch_zones = []

    img = Image.new("RGBA", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    inv = data.get("inventory", [])
    evts = data.get("events", [])
    st  = data.get("status", {})
    door = st.get("door_state", "closed")
    temp = st.get("cpu_temp", 0)

    # ── 顶栏 黑色底+绿色线 ──
    d.rectangle([(0,0),(W,58)], fill=LGRAY)
    d.line([(0,58),(W,58)], fill=GREEN, width=2)
    d.text((14,8), "SMART FRIDGE", fill=GREEN, font=get_font(26))
    dc = YELLOW if door=="open" else GRAY
    d.text((14,36), door.upper(), fill=dc, font=get_font(16))
    d.text((W-70,8), f"{temp:.0f}C", fill=WHITE, font=get_font(26))

    y = 66

    # ── 库存区 ──
    cats = len(inv); total = sum(i.get("count",0) for i in inv)
    d.rectangle([(6,y-2),(W-6,y+26)], fill=DGRAY)
    d.text((14,y+2), "INVENTORY", fill=BLUE, font=get_font(20))
    d.text((W-90,y+4), f"[{cats}/{total}]", fill=GRAY, font=get_font(15))
    if door != "open":
        rr(d, (W-120,y,W-14,y+28), 8, GREEN)
        d.text((W-110,y+4), "+ ADD", fill=BLACK, font=get_font(15))
        _touch_zones.append((W-120,y,106,28,"add_dialog",))
    y += 32

    if not inv:
        d.text((W//2-65,y+30), "-- EMPTY --", fill=GRAY, font=get_font(22))
        y += 80
    else:
        for idx, item in enumerate(inv[:5]):
            name = item.get("name","?"); cnt = item.get("count",0)
            # 交替行背景
            if idx%2==0: d.rectangle([(6,y),(W-6,y+48)], fill=(18,18,28))
            else: d.rectangle([(6,y),(W-6,y+48)], fill=BLACK)
            # 图标 + 名称
            icon = _item_icon(name)
            d.text((14,y+8), icon, fill=WHITE, font=get_font(18))
            d.text((50,y+10), name, fill=WHITE, font=get_font(22))
            # 数量
            cnt_s = f"x{cnt}"
            d.text((250,y+12), cnt_s, fill=GREEN, font=get_font(20))
            # 按钮
            if door != "open":
                rr(d, (W-118,y+6,W-88,y+38), 6, GREEN)
                d.text((W-110,y+10), "+1", fill=BLACK, font=get_font(15))
                _touch_zones.append((W-118,y+6,30,32,"inv_inc",idx))
                rr(d, (W-82,y+6,W-52,y+38), 6, RED)
                d.text((W-74,y+10), "-1", fill=WHITE, font=get_font(15))
                _touch_zones.append((W-82,y+6,30,32,"inv_dec",idx))
                rr(d, (W-46,y+6,W-20,y+38), 6, DGRAY)
                d.text((W-40,y+10), "X", fill=RED, font=get_font(18))
                _touch_zones.append((W-46,y+6,26,32,"inv_del",idx))
            y += 52

    # ── 分隔 ──
    d.line([(10,y),(W-10,y)], fill=DGRAY, width=2)
    y += 8

    # ── 事件区 ──
    d.rectangle([(6,y-2),(W-6,y+26)], fill=DGRAY)
    d.text((14,y+2), "ACTIVITY LOG", fill=BLUE, font=get_font(20))
    d.text((W-80,y+4), f"[{len(evts)}]", fill=GRAY, font=get_font(15))
    y += 32

    if not evts:
        d.text((W//2-60,y+20), "-- NO EVENTS --", fill=GRAY, font=get_font(18))
    else:
        for idx, e in enumerate(reversed(evts[-4:])):
            real_idx = len(evts)-1-idx
            if idx%2==0: d.rectangle([(6,y),(W-6,y+38)], fill=(18,18,28))
            is_in = e.get("action","")=="put_in"
            label = "IN " if is_in else "OUT"
            lc = GREEN if is_in else RED
            food = e.get("food_name","?")
            cnt = e.get("count",1)
            d.text((14,y+6), label, fill=lc, font=get_font(18))
            d.text((60,y+8), f"{food} x{cnt}", fill=WHITE, font=get_font(18))
            if door != "open":
                rr(d, (W-40,y+4,W-14,y+30), 6, RED)
                d.text((W-34,y+8), "X", fill=WHITE, font=get_font(16))
                _touch_zones.append((W-40,y+4,26,26,"evt_del",real_idx))
            y += 42

    # ── 弹窗 ──
    if _touch_dialog:
        _draw_dialog(d)

    # ── 底部 ──
    d.rectangle([(0,H-26),(W,H)], fill=LGRAY)
    mode = ">> DETECTING <<" if door=="open" else "STANDBY"
    mc = GREEN if door=="open" else GRAY
    d.text((10,H-22), mode, fill=mc, font=get_font(16))
    d.text((W-170,H-22), "Luckfox Pico Ultra", fill=GRAY, font=get_font(13))

    pixels = img.tobytes("raw", "BGRA")
    with open(FB_DEV, "wb") as fb: fb.write(pixels)

def _draw_dialog(d):
    d.rectangle([(0,0),(W,H)], fill=(0,0,0,200))
    dx,dy,dw,dh = 20,80,W-40,320
    rr(d, (dx,dy,dx+dw,dy+dh), 14, DGRAY)
    d.rectangle((dx+2,dy+2,dx+dw-2,dy+dh-2), outline=GREEN, width=1)

    if _touch_dialog == "add_dialog":
        d.text((dx+20,dy+10), "SELECT FOOD", fill=GREEN, font=get_font(24))
        d.line([(dx+20,dy+40),(dx+dw-20,dy+40)], fill=GREEN)
        gx,gy = dx+16, dy+54
        for i, name in enumerate(FOOD_NAMES[:12]):
            col,row = i%4, i//4
            bx,by_ = gx+col*100, gy+row*46
            rr(d, (bx,by_,bx+90,by_+38), 8, DGRAY)
            d.rectangle((bx,by_,bx+90,by_+38), outline=GRAY)
            d.text((bx+10,by_+8), name, fill=WHITE, font=get_font(16))
            _touch_zones.append((bx,by_,90,38,"add_item_name",name))
        rr(d, (dx+dw-46,dy+4,dx+dw-8,dy+34), 8, RED)
        d.text((dx+dw-38,dy+10), "X", fill=WHITE, font=get_font(22))
        _touch_zones.append((dx+dw-46,dy+4,38,30,"close_dialog",))

# ── 触屏 (MT协议) ──
def _open_touch(dev_path):
    global _touch_fd
    if _touch_fd is not None:
        try: os.close(_touch_fd)
        except: pass
        _touch_fd = None
    if dev_path and os.path.exists(dev_path):
        _touch_fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK)

def handle_touch_events(data, dev_path):
    global _touch_zones, _touch_fd, _touch_x, _touch_y, _touch_active, _touch_dialog
    if _touch_fd is None: _open_touch(dev_path)
    if _touch_fd is None: return False

    modified = False; contact_active = False; cx=cy=0
    for _ in range(32):
        r, _, _ = select.select([_touch_fd], [], [], 0)
        if not r: break
        try: raw = os.read(_touch_fd, 16)
        except: break
        if len(raw)<16: break
        _, _, etype, ecode, evalue = struct.unpack('llHHi', raw)

        if etype==3:
            if ecode==53: cx=evalue
            elif ecode==54: cy=evalue
            elif ecode==57: contact_active=(evalue>=0)
            elif ecode==0: cx=evalue
            elif ecode==1: cy=evalue
        elif etype==1 and ecode==330: contact_active=(evalue>0)
        elif etype==0 and ecode==0:  # SYN_REPORT
            if contact_active: _touch_x,_touch_y,_touch_active=cx,cy,True
            elif _touch_active:
                sx=min(_touch_x*W//480,W-1); sy=min(_touch_y*H//480,H-1)
                for (zx,zy,zw,zh,action,*args) in _touch_zones:
                    if zx<=sx<=zx+zw and zy<=sy<=zy+zh:
                        if _do_action(data,action,*args): modified=True
                        break
                _touch_active=contact_active=False
    return modified

def _do_action(data, action, *args):
    global _touch_dialog
    inv=data.get("inventory",[]); evts=data.get("events",[])
    if action=="add_dialog": _touch_dialog="add_dialog"; return True
    elif action=="close_dialog": _touch_dialog=None; return True
    elif action=="add_item_name":
        _touch_dialog=None; return _inv_op(data,args[0] if args else"?",1)
    elif action=="inv_inc":
        idx=args[0] if args else -1
        if 0<=idx<len(inv): return _inv_op(data,inv[idx]["name"],1)
    elif action=="inv_dec":
        idx=args[0] if args else -1
        if 0<=idx<len(inv): return _inv_op(data,inv[idx]["name"],-1)
    elif action=="inv_del":
        idx=args[0] if args else -1
        if 0<=idx<len(inv):
            n,c=inv[idx]["name"],inv[idx]["count"]; del inv[idx]
            evts.append({"id":len(evts)+1,"timestamp":time.strftime("%Y-%m-%d %H:%M:%S"),
                         "action":"manual_del","food_name":n,"count":c}); return True
    elif action=="evt_del":
        idx=args[0] if args else -1
        if 0<=idx<len(evts): del evts[idx]; return True
    return False

def _inv_op(data, name, delta):
    inv=data.get("inventory",[])
    for it in inv:
        if it["name"]==name:
            it["count"]+=delta
            if it["count"]<=0: inv.remove(it)
            else: it["last_updated"]=time.strftime("%Y-%m-%d %H:%M:%S")
            return True
    if delta>0:
        inv.append({"name":name,"count":delta,"first_seen":time.strftime("%Y-%m-%d %H:%M:%S"),
                     "last_updated":time.strftime("%Y-%m-%d %H:%M:%S")})
    return True

if __name__=="__main__":
    print("Touch:",find_touch_device() or "N/A")
    draw_lcd({"inventory":[{"name":"apple","count":3},{"name":"banana","count":2}],
              "events":[{"timestamp":"2026-05-23","action":"put_in","food_name":"apple","count":3}],
              "status":{"door_state":"closed","cpu_temp":65}})
    print("OK")
