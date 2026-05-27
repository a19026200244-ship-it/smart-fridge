"""SmartFridge Server · 冰箱食材识别系统"""
import json, os, sqlite3, subprocess, time, threading, shutil
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response

app = Flask(__name__)
DB = os.path.join(os.path.dirname(__file__), "server_fridge.db")
RTSP_URL = "rtsp://192.168.2.77/live/0"  # 开发板RTSP流

def get_db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init_db():
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS inventory(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, category TEXT DEFAULT '',
            count INTEGER DEFAULT 1, first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            action TEXT, food_name TEXT, count INTEGER DEFAULT 1)""")
        db.execute("""CREATE TABLE IF NOT EXISTS status(
            id INTEGER PRIMARY KEY CHECK(id=1), door_state TEXT DEFAULT 'closed',
            light_state TEXT DEFAULT 'off', cpu_temp REAL DEFAULT 0,
            updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("INSERT OR IGNORE INTO status(id) VALUES(1)")
init_db()

# ===== MJPEG 视频流 =====
mjpeg_frame = None
mjpeg_lock = threading.Lock()
ffmpeg_proc = None

# Find ffmpeg — try PATH first, then common install locations
_ffmpeg_exe = None
for _candidate in [
    "ffmpeg",
    r"C:\Users\24139\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe",
]:
    if shutil.which(_candidate) or os.path.isfile(_candidate):
        _ffmpeg_exe = _candidate
        break

def start_ffmpeg():
    global ffmpeg_proc, mjpeg_frame
    if not _ffmpeg_exe:
        print("[Video] ffmpeg not found, video proxy disabled")
        return
    while True:
        try:
            print(f"[Video] Starting ffmpeg: {_ffmpeg_exe}")
            ffmpeg_proc = subprocess.Popen([
                _ffmpeg_exe, "-loglevel", "quiet",
                "-rtsp_transport", "tcp", "-i", RTSP_URL,
                "-an", "-vf", "fps=8,scale=640:-1",
                "-f", "mjpeg", "-q:v", "6", "pipe:1"
            ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            print(f"[Video] ffmpeg started, RTSP→MJPEG pid={ffmpeg_proc.pid}")
            buf = b""
            while True:
                chunk = ffmpeg_proc.stdout.read(4096)
                if not chunk: break
                buf += chunk
                while True:
                    a = buf.find(b'\xff\xd8')
                    b = buf.find(b'\xff\xd9', a + 2)
                    if a >= 0 and b > a:
                        with mjpeg_lock: mjpeg_frame = buf[a:b+2]
                        buf = buf[b+2:]
                    else: break
            # ffmpeg exited, clean up and retry
            ffmpeg_proc.wait()
            print(f"[Video] ffmpeg exited (code={ffmpeg_proc.returncode}), restarting in 3s...")
        except Exception as e:
            print(f"[Video] ffmpeg error: {e}")
        time.sleep(3)

threading.Thread(target=start_ffmpeg, daemon=True).start()

# ===== Web =====
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/dashboard")
def dashboard():
    with get_db() as db:
        inv = [dict(r) for r in db.execute("SELECT * FROM inventory ORDER BY last_updated DESC").fetchall()]
        evt = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30").fetchall()]
        st = dict(db.execute("SELECT * FROM status WHERE id=1").fetchone())
    return jsonify({"inventory": inv, "events": list(reversed(evt)), "status": st})

@app.route("/api/edit", methods=["POST"])
def edit():
    """Web UI 编辑数据库"""
    try:
        data = request.get_json()
        action = data.get("action", "")
        with get_db() as db:
            if action == "add":
                db.execute("INSERT INTO inventory(name,count,first_seen,last_updated) VALUES(?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (data.get("name",""), data.get("count",1)))
            elif action == "adjust":
                items = db.execute("SELECT * FROM inventory ORDER BY last_updated DESC").fetchall()
                idx = data.get("index", -1)
                if 0 <= idx < len(items):
                    new_cnt = items[idx]["count"] + data.get("delta", 0)
                    if new_cnt <= 0:
                        db.execute("DELETE FROM inventory WHERE id=?", (items[idx]["id"],))
                    else:
                        db.execute("UPDATE inventory SET count=?, last_updated=CURRENT_TIMESTAMP WHERE id=?",
                            (new_cnt, items[idx]["id"]))
            elif action == "delete":
                items = db.execute("SELECT * FROM inventory ORDER BY last_updated DESC").fetchall()
                idx = data.get("index", -1)
                if 0 <= idx < len(items):
                    db.execute("DELETE FROM inventory WHERE id=?", (items[idx]["id"],))
            elif action == "delete_event":
                evts = db.execute("SELECT * FROM events ORDER BY id DESC").fetchall()
                idx = data.get("index", -1)
                if 0 <= idx < len(evts):
                    db.execute("DELETE FROM events WHERE id=?", (evts[idx]["id"],))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/sync", methods=["POST"])
def sync():
    try:
        data = request.get_json()
        with get_db() as db:
            if "inventory" in data:
                db.execute("DELETE FROM inventory")
                for item in data["inventory"]:
                    db.execute("INSERT INTO inventory(name,category,count,first_seen,last_updated) VALUES(?,?,?,?,?)",
                        (item.get("name",""), item.get("category",""), item.get("count",1),
                         item.get("first_seen",datetime.now()), item.get("last_updated",datetime.now())))
            if "events" in data:
                db.execute("DELETE FROM events")
                for e in data["events"]:
                    db.execute("INSERT INTO events(id,timestamp,action,food_name,count) VALUES(?,?,?,?,?)",
                        (e.get("id",0), e.get("timestamp",datetime.now()), e.get("action",""), e.get("food_name",""), e.get("count",1)))
            hw = data.get("hardware_status") or data.get("status") or {}
            if hw:
                db.execute("UPDATE status SET door_state=?,light_state=?,cpu_temp=?,updated=CURRENT_TIMESTAMP WHERE id=1",
                    (hw.get("door_state","closed"), hw.get("light_state","off"), hw.get("cpu_temp",0.0)))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/video")
def video_feed():
    def generate():
        while True:
            with mjpeg_lock:
                frame = mjpeg_frame
            if frame:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            else:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + _placeholder_jpeg() + b'\r\n')
            time.sleep(0.08)
    resp = Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route("/api/snapshot")
def snapshot():
    """返回最新JPEG快照（用于JS轮询刷新，比MJPEG更兼容）"""
    with mjpeg_lock:
        frame = mjpeg_frame
    if frame:
        resp = Response(frame, mimetype='image/jpeg')
    else:
        resp = Response(_placeholder_jpeg(), mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

def _placeholder_jpeg():
    """1x1 蓝色占位JPEG"""
    return b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00' \
           b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n' \
           b'\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a' \
           b'\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\x1c\x1b\xff\xda\x00\x08\x01\x01' \
           b'\x00\x01?\x10\xff\xd9'

@app.route("/api/ping")
def ping(): return "pong"

if __name__ == "__main__":
    import socket
    def _local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except: return "localhost"
    lip = _local_ip()
    print(f"SmartFridge Server started!")
    print(f"  Local:   http://127.0.0.1:5000")
    print(f"  Network: http://{lip}:5000")

    # 公网隧道: 优先 ngrok(需token), 回退 localtunnel(无需注册)
    public_url = None
    lt_proc = None
    ngrok_token = os.environ.get("NGROK_AUTHTOKEN", "")
    if ngrok_token:
        try:
            from pyngrok import ngrok
            ngrok.set_auth_token(ngrok_token)
            tunnel = ngrok.connect(5000, "http")
            public_url = tunnel.public_url
        except Exception as e:
            print(f"  [ngrok] Failed: {e}")
    if not public_url:
        try:
            lt_bin = shutil.which("lt") or shutil.which("localtunnel")
            if lt_bin:
                lt_proc = subprocess.Popen([lt_bin, "--port", "5000"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for _ in range(10):
                    line = lt_proc.stdout.readline()
                    if "your url is:" in line:
                        public_url = line.strip().split("your url is: ")[-1]
                        break
        except Exception as e:
            print(f"  [localtunnel] Failed: {e}")
    if public_url:
        print(f"  Public:  {public_url}")
        print(f"  (first-time visitors may need to enter their IP on the landing page)")
    else:
        print(f"  Public:  (run: npm i -g localtunnel, or set NGROK_AUTHTOKEN)")

    import atexit
    def _cleanup():
        if lt_proc and lt_proc.poll() is None: lt_proc.kill()
        if ffmpeg_proc and ffmpeg_proc.poll() is None:
            ffmpeg_proc.kill()
    atexit.register(_cleanup)

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
